"""Live Species actions and the legacy resolve route's read-method guard."""

import json
import uuid
from unittest.mock import patch

from django.db import connection
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.cultures.models import Box, BoxLocalIdAlias

from . import institution_species_services as services
from .models import (
    BiologicalProvenance, InstitutionSpecies, InstitutionStrainCreationRequest, OrganizationProvenanceCode,
    OrganizationSpeciesCode, Origin, Species, Strain,
)
from .test_institution_species_api import (
    DETAIL_URL, HISTORICAL_URL, LIST_URL, InstitutionSpeciesApiBase, _snapshot,
)

RESOLVE_URL = "api_admin_institution_species_resolve"


class InstitutionSpeciesActionTests(InstitutionSpeciesApiBase):
    def setUp(self):
        super().setUp()
        self.species = Species.objects.create(scientific_name="Aurelia aurita", worms_aphia_id=135306)
        self.assignment = OrganizationSpeciesCode.objects.create(
            organization=self.a, species=self.species, code="AAU",
        )
        network = patch("apps.taxonomy.worms.urlopen", side_effect=AssertionError("No network allowed"))
        network.start()
        self.addCleanup(network.stop)

    def existing_profile_id(self):
        return InstitutionSpecies.objects.create(
            organization=self.a, species=self.species,
            scientific_name=self.species.scientific_name, worms_aphia_id=self.species.worms_aphia_id,
        ).pk

    def assert_readiness(self, body, issue=None):
        self.assertEqual(body["can_create_strain"], issue is None)
        self.assertEqual(body["strain_creation_issue"], issue)

    def create_strain(self, profile_id):
        self.client.force_login(self.admin)
        return self.client.post(
            reverse("api_admin_institution_species_strains", args=[profile_id]),
            data=json.dumps({"request_id": str(uuid.uuid4()), "country": "FR",
                             "country_name": "France", "bbb_code": "FRA"}),
            content_type="application/json", **self.headers(self.a),
        )

    def test_historical_detail_is_read_only_with_and_without_a_profile(self):
        before = (_snapshot(), list(AuditLog.objects.values()))
        body = self.get(HISTORICAL_URL, self.a, self.species.pk).json()
        self.assertIsNone(body["institution_species_id"])
        self.assert_readiness(body)
        self.assertEqual((body["scientific_name"], body["worms_aphia_id"]), ("Aurelia aurita", 135306))
        self.assertFalse(InstitutionSpecies.objects.exists())
        self.assertEqual((_snapshot(), list(AuditLog.objects.values())), before)
        profile_id = self.existing_profile_id()
        self.edit(self.a, profile_id, {"expected_revision": 1, "scientific_name": "Aurelia coerulea",
                                       "worms_aphia_id": None})
        before = (_snapshot(), list(InstitutionSpecies.objects.values()), list(AuditLog.objects.values()))
        body = self.get(HISTORICAL_URL, self.a, self.species.pk).json()
        self.assertEqual((body["institution_species_id"], body["scientific_name"], body["shared_scientific_name"]),
                         (profile_id, "Aurelia coerulea", "Aurelia aurita"))
        self.assertIsNone(body["worms_aphia_id"])
        self.assertIsNone(body["worms_url"])
        self.assertFalse(body["requires_historical_adoption"])
        listed = self.get(LIST_URL, self.a).json()["institution_species"][0]
        self.assertNotIn("can_create_strain", listed)
        self.assertNotIn("strain_creation_issue", listed)
        self.assertEqual((_snapshot(), list(InstitutionSpecies.objects.values()), list(AuditLog.objects.values())), before)

    def test_existing_profile_allows_name_edit_and_strain_create(self):
        profile_id = self.existing_profile_id()
        before = _snapshot()
        response = self.edit(self.a, profile_id, {"expected_revision": 1, "scientific_name": "Aurelia coerulea",
                                                 "worms_aphia_id": None})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(_snapshot(), before)
        response = self.create_strain(profile_id)
        self.assertEqual(response.status_code, 201, response.content)
        strain = Strain.objects.get()
        self.assertEqual((strain.species_id, strain.organization_id, strain.code),
                         (self.species.pk, self.a.pk, "AAU-FRA-1"))
        self.assertEqual(Species.objects.count(), 1)
        self.assertEqual(OrganizationSpeciesCode.objects.get().pk, self.assignment.pk)

    def test_strain_create_rejects_contradictory_evidence_for_existing_profile(self):
        profile_id = self.existing_profile_id()
        Strain.objects.create(organization=self.a, species=self.species, code="XYZ-FRA-1")
        before = (_snapshot(), list(AuditLog.objects.values()))
        response = self.create_strain(profile_id)
        self.assertEqual((response.status_code, response.json()), (409, {"code": "species_code_inconsistent"}))
        self.assertEqual((_snapshot(), list(AuditLog.objects.values())), before)
        self.assertFalse(OrganizationProvenanceCode.objects.exists())
        self.assertFalse(InstitutionStrainCreationRequest.objects.exists())

    def test_actual_strain_create_rejects_missing_and_unusable_assignments(self):
        profile_id = self.existing_profile_id()
        OrganizationSpeciesCode.objects.filter(pk=self.assignment.pk).update(code="aa")
        self.assertEqual(self.create_strain(profile_id).json(), {"code": "species_code_unusable"})
        self.assignment.delete()
        self.assertEqual(self.create_strain(profile_id).json(), {"code": "species_code_missing"})
        self.assertFalse(Strain.objects.exists())
        self.assertFalse(OrganizationProvenanceCode.objects.exists())
        self.assertFalse(InstitutionStrainCreationRequest.objects.exists())

    def test_detail_readiness_is_server_computed_for_profiles_and_historical_aliases(self):
        profile_id = self.existing_profile_id()
        for code, expected in (("AAU", None), ("aa", "species_code_unusable")):
            OrganizationSpeciesCode.objects.filter(pk=self.assignment.pk).update(code=code)
            for route, pk in ((DETAIL_URL, profile_id), (HISTORICAL_URL, self.species.pk)):
                self.assert_readiness(self.get(route, self.a, pk).json(), expected)
        self.assignment.delete()
        for route, pk in ((DETAIL_URL, profile_id), (HISTORICAL_URL, self.species.pk)):
            self.assert_readiness(self.get(route, self.a, pk).json(), "species_code_missing")

    def test_get_resolve_route_is_not_a_write_or_supported_operation(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse(RESOLVE_URL), **self.headers(self.a))
        self.assertEqual(response.status_code, 405)
        self.assertFalse(InstitutionSpecies.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def _all_creation_state(self):
        return (_snapshot(), {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in (InstitutionSpecies, BiologicalProvenance, OrganizationProvenanceCode,
                          Origin, InstitutionStrainCreationRequest, AuditLog)
        })

    def _other_species_evidence(self, kind, organization):
        other = Species.objects.create(scientific_name="Cassiopea andromeda")
        if kind == "strain":
            return Strain.objects.create(organization=organization, species=other, code="AAU-FRA-1")
        if kind == "unowned_strain":
            strain = Strain.objects.create(species=other, code="AAU-FRA-1")
            return Box.objects.create(
                organization=organization, strain=strain, global_code="legacy-box", box_number="001",
            )
        strain = Strain.objects.create(species=other, code="legacy-unowned")
        return Box.objects.create(
            organization=organization, strain=strain, global_code="AAU-FRA-1.001", box_number="001",
        )

    def _assert_other_species_evidence_blocks_without_writes(self, kind):
        self._other_species_evidence(kind, self.a)
        profile_id = self.existing_profile_id()
        self.assert_readiness(self.get(DETAIL_URL, self.a, profile_id).json(), "species_code_inconsistent")
        before = self._all_creation_state()
        response = self.create_strain(profile_id)
        self.assertEqual((response.status_code, response.json()), (409, {"code": "species_code_inconsistent"}))
        self.assertEqual(self._all_creation_state(), before)
        self.assertFalse(OrganizationProvenanceCode.objects.exists())
        self.assertFalse(InstitutionStrainCreationRequest.objects.exists())

    def test_other_species_own_strain_aaa_blocks_strain_create_atomically(self):
        self._assert_other_species_evidence_blocks_without_writes("strain")

    def test_other_species_own_box_aaa_blocks_strain_create_atomically(self):
        self._assert_other_species_evidence_blocks_without_writes("box")

    def test_other_species_unowned_strain_used_by_own_box_blocks_strain_create_atomically(self):
        self._assert_other_species_evidence_blocks_without_writes("unowned_strain")

    def _assert_foreign_evidence_does_not_block(self, kind):
        self._other_species_evidence(kind, self.b)
        profile_id = self.existing_profile_id()
        self.assert_readiness(self.get(DETAIL_URL, self.a, profile_id).json())
        response = self.create_strain(profile_id)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Strain.objects.filter(organization=self.a, species=self.species).count(), 1)

    def test_other_species_foreign_owned_strain_does_not_block(self):
        self._assert_foreign_evidence_does_not_block("strain")

    def test_other_species_foreign_box_does_not_block(self):
        self._assert_foreign_evidence_does_not_block("box")

    def test_other_species_merged_box_is_not_current_evidence(self):
        box = self._other_species_evidence("box", self.a)
        target = Box.objects.create(
            organization=self.a, strain=box.strain, global_code="ZZZ-FRA-1.001", box_number="002",
        )
        Box.objects.filter(pk=box.pk).update(merged_into=target, status=Box.Status.INACTIVE, thermal_zone=None)
        profile_id = self.existing_profile_id()
        self.assert_readiness(self.get(DETAIL_URL, self.a, profile_id).json())

    def test_other_species_alias_and_previous_code_are_not_current_evidence(self):
        other = Species.objects.create(scientific_name="Cassiopea andromeda")
        strain = Strain.objects.create(species=other, code="legacy-unowned")
        box = Box.objects.create(
            organization=self.a, strain=strain, global_code="ZZZ-FRA-1.001", box_number="001",
            previous_global_code="AAU-FRA-1.001",
        )
        BoxLocalIdAlias.objects.create(box=box, code="AAU-FRA-1.002")
        profile_id = self.existing_profile_id()
        self.assert_readiness(self.get(DETAIL_URL, self.a, profile_id).json())

    def test_strain_create_locks_registry_before_allocation_gate(self):
        events = []
        registry = services.lock_local_id_registry
        gate = services.lock_species_code_allocation

        def lock_registry():
            events.append("registry")
            registry()

        def lock_gate():
            events.append("gate")
            gate()

        profile_id = self.existing_profile_id()
        with patch.object(services, "lock_local_id_registry", side_effect=lock_registry), \
                patch.object(services, "lock_species_code_allocation", side_effect=lock_gate):
            response = self.create_strain(profile_id)
            self.assertEqual(response.status_code, 201, response.content)
            self.assertEqual(events, ["registry", "gate"])

    def test_legacy_strain_patch_locks_registry_gate_then_strain_before_reassignment(self):
        from . import api_views

        other = Species.objects.create(scientific_name="Cassiopea andromeda")
        strain = Strain.objects.create(organization=self.a, species=self.species, code="AAU-FRA-1")
        species_before = list(Species.objects.order_by("pk").values())
        events = []
        registry = api_views.lock_local_id_registry
        gate = api_views.lock_species_code_allocation
        row_lock = Strain.objects.select_for_update

        def lock_registry():
            self.assertTrue(connection.in_atomic_block)
            events.append("registry")
            registry()

        def lock_gate():
            self.assertTrue(connection.in_atomic_block)
            events.append("gate")
            gate()

        def lock_strain(*args, **kwargs):
            self.assertEqual(events, ["registry", "gate"])
            events.append("strain")
            return row_lock(*args, **kwargs)

        self.client.force_login(self.admin)
        with patch.object(api_views, "lock_local_id_registry", side_effect=lock_registry), \
                patch.object(api_views, "lock_species_code_allocation", side_effect=lock_gate), \
                patch.object(Strain.objects, "select_for_update", side_effect=lock_strain):
            response = self.client.patch(
                reverse("api_taxonomy_strains_detail", args=[strain.pk]),
                data=json.dumps({"species": other.pk, "notes": "Existing update contract"}),
                content_type="application/json", **self.headers(self.a),
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(events, ["registry", "gate", "strain"])
        strain.refresh_from_db()
        self.assertEqual((strain.species_id, strain.organization_id, strain.code, strain.notes),
                         (other.pk, self.a.pk, "AAU-FRA-1", "Existing update contract"))
        self.assertEqual(list(Species.objects.order_by("pk").values()), species_before)
        self.assertEqual(AuditLog.objects.filter(organization=self.a, object_type="strain").count(), 1)

    def test_normal_registration_safety_is_unchanged(self):
        response = self.register(self.a, {"scientific_name": "Aurelia aurita"})
        self.assertEqual((response.status_code, response.json()["code"]), (409, "historical_adoption_required"))
