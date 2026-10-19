"""Synthetic canonical/evidence contracts; run only on disposable PostgreSQL."""

from dataclasses import asdict
from datetime import date, timedelta
from unittest import skipUnless

from django.contrib.admin import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Prefetch
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.biological_timeline import biological_timeline
from apps.cultures.models import Box
from apps.cultures.polyp_state import current_state_prefetches, resolve_current_polyp_state
from apps.cultures.scientific_resolution import resolve_week_observation_state
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .admin import BiologicalMeasurementAdmin
from .csv_import import read_csv
from .import_apply import MeasurementImportApplyError, apply_measurement_import
from .import_planner import _baseline, build_measurement_import_plan
from .models import BiologicalMeasurement, MeasurementImportReceipt
from .services import (
    MeasurementWeekConflict, MeasurementWriteForbidden, MeasurementWriteInvalid,
    correct_biological_measurement, create_biological_measurement, get_measurement_editability,
)


@skipUnless(connection.vendor == "postgresql", "Requires disposable PostgreSQL")
class CanonicalMeasurementEvidenceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Synthetic canonical laboratory")
        self.other_org = Organization.objects.create(name="Foreign canonical laboratory")
        self.actor = get_user_model().objects.create_user(
            username="canonical-admin", email="canonical-evidence-admin@example.test",
        )
        OrganizationMembership.objects.create(user=self.actor, organization=self.org, role="admin")
        species = Species.objects.create(scientific_name="Aurelia canonical", genus_species_code="ACN")
        self.strain = Strain.objects.create(organization=self.org, species=species, code="ACN-QA-1")
        self.box = Box.objects.create(
            organization=self.org, strain=self.strain, global_code="ACN-QA-1.001", box_number="001",
        )
        self.source_box = Box.objects.create(
            organization=self.org, strain=self.strain, global_code="ACN-QA-1.002", box_number="002",
            status=Box.Status.INACTIVE,
        )
        self.foreign_box = Box.objects.create(
            organization=self.other_org, strain=self.strain, global_code="ACN-QA-1.003", box_number="003",
        )
        self.day = date(2026, 6, 1)

    def pair(self, *, move=True):
        canonical = BiologicalMeasurement.objects.create(
            box=self.source_box, measured_on=self.day, polyp_count=0, ephyrae_count=0,
            strobila_count=None, notes="Original JKA evidence",
        )
        duplicate = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=self.day + timedelta(days=2), polyp_count=900, ephyrae_count=99,
            duplicate_of=canonical, notes="Original CHO evidence",
        )
        if move:
            BiologicalMeasurement.objects.filter(pk=canonical.pk).update(
                box=self.box, environment_source_box=self.source_box,
            )
            canonical.refresh_from_db()
        return canonical, duplicate

    def prepare(self, *, count=0):
        source = (
            "box_reference,measured_on,polyp_count,ephyrae_count\n"
            f"{self.box.global_code},{self.day.isoformat()},{count},0\n"
        ).encode()
        mapping = {field: field for field in ("box_reference", "measured_on", "polyp_count", "ephyrae_count")}
        preparation = read_csv(source, source_id="diagnostic-upload", mapping=mapping, confirmed=True)
        return source, mapping, preparation

    def plan(self, preparation):
        return build_measurement_import_plan(preparation, organization=self.org, actor=self.actor)

    def apply(self, source, mapping, plan):
        return apply_measurement_import(
            source, mapping, ",", plan.preparation_fingerprint, plan.fingerprint, self.org, self.actor,
        )

    def test_default_base_and_reverse_managers_retain_evidence(self):
        canonical, duplicate = self.pair()
        for manager in (BiologicalMeasurement.objects, BiologicalMeasurement._default_manager,
                        BiologicalMeasurement._base_manager, self.box.biological_measurements):
            self.assertEqual(set(manager.values_list("pk", flat=True)), {canonical.pk, duplicate.pk})
        self.assertEqual(list(self.box.biological_measurements.canonical().values_list("pk", flat=True)), [canonical.pk])
        for name in ("duplicate_of", "environment_source_box"):
            self.assertFalse(BiologicalMeasurement._meta.get_field(name).editable)

    def test_constraint_still_rejects_two_canonical_rows_in_one_week(self):
        canonical, duplicate = self.pair()
        with self.assertRaises(IntegrityError), transaction.atomic():
            BiologicalMeasurement.objects.create(box=self.box, measured_on=self.day + timedelta(days=1))
        self.assertEqual(BiologicalMeasurement.objects.count(), 2)
        self.assertEqual(duplicate.duplicate_of_id, canonical.pk)

    def test_database_rejects_self_reference_even_through_update(self):
        canonical, _ = self.pair()
        with self.assertRaises(IntegrityError), transaction.atomic():
            BiologicalMeasurement.objects.filter(pk=canonical.pk).update(duplicate_of_id=canonical.pk)

    def test_canonical_target_and_environment_source_are_protected(self):
        canonical, _ = self.pair()
        with self.assertRaises(ProtectedError):
            canonical.delete()
        with self.assertRaises(ProtectedError):
            self.source_box.delete()

    def test_duplicate_instance_delete_checks_stored_state_not_mutable_instance(self):
        _, duplicate = self.pair()
        before = list(BiologicalMeasurement.objects.order_by("pk").values())
        duplicate.duplicate_of = None
        with self.assertRaises(ProtectedError):
            duplicate.delete()
        self.assertEqual(list(BiologicalMeasurement.objects.order_by("pk").values()), before)

    def test_duplicate_queryset_delete_rejects_entire_mixed_selection(self):
        _, duplicate = self.pair()
        ordinary = BiologicalMeasurement.objects.create(box=self.box, measured_on=self.day + timedelta(days=7))
        before = list(BiologicalMeasurement.objects.order_by("pk").values())
        for ids in ([duplicate.pk], [ordinary.pk, duplicate.pk]):
            with self.subTest(ids=ids), self.assertRaises(ProtectedError):
                BiologicalMeasurement.objects.filter(pk__in=ids).distinct().delete()
            self.assertEqual(list(BiologicalMeasurement.objects.order_by("pk").values()), before)

    def admin_context(self):
        request = RequestFactory().post("/admin/measurements/biologicalmeasurement/")
        request.user = get_user_model().objects.create_superuser(
            username="canonical-evidence-superuser", email="canonical-evidence-superuser@example.test",
        )
        return BiologicalMeasurementAdmin(BiologicalMeasurement, AdminSite()), request

    def test_admin_individual_delete_is_forbidden_and_model_delete_stays_guarded(self):
        canonical, duplicate = self.pair()
        model_admin, request = self.admin_context()
        self.assertFalse(model_admin.has_delete_permission(request, duplicate))
        self.assertTrue(model_admin.has_delete_permission(request, canonical))
        before = list(BiologicalMeasurement.objects.order_by("pk").values())
        with self.assertRaises(ProtectedError):
            model_admin.delete_model(request, duplicate)
        self.assertEqual(list(BiologicalMeasurement.objects.order_by("pk").values()), before)

    def test_admin_bulk_delete_rejects_any_duplicate_without_partial_deletion(self):
        _, duplicate = self.pair()
        ordinary = BiologicalMeasurement.objects.create(box=self.box, measured_on=self.day + timedelta(days=7))
        model_admin, request = self.admin_context()
        before = list(BiologicalMeasurement.objects.order_by("pk").values())
        with self.assertRaises(ProtectedError):
            model_admin.delete_queryset(request, BiologicalMeasurement.objects.filter(pk__in=[ordinary.pk, duplicate.pk]))
        self.assertEqual(list(BiologicalMeasurement.objects.order_by("pk").values()), before)

    def test_unreferenced_canonical_deletion_keeps_existing_model_and_admin_behavior(self):
        ordinary = BiologicalMeasurement.objects.create(box=self.box, measured_on=self.day)
        ordinary.delete()
        self.assertFalse(BiologicalMeasurement.objects.exists())
        ordinary = BiologicalMeasurement.objects.create(box=self.box, measured_on=self.day)
        model_admin, request = self.admin_context()
        self.assertTrue(model_admin.has_delete_permission(request, ordinary))
        model_admin.delete_queryset(request, BiologicalMeasurement.objects.filter(pk=ordinary.pk))
        self.assertFalse(BiologicalMeasurement.objects.exists())

    def test_duplicate_ordinary_save_rejects_noop_edits_and_flag_clearing(self):
        _, duplicate = self.pair()
        before = list(BiologicalMeasurement.objects.order_by("pk").values())
        self.box.refresh_from_db()
        revision = self.box.polyp_state_revision
        for change in ("none", "counts", "canonicality"):
            with self.subTest(change=change):
                duplicate.refresh_from_db()
                if change == "counts":
                    duplicate.polyp_count = 1
                elif change == "canonicality":
                    duplicate.duplicate_of = None
                with self.assertRaises(ValidationError) as caught:
                    duplicate.save()
                self.assertEqual(caught.exception.code, "measurement_duplicate_read_only")
                self.assertEqual(list(BiologicalMeasurement.objects.order_by("pk").values()), before)
                self.box.refresh_from_db()
                self.assertEqual(self.box.polyp_state_revision, revision)

    def test_explicit_pk_instance_cannot_overwrite_duplicate_evidence(self):
        _, duplicate = self.pair()
        before = BiologicalMeasurement.objects.filter(pk=duplicate.pk).values().get()
        replacement = BiologicalMeasurement(
            pk=duplicate.pk, box=self.box, measured_on=self.day, polyp_count=1,
        )
        with self.assertRaises(ValidationError):
            replacement.save()
        self.assertEqual(BiologicalMeasurement.objects.filter(pk=duplicate.pk).values().get(), before)

    def test_stale_canonical_instance_cannot_save_after_reviewed_reclassification(self):
        canonical, _ = self.pair(move=False)
        stale = BiologicalMeasurement.objects.create(box=self.box, measured_on=self.day)
        BiologicalMeasurement.objects.filter(pk=stale.pk).update(duplicate_of=canonical)
        before = BiologicalMeasurement.objects.filter(pk=stale.pk).values().get()
        stale.notes = "Must not overwrite evidence"
        with self.assertRaises(ValidationError):
            stale.save(update_fields=["notes"])
        self.assertEqual(BiologicalMeasurement.objects.filter(pk=stale.pk).values().get(), before)

    def test_relation_validation_rejects_foreign_sources_and_invalid_targets(self):
        canonical, duplicate = self.pair()
        row = BiologicalMeasurement(box=self.box, measured_on=self.day, environment_source_box=self.foreign_box)
        with self.assertRaises(ValidationError):
            row.clean()
        row.environment_source_box = None
        row.duplicate_of = duplicate
        with self.assertRaises(ValidationError):
            row.clean()
        row.duplicate_of = canonical
        row.measured_on += timedelta(days=7)
        with self.assertRaises(ValidationError):
            row.clean()
        foreign = BiologicalMeasurement.objects.create(box=self.foreign_box, measured_on=self.day)
        row.measured_on = self.day
        row.duplicate_of = foreign
        with self.assertRaises(ValidationError):
            row.clean()

    def test_duplicate_cannot_be_corrected_even_by_admin(self):
        _, duplicate = self.pair()
        before = list(BiologicalMeasurement.objects.order_by("pk").values())
        self.box.refresh_from_db()
        revision = self.box.polyp_state_revision
        editability = get_measurement_editability(user=self.actor, measurement=duplicate)
        self.assertFalse(editability["can_edit"])
        self.assertEqual(editability["edit_restriction"], "measurement_duplicate_read_only")
        with self.assertRaises(MeasurementWriteForbidden) as caught:
            correct_biological_measurement(
                box_id=self.box.pk, measurement_id=duplicate.pk, organization_ids=[self.org.pk],
                user=self.actor, data={"polyp_count": 1},
            )
        self.assertEqual(caught.exception.error_code, "measurement_duplicate_read_only")
        self.assertEqual(list(BiologicalMeasurement.objects.order_by("pk").values()), before)
        self.box.refresh_from_db()
        self.assertEqual(self.box.polyp_state_revision, revision)
        self.assertFalse(AuditLog.objects.exists())

    def test_canonical_value_correction_preserves_pair_and_environment_but_not_week_move(self):
        canonical, duplicate = self.pair()
        corrected = correct_biological_measurement(
            box_id=self.box.pk, measurement_id=canonical.pk, organization_ids=[self.org.pk],
            user=self.actor, data={"polyp_count": 1, "duplicate_of": duplicate.pk, "environment_source_box": None},
        )
        self.assertIsNone(corrected.duplicate_of_id)
        self.assertEqual(corrected.environment_source_box_id, self.source_box.pk)
        with self.assertRaises(MeasurementWriteInvalid):
            correct_biological_measurement(
                box_id=self.box.pk, measurement_id=canonical.pk, organization_ids=[self.org.pk],
                user=self.actor, data={"measured_on": self.day + timedelta(days=7)},
            )
        duplicate.refresh_from_db()
        self.assertEqual(duplicate.polyp_count, 900)

    def test_create_conflict_returns_canonical_not_duplicate_id(self):
        canonical, _ = self.pair()
        with self.assertRaises(MeasurementWeekConflict) as caught:
            create_biological_measurement(
                box_id=self.box.pk, organization_ids=[self.org.pk], user=self.actor,
                data={"measured_on": self.day, "polyp_count": 0, "ephyrae_count": 0},
            )
        self.assertEqual(caught.exception.measurement_id, canonical.pk)

    def test_state_filters_database_and_supplied_prefetches_without_losing_zero(self):
        canonical, duplicate = self.pair()
        self.assertEqual(resolve_current_polyp_state(self.box)["polyp_count"], 0)
        prefetched = Box.objects.prefetch_related(*current_state_prefetches()).get(pk=self.box.pk)
        with self.assertNumQueries(0):
            self.assertEqual(resolve_current_polyp_state(prefetched)["source"]["id"], canonical.pk)
        prefetched.polyp_state_measurements = [duplicate, canonical]
        with self.assertNumQueries(0):
            self.assertEqual(resolve_current_polyp_state(prefetched)["source"]["id"], canonical.pk)
        prefetched.polyp_state_measurements = [duplicate]
        self.assertEqual(resolve_current_polyp_state(prefetched)["source"]["id"], canonical.pk)

    def test_timeline_filters_unfiltered_related_prefetch(self):
        canonical, duplicate = self.pair()
        box = Box.objects.prefetch_related(Prefetch(
            "biological_measurements", queryset=BiologicalMeasurement.objects.order_by("-measured_on"),
        )).get(pk=self.box.pk)
        self.assertEqual(len(box.biological_measurements.all()), 2)
        entries = biological_timeline(box, context={})
        self.assertEqual([entry["id"] for entry in entries], [canonical.pk])
        self.assertNotIn(duplicate.pk, [entry["id"] for entry in entries])

    def test_week_proposal_rejects_excluded_evidence(self):
        canonical, _ = self.pair()
        proposal = BiologicalMeasurement(
            box=self.box, measured_on=None, week_start=self.day, temporal_precision="iso_week",
            duplicate_of=canonical,
        )
        with self.assertRaisesMessage(ValueError, "excluded duplicate"):
            resolve_week_observation_state(box=self.box, organization_id=self.org.pk, measurement=proposal)

    def test_import_baseline_chooses_canonical_not_newer_duplicate(self):
        canonical, duplicate = self.pair()
        source, mapping, preparation = self.prepare()
        plan = self.plan(preparation)
        self.assertTrue(plan.ready)
        self.assertEqual(plan.schema_version, "1.3")
        self.assertEqual(plan.rows[0].outcome, "ALREADY_MATCHES")
        self.assertEqual(plan.rows[0].existing.id, canonical.pk)
        baseline = asdict(plan.rows[0].existing)
        self.assertIsNone(baseline["duplicate_of_id"])
        self.assertEqual(baseline["environment_source_box_id"], self.source_box.pk)
        self.assertEqual(asdict(_baseline(duplicate))["duplicate_of_id"], canonical.pk)
        before = list(BiologicalMeasurement.objects.order_by("pk").values())
        receipt, replayed = self.apply(source, mapping, plan)
        self.assertFalse(replayed)
        self.assertEqual(receipt.result["rows"][0]["measurement_id"], canonical.pk)
        self.assertEqual(list(BiologicalMeasurement.objects.order_by("pk").values()), before)
        self.assertTrue(self.apply(source, mapping, plan)[1])

    def test_import_creates_canonical_row_without_updating_duplicate_only_slot(self):
        _, duplicate = self.pair(move=False)
        before = BiologicalMeasurement.objects.filter(pk=duplicate.pk).values().get()
        source, mapping, preparation = self.prepare()
        plan = self.plan(preparation)
        self.assertEqual(plan.rows[0].outcome, "CREATE")
        receipt, replayed = self.apply(source, mapping, plan)
        self.assertFalse(replayed)
        created = BiologicalMeasurement.objects.canonical().get(box=self.box)
        self.assertNotEqual(created.pk, duplicate.pk)
        self.assertIsNone(created.environment_source_box_id)
        self.assertEqual(receipt.result["rows"][0]["measurement_id"], created.pk)
        self.assertEqual(BiologicalMeasurement.objects.filter(pk=duplicate.pk).values().get(), before)

    def test_accepted_receipt_replay_keeps_original_baseline_after_exclusion(self):
        target, _ = self.pair(move=False)
        source, mapping, preparation = self.prepare()
        plan = self.plan(preparation)
        receipt, _ = self.apply(source, mapping, plan)
        accepted = receipt.accepted_plan
        result = receipt.result
        created_id = result["rows"][0]["measurement_id"]
        BiologicalMeasurement.objects.filter(pk=created_id).update(duplicate_of=target)
        replay, replayed = self.apply(source, mapping, plan)
        self.assertTrue(replayed)
        self.assertEqual(replay.pk, receipt.pk)
        self.assertEqual(replay.accepted_plan, accepted)
        self.assertEqual(replay.result, result)
        self.assertIsNone(replay.result["rows"][0]["measurement"]["duplicate_of_id"])

    def retire_box(self):
        Box.objects.filter(pk=self.source_box.pk).update(status=Box.Status.ACTIVE)
        Box.objects.filter(pk=self.box.pk).update(
            merged_into=self.source_box, status=Box.Status.INACTIVE, thermal_zone=None,
        )

    def test_planner_rejects_retired_box_even_with_matching_canonical_measurement(self):
        self.pair()
        source, mapping, preparation = self.prepare()
        before = self.plan(preparation)
        self.assertEqual(before.rows[0].outcome, "ALREADY_MATCHES")
        measurements = list(BiologicalMeasurement.objects.order_by("pk").values())
        self.retire_box()
        after = self.plan(preparation)
        self.assertFalse(after.ready)
        self.assertEqual(after.rows[0].outcome, "UNKNOWN_BOX")
        self.assertIsNone(after.rows[0].box)
        self.assertIsNone(after.rows[0].existing)
        with self.assertRaises(MeasurementImportApplyError) as caught:
            self.apply(source, mapping, before)
        self.assertEqual(caught.exception.code, "stale_plan")
        self.assertEqual(list(BiologicalMeasurement.objects.order_by("pk").values()), measurements)
        self.assertFalse(MeasurementImportReceipt.objects.exists())

    def test_planner_rejects_empty_retired_box_and_apply_cannot_create(self):
        source, mapping, preparation = self.prepare()
        before = self.plan(preparation)
        self.assertEqual(before.rows[0].outcome, "CREATE")
        self.retire_box()
        after = self.plan(preparation)
        self.assertEqual(after.rows[0].outcome, "UNKNOWN_BOX")
        self.assertFalse(after.ready)
        for plan, code in ((before, "stale_plan"), (after, "plan_not_applicable")):
            with self.subTest(code=code), self.assertRaises(MeasurementImportApplyError) as caught:
                self.apply(source, mapping, plan)
            self.assertEqual(caught.exception.code, code)
        self.assertFalse(BiologicalMeasurement.objects.exists())
        self.assertFalse(MeasurementImportReceipt.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_environment_change_invalidates_import_plan_without_revision_change(self):
        canonical, _ = self.pair()
        source, mapping, preparation = self.prepare()
        plan = self.plan(preparation)
        BiologicalMeasurement.objects.filter(pk=canonical.pk).update(environment_source_box=None)
        self.assertNotEqual(self.plan(preparation).fingerprint, plan.fingerprint)
        with self.assertRaises(MeasurementImportApplyError) as caught:
            self.apply(source, mapping, plan)
        self.assertEqual(caught.exception.code, "stale_plan")
        self.assertFalse(MeasurementImportReceipt.objects.exists())

    def test_exclusion_invalidates_baseline_and_duplicate_only_slot_is_free(self):
        canonical, duplicate = self.pair(move=False)
        _, _, preparation = self.prepare(count=900)
        self.assertEqual(self.plan(preparation).rows[0].outcome, "CREATE")
        created = create_biological_measurement(
            box_id=self.box.pk, organization_ids=[self.org.pk], user=self.actor,
            data={"measured_on": self.day, "polyp_count": 900, "ephyrae_count": 0},
        )
        before = self.plan(preparation)
        self.assertEqual(before.rows[0].existing.id, created.pk)
        BiologicalMeasurement.objects.filter(pk=created.pk).update(duplicate_of=canonical)
        after = self.plan(preparation)
        self.assertNotEqual(before.fingerprint, after.fingerprint)
        self.assertEqual(after.rows[0].outcome, "CREATE")
        self.assertEqual(BiologicalMeasurement.objects.filter(box=self.box).count(), 2)
        duplicate.refresh_from_db()
        self.assertEqual(duplicate.duplicate_of_id, canonical.pk)
