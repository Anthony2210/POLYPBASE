"""Internal Species recoding: preservation, replay, fail-closed and atomicity tests.

SQLite exercises functional transactions, not PostgreSQL lock guarantees.
Only fixture helpers are inherited; no preview test class is collected here.
"""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Barrier, Event
from unittest import skipUnless
from datetime import date
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import serializers
from django.core.signing import TimestampSigner
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.http import Http404
from django.urls import get_resolver, reverse
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import OrganizationMembership, UserPreference
from apps.audit.models import AuditLog
from apps.cultures.box_codes import allocate_box_codes
from apps.cultures.identity_resolution import resolve_box_local_id
from apps.cultures.models import (
    Box, BoxCodeNamespace, BoxGlobalIdentity, BoxLocalIdAlias, BoxLocation,
    BoxMovement, SubcultureAllocation, SubcultureEvent, ThermalZone,
    new_box_global_identity,
)
from apps.cultures.qr import box_scan_url, sync_box_qr_tag
from apps.measurements.models import BiologicalMeasurement, MeasurementImportReceipt, Observation
from apps.organizations.models import Organization

from . import species_code_change_apply as service
from .institution_species_services import (
    InstitutionSpeciesConflict, InstitutionSpeciesInvalid, InstitutionSpeciesNotFound,
)
from .models import (
    GlobalStrainIdentity, InstitutionSpecies, LocalStrainIdentity,
    LocalStrainNumberCounter, OrganizationSpeciesCode, Species, SpeciesCodeChangeRequest, Strain,
)
from .species_code_change import plan_species_code_change
from .test_species_code_change_preview import SpeciesCodeChangePreviewBase
from config.disposable_postgres import disposable_postgres_declared

from .test_species_review_regressions import SpeciesReviewTransactionTestCase


def _snapshot():
    """Include every concrete installed model, not just expected write targets."""
    return {
        model._meta.label_lower: serializers.serialize("json", model._base_manager.order_by("pk"))
        for model in apps.get_models() if not model._meta.proxy
    }


def _records(model):
    return json.loads(serializers.serialize("json", model._base_manager.order_by("pk")))


class DeliberateFailure(RuntimeError):
    pass


class SpeciesCodeChangeApplyTests(SpeciesCodeChangePreviewBase):
    def setUp(self):
        super().setUp()
        self.active = self.box("AAU-BRE-1.001", local_code="AAU-personal-label")
        UserPreference.objects.get_or_create(user=self.admin)

    def make_plan(self, proposed="AUR", **overrides):
        return plan_species_code_change(**{
            "user": self.admin, "organization": self.a, "profile_pk": self.profile.pk,
            "proposed_code": proposed, **overrides,
        })

    def arguments(self, proposed="AUR", **overrides):
        return {
            "user": self.admin, "organization": self.a, "profile_pk": self.profile.pk,
            "proposed_code": proposed,
            "expected_plan_fingerprint": self.make_plan(proposed)["plan_fingerprint"],
            "request_id": uuid.uuid4(), **overrides,
        }

    def assert_rejected(self, arguments, exception=InstitutionSpeciesConflict, code=None):
        before = _snapshot()
        with self.assertRaises(exception) as caught:
            service.apply_species_code_change(**arguments)
        self.assertEqual(_snapshot(), before)
        if code is not None:
            self.assertEqual(caught.exception.code, code)
        return caught.exception

    def rich_fixture(self):
        self.strain.global_identity = GlobalStrainIdentity.objects.create()
        self.strain.save(update_fields=["global_identity"])
        local_identity = LocalStrainIdentity.objects.create(
            strain=self.strain, species_code_assignment=self.aaa,
        )
        LocalStrainNumberCounter.objects.create(
            organization=self.a, species=self.species, last_number=17,
        )
        identity = new_box_global_identity(BoxGlobalIdentity.AllocationReason.AUTOMATIC_CREATION)
        self.active.global_identity = identity
        self.active.save(update_fields=["global_identity"])
        # A separate previous field and two registered aliases must all survive.
        previous = self.box("AAU-BRE-1.004", previous_global_code="PREVIOUS.004")
        for code in ("OLDER.001", "OLDEST.001"):
            BoxLocalIdAlias.objects.create(box=self.active, code=code)
        inactive = self.box("AAU-BRE-1.009", status=Box.Status.INACTIVE, stop_reason="History")
        pending = self.box("AAU-BRE-1.012", status=Box.Status.PENDING_REVIEW)
        merged = self.box("AAU-BRE-1.013", merged_into=self.active, status=Box.Status.INACTIVE)
        second = self.owned_strain("AAU-FRA-2")
        second_box = self.box("AAU-FRA-2.003", strain=second)
        self.owned_strain("AAU-KOB-3")  # No Box, but still part of the rename.
        BoxCodeNamespace.objects.filter(namespace=self.strain.code).update(high_water=70)
        tag = sync_box_qr_tag(self.active)
        zone = ThermalZone.objects.create(organization=self.a, name="Laboratory")
        BoxLocation.objects.create(box=self.active, thermal_zone=zone, notes="Original location")
        BoxMovement.objects.create(box=self.active, to_thermal_zone=zone, user=self.admin)
        BiologicalMeasurement.objects.create(
            box=self.active, measured_on=date(2026, 1, 5), polyp_count=0,
            ephyrae_count=0, strobila_count=0, salinity_psu=0,
            culture_status=BiologicalMeasurement.CultureStatus.GOOD,
            needs_attention=False, notes="Measured zero, not missing", user=self.admin,
        )
        Observation.objects.create(box=self.active, notes="Original observation", user=self.admin)
        scientific = {"box_code": self.active.global_code, "strain_code": self.strain.code,
                      "polyp_count": 0, "unknown": None}
        event = SubcultureEvent.objects.create(
            parent_box=self.active, user=self.admin, occurred_at=timezone.now(),
            parent_state_sequence=1, parent_polyp_count_before=0,
            allocated_polyp_count=0, parent_polyp_count_after=0,
            parent_state_snapshot=scientific, author_name="Original author",
        )
        SubcultureAllocation.objects.create(
            event=event, child_box=previous, position=1, allocated_polyps=0,
            child_global_code=previous.global_code,
        )
        MeasurementImportReceipt.objects.create(
            organization=self.a, actor_id=self.admin.pk, source_sha256="a" * 64,
            source_bytes=b"original scientific evidence", preparation_fingerprint="b" * 64,
            plan_fingerprint="c" * 64, preparation=scientific,
            accepted_plan={"scientific_snapshot": scientific}, result=scientific,
        )
        AuditLog.objects.create(
            organization=self.a, user=self.admin, action=AuditLog.Action.VIEW,
            object_type="box", object_id=self.active.global_code,
            description="Historical Local ID", metadata=scientific,
        )
        OrganizationSpeciesCode.objects.create(organization=self.b, species=self.species, code="AAU")
        InstitutionSpecies.objects.create(
            organization=self.b, species=self.species, scientific_name="Aurelia aurita",
        )
        foreign = self.owned_strain("AAU-EXT-8", organization=self.b)
        self.box("AAU-EXT-8.001", strain=foreign, organization=self.b)
        return [self.active, previous, inactive, pending, merged, second_box], local_identity, tag

    def test_full_recode_preserves_every_unrelated_field_and_scientific_record(self):
        boxes, local_identity, tag = self.rich_fixture()
        before = _snapshot()
        old_strains = _records(Strain)
        old_boxes = _records(Box)
        old_aliases = _records(BoxLocalIdAlias)
        old_audits = _records(AuditLog)
        old_revision = self.profile.revision
        scan_url = box_scan_url(self.active)
        args = self.arguments(" aur ")
        self.assertTrue(self.make_plan()["feasible"])

        receipt, created = service.apply_species_code_change(**args)

        self.assertTrue(created)
        receipt.refresh_from_db()
        self.assertEqual((receipt.organization_id, receipt.actor_id, receipt.profile_id),
                         (self.a.pk, self.admin.pk, self.profile.pk))
        self.assertEqual(receipt.intent, {
            "profile": self.profile.pk, "proposed_code": "AUR",
            "expected_plan_fingerprint": args["expected_plan_fingerprint"],
        })
        result = receipt.result
        self.assertEqual(result["operation"], "species_code_change")
        self.assertEqual(str(uuid.UUID(result["batch_id"])), result["batch_id"])
        self.assertEqual(result["request_id"], str(args["request_id"]))
        self.assertEqual((result["organization_id"], result["profile_id"], result["species_id"],
                          result["assignment_id"]),
                         (self.a.pk, self.profile.pk, self.species.pk, self.aaa.pk))
        self.assertEqual((result["old_code"], result["new_code"], result["old_revision"],
                          result["new_revision"], result["plan_fingerprint"]),
                         ("AAU", "AUR", old_revision, old_revision + 1, args["expected_plan_fingerprint"]))
        expected_strains = []
        for row in old_strains:
            if row["fields"]["organization"] == self.a.pk:
                old = row["fields"]["code"]
                row["fields"]["code"] = "AUR" + old[3:]
                expected_strains.append({"id": row["pk"], "old_code": old,
                                         "new_code": row["fields"]["code"]})
        self.assertEqual(_records(Strain), old_strains)
        self.assertEqual(result["strains"], expected_strains)
        expected_boxes = []
        for row in old_boxes:
            if row["fields"]["organization"] == self.a.pk and row["fields"]["merged_into"] is None:
                old = row["fields"]["global_code"]
                row["fields"]["global_code"] = "AUR" + old[3:]
                expected_boxes.append({"id": row["pk"], "old_local_id": old,
                                       "new_local_id": row["fields"]["global_code"]})
        self.assertEqual(_records(Box), old_boxes)
        self.assertEqual(result["boxes"], expected_boxes)
        self.assertEqual(result["retained_merged_box_ids"], [boxes[4].pk])
        self.assertEqual(_records(BoxLocalIdAlias)[:len(old_aliases)], old_aliases)
        self.assertEqual(BoxLocalIdAlias.objects.count(), len(old_aliases) + len(expected_boxes))
        for item in expected_boxes:
            self.assertEqual(BoxLocalIdAlias.objects.get(code=item["old_local_id"]).box_id, item["id"])
        self.assertEqual(_records(AuditLog)[:-1], old_audits)
        audit = AuditLog.objects.latest("pk")
        self.assertEqual((audit.organization_id, audit.user_id, audit.action, audit.object_type,
                          audit.object_id, audit.description, audit.metadata),
                         (self.a.pk, self.admin.pk, AuditLog.Action.UPDATE, "institution_species",
                          str(self.profile.pk), "Institutional species code changed", result))
        self.aaa.refresh_from_db()
        local_identity.refresh_from_db()
        self.assertEqual(local_identity.species_code_assignment_id, self.aaa.pk)
        self.assertEqual(local_identity.species_code_assignment.code, "AUR")
        self.assertEqual(LocalStrainIdentity.objects.get(strain=self.strain).pk, local_identity.pk)
        self.assertEqual(self.aaa.code, "AUR")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.revision, old_revision + 1)
        # All other models, including full measurement fields, portable IDs,
        # locations, movements, allocations and JSON evidence, must be identical.
        allowed = {model._meta.label_lower for model in (
            Strain, Box, BoxCodeNamespace, BoxLocalIdAlias, OrganizationSpeciesCode,
            InstitutionSpecies, AuditLog, SpeciesCodeChangeRequest,
        )}
        after = _snapshot()
        for name in before.keys() - allowed:
            self.assertEqual(after[name], before[name], name)
        for model, target_pk, field, value in (
            (OrganizationSpeciesCode, self.aaa.pk, "code", "AUR"),
            (InstitutionSpecies, self.profile.pk, "revision", old_revision + 1),
        ):
            expected = json.loads(before[model._meta.label_lower])
            for row in expected:
                if row["pk"] == target_pk:
                    row["fields"][field] = value
            self.assertEqual(_records(model), expected)
        self.active.refresh_from_db()
        tag.refresh_from_db()
        self.assertEqual((box_scan_url(self.active), tag.url, tag.code),
                         (scan_url, scan_url, f"box:{self.active.pk}"))
        for code in ("AAU-BRE-1.001", "AUR-BRE-1.001", "OLDER.001", "OLDEST.001"):
            self.assertEqual(resolve_box_local_id(Box.objects.filter(organization=self.a), code).pk,
                             self.active.pk)
            with self.assertRaises(Http404):
                resolve_box_local_id(Box.objects.filter(organization=self.b), code)
        self.assertEqual(resolve_box_local_id(Box.objects.filter(organization=self.a), "PREVIOUS.004").pk,
                         boxes[1].pk)
        self.client.force_login(self.admin)
        resolved = self.client.get(
            reverse("api_box_global_id_resolve", args=[self.active.global_identity_id]), **self.headers(self.a),
        )
        self.assertEqual(resolved.json(), {"id": self.active.pk,
                         "global_id": str(self.active.global_identity_id), "global_code": "AUR-BRE-1.001"})
        for item in expected_boxes:
            for code in (item["old_local_id"], item["new_local_id"]):
                response = self.client.get(reverse("api_box_detail_by_code", args=[code]), **self.headers(self.a))
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.json()["id"], item["id"])
        self.assertEqual(BoxCodeNamespace.objects.get(namespace="AUR-BRE-1").high_water, 70)
        self.assertEqual(BoxCodeNamespace.objects.get(namespace="AAU-BRE-1").high_water, 70)
        with transaction.atomic():
            self.assertEqual(allocate_box_codes("AUR-BRE-1", 2),
                             [("AUR-BRE-1.071", "071"), ("AUR-BRE-1.072", "072")])

    def test_noncanonical_merged_donor_and_its_aliases_are_retained(self):
        donor = self.box(
            "AAU-BRE-1.002-legacy", merged_into=self.active, status=Box.Status.INACTIVE,
            previous_global_code="LEGACY-DONOR.002",
        )
        alias = BoxLocalIdAlias.objects.create(box=donor, code="DONOR-ALIAS.002")
        before = _records(Box)
        aliases = _records(BoxLocalIdAlias)

        receipt, created = service.apply_species_code_change(**self.arguments())

        self.assertTrue(created)
        self.assertEqual(receipt.result["retained_merged_box_ids"], [donor.pk])

        self.assertEqual(next(row for row in _records(Box) if row["pk"] == donor.pk),
                         next(row for row in before if row["pk"] == donor.pk))
        self.assertEqual(_records(BoxLocalIdAlias)[:len(aliases)], aliases)
        for code in (donor.global_code, donor.previous_global_code, alias.code,
                     "AAU-BRE-1.001", "AUR-BRE-1.001"):
            self.assertEqual(resolve_box_local_id(Box.objects.filter(organization=self.a), code).pk,
                             self.active.pk)
            with self.assertRaises(Http404):
                resolve_box_local_id(Box.objects.filter(organization=self.b), code)

    def test_existing_source_alias_on_merged_donor_keeps_original_owner(self):
        donor = self.box("AAU-BRE-1.002", merged_into=self.active, status=Box.Status.INACTIVE)
        alias = BoxLocalIdAlias.objects.create(box=donor, code=self.active.global_code)
        before = _records(BoxLocalIdAlias)

        receipt, created = service.apply_species_code_change(**self.arguments())

        self.assertTrue(created)
        self.assertEqual(_records(BoxLocalIdAlias), before)
        alias.refresh_from_db()
        self.assertEqual(alias.box_id, donor.pk)
        self.assertEqual(receipt.result["retained_merged_box_ids"], [donor.pk])
        for code in (alias.code, donor.global_code, "AUR-BRE-1.001"):
            self.assertEqual(resolve_box_local_id(Box.objects.filter(organization=self.a), code).pk,
                             self.active.pk)

    def test_cross_species_incoming_merged_donor_survives_repeated_recodes(self):
        donor_strain = self.owned_strain("CAN-EXT-8", species=self.other_species)
        donor = self.box(
            "CAN-EXT-8.002-legacy", strain=donor_strain, merged_into=self.active,
            status=Box.Status.INACTIVE, previous_global_code="LEGACY-CROSS-SPECIES.002",
        )
        BoxLocalIdAlias.objects.create(box=donor, code="CROSS-SPECIES-ALIAS.002")
        # PCA-style source alias already belongs to a donor of another Species.
        BoxLocalIdAlias.objects.create(box=donor, code=self.active.global_code)
        original_donor = next(row for row in _records(Box) if row["pk"] == donor.pk)
        original_strain = next(row for row in _records(Strain) if row["pk"] == donor_strain.pk)
        original_aliases = [row for row in _records(BoxLocalIdAlias) if row["fields"]["box"] == donor.pk]

        for proposed in ("AUR", "AUI"):
            with self.subTest(proposed=proposed):
                plan = self.make_plan(proposed)
                self.assertTrue(plan["feasible"], plan["blockers"])
                self.assertNotIn(donor.pk, {row["id"] for row in plan["boxes"]})
                receipt, created = service.apply_species_code_change(**self.arguments(proposed))
                self.assertTrue(created)
                self.assertNotIn(donor.pk, {row["id"] for row in receipt.result["boxes"]})
                self.assertEqual(next(row for row in _records(Box) if row["pk"] == donor.pk), original_donor)
                self.assertEqual(next(row for row in _records(Strain) if row["pk"] == donor_strain.pk), original_strain)
                self.assertEqual(
                    [row for row in _records(BoxLocalIdAlias) if row["fields"]["box"] == donor.pk], original_aliases,
                )
                for code in (donor.global_code, donor.previous_global_code, "CROSS-SPECIES-ALIAS.002",
                             "AAU-BRE-1.001", "AUR-BRE-1.001", f"{proposed}-BRE-1.001"):
                    self.assertEqual(resolve_box_local_id(Box.objects.filter(organization=self.a), code).pk,
                                     self.active.pk)
                    with self.assertRaises(Http404):
                        resolve_box_local_id(Box.objects.filter(organization=self.b), code)

    def test_normalized_request_replay_makes_no_duplicate_writes(self):
        args = self.arguments(" aur ", request_id=uuid.uuid4().hex.upper())
        receipt, created = service.apply_species_code_change(**args)
        self.assertTrue(created)
        before = _snapshot()
        repeated, created = service.apply_species_code_change(**{
            **args, "proposed_code": "AUR", "request_id": uuid.UUID(args["request_id"]),
        })
        self.assertFalse(created)
        self.assertEqual((repeated.pk, repeated.intent, repeated.result),
                         (receipt.pk, receipt.intent, receipt.result))
        self.assertEqual(_snapshot(), before)

    def test_repeated_distinct_changes_and_original_retry_after_later_recodes(self):
        original_args = self.arguments()
        first, _ = service.apply_species_code_change(**original_args)
        for proposed in ("AUI", "AUZ"):
            receipt, created = service.apply_species_code_change(**self.arguments(proposed))
            self.assertTrue(created)
            self.assertEqual(receipt.result["new_code"], proposed)
        before = _snapshot()
        replay, created = service.apply_species_code_change(**original_args)
        self.assertFalse(created)
        self.assertEqual((replay.pk, replay.result), (first.pk, first.result))
        self.assertEqual(_snapshot(), before)
        self.active.refresh_from_db()
        self.assertEqual(self.active.global_code, "AUZ-BRE-1.001")
        self.assertEqual(SpeciesCodeChangeRequest.objects.count(), 3)
        self.assertEqual(AuditLog.objects.filter(description="Institutional species code changed").count(), 3)
        for prefix in ("AAU", "AUR", "AUI", "AUZ"):
            self.assertEqual(resolve_box_local_id(Box.objects.filter(organization=self.a),
                             prefix + "-BRE-1.001").pk, self.active.pk)

    def test_request_key_rejects_any_changed_intent(self):
        args = self.arguments()
        service.apply_species_code_change(**args)
        for change in ({"proposed_code": "AUI"}, {"profile_pk": self.other_profile.pk},
                       {"expected_plan_fingerprint": "f" * 64}):
            with self.subTest(change=change):
                self.assert_rejected({**args, **change}, code="species_code_change_request_conflict")

    def test_different_request_with_old_fingerprint_is_stale_not_a_replay(self):
        args = self.arguments()
        service.apply_species_code_change(**args)
        self.assert_rejected({**args, "request_id": uuid.uuid4()}, code="species_code_change_plan_changed")

    def test_authorization_is_fresh_even_for_cached_user_and_successful_retry(self):
        args = self.arguments()
        service.apply_species_code_change(**args)
        membership = OrganizationMembership.objects.get(user=self.admin, organization=self.a)
        changes = (
            (OrganizationMembership, membership.pk, {"role": OrganizationMembership.Role.VIEWER}),
            (OrganizationMembership, membership.pk, {"is_active": False}),
            (get_user_model(), self.admin.pk, {"is_active": False}),
            (self.a.__class__, self.a.pk, {"is_active": False}),
        )
        for model, pk, change in changes:
            with self.subTest(model=model.__name__, change=change), transaction.atomic():
                fresh = self.arguments("AUI")
                model.objects.filter(pk=pk).update(**change)
                self.assertTrue(self.admin.is_active)
                self.assert_rejected(args, PermissionDenied)
                self.assert_rejected(fresh, PermissionDenied)
                transaction.set_rollback(True)
        for actor in (None, self.tech, self.viewer, self.b_admin):
            with self.subTest(actor=actor):
                self.assert_rejected({**args, "user": actor}, PermissionDenied)

    def test_foreign_profile_is_not_found_without_writes(self):
        foreign = InstitutionSpecies.objects.create(
            organization=self.b, species=self.species, scientific_name="Aurelia aurita",
        )
        self.assert_rejected(self.arguments(profile_pk=foreign.pk), InstitutionSpeciesNotFound)
        self.assert_rejected(self.arguments(profile_pk=999999), InstitutionSpeciesNotFound)

    def test_shared_unowned_foreign_and_noncanonical_identities_are_blocked(self):
        for kind in ("shared", "unowned", "foreign", "strain_format", "box_format", "local_identity"):
            with self.subTest(kind=kind), transaction.atomic():
                if kind == "shared":
                    self.box("AAU-BRE-1.900", organization=self.b)
                elif kind in ("unowned", "foreign"):
                    strain = Strain.objects.create(
                        species=self.species, code="AAU-LEG-2", number=2, origin_code="LEG",
                        organization=self.b if kind == "foreign" else None,
                    )
                    self.box("AAU-LEG-2.001", strain=strain)
                elif kind == "strain_format":
                    strain = Strain.objects.create(species=self.species, organization=self.a, code="OLD1")
                    self.box("OLD1.001", strain=strain)
                elif kind == "box_format":
                    self.box("AAU-BRE-1.002-extra")
                else:
                    LocalStrainIdentity.objects.create(
                        strain=self.strain, species_code_assignment=OrganizationSpeciesCode.objects.get(
                            organization=self.a, species=self.other_species),
                    )
                error = self.assert_rejected(self.arguments(), code="species_code_change_blocked")
                self.assertTrue(error.details["blockers"])
                self.assertNotIn(self.b.name, str(error.details))
                transaction.set_rollback(True)

    def test_reviewed_identity_is_protected(self):
        from .models import Species

        species = Species.objects.create(scientific_name="Clytia languida")
        OrganizationSpeciesCode.objects.create(organization=self.a, species=species, code="CLA")
        profile = InstitutionSpecies.objects.create(
            organization=self.a, species=species, scientific_name=species.scientific_name,
        )
        strain = self.owned_strain("CLA-JKA-1", species=species)
        self.box("CLA-JKA-1.001", strain=strain)
        fingerprint = self.make_plan("CLG", profile_pk=profile.pk)["plan_fingerprint"]
        error = self.assert_rejected(self.arguments(
            "CLG", profile_pk=profile.pk, expected_plan_fingerprint=fingerprint,
        ), code="species_code_change_blocked")
        self.assertIn("reviewed_identity_protected", {b["code"] for b in error.details["blockers"]})

    def test_collision_revalidation_rejects_stale_and_current_blocked_plans(self):
        for kind in ("assignment", "current", "alias_same", "alias_foreign", "previous", "namespace"):
            with self.subTest(kind=kind), transaction.atomic():
                args = self.arguments()
                if kind == "namespace":
                    BoxCodeNamespace.objects.create(namespace="AUR-BRE-1", high_water=40)
                elif kind == "assignment":
                    OrganizationSpeciesCode.objects.filter(
                        organization=self.a, species=self.other_species,
                    ).update(code="AUR")
                else:
                    organization = self.b if kind in ("current", "alias_foreign") else self.a
                    holder = self.owned_strain("CAN-EXT-8", species=self.other_species, organization=organization)
                    box = self.box(
                        "AUR-BRE-1.001" if kind == "current" else "CAN-EXT-8.001",
                        strain=holder, organization=organization,
                        **({"previous_global_code": "AUR-BRE-1.001"} if kind == "previous" else {}),
                    )
                    if kind.startswith("alias"):
                        BoxLocalIdAlias.objects.create(box=box, code="AUR-BRE-1.001")
                self.assert_rejected(args, code="species_code_change_plan_changed")
                error = self.assert_rejected(self.arguments(), code="species_code_change_blocked")
                self.assertTrue(error.details["blockers"])
                self.assertNotIn(self.b.name, str(error.details))
                for blocker in error.details["blockers"]:
                    if blocker.get("scope") == "outside_institution":
                        self.assertNotIn("conflicting_box_id", blocker)
                        self.assertNotIn("conflicting_strain_id", blocker)
                transaction.set_rollback(True)

    def test_identity_changes_invalidate_confirmed_fingerprint(self):
        for kind in ("box", "strain", "alias", "revision", "local_code", "identity", "floor"):
            with self.subTest(kind=kind), transaction.atomic():
                args = self.arguments()
                if kind == "box":
                    self.box("AAU-BRE-1.002")
                elif kind == "strain":
                    self.owned_strain("AAU-FRA-2")
                elif kind == "alias":
                    BoxLocalIdAlias.objects.create(box=self.active, code="OLDER.001")
                elif kind == "revision":
                    InstitutionSpecies.objects.filter(pk=self.profile.pk).update(revision=self.profile.revision + 1)
                elif kind == "local_code":
                    Box.objects.filter(pk=self.active.pk).update(local_code="Revised")
                elif kind == "identity":
                    LocalStrainIdentity.objects.create(strain=self.strain, species_code_assignment=self.aaa)
                else:
                    BoxCodeNamespace.objects.filter(namespace=self.strain.code).update(high_water=99)
                self.assert_rejected(args, code="species_code_change_plan_changed")
                transaction.set_rollback(True)

    def raw_foreign_source_alias(self, box=None):
        if box is None:
            holder = self.owned_strain("CAN-EXT-8", species=self.other_species, organization=self.b)
            box = self.box("CAN-EXT-8.001", strain=holder, organization=self.b)
        # Bypass Python's namespace reconciliation, not SQL identity guards.
        table = connection.ops.quote_name(BoxLocalIdAlias._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {table} (code, box_id, created_at) VALUES (%s, %s, %s)",
                ["AAU-BRE-1.999", box.pk, timezone.now()],
            )
        return box

    def test_raw_alias_on_foreign_box_invalidates_effective_source_floor(self):
        holder = self.owned_strain("CAN-EXT-8", species=self.other_species, organization=self.b)
        foreign = self.box("CAN-EXT-8.001", strain=holder, organization=self.b)
        args = self.arguments()
        original = self.make_plan()
        stored = BoxCodeNamespace.objects.get(namespace=self.strain.code).high_water
        self.assertNotIn(foreign.pk, {item["id"] for item in original["boxes"]})
        self.raw_foreign_source_alias(foreign)
        self.assertEqual(BoxCodeNamespace.objects.get(namespace=self.strain.code).high_water, stored)
        fresh = self.make_plan()
        self.assertTrue(fresh["feasible"])
        self.assertEqual(fresh["boxes"], original["boxes"])
        self.assertEqual(fresh["strains"], original["strains"])
        self.assert_rejected(args, code="species_code_change_plan_changed")
        receipt, created = service.apply_species_code_change(**self.arguments())
        self.assertTrue(created)
        self.assertEqual(receipt.result["new_code"], "AUR")
        self.assertEqual(BoxCodeNamespace.objects.get(namespace="AAU-BRE-1").high_water, 999)
        self.assertEqual(BoxCodeNamespace.objects.get(namespace="AUR-BRE-1").high_water, 999)
        self.assertEqual(BoxLocalIdAlias.objects.get(code="AAU-BRE-1.999").box_id, foreign.pk)

    def test_source_namespace_absent_then_created_invalidates_fingerprint(self):
        BoxCodeNamespace.objects.filter(namespace=self.strain.code).delete()
        args = self.arguments()
        # The effective floor remains 1; existence of the stored row changes.
        BoxCodeNamespace.objects.create(namespace=self.strain.code, high_water=1)
        self.assertTrue(self.make_plan()["feasible"])
        self.assert_rejected(args, code="species_code_change_plan_changed")
        receipt, created = service.apply_species_code_change(**self.arguments())
        self.assertTrue(created)
        self.assertEqual(receipt.result["new_code"], "AUR")
        self.assertEqual(BoxCodeNamespace.objects.get(namespace="AUR-BRE-1").high_water, 1)

    def test_source_namespace_reconciliation_failure_rolls_back_written_floor(self):
        self.raw_foreign_source_alias()
        args = self.arguments()
        before = _snapshot()
        original = BoxCodeNamespace.save
        reached = []

        def save_then_fail(instance, *positional, **keywords):
            result = original(instance, *positional, **keywords)
            if instance.namespace == "AAU-BRE-1" and instance.high_water == 999:
                self.assertEqual(BoxCodeNamespace.objects.get(pk=instance.pk).high_water, 999)
                reached.append(instance.pk)
                raise DeliberateFailure("source namespace written")
            return result

        with patch.object(BoxCodeNamespace, "save", new=save_then_fail):
            with self.assertRaisesRegex(DeliberateFailure, "source namespace written"):
                service.apply_species_code_change(**args)
        self.assertEqual(len(reached), 1)
        self.assertEqual(_snapshot(), before)
        receipt, created = service.apply_species_code_change(**args)
        self.assertTrue(created)
        self.assertEqual(receipt.result["new_code"], "AUR")
        self.assertEqual(BoxCodeNamespace.objects.get(namespace="AUR-BRE-1").high_water, 999)

    def test_invalid_inputs_never_write(self):
        args = self.arguments()
        for field, values in (
            ("profile_pk", (None, True, 0, -1, "1")),
            ("proposed_code", (None, "", "AU", "A1U", "AURR")),
            ("expected_plan_fingerprint", (None, "", "A" * 64, "a" * 63)),
            ("request_id", (None, True, 123, "not-a-uuid")),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assert_rejected({**args, field: value}, InstitutionSpeciesInvalid)

    def test_every_write_stage_rolls_back_all_models_and_allows_retry(self):
        self.rich_fixture()
        args = self.arguments()
        stages = (
            ("namespace", BoxCodeNamespace, {"namespace": "AUR-BRE-1", "high_water": 70}),
            ("alias", BoxLocalIdAlias, {"code": "AAU-BRE-1.001"}),
            ("assignment", OrganizationSpeciesCode, {"code": "AUR"}),
            ("strain", Strain, {"code": "AUR-BRE-1"}),
            ("box", Box, {"global_code": "AUR-BRE-1.001"}),
            ("profile", InstitutionSpecies, {"pk": self.profile.pk}),
            ("audit", AuditLog, {"description": "Institutional species code changed"}),
            ("receipt", SpeciesCodeChangeRequest, {"request_id": args["request_id"]}),
        )
        for stage, model, lookup in stages:
            with self.subTest(stage=stage):
                before = _snapshot()
                original = model.save
                reached = []

                def save_then_fail(instance, *positional, **keywords):
                    result = original(instance, *positional, **keywords)
                    if all(getattr(instance, key) == value for key, value in lookup.items()):
                        self.assertTrue(model.objects.filter(**lookup).exists())
                        reached.append(instance.pk)
                        raise DeliberateFailure(stage)
                    return result

                with patch.object(model, "save", new=save_then_fail):
                    with self.assertRaisesRegex(DeliberateFailure, stage):
                        service.apply_species_code_change(**args)
                self.assertEqual(len(reached), 1)
                self.assertEqual(_snapshot(), before, stage)
        receipt, created = service.apply_species_code_change(**args)
        self.assertTrue(created)
        self.assertEqual(receipt.result["new_code"], "AUR")
        self.assertEqual(SpeciesCodeChangeRequest.objects.count(), 1)

    def test_no_apply_route_and_existing_http_code_paths_stay_immutable(self):
        self.client.force_login(self.admin)
        preference = UserPreference.objects.get(user=self.admin)
        session = self.client.session
        session["interface_language"] = preference.interface_language
        signing_patch = patch("django.core.signing.TimestampSigner.timestamp", return_value=TimestampSigner().timestamp())
        signing_patch.start()
        self.addCleanup(signing_patch.stop)
        session.save()
        # Middleware saves the language on every request; keep session expiry
        # deterministic so snapshots still detect any business-model write.
        expiry = session.get_model_class().objects.get(session_key=session.session_key).expire_date
        expiry_patch = patch("django.contrib.sessions.backends.base.SessionBase.get_expiry_date", return_value=expiry)
        expiry_patch.start()
        self.addCleanup(expiry_patch.stop)
        args = self.arguments()
        payload = {"proposed_code": "AUR", "code": "AUR", "species_code": "AUR",
                   "expected_plan_fingerprint": args["expected_plan_fingerprint"],
                   "request_id": str(args["request_id"])}
        routes = []

        def collect(patterns, prefix=""):
            for pattern in patterns:
                route = prefix + str(pattern.pattern)
                if hasattr(pattern, "url_patterns"):
                    collect(pattern.url_patterns, route)
                else:
                    routes.append(route)

        collect(get_resolver().url_patterns)
        change_routes = [route for route in routes if "code-change" in route]
        self.assertEqual(len(change_routes), 1, change_routes)
        self.assertIn("code-change-preview/", change_routes[0])
        preview = reverse("api_admin_institution_species_code_change_preview", args=[self.profile.pk])
        before = _snapshot()
        response = self.client.get(preview, {"proposed_code": "AUR"}, **self.headers(self.a))
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()["advisory_only"], response.json()["mutation_available"]), (True, False))
        self.assertEqual(_snapshot(), before)
        endpoints = (
            (preview, (405, 405)),
            (reverse("api_admin_species_code", args=[self.species.pk]), (410, 405)),
            (reverse("api_taxonomy_species_codes_detail", args=[self.aaa.pk]), (405, 405)),
            (reverse("api_admin_institution_species_detail", args=[self.profile.pk]), (405, 400)),
            (f"/api/admin/institution-species/{self.profile.pk}/code-change/", (404, 404)),
            (f"/api/admin/institution-species/{self.profile.pk}/code-change-apply/", (404, 404)),
        )
        with patch.object(service, "apply_species_code_change", wraps=service.apply_species_code_change) as apply:
            for endpoint, statuses in endpoints:
                for method, expected in zip(("post", "patch"), statuses):
                    with self.subTest(endpoint=endpoint, method=method):
                        before = _snapshot()
                        response = getattr(self.client, method)(
                            endpoint, json.dumps({"code": "AUR"} if "/taxonomy/species-codes/" in endpoint else payload),
                            content_type="application/json", **self.headers(self.a),
                        )
                        self.assertEqual(response.status_code, expected, response.content)
                        self.assertEqual(_snapshot(), before)
            apply.assert_not_called()


class SpeciesCodeChangeTransactionBase(SpeciesReviewTransactionTestCase):
    """Shared committed fixtures and guarded cleanup, without inherited tests."""

    def setUp(self):
        self.a = Organization.objects.create(name="Recode concurrency")
        self.admin = get_user_model().objects.create_user(
                    username="recode-admin", email="recode-admin@example.org",
                )
        OrganizationMembership.objects.create(
            organization=self.a, user=self.admin, role=OrganizationMembership.Role.ADMIN,
        )
        self.species = Species.objects.create(scientific_name="Aurelia aurita")
        self.aaa = OrganizationSpeciesCode.objects.create(organization=self.a, species=self.species, code="AAU")
        self.profile = InstitutionSpecies.objects.create(
            organization=self.a, species=self.species, scientific_name=self.species.scientific_name,
        )
        self.strain = Strain.objects.create(
            organization=self.a, species=self.species, code="AAU-BRE-1", number=1, origin_code="BRE",
        )
        self.active = Box.objects.create(
            organization=self.a, strain=self.strain, global_code="AAU-BRE-1.001", box_number="001",
        )
        self.args = {
            "user": self.admin, "organization": self.a, "profile_pk": self.profile.pk,
            "proposed_code": "AUR", "request_id": uuid.uuid4(),
            "expected_plan_fingerprint": plan_species_code_change(
                user=self.admin, organization=self.a, profile_pk=self.profile.pk, proposed_code="AUR",
            )["plan_fingerprint"],
        }

    def _fixture_teardown(self):
        # The inherited PostgreSQL cleanup verifies data_directory, then uses
        # transaction-local session_replication_role=replica. That suppresses
        # 0023's ordinary alias DELETE/TRUNCATE triggers as well as temperature
        # guards; commit/rollback restores their normal execution automatically.
        # SQLite needs explicit removal/reinstallation of its 0023 guards.
        if connection.vendor == "postgresql":
            return super()._fixture_teardown()
        guards = import_module("apps.cultures.migrations.0023_box_local_id_alias")
        with connection.schema_editor() as editor:
            guards.remove_guards(None, editor)
        try:
            super()._fixture_teardown()
        finally:
            with connection.schema_editor() as editor:
                guards.install_guards(None, editor)

    def worker(self, action):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '8000ms'")
                cursor.execute("SET lock_timeout = '3000ms'")
            return action()
        finally:
            connection.close()

    def apply(self):
        try:
            receipt, created = service.apply_species_code_change(**self.args)
            return receipt.pk, created, receipt.result
        except InstitutionSpeciesConflict as error:
            return error.code

@skipUnless(connection.vendor == "sqlite", "Exercises actual SQLite migration rollback.")
class SpeciesCodeChangeMigrationTests(SpeciesCodeChangeTransactionBase):
    old = ("taxonomy", "0013_institutionstraincreationrequest")
    new = ("taxonomy", "0014_speciescodechangerequest")
    # Later additive migrations are unapplied with 0014 and must be reapplied.
    latest = ("taxonomy", "0015_provenance_country_acquisition_types")

    def setUp(self):
        super().setUp()
        leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(leaves))

    def test_actual_reverse_drops_empty_table_but_refuses_successful_receipt_loss(self):
        table = SpeciesCodeChangeRequest._meta.db_table
        before_empty_reverse = _snapshot()
        self.assertEqual(SpeciesCodeChangeRequest.objects.count(), 0)
        MigrationExecutor(connection).migrate([self.old])
        self.assertNotIn(table, connection.introspection.table_names())
        self.assertNotIn(self.new, MigrationExecutor(connection).loader.applied_migrations)
        self.active.refresh_from_db()
        self.strain.refresh_from_db()
        self.assertEqual((self.active.global_code, self.strain.code), ("AAU-BRE-1.001", "AAU-BRE-1"))

        MigrationExecutor(connection).migrate([self.latest])
        self.assertIn(table, connection.introspection.table_names())
        self.assertEqual(_snapshot(), before_empty_reverse)
        receipt, created = service.apply_species_code_change(**self.args)
        self.assertTrue(created)
        before_refusal = _snapshot()
        # The later additive migration holds no country here and is unapplied
        # first; the refusal under test belongs to 0014.
        MigrationExecutor(connection).migrate([self.new])
        with self.assertRaisesRegex(IrreversibleError, "receipts must be retained"):
            MigrationExecutor(connection).migrate([self.old])
        self.assertIn(table, connection.introspection.table_names())
        self.assertIn(self.new, MigrationExecutor(connection).loader.applied_migrations)
        MigrationExecutor(connection).migrate([self.latest])
        self.assertEqual(_snapshot(), before_refusal)
        receipt.refresh_from_db()
        self.active.refresh_from_db()
        self.strain.refresh_from_db()
        self.aaa.refresh_from_db()
        self.assertEqual((self.active.global_code, self.strain.code, self.aaa.code),
                         ("AUR-BRE-1.001", "AUR-BRE-1", "AUR"))
        self.assertEqual(receipt.result["new_code"], "AUR")
        self.assertEqual(BoxLocalIdAlias.objects.get(code="AAU-BRE-1.001").box_id, self.active.pk)
        replay, created = service.apply_species_code_change(**self.args)
        self.assertFalse(created)
        self.assertEqual(replay.pk, receipt.pk)
        self.assertEqual(_snapshot(), before_refusal)


@skipUnless(disposable_postgres_declared(), "Requires a declared, verified disposable PostgreSQL cluster.")
class SpeciesCodeChangePostgresTests(SpeciesCodeChangeTransactionBase):
    """Real connections and commits; SQLite cannot establish these guarantees."""

    def test_duplicate_submit_same_key_commits_once_and_replays(self):
        barrier = Barrier(2)

        def submit():
            barrier.wait(timeout=10)
            return self.apply()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.worker, submit) for _ in range(2)]
            results = [future.result(timeout=15) for future in futures]
        self.assertTrue(all(isinstance(result, tuple) for result in results), results)
        self.assertEqual(sorted(result[1] for result in results), [False, True])
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(results[0][2], results[1][2])
        self.assertEqual(SpeciesCodeChangeRequest.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(description="Institutional species code changed").count(), 1)
        self.assertEqual(BoxLocalIdAlias.objects.count(), 1)

    def test_intervening_raw_collision_invalidates_confirmed_plan(self):
        def collide():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE taxonomy_strain SET code = %s WHERE id = %s",
                    ["AUR-BRE-1", self.strain.pk],
                )

        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(self.worker, collide).result(timeout=15)
        before = _snapshot()
        self.assertEqual(self.apply(), "species_code_change_plan_changed")
        self.assertEqual(_snapshot(), before)

    def test_raw_other_species_insert_after_authorization_invalidates_plan(self):
        competitor_species = Species.objects.create(scientific_name="Cassiopea andromeda")
        competitor_org = Organization.objects.create(name="Raw insert competitor")
        authorized, release = Event(), Event()
        original = service._fence_identity_writers

        def pause_before_fences():
            with connection.cursor() as cursor:
                cursor.execute("SHOW transaction_isolation")
                self.assertEqual(cursor.fetchone()[0], "read committed")
            authorized.set()
            if not release.wait(timeout=10):
                raise AssertionError("Authorized recode was not released")
            original()

        with patch.object(service, "_fence_identity_writers", side_effect=pause_before_fences), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.worker, self.apply)
            try:
                self.assertTrue(authorized.wait(timeout=10))
                # Strain alone does not take the Box registry gate. Use another
                # organization so its FK check cannot wait on the worker's
                # authorized organization row. Codes still collide globally.
                with transaction.atomic():
                    competitor = Strain.objects.create(
                        organization=competitor_org, species=competitor_species,
                        code="AUR-BRE-1", number=1, origin_code="BRE",
                    )
                before_rejection = _snapshot()
            finally:
                release.set()
            self.assertEqual(future.result(timeout=15), "species_code_change_plan_changed")
        self.assertEqual(_snapshot(), before_rejection)
        self.assertEqual(Strain.objects.get(pk=competitor.pk).code, "AUR-BRE-1")
        self.assertEqual(BoxLocalIdAlias.objects.count(), 0)
        self.assertEqual(SpeciesCodeChangeRequest.objects.count(), 0)
        self.assertEqual(AuditLog.objects.filter(description="Institutional species code changed").count(), 0)

    def test_existing_raw_writer_causes_busy_without_partial_writes(self):
        before = _snapshot()
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("UPDATE taxonomy_strain SET notes = notes WHERE id = %s", [self.strain.pk])
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertEqual(pool.submit(self.worker, self.apply).result(timeout=15), "species_code_change_busy")
        self.assertEqual(_snapshot(), before)

    def test_namespace_row_lock_causes_busy_instead_of_waiting(self):
        before = _snapshot()
        with transaction.atomic():
            BoxCodeNamespace.objects.select_for_update().get(namespace="AAU-BRE-1")
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertEqual(pool.submit(self.worker, self.apply).result(timeout=15), "species_code_change_busy")
        self.assertEqual(_snapshot(), before)

    def test_fence_excludes_raw_writer_until_recode_commits(self):
        fenced, release = Event(), Event()
        original = service._fence_identity_writers

        def pause():
            original()
            fenced.set()
            if not release.wait(timeout=10):
                raise AssertionError("Recode fence was not released")

        with patch.object(service, "_fence_identity_writers", side_effect=pause), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.worker, self.apply)
            try:
                self.assertTrue(fenced.wait(timeout=10))
                with self.assertRaises(DatabaseError) as caught, transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '200ms'")
                        cursor.execute("UPDATE taxonomy_strain SET notes = %s WHERE id = %s", ["Intruder", self.strain.pk])
                cause = caught.exception.__cause__
                self.assertEqual(getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None), "55P03")
            finally:
                release.set()
            result = future.result(timeout=15)
        self.assertIsInstance(result, tuple)
        self.assertTrue(result[1])
        self.strain.refresh_from_db()
        self.assertEqual((self.strain.code, self.strain.notes), ("AUR-BRE-1", ""))

    def test_serializable_snapshot_is_rejected_without_writes(self):
        before = _snapshot()
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                cursor.execute("SELECT count(*) FROM taxonomy_strain")
            with self.assertRaises(InstitutionSpeciesConflict) as caught:
                service.apply_species_code_change(**self.args)
            self.assertEqual(caught.exception.code, "species_code_change_isolation_unsupported")
        self.assertEqual(_snapshot(), before)

    def test_empty_receipt_rollback_fence_excludes_new_submit(self):
        migration = import_module("apps.taxonomy.migrations.0014_speciescodechangerequest")
        before = _snapshot()
        with transaction.atomic():
            with connection.schema_editor(atomic=False) as editor:
                migration.refuse_receipt_loss(apps, editor)
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertEqual(pool.submit(self.worker, self.apply).result(timeout=15), "species_code_change_busy")
        self.assertEqual(_snapshot(), before)
        result = self.apply()
        self.assertIsInstance(result, tuple)
        self.assertTrue(result[1])

    def test_receipt_rollback_guard_excludes_uncommitted_success(self):
        saved, release = Event(), Event()
        original = SpeciesCodeChangeRequest.save

        def pause(instance, *args, **kwargs):
            result = original(instance, *args, **kwargs)
            saved.set()
            if not release.wait(timeout=10):
                raise AssertionError("Receipt writer was not released")
            return result

        migration = import_module("apps.taxonomy.migrations.0014_speciescodechangerequest")
        with patch.object(SpeciesCodeChangeRequest, "save", new=pause), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.worker, self.apply)
            try:
                self.assertTrue(saved.wait(timeout=10))
                with self.assertRaises(DatabaseError) as caught, transaction.atomic():
                    with connection.schema_editor(atomic=False) as editor:
                        migration.refuse_receipt_loss(apps, editor)
                cause = caught.exception.__cause__
                self.assertEqual(getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None), "55P03")
            finally:
                release.set()
            result = future.result(timeout=15)
        self.assertIsInstance(result, tuple)
        self.assertTrue(result[1])
        self.assertEqual(SpeciesCodeChangeRequest.objects.count(), 1)
        with self.assertRaises(IrreversibleError), transaction.atomic():
            with connection.schema_editor(atomic=False) as editor:
                migration.refuse_receipt_loss(apps, editor)
