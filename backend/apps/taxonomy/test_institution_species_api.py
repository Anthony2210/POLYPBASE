"""Institutional Species registration, editing, reads and legacy-path protection."""

import io
import json
import socket
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from time import sleep
from unittest import skipUnless
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.contrib.admin.sites import site as admin_site
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import close_old_connections, connection, transaction
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box, BoxCodeNamespace, BoxLocalIdAlias
from apps.organizations.models import Organization

from . import institution_species_services as services
from .models import (
    BiologicalProvenance,
    GlobalStrainIdentity,
    InstitutionSpecies,
    LocalStrainIdentity,
    LocalStrainNumberCounter,
    OrganizationProvenanceCode,
    Origin,
    OrganizationSpeciesCode,
    Species,
    Strain,
    StrainTranslation,
)
from .test_admin_species_catalog import FakeResponse, worms_record
from .test_species_review_regressions import SpeciesReviewTransactionTestCase

LIST_URL = "api_admin_institution_species"
DETAIL_URL = "api_admin_institution_species_detail"
STRAINS_URL = "api_admin_institution_species_strains"
HISTORICAL_URL = "api_admin_historical_species_detail"


def _snapshot():
    """Historical identities that institutional writes must never touch."""
    return {
        "species": list(Species.objects.order_by("pk").values("pk", "scientific_name", "worms_aphia_id")),
        "strains": list(Strain.objects.order_by("pk").values(
            "pk", "species_id", "organization_id", "code", "number", "global_identity_id",
        )),
        "boxes": list(Box.objects.order_by("pk").values("pk", "strain_id", "global_code", "status", "organization_id")),
        "codes": list(OrganizationSpeciesCode.objects.order_by("pk").values()),
        "local_identities": list(LocalStrainIdentity.objects.order_by("pk").values()),
        "namespaces": list(BoxCodeNamespace.objects.order_by("pk").values()),
        "counters": list(LocalStrainNumberCounter.objects.order_by("pk").values()),
    }


class InstitutionSpeciesApiBase(TestCase):
    def setUp(self):
        self.a = Organization.objects.create(name="Institution A")
        self.b = Organization.objects.create(name="Institution B")
        users = get_user_model().objects
        self.admin = users.create_user(username="is_admin", email="is_a@example.org", password="secret")
        self.b_admin = users.create_user(username="is_b_admin", email="is_b@example.org", password="secret")
        self.tech = users.create_user(username="is_tech", email="is_t@example.org", password="secret")
        self.viewer = users.create_user(username="is_viewer", email="is_v@example.org", password="secret")
        for user, organization, role in (
            (self.admin, self.a, OrganizationMembership.Role.ADMIN),
            (self.b_admin, self.b, OrganizationMembership.Role.ADMIN),
            (self.tech, self.a, OrganizationMembership.Role.LAB_TECHNICIAN),
            (self.viewer, self.a, OrganizationMembership.Role.VIEWER),
        ):
            OrganizationMembership.objects.create(user=user, organization=organization, role=role)

    def headers(self, organization):
        return {"HTTP_X_ORGANIZATION_ID": str(organization.pk)}

    def register(self, organization, payload, user=None):
        self.client.force_login(user or (self.admin if organization == self.a else self.b_admin))
        return self.client.post(
            reverse(LIST_URL), data=json.dumps(payload), content_type="application/json",
            **self.headers(organization),
        )

    def edit(self, organization, pk, payload, user=None):
        self.client.force_login(user or (self.admin if organization == self.a else self.b_admin))
        return self.client.patch(
            reverse(DETAIL_URL, args=[pk]), data=json.dumps(payload), content_type="application/json",
            **self.headers(organization),
        )

    def get(self, name, organization, *args, user=None):
        self.client.force_login(user or (self.admin if organization == self.a else self.b_admin))
        return self.client.get(reverse(name, args=args), **self.headers(organization))


class InstitutionSpeciesRegistrationTests(InstitutionSpeciesApiBase):
    def test_new_registration_creates_reference_profile_code_and_audit_atomically(self):
        response = self.register(self.a, {"scientific_name": "  Chrysaora   hysoscella "})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        species = Species.objects.get(scientific_name="Chrysaora hysoscella")
        profile = InstitutionSpecies.objects.get(organization=self.a)
        assignment = OrganizationSpeciesCode.objects.get(organization=self.a, species=species)
        self.assertEqual(body, {
            "id": profile.pk, "species_id": species.pk, "scientific_name": "Chrysaora hysoscella",
            "worms_aphia_id": None, "worms_url": None, "species_code": "CHY", "revision": 1, "strain_count": 0,
        })
        self.assertEqual((species.worms_aphia_id, species.common_name), (None, ""))
        self.assertFalse(species.translations.exists())
        self.assertEqual(assignment.code, "CHY")
        audits = list(AuditLog.objects.filter(organization=self.a, user=self.admin).order_by("pk"))
        self.assertEqual(
            [(audit.action, audit.object_type, audit.object_id) for audit in audits],
            [
                (AuditLog.Action.CREATION, "species", str(species.pk)),
                (AuditLog.Action.CREATION, "institution_species", str(profile.pk)),
                (AuditLog.Action.CREATION, "organization_species_code", str(assignment.pk)),
            ],
        )
        self.assertEqual(audits[1].metadata["species_code"], "CHY")
        self.assertTrue(audits[1].metadata["shared_reference_created"])
        self.assertFalse(Strain.objects.filter(species=species).exists())
        with patch.object(services, "allocate_species_code") as allocate:
            self.assertEqual(self.get(DETAIL_URL, self.a, profile.pk).json()["species_code"], assignment.code)
            self.assertEqual(self.get(LIST_URL, self.a).json()["institution_species"][0]["species_code"], assignment.code)
        allocate.assert_not_called()

    def test_client_cannot_supply_code_owner_or_translations(self):
        for extra in (
            {"species_code": "XYZ"}, {"code": "XYZ"}, {"translations": {"fr": {"name": "x"}}},
            {"organization": self.b.pk}, {"species_id": 1}, {"revision": 4}, {"common_name": "x"},
        ):
            with self.subTest(extra=extra):
                response = self.register(self.a, {"scientific_name": "Chrysaora hysoscella", **extra})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(set(response.json()), set(extra))
        self.assertFalse(InstitutionSpecies.objects.exists())
        self.assertFalse(Species.objects.exists())

    def test_invalid_names_and_aphia_ids_are_rejected_without_writes(self):
        for name in ("", "   ", "1234", "Aurelia%", "Aurelia_aurita", "A" * 151, "<b>Aurelia</b>", None, 12):
            with self.subTest(name=name):
                response = self.register(self.a, {"scientific_name": name})
                self.assertEqual((response.status_code, response.json()), (400, {"scientific_name": ["invalid_name"]}))
        for aphia_id in (0, -1, 2147483648, True, "135306", 1.5):
            with self.subTest(aphia_id=aphia_id):
                response = self.register(self.a, {"scientific_name": "Aurelia aurita", "worms_aphia_id": aphia_id})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {"worms_aphia_id": ["invalid_aphia_id"]})
        self.assertEqual(self.register(self.a, {}).json(), {"scientific_name": ["required"]})
        self.assertEqual(self.register(self.a, ["Aurelia aurita"]).status_code, 400)
        self.assertFalse(Species.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_requires_admin_of_explicit_active_institution(self):
        payload = {"scientific_name": "Chrysaora hysoscella"}
        for user in (self.tech, self.viewer, self.b_admin):
            with self.subTest(user=user.username):
                self.assertEqual(self.register(self.a, payload, user=user).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.post(reverse(LIST_URL), data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.client.logout()
        response = self.client.post(
            reverse(LIST_URL), data=json.dumps(payload), content_type="application/json", **self.headers(self.a),
        )
        self.assertIn(response.status_code, (401, 403))
        self.a.is_active = False
        self.a.save(update_fields=["is_active"])
        self.assertEqual(self.register(self.a, payload).status_code, 403)
        self.assertFalse(Species.objects.exists())

    def test_same_name_in_b_reuses_the_shared_reference_without_touching_a(self):
        first = self.register(self.a, {"scientific_name": "Aurelia aurita"}).json()
        a_profile = InstitutionSpecies.objects.get(pk=first["id"])
        response = self.register(self.b, {"scientific_name": "AURELIA  aurita"})
        self.assertEqual(response.status_code, 201, response.content)
        second = response.json()
        self.assertEqual(Species.objects.count(), 1)
        self.assertEqual(first["species_id"], second["species_id"])
        self.assertNotEqual(first["id"], second["id"])
        # Same reference: the same generated code is safe in both institutions.
        self.assertEqual((first["species_code"], second["species_code"]), ("AAU", "AAU"))
        self.assertEqual(second["scientific_name"], "AURELIA aurita")
        a_profile.refresh_from_db()
        self.assertEqual((a_profile.scientific_name, a_profile.revision), ("Aurelia aurita", 1))
        b_audit = AuditLog.objects.filter(organization=self.b, object_type="institution_species").get()
        self.assertFalse(b_audit.metadata["shared_reference_created"])
        self.assertFalse(AuditLog.objects.filter(organization=self.b, object_type="species").exists())

    def test_existing_profile_is_reported_never_duplicated(self):
        created = self.register(self.a, {"scientific_name": "Aurelia aurita"}).json()
        before = _snapshot()
        for name in ("Aurelia aurita", "aurelia  AURITA"):
            with self.subTest(name=name):
                response = self.register(self.a, {"scientific_name": name})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json(), {
                    "code": "institution_species_exists", "institution_species_id": created["id"],
                    "species_id": created["species_id"], "scientific_name": "Aurelia aurita", "species_code": "AAU",
                })
        # After a local correction, the shared name still resolves to this profile.
        self.edit(self.a, created["id"], {"expected_revision": 1, "scientific_name": "Aurelia coerulea",
                                          "worms_aphia_id": None})
        response = self.register(self.a, {"scientific_name": "Aurelia aurita"})
        self.assertEqual((response.status_code, response.json()["institution_species_id"]), (409, created["id"]))
        self.assertEqual(InstitutionSpecies.objects.count(), 1)
        self.assertEqual(_snapshot(), before)

    def test_historical_species_requires_adoption_and_gets_no_implicit_code(self):
        historical = Species.objects.create(scientific_name="Cassiopea andromeda")
        strain = Strain.objects.create(species=historical, code="legacy-1")  # unowned
        Box.objects.create(organization=self.a, strain=strain, global_code="LEG-1.001", box_number="001")
        coded = Species.objects.create(scientific_name="Aurelia aurita")
        OrganizationSpeciesCode.objects.create(organization=self.a, species=coded, code="AUR")
        owned = Species.objects.create(scientific_name="Rhizostoma pulmo")
        Strain.objects.create(species=owned, organization=self.a, code="RPU-FRA-1")
        before = _snapshot()
        for species, code in ((historical, None), (coded, "AUR"), (owned, None)):
            with self.subTest(species=species.scientific_name):
                response = self.register(self.a, {"scientific_name": species.scientific_name.upper()})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json(), {
                    "code": "historical_adoption_required", "species_id": species.pk,
                    "shared_scientific_name": species.scientific_name, "species_code": code,
                })
        self.assertEqual(_snapshot(), before)
        self.assertFalse(InstitutionSpecies.objects.exists())
        self.assertFalse(AuditLog.objects.exists())
        # The same references are new registrations for an institution without history.
        response = self.register(self.b, {"scientific_name": "Cassiopea andromeda"})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["species_id"], historical.pk)
        self.assertFalse(OrganizationSpeciesCode.objects.filter(organization=self.a, species=historical).exists())

    def test_existing_codes_elsewhere_are_preserved_and_global_namespace_protected(self):
        other = Species.objects.create(scientific_name="Aurelia aurantia")
        OrganizationSpeciesCode.objects.create(organization=self.b, species=other, code="AAU")
        before = list(OrganizationSpeciesCode.objects.order_by("pk").values())
        response = self.register(self.a, {"scientific_name": "Aurelia aurita"})
        self.assertEqual(response.json()["species_code"], "AAR")
        self.assertEqual(list(OrganizationSpeciesCode.objects.filter(pk=before[0]["id"]).values()), before)

    def test_code_failure_rolls_back_reference_and_profile(self):
        response = self.register(self.a, {"scientific_name": "Aa"})
        self.assertEqual((response.status_code, response.json()), (409, {"code": "name_not_derivable"}))
        self.assertFalse(Species.objects.exists())
        self.assertFalse(InstitutionSpecies.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_audit_failure_rolls_back_everything(self):
        original = AuditLog.objects.create
        calls = []

        def fail_third(*args, **kwargs):
            calls.append(kwargs.get("object_type"))
            if len(calls) == 3:
                raise RuntimeError("audit failure")
            return original(*args, **kwargs)

        self.client.force_login(self.admin)
        with patch.object(AuditLog.objects, "create", side_effect=fail_third), self.assertRaises(RuntimeError):
            self.register(self.a, {"scientific_name": "Chrysaora hysoscella"})
        self.assertEqual(calls, ["species", "institution_species", "organization_species_code"])
        self.assertFalse(Species.objects.exists())
        self.assertFalse(InstitutionSpecies.objects.exists())
        self.assertFalse(OrganizationSpeciesCode.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_shared_reference_created_outside_the_gate_is_resolved_not_duplicated(self):
        """A legacy writer commits the same name between resolution and insert."""
        original = services._name_matches
        calls = []

        def miss_first_species_lookup(queryset, name, field="scientific_name"):
            if queryset.model is Species and not calls:
                calls.append(name)
                Species.objects.create(scientific_name="Aurelia aurita")
                return queryset.none()
            return original(queryset, name, field)

        with patch.object(services, "_name_matches", side_effect=miss_first_species_lookup):
            response = self.register(self.a, {"scientific_name": "Aurelia aurita"})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Species.objects.count(), 1)
        self.assertFalse(AuditLog.objects.filter(object_type="species").exists())


class InstitutionSpeciesWormsRegistrationTests(InstitutionSpeciesApiBase):
    @patch("apps.taxonomy.worms.urlopen")
    def test_verified_aphia_id_belongs_to_the_profile_not_the_shared_reference(self, urlopen):
        urlopen.return_value = FakeResponse(worms_record(287206, "Aurelia coerulea"))
        response = self.register(self.a, {"scientific_name": "Aurelia coerulea", "worms_aphia_id": 287206})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["worms_aphia_id"], 287206)
        self.assertEqual(response.json()["worms_url"], "https://www.marinespecies.org/aphia.php?p=taxdetails&id=287206")
        self.assertIsNone(Species.objects.get(scientific_name="Aurelia coerulea").worms_aphia_id)
        self.assertEqual(urlopen.call_count, 1)
        # Same AphiaID in another institution.
        response = self.register(self.b, {"scientific_name": "Aurelia coerulea", "worms_aphia_id": 287206})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(InstitutionSpecies.objects.filter(worms_aphia_id=287206).count(), 2)

    @patch("apps.taxonomy.worms.urlopen")
    def test_rejected_references_write_nothing(self, urlopen):
        cases = (
            (worms_record(135306, "Aurelia aurita"), "Medusa aurita", 135306, "worms_reference_name_mismatch"),
            (worms_record(287206, "Aurelia coerulea"), "aurelia coerulea", 287206, "worms_reference_name_mismatch"),
            (worms_record(135263, "Aurelia", rank="Genus", taxonRankID=180), "Aurelia", 135263, "worms_reference_rank"),
            (b"", "Aurelia coerulea", 999_999_999, "worms_reference_not_found"),
        )
        for body, name, aphia_id, code in cases:
            with self.subTest(code=code, name=name):
                urlopen.return_value = FakeResponse(body, status=204 if body == b"" else 200)
                response = self.register(self.a, {"scientific_name": name, "worms_aphia_id": aphia_id})
                self.assertEqual((response.status_code, response.json()), (400, {"code": code}))
        self.assertFalse(Species.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    @patch("apps.taxonomy.worms.urlopen")
    def test_synonym_is_kept_as_chosen_never_replaced_by_accepted_name(self, urlopen):
        urlopen.return_value = FakeResponse(worms_record(
            1_000_300, "Chrysaora isosceles", status="unaccepted", valid_id=1_000_301, valid_name="Chrysaora hysoscella",
        ))
        response = self.register(self.a, {"scientific_name": "Chrysaora isosceles", "worms_aphia_id": 1_000_300})
        self.assertEqual(response.json()["scientific_name"], "Chrysaora isosceles")
        self.assertFalse(Species.objects.filter(scientific_name="Chrysaora hysoscella").exists())

    @patch("apps.taxonomy.worms.urlopen")
    def test_unavailable_worms_refuses_reference_but_manual_registration_needs_no_network(self, urlopen):
        for outcome in (socket.timeout("t"), URLError("offline"), HTTPError("u", 500, "e", {}, io.BytesIO(b"")),
                        FakeResponse(b"<html>maintenance</html>")):
            with self.subTest(outcome=repr(outcome)[:30]):
                urlopen.reset_mock()
                urlopen.side_effect = outcome if isinstance(outcome, Exception) else None
                urlopen.return_value = outcome
                response = self.register(self.a, {"scientific_name": "Aurelia coerulea", "worms_aphia_id": 287206})
                self.assertEqual((response.status_code, response.json()), (503, {"code": "worms_reference_unavailable"}))
        self.assertFalse(Species.objects.exists())
        urlopen.reset_mock()
        urlopen.side_effect = None
        response = self.register(self.a, {"scientific_name": "Aurelia coerulea"})
        self.assertEqual(response.status_code, 201, response.content)
        urlopen.assert_not_called()

    @patch("apps.taxonomy.worms.urlopen")
    def test_worms_call_happens_before_any_transaction(self, urlopen):
        depth = len(connection.atomic_blocks)

        def answer(request, **kwargs):
            self.assertEqual(len(connection.atomic_blocks), depth)
            self.assertFalse(Species.objects.exists())
            return FakeResponse(worms_record(287206, "Aurelia coerulea"))

        urlopen.side_effect = answer
        response = self.register(self.a, {"scientific_name": "Aurelia coerulea", "worms_aphia_id": 287206})
        self.assertEqual(response.status_code, 201, response.content)

    @patch("apps.taxonomy.worms.urlopen")
    def test_shared_reference_with_contradicting_aphia_is_a_conflict_not_a_merge(self, urlopen):
        homonym = Species.objects.create(scientific_name="Aurelia aurita", worms_aphia_id=135306)
        urlopen.return_value = FakeResponse(worms_record(1_000_500, "Aurelia aurita"))
        response = self.register(self.a, {"scientific_name": "Aurelia aurita", "worms_aphia_id": 1_000_500})
        self.assertEqual(response.json(), {"code": "shared_reference_conflict", "species_id": homonym.pk})
        self.assertEqual(response.status_code, 409)
        homonym.refresh_from_db()
        self.assertEqual(homonym.worms_aphia_id, 135306)
        self.assertFalse(InstitutionSpecies.objects.exists())
        # A matching or absent reference reuses the shared record unchanged.
        urlopen.return_value = FakeResponse(worms_record(135306, "Aurelia aurita"))
        response = self.register(self.a, {"scientific_name": "Aurelia aurita", "worms_aphia_id": 135306})
        self.assertEqual((response.status_code, response.json()["species_id"]), (201, homonym.pk))

    @patch("apps.taxonomy.worms.urlopen")
    def test_shared_aphia_on_another_name_does_not_merge_records(self, urlopen):
        historical = Species.objects.create(scientific_name="Medusa lunaris", worms_aphia_id=1_000_100)
        urlopen.return_value = FakeResponse(worms_record(1_000_100, "Medusa lunata"))
        response = self.register(self.a, {"scientific_name": "Medusa lunata", "worms_aphia_id": 1_000_100})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotEqual(response.json()["species_id"], historical.pk)
        historical.refresh_from_db()
        self.assertEqual((historical.scientific_name, historical.worms_aphia_id), ("Medusa lunaris", 1_000_100))
        # One institution cannot register the same AphiaID twice under another
        # name, even if the WoRMS record name later matches that other name.
        urlopen.return_value = FakeResponse(worms_record(1_000_100, "Medusa lunata var. x"))
        response = self.register(self.a, {"scientific_name": "Medusa lunata var. x", "worms_aphia_id": 1_000_100})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "institution_aphia_id_exists")


class InstitutionSpeciesEditTests(InstitutionSpeciesApiBase):
    def setUp(self):
        super().setUp()
        self.shared = Species.objects.create(scientific_name="Aurelia aurita", worms_aphia_id=135306)
        self.a_code = OrganizationSpeciesCode.objects.create(organization=self.a, species=self.shared, code="AAU")
        self.b_code = OrganizationSpeciesCode.objects.create(organization=self.b, species=self.shared, code="AUR")
        self.profile = InstitutionSpecies.objects.create(
            organization=self.a, species=self.shared, scientific_name="Aurelia aurita", worms_aphia_id=135306,
        )
        self.b_profile = InstitutionSpecies.objects.create(
            organization=self.b, species=self.shared, scientific_name="Aurelia aurita", worms_aphia_id=135306,
        )
        identity = GlobalStrainIdentity.objects.create()
        self.strain = Strain.objects.create(
            species=self.shared, organization=self.a, code="AAU-FRA-1", number=1, origin_code="FRA",
            global_identity=identity,
        )
        LocalStrainIdentity.objects.create(strain=self.strain, species_code_assignment=self.a_code)
        Box.objects.create(organization=self.a, strain=self.strain, global_code="AAU-FRA-1.001", box_number="001")

    def payload(self, name, aphia_id=None, revision=1):
        return {"expected_revision": revision, "scientific_name": name, "worms_aphia_id": aphia_id}

    def test_manual_correction_without_worms_is_isolated_and_audited(self):
        before = _snapshot()
        with patch("apps.taxonomy.worms.urlopen") as urlopen:
            response = self.edit(self.a, self.profile.pk, self.payload("  Aurelia   coerulea "))
        urlopen.assert_not_called()
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual((body["scientific_name"], body["worms_aphia_id"], body["revision"], body["species_code"]),
                         ("Aurelia coerulea", None, 2, "AAU"))
        self.assertEqual(body["species_id"], self.shared.pk)
        self.assertEqual(_snapshot(), before, "Shared name, AphiaID, codes and identities are untouched")
        self.b_profile.refresh_from_db()
        self.assertEqual((self.b_profile.scientific_name, self.b_profile.worms_aphia_id, self.b_profile.revision),
                         ("Aurelia aurita", 135306, 1))
        audit = AuditLog.objects.get()
        self.assertEqual((audit.organization_id, audit.user_id, audit.action, audit.object_type, audit.object_id),
                         (self.a.pk, self.admin.pk, AuditLog.Action.UPDATE, "institution_species", str(self.profile.pk)))
        self.assertEqual(audit.metadata, {
            "species_id": self.shared.pk,
            "revision": {"before": 1, "after": 2},
            "changes": {
                "scientific_name": {"before": "Aurelia aurita", "after": "Aurelia coerulea"},
                "worms_aphia_id": {"before": 135306, "after": None},
            },
        })

    @patch("apps.taxonomy.worms.urlopen")
    def test_edit_refuses_another_local_profiles_aphia_id_without_writes(self, urlopen):
        other = Species.objects.create(scientific_name="Medusa lunaris")
        occupied = InstitutionSpecies.objects.create(
            organization=self.a, species=other,
            scientific_name="Medusa lunaris", worms_aphia_id=1_000_100,
        )
        urlopen.return_value = FakeResponse(worms_record(1_000_100, "Medusa lunata"))
        before = _snapshot()
        audit_count = AuditLog.objects.count()
        response = self.edit(self.a, self.profile.pk, {
            "expected_revision": self.profile.revision,
            "scientific_name": "Medusa lunata", "worms_aphia_id": 1_000_100,
        })
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json(), {
            "code": "institution_aphia_id_exists",
            "institution_species_id": occupied.pk, "scientific_name": "Medusa lunaris",
        })
        self.profile.refresh_from_db()
        self.assertEqual(
            (self.profile.scientific_name, self.profile.worms_aphia_id, self.profile.revision),
            ("Aurelia aurita", 135306, 1),
        )
        self.assertEqual(_snapshot(), before)
        self.assertEqual(AuditLog.objects.count(), audit_count)

    @patch("apps.taxonomy.worms.urlopen")
    def test_edit_does_not_treat_another_institutions_aphia_id_as_a_collision(self, urlopen):
        urlopen.return_value = FakeResponse(worms_record(135306, "Aurelia aurita var. x"))
        response = self.edit(self.a, self.profile.pk, {
            "expected_revision": self.profile.revision,
            "scientific_name": "Aurelia aurita var. x", "worms_aphia_id": 135306,
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.b_profile.refresh_from_db()
        self.assertEqual(
            (self.b_profile.scientific_name, self.b_profile.worms_aphia_id, self.b_profile.revision),
            ("Aurelia aurita", 135306, 1),
        )

    def test_stale_revision_is_refused_without_writes(self):
        self.assertEqual(self.edit(self.a, self.profile.pk, self.payload("Aurelia coerulea")).status_code, 200)
        response = self.edit(self.a, self.profile.pk, self.payload("Aurelia labiata"))
        self.assertEqual((response.status_code, response.json()), (409, {"code": "stale_revision", "current_revision": 2}))
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.scientific_name, self.profile.revision), ("Aurelia coerulea", 2))
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_revision_and_final_state_are_required(self):
        for payload in (
            {"scientific_name": "Aurelia coerulea", "worms_aphia_id": None},
            {"expected_revision": 1, "scientific_name": "Aurelia coerulea"},
            {"expected_revision": 1, "worms_aphia_id": None},
        ):
            with self.subTest(payload=payload):
                response = self.edit(self.a, self.profile.pk, payload)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(list(response.json().values()), [["required"]])
        for revision in (0, -1, True, "1", None, 1.0):
            with self.subTest(revision=revision):
                response = self.edit(self.a, self.profile.pk, self.payload("Aurelia coerulea", revision=revision))
                self.assertEqual(response.json(), {"expected_revision": ["invalid_revision"]})
        for extra in ({"species_id": 3}, {"species_code": "XYZ"}, {"revision": 3}, {"organization": self.b.pk}):
            with self.subTest(extra=extra):
                response = self.edit(self.a, self.profile.pk, {**self.payload("Aurelia coerulea"), **extra})
                self.assertEqual((response.status_code, set(response.json())), (400, set(extra)))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.revision, 1)
        self.assertFalse(AuditLog.objects.exists())

    def test_unchanged_submission_is_a_no_op_without_audit(self):
        response = self.edit(self.a, self.profile.pk, self.payload("Aurelia  aurita", 135306))
        self.assertEqual((response.status_code, response.json()["revision"]), (200, 1))
        self.assertFalse(AuditLog.objects.exists())

    def test_cross_institution_and_unauthorized_edits_are_refused(self):
        response = self.edit(self.a, self.b_profile.pk, self.payload("Aurelia coerulea"))
        self.assertEqual(response.status_code, 404)
        for user in (self.tech, self.viewer, self.b_admin):
            with self.subTest(user=user.username):
                self.assertEqual(self.edit(self.a, self.profile.pk, self.payload("Aurelia coerulea"), user=user).status_code, 403)
        self.assertEqual(self.edit(self.a, 999999, self.payload("Aurelia coerulea")).status_code, 404)
        self.b_profile.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.revision, self.b_profile.revision), (1, 1))
        self.assertFalse(AuditLog.objects.exists())

    def test_normalized_local_name_collisions_are_refused(self):
        other_shared = Species.objects.create(scientific_name="Cassiopea andromeda")
        other = InstitutionSpecies.objects.create(organization=self.a, species=other_shared, scientific_name="Cassiopea andromeda")
        response = self.edit(self.a, self.profile.pk, self.payload("CASSIOPEA  andromeda"))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {
            "code": "institution_species_name_exists", "institution_species_id": other.pk,
            "scientific_name": "Cassiopea andromeda",
        })
        # Different institution: the same local name is allowed.
        response = self.edit(self.b, self.b_profile.pk, self.payload("Cassiopea andromeda"))
        self.assertEqual(response.status_code, 200, response.content)

    def test_local_name_cannot_hide_a_historical_reference_used_here(self):
        historical = Species.objects.create(scientific_name="Rhizostoma pulmo")
        Strain.objects.create(species=historical, organization=self.a, code="RPU-FRA-1")
        response = self.edit(self.a, self.profile.pk, self.payload("Rhizostoma  pulmo"))
        self.assertEqual(response.json(), {"code": "historical_name_collision", "species_id": historical.pk, "species_code": None})
        # B has no history with that reference.
        self.assertEqual(self.edit(self.b, self.b_profile.pk, self.payload("Rhizostoma pulmo")).status_code, 200)

    def test_database_constraint_race_is_reported_as_conflict(self):
        other_shared = Species.objects.create(scientific_name="Cassiopea andromeda")

        def race(*args, **kwargs):
            # Another writer commits the name after the pre-check.
            InstitutionSpecies.objects.create(organization=self.a, species=other_shared, scientific_name="Aurelia coerulea")
            return original(*args, **kwargs)

        original = InstitutionSpecies.objects.compare_and_swap
        with patch.object(InstitutionSpecies.objects, "compare_and_swap", side_effect=race):
            response = self.edit(self.a, self.profile.pk, self.payload("Aurelia coerulea"))
        self.assertEqual((response.status_code, response.json()["code"]), (409, "institution_species_name_exists"))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.revision, 1)
        self.assertFalse(AuditLog.objects.exists())

    def test_audit_failure_rolls_back_the_update(self):
        self.client.force_login(self.admin)
        with patch.object(AuditLog.objects, "create", side_effect=RuntimeError("audit")), self.assertRaises(RuntimeError):
            self.edit(self.a, self.profile.pk, self.payload("Aurelia coerulea"))
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.scientific_name, self.profile.revision), ("Aurelia aurita", 1))

    @patch("apps.taxonomy.worms.urlopen")
    def test_selected_verified_proposal_is_saved(self, urlopen):
        urlopen.return_value = FakeResponse(worms_record(287206, "Aurelia coerulea"))
        response = self.edit(self.a, self.profile.pk, self.payload("Aurelia coerulea", 287206))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["scientific_name"], response.json()["worms_aphia_id"]), ("Aurelia coerulea", 287206))
        self.assertEqual(urlopen.call_args.args[0].full_url, "https://www.marinespecies.org/rest/AphiaRecordByAphiaID/287206")
        self.shared.refresh_from_db()
        self.assertEqual((self.shared.scientific_name, self.shared.worms_aphia_id), ("Aurelia aurita", 135306))

    @patch("apps.taxonomy.worms.urlopen")
    def test_adjusted_name_cannot_keep_an_incompatible_aphia_id(self, urlopen):
        urlopen.return_value = FakeResponse(worms_record(135306, "Aurelia aurita"))
        # Proposal adjusted (or name corrected) while keeping the old AphiaID.
        response = self.edit(self.a, self.profile.pk, self.payload("Aurelia aurita alba", 135306))
        self.assertEqual((response.status_code, response.json()), (400, {"code": "worms_reference_name_mismatch"}))
        self.profile.refresh_from_db()
        self.assertEqual((self.profile.scientific_name, self.profile.worms_aphia_id, self.profile.revision),
                         ("Aurelia aurita", 135306, 1))
        # Explicit removal of the reference is the accepted manual path.
        urlopen.reset_mock()
        response = self.edit(self.a, self.profile.pk, self.payload("Aurelia aurita alba", None))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.json()["worms_aphia_id"])
        urlopen.assert_not_called()

    @patch("apps.taxonomy.worms.urlopen")
    def test_accepted_name_is_never_substituted(self, urlopen):
        urlopen.return_value = FakeResponse(worms_record(
            1_000_302, "Rhizostoma octopus", status="unaccepted", valid_id=1_000_303, valid_name="Rhizostoma pulmo",
        ))
        response = self.edit(self.a, self.profile.pk, self.payload("Rhizostoma pulmo", 1_000_302))
        self.assertEqual(response.json(), {"code": "worms_reference_name_mismatch"})
        response = self.edit(self.a, self.profile.pk, self.payload("Rhizostoma octopus", 1_000_302))
        self.assertEqual(response.json()["scientific_name"], "Rhizostoma octopus")

    @patch("apps.taxonomy.worms.urlopen")
    def test_worms_unavailable_blocks_reference_but_not_explicit_manual_update(self, urlopen):
        urlopen.side_effect = URLError("offline")
        response = self.edit(self.a, self.profile.pk, self.payload("Aurelia coerulea", 287206))
        self.assertEqual((response.status_code, response.json()), (503, {"code": "worms_reference_unavailable"}))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.revision, 1)
        urlopen.reset_mock()
        response = self.edit(self.a, self.profile.pk, self.payload("Aurelia coerulea", None))
        self.assertEqual(response.status_code, 200, response.content)
        urlopen.assert_not_called()

    @patch("apps.taxonomy.worms.urlopen")
    def test_worms_call_precedes_transaction_and_locks(self, urlopen):
        depth = len(connection.atomic_blocks)

        def answer(request, **kwargs):
            self.assertEqual(len(connection.atomic_blocks), depth)
            return FakeResponse(worms_record(287206, "Aurelia coerulea"))

        urlopen.side_effect = answer
        self.assertEqual(self.edit(self.a, self.profile.pk, self.payload("Aurelia coerulea", 287206)).status_code, 200)

    @patch("apps.taxonomy.worms.urlopen")
    def test_concurrent_change_during_verification_is_stale(self, urlopen):
        def answer(request, **kwargs):
            InstitutionSpecies.objects.compare_and_swap(
                organization=self.a, pk=self.profile.pk, expected_revision=1,
                scientific_name="Concurrent name", worms_aphia_id=None,
            )
            return FakeResponse(worms_record(287206, "Aurelia coerulea"))

        urlopen.side_effect = answer
        response = self.edit(self.a, self.profile.pk, self.payload("Aurelia coerulea", 287206))
        self.assertEqual(response.json(), {"code": "stale_revision", "current_revision": 2})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.scientific_name, "Concurrent name")


class InstitutionSpeciesReadTests(InstitutionSpeciesApiBase):
    def setUp(self):
        super().setUp()
        self.shared = Species.objects.create(scientific_name="Aurelia aurita", worms_aphia_id=135306)
        self.a_code = OrganizationSpeciesCode.objects.create(organization=self.a, species=self.shared, code="AAU")
        OrganizationSpeciesCode.objects.create(organization=self.b, species=self.shared, code="BBU")
        self.profile = InstitutionSpecies.objects.create(
            organization=self.a, species=self.shared, scientific_name="Aurelia coerulea", worms_aphia_id=287206,
        )
        self.b_profile = InstitutionSpecies.objects.create(
            organization=self.b, species=self.shared, scientific_name="B private interpretation",
        )
        self.owned = Strain.objects.create(species=self.shared, organization=self.a, code="AAU-FRA-1", number=1, origin_code="FRA")
        LocalStrainIdentity.objects.create(strain=self.owned, species_code_assignment=self.a_code)
        Box.objects.create(organization=self.a, strain=self.owned, global_code="AAU-FRA-1.001", box_number="001")
        self.b_strain = Strain.objects.create(species=self.shared, organization=self.b, code="BBU-JPN-1")
        self.legacy = Strain.objects.create(species=self.shared, code="legacy")
        Box.objects.create(organization=self.a, strain=self.legacy, global_code="LEG.001", box_number="001",
                           status=Box.Status.INACTIVE)
        self.historical = Species.objects.create(scientific_name="Cassiopea andromeda")
        Strain.objects.create(species=self.historical, organization=self.a, code="CAN-FRA-1")
        self.unrelated = Species.objects.create(scientific_name="Unused shared species")
        b_only = Species.objects.create(scientific_name="B only species")
        Strain.objects.create(species=b_only, organization=self.b, code="BON-1")

    def test_list_returns_only_active_institution_information(self):
        response = self.get(LIST_URL, self.a)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["institution_species"], [{
            "id": self.profile.pk, "species_id": self.shared.pk, "scientific_name": "Aurelia coerulea",
            "worms_aphia_id": 287206, "worms_url": "https://www.marinespecies.org/aphia.php?p=taxdetails&id=287206",
            "species_code": "AAU", "revision": 1, "strain_count": 2,
        }])
        self.assertEqual(body["historical_species"], [{
            "species_id": self.historical.pk, "shared_scientific_name": "Cassiopea andromeda",
            "species_code": "CAN", "strain_count": 1, "requires_historical_adoption": True,
        }])
        text = json.dumps(body)
        for private in ("B private interpretation", "BBU", "B only species", "Unused shared species", "BBU-JPN-1"):
            self.assertNotIn(private, text)
        self.assertFalse(InstitutionSpecies.objects.filter(species=self.historical).exists())
        self.assertFalse(OrganizationSpeciesCode.objects.filter(species=self.historical).exists())

    def test_detail_and_strains_are_scoped(self):
        response = self.get(DETAIL_URL, self.a, self.profile.pk)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["id"], body["species_id"], body["revision"], body["species_code"]),
                         (self.profile.pk, self.shared.pk, 1, "AAU"))
        strains = {item["code"]: item for item in body["strains"]}
        self.assertEqual(set(strains), {"AAU-FRA-1", "legacy"})
        self.assertTrue(strains["AAU-FRA-1"]["is_owned"])
        self.assertFalse(strains["legacy"]["is_owned"])
        self.assertEqual(strains["AAU-FRA-1"]["active_box_count"], 1)
        response = self.get(STRAINS_URL, self.a, self.profile.pk)
        self.assertEqual([item["code"] for item in response.json()["strains"]], ["AAU-FRA-1", "legacy"])
        for name in (DETAIL_URL, STRAINS_URL):
            self.assertEqual(self.get(name, self.a, self.b_profile.pk).status_code, 404)
            self.assertNotIn("B private", self.get(name, self.a, self.b_profile.pk).content.decode())

    def test_strains_expose_only_verified_recorded_provenance(self):
        own = BiologicalProvenance.objects.create(name="Recorded wild provenance")
        foreign = BiologicalProvenance.objects.create(name="B private provenance")
        own_code = OrganizationProvenanceCode.objects.create(
            organization=self.a, biological_provenance=own, code="WLD")
        b_code = OrganizationProvenanceCode.objects.create(
            organization=self.b, biological_provenance=foreign, code="FRB")
        recorded = Strain.objects.create(species=self.shared, organization=self.a, code="AAU-WLD-2",
                                         number=2, origin_code="WLD")
        LocalStrainIdentity.objects.create(strain=recorded, species_code_assignment=self.a_code,
                                           provenance_code_assignment=own_code)
        # A foreign assignment is never evidence: the strain is unusable and shows nothing.
        mismatched = Strain.objects.create(species=self.shared, organization=self.a, code="AAU-FRB-3", number=3)
        LocalStrainIdentity.objects.create(strain=mismatched, species_code_assignment=self.a_code,
                                           provenance_code_assignment=b_code)
        strains = {item["code"]: item for item in self.get(DETAIL_URL, self.a, self.profile.pk).json()["strains"]}
        self.assertEqual(strains["AAU-WLD-2"]["recorded_provenance_name"], "Recorded wild provenance")
        self.assertTrue(strains["AAU-WLD-2"]["is_box_usable"])
        # No recorded provenance stays null, never a placeholder or the BBB code.
        self.assertIsNone(strains["AAU-FRA-1"]["recorded_provenance_name"])
        self.assertIsNone(strains["legacy"]["recorded_provenance_name"])
        self.assertIsNone(strains["AAU-FRB-3"]["recorded_provenance_name"])
        self.assertFalse(strains["AAU-FRB-3"]["is_box_usable"])
        listed = self.get(STRAINS_URL, self.a, self.profile.pk).json()["strains"]
        self.assertEqual({item["code"]: item["recorded_provenance_name"] for item in listed}["AAU-WLD-2"],
                         "Recorded wild provenance")
        self.assertNotIn("B private provenance", json.dumps(listed))

    def test_reads_require_admin_of_explicit_context(self):
        for user in (self.tech, self.viewer, self.b_admin):
            with self.subTest(user=user.username):
                self.assertEqual(self.get(LIST_URL, self.a, user=user).status_code, 403)
                self.assertEqual(self.get(DETAIL_URL, self.a, self.profile.pk, user=user).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse(LIST_URL)).status_code, 403)
        self.assertEqual(self.client.get(reverse(LIST_URL), HTTP_X_ORGANIZATION_ID=str(self.b.pk)).status_code, 403)

    def test_historical_detail_is_read_only_and_limited_to_references_used_here(self):
        before = (_snapshot(), list(InstitutionSpecies.objects.values()))
        response = self.get(HISTORICAL_URL, self.a, self.historical.pk)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(
            {key: body[key] for key in ("species_id", "shared_scientific_name", "species_code", "strain_count",
                                        "requires_historical_adoption")},
            {"species_id": self.historical.pk, "shared_scientific_name": "Cassiopea andromeda",
             "species_code": "CAN", "strain_count": 1, "requires_historical_adoption": True},
        )
        self.assertEqual([item["code"] for item in body["strains"]], ["CAN-FRA-1"])
        # An own profile keeps this read-only URL valid after explicit resolution.
        alias = self.get(HISTORICAL_URL, self.a, self.shared.pk)
        self.assertEqual(alias.status_code, 200)
        self.assertEqual(alias.json()["institution_species_id"], self.profile.pk)
        self.assertEqual(alias.json()["scientific_name"], self.profile.scientific_name)
        self.assertFalse(alias.json()["requires_historical_adoption"])
        # Unused or another institution's references remain inaccessible.
        for species in (self.unrelated, Species.objects.get(scientific_name="B only species")):
            with self.subTest(species=species.scientific_name):
                self.assertEqual(self.get(HISTORICAL_URL, self.a, species.pk).status_code, 404)
        self.assertEqual(self.get(HISTORICAL_URL, self.a, 999999).status_code, 404)
        self.assertEqual(self.get(HISTORICAL_URL, self.b, self.historical.pk).status_code, 404)
        for user in (self.tech, self.viewer, self.b_admin):
            with self.subTest(user=user.username):
                self.assertEqual(self.get(HISTORICAL_URL, self.a, self.historical.pk, user=user).status_code, 403)
        self.assertEqual((_snapshot(), list(InstitutionSpecies.objects.values())), before)
        self.assertFalse(AuditLog.objects.exists())

    def test_recorded_origin_gps_and_names_are_read_only_and_preserve_zero(self):
        origin = Origin.objects.create(
            source_type=Origin.SourceType.FIELD_COLLECTION, latitude=0, longitude=0,
            origin_institution_name="Recorded provider", description="Recorded event",
        )
        self.owned.origin = origin
        self.owned.save(update_fields=["origin"])
        self.legacy.origin = origin
        self.legacy.save(update_fields=["origin"])
        StrainTranslation.objects.create(strain=self.owned, language_code="fr", name="Recorded name")
        before = (_snapshot(), list(Origin.objects.values()), list(StrainTranslation.objects.values()))
        for route in (DETAIL_URL, STRAINS_URL):
            items = {item["id"]: item for item in self.get(route, self.a, self.profile.pk).json()["strains"]}
            self.assertEqual(items[self.owned.pk]["translations"]["fr"]["name"], "Recorded name")
            self.assertEqual(items[self.owned.pk]["origin"]["latitude"], "0.000000")
            self.assertEqual(items[self.owned.pk]["origin"]["longitude"], "0.000000")
            self.assertIsNone(items[self.owned.pk]["origin"]["event_date"])
            self.assertEqual(items[self.owned.pk]["origin"]["origin_institution_name"], "Recorded provider")
            self.assertIsNone(items[self.legacy.pk]["origin"])
        self.assertEqual((_snapshot(), list(Origin.objects.values()), list(StrainTranslation.objects.values())), before)

    def test_reads_never_write(self):
        before = (_snapshot(), list(InstitutionSpecies.objects.values()))
        with patch("apps.taxonomy.worms.urlopen") as urlopen:
            self.get(LIST_URL, self.a)
            self.get(DETAIL_URL, self.a, self.profile.pk)
            self.get(STRAINS_URL, self.a, self.profile.pk)
        urlopen.assert_not_called()
        self.assertEqual((_snapshot(), list(InstitutionSpecies.objects.values())), before)
        self.assertFalse(AuditLog.objects.exists())


class LegacySharedSpeciesProtectionTests(InstitutionSpeciesApiBase):
    def setUp(self):
        super().setUp()
        self.shared = Species.objects.create(scientific_name="Aurelia aurita", worms_aphia_id=135306)

    def patch_species(self, organization, species, payload):
        self.client.force_login(self.admin if organization == self.a else self.b_admin)
        return self.client.patch(
            reverse("api_taxonomy_species_detail", args=[species.pk]), data=json.dumps(payload),
            content_type="application/json", **self.headers(organization),
        )

    def test_reference_used_by_another_institution_cannot_be_modified(self):
        foreign_evidence = (
            lambda: OrganizationSpeciesCode.objects.create(organization=self.b, species=self.shared, code="AAU"),
            lambda: Strain.objects.create(species=self.shared, organization=self.b, code="B-1"),
            lambda: Box.objects.create(organization=self.b, strain=Strain.objects.create(species=self.shared, code="L"),
                                       global_code="L.001", box_number="001"),
            lambda: InstitutionSpecies.objects.create(organization=self.b, species=self.shared, scientific_name="B name"),
        )
        for create_evidence in foreign_evidence:
            with self.subTest(evidence=create_evidence):
                InstitutionSpecies.objects.all().delete()
                Box.objects.all().delete()
                Strain.objects.all().delete()
                OrganizationSpeciesCode.objects.all().delete()
                create_evidence()
                for payload in ({"scientific_name": "Aurelia coerulea"}, {"worms_aphia_id": 1},
                                {"notes": "x"}, {"translations": {"fr": {"name": "Méduse"}}}):
                    response = self.patch_species(self.a, self.shared, payload)
                    self.assertEqual(response.status_code, 409, response.content)
                    self.assertEqual(response.json()["error"], "shared_species_used_by_another_institution")
        self.shared.refresh_from_db()
        self.assertEqual((self.shared.scientific_name, self.shared.worms_aphia_id, self.shared.notes),
                         ("Aurelia aurita", 135306, ""))
        self.assertFalse(AuditLog.objects.exists())

    def test_reference_with_institutional_information_keeps_name_and_aphia(self):
        InstitutionSpecies.objects.create(organization=self.a, species=self.shared, scientific_name="Local name")
        for payload in ({"scientific_name": "Aurelia coerulea"}, {"worms_aphia_id": 1}):
            response = self.patch_species(self.a, self.shared, payload)
            self.assertEqual((response.status_code, response.json()["error"]), (409, "shared_species_reference_frozen"))
        response = self.patch_species(self.a, self.shared, {"notes": "Single institution note"})
        self.assertEqual(response.status_code, 200, response.content)
        self.shared.refresh_from_db()
        self.assertEqual((self.shared.scientific_name, self.shared.worms_aphia_id), ("Aurelia aurita", 135306))

    def test_single_institution_legacy_edit_keeps_current_behavior(self):
        OrganizationSpeciesCode.objects.create(organization=self.a, species=self.shared, code="AAU")
        response = self.patch_species(self.a, self.shared, {"scientific_name": "Aurelia coerulea"})
        self.assertEqual(response.status_code, 200, response.content)

    def test_django_admin_is_superuser_only_and_keeps_reference_identity(self):
        species_admin = admin_site._registry[Species]
        inline = species_admin.inlines[0](Species, admin_site)
        request = RequestFactory().get("/")
        staff = get_user_model().objects.create_user(username="staff", email="s@example.org", is_staff=True)
        staff.user_permissions.set(Permission.objects.filter(content_type__app_label="taxonomy"))
        request.user = get_user_model().objects.get(pk=staff.pk)
        self.assertTrue(request.user.has_perm("taxonomy.change_species"))
        self.assertFalse(species_admin.has_add_permission(request))
        self.assertFalse(species_admin.has_change_permission(request, self.shared))
        self.assertFalse(species_admin.has_delete_permission(request, self.shared))
        self.assertFalse(inline.has_add_permission(request, self.shared))
        self.assertFalse(inline.has_change_permission(request, self.shared))
        self.assertFalse(inline.has_delete_permission(request, self.shared))
        request.user = get_user_model().objects.create_superuser(username="root", email="r@example.org", password="x")
        self.assertTrue(species_admin.has_change_permission(request, self.shared))
        self.assertIn("scientific_name", species_admin.get_readonly_fields(request, self.shared))
        self.assertIn("worms_aphia_id", species_admin.get_readonly_fields(request, self.shared))
        self.assertNotIn("scientific_name", species_admin.get_readonly_fields(request, None))
        self.assertNotIn(InstitutionSpecies, admin_site._registry)
        self.assertNotIn(OrganizationSpeciesCode, admin_site._registry)


@skipUnless(connection.vendor == "postgresql", "Requires a new verified disposable PostgreSQL instance.")
class InstitutionSpeciesServiceConcurrencyTests(SpeciesReviewTransactionTestCase):
    """True concurrent service calls with PostgreSQL row and advisory locks."""

    def setUp(self):
        self.a = Organization.objects.create(name="Concurrent A")
        self.b = Organization.objects.create(name="Concurrent B")
        users = get_user_model().objects
        self.admins = {}
        for organization in (self.a, self.b):
            admin = users.create_user(username=f"isc-{organization.pk}", email=f"isc{organization.pk}@example.org")
            OrganizationMembership.objects.create(user=admin, organization=organization,
                                                  role=OrganizationMembership.Role.ADMIN)
            self.admins[organization.pk] = admin

    def race(self, operation, *args):
        barrier = Barrier(len(args))

        def worker(arg):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return operation(arg)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(args)) as pool:
            futures = [pool.submit(worker, arg) for arg in args]
            return [future.result(timeout=30) for future in futures]

    def register(self, args):
        organization, name = args
        try:
            profile, code = services.register_institution_species(
                user=self.admins[organization.pk], organization=organization, data={"scientific_name": name},
            )
            return ("created", code)
        except services.InstitutionSpeciesConflict as conflict:
            return (conflict.code, None)

    def test_equivalent_names_in_one_institution_create_one_profile(self):
        results = self.race(self.register, (self.a, "Aurelia aurita"), (self.a, "AURELIA  aurita"))
        self.assertEqual(sorted(status for status, _ in results), ["created", "institution_species_exists"])
        self.assertEqual((Species.objects.count(), InstitutionSpecies.objects.count(),
                          OrganizationSpeciesCode.objects.count()), (1, 1, 1))
        self.assertEqual(AuditLog.objects.count(), 3)

    def test_same_name_in_two_institutions_shares_one_reference(self):
        results = self.race(self.register, (self.a, "Aurelia aurita"), (self.b, "aurelia aurita"))
        self.assertEqual(results, [("created", "AAU"), ("created", "AAU")])
        self.assertEqual(Species.objects.count(), 1)
        self.assertEqual(InstitutionSpecies.objects.count(), 2)
        self.assertEqual(AuditLog.objects.filter(object_type="species").count(), 1)

    def test_species_competing_for_one_code_get_distinct_codes(self):
        results = self.race(self.register, (self.a, "Aurelia aurita"), (self.b, "Aurelia aurantia"))
        self.assertEqual(sorted(code for _, code in results), ["AAR", "AAU"])

    def test_concurrent_edits_have_one_winner(self):
        species = Species.objects.create(scientific_name="Aurelia aurita")
        profile = InstitutionSpecies.objects.create(organization=self.a, species=species, scientific_name="Aurelia aurita")

        def edit(name):
            try:
                services.update_institution_species(
                    user=self.admins[self.a.pk], organization=self.a, pk=profile.pk,
                    data={"expected_revision": 1, "scientific_name": name, "worms_aphia_id": None},
                )
                return "updated"
            except services.InstitutionSpeciesConflict as conflict:
                return conflict.code

        results = self.race(edit, "Aurelia coerulea", "Aurelia labiata")
        self.assertEqual(sorted(results), ["stale_revision", "updated"])
        profile.refresh_from_db()
        self.assertEqual(profile.revision, 2)
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_concurrent_edits_to_one_local_name_have_one_winner(self):
        first = InstitutionSpecies.objects.create(
            organization=self.a, species=Species.objects.create(scientific_name="First"), scientific_name="First",
        )
        second = InstitutionSpecies.objects.create(
            organization=self.a, species=Species.objects.create(scientific_name="Second"), scientific_name="Second",
        )

        def edit(pk):
            try:
                services.update_institution_species(
                    user=self.admins[self.a.pk], organization=self.a, pk=pk,
                    data={"expected_revision": 1, "scientific_name": "Same name", "worms_aphia_id": None},
                )
                return "updated"
            except services.InstitutionSpeciesConflict as conflict:
                return conflict.code

        self.assertEqual(sorted(self.race(edit, first.pk, second.pk)), ["institution_species_name_exists", "updated"])


@skipUnless(connection.vendor == "postgresql", "Requires a new verified disposable PostgreSQL instance.")
class LegacySharedSpeciesConcurrencyTests(SpeciesReviewTransactionTestCase):
    """Legacy shared PATCH vs a concurrent attachment by another institution.

    Each attacher is paused inside its transaction; the other side must wait
    for it instead of acting on a stale guard. Without the PATCH locks, the
    PATCH completes while the attacher is paused and renames the reference.
    """

    BLOCKED_FOR = 0.5

    def setUp(self):
        self.a = Organization.objects.create(name="Legacy race A")
        self.b = Organization.objects.create(name="Legacy race B")
        users = get_user_model().objects
        self.admins = {}
        for organization in (self.a, self.b):
            admin = users.create_user(username=f"lr-{organization.pk}", email=f"lr{organization.pk}@example.org")
            OrganizationMembership.objects.create(user=admin, organization=organization,
                                                  role=OrganizationMembership.Role.ADMIN)
            self.admins[organization.pk] = admin
        self.shared = Species.objects.create(scientific_name="Aurelia aurita")
        OrganizationSpeciesCode.objects.create(organization=self.a, species=self.shared, code="AAU")
        self.attached = Event()
        self.release = Event()
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.addCleanup(self.pool.shutdown, wait=True)
        self.addCleanup(self.release.set)

    def in_thread(self, function, *args):
        def run():
            close_old_connections()
            try:
                return function(*args)
            finally:
                close_old_connections()
        return self.pool.submit(run)

    def legacy_patch(self, payload):
        from rest_framework.test import APIRequestFactory, force_authenticate

        from .api_views import SpeciesReferenceDetailAPIView

        request = APIRequestFactory().patch(
            f"/api/taxonomy/species/{self.shared.pk}/", payload, format="json",
            HTTP_X_ORGANIZATION_ID=str(self.a.pk),
        )
        force_authenticate(request, user=self.admins[self.a.pk])
        response = SpeciesReferenceDetailAPIView.as_view()(request, pk=self.shared.pk)
        response.render()
        return response.status_code, json.loads(response.content)

    def assert_patch_waits_for_attacher_then_refuses(self, attacher):
        attacher_future = self.in_thread(attacher)
        self.assertTrue(self.attached.wait(10), "attacher did not reach its paused state")
        patch_future = self.in_thread(self.legacy_patch, {"scientific_name": "Aurelia coerulea"})
        sleep(self.BLOCKED_FOR)
        self.assertFalse(patch_future.done(), "legacy PATCH evaluated its guard before the attachment committed")
        self.release.set()
        attacher_future.result(timeout=30)
        status_code, body = patch_future.result(timeout=30)
        self.assertEqual((status_code, body["error"]), (409, "shared_species_used_by_another_institution"))
        self.shared.refresh_from_db()
        self.assertEqual(self.shared.scientific_name, "Aurelia aurita")
        self.assertFalse(AuditLog.objects.filter(organization=self.a, object_type="species").exists())

    def test_patch_waits_for_institutional_registration(self):
        original = services._audit

        def pause_after_profile(**kwargs):
            if kwargs["object_type"] == "institution_species":
                self.attached.set()
                self.release.wait(10)
            return original(**kwargs)

        def register_b():
            return services.register_institution_species(
                user=self.admins[self.b.pk], organization=self.b, data={"scientific_name": "Aurelia aurita"},
            )

        with patch.object(services, "_audit", side_effect=pause_after_profile):
            self.assert_patch_waits_for_attacher_then_refuses(register_b)
        self.assertTrue(InstitutionSpecies.objects.filter(organization=self.b, species=self.shared).exists())

    def test_patch_waits_for_gated_code_assignment(self):
        from .species_codes import allocate_species_code

        def assign_b_code():
            with transaction.atomic():
                allocate_species_code(organization=self.b, species=self.shared)
                self.attached.set()
                self.release.wait(10)

        self.assert_patch_waits_for_attacher_then_refuses(assign_b_code)

    def test_patch_waits_for_transfer_v1_species_row_lock(self):
        def import_into_b():
            # Mirrors transfer_v1._import_transfer_v1: Species row lock, then a B Strain.
            with transaction.atomic():
                Species.objects.select_for_update().get(pk=self.shared.pk)
                Strain.objects.create(species=self.shared, organization=self.b, code="AAU-JPN-1")
                self.attached.set()
                self.release.wait(10)

        self.assert_patch_waits_for_attacher_then_refuses(import_into_b)

    def test_registration_after_a_pending_rename_does_not_attach_to_the_renamed_reference(self):
        from . import api_views

        original = api_views._write_audit_log

        def pause_before_commit(request, **kwargs):
            self.attached.set()
            self.release.wait(10)
            return original(request, **kwargs)

        def register_b():
            return services.register_institution_species(
                user=self.admins[self.b.pk], organization=self.b, data={"scientific_name": "Aurelia aurita"},
            )

        with patch.object(api_views, "_write_audit_log", side_effect=pause_before_commit):
            patch_future = self.in_thread(self.legacy_patch, {"scientific_name": "Aurelia coerulea"})
            self.assertTrue(self.attached.wait(10))
            register_future = self.in_thread(register_b)
            sleep(self.BLOCKED_FOR)
            self.assertFalse(register_future.done(), "registration resolved the name during a pending rename")
            self.release.set()
            self.assertEqual(patch_future.result(timeout=30)[0], 200)
            profile, code = register_future.result(timeout=30)
        self.shared.refresh_from_db()
        self.assertEqual(self.shared.scientific_name, "Aurelia coerulea")
        # The old name is now a different, new shared reference: nothing is merged.
        self.assertNotEqual(profile.species_id, self.shared.pk)
        self.assertEqual(Species.objects.get(pk=profile.species_id).scientific_name, "Aurelia aurita")


class InstitutionSpeciesCodeReadTests(InstitutionSpeciesApiBase):
    def setUp(self):
        super().setUp()
        self.species = Species.objects.create(scientific_name="Aurelia aurita")

    def strain(self, code="AAU-FRA-1", organization=None, **kwargs):
        return Strain.objects.create(
            species=self.species, organization=organization, code=code, **kwargs,
        )

    def box(self, strain, code="AAU-FRA-1.001", organization=None, **kwargs):
        return Box.objects.create(
            organization=organization or self.a, strain=strain, global_code=code,
            box_number="001", **kwargs,
        )

    def assert_code(self, code, *, issue=None):
        before = (_snapshot(), list(InstitutionSpecies.objects.values()), list(AuditLog.objects.values()))
        response = self.get(HISTORICAL_URL, self.a, self.species.pk)
        self.assertEqual(response.status_code, 200, response.content)
        detail = response.json()
        listing = self.get(LIST_URL, self.a).json()["historical_species"]
        listed = next(item for item in listing if item["species_id"] == self.species.pk)
        for item in (detail, listed):
            self.assertEqual(item["species_code"], code)
            self.assertEqual(item.get("species_code_issue"), issue)
        self.assertEqual((_snapshot(), list(InstitutionSpecies.objects.values()), list(AuditLog.objects.values())), before)

    def test_authoritative_assignment_without_strains(self):
        OrganizationSpeciesCode.objects.create(organization=self.a, species=self.species, code="NEW")
        self.assert_code("NEW")

    def test_consistent_current_owned_strains_without_assignment(self):
        from apps.cultures.box_identity import USABLE, strain_box_identity

        strain = self.strain(organization=self.a)
        self.strain("AAU-JPN-2", organization=self.a)
        self.assertFalse(InstitutionSpecies.objects.filter(organization=self.a).exists())
        self.assertFalse(OrganizationSpeciesCode.objects.filter(organization=self.a).exists())
        self.assertEqual(strain_box_identity(strain, self.a)["status"], USABLE)
        self.assert_code("AAU")

    def test_unowned_strain_requires_local_box_association(self):
        strain = self.strain()
        self.assertEqual(self.get(HISTORICAL_URL, self.a, self.species.pk).status_code, 404)
        self.box(strain, "legacy-box")
        self.assert_code("AAU")

    def test_current_box_fallback_includes_inactive_storage(self):
        strain = self.strain("legacy")
        self.box(strain, status=Box.Status.INACTIVE)
        self.assert_code("AAU")

    def test_merged_box_local_id_does_not_conflict_with_current_box(self):
        strain = self.strain("legacy", organization=self.a)
        current = self.box(strain)
        self.box(strain, "OLD-FRA-1.002", status=Box.Status.INACTIVE, merged_into=current)
        self.assert_code("AAU")

    def test_merged_box_alone_cannot_supply_current_code(self):
        strain = self.strain("legacy", organization=self.a)
        current = self.box(strain, "unstructured-current")
        self.box(strain, "OLD-FRA-1.002", status=Box.Status.INACTIVE, merged_into=current)
        self.assert_code(None)

    def test_unowned_strain_used_only_by_merged_box_is_not_current_evidence(self):
        current_strain = self.strain("legacy", organization=self.a)
        current = self.box(current_strain)
        retired_strain = self.strain("OLD-FRA-2")
        self.box(retired_strain, "OLD-FRA-2.001", status=Box.Status.INACTIVE, merged_into=current)
        self.assert_code("AAU")

    def test_previous_local_ids_are_never_code_evidence(self):
        strain = self.strain("legacy", organization=self.a)
        box = self.box(strain, "unstructured-current")
        BoxLocalIdAlias.objects.create(box=box, code="OLD-FRA-1.001")
        self.assert_code(None)
        box.global_code = "NEW-FRA-1.001"
        box.save(update_fields=["global_code"])
        self.assert_code("NEW")

    def test_noncanonical_current_box_is_not_prefix_evidence(self):
        strain = self.strain("legacy", organization=self.a)
        self.box(strain, "AAU-FRA-1.001-previous")
        self.assert_code(None)

    def test_code_assigned_to_another_local_species_is_inconsistent(self):
        other = Species.objects.create(scientific_name="Other species")
        OrganizationSpeciesCode.objects.create(organization=self.a, species=other, code="AAU")
        self.strain(organization=self.a)
        self.assert_code(None, issue="species_code_inconsistent")

    def test_foreign_evidence_is_never_used(self):
        self.strain("legacy", organization=self.a)
        foreign = self.strain("FOR-JPN-2", organization=self.b)
        self.box(foreign, "FOR-JPN-2.001", organization=self.b)
        OrganizationSpeciesCode.objects.create(organization=self.b, species=self.species, code="FOR")
        self.assert_code(None)
        self.strain(organization=self.a)
        self.assert_code("AAU")

    def test_conflicting_current_strains_do_not_fall_back_to_box(self):
        strain = self.strain(organization=self.a)
        self.strain("NEW-JPN-2", organization=self.a)
        self.box(strain)
        self.assert_code(None, issue="species_code_inconsistent")

    def test_conflicting_current_boxes_are_not_arbitrarily_selected(self):
        strain = self.strain("legacy", organization=self.a)
        self.box(strain)
        self.box(strain, "NEW-FRA-1.002")
        self.assert_code(None, issue="species_code_inconsistent")

    def test_assignment_and_current_strain_disagreement_is_reported(self):
        OrganizationSpeciesCode.objects.create(organization=self.a, species=self.species, code="NEW")
        self.strain(organization=self.a)
        self.assert_code(None, issue="species_code_inconsistent")

    def test_current_strain_and_box_disagreement_is_reported(self):
        strain = self.strain(organization=self.a)
        self.box(strain, "NEW-FRA-1.001")
        self.assert_code(None, issue="species_code_inconsistent")

    def test_contradictory_recorded_number_does_not_become_fallback_evidence(self):
        self.strain(organization=self.a, number=0)
        self.assert_code(None, issue="species_code_inconsistent")

    def test_foreign_owned_strain_on_local_box_is_inconsistent_not_evidence(self):
        strain = self.strain(organization=self.b)
        self.box(strain)
        self.assert_code(None, issue="species_code_inconsistent")

    def test_profile_reads_use_the_same_resolver_without_creating_assignment(self):
        self.strain(organization=self.a)
        profile = InstitutionSpecies.objects.create(
            organization=self.a, species=self.species, scientific_name="Aurelia aurita",
        )
        before = _snapshot()
        detail = self.get(DETAIL_URL, self.a, profile.pk).json()
        listing = self.get(LIST_URL, self.a).json()["institution_species"][0]
        self.assertEqual(detail["species_code"], "AAU")
        self.assertEqual(listing["species_code"], "AAU")
        self.assertEqual(_snapshot(), before)
        self.assertFalse(OrganizationSpeciesCode.objects.exists())
