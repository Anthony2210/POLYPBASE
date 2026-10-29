"""AAA-BBB-X verification for automatic creation and its read-only strain options."""

import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import OrganizationMembership, UserPreference
from apps.audit.models import AuditLog
from apps.taxonomy.models import (
    BiologicalProvenance, LocalStrainIdentity, OrganizationProvenanceCode,
    OrganizationSpeciesCode, Species, Strain, StrainTranslation,
)

from .box_codes import allocate_box_codes
from .box_identity import strain_box_identity
from .models import AutomaticBoxCreationRequest, Box, BoxCodeNamespace, BoxLocation, ThermalZone
from .test_automatic_box_creation import AutomaticBoxFixture


class StrainBoxIdentityTests(AutomaticBoxFixture, TestCase):
    def identity(self, **changes):
        for field, value in changes.items():
            setattr(self.strain, field, value)
        self.strain.save()
        return strain_box_identity(Strain.objects.get(pk=self.strain.pk), self.org)

    def test_canonical_code_is_usable_and_never_rebuilt(self):
        self.assertEqual(self.identity(), {
            "status": "usable", "prefix": "AUT-LAB-1", "species_code": "AUT",
            "origin_code": "LAB", "strain_number": "1", "recorded_provenance_name": None,
        })
        self.assertEqual(self.identity(number=1, origin_code="LAB")["status"], "usable")

    def test_legacy_or_partial_codes_are_unusable(self):
        for code in ("1-LAB", "AUT-1", "AUT-LAB", "AUT-LAB-1.001", "aut-lab-1", "AUTO-LAB-1", "AUT-LAB-X", ""):
            with self.subTest(code=code):
                self.assertEqual(self.identity(code=code), {"status": "unusable", "reason": "code_format"})

    def test_recorded_components_must_not_contradict_the_code(self):
        self.assertEqual(self.identity(number=2)["reason"], "number_mismatch")
        self.assertEqual(self.identity(number=None, origin_code="ATL")["reason"], "origin_mismatch")
        self.identity(origin_code="")
        assignment = OrganizationSpeciesCode.objects.create(organization=self.org, species=self.species, code="AUR")
        self.assertEqual(self.identity()["reason"], "species_code_mismatch")
        assignment.code = "AUT"
        assignment.save()
        self.assertEqual(self.identity()["status"], "usable")

    def test_another_institution_species_code_is_not_used(self):
        OrganizationSpeciesCode.objects.create(organization=self.other_org, species=self.species, code="ZZZ")
        self.assertEqual(self.identity()["status"], "usable")

    def test_local_identity_must_match_and_names_recorded_provenance(self):
        species_code = OrganizationSpeciesCode.objects.create(organization=self.org, species=self.species, code="AUT")
        provenance = BiologicalProvenance.objects.create(name="Lagoon population")
        provenance_code = OrganizationProvenanceCode.objects.create(
            organization=self.org, biological_provenance=provenance, code="LAB",
        )
        LocalStrainIdentity.objects.create(
            strain=self.strain, species_code_assignment=species_code, provenance_code_assignment=provenance_code,
        )
        self.assertEqual(self.identity()["recorded_provenance_name"], "Lagoon population")
        provenance_code.code = "ATL"
        provenance_code.save()
        self.assertEqual(self.identity()["reason"], "local_identity_mismatch")

    def test_local_identity_without_provenance_has_no_provenance_name(self):
        species_code = OrganizationSpeciesCode.objects.create(organization=self.org, species=self.species, code="AUT")
        LocalStrainIdentity.objects.create(strain=self.strain, species_code_assignment=species_code)
        identity = self.identity()
        self.assertEqual((identity["status"], identity["recorded_provenance_name"]), ("usable", None))


class CrossInstitutionIdentityTests(AutomaticBoxFixture, TestCase):
    """Local code assignments are evidence only for the institution that owns them."""

    def setUp(self):
        super().setUp()
        self.own_species_code = OrganizationSpeciesCode.objects.create(
            organization=self.org, species=self.species, code="AUT",
        )
        # Same literal codes, owned by another institution.
        self.foreign_species_code = OrganizationSpeciesCode.objects.create(
            organization=self.other_org, species=self.species, code="AUT",
        )
        self.provenance = BiologicalProvenance.objects.create(name="Foreign lagoon")
        self.foreign_provenance_code = OrganizationProvenanceCode.objects.create(
            organization=self.other_org, biological_provenance=self.provenance, code="LAB",
        )

    def identity(self, strain=None):
        strain = Strain.objects.get(pk=(strain or self.strain).pk)
        return strain_box_identity(strain, self.org)

    def test_foreign_species_assignment_with_matching_code_is_not_corroboration(self):
        LocalStrainIdentity.objects.create(strain=self.strain, species_code_assignment=self.foreign_species_code)
        self.assertEqual(self.identity(), {"status": "unusable", "reason": "local_identity_mismatch"})

    def test_foreign_provenance_assignment_with_matching_code_is_rejected_and_not_named(self):
        LocalStrainIdentity.objects.create(
            strain=self.strain, species_code_assignment=self.own_species_code,
            provenance_code_assignment=self.foreign_provenance_code,
        )
        identity = self.identity()
        self.assertEqual(identity, {"status": "unusable", "reason": "local_identity_mismatch"})
        self.assertNotIn("Foreign lagoon", str(identity))

    def test_species_assignment_for_another_species_is_rejected(self):
        other_species = Species.objects.create(scientific_name="Other automatic species")
        # Institution code uniqueness forbids a second "AUT"; the species check is defence in depth.
        other_code = OrganizationSpeciesCode.objects.create(organization=self.org, species=other_species, code="OTH")
        LocalStrainIdentity.objects.create(strain=self.strain, species_code_assignment=other_code)
        self.assertEqual(self.identity()["reason"], "local_identity_mismatch")
        strain = Strain.objects.select_related("local_identity__species_code_assignment").get(pk=self.strain.pk)
        strain.local_identity.species_code_assignment.code = "AUT"  # unsaved: isolate the species rule
        self.assertEqual(strain_box_identity(strain, self.org)["reason"], "local_identity_mismatch")

    def test_unowned_legacy_strain_with_a_local_identity_is_rejected(self):
        legacy = Strain.objects.create(species=self.species, code="AUT-LAB-7")
        Box.objects.create(organization=self.org, strain=legacy, global_code="AUT-LAB-7.001", box_number="001")
        LocalStrainIdentity.objects.create(strain=legacy, species_code_assignment=self.own_species_code)
        self.assertEqual(self.identity(legacy)["reason"], "local_identity_mismatch")

    def test_unowned_legacy_strain_without_local_identity_remains_usable(self):
        legacy = Strain.objects.create(species=self.species, code="AUT-LAB-8")
        self.assertEqual(self.identity(legacy)["status"], "usable")

    def test_own_assignments_still_corroborate_and_are_named(self):
        own_provenance = OrganizationProvenanceCode.objects.create(
            organization=self.org, biological_provenance=BiologicalProvenance.objects.create(name="Own lagoon"), code="LAB",
        )
        LocalStrainIdentity.objects.create(
            strain=self.strain, species_code_assignment=self.own_species_code, provenance_code_assignment=own_provenance,
        )
        self.assertEqual(self.identity()["recorded_provenance_name"], "Own lagoon")

    def test_foreign_identity_blocks_creation_and_never_reaches_the_options(self):
        LocalStrainIdentity.objects.create(
            strain=self.strain, species_code_assignment=self.own_species_code,
            provenance_code_assignment=self.foreign_provenance_code,
        )
        self.client.force_login(self.user)
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.org.pk), "secure": True}
        options = self.client.get(reverse("api_box_automatic_strains"), **headers)
        self.assertEqual(options.status_code, 200)
        self.assertNotIn("Foreign lagoon", options.content.decode())
        self.assertEqual(options.json()[0]["identity"]["status"], "unusable")
        response = self.client.post(reverse("api_box_automatic"), self.payload, content_type="application/json", **headers)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Box.objects.exists())
        self.assertFalse(BoxCodeNamespace.objects.exists())


@override_settings(SECURE_SSL_REDIRECT=False)
class AutomaticBoxIdentityApiTests(AutomaticBoxFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def options(self, organization=None):
        headers = {} if organization is False else {"HTTP_X_ORGANIZATION_ID": str((organization or self.org).pk)}
        return self.client.get(reverse("api_box_automatic_strains"), **headers)

    def post(self, payload):
        return self.client.post(
            reverse("api_box_automatic"), payload, content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str(self.org.pk),
        )

    def assert_nothing_written(self):
        for model in (Box, BoxLocation, AuditLog, AutomaticBoxCreationRequest, BoxCodeNamespace):
            self.assertFalse(model.objects.exists(), model.__name__)

    def test_options_expose_verified_identity_without_next_number(self):
        StrainTranslation.objects.create(strain=self.strain, language_code="fr", name="Souche lagune")
        legacy = Strain.objects.create(organization=self.org, species=self.species, code="1-LAB", number=1, origin_code="LAB")
        Box.objects.create(organization=self.org, strain=self.strain, global_code="AUT-LAB-1.004", box_number="004")
        Box.objects.create(
            organization=self.org, strain=self.strain, global_code="AUT-LAB-1.005", box_number="005",
            status=Box.Status.INACTIVE,
        )
        namespaces = list(BoxCodeNamespace.objects.values_list("namespace", "high_water"))
        response = self.options()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("no-cache", response.headers.get("Cache-Control", ""))
        by_id = {item["id"]: item for item in response.json()}
        self.assertEqual(set(by_id), {self.strain.pk, legacy.pk})
        self.assertEqual(by_id[self.strain.pk], {
            "id": self.strain.pk,
            "species": {"id": self.species.pk, "scientific_name": "Automatic species"},
            "name": "Souche lagune",
            "code": "AUT-LAB-1",
            "active_box_count": 1,
            "identity": {
                "status": "usable", "prefix": "AUT-LAB-1", "species_code": "AUT",
                "origin_code": "LAB", "strain_number": "1", "recorded_provenance_name": None,
            },
        })
        self.assertEqual(by_id[legacy.pk]["identity"], {"status": "unusable", "reason": "code_format"})
        self.assertNotIn("006", response.content.decode())
        self.assertEqual(list(BoxCodeNamespace.objects.values_list("namespace", "high_water")), namespaces)

    def test_options_are_scoped_to_eligible_strains_of_the_active_institution(self):
        foreign = Strain.objects.create(organization=self.other_org, species=self.species, code="AUT-OTH-1")
        unused_legacy = Strain.objects.create(species=self.species, code="AUT-OLD-1")
        used_legacy = Strain.objects.create(species=self.species, code="AUT-OLD-2")
        Box.objects.create(organization=self.org, strain=used_legacy, global_code="AUT-OLD-2.001", box_number="001")
        Box.objects.create(organization=self.other_org, strain=self.strain, global_code="AUT-LAB-1.009", box_number="009")
        items = {item["id"]: item for item in self.options().json()}
        self.assertEqual(set(items), {self.strain.pk, used_legacy.pk})
        self.assertNotIn(foreign.pk, items)
        self.assertNotIn(unused_legacy.pk, items)
        self.assertEqual(items[self.strain.pk]["active_box_count"], 0)

    def test_options_require_creation_authority_and_explicit_institution(self):
        self.assertEqual(self.options(organization=False).status_code, 403)
        self.assertEqual(self.options(organization=self.other_org).status_code, 403)
        self.membership.role = "viewer"
        self.membership.save()
        self.assertEqual(self.options().status_code, 403)
        self.membership.role = "admin"
        self.membership.is_active = False
        self.membership.save()
        self.assertEqual(self.options().status_code, 403)
        self.membership.is_active = True
        self.membership.save()
        self.org.is_active = False
        self.org.save()
        self.assertEqual(self.options().status_code, 403)

    def test_unusable_identities_are_refused_without_any_write(self):
        cases = (
            {"code": "1-LAB", "number": 1, "origin_code": "LAB"},
            {"code": "AUT-LAB-1", "number": 2},
            {"code": "AUT-LAB-1", "number": None, "origin_code": "ATL"},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                Strain.objects.filter(pk=self.strain.pk).update(**changes)
                response = self.post({**self.payload, "request_id": str(uuid.uuid4())})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(list(response.json()), ["strain"])
                self.assert_nothing_written()
        Strain.objects.filter(pk=self.strain.pk).update(code="AUT-LAB-1", number=1, origin_code="LAB")
        OrganizationSpeciesCode.objects.create(organization=self.org, species=self.species, code="AUR")
        self.assertEqual(self.post(self.payload).status_code, 400)
        self.assert_nothing_written()

    def test_refusal_is_translated(self):
        Strain.objects.filter(pk=self.strain.pk).update(code="1-LAB")
        for language, expected in (
            ("fr", "Le code enregistré de cette souche ne permet pas de numéroter une boîte AAA-BBB-X."),
            ("en", "The recorded code of this strain cannot be used to number an AAA-BBB-X box."),
        ):
            with self.subTest(language=language):
                UserPreference.objects.update_or_create(user=self.user, defaults={"interface_language": language})
                self.assertTrue(self.post(self.payload).json()["strain"][0].startswith(expected))

    def test_verified_prefix_numbers_after_legacy_and_subculture_codes(self):
        Box.objects.create(organization=self.org, strain=self.strain, global_code="AUT-LAB-1.007", box_number="007")
        legacy = Box.objects.create(organization=self.org, strain=self.strain, global_code="CAN-1.001-LAB", box_number="001")
        with transaction.atomic():
            self.assertEqual(allocate_box_codes("AUT-LAB-1", 1), [("AUT-LAB-1.008", "008")])
        response = self.post(self.payload)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["global_code"], "AUT-LAB-1.009")
        legacy.refresh_from_db()
        self.assertEqual(legacy.global_code, "CAN-1.001-LAB")

    def test_committed_replay_survives_a_later_unusable_identity(self):
        first = self.post(self.payload)
        self.assertEqual(first.status_code, 201)
        Strain.objects.filter(pk=self.strain.pk).update(number=5)
        replay = self.post(self.payload)
        self.assertEqual((replay.status_code, replay.json()["id"]), (200, first.json()["id"]))
        self.assertEqual(Box.objects.count(), 1)
        self.assertEqual(BoxCodeNamespace.objects.get(namespace="AUT-LAB-1").high_water, 1)


class AutomaticBoxIdentityAuthorityTests(AutomaticBoxFixture, TestCase):
    def test_superuser_break_glass_can_list_without_membership(self):
        root = get_user_model().objects.create_superuser(username="root-identity", email="root@example.org", password="x")
        self.client.force_login(root)
        response = self.client.get(
            reverse("api_box_automatic_strains"), HTTP_X_ORGANIZATION_ID=str(self.org.pk), secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()], [self.strain.pk])
