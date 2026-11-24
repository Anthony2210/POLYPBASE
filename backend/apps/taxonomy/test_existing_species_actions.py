"""Explicit-submit actions on existing Species without an institutional profile.

Opening a Species page or a dialog writes nothing. The identification save and
the Strain creation establish the missing profile (and, for a Strain, the
missing AAA registration recovered from proven institutional evidence) inside
the submitted operation only, atomically, with the confirmed user action audited.
"""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box, BoxCodeNamespace
from apps.organizations.models import Organization
from config.disposable_postgres import disposable_postgres_declared

from . import institution_species_services as services
from .institution_strains import create_institution_strain
from .models import (
    BiologicalProvenance, InstitutionSpecies, InstitutionStrainCreationRequest, LocalStrainIdentity,
    LocalStrainNumberCounter, OrganizationProvenanceCode, OrganizationSpeciesCode, Origin, Species, Strain,
    StrainTranslation,
)
from .test_institution_species_api import HISTORICAL_URL, LIST_URL, InstitutionSpeciesApiBase
from .test_species_review_regressions import SpeciesReviewTransactionTestCase

HISTORICAL_STRAINS_URL = "api_admin_historical_species_strains"
PROFILE_STRAINS_URL = "api_admin_institution_species_strains"
RESOLVE_URL = "api_admin_institution_species_resolve"


def _state():
    """Every persisted row an existing-Species action could touch."""
    models = (
        Species, Strain, Box, BoxCodeNamespace, InstitutionSpecies, OrganizationSpeciesCode,
        OrganizationProvenanceCode, BiologicalProvenance, Origin, StrainTranslation, LocalStrainIdentity,
        LocalStrainNumberCounter, InstitutionStrainCreationRequest, AuditLog,
    )
    return {model.__name__: list(model.objects.order_by("pk").values()) for model in models}


class ExistingSpeciesBase(InstitutionSpeciesApiBase):
    def setUp(self):
        super().setUp()
        # Aquarium-like history: one owned Strain and four Boxes, no profile.
        self.species = Species.objects.create(scientific_name="Acromitus flagellatus")
        self.strain = Strain.objects.create(organization=self.a, species=self.species, code="AFL-TAI-1")
        for number in range(1, 5):
            Box.objects.create(
                organization=self.a, strain=self.strain, global_code=f"AFL-TAI-1.00{number}",
                box_number=f"00{number}",
            )
        network = patch("apps.taxonomy.worms.urlopen", side_effect=AssertionError("No network allowed"))
        network.start()
        self.addCleanup(network.stop)

    def remove_canonical_evidence(self):
        Strain.objects.filter(pk=self.strain.pk).update(code="legacy")
        for box in Box.objects.filter(strain=self.strain):
            Box.objects.filter(pk=box.pk).update(global_code=f"legacy-{box.pk}")

    def assign(self, code="AFL"):
        return OrganizationSpeciesCode.objects.create(organization=self.a, species=self.species, code=code)

    def patch_identification(self, payload, *, organization=None, user=None, species=None):
        self.client.force_login(user or self.admin)
        return self.client.patch(
            reverse(HISTORICAL_URL, args=[(species or self.species).pk]), data=json.dumps(payload),
            content_type="application/json", **self.headers(organization or self.a),
        )

    def strain_payload(self, **extra):
        return {"request_id": str(uuid.uuid4()), "country": "FR", "country_name": "France", "bbb_code": "FRA", **extra}

    def post_strain(self, payload=None, *, organization=None, user=None, species=None, profile=None):
        self.client.force_login(user or self.admin)
        url = (reverse(PROFILE_STRAINS_URL, args=[profile.pk]) if profile is not None
               else reverse(HISTORICAL_STRAINS_URL, args=[(species or self.species).pk]))
        return self.client.post(
            url, data=json.dumps(payload if payload is not None else self.strain_payload()),
            content_type="application/json", **self.headers(organization or self.a),
        )

    def journal(self, url_name):
        self.client.force_login(self.admin)
        response = self.client.get(reverse(url_name), **self.headers(self.a))
        self.assertEqual(response.status_code, 200, response.content)
        return {entry["id"] for entry in response.json()["results"]}


class OpeningWritesNothingTests(ExistingSpeciesBase):
    def test_list_detail_and_readiness_reads_write_nothing(self):
        self.assign()
        before = _state()
        self.assertEqual(self.get(LIST_URL, self.a).status_code, 200)
        for _ in range(2):
            body = self.get(HISTORICAL_URL, self.a, self.species.pk).json()
        self.assertIsNone(body["institution_species_id"])
        self.assertEqual((body["scientific_name"], body["species_code"]), ("Acromitus flagellatus", "AFL"))
        self.assertTrue(body["can_create_strain"])
        self.assertEqual(_state(), before)

    def test_recoverable_aaa_is_ready_but_not_registered_by_reading(self):
        before = _state()
        body = self.get(HISTORICAL_URL, self.a, self.species.pk).json()
        self.assertEqual((body["species_code"], body["can_create_strain"], body["strain_creation_issue"]),
                         ("AFL", True, None))
        self.assertEqual(_state(), before)

    def test_retired_resolve_still_answers_410_without_writes(self):
        before = _state()
        self.client.force_login(self.admin)
        for action in ("edit", "strain"):
            response = self.client.post(
                reverse(RESOLVE_URL), data=json.dumps({"species_id": self.species.pk, "action": action}),
                content_type="application/json", **self.headers(self.a),
            )
            self.assertEqual(response.status_code, 410, response.content)
        self.assertEqual(_state(), before)


class IdentificationSaveTests(ExistingSpeciesBase):
    def test_unchanged_prefill_creates_no_profile_and_no_audit(self):
        self.assign()
        before = _state()
        with patch.object(services, "_verify_reference", side_effect=AssertionError("No WoRMS call")):
            response = self.patch_identification({"scientific_name": " Acromitus  flagellatus ", "worms_aphia_id": None})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.json()["institution_species_id"])
        self.assertEqual(_state(), before)

    def test_explicit_save_creates_profile_and_applies_change_atomically(self):
        assignment = self.assign()
        before = _state()
        with patch.object(services, "_verify_reference") as verify:
            response = self.patch_identification({"scientific_name": "Acromitus flagellatus", "worms_aphia_id": 290000})
        self.assertEqual(response.status_code, 201, response.content)
        verify.assert_called_once_with(290000, "Acromitus flagellatus")
        profile = InstitutionSpecies.objects.get(organization=self.a)
        self.assertEqual(response.json(), {
            "id": profile.pk, "species_id": self.species.pk, "scientific_name": "Acromitus flagellatus",
            "worms_aphia_id": 290000, "worms_url": response.json()["worms_url"], "species_code": "AFL",
            "revision": 1, "strain_count": 1,
        })
        after = _state()
        for unchanged in ("Species", "Strain", "Box", "OrganizationSpeciesCode", "LocalStrainIdentity"):
            self.assertEqual(after[unchanged], before[unchanged], unchanged)
        self.assertEqual(OrganizationSpeciesCode.objects.get().pk, assignment.pk)
        audits = list(AuditLog.objects.order_by("pk"))
        self.assertEqual([(a.action, a.object_type, a.object_id, a.user_id, a.organization_id) for a in audits], [
            (AuditLog.Action.CREATION, "institution_species", str(profile.pk), self.admin.pk, self.a.pk),
            (AuditLog.Action.UPDATE, "institution_species", str(profile.pk), self.admin.pk, self.a.pk),
        ])
        self.assertEqual(audits[0].metadata["source"], services.EDIT_ASSOCIATION_SOURCE)
        self.assertEqual(audits[1].metadata["changes"], {"worms_aphia_id": {"before": None, "after": 290000}})
        self.assertTrue(audits[1].metadata["institution_species_created"])
        # The personal journal shows the confirmed edit only; the admin audit keeps both.
        self.assertEqual(self.journal("api_profile_actions") & {a.pk for a in audits}, {audits[1].pk})
        self.assertTrue({a.pk for a in audits} <= self.journal("api_account_audit_log"))

    def test_name_change_keeps_shared_reference_and_needs_no_aaa(self):
        # No assignment and no canonical evidence: editing must neither need nor create an AAA.
        self.remove_canonical_evidence()
        before = _state()
        with patch.object(services, "allocate_species_code", side_effect=AssertionError("No AAA")), \
                patch("apps.taxonomy.species_codes.candidate_codes", side_effect=AssertionError("No AAA")):
            response = self.patch_identification({"scientific_name": "Acromitus  maculosus", "worms_aphia_id": None})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["scientific_name"], "Acromitus maculosus")
        self.assertIsNone(response.json()["species_code"])
        self.assertEqual(_state()["Species"], before["Species"])
        self.assertFalse(OrganizationSpeciesCode.objects.exists())
        update = AuditLog.objects.get(action=AuditLog.Action.UPDATE)
        self.assertEqual(update.metadata["changes"], {
            "scientific_name": {"before": "Acromitus flagellatus", "after": "Acromitus maculosus"},
        })
        # The old URL now reads the profile's local information.
        detail = self.get(HISTORICAL_URL, self.a, self.species.pk).json()
        self.assertEqual((detail["institution_species_id"], detail["scientific_name"], detail["shared_scientific_name"]),
                         (response.json()["id"], "Acromitus maculosus", "Acromitus flagellatus"))

    def test_worms_rejection_and_unavailability_write_nothing(self):
        before = _state()
        with patch.object(services, "_verify_reference",
                          side_effect=services.WormsReferenceRejected("worms_reference_name_mismatch")):
            response = self.patch_identification({"scientific_name": "Acromitus flagellatus", "worms_aphia_id": 5})
        self.assertEqual((response.status_code, response.json()), (400, {"code": "worms_reference_name_mismatch"}))
        with patch.object(services, "_verify_reference", side_effect=services.WormsReferenceUnavailable()):
            response = self.patch_identification({"scientific_name": "Acromitus flagellatus", "worms_aphia_id": 5})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(_state(), before)

    def test_existing_profile_makes_the_intent_stale(self):
        profile = InstitutionSpecies.objects.create(
            organization=self.a, species=self.species, scientific_name="Acromitus flagellatus", revision=3,
        )
        before = _state()
        response = self.patch_identification({"scientific_name": "Acromitus maculosus", "worms_aphia_id": None})
        self.assertEqual((response.status_code, response.json()), (409, {
            "code": "stale_revision", "current_revision": 3, "institution_species_id": profile.pk,
        }))
        self.assertEqual(_state(), before)

    def test_local_name_reference_and_historical_collisions_are_refused(self):
        other = Species.objects.create(scientific_name="Cassiopea andromeda")
        InstitutionSpecies.objects.create(organization=self.a, species=other, scientific_name="Cassiopea andromeda",
                                          worms_aphia_id=135306)
        historical = Species.objects.create(scientific_name="Aurelia aurita")
        Strain.objects.create(organization=self.a, species=historical, code="AAU-FRA-1")
        before = _state()
        cases = (
            ({"scientific_name": "CASSIOPEA andromeda", "worms_aphia_id": None}, "institution_species_name_exists"),
            ({"scientific_name": "Acromitus flagellatus", "worms_aphia_id": 135306}, "institution_aphia_id_exists"),
            ({"scientific_name": "Aurelia aurita", "worms_aphia_id": None}, "historical_name_collision"),
        )
        with patch.object(services, "_verify_reference"):
            for payload, code in cases:
                response = self.patch_identification(payload)
                self.assertEqual((response.status_code, response.json()["code"]), (409, code), payload)
        self.assertEqual(_state(), before)

    def test_audit_failure_rolls_back_profile_and_association_audit(self):
        before = _state()
        original = services._audit

        def fail_update(**kwargs):
            if kwargs["action"] == AuditLog.Action.UPDATE:
                raise RuntimeError("update audit failed")
            return original(**kwargs)

        with patch.object(services, "_audit", side_effect=fail_update), \
                self.assertRaisesMessage(RuntimeError, "update audit failed"):
            self.patch_identification({"scientific_name": "Acromitus maculosus", "worms_aphia_id": None})
        self.assertEqual(_state(), before)

    def test_payload_is_strict_and_explicit(self):
        before = _state()
        for payload in (
            {"scientific_name": "Acromitus maculosus"},
            {"scientific_name": "Acromitus maculosus", "worms_aphia_id": None, "species_code": "XXX"},
            {"scientific_name": "", "worms_aphia_id": None},
            {"scientific_name": "Acromitus maculosus", "worms_aphia_id": 0},
            {"scientific_name": "Acromitus maculosus", "worms_aphia_id": True},
            ["Acromitus maculosus"],
        ):
            self.assertEqual(self.patch_identification(payload).status_code, 400, payload)
        self.assertEqual(_state(), before)

    def test_unmanaged_unknown_and_foreign_species_are_not_found(self):
        foreign = Species.objects.create(scientific_name="Phyllorhiza punctata")
        Strain.objects.create(organization=self.b, species=foreign, code="PPU-FRA-1")
        OrganizationSpeciesCode.objects.create(organization=self.b, species=foreign, code="PPU")
        unused = Species.objects.create(scientific_name="Mastigias papua")
        before = _state()
        payload = {"scientific_name": "Changed name", "worms_aphia_id": None}
        for species in (foreign, unused):
            self.assertEqual(self.patch_identification(payload, species=species).status_code, 404)
        self.client.force_login(self.admin)
        response = self.client.patch(
            reverse(HISTORICAL_URL, args=[999999]), data=json.dumps(payload),
            content_type="application/json", **self.headers(self.a),
        )
        self.assertEqual(response.status_code, 404)
        # Institution B never reaches A's Species through its ID.
        self.assertEqual(self.patch_identification(payload, organization=self.b, user=self.b_admin).status_code, 404)
        self.assertEqual(_state(), before)

    def test_only_active_admins_of_the_active_institution_may_save(self):
        before = _state()
        payload = {"scientific_name": "Acromitus maculosus", "worms_aphia_id": None}
        for user in (self.tech, self.viewer, self.b_admin):
            self.assertEqual(self.patch_identification(payload, user=user).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.patch(reverse(HISTORICAL_URL, args=[self.species.pk]), data=json.dumps(payload),
                                     content_type="application/json")
        self.assertEqual(response.status_code, 403, "an explicit institution context is required")
        OrganizationMembership.objects.filter(user=self.admin).update(is_active=False)
        self.assertEqual(self.patch_identification(payload).status_code, 403)
        self.client.logout()
        response = self.client.patch(reverse(HISTORICAL_URL, args=[self.species.pk]), data=json.dumps(payload),
                                     content_type="application/json", **self.headers(self.a))
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(_state(), before)

    def test_authority_is_rechecked_under_locks(self):
        original = services.lock_managed_species
        calls = []

        def revoke_then_lock(**kwargs):
            calls.append(1)
            if len(calls) == 2:
                OrganizationMembership.objects.filter(user=self.admin).update(is_active=False)
            return original(**kwargs)

        before = _state()
        with patch.object(services, "lock_managed_species", side_effect=revoke_then_lock):
            response = self.patch_identification({"scientific_name": "Acromitus maculosus", "worms_aphia_id": None})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(calls), 2)
        self.assertEqual(_state(), before)


class ExistingSpeciesStrainCreationTests(ExistingSpeciesBase):
    def assert_existing_identity_preserved(self, before):
        after = _state()
        for key in ("Species", "Box"):
            self.assertEqual(after[key], before[key], key)
        existing = {row["id"]: row for row in after["Strain"]}
        for row in before["Strain"]:
            self.assertEqual(existing[row["id"]], row)

    def test_valid_registered_aaa_and_missing_profile(self):
        assignment = self.assign()
        before = _state()
        response = self.post_strain()
        self.assertEqual(response.status_code, 201, response.content)
        profile = InstitutionSpecies.objects.get(organization=self.a)
        self.assertEqual((profile.species_id, profile.scientific_name, profile.worms_aphia_id, profile.revision),
                         (self.species.pk, "Acromitus flagellatus", None, 1))
        body = response.json()
        self.assertEqual(body["institution_species_id"], profile.pk)
        self.assertEqual(body["strain"]["code"], "AFL-FRA-1")
        self.assertEqual(OrganizationSpeciesCode.objects.get().pk, assignment.pk)
        self.assert_existing_identity_preserved(before)
        audits = list(AuditLog.objects.order_by("pk").values_list("object_type", "action", "user_id", "organization_id"))
        self.assertEqual(sorted(audits), sorted([
            ("biological_provenance", AuditLog.Action.CREATION, self.admin.pk, self.a.pk),
            ("organization_provenance_code", AuditLog.Action.CREATION, self.admin.pk, self.a.pk),
            ("strain", AuditLog.Action.CREATION, self.admin.pk, self.a.pk),
            ("institution_species", AuditLog.Action.CREATION, self.admin.pk, self.a.pk),
        ]))
        association = AuditLog.objects.get(object_type="institution_species")
        strain_id = body["strain"]["id"]
        self.assertEqual((association.metadata["source"], association.metadata["strain_id"]),
                         (services.STRAIN_ASSOCIATION_SOURCE, strain_id))
        personal = self.journal("api_profile_actions")
        self.assertIn(AuditLog.objects.get(object_type="strain").pk, personal)
        self.assertNotIn(association.pk, personal)
        self.assertIn(association.pk, self.journal("api_account_audit_log"))
        # The page now reads an established profile, ready for the Box flow.
        detail = self.get(HISTORICAL_URL, self.a, self.species.pk).json()
        self.assertEqual(detail["institution_species_id"], profile.pk)
        created = next(item for item in detail["strains"] if item["id"] == strain_id)
        self.assertTrue(created["is_box_usable"])

    def test_existing_profile_with_missing_registration_recovers_recorded_aaa(self):
        profile = InstitutionSpecies.objects.create(
            organization=self.a, species=self.species, scientific_name="Acromitus flagellatus", revision=4,
        )
        before = _state()
        with patch("apps.taxonomy.species_codes.candidate_codes", side_effect=AssertionError("Keep recorded AAA")):
            response = self.post_strain(profile=profile)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["strain"]["code"], "AFL-FRA-1")
        assignment = OrganizationSpeciesCode.objects.get(organization=self.a, species=self.species)
        self.assertEqual(assignment.code, "AFL")
        profile.refresh_from_db()
        self.assertEqual(profile.revision, 4)
        audit = AuditLog.objects.get(object_type="organization_species_code")
        self.assertEqual((audit.user_id, audit.organization_id, audit.object_id), (self.admin.pk, self.a.pk, str(assignment.pk)))
        self.assertTrue(audit.metadata["recorded_code_preserved"])
        self.assertEqual(audit.metadata["source"], services.STRAIN_ASSOCIATION_SOURCE)
        self.assertFalse(AuditLog.objects.filter(object_type="institution_species").exists())
        self.assert_existing_identity_preserved(before)

    def test_missing_registration_and_profile_recovered_from_own_box_only(self):
        legacy = Strain.objects.create(species=self.species, code="legacy-unowned")
        Box.objects.filter(strain=self.strain).update(strain=legacy)
        self.strain.delete()
        before = _state()
        response = self.post_strain()
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(OrganizationSpeciesCode.objects.get(organization=self.a).code, "AFL")
        legacy.refresh_from_db()
        self.assertIsNone(legacy.organization_id, "an unowned Strain is never claimed")
        self.assertEqual(Strain.objects.get(pk=response.json()["strain"]["id"]).organization_id, self.a.pk)
        self.assert_existing_identity_preserved(before)
        self.assertEqual(AuditLog.objects.filter(object_type__in=("institution_species", "organization_species_code"))
                         .count(), 2)

    def test_missing_evidence_never_generates_a_first_aaa(self):
        self.remove_canonical_evidence()
        detail = self.get(HISTORICAL_URL, self.a, self.species.pk).json()
        self.assertEqual((detail["can_create_strain"], detail["strain_creation_issue"]), (False, "species_code_missing"))
        before = _state()
        with patch("apps.taxonomy.species_codes.candidate_codes", side_effect=AssertionError("Never generated")):
            response = self.post_strain()
        self.assertEqual((response.status_code, response.json()), (409, {"code": "species_code_missing"}))
        self.assertEqual(_state(), before)

    def test_contradictory_evidence_is_refused_without_writes(self):
        Strain.objects.create(organization=self.a, species=self.species, code="XYZ-FRA-2")
        detail = self.get(HISTORICAL_URL, self.a, self.species.pk).json()
        self.assertEqual(detail["strain_creation_issue"], "species_code_inconsistent")
        before = _state()
        response = self.post_strain()
        self.assertEqual((response.status_code, response.json()), (409, {"code": "species_code_inconsistent"}))
        self.assertEqual(_state(), before)

    def test_code_reserved_by_another_identity_is_refused_without_writes(self):
        other = Species.objects.create(scientific_name="Cassiopea andromeda")
        OrganizationSpeciesCode.objects.create(organization=self.b, species=other, code="AFL")
        detail = self.get(HISTORICAL_URL, self.a, self.species.pk).json()
        self.assertEqual(detail["strain_creation_issue"], "species_code_inconsistent")
        before = _state()
        response = self.post_strain()
        self.assertEqual((response.status_code, response.json()), (409, {"code": "historical_code_conflict"}))
        self.assertEqual(_state(), before)

    def test_code_used_locally_by_another_species_is_refused_without_writes(self):
        other = Species.objects.create(scientific_name="Cassiopea andromeda")
        Strain.objects.create(organization=self.a, species=other, code="AFL-FRA-9")
        detail = self.get(HISTORICAL_URL, self.a, self.species.pk).json()
        self.assertEqual(detail["strain_creation_issue"], "species_code_inconsistent")
        before = _state()
        response = self.post_strain()
        self.assertEqual((response.status_code, response.json()), (409, {"code": "historical_code_conflict"}))
        self.assertEqual(_state(), before)

    def test_later_failure_rolls_back_profile_and_registration(self):
        # BBB already used by another country location: refused after association.
        provenance = BiologicalProvenance.objects.create(name="Spain")
        OrganizationProvenanceCode.objects.create(organization=self.a, biological_provenance=provenance,
                                                  code="FRA", country_code="ES")
        before = _state()
        response = self.post_strain()
        self.assertEqual((response.status_code, response.json()["code"]), (409, "collection_location_code_taken"))
        self.assertEqual(_state(), before)

    def test_profile_name_conflict_rolls_back(self):
        other = Species.objects.create(scientific_name="Cassiopea andromeda")
        InstitutionSpecies.objects.create(organization=self.a, species=other, scientific_name="Acromitus flagellatus")
        before = _state()
        response = self.post_strain()
        self.assertEqual((response.status_code, response.json()["code"]), (409, "institution_species_name_exists"))
        self.assertEqual(_state(), before)

    def test_side_effect_audit_failure_rolls_back_everything(self):
        before = _state()
        original = services._audit

        def fail_code(**kwargs):
            if kwargs["object_type"] == "organization_species_code":
                raise RuntimeError("code audit failed")
            return original(**kwargs)

        with patch.object(services, "_audit", side_effect=fail_code), \
                self.assertRaisesMessage(RuntimeError, "code audit failed"):
            self.post_strain()
        self.assertEqual(_state(), before)

    def test_retry_is_idempotent_and_a_changed_intent_conflicts(self):
        payload = self.strain_payload()
        first = self.post_strain(payload)
        self.assertEqual(first.status_code, 201, first.content)
        after_first = _state()
        replay = self.post_strain(payload)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay.json()["strain"], first.json()["strain"])
        self.assertEqual(_state(), after_first)
        changed = self.post_strain({**payload, "name": "Other intent", "name_language": "fr"})
        self.assertEqual((changed.status_code, changed.json()), (409, {"code": "strain_creation_request_conflict"}))
        self.assertEqual(_state(), after_first)
        # A second deliberate intent creates the next Strain on the established profile.
        second = self.post_strain()
        self.assertEqual(second.json()["strain"]["code"], "AFL-FRA-2")
        self.assertEqual(InstitutionSpecies.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(object_type="institution_species").count(), 1)

    def test_authorization_and_institution_scope(self):
        foreign = Species.objects.create(scientific_name="Phyllorhiza punctata")
        Strain.objects.create(organization=self.b, species=foreign, code="PPU-FRA-1")
        OrganizationSpeciesCode.objects.create(organization=self.b, species=foreign, code="PPU")
        before = _state()
        for user in (self.tech, self.viewer, self.b_admin):
            self.assertEqual(self.post_strain(user=user).status_code, 403)
        self.assertEqual(self.post_strain(species=foreign).status_code, 404)
        self.assertEqual(self.post_strain(organization=self.b, user=self.b_admin).status_code, 404)
        self.client.force_login(self.admin)
        response = self.client.post(reverse(HISTORICAL_STRAINS_URL, args=[self.species.pk]),
                                    data=json.dumps(self.strain_payload()), content_type="application/json")
        self.assertEqual(response.status_code, 403)
        OrganizationMembership.objects.filter(user=self.admin).update(is_active=False)
        self.assertEqual(self.post_strain().status_code, 403)
        self.assertEqual(_state(), before)

    def test_modern_profile_route_is_unchanged(self):
        response = self.register(self.a, {"scientific_name": "Chrysaora hysoscella"})
        self.assertEqual(response.status_code, 201, response.content)
        profile = InstitutionSpecies.objects.get(pk=response.json()["id"])
        created = self.post_strain(profile=profile)
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()["strain"]["code"], "CHY-FRA-1")
        self.assertEqual(created.json()["institution_species_id"], profile.pk)
        self.assertFalse(AuditLog.objects.filter(metadata__source=services.STRAIN_ASSOCIATION_SOURCE).exists())


@skipUnless(
    disposable_postgres_declared(),
    "Requires explicitly verified disposable PostgreSQL; SQLite cannot prove row locks.",
)
class ExistingSpeciesConcurrencyTests(SpeciesReviewTransactionTestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Existing species concurrency")
        self.user = get_user_model().objects.create_user(username="existing_admin", email="existing@example.org")
        OrganizationMembership.objects.create(user=self.user, organization=self.organization, role="admin")
        self.species = Species.objects.create(scientific_name="Acromitus flagellatus")
        strain = Strain.objects.create(organization=self.organization, species=self.species, code="AFL-TAI-1")
        Box.objects.create(organization=self.organization, strain=strain, global_code="AFL-TAI-1.001", box_number="001")

    def race(self, *calls):
        barrier = Barrier(len(calls))

        def run(call):
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=self.user.pk)
                organization = Organization.objects.get(pk=self.organization.pk)
                barrier.wait(timeout=10)
                try:
                    return call(user, organization)
                except services.InstitutionSpeciesConflict as conflict:
                    return conflict.code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            futures = [pool.submit(run, call) for call in calls]
            return [future.result(timeout=30) for future in futures]

    def create(self, request_id=None):
        payload = {"request_id": str(request_id or uuid.uuid4()), "country": "FR", "bbb_code": "FRA"}
        return lambda user, organization: create_institution_strain(
            user=user, organization=organization, species_id=self.species.pk, data=payload,
        )

    def test_concurrent_first_association_creates_one_profile_and_one_registration(self):
        results = self.race(self.create(), self.create())
        self.assertEqual(sorted(created for _strain, created in results), [True, True])
        self.assertEqual(InstitutionSpecies.objects.count(), 1)
        self.assertEqual(OrganizationSpeciesCode.objects.get().code, "AFL")
        self.assertEqual(sorted(Strain.objects.exclude(code="AFL-TAI-1").values_list("code", flat=True)),
                         ["AFL-FRA-1", "AFL-FRA-2"])
        self.assertEqual(AuditLog.objects.filter(object_type="institution_species").count(), 1)
        self.assertEqual(AuditLog.objects.filter(object_type="organization_species_code").count(), 1)

    def test_concurrent_retries_of_one_request_create_one_strain(self):
        request_id = uuid.uuid4()
        results = self.race(self.create(request_id), self.create(request_id))
        self.assertEqual(results[0][0].pk, results[1][0].pk)
        self.assertEqual(sorted(created for _strain, created in results), [False, True])
        self.assertEqual(Strain.objects.count(), 2)
        self.assertEqual(InstitutionStrainCreationRequest.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(object_type="strain").count(), 1)

    def test_concurrent_edit_and_strain_creation_never_overwrite_or_duplicate(self):
        def edit(user, organization):
            return services.edit_existing_species(
                user=user, organization=organization, species_id=self.species.pk,
                data={"scientific_name": "Acromitus maculosus", "worms_aphia_id": None},
            )

        edit_result, create_result = self.race(edit, self.create())
        self.assertTrue(create_result[1])
        self.assertEqual(InstitutionSpecies.objects.count(), 1)
        profile = InstitutionSpecies.objects.get()
        if edit_result == "stale_revision":
            # The Strain association won: the edit must be reloaded, never applied blindly.
            self.assertEqual(profile.scientific_name, "Acromitus flagellatus")
            self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.UPDATE).exists())
        else:
            self.assertEqual(edit_result[0].pk, profile.pk)
            self.assertEqual(profile.scientific_name, "Acromitus maculosus")
        self.assertEqual(AuditLog.objects.filter(object_type="institution_species",
                                                 action=AuditLog.Action.CREATION).count(), 1)
        self.assertEqual(Species.objects.get().scientific_name, "Acromitus flagellatus")
