"""Isolated acceptance, immutable evidence and rollback tests (SQLite only)."""

import csv
from dataclasses import asdict
from datetime import date
import hashlib
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import TestCase
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import OrganizationMembership
from apps.audit.models import Alert, AuditLog
from apps.cultures.models import Box
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .csv_import import read_csv
from .import_apply import MeasurementImportApplyError, apply_measurement_import
from .import_planner import build_measurement_import_plan, preview_identity
from .models import BiologicalMeasurement, MeasurementImportReceipt
from .services import correct_biological_measurement, create_biological_measurement


class MeasurementImportApplyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Synthetic apply laboratory")
        cls.other_org = Organization.objects.create(name="Foreign apply laboratory")
        cls.actor = get_user_model().objects.create_user(username="apply-admin", email="apply-admin@example.org")
        cls.tech = get_user_model().objects.create_user(username="apply-tech", email="apply-tech@example.org")
        cls.viewer = get_user_model().objects.create_user(username="apply-viewer", email="apply-viewer@example.org")
        for user, role in (
            (cls.actor, OrganizationMembership.Role.ADMIN),
            (cls.tech, OrganizationMembership.Role.LAB_TECHNICIAN),
            (cls.viewer, OrganizationMembership.Role.VIEWER),
        ):
            OrganizationMembership.objects.create(user=user, organization=cls.org, role=role)
        species = Species.objects.create(scientific_name="Aurelia apply", genus_species_code="AAP")
        strain = Strain.objects.create(organization=cls.org, species=species, code="AAP-QA-1")
        other_strain = Strain.objects.create(organization=cls.other_org, species=species, code="AAP-OTHER-1")
        cls.box = Box.objects.create(
            organization=cls.org, strain=strain, global_code="AAP-QA-1.001", box_number="001",
        )
        cls.box2 = Box.objects.create(
            organization=cls.org, strain=strain, global_code="AAP-QA-1.002", box_number="002",
        )
        cls.foreign_box = Box.objects.create(
            organization=cls.other_org, strain=other_strain,
            global_code="AAP-OTHER-1.001", box_number="001",
        )
        cls.day = date(2026, 9, 16)

    def source(self, rows=None, fields=None):
        fields = fields or ("box_reference", "measured_on", "polyp_count", "ephyrae_count")
        rows = rows if rows is not None else [self.row()]
        output = StringIO()
        writer = csv.writer(output, lineterminator="\r\n")
        writer.writerow(fields)
        for row in rows:
            writer.writerow([row.get(field, "") for field in fields])
        return output.getvalue().encode("utf-8"), {field: field for field in fields}

    def row(self, **changes):
        return dict({
            "box_reference": self.box.global_code, "measured_on": self.day.isoformat(),
            "polyp_count": "0", "ephyrae_count": "0",
        }, **changes)

    def preview(self, source, mapping, *, actor=None, organization=None, delimiter=","):
        preparation = read_csv(
            source, source_id="diagnostic-upload", mapping=mapping, confirmed=True, delimiter=delimiter,
        )
        return build_measurement_import_plan(
            preparation, actor=actor or self.actor, organization=organization or self.org,
        )

    def arguments(self, source=None, mapping=None, **changes):
        if source is None:
            source, mapping = self.source()
        actor = changes.pop("actor", self.actor)
        organization = changes.pop("organization", self.org)
        delimiter = changes.pop("delimiter", ",")
        plan = self.preview(source, mapping, actor=actor, organization=organization, delimiter=delimiter)
        result = dict(
            source=source, mapping=mapping, delimiter=delimiter, actor=actor,
            organization=organization,
            expected_preparation_fingerprint=plan.preparation_fingerprint,
            expected_plan_fingerprint=plan.fingerprint,
        )
        result.update(changes)
        return result

    def apply(self, **changes):
        return apply_measurement_import(**self.arguments(**changes))

    def assert_rejected(self, args, code):
        with self.assertRaises(MeasurementImportApplyError) as caught:
            apply_measurement_import(**args)
        self.assertEqual(caught.exception.code, code)

    def assert_empty(self):
        self.assertFalse(BiologicalMeasurement.objects.exists())
        self.assertFalse(MeasurementImportReceipt.objects.exists())
        self.assertFalse(AuditLog.objects.exists())
        self.assertFalse(Alert.objects.exists())
        self.box.refresh_from_db()
        self.box2.refresh_from_db()
        self.assertEqual(self.box.polyp_state_revision, 0)
        self.assertEqual(self.box2.polyp_state_revision, 0)

    def test_minimal_exact_create_retains_bytes_unknowns_zero_and_attribution(self):
        source, mapping = self.source()
        source = b"\xef\xbb\xbf" + source
        args = self.arguments(source, mapping)
        receipt, replayed = apply_measurement_import(**args)
        self.assertFalse(replayed)
        receipt.refresh_from_db()
        measurement = BiologicalMeasurement.objects.get()
        self.assertEqual(bytes(receipt.source_bytes), source)
        self.assertEqual(receipt.source_sha256, hashlib.sha256(source).hexdigest())
        self.assertEqual(receipt.organization_id, self.org.pk)
        self.assertEqual(receipt.actor_id, self.actor.pk)
        self.assertIsNotNone(receipt.accepted_at)
        self.assertEqual(measurement.measured_on, self.day)
        self.assertEqual(measurement.week_start, date(2026, 9, 14))
        self.assertEqual(measurement.temporal_precision, "exact_date")
        self.assertEqual((measurement.polyp_count, measurement.ephyrae_count), (0, 0))
        for field in ("strobila_count", "salinity_psu", "culture_status", "needs_attention", "notes"):
            self.assertIsNone(getattr(measurement, field), field)
        self.assertEqual(measurement.observer_status, "not_recorded")
        self.assertIsNone(measurement.original_observer_id)
        self.assertEqual(measurement.user_id, self.actor.pk)
        self.assertEqual(measurement.imported_by_id, self.actor.pk)
        self.assertEqual((measurement.origin, measurement.import_format), ("file_import", "csv"))
        result = receipt.result["rows"][0]
        self.assertEqual(result["measurement_id"], measurement.pk)
        self.assertEqual(result["measurement"]["imported_by_id"], self.actor.pk)
        self.assertEqual(receipt.preparation["source_id"], "diagnostic-upload")
        self.assertEqual(receipt.preparation["source_sha256"], receipt.source_sha256)
        self.assertEqual(receipt.plan_fingerprint, args["expected_plan_fingerprint"])
        self.assertEqual(receipt.accepted_plan["schema_version"], "1.3")
        self.assertEqual(set(receipt.result["audit_ids"]), set(AuditLog.objects.values_list("pk", flat=True)))
        self.assertEqual(AuditLog.objects.count(), 2)
        self.assertFalse(Alert.objects.exists())
        self.box.refresh_from_db()
        self.assertEqual(self.box.polyp_state_revision, 1)

    def test_explicit_metadata_and_zero_are_not_lost(self):
        fields = ("box_reference", "measured_on", "polyp_count", "ephyrae_count", "strobila_count",
                  "salinity_psu", "culture_status", "needs_attention", "notes", "observer_reference")
        source, mapping = self.source([self.row(
            strobila_count="0", salinity_psu="0", culture_status="not_specified",
            needs_attention="false", notes=" preserved\nverbatim ", observer_reference="",
        )], fields)
        receipt, _ = self.apply(source=source, mapping=mapping)
        measurement = BiologicalMeasurement.objects.get()
        self.assertEqual(measurement.strobila_count, 0)
        self.assertEqual(measurement.salinity_psu, 0)
        self.assertEqual(measurement.culture_status, "not_specified")
        self.assertIs(measurement.needs_attention, False)
        self.assertEqual(measurement.notes, " preserved\nverbatim ")
        self.assertEqual(receipt.preparation["records"][0]["row"]["values"][-1]["state"], "null")

    def test_blank_optional_states_remain_unknown_and_retained_in_evidence(self):
        fields = ("box_reference", "measured_on", "polyp_count", "ephyrae_count", "strobila_count",
                  "salinity_psu", "culture_status", "needs_attention", "notes")
        source, mapping = self.source(fields=fields)
        receipt, _ = self.apply(source=source, mapping=mapping)
        states = {value["field"]: value["state"] for value in receipt.preparation["records"][0]["row"]["values"]}
        self.assertEqual(states["strobila_count"], "null")
        self.assertEqual(states["salinity_psu"], "null")
        for field in ("culture_status", "needs_attention", "notes"):
            self.assertEqual(states[field], "blank")
            self.assertIsNone(getattr(BiologicalMeasurement.objects.get(), field))

    def test_preview_does_not_store_sources_or_receipts(self):
        source, mapping = self.source()
        plan = self.preview(source, mapping)
        self.assertTrue(plan.ready)
        self.assertEqual(preview_identity(plan), {
            "preparation_fingerprint": plan.preparation_fingerprint,
            "plan_fingerprint": plan.fingerprint,
        })
        self.assert_empty()

    def test_replay_original_guards_has_no_writes_and_retains_acceptance_actor(self):
        args = self.arguments()
        other_review = self.arguments(args["source"], args["mapping"], actor=self.tech)
        receipt, _ = apply_measurement_import(**args)
        other_receipt, other_replayed = apply_measurement_import(**other_review)
        self.assertTrue(other_replayed)
        self.assertEqual(other_receipt.pk, receipt.pk)
        receipt2, replayed = apply_measurement_import(**args)
        self.assertTrue(replayed)
        self.assertEqual(receipt.pk, receipt2.pk)
        self.assertEqual(BiologicalMeasurement.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 2)
        self.assertEqual(MeasurementImportReceipt.objects.count(), 1)
        fresh = self.arguments(args["source"], args["mapping"], actor=self.tech)
        receipt3, replayed = apply_measurement_import(**fresh)
        self.assertTrue(replayed)
        self.assertEqual(receipt3.actor_id, self.actor.pk)

    def test_replay_survives_later_correction_without_reapplying_old_values(self):
        args = self.arguments()
        receipt, _ = apply_measurement_import(**args)
        measurement = BiologicalMeasurement.objects.get()
        correct_biological_measurement(
            box_id=self.box.pk, measurement_id=measurement.pk, organization_ids=[self.org.pk],
            user=self.actor, data={"polyp_count": 17},
        )
        audits = AuditLog.objects.count()
        replay, replayed = apply_measurement_import(**args)
        self.assertTrue(replayed)
        self.assertEqual(replay.pk, receipt.pk)
        measurement.refresh_from_db()
        self.assertEqual(measurement.polyp_count, 17)
        self.assertEqual(replay.result["rows"][0]["measurement"]["values"][0], ["polyp_count", 0])
        self.assertEqual(AuditLog.objects.count(), audits)
        entry = AuditLog.objects.get(action=AuditLog.Action.ENTRY)
        self.assertEqual(entry.metadata["valeurs"]["polypes"], 0)

    def test_replay_does_not_recreate_deleted_measurement(self):
        args = self.arguments()
        receipt, _ = apply_measurement_import(**args)
        measurement_id = BiologicalMeasurement.objects.get().pk
        BiologicalMeasurement.objects.all().delete()
        replay, replayed = apply_measurement_import(**args)
        self.assertTrue(replayed)
        self.assertEqual(replay.pk, receipt.pk)
        self.assertEqual(replay.result["rows"][0]["measurement_id"], measurement_id)
        self.assertFalse(BiologicalMeasurement.objects.exists())

    def test_changed_mapping_is_a_distinct_interpretation_not_duplicate_science(self):
        args = self.arguments()
        original, _ = apply_measurement_import(**args)
        mapping = dict(args["mapping"])
        mapping["polyp_count"], mapping["ephyrae_count"] = "ephyrae_count", "polyp_count"
        changed = self.arguments(args["source"], mapping)
        second, replayed = apply_measurement_import(**changed)
        self.assertFalse(replayed)
        self.assertNotEqual(second.pk, original.pk)
        self.assertEqual(second.source_sha256, original.source_sha256)
        self.assertNotEqual(second.preparation_fingerprint, original.preparation_fingerprint)
        self.assertEqual(second.result["rows"][0]["outcome"], "ALREADY_MATCHES")
        self.assertEqual(BiologicalMeasurement.objects.count(), 1)
        self.assertEqual(MeasurementImportReceipt.objects.count(), 2)

    def test_noop_and_blank_only_acceptance_preserve_original_source(self):
        create_biological_measurement(
            box_id=self.box.pk, organization_ids=[self.org.pk], user=self.actor,
            data={"measured_on": str(self.day), "polyp_count": 0, "ephyrae_count": 0},
        )
        before = asdict(self.preview(*self.source()).rows[0].existing)
        receipt, _ = self.apply()
        self.assertEqual(receipt.result["rows"][0]["outcome"], "ALREADY_MATCHES")
        self.assertEqual(asdict(self.preview(*self.source()).rows[0].existing), before)
        source, mapping = self.source([self.row(polyp_count="", ephyrae_count="")])
        blank, _ = self.apply(source=source, mapping=mapping)
        blank.refresh_from_db()
        self.assertEqual(bytes(blank.source_bytes), source)
        self.assertEqual(blank.result["rows"][0]["outcome"], "IGNORED")
        self.assertIsNone(blank.result["rows"][0]["measurement_id"])
        self.assertEqual(len(blank.result["audit_ids"]), 1)

    def test_header_only_noop_has_receipt_and_replays(self):
        source, mapping = self.source([])
        args = self.arguments(source, mapping)
        receipt, replayed = apply_measurement_import(**args)
        self.assertFalse(replayed)
        self.assertEqual(receipt.result["rows"], [])
        self.assertTrue(apply_measurement_import(**args)[1])
        self.assertEqual(AuditLog.objects.count(), 1)
        self.assertFalse(BiologicalMeasurement.objects.exists())

    def test_mixed_create_matching_and_ignored_accept_atomically_without_overwrite(self):
        existing = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=self.day, polyp_count=0, ephyrae_count=0,
        )
        source, mapping = self.source([
            self.row(), self.row(box_reference=self.box2.global_code),
            self.row(polyp_count="", ephyrae_count=""),
        ])
        receipt, _ = self.apply(source=source, mapping=mapping)
        self.assertEqual([row["outcome"] for row in receipt.result["rows"]],
                         ["ALREADY_MATCHES", "CREATE", "IGNORED"])
        existing.refresh_from_db()
        self.assertEqual(existing.origin, "legacy_unclassified")
        self.assertEqual(existing.temporal_precision, "legacy_unverified")
        self.assertEqual(BiologicalMeasurement.objects.count(), 2)

    def test_preparation_and_plan_fingerprints_are_required_even_for_replays(self):
        args = self.arguments()
        for field, code in (("expected_preparation_fingerprint", "stale_preparation"),
                            ("expected_plan_fingerprint", "stale_plan")):
            with self.subTest(field=field):
                self.assert_rejected(dict(args, **{field: "0" * 64}), code)
                self.assert_empty()
        apply_measurement_import(**args)
        self.assert_rejected(dict(args, expected_plan_fingerprint="0" * 64), "stale_plan")

    def test_changed_source_or_interpretation_cannot_use_reviewed_guards(self):
        args = self.arguments()
        self.assert_rejected(dict(args, source=args["source"] + b"\r\n"), "stale_preparation")
        mapping = dict(args["mapping"], polyp_count="ephyrae_count", ephyrae_count="polyp_count")
        self.assert_rejected(dict(args, mapping=mapping), "stale_preparation")
        self.assert_empty()

    def test_stale_manual_create_blocks_without_overwriting(self):
        args = self.arguments()
        create_biological_measurement(
            box_id=self.box.pk, organization_ids=[self.org.pk], user=self.actor,
            data={"measured_on": str(self.day), "polyp_count": 0, "ephyrae_count": 0},
        )
        self.assert_rejected(args, "stale_plan")
        self.assertFalse(MeasurementImportReceipt.objects.exists())
        self.assertEqual(BiologicalMeasurement.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_all_provenance_baseline_fields_invalidate_stale_preview(self):
        measurement = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=self.day, polyp_count=0, ephyrae_count=0,
        )
        changes = (
            {"temporal_precision": "exact_date"},
            {"observer_status": "not_recorded"},
            {"observer_status": "known", "original_observer_id": self.actor.pk},
            {"origin": "file_import", "import_format": "csv", "imported_by_id": self.actor.pk},
            {"user_id": self.tech.pk},
            {"polyp_state_sequence": 999},
            {"notes": "changed"},
            {"created_at": "2026-01-01T00:00:00Z"},
        )
        original = BiologicalMeasurement.objects.filter(pk=measurement.pk).values().get()
        for change in changes:
            with self.subTest(change=change):
                args = self.arguments()
                BiologicalMeasurement.objects.filter(pk=measurement.pk).update(**change)
                self.assert_rejected(args, "stale_plan")
                BiologicalMeasurement.objects.filter(pk=measurement.pk).update(**original)
        self.assertFalse(MeasurementImportReceipt.objects.exists())

    def test_box_lifecycle_revision_and_reference_changes_invalidate_preview(self):
        for change in ({"status": Box.Status.INACTIVE}, {"polyp_state_revision": 33},
                       {"global_code": "AAP-QA-1.renamed"}, {"organization_id": self.other_org.pk}):
            with self.subTest(change=change):
                args = self.arguments()
                original = Box.objects.filter(pk=self.box.pk).values().get()
                Box.objects.filter(pk=self.box.pk).update(**change)
                self.assert_rejected(args, "stale_plan")
                Box.objects.filter(pk=self.box.pk).update(**original)
        self.assert_empty()

    def test_unresolved_batches_never_partially_apply(self):
        bad_rows = (
            self.row(polyp_count="-1"), self.row(box_reference="unknown"),
            self.row(box_reference=self.foreign_box.global_code),
        )
        for bad in bad_rows:
            with self.subTest(bad=bad):
                source, mapping = self.source([self.row(box_reference=self.box2.global_code), bad])
                self.assert_rejected(self.arguments(source, mapping), "plan_not_applicable")
                self.assert_empty()

    def test_duplicate_source_slots_block_even_identical(self):
        source, mapping = self.source([self.row(), self.row()])
        self.assert_rejected(self.arguments(source, mapping), "plan_not_applicable")
        self.assert_empty()

    def test_named_observer_blocked(self):
        source, mapping = self.source([self.row(observer_reference="apply-admin")],
                                     ("box_reference", "measured_on", "polyp_count", "ephyrae_count", "observer_reference"))
        self.assert_rejected(self.arguments(source, mapping), "plan_not_applicable")
        self.assert_empty()

    def test_week_only_create_blocked_but_existing_matching_week_is_noop(self):
        source, mapping = self.source([self.row(iso_year="2026", iso_week="38")],
                                     ("box_reference", "iso_year", "iso_week", "polyp_count", "ephyrae_count"))
        self.assert_rejected(self.arguments(source, mapping), "plan_not_applicable")
        self.assert_empty()
        measurement = BiologicalMeasurement.objects.create(box=self.box, measured_on=self.day)
        receipt, _ = self.apply(source=source, mapping=mapping)
        self.assertEqual(receipt.result["rows"][0]["measurement_id"], measurement.pk)
        measurement.refresh_from_db()
        self.assertEqual(measurement.measured_on, self.day)
        self.assertEqual(measurement.temporal_precision, "legacy_unverified")

    def test_conflicting_existing_value_blocks_whole_batch(self):
        measurement = BiologicalMeasurement.objects.create(box=self.box, measured_on=self.day, polyp_count=12)
        source, mapping = self.source([self.row(box_reference=self.box2.global_code), self.row()])
        self.assert_rejected(self.arguments(source, mapping), "plan_not_applicable")
        self.assertEqual(BiologicalMeasurement.objects.count(), 1)
        measurement.refresh_from_db()
        self.assertEqual(measurement.polyp_count, 12)
        self.assertFalse(MeasurementImportReceipt.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_inactive_box_create_blocked(self):
        Box.objects.filter(pk=self.box.pk).update(status=Box.Status.INACTIVE)
        self.assert_rejected(self.arguments(), "plan_not_applicable")
        self.assert_empty()

    def test_malformed_and_oversize_sources_never_persist(self):
        source, mapping = self.source()
        for bad in (source + b'"unterminated', b"\xff", b"x" * (5 * 1024 * 1024 + 1)):
            with self.subTest(length=len(bad)):
                self.assert_rejected(self.arguments(bad, mapping), "plan_not_applicable")
                self.assert_empty()

    def test_permissions_reloaded_before_write_and_replay(self):
        args = self.arguments()
        receipt, _ = apply_measurement_import(**args)
        membership = OrganizationMembership.objects.get(user=self.actor, organization=self.org)
        for field, value in (("is_active", False), ("role", OrganizationMembership.Role.VIEWER)):
            previous = getattr(membership, field)
            setattr(membership, field, value)
            membership.save()
            with self.assertRaises(PermissionDenied):
                apply_measurement_import(**args)
            setattr(membership, field, previous)
            membership.save()
        for model, pk in ((get_user_model(), self.actor.pk), (Organization, self.org.pk)):
            model.objects.filter(pk=pk).update(is_active=False)
            with self.assertRaises(PermissionDenied):
                apply_measurement_import(**args)
            model.objects.filter(pk=pk).update(is_active=True)
        self.assertEqual(MeasurementImportReceipt.objects.get().pk, receipt.pk)
        self.assertEqual(AuditLog.objects.count(), 2)

    def test_permission_rechecked_after_box_locks(self):
        args = self.arguments()
        with patch("apps.measurements.import_apply.authorize_measurement_import",
                   side_effect=[(self.actor, self.org), PermissionDenied()]):
            with self.assertRaises(PermissionDenied):
                apply_measurement_import(**args)
        self.assert_empty()

    def test_foreign_organization_does_not_replay_receipt(self):
        args = self.arguments()
        apply_measurement_import(**args)
        OrganizationMembership.objects.create(
            user=self.actor, organization=self.other_org, role=OrganizationMembership.Role.ADMIN,
        )
        foreign = self.arguments(args["source"], args["mapping"], organization=self.other_org)
        self.assert_rejected(foreign, "plan_not_applicable")
        self.assertEqual(MeasurementImportReceipt.objects.count(), 1)

    def test_audit_failure_rolls_back_all_science_and_box_revisions(self):
        args = self.arguments()
        with patch("apps.measurements.import_apply.AuditLog.objects.create", side_effect=DatabaseError("audit")):
            with self.assertRaises(DatabaseError):
                apply_measurement_import(**args)
        self.assert_empty()

    def test_acceptance_audit_failure_rolls_back_row_audit_and_measurement(self):
        args = self.arguments()
        original = AuditLog.objects.create
        def fail_acceptance(**kwargs):
            if kwargs["action"] == AuditLog.Action.IMPORT:
                raise DatabaseError("acceptance audit")
            return original(**kwargs)
        with patch("apps.measurements.import_apply.AuditLog.objects.create", side_effect=fail_acceptance):
            with self.assertRaises(DatabaseError):
                apply_measurement_import(**args)
        self.assert_empty()

    def test_second_measurement_failure_rolls_back_first_row_and_its_audit(self):
        source, mapping = self.source([self.row(), self.row(box_reference=self.box2.global_code)])
        args = self.arguments(source, mapping)
        original = BiologicalMeasurement.objects.create
        def fail_second(**kwargs):
            if kwargs["box_id"] == self.box2.pk:
                raise IntegrityError("scientific write")
            return original(**kwargs)
        with patch("apps.measurements.import_apply.BiologicalMeasurement.objects.create", side_effect=fail_second):
            with self.assertRaises(IntegrityError):
                apply_measurement_import(**args)
        self.assert_empty()

    def test_binary_source_storage_failure_rolls_back_receipt_audits_and_science(self):
        args = self.arguments()
        field = MeasurementImportReceipt._meta.get_field("source_bytes")
        with patch.object(field, "get_db_prep_value", side_effect=DatabaseError("binary storage")):
            with self.assertRaises(DatabaseError):
                apply_measurement_import(**args)
        self.assert_empty()

    def test_receipt_insert_failure_rolls_back_entire_acceptance(self):
        args = self.arguments()
        def fail_insert(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith("INSERT INTO \"MEASUREMENTS_MEASUREMENTIMPORTRECEIPT\""):
                raise IntegrityError("receipt storage")
            return execute(sql, params, many, context)
        with connection.execute_wrapper(fail_insert):
            with self.assertRaises(IntegrityError):
                apply_measurement_import(**args)
        self.assert_empty()

    def test_receipt_evidence_cannot_be_updated_or_deleted_through_model(self):
        receipt, _ = self.apply()
        for operation in (
            lambda: receipt.save(), lambda: receipt.delete(),
            lambda: MeasurementImportReceipt.objects.filter(pk=receipt.pk).update(actor_id=self.tech.pk),
            lambda: MeasurementImportReceipt.objects.filter(pk=receipt.pk).delete(),
            lambda: MeasurementImportReceipt.objects.bulk_update([receipt], ["result"]),
        ):
            with self.assertRaises(ValidationError):
                operation()
        receipt.refresh_from_db()
        self.assertEqual(receipt.actor_id, self.actor.pk)

    def test_receipt_is_immutable_even_through_raw_database_writes(self):
        receipt, _ = self.apply()
        for sql in (
            'UPDATE measurements_measurementimportreceipt SET actor_id = 999',
            'DELETE FROM measurements_measurementimportreceipt',
        ):
            with self.subTest(sql=sql):
                with self.assertRaises(DatabaseError):
                    with transaction.atomic(), connection.cursor() as cursor:
                        cursor.execute(sql)
        receipt.refresh_from_db()
        self.assertEqual(receipt.actor_id, self.actor.pk)
        self.assertEqual(MeasurementImportReceipt.objects.count(), 1)

    def test_initial_authority_rejects_viewers_and_foreign_scope(self):
        args = self.arguments()
        for changes in ({"actor": self.viewer}, {"organization": self.other_org}):
            with self.subTest(changes=changes), self.assertRaises(PermissionDenied):
                apply_measurement_import(**dict(args, **changes))
        self.assert_empty()

    def test_technician_can_accept_and_is_not_invented_as_observer(self):
        receipt, _ = self.apply(actor=self.tech)
        self.assertEqual(receipt.actor_id, self.tech.pk)
        measurement = BiologicalMeasurement.objects.get()
        self.assertEqual(measurement.imported_by_id, self.tech.pk)
        self.assertIsNone(measurement.original_observer_id)

    def test_stale_manual_correction_invalidates_matching_preview(self):
        measurement = BiologicalMeasurement.objects.create(box=self.box, measured_on=self.day)
        args = self.arguments()
        correct_biological_measurement(
            box_id=self.box.pk, measurement_id=measurement.pk, organization_ids=[self.org.pk],
            user=self.actor, data={"polyp_count": 7},
        )
        self.assert_rejected(args, "stale_plan")
        self.assertFalse(MeasurementImportReceipt.objects.exists())
        measurement.refresh_from_db()
        self.assertEqual(measurement.polyp_count, 7)

    def test_reverse_source_order_still_locks_boxes_in_primary_key_order(self):
        source, mapping = self.source([self.row(box_reference=self.box2.global_code), self.row()])
        args = self.arguments(source, mapping)
        statements = []
        def observe(execute, sql, params, many, context):
            if sql.lstrip().upper().startswith('SELECT') and 'FROM "cultures_box"' in sql:
                statements.append(sql)
            return execute(sql, params, many, context)
        with connection.execute_wrapper(observe):
            apply_measurement_import(**args)
        lock_lookup = next(sql for sql in statements if '"global_code" IN' in sql)
        self.assertIn('ORDER BY "cultures_box"."id" ASC', lock_lookup)
        self.assertEqual(BiologicalMeasurement.objects.count(), 2)

    def test_concurrent_noop_receipt_uniqueness_returns_winner_without_extra_audit(self):
        source, mapping = self.source([])
        args = self.arguments(source, mapping)
        winner, _ = apply_measurement_import(**args)
        # Deterministically simulate a competing acceptance becoming visible only
        # after the unique constraint rejects our INSERT. PostgreSQL lock behavior
        # itself is deliberately not claimed by this SQLite test.
        with patch("apps.measurements.import_apply._receipt", side_effect=[None, None, winner]):
            receipt, replayed = apply_measurement_import(**args)
        self.assertTrue(replayed)
        self.assertEqual(receipt.pk, winner.pk)
        self.assertEqual(AuditLog.objects.count(), 1)
        self.assertEqual(MeasurementImportReceipt.objects.count(), 1)
        self.assertFalse(BiologicalMeasurement.objects.exists())

    def test_source_failure_also_rejects_noop_acceptance_without_audit(self):
        source, mapping = self.source([])
        args = self.arguments(source, mapping)
        field = MeasurementImportReceipt._meta.get_field("source_bytes")
        with patch.object(field, "get_db_prep_value", side_effect=DatabaseError("binary storage")):
            with self.assertRaises(DatabaseError):
                apply_measurement_import(**args)
        self.assert_empty()

    def test_interactive_defaults_remain_unchanged(self):
        measurement = create_biological_measurement(
            box_id=self.box.pk, organization_ids=[self.org.pk], user=self.actor,
            data={"measured_on": str(self.day), "polyp_count": 0, "ephyrae_count": 0},
        )
        self.assertEqual(measurement.strobila_count, 0)
        self.assertEqual(measurement.culture_status, "not_specified")
        self.assertIs(measurement.needs_attention, False)
        self.assertEqual(measurement.notes, "")
