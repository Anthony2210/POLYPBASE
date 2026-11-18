"""Operational Local ID resolution; all fixtures live in isolated test SQLite."""

from contextlib import contextmanager
import uuid


from django.contrib.auth import get_user_model
from django.db import connection
from django.http import Http404
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.audit.services import resolve_audit_box_references
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain


from .box_aliases import register_box_local_id_alias
from .identity_resolution import box_local_id_search_ids, resolve_box_identity, resolve_box_local_id, resolve_box_local_ids
from .models import Box, BoxGlobalIdentity, BoxLocalIdAlias


@override_settings(SECURE_SSL_REDIRECT=False)
class BoxAliasResolutionTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Alias resolution laboratory")
        self.other = Organization.objects.create(name="Foreign alias resolution laboratory")
        self.user = get_user_model().objects.create_user(username="alias-resolver", email="resolver@example.org")
        for org in (self.org, self.other):
            OrganizationMembership.objects.create(user=self.user, organization=org, role="admin")
        species = Species.objects.create(scientific_name="Alias resolution species", genus_species_code="ARS")
        self.strain = Strain.objects.create(organization=self.org, species=species, code="ARS-LAB-1")
        self.foreign_strain = Strain.objects.create(organization=self.other, species=species, code="ARS-OTH-1")
        self.box = self.make_box("ARS-LAB-1.001", previous_global_code="previous-local-id")
        self.client.force_login(self.user)
        self.headers = {"HTTP_X_ORGANIZATION_ID": str(self.org.pk)}

    def make_box(self, code, *, organization=None, **extra):
        organization = organization or self.org
        return Box.objects.create(
            organization=organization, strain=self.strain if organization == self.org else self.foreign_strain,
            global_code=code, box_number=code.rsplit(".", 1)[-1], **extra,
        )

    def scope(self, organization=None):
        return Box.objects.filter(organization=organization or self.org)

    def alias(self, code, box=None):
        box = box or self.box
        return register_box_local_id_alias(box=box, code=code, organization_id=box.organization_id)

    def by_code(self, code, **headers):
        return self.client.get(reverse("api_box_detail_by_code", args=[code]), **(headers or self.headers))

    def search(self, query, *, inventory=False, **params):
        name = "api_admin_box_inventory" if inventory else "api_box_list"
        response = self.client.get(reverse(name), {"q": query, **params}, **self.headers)
        self.assertEqual(response.status_code, 200, response.content)
        return [row["id"] for row in response.json()["results"]]

    @contextmanager
    def legacy_writes(self):
        """Simulate preserved pre-0019 anomalies, then immediately restore guards."""
        if connection.vendor != "sqlite":
            # Dropping production guards is only done in in-memory SQLite.
            self.skipTest("Legacy anomaly fixtures require isolated in-memory SQLite.")
        self.assertIn("memory", connection.settings_dict["NAME"])
        with connection.cursor() as cursor:
            cursor.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name IN (%s, %s)",
                           ["cultures_box", "cultures_boxlocalidalias"])
            triggers = cursor.fetchall()
            for name, _ in triggers:
                cursor.execute(f'DROP TRIGGER "{name}"')
            try:
                yield
            finally:
                for _, sql in triggers:
                    cursor.execute(sql)

    def test_current_previous_and_multiple_registered_ids_have_identical_details(self):
        codes = [self.box.global_code, self.box.previous_global_code, "first-historical-id", "second-historical-id"]
        for code in codes[2:]:
            self.alias(code)
        before = list(Box.objects.values())
        details = [self.by_code(code) for code in codes]
        self.assertTrue(all(response.status_code == 200 for response in details))
        self.assertTrue(all(response.json() == details[0].json() for response in details))
        self.assertEqual(details[0].json()["id"], self.box.pk)
        self.assertEqual(list(Box.objects.values()), before)
        self.assertEqual(BoxLocalIdAlias.objects.count(), 2)

    def test_exact_route_preserves_confirmed_legacy_codes_with_encoded_slashes(self):
        self.alias("AUR /42")
        response = self.by_code("AUR /42")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["id"], self.box.pk)

    def test_unknown_and_foreign_ids_have_the_same_404_even_for_dual_members(self):
        foreign = self.make_box("ARS-OTH-1.001", organization=self.other, previous_global_code="foreign-previous")
        self.alias("foreign-alias", foreign)
        unknown = self.by_code("unknown")
        for code in (foreign.global_code, foreign.previous_global_code, "foreign-alias"):
            response = self.by_code(code)
            self.assertEqual((response.status_code, response.json()), (404, unknown.json()))
        allowed = self.by_code("foreign-alias", HTTP_X_ORGANIZATION_ID=str(self.other.pk))
        self.assertEqual(allowed.json()["id"], foreign.pk)
        self.assertEqual(self.client.get(reverse("api_box_detail_by_code", args=[self.box.global_code])).status_code, 403)
        self.client.logout()
        self.assertIn(self.by_code(self.box.global_code).status_code, (401, 403))

    def test_explicit_merge_chains_and_equivalent_claims_resolve_to_terminal(self):
        middle = self.make_box("retired-middle", status="inactive", merged_into=self.box)
        source = self.make_box("retired-source", status="inactive", merged_into=middle,
                               previous_global_code=self.box.global_code)
        self.alias("source-alias-one", source)
        self.alias("source-alias-two", source)
        self.alias(self.box.global_code, source)
        for code in (middle.global_code, source.global_code, "source-alias-one", "source-alias-two", self.box.global_code):
            self.assertEqual(resolve_box_local_id(self.scope(), code).pk, self.box.pk)
            self.assertEqual(self.by_code(code).json()["id"], self.box.pk)
        self.assertEqual(resolve_box_identity(self.scope(), source.pk).pk, self.box.pk)
        self.assertEqual(BoxLocalIdAlias.objects.get(code="source-alias-one").box_id, source.pk)

    def test_search_list_inventory_all_ids_and_merged_sources_without_duplicates(self):
        source = self.make_box("retired-search-source", status="inactive", merged_into=self.box,
                               previous_global_code="search-old-source")
        for code, owner in (("search-alias-one", self.box), ("search-alias-two", self.box),
                            ("search-alias-three", source)):
            self.alias(code, owner)
        for inventory in (False, True):
            for query in (self.box.global_code, self.box.previous_global_code, source.global_code,
                          "search-old-source", "search-alias-one", "search-alias-two", "search-alias-three", "search"):
                with self.subTest(inventory=inventory, query=query):
                    self.assertEqual(self.search(query, inventory=inventory), [self.box.pk])
            self.assertEqual(self.search("search", inventory=inventory, status="inactive"), [])

    def test_search_chain_and_case_insensitive_aliases(self):
        middle = self.make_box("search-chain-middle", status="inactive", merged_into=self.box)
        source = self.make_box("search-chain-source", status="inactive", merged_into=middle)
        self.alias("Mixed-Case-Chain", source)
        for inventory in (False, True):
            self.assertEqual(self.search("mixed-case", inventory=inventory), [self.box.pk])
            self.assertEqual(self.search("search-chain", inventory=inventory), [self.box.pk])

    def test_foreign_aliases_never_disclose_a_box_in_search(self):
        foreign = self.make_box("foreign-search-current", organization=self.other, previous_global_code="foreign-search-old")
        self.alias("foreign-search-alias", foreign)
        for inventory in (False, True):
            self.assertEqual(self.search("foreign-search", inventory=inventory), [])

    def test_numeric_detail_qr_and_uuid_routes_keep_their_contracts(self):
        identity = BoxGlobalIdentity.objects.create(allocation_reason="automatic_creation")
        Box.objects.filter(pk=self.box.pk).update(global_identity=identity)
        middle = self.make_box("qr-middle", status="inactive", merged_into=self.box)
        source = self.make_box("qr-source", status="inactive", merged_into=middle)
        for box in (self.box, middle, source):
            self.assertEqual(self.client.get(reverse("api_box_detail", args=[box.pk]), **self.headers).json()["id"], self.box.pk)
            scan = self.client.post(reverse("api_box_scan", args=[box.pk]), **self.headers)
            self.assertEqual((scan.status_code, scan.json()), (201, {"id": self.box.pk, "global_code": self.box.global_code}))
            log = AuditLog.objects.latest("pk")
            self.assertEqual(log.metadata["requested_box_id"], box.pk)
        resolved = self.client.get(reverse("api_box_global_id_resolve", args=[identity.global_id]), **self.headers)
        self.assertEqual(resolved.json()["id"], self.box.pk)
        self.assertEqual(self.client.get(reverse("api_box_global_id_resolve", args=[uuid.uuid4()]), **self.headers).status_code, 404)
        foreign_headers = {"HTTP_X_ORGANIZATION_ID": str(self.other.pk)}
        before = AuditLog.objects.count()
        self.assertEqual(self.client.post(reverse("api_box_scan", args=[source.pk]), **foreign_headers).status_code, 404)
        self.assertEqual(self.client.get(reverse("api_box_global_id_resolve", args=[identity.global_id]), **foreign_headers).status_code, 404)
        self.assertEqual(AuditLog.objects.count(), before)

    def test_globally_conflicting_current_previous_and_alias_claims_fail_closed(self):
        foreign = self.make_box("conflict-foreign", organization=self.other)
        rival = self.make_box("conflict-own")
        self.alias("registered-conflict")
        with self.legacy_writes():
            Box.objects.filter(pk=foreign.pk).update(previous_global_code=self.box.global_code)
            Box.objects.filter(pk=rival.pk).update(previous_global_code="registered-conflict")
            BoxLocalIdAlias.objects.create(code=self.box.previous_global_code, box=foreign)
        for code in (self.box.global_code, self.box.previous_global_code, "registered-conflict"):
            with self.subTest(code=code):
                self.assertEqual(resolve_box_local_ids(self.scope(), [code]), {})
                self.assertEqual(self.by_code(code).status_code, 404)
                for inventory in (False, True):
                    self.assertEqual(self.search(code, inventory=inventory), [])

    def test_cycles_and_foreign_merge_hops_are_rejected_in_every_consumer(self):
        foreign = self.make_box("foreign-terminal", organization=self.other)
        cycle_a = self.make_box("bad-cycle-a", status="inactive")
        cycle_b = self.make_box("bad-cycle-b", status="inactive")
        crossed = self.make_box("bad-crossed", status="inactive")
        # Register while sources are still valid; legacy graph changes are not
        # permitted by today's guards but can predate them.
        self.alias("bad-cycle-alias", cycle_a)
        self.alias("bad-foreign-alias", crossed)
        with self.legacy_writes():
            Box.objects.filter(pk=cycle_a.pk).update(merged_into=cycle_b)
            Box.objects.filter(pk=cycle_b.pk).update(merged_into=cycle_a)
            Box.objects.filter(pk=crossed.pk).update(merged_into=foreign)
        for source, code in ((cycle_a, "bad-cycle-alias"), (crossed, "bad-foreign-alias")):
            with self.assertRaises(Http404):
                resolve_box_identity(self.scope(), source.pk)
            self.assertEqual(self.by_code(code).status_code, 404)
            for inventory in (False, True):
                self.assertEqual(self.search(code, inventory=inventory), [])
        self.assertEqual(self.search("bad-"), [])
        # Even a caller authorized in both institutions must not follow a
        # cross-institution merge edge.
        with self.assertRaises(Http404):
            resolve_box_local_id(Box.objects.all(), "bad-foreign-alias")
        for code in ("bad-cycle-alias", "bad-foreign-alias"):
            log = AuditLog(object_type="box", object_id=code)
            self.assertEqual(resolve_audit_box_references([log], organization_id=self.org.pk), {})

    def test_unknown_empty_and_unconfirmed_ids_are_not_inferred(self):
        for code in ("", " ", "unregistered", f" {self.box.global_code}", self.box.global_code.lower()):
            with self.subTest(code=code), self.assertRaises(Http404):
                resolve_box_local_id(self.scope(), code)
        with self.assertRaises(Http404):
            resolve_box_identity(self.scope(), 99999999)
        self.assertEqual(resolve_box_local_ids(self.scope(), []), {})

    def test_audit_references_use_stored_ids_without_rewriting_history(self):
        source = self.make_box("audit-retired", status="inactive", merged_into=self.box)
        self.alias("audit-registered-one", source)
        self.alias("audit-registered-two")
        foreign = self.make_box("audit-foreign", organization=self.other)
        codes = [self.box.global_code, self.box.previous_global_code, source.global_code,
                 "audit-registered-one", "audit-registered-two", foreign.global_code, "missing-audit-code"]
        logs = [AuditLog.objects.create(organization=self.org, user=self.user, action="update", object_type="box",
                                       object_id=code, description="Stored historical code") for code in codes]
        before = list(AuditLog.objects.values())
        references = resolve_audit_box_references(logs, organization_id=self.org.pk)
        self.assertEqual(set(references), set(codes[:5]))
        self.assertEqual({box.pk for box in references.values()}, {self.box.pk})
        response = self.client.get(reverse("api_account_audit_log"), **self.headers)
        self.assertEqual(response.status_code, 200)
        rendered = {row["object_id"]: row["box_reference"] for row in response.json()["results"]}
        self.assertEqual(set(rendered), set(codes))
        for code in codes[:5]:
            self.assertEqual(rendered[code]["id"], self.box.pk)
            self.assertEqual(rendered[code]["global_code"], self.box.global_code)
        for code in codes[5:]:
            self.assertIsNone(rendered[code])
        self.assertEqual(list(AuditLog.objects.values()), before)

    def test_audit_ambiguous_or_invalid_codes_are_not_linked(self):
        foreign = self.make_box("audit-conflict-foreign", organization=self.other)
        with self.legacy_writes():
            Box.objects.filter(pk=foreign.pk).update(previous_global_code=self.box.global_code)
        log = AuditLog.objects.create(organization=self.org, user=self.user, action="update", object_type="box",
                                      object_id=self.box.global_code, description="Unresolved historical code")
        self.assertEqual(resolve_audit_box_references([log], organization_id=self.org.pk), {})
        self.assertEqual(resolve_audit_box_references([], organization_id=self.org.pk), {})

    def test_batch_resolution_query_count_does_not_grow_with_alias_count(self):
        for number in range(12):
            self.alias(f"batch-alias-{number}")
        with CaptureQueriesContext(connection) as queries:
            result = resolve_box_local_ids(self.scope(), [f"batch-alias-{number}" for number in range(12)])
        self.assertEqual(len(result), 12)
        self.assertLessEqual(len(queries), 4)
        with CaptureQueriesContext(connection) as search_queries:
            self.assertEqual(box_local_id_search_ids(self.scope(), "batch-alias"), {self.box.pk})
        self.assertLessEqual(len(search_queries), 6)
