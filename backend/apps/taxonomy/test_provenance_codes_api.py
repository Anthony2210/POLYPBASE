import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import Resolver404, resolve, reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box
from apps.organizations.models import Organization

from .models import (
    BiologicalProvenance,
    LocalStrainIdentity,
    OrganizationProvenanceCode,
    OrganizationSpeciesCode,
    Species,
    Strain,
)


class ProvenanceCodesApiTests(TestCase):
    def setUp(self):
        self.a = Organization.objects.create(name="Institution A")
        self.b = Organization.objects.create(name="Institution B")
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            username="bbb_admin", email="bbb_admin@example.org", password="secret"
        )
        self.tech = user_model.objects.create_user(
            username="bbb_tech", email="bbb_tech@example.org", password="secret"
        )
        self.viewer = user_model.objects.create_user(
            username="bbb_viewer", email="bbb_viewer@example.org", password="secret"
        )
        for user, organization, role in (
            (self.admin, self.a, OrganizationMembership.Role.ADMIN),
            (self.admin, self.b, OrganizationMembership.Role.ADMIN),
            (self.tech, self.a, OrganizationMembership.Role.LAB_TECHNICIAN),
            (self.viewer, self.a, OrganizationMembership.Role.VIEWER),
        ):
            OrganizationMembership.objects.create(user=user, organization=organization, role=role)
        # Preserve already-verified historical associations, even if shared.
        self.provenance = BiologicalProvenance.objects.create(name="Shared source")
        self.other_provenance = BiologicalProvenance.objects.create(name="Other source")
        self.a_code = OrganizationProvenanceCode.objects.create(
            organization=self.a, biological_provenance=self.provenance, code="AAA"
        )
        self.b_code = OrganizationProvenanceCode.objects.create(
            organization=self.b, biological_provenance=self.provenance, code="BBB"
        )
        self.provenances_url = reverse("api_taxonomy_biological_provenances")
        self.codes_url = reverse("api_taxonomy_provenance_codes")

    def headers(self, organization):
        return {"HTTP_X_ORGANIZATION_ID": str(organization.pk)}

    def post(self, url, organization, data):
        return self.client.post(
            url, data=json.dumps(data), content_type="application/json", **self.headers(organization)
        )

    def snapshot(self):
        return {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in (BiologicalProvenance, OrganizationProvenanceCode, Strain,
                          LocalStrainIdentity, Box, AuditLog)
        }

    def test_collections_have_expected_paths_and_scoped_read_access(self):
        self.assertEqual(self.provenances_url, "/api/taxonomy/biological-provenances/")
        self.assertEqual(self.codes_url, "/api/taxonomy/provenance-codes/")
        expected_provenances = [{"id": self.provenance.pk, "name": "Shared source"}]
        self.client.force_login(self.admin)
        for organization in (self.a, self.b):
            with self.subTest(organization=organization.pk):
                response = self.client.get(self.provenances_url, **self.headers(organization))
                self.assertEqual(response.status_code, 200)
                self.assertCountEqual(response.json(), expected_provenances)
                self.assertNotIn("Other source", response.content.decode())

        for organization, assignment in ((self.a, self.a_code), (self.b, self.b_code)):
            with self.subTest(organization=organization.pk):
                response = self.client.get(self.codes_url, **self.headers(organization))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), [{
                    "id": assignment.pk,
                    "biological_provenance": self.provenance.pk,
                    "biological_provenance_name": "Shared source",
                    "code": assignment.code,
                }])
                self.assertNotIn("organization", response.json()[0])

        self.client.force_login(self.tech)
        self.assertCountEqual(
            self.client.get(self.provenances_url, **self.headers(self.a)).json(),
            expected_provenances,
        )
        self.assertEqual(
            self.client.get(self.codes_url, **self.headers(self.a)).json()[0]["code"], "AAA"
        )
        self.client.force_login(self.viewer)
        for url in (self.provenances_url, self.codes_url):
            self.assertEqual(self.client.get(url, **self.headers(self.a)).status_code, 403)

    def test_both_legacy_posts_are_retired_for_every_institution_and_role(self):
        before = self.snapshot()
        for user in (self.admin, self.tech, self.viewer):
            self.client.force_login(user)
            for url, payload in (
                (self.provenances_url, {"name": "New private location"}),
                (self.codes_url, {"biological_provenance": self.other_provenance.pk, "code": "CCC"}),
            ):
                with self.subTest(user=user.pk, url=url):
                    response = self.post(url, self.a, payload)
                    self.assertEqual(response.status_code, 405)
                    self.assertNotIn("POST", response["Allow"])
        self.client.force_login(self.admin)
        for url in (self.provenances_url, self.codes_url):
            self.assertEqual(self.post(url, self.b, {}).status_code, 405)
        self.assertEqual(self.snapshot(), before)

    def test_read_permissions_follow_active_membership_and_organization_header(self):
        for url in (self.provenances_url, self.codes_url):
            self.assertIn(self.client.get(url, **self.headers(self.a)).status_code, (401, 403))
            self.assertIn(self.post(url, self.a, {}).status_code, (401, 403))
        self.client.force_login(self.tech)
        for url in (self.provenances_url, self.codes_url):
            self.assertEqual(self.client.get(url, **self.headers(self.b)).status_code, 403)
        self.client.force_login(self.admin)
        membership = OrganizationMembership.objects.get(user=self.admin, organization=self.b)
        for role, expected in ((OrganizationMembership.Role.LAB_TECHNICIAN, 200),
                               (OrganizationMembership.Role.VIEWER, 403)):
            membership.role = role
            membership.save(update_fields=["role"])
            for url in (self.provenances_url, self.codes_url):
                self.assertEqual(self.client.get(url, **self.headers(self.b)).status_code, expected)
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        for url in (self.provenances_url, self.codes_url):
            self.assertEqual(self.client.get(url, **self.headers(self.b)).status_code, 403)
            self.assertEqual(self.client.get(url, HTTP_X_ORGANIZATION_ID="999999").status_code, 403)
            self.assertEqual(self.client.get(url, **self.headers(self.a)).status_code, 200)

    def test_only_collection_reads_are_exposed(self):
        self.client.force_login(self.admin)
        before = self.snapshot()
        for url, pk in ((self.provenances_url, self.provenance.pk),
                        (self.codes_url, self.a_code.pk)):
            options = self.client.options(url, **self.headers(self.a))
            self.assertEqual(options.status_code, 200)
            self.assertEqual(set(options["Allow"].split(", ")), {"GET", "HEAD", "OPTIONS"})
            self.assertEqual(self.client.head(url, **self.headers(self.a)).status_code, 200)
            for method in ("post", "put", "patch", "delete"):
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(
                        url, data=json.dumps({"name": "Edited", "code": "XYZ"}),
                        content_type="application/json", **self.headers(self.a)
                    )
                    self.assertEqual(response.status_code, 405)
            with self.assertRaises(Resolver404):
                resolve(f"{url}{pk}/")
            self.assertEqual(self.client.get(f"{url}{pk}/", **self.headers(self.a)).status_code, 404)
        self.assertEqual(self.snapshot(), before)

    def test_refused_writes_preserve_historical_identities_boxes_and_audits(self):
        species = Species.objects.create(scientific_name="Historical species")
        aaa = OrganizationSpeciesCode.objects.create(organization=self.a, species=species, code="HIS")
        for code, assignment in (("HIS-1", None), ("HIS-AAA-2", self.a_code)):
            strain = Strain.objects.create(
                organization=self.a, species=species, code=code, origin_code="AAA"
            )
            LocalStrainIdentity.objects.create(
                strain=strain, species_code_assignment=aaa, provenance_code_assignment=assignment,
            )
            Box.objects.create(
                organization=self.a, strain=strain, global_code=f"{code}.001", box_number="001"
            )
        AuditLog.objects.create(
            organization=self.a, user=self.admin, action=AuditLog.Action.CREATION,
            object_type="biological_provenance", object_id=str(self.other_provenance.pk),
            description="Historical provenance creation",
        )
        before = self.snapshot()
        self.client.force_login(self.admin)
        self.assertEqual(self.post(self.provenances_url, self.a, {"name": "New source"}).status_code, 405)
        self.assertEqual(self.post(self.codes_url, self.a, {
            "biological_provenance": self.other_provenance.pk, "code": "LEG",
        }).status_code, 405)
        self.assertEqual(self.snapshot(), before)


class ProvenanceIsolationTests(TestCase):
    """M1: private and unattributed locations cannot be claimed through legacy APIs."""

    def setUp(self):
        self.a = Organization.objects.create(name="Isolation A")
        self.b = Organization.objects.create(name="Isolation B")
        user_model = get_user_model()
        self.a_admin = user_model.objects.create_user(
            username="iso_a", email="iso_a@example.org", password="secret"
        )
        self.b_admin = user_model.objects.create_user(
            username="iso_b", email="iso_b@example.org", password="secret"
        )
        for user, organization in ((self.a_admin, self.a), (self.b_admin, self.b)):
            OrganizationMembership.objects.create(
                user=user, organization=organization, role=OrganizationMembership.Role.ADMIN
            )
        self.provenances_url = reverse("api_taxonomy_biological_provenances")
        self.codes_url = reverse("api_taxonomy_provenance_codes")

    def headers(self, organization):
        return {"HTTP_X_ORGANIZATION_ID": str(organization.pk)}

    def register(self, organization, name, code):
        """Seed an existing verified association, not a legacy API write."""
        provenance = BiologicalProvenance.objects.create(name=name)
        OrganizationProvenanceCode.objects.create(
            organization=organization, biological_provenance=provenance, code=code,
        )
        return provenance.pk

    def test_a_private_location_is_invisible_to_another_institution(self):
        provenance = self.register(self.a, "Secret lagoon", "SEC")
        self.client.force_login(self.a_admin)
        self.assertEqual(self.client.get(self.provenances_url, **self.headers(self.a)).json(),
                         [{"id": provenance, "name": "Secret lagoon"}])
        self.client.force_login(self.b_admin)
        for url in (self.provenances_url, self.codes_url):
            response = self.client.get(url, **self.headers(self.b))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), [])
            self.assertNotIn("Secret lagoon", response.content.decode())

    def test_same_name_and_bbb_remain_institution_scoped(self):
        a_provenance = self.register(self.a, "Rade de Brest", "BRE")
        b_provenance = self.register(self.b, "Rade de Brest", "BRE")
        self.assertNotEqual(a_provenance, b_provenance)
        self.assertEqual(BiologicalProvenance.objects.filter(name="Rade de Brest").count(), 2)
        for user, organization, provenance in ((self.a_admin, self.a, a_provenance),
                                                (self.b_admin, self.b, b_provenance)):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.provenances_url, **self.headers(organization)).json(),
                             [{"id": provenance, "name": "Rade de Brest"}])
            codes = self.client.get(self.codes_url, **self.headers(organization)).json()
            self.assertEqual([(item["biological_provenance"], item["code"]) for item in codes],
                             [(provenance, "BRE")])

    def test_own_foreign_unassigned_and_unknown_ids_are_equally_refused_without_disclosure(self):
        own = self.register(self.b, "Own lagoon", "OWN")
        foreign = self.register(self.a, "Secret lagoon", "SEC")
        legacy = BiologicalProvenance.objects.create(name="Unknown historical ownership")
        before = list(OrganizationProvenanceCode.objects.order_by("pk").values())
        self.client.force_login(self.b_admin)
        responses = []
        for pk in (own, foreign, legacy.pk, 999999):
            response = self.client.post(
                self.codes_url, data=json.dumps({"biological_provenance": pk, "code": "BBB"}),
                content_type="application/json", **self.headers(self.b),
            )
            self.assertEqual(response.status_code, 405)
            self.assertNotIn("Secret lagoon", response.content.decode())
            self.assertNotIn(legacy.name, response.content.decode())
            responses.append(response.json())
        self.assertTrue(all(body == responses[0] for body in responses))
        self.assertEqual(list(OrganizationProvenanceCode.objects.order_by("pk").values()), before)
        self.assertTrue(BiologicalProvenance.objects.filter(pk=legacy.pk, name=legacy.name).exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_unassigned_legacy_rows_are_preserved_invisible_and_not_claimable(self):
        legacy = BiologicalProvenance.objects.create(name="Legacy source")
        for user, organization in ((self.a_admin, self.a), (self.b_admin, self.b)):
            self.client.force_login(user)
            for url in (self.provenances_url, self.codes_url):
                self.assertEqual(self.client.get(url, **self.headers(organization)).json(), [])
            response = self.client.post(
                self.codes_url, data=json.dumps({"biological_provenance": legacy.pk, "code": "LEG"}),
                content_type="application/json", **self.headers(organization),
            )
            self.assertEqual(response.status_code, 405)
        self.assertTrue(BiologicalProvenance.objects.filter(pk=legacy.pk, name="Legacy source").exists())
        self.assertFalse(OrganizationProvenanceCode.objects.exists())
        self.assertFalse(AuditLog.objects.exists())
