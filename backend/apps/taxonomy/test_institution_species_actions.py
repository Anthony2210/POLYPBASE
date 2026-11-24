"""Explicit Species actions reuse identities and never repair historical codes."""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.urls import reverse
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box, BoxLocalIdAlias, BoxTransferImport, ThermalZone
from apps.organizations.models import Organization

from . import institution_species_api as species_api
from . import institution_species_services as services

from .models import (
    BiologicalProvenance, InstitutionSpecies, InstitutionStrainCreationRequest, OrganizationProvenanceCode,
    OrganizationSpeciesCode, Origin, Species, Strain,
)
from .test_institution_species_api import (
    DETAIL_URL, HISTORICAL_URL, LIST_URL, InstitutionSpeciesApiBase, _snapshot,
)
from config.disposable_postgres import disposable_postgres_declared

from .test_species_review_regressions import SpeciesReviewTransactionTestCase

RESOLVE_URL = "api_admin_institution_species_resolve"


def _transfer_source(species):
    return {
        "format": "polypbase.box_transfer.v1", "transfer_id": "species-action-transfer-1",
        "source_organization_name": "Transfer source QA", "source_global_code": "SOURCE.001",
        "species_scientific_name": species.scientific_name,
        "strain_code": "AAU-TAI-1", "strain_origin_code": "TAI", "transferred_polyp_count": "12",
    }


class InstitutionSpeciesActionTests(InstitutionSpeciesApiBase):
    def setUp(self):
        super().setUp()
        self.species = Species.objects.create(scientific_name="Aurelia aurita", worms_aphia_id=135306)
        self.assignment = OrganizationSpeciesCode.objects.create(
            organization=self.a, species=self.species, code="AAU",
        )
        network = patch("apps.taxonomy.worms.urlopen", side_effect=AssertionError("No network allowed"))
        self.network = network.start()
        self.addCleanup(network.stop)

    def resolve(self, action="edit", *, species=None, organization=None, user=None, payload=None):
        """Call the retained service directly: its HTTP route is retired."""
        user = user or self.admin
        user.refresh_from_db()
        # Later HTTP calls in a test (edit, strain creation) expect this login.
        self.client.force_login(user)
        organization = Organization.objects.get(pk=(organization or self.a).pk)
        data = payload if payload is not None else {
            "species_id": (species or self.species).pk, "action": action,
        }

        def run():
            if not isinstance(data, dict):
                raise services.InstitutionSpeciesInvalid({"non_field_errors": ["invalid_payload"]})
            profile, created = services.resolve_institution_species(
                user=user, organization=organization, data=data,
            )
            return Response(species_api._detail_response(organization, profile), status=201 if created else 200)

        try:
            response = species_api._service_errors(run)
        except PermissionDenied:
            response = Response({"code": "forbidden"}, status=403)
        response.json = lambda: response.data
        response.content = json.dumps(response.data, default=str).encode()
        return response

    def assert_readiness(self, body, issue=None):
        self.assertEqual(body["can_create_strain"], issue is None)
        self.assertEqual(body["strain_creation_issue"], issue)

    def create_strain(self, profile_id):
        return self.client.post(
            reverse("api_admin_institution_species_strains", args=[profile_id]),
            data=json.dumps({"request_id": str(uuid.uuid4()), "country": "FR",
                             "country_name": "France", "bbb_code": "FRA"}),
            content_type="application/json", **self.headers(self.a),
        )

    def test_explicit_resolution_returns_full_detail_without_identity_writes(self):
        before = _snapshot()
        with patch.object(services, "allocate_species_code", side_effect=AssertionError("No allocation")):
            response = self.resolve("strain")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        profile = InstitutionSpecies.objects.get(organization=self.a, species=self.species)
        self.assertEqual(body, {
            "id": profile.pk, "species_id": self.species.pk, "scientific_name": "Aurelia aurita",
            "worms_aphia_id": 135306,
            "worms_url": "https://www.marinespecies.org/aphia.php?p=taxdetails&id=135306",
            "species_code": "AAU", "revision": 1, "strain_count": 0, "strains": [],
            "can_create_strain": True, "strain_creation_issue": None,
        })
        self.assertEqual(_snapshot(), before)
        self.assertEqual(self.get(DETAIL_URL, self.a, profile.pk).json(), body)
        audit = AuditLog.objects.get()
        self.assertEqual((audit.organization_id, audit.user_id, audit.object_type, audit.object_id),
                         (self.a.pk, self.admin.pk, "institution_species", str(profile.pk)))
        self.assertEqual(audit.metadata["source"], "institution_species_resolution")
        self.network.assert_not_called()

    def test_retry_and_existing_profile_preserve_local_information_and_audit(self):
        profile = InstitutionSpecies.objects.create(
            organization=self.a, species=self.species, scientific_name="Aurelia coerulea",
            worms_aphia_id=287206, revision=7,
        )
        for action in ("edit", "strain", "edit"):
            response = self.resolve(action)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual((response.json()["id"], response.json()["scientific_name"],
                              response.json()["worms_aphia_id"], response.json()["revision"]),
                             (profile.pk, "Aurelia coerulea", 287206, 7))
        self.assertFalse(AuditLog.objects.exists())
        self.assertEqual(InstitutionSpecies.objects.count(), 1)

    def test_created_retry_writes_exactly_one_association_and_audit(self):
        first = self.resolve()
        second = self.resolve("strain")
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.json(), second.json())
        self.assertEqual((InstitutionSpecies.objects.count(), AuditLog.objects.count()), (1, 1))

    def test_no_worms_is_preserved_without_fetching(self):
        Species.objects.filter(pk=self.species.pk).update(worms_aphia_id=None)
        body = self.resolve().json()
        self.assertIsNone(body["worms_aphia_id"])
        self.assertIsNone(body["worms_url"])
        self.network.assert_not_called()

    def test_stored_shared_name_is_copied_exactly_not_normalized(self):
        Species.objects.filter(pk=self.species.pk).update(scientific_name="  Aurelia  aurita ")
        self.assertEqual(self.resolve().json()["scientific_name"], "  Aurelia  aurita ")

    def test_edit_never_assigns_missing_code_but_explicit_strain_resolution_does(self):
        self.assignment.delete()
        Strain.objects.create(organization=self.a, species=self.species, code="AAU-FRA-1")
        before = _snapshot()
        response = self.resolve("edit")
        self.assertEqual(response.status_code, 201)
        # Readiness counts the proven recorded AAA, but this read registers nothing.
        self.assert_readiness(response.json())
        self.assertFalse(OrganizationSpeciesCode.objects.filter(organization=self.a).exists())
        self.assertEqual(_snapshot(), before)
        self.assertEqual(response.json()["species_code"], "AAU")
        prepared = self.resolve("strain")
        self.assertEqual(prepared.status_code, 200, prepared.content)
        self.assert_readiness(prepared.json())
        self.assertEqual(OrganizationSpeciesCode.objects.get(organization=self.a).code, "AAU")

    def _assert_missing_afl_assignment_is_preserved(self, *, owned, canonical_strain=True):
        self.assignment.delete()
        strain = Strain.objects.create(
            organization=self.a if owned else None, species=self.species,
            code="AFL-TAI-1" if canonical_strain else "legacy-unowned",
        )
        Box.objects.create(organization=self.a, strain=strain, global_code="AFL-TAI-1.001", box_number="001")
        before = _snapshot()
        before.pop("codes")
        read_state = self._all_creation_state()
        detail = self.get(HISTORICAL_URL, self.a, self.species.pk).json()
        self.assertEqual(detail["species_code"], "AFL")
        # Ready from proven evidence; the read itself registers nothing.
        self.assert_readiness(detail)
        self.assertEqual(self._all_creation_state(), read_state)
        with patch("apps.taxonomy.species_codes.candidate_codes", side_effect=AssertionError("Keep recorded AAA")):
            response = self.resolve("strain")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["species_code"], "AFL")
        self.assert_readiness(response.json())
        assignment = OrganizationSpeciesCode.objects.get(organization=self.a, species=self.species)
        self.assertEqual(assignment.code, "AFL")
        after = _snapshot()
        after.pop("codes")
        self.assertEqual(after, before)
        audit = AuditLog.objects.get(object_type="organization_species_code")
        self.assertEqual((audit.organization_id, audit.user_id, audit.object_id),
                         (self.a.pk, self.admin.pk, str(assignment.pk)))
        self.assertTrue(audit.metadata["recorded_code_preserved"])
        retry = self.resolve("strain")
        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertEqual(retry.json(), response.json())
        self.assertEqual(OrganizationSpeciesCode.objects.get(organization=self.a, species=self.species).pk, assignment.pk)
        self.assertEqual(AuditLog.objects.count(), 2)
        strain.refresh_from_db()
        self.assertEqual(strain.organization_id, self.a.pk if owned else None)
        self.network.assert_not_called()

    def test_missing_afl_assignment_preserves_owned_strain_and_boxes_exactly(self):
        self._assert_missing_afl_assignment_is_preserved(owned=True)

    def test_missing_afl_assignment_preserves_unowned_strain_used_by_own_box(self):
        self._assert_missing_afl_assignment_is_preserved(owned=False)

    def test_missing_afl_assignment_uses_own_box_code_without_canonical_strain(self):
        self._assert_missing_afl_assignment_is_preserved(owned=False, canonical_strain=False)

    def test_no_current_aaa_allocates_with_existing_algorithm_without_worms(self):
        self.assignment.delete()
        Species.objects.filter(pk=self.species.pk).update(worms_aphia_id=None)
        strain = Strain.objects.create(organization=self.a, species=self.species, code="legacy")
        response = self.resolve("strain")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["species_code"], "AAU")
        self.assertIsNone(response.json()["worms_aphia_id"])
        self.assert_readiness(response.json())
        audit = AuditLog.objects.get(object_type="organization_species_code")
        self.assertFalse(audit.metadata["recorded_code_preserved"])
        strain.refresh_from_db()
        self.assertEqual(strain.code, "legacy")
        self.assertEqual(Species.objects.count(), 1)
        self.assertEqual(Strain.objects.count(), 1)
        self.assertFalse(OrganizationProvenanceCode.objects.exists())
        self.network.assert_not_called()

    def test_existing_profile_receives_missing_assignment_without_revision_or_name_change(self):
        self.assignment.delete()
        profile = InstitutionSpecies.objects.create(
            organization=self.a, species=self.species, scientific_name="Aurelia coerulea",
            worms_aphia_id=287206, revision=7,
        )
        Strain.objects.create(organization=self.a, species=self.species, code="AFL-TAI-1")
        response = self.resolve("strain")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.json()["id"], response.json()["scientific_name"],
                          response.json()["worms_aphia_id"], response.json()["revision"],
                          response.json()["species_code"]),
                         (profile.pk, "Aurelia coerulea", 287206, 7, "AFL"))
        self.assertEqual(AuditLog.objects.count(), 1)
        self.assertEqual(AuditLog.objects.get().object_type, "organization_species_code")
        self.assertEqual(self.resolve("strain").status_code, 200)
        self.assertEqual(AuditLog.objects.count(), 1)
        self.network.assert_not_called()

    def test_missing_assignment_ambiguity_rolls_back_without_allocation(self):
        self.assignment.delete()
        Strain.objects.create(organization=self.a, species=self.species, code="AFL-TAI-1")
        Strain.objects.create(organization=self.a, species=self.species, code="XYZ-FRA-2")
        before = self._all_creation_state()
        with patch.object(services, "allocate_species_code", side_effect=AssertionError("Ambiguous evidence")):
            response = self.resolve("strain")
        self.assertEqual((response.status_code, response.json()), (409, {"code": "species_code_inconsistent"}))
        self.assertEqual(self._all_creation_state(), before)

    def test_missing_assignment_with_contradictory_strain_metadata_is_not_repaired(self):
        self.assignment.delete()
        Strain.objects.create(
            organization=self.a, species=self.species, code="AFL-TAI-1", origin_code="FRA",
        )
        before = self._all_creation_state()
        with patch.object(services, "allocate_species_code", side_effect=AssertionError("Invalid current evidence")):
            response = self.resolve("strain")
        self.assertEqual((response.status_code, response.json()), (409, {"code": "species_code_inconsistent"}))
        self.assertEqual(self._all_creation_state(), before)

    def test_recorded_aaa_collision_never_generates_alternative(self):
        self.assignment.delete()
        Strain.objects.create(organization=self.a, species=self.species, code="AFL-TAI-1")
        other = Species.objects.create(scientific_name="Cassiopea andromeda")
        OrganizationSpeciesCode.objects.create(organization=self.b, species=other, code="AFL")
        before = self._all_creation_state()
        response = self.resolve("strain")
        self.assertEqual((response.status_code, response.json()), (409, {"code": "historical_code_conflict"}))
        self.assertEqual(self._all_creation_state(), before)
        self.assertFalse(InstitutionSpecies.objects.exists())
        self.assertFalse(OrganizationSpeciesCode.objects.filter(organization=self.a).exists())

    def test_unowned_box_recorded_aaa_collision_never_generates_alternative(self):
        self.assignment.delete()
        legacy = Strain.objects.create(species=self.species, code="legacy-unowned")
        Box.objects.create(organization=self.a, strain=legacy, global_code="AFL-TAI-1.001", box_number="001")
        other = Species.objects.create(scientific_name="Cassiopea andromeda")
        Strain.objects.create(organization=self.b, species=other, code="AFL-FRA-1")
        before = self._all_creation_state()
        response = self.resolve("strain")
        self.assertEqual((response.status_code, response.json()), (409, {"code": "historical_code_conflict"}))
        self.assertEqual(self._all_creation_state(), before)

    def test_recorded_aaa_used_by_another_local_species_without_assignment_is_blocked(self):
        self.assignment.delete()
        Strain.objects.create(organization=self.a, species=self.species, code="AFL-TAI-1")
        other = Species.objects.create(scientific_name="Cassiopea andromeda")
        Strain.objects.create(organization=self.a, species=other, code="AFL-FRA-1")
        before = self._all_creation_state()
        response = self.resolve("strain")
        self.assertEqual((response.status_code, response.json()), (409, {"code": "historical_code_conflict"}))
        self.assertEqual(self._all_creation_state(), before)

    def test_recorded_aaa_cannot_take_another_local_species_assignment(self):
        self.assignment.delete()
        Strain.objects.create(organization=self.a, species=self.species, code="AFL-TAI-1")
        other = Species.objects.create(scientific_name="Cassiopea andromeda")
        OrganizationSpeciesCode.objects.create(organization=self.a, species=other, code="AFL")
        before = self._all_creation_state()
        response = self.resolve("strain")
        self.assertEqual((response.status_code, response.json()), (409, {"code": "species_code_inconsistent"}))
        self.assertEqual(self._all_creation_state(), before)

    def test_profile_without_current_evidence_can_receive_generated_code(self):
        self.assignment.delete()
        profile = InstitutionSpecies.objects.create(
            organization=self.a, species=self.species, scientific_name="Aurelia aurita",
        )
        response = self.resolve("strain")
        self.assertEqual((response.status_code, response.json()["id"], response.json()["species_code"]),
                         (200, profile.pk, "AAU"))
        self.assertFalse(Strain.objects.exists())
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_no_available_generated_code_rolls_back_without_partial_profile_or_audit(self):
        self.assignment.delete()
        Strain.objects.create(organization=self.a, species=self.species, code="legacy")
        before = self._all_creation_state()
        with patch("apps.taxonomy.species_codes._is_safe", return_value=False):
            response = self.resolve("strain")
        self.assertEqual((response.status_code, response.json()), (409, {"code": "code_exhausted"}))
        self.assertEqual(self._all_creation_state(), before)

    def test_assignment_audit_failure_rolls_back_for_existing_profile(self):
        self.assignment.delete()
        InstitutionSpecies.objects.create(
            organization=self.a, species=self.species, scientific_name="Aurelia aurita",
        )
        before = self._all_creation_state()
        with patch.object(services, "_audit", side_effect=RuntimeError("assignment audit failed")):
            with self.assertRaisesMessage(RuntimeError, "assignment audit failed"):
                self.resolve("strain")
        self.assertEqual(self._all_creation_state(), before)

    def test_required_assignment_and_profile_audits_share_rollback(self):
        self.assignment.delete()
        Strain.objects.create(organization=self.a, species=self.species, code="AFL-TAI-1")
        before = self._all_creation_state()
        original = services._audit

        def fail_profile(**kwargs):
            if kwargs["object_type"] == "institution_species":
                raise RuntimeError("profile audit failed")
            return original(**kwargs)

        with patch.object(services, "_audit", side_effect=fail_profile):
            with self.assertRaisesMessage(RuntimeError, "profile audit failed"):
                self.resolve("strain")
        self.assertEqual(self._all_creation_state(), before)
        self.assertEqual(self.resolve("strain").status_code, 201)
        self.assertEqual(AuditLog.objects.count(), 2)

    def test_missing_code_assignment_rolls_back_when_profile_name_conflicts(self):
        self.assignment.delete()
        Strain.objects.create(organization=self.a, species=self.species, code="AFL-TAI-1")
        other = Species.objects.create(scientific_name="Cassiopea andromeda")
        InstitutionSpecies.objects.create(organization=self.a, species=other, scientific_name="Aurelia aurita")
        before = self._all_creation_state()
        response = self.resolve("strain")
        self.assertEqual((response.status_code, response.json()["code"]), (409, "institution_species_name_exists"))
        self.assertEqual(self._all_creation_state(), before)

    def test_unusable_assignment_blocks_strain_but_allows_edit(self):
        OrganizationSpeciesCode.objects.filter(pk=self.assignment.pk).update(code="aa")
        self.assertEqual(self.resolve("strain").json(), {"code": "species_code_unusable"})
        self.assertFalse(InstitutionSpecies.objects.exists())
        self.assert_readiness(self.resolve().json(), "species_code_unusable")

    def test_contradiction_blocks_strain_without_partial_mutation_but_not_edit(self):
        Strain.objects.create(organization=self.a, species=self.species, code="XYZ-FRA-1")
        before = _snapshot()
        response = self.resolve("strain")
        self.assertEqual((response.status_code, response.json()), (409, {"code": "species_code_inconsistent"}))
        self.assertFalse(InstitutionSpecies.objects.exists())
        self.assertFalse(AuditLog.objects.exists())
        response = self.resolve()
        self.assertEqual(response.status_code, 201)
        self.assert_readiness(response.json(), "species_code_inconsistent")
        self.assertEqual(_snapshot(), before)

    def test_own_box_evidence_allows_edit_without_claiming_unowned_strain(self):
        self.assignment.delete()
        strain = Strain.objects.create(species=self.species, code="legacy")
        Box.objects.create(organization=self.a, strain=strain, global_code="legacy-box", box_number="001")
        before = _snapshot()
        self.assertEqual(self.resolve().status_code, 201)
        self.assertEqual(_snapshot(), before)
        strain.refresh_from_db()
        self.assertIsNone(strain.organization_id)

    def test_profile_alone_is_established_use_and_does_not_require_code_for_edit(self):
        self.assignment.delete()
        profile = InstitutionSpecies.objects.create(
            organization=self.a, species=self.species, scientific_name="Aurelia aurita",
        )
        response = self.resolve()
        self.assertEqual((response.status_code, response.json()["id"]), (200, profile.pk))
        self.assert_readiness(response.json(), "species_code_missing")
        self.assertEqual(self.get(HISTORICAL_URL, self.a, self.species.pk).status_code, 200)

    def test_no_label_or_foreign_evidence_can_authorize_resolution(self):
        self.assignment.delete()
        OrganizationSpeciesCode.objects.create(organization=self.b, species=self.species, code="AAU")
        Strain.objects.create(organization=self.b, species=self.species, code="AAU-FRA-1")
        for action in ("edit", "strain"):
            self.assertEqual(self.resolve(action).status_code, 404)
        self.assertFalse(InstitutionSpecies.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_invalid_payloads_are_strict_without_writes(self):
        payloads = [[], {}, {"species_id": self.species.pk},
                    {"species_id": self.species.pk, "action": "delete"},
                    {"species_id": self.species.pk, "action": []},
                    {"species_id": self.species.pk, "action": "edit", "code": "XYZ"}]
        payloads += [{"species_id": value, "action": "edit"} for value in (True, 0, -1, "1", 1.2, None)]
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertEqual(self.resolve(payload=payload).status_code, 400)
        self.assertFalse(InstitutionSpecies.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_unknown_species_is_404(self):
        self.assertEqual(self.resolve(payload={"species_id": 999999, "action": "edit"}).status_code, 404)

    def test_requires_active_admin(self):
        for user in (self.tech, self.viewer, self.b_admin):
            self.assertEqual(self.resolve(user=user).status_code, 403)
        OrganizationMembership.objects.filter(user=self.admin).update(is_active=False)
        self.assertEqual(self.resolve().status_code, 403)
        self.assertFalse(InstitutionSpecies.objects.exists())

    def test_inactive_user_and_organization_are_denied(self):
        self.admin.is_active = False
        self.admin.save(update_fields=["is_active"])
        self.assertEqual(self.resolve().status_code, 403)
        self.admin.is_active = True
        self.admin.save(update_fields=["is_active"])
        self.a.is_active = False
        self.a.save(update_fields=["is_active"])
        self.assertEqual(self.resolve().status_code, 403)
        self.assertFalse(InstitutionSpecies.objects.exists())

    def test_authority_revalidated_after_locks(self):
        original = services.lock_species_code_allocation

        def revoke():
            original()
            OrganizationMembership.objects.filter(user=self.admin, organization=self.a).update(is_active=False)

        with patch.object(services, "lock_species_code_allocation", side_effect=revoke):
            self.assertEqual(self.resolve().status_code, 403)
        self.assertFalse(InstitutionSpecies.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_multi_membership_does_not_borrow_other_organization_evidence(self):
        OrganizationMembership.objects.create(user=self.admin, organization=self.b, role="admin")
        self.assertEqual(self.resolve(organization=self.b).status_code, 404)
        OrganizationSpeciesCode.objects.create(organization=self.b, species=self.species, code="BBU")
        first = self.resolve()
        second = self.resolve(organization=self.b)
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertNotEqual(first.json()["id"], second.json()["id"])
        self.assertEqual((first.json()["species_code"], second.json()["species_code"]), ("AAU", "BBU"))
        self.assertEqual(Species.objects.count(), 1)

    def test_own_normalized_name_and_reference_conflicts_are_guarded(self):
        other = Species.objects.create(scientific_name="Cassiopea andromeda")
        profile = InstitutionSpecies.objects.create(
            organization=self.a, species=other, scientific_name=" AURELIA  aurita ",
        )
        self.assertEqual(self.resolve().json()["code"], "institution_species_name_exists")
        InstitutionSpecies.objects.filter(pk=profile.pk).update(
            scientific_name="Cassiopea andromeda", worms_aphia_id=135306,
        )
        self.assertEqual(self.resolve().json()["code"], "institution_aphia_id_exists")
        self.assertFalse(InstitutionSpecies.objects.filter(species=self.species).exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_required_audit_failure_rolls_back_association(self):
        before = _snapshot()
        with patch.object(services, "_audit", side_effect=RuntimeError("audit failed")):
            with self.assertRaisesMessage(RuntimeError, "audit failed"):
                self.resolve()
        self.assertFalse(InstitutionSpecies.objects.exists())
        self.assertEqual(_snapshot(), before)
        self.assertEqual(self.resolve().status_code, 201)
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_get_is_read_only_before_and_after_resolution_and_lists_unchanged(self):
        before = (_snapshot(), list(AuditLog.objects.values()))
        body = self.get(HISTORICAL_URL, self.a, self.species.pk).json()
        self.assertIsNone(body["institution_species_id"])
        self.assert_readiness(body)
        self.assertEqual((body["scientific_name"], body["worms_aphia_id"]), ("Aurelia aurita", 135306))
        self.assertFalse(InstitutionSpecies.objects.exists())
        self.assertEqual((_snapshot(), list(AuditLog.objects.values())), before)
        profile_id = self.resolve().json()["id"]
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

    def test_resolve_enables_actual_name_edit_and_strain_create(self):
        profile_id = self.resolve("strain").json()["id"]
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

    def test_actual_strain_create_rechecks_contradiction_after_resolution(self):
        profile_id = self.resolve("strain").json()["id"]
        Strain.objects.create(organization=self.a, species=self.species, code="XYZ-FRA-1")
        before = (_snapshot(), list(AuditLog.objects.values()))
        response = self.create_strain(profile_id)
        self.assertEqual((response.status_code, response.json()), (409, {"code": "species_code_inconsistent"}))
        self.assertEqual((_snapshot(), list(AuditLog.objects.values())), before)
        self.assertFalse(OrganizationProvenanceCode.objects.exists())
        self.assertFalse(InstitutionStrainCreationRequest.objects.exists())

    def test_actual_strain_create_rejects_missing_and_unusable_assignments(self):
        profile_id = self.resolve().json()["id"]
        OrganizationSpeciesCode.objects.filter(pk=self.assignment.pk).update(code="aa")
        self.assertEqual(self.create_strain(profile_id).json(), {"code": "species_code_unusable"})
        self.assignment.delete()
        self.assertEqual(self.create_strain(profile_id).json(), {"code": "species_code_missing"})
        self.assertFalse(Strain.objects.exists())
        self.assertFalse(OrganizationProvenanceCode.objects.exists())
        self.assertFalse(InstitutionStrainCreationRequest.objects.exists())

    def test_detail_readiness_is_server_computed_for_profiles_and_historical_aliases(self):
        profile_id = self.resolve().json()["id"]
        for code, expected in (("AAU", None), ("aa", "species_code_unusable")):
            OrganizationSpeciesCode.objects.filter(pk=self.assignment.pk).update(code=code)
            for route, pk in ((DETAIL_URL, profile_id), (HISTORICAL_URL, self.species.pk)):
                self.assert_readiness(self.get(route, self.a, pk).json(), expected)
        self.assignment.delete()
        for route, pk in ((DETAIL_URL, profile_id), (HISTORICAL_URL, self.species.pk)):
            self.assert_readiness(self.get(route, self.a, pk).json(), "species_code_missing")

    def test_own_box_code_contradiction_is_not_repaired(self):
        strain = Strain.objects.create(species=self.species, code="legacy")
        Box.objects.create(organization=self.a, strain=strain, global_code="XYZ-FRA-1.001", box_number="001")
        before = _snapshot()
        self.assertEqual(self.resolve("strain").json(), {"code": "species_code_inconsistent"})
        self.assertFalse(InstitutionSpecies.objects.exists())
        self.assertEqual(_snapshot(), before)

    def test_foreign_local_name_and_reference_do_not_block_own_association(self):
        other = Species.objects.create(scientific_name="Cassiopea andromeda")
        InstitutionSpecies.objects.create(
            organization=self.b, species=other, scientific_name="Aurelia aurita", worms_aphia_id=135306,
        )
        self.assertEqual(self.resolve().status_code, 201)
        self.assertEqual(InstitutionSpecies.objects.filter(organization=self.a).count(), 1)

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
        before = self._all_creation_state()
        response = self.resolve("strain")
        self.assertEqual((response.status_code, response.json()), (409, {"code": "species_code_inconsistent"}))
        self.assertEqual(self._all_creation_state(), before)
        self.assertFalse(InstitutionSpecies.objects.exists())
        # Editing remains allowed, but actual creation must independently refuse.
        profile_id = self.resolve("edit").json()["id"]
        self.assert_readiness(self.get(DETAIL_URL, self.a, profile_id).json(), "species_code_inconsistent")
        before = self._all_creation_state()
        response = self.create_strain(profile_id)
        self.assertEqual((response.status_code, response.json()), (409, {"code": "species_code_inconsistent"}))
        self.assertEqual(self._all_creation_state(), before)
        self.assertFalse(OrganizationProvenanceCode.objects.exists())
        self.assertFalse(InstitutionStrainCreationRequest.objects.exists())

    def test_other_species_own_strain_aaa_blocks_resolve_and_actual_create_atomically(self):
        self._assert_other_species_evidence_blocks_without_writes("strain")

    def test_other_species_own_box_aaa_blocks_resolve_and_actual_create_atomically(self):
        self._assert_other_species_evidence_blocks_without_writes("box")

    def test_other_species_unowned_strain_used_by_own_box_blocks_both_actions(self):
        self._assert_other_species_evidence_blocks_without_writes("unowned_strain")

    def _assert_foreign_evidence_does_not_block(self, kind):
        self._other_species_evidence(kind, self.b)
        response = self.resolve("strain")
        self.assertEqual(response.status_code, 201, response.content)
        self.assert_readiness(response.json())
        response = self.create_strain(response.json()["id"])
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
        response = self.resolve("strain")
        self.assertEqual(response.status_code, 201, response.content)
        self.assert_readiness(response.json())

    def test_other_species_alias_and_previous_code_are_not_current_evidence(self):
        other = Species.objects.create(scientific_name="Cassiopea andromeda")
        strain = Strain.objects.create(species=other, code="legacy-unowned")
        box = Box.objects.create(
            organization=self.a, strain=strain, global_code="ZZZ-FRA-1.001", box_number="001",
            previous_global_code="AAU-FRA-1.001",
        )
        BoxLocalIdAlias.objects.create(box=box, code="AAU-FRA-1.002")
        response = self.resolve("strain")
        self.assertEqual(response.status_code, 201, response.content)
        self.assert_readiness(response.json())


    def test_identity_coordination_uses_registry_before_allocation_gate(self):
        events = []
        registry = services.lock_local_id_registry
        gate = services.lock_species_code_allocation

        def lock_registry():
            events.append("registry")
            registry()

        def lock_gate():
            events.append("gate")
            gate()

        with patch.object(services, "lock_local_id_registry", side_effect=lock_registry), \
                patch.object(services, "lock_species_code_allocation", side_effect=lock_gate):
            response = self.resolve("strain")
            self.assertEqual(response.status_code, 201, response.content)
            self.assertEqual(events, ["registry", "gate"])
            events.clear()
            response = self.create_strain(response.json()["id"])
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

    def test_transfer_import_locks_registry_before_import_and_species_work(self):
        from apps.cultures import transfer_v1

        zone = ThermalZone.objects.create(organization=self.a, name="Transfer destination")
        events = []
        registry = transfer_v1.lock_local_id_registry
        importer = transfer_v1._import_transfer_v1
        species_lookup = Species.objects.get_or_create
        species_lock = Species.objects.select_for_update

        def lock_registry():
            self.assertTrue(connection.in_atomic_block)
            events.append("registry")
            registry()

        def run_import(**kwargs):
            self.assertEqual(events, ["registry"])
            events.append("import")
            return importer(**kwargs)

        def lookup_species(*args, **kwargs):
            self.assertEqual(events, ["registry", "import"])
            events.append("species_lookup")
            return species_lookup(*args, **kwargs)

        def lock_species(*args, **kwargs):
            self.assertEqual(events, ["registry", "import", "species_lookup"])
            events.append("species_lock")
            return species_lock(*args, **kwargs)

        self.client.force_login(self.admin)
        with patch.object(transfer_v1, "lock_local_id_registry", side_effect=lock_registry), \
                patch.object(transfer_v1, "_import_transfer_v1", side_effect=run_import), \
                patch.object(Species.objects, "get_or_create", side_effect=lookup_species), \
                patch.object(Species.objects, "select_for_update", side_effect=lock_species):
            response = self.client.post(
                reverse("api_box_transfer_import"),
                data=json.dumps({"source_data": _transfer_source(self.species),
                                 "organization": self.a.pk, "thermal_zone": zone.pk}),
                content_type="application/json", **self.headers(self.a),
            )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(events, ["registry", "import", "species_lookup", "species_lock"])
        box = Box.objects.get(pk=response.json()["id"])
        self.assertEqual((box.organization_id, box.strain.species_id, box.global_code),
                         (self.a.pk, self.species.pk, "AAU-TAI-1.001"))
        self.assertEqual(BoxTransferImport.objects.count(), 1)
        self.assertEqual(Species.objects.count(), 1)

    def test_normal_registration_safety_is_unchanged(self):
        response = self.register(self.a, {"scientific_name": "Aurelia aurita"})
        self.assertEqual((response.status_code, response.json()["code"]), (409, "historical_adoption_required"))
        self.assertFalse(InstitutionSpecies.objects.exists())


@skipUnless(
    disposable_postgres_declared(),
    "Requires explicitly verified disposable PostgreSQL; SQLite cannot prove row locks.",
)
class InstitutionSpeciesActionConcurrencyTests(SpeciesReviewTransactionTestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Resolution concurrency")
        self.user = get_user_model().objects.create_user(
            username="resolution_admin", email="resolution-admin@example.org",
        )
        OrganizationMembership.objects.create(user=self.user, organization=self.organization, role="admin")
        self.species = Species.objects.create(scientific_name="Aurelia aurita")
        OrganizationSpeciesCode.objects.create(organization=self.organization, species=self.species, code="AAU")

    def _concurrent_resolve(self, actions):
        barrier = Barrier(2)

        def resolve(action):
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=self.user.pk)
                organization = Organization.objects.get(pk=self.organization.pk)
                barrier.wait(timeout=10)
                profile, created = services.resolve_institution_species(
                    user=user, organization=organization,
                    data={"species_id": self.species.pk, "action": action},
                )
                return profile.pk, created
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(resolve, action) for action in actions]
            return [future.result(timeout=20) for future in futures]

    def test_missing_recorded_assignment_concurrent_retries_create_one_code_and_profile(self):
        OrganizationSpeciesCode.objects.filter(organization=self.organization, species=self.species).delete()
        Strain.objects.create(organization=self.organization, species=self.species, code="AFL-TAI-1")
        results = self._concurrent_resolve(("strain", "strain"))
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sorted(created for _, created in results), [False, True])
        self.assertEqual(InstitutionSpecies.objects.count(), 1)
        self.assertEqual(OrganizationSpeciesCode.objects.get(organization=self.organization).code, "AFL")
        self.assertEqual(AuditLog.objects.filter(object_type="organization_species_code").count(), 1)
        self.assertEqual(AuditLog.objects.filter(object_type="institution_species").count(), 1)

    def test_concurrent_retries_create_one_association_and_required_audit(self):
        results = self._concurrent_resolve(("strain", "strain"))
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sorted(created for _, created in results), [False, True])
        self.assertEqual(InstitutionSpecies.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(object_type="institution_species").count(), 1)


    def test_resolution_waits_for_authorized_manual_code_assignment(self):
        from .api_views import SpeciesCodeListCreateAPIView

        OrganizationSpeciesCode.objects.filter(organization=self.organization, species=self.species).delete()
        ready, release, checking = Event(), Event(), Event()
        gate = services.lock_species_code_allocation

        def assign():
            close_old_connections()
            try:
                with transaction.atomic():
                    request = APIRequestFactory().post(
                        reverse("api_taxonomy_species_codes"), {"species": self.species.pk, "code": "AAU"},
                        format="json", HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
                    )
                    force_authenticate(request, user=self.user)
                    response = SpeciesCodeListCreateAPIView.as_view()(request)
                    if response.status_code != 201:
                        raise AssertionError(response.data)
                    ready.set()
                    if not release.wait(timeout=15):
                        raise RuntimeError("Assignment was not released")
            finally:
                close_old_connections()

        def checked_gate():
            checking.set()
            gate()

        def resolve():
            close_old_connections()
            try:
                profile, created = services.resolve_institution_species(
                    user=self.user, organization=self.organization,
                    data={"species_id": self.species.pk, "action": "strain"},
                )
                return profile.pk, created
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(assign)
            try:
                self.assertTrue(ready.wait(timeout=10))
                with patch.object(services, "lock_species_code_allocation", side_effect=checked_gate):
                    reader = pool.submit(resolve)
                    self.assertTrue(checking.wait(timeout=10))
                    self.assertFalse(reader.done())
                    release.set()
                    writer.result(timeout=20)
                    profile_pk, created = reader.result(timeout=20)
            finally:
                release.set()
                writer.result(timeout=20)
        self.assertTrue(created)
        self.assertEqual(InstitutionSpecies.objects.get(pk=profile_pk).species_id, self.species.pk)
        self.assertEqual(OrganizationSpeciesCode.objects.get(organization=self.organization).code, "AAU")
        self.assertEqual(AuditLog.objects.filter(object_type="institution_species").count(), 1)

    def test_resolution_waits_for_legacy_strain_reassignment_and_rechecks_evidence(self):
        from .api_views import StrainReferenceDetailAPIView

        other = Species.objects.create(scientific_name="Cassiopea andromeda")
        strain = Strain.objects.create(
            organization=self.organization, species=self.species, code="AAU-FRA-1",
        )
        ready, release, checking = Event(), Event(), Event()
        registry = services.lock_local_id_registry

        def reassign():
            close_old_connections()
            try:
                with transaction.atomic():
                    request = APIRequestFactory().patch(
                        reverse("api_taxonomy_strains_detail", args=[strain.pk]),
                        {"species": other.pk}, format="json",
                        HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
                    )
                    force_authenticate(request, user=self.user)
                    response = StrainReferenceDetailAPIView.as_view()(request, pk=strain.pk)
                    if response.status_code != 200:
                        raise AssertionError(response.data)
                    ready.set()
                    if not release.wait(timeout=15):
                        raise RuntimeError("Reassignment was not released")
            finally:
                close_old_connections()

        def checked_registry():
            checking.set()
            registry()

        def resolve():
            close_old_connections()
            try:
                try:
                    services.resolve_institution_species(
                        user=self.user, organization=self.organization,
                        data={"species_id": self.species.pk, "action": "strain"},
                    )
                except services.InstitutionSpeciesConflict as error:
                    return error.code
                raise AssertionError("Conflicting AAA evidence was accepted")
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(reassign)
            try:
                self.assertTrue(ready.wait(timeout=10))
                with patch.object(services, "lock_local_id_registry", side_effect=checked_registry):
                    reader = pool.submit(resolve)
                    self.assertTrue(checking.wait(timeout=10))
                    self.assertFalse(reader.done())
                    release.set()
                    writer.result(timeout=20)
                    self.assertEqual(reader.result(timeout=20), "species_code_inconsistent")
            finally:
                release.set()
                writer.result(timeout=20)
        strain.refresh_from_db()
        self.assertEqual((strain.species_id, strain.code, strain.organization_id),
                         (other.pk, "AAU-FRA-1", self.organization.pk))
        self.assertFalse(InstitutionSpecies.objects.exists())
        self.assertFalse(InstitutionStrainCreationRequest.objects.exists())
        self.assertFalse(OrganizationProvenanceCode.objects.exists())
        self.assertFalse(AuditLog.objects.filter(object_type="institution_species").exists())
        self.assertEqual(AuditLog.objects.filter(object_type="strain").count(), 1)

    def _assert_transfer_waits_before_species_work(self, *, actual_create):
        from apps.cultures import transfer_v1
        from apps.cultures.api_views import BoxTransferImportAPIView
        from .institution_strains import create_institution_strain

        zone = ThermalZone.objects.create(organization=self.organization, name="Transfer destination")
        profile = None
        if actual_create:
            profile, _ = services.resolve_institution_species(
                user=self.user, organization=self.organization,
                data={"species_id": self.species.pk, "action": "edit"},
            )
        ready, release, importing = Event(), Event(), Event()
        gate = services.lock_species_code_allocation
        registry = transfer_v1.lock_local_id_registry

        def held_gate():
            gate()
            ready.set()
            if not release.wait(timeout=15):
                raise RuntimeError("Species action was not released")

        def import_registry():
            importing.set()
            registry()

        def action():
            close_old_connections()
            try:
                if actual_create:
                    strain, created = create_institution_strain(
                        user=self.user, organization=self.organization, profile_pk=profile.pk,
                        data={"request_id": str(uuid.uuid4()), "country": "FR",
                              "country_name": "France", "bbb_code": "FRA"},
                    )
                    return strain.pk, created
                resolved, created = services.resolve_institution_species(
                    user=self.user, organization=self.organization,
                    data={"species_id": self.species.pk, "action": "strain"},
                )
                return resolved.pk, created
            finally:
                close_old_connections()

        def import_transfer():
            close_old_connections()
            try:
                request = APIRequestFactory().post(
                    reverse("api_box_transfer_import"),
                    {"source_data": _transfer_source(self.species),
                     "organization": self.organization.pk, "thermal_zone": zone.pk},
                    format="json", HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
                )
                force_authenticate(request, user=self.user)
                response = BoxTransferImportAPIView.as_view()(request)
                if response.status_code != 201:
                    raise AssertionError(response.data)
                return response.data["id"]
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            with patch.object(services, "lock_species_code_allocation", side_effect=held_gate), \
                    patch.object(transfer_v1, "lock_local_id_registry", side_effect=import_registry):
                action_future = pool.submit(action)
                try:
                    self.assertTrue(ready.wait(timeout=10))
                    import_future = pool.submit(import_transfer)
                    self.assertTrue(importing.wait(timeout=10))
                    self.assertFalse(import_future.done())
                    release.set()
                    _action_pk, created = action_future.result(timeout=20)
                    box_pk = import_future.result(timeout=20)
                finally:
                    release.set()
                    action_future.result(timeout=20)
        self.assertTrue(created)
        self.assertEqual(Species.objects.count(), 1)
        self.assertEqual(InstitutionSpecies.objects.count(), 1)
        box = Box.objects.get(pk=box_pk)
        self.assertEqual((box.organization_id, box.strain.species_id, box.global_code),
                         (self.organization.pk, self.species.pk, "AAU-TAI-1.001"))
        self.assertEqual(BoxTransferImport.objects.get().created_box_id, box_pk)
        self.assertEqual(Strain.objects.count(), 2 if actual_create else 1)
        self.assertEqual(InstitutionStrainCreationRequest.objects.count(), int(actual_create))
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.IMPORT).count(), 1)

    def test_transfer_import_and_resolve_share_registry_before_species_locks(self):
        self._assert_transfer_waits_before_species_work(actual_create=False)

    def test_transfer_import_and_actual_strain_create_share_registry_before_species_locks(self):
        self._assert_transfer_waits_before_species_work(actual_create=True)

    def test_concurrent_edit_and_strain_resolve_share_one_association(self):
        results = self._concurrent_resolve(("edit", "strain"))
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sorted(created for _, created in results), [False, True])
        self.assertEqual(InstitutionSpecies.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(object_type="institution_species").count(), 1)
