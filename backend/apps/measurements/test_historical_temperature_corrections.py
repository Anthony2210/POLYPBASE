"""Synthetic correction contracts on isolated in-memory SQLite tables.

These tests cover service transactions and ORM validation, not PostgreSQL locks
or SQL guards.
"""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box

from . import services, test_historical_temperature_apply as apply_tests
from .historical_temperature_partial import PartialImportError
from .models import (
    BiologicalMeasurement,
    HistoricalTemperatureEvidence,
    HistoricalTemperatureImportReceipt,
    HistoricalTemperatureReading,
)
from .temperature_series import historical_temperature_point
from .test_historical_temperature_plan import evidence


@skipUnless(connection.vendor == "sqlite", "Requires isolated SQLite settings")
class HistoricalTemperatureCorrectionTests(TestCase):
    # Import the module, not its TestCase, to avoid collecting its tests again.
    _set_up_acceptance = apply_tests.AcceptanceTests.setUp
    accept = apply_tests.AcceptanceTests.accept

    def setUp(self):
        self._set_up_acceptance()
        self.membership = OrganizationMembership.objects.create(
            user=self.actor,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.receipt, replay = self.accept()
        self.assertFalse(replay)
        self.reading = HistoricalTemperatureReading.objects.get(
            biological_measurement=self.biology,
        )
        self.assertEqual(self.reading.association_status, "confirmed")
        self.original_history = self.history()
        self.original_biology = BiologicalMeasurement.objects.values().get(pk=self.biology.pk)
        self.original_audit_values = {
            "date": "2026-01-01", "polypes": 0, "ephyrules": 0,
            "strobiles": 0, "salinite_psu": None,
            "statut_culture": "not_specified", "a_verifier": False, "note": "",
        }

    def history(self):
        return {
            "readings": list(HistoricalTemperatureReading.objects.order_by("pk").values()),
            "evidences": list(HistoricalTemperatureEvidence.objects.order_by("pk").values()),
            "receipts": list(HistoricalTemperatureImportReceipt.objects.order_by("pk").values()),
        }

    def state(self):
        return {
            **self.history(),
            "boxes": list(Box.objects.order_by("pk").values()),
            "biology": list(BiologicalMeasurement.objects.order_by("pk").values()),
            "audits": list(AuditLog.objects.order_by("pk").values()),
        }

    def correct(self, data, **overrides):
        arguments = {
            "box_id": self.box.pk, "measurement_id": self.biology.pk,
            "organization_ids": [self.organization.pk], "user": self.actor,
            "data": data,
        }
        arguments.update(overrides)
        return services.correct_biological_measurement(**arguments)

    def assert_history_preserved(self, status, *, before=None, reading_id=None):
        expected = deepcopy(self.original_history if before is None else before)
        reading_id = self.reading.pk if reading_id is None else reading_id
        for row in expected["readings"]:
            if row["id"] == reading_id:
                row["association_status"] = status
        self.assertEqual(self.history(), expected)

    def assert_correction_audit(self, before, after, *, previous_status="confirmed", status="pending_review", user=None):
        event = AuditLog.objects.filter(
            action=AuditLog.Action.UPDATE,
            metadata__measurement_id=self.biology.pk,
        ).latest("pk")
        self.assertEqual(event.organization_id, self.organization.pk)
        self.assertEqual(event.user_id, (user or self.actor).pk)
        self.assertEqual(event.object_type, "box")
        self.assertEqual(event.object_id, self.box.global_code)
        self.assertEqual(event.metadata, {
            "measurement_id": self.biology.pk,
            "before": before,
            "after": after,
            "valeurs": after,
            "modifications": {
                key: {"avant": before[key], "apres": value}
                for key, value in after.items() if before[key] != value
            },
            "historical_temperature": {
                "reading_id": self.reading.pk, "before": previous_status, "after": status,
            },
        })

    def projection_values(self):
        self.reading.refresh_from_db()
        return {
            "reading_id": self.reading.pk,
            "biological_measurement_id": self.reading.biological_measurement_id,
            "box_id": self.box.pk,
            "iso_year": self.reading.iso_year, "iso_week": self.reading.iso_week,
            "temperature_c": self.reading.temperature_c,
            "evidence_count": self.reading.evidences.count(),
            "association_status": self.reading.association_status,
        }

    def foreign_biology(self):
        box = Box.objects.create(
            organization=self.other, strain=self.box.strain,
            global_code="ABC-DEF-1.002", box_number="002",
        )
        biology = BiologicalMeasurement.objects.create(
            box=box, measured_on=date(2026, 1, 1), polyp_count=0, ephyrae_count=0,
        )
        reading = HistoricalTemperatureReading.objects.create(
            biological_measurement=biology, iso_year=2026, iso_week=1, temperature_c="21.00",
        )
        HistoricalTemperatureEvidence.objects.create(
            reading=reading, **evidence(source_box=box.global_code, source_value="21.00"),
        )
        return box, biology

    def test_count_changes_mark_pending_and_preserve_complete_source_payloads(self):
        cases = (
            ({"polyp_count": 7}, {"polypes": 7}),
            ({"ephyrae_count": 3}, {"ephyrules": 3}),
            ({"polyp_count": 7, "ephyrae_count": 3}, {"polypes": 7, "ephyrules": 3}),
            ({"polyp_count": 0, "ephyrae_count": 3}, {"ephyrules": 3}),
        )
        for data, changes in cases:
            with self.subTest(data=data), transaction.atomic():
                audits = AuditLog.objects.count()
                corrected = self.correct(data)
                self.assertEqual(corrected.pk, self.biology.pk)
                self.assertEqual(BiologicalMeasurement.objects.values().get(pk=corrected.pk), {
                    **self.original_biology, **data, "user_id": self.actor.pk,
                })
                self.assert_history_preserved("pending_review")
                self.assert_correction_audit(
                    self.original_audit_values, {**self.original_audit_values, **changes},
                )
                self.assertEqual(AuditLog.objects.count(), audits + 1)
                self.assertEqual(self.reading.temperature_c, "0.000")
                self.assertCountEqual(
                    [row["source_value"] for row in self.original_history["evidences"]],
                    ["0", "0.000"],
                )
                transaction.set_rollback(True)

    def test_nonzero_source_counts_corrected_to_real_zero_mark_pending(self):
        for data in ({"polyp_count": 0}, {"ephyrae_count": 0}, {"polyp_count": 0, "ephyrae_count": 0}):
            with self.subTest(data=data), transaction.atomic():
                biology = BiologicalMeasurement.objects.create(
                    box=self.box, measured_on=date(2026, 1, 5), polyp_count=8, ephyrae_count=2,
                )
                reading = HistoricalTemperatureReading.objects.create(
                    biological_measurement=biology, iso_year=2026, iso_week=2, temperature_c="0.00",
                )
                HistoricalTemperatureEvidence.objects.create(
                    reading=reading, **evidence(
                        source_value="0.00", polyp_value="8", ephyrae_value="2",
                        source_iso_week=2, week_value="2",
                    ),
                )
                before = self.history()
                corrected = self.correct(data, measurement_id=biology.pk)
                corrected.refresh_from_db()
                self.assertEqual(corrected.polyp_count, data.get("polyp_count", 8))
                self.assertEqual(corrected.ephyrae_count, data.get("ephyrae_count", 2))
                self.assert_history_preserved("pending_review", before=before, reading_id=reading.pk)
                event = AuditLog.objects.get(action=AuditLog.Action.UPDATE, metadata__measurement_id=biology.pk)
                self.assertEqual(event.metadata["before"]["polypes"], 8)
                self.assertEqual(event.metadata["before"]["ephyrules"], 2)
                self.assertEqual(event.metadata["after"]["polypes"], corrected.polyp_count)
                self.assertEqual(event.metadata["after"]["ephyrules"], corrected.ephyrae_count)
                self.assertEqual(event.metadata["historical_temperature"], {
                    "reading_id": reading.pk, "before": "confirmed", "after": "pending_review",
                })
                transaction.set_rollback(True)

    def test_noop_and_explicit_unchanged_zero_counts_stay_confirmed(self):
        for data in ({}, {"polyp_count": 0}, {"ephyrae_count": 0}, {"polyp_count": 0, "ephyrae_count": 0}):
            with self.subTest(data=data), transaction.atomic():
                self.correct(data)
                self.assert_history_preserved("confirmed")
                self.assert_correction_audit(
                    self.original_audit_values, self.original_audit_values, status="confirmed",
                )
                transaction.set_rollback(True)

    def test_irrelevant_changes_and_dates_in_same_iso_week_stay_confirmed(self):
        cases = (
            ({"measured_on": "2026-01-04"}, {"date": "2026-01-04"}),
            ({"measured_on": "2025-12-31"}, {"date": "2025-12-31"}),
            ({"strobila_count": 4}, {"strobiles": 4}),
            ({"salinity_psu": "0.00"}, {"salinite_psu": "0.00"}),
            ({"salinity_psu": "35.25"}, {"salinite_psu": "35.25"}),
            ({"notes": "Synthetic correction note"}, {"note": "Synthetic correction note"}),
            ({"culture_status": "bad"}, {"statut_culture": "bad"}),
            ({"needs_attention": True}, {"a_verifier": True}),
        )
        for data, changes in cases:
            with self.subTest(data=data), transaction.atomic():
                corrected = self.correct(data)
                corrected.refresh_from_db()
                for field, value in data.items():
                    expected = date.fromisoformat(value) if field == "measured_on" else (
                        Decimal(value) if field == "salinity_psu" else value
                    )
                    self.assertEqual(getattr(corrected, field), expected)
                self.assertEqual(corrected.week_start, date(2025, 12, 29))
                self.assertEqual((corrected.polyp_count, corrected.ephyrae_count), (0, 0))
                self.assert_history_preserved("confirmed")
                self.assert_correction_audit(
                    self.original_audit_values, {**self.original_audit_values, **changes}, status="confirmed",
                )
                self.assertIsNotNone(historical_temperature_point(**self.projection_values()))
                transaction.set_rollback(True)

    def test_cross_week_correction_keeps_source_week_and_never_relinks(self):
        for day in ("2026-01-05", "2027-01-04"):
            with self.subTest(day=day), transaction.atomic():
                corrected = self.correct({"measured_on": day})
                self.assertEqual(corrected.measured_on, date.fromisoformat(day))
                self.assertEqual(corrected.week_start, BiologicalMeasurement.week_start_for(day))
                self.assert_history_preserved("pending_review")
                self.reading.refresh_from_db()
                self.assertEqual((self.reading.iso_year, self.reading.iso_week), (2026, 1))
                self.assertEqual(self.reading.biological_measurement_id, corrected.pk)
                self.assert_correction_audit(
                    self.original_audit_values, {**self.original_audit_values, "date": day},
                )
                replacement = BiologicalMeasurement.objects.create(
                    box=self.box, measured_on=date(2026, 1, 1), polyp_count=0, ephyrae_count=0,
                )
                self.assertFalse(HistoricalTemperatureReading.objects.filter(biological_measurement=replacement).exists())
                self.assert_history_preserved("pending_review")
                self.assertIsNone(historical_temperature_point(**self.projection_values()))
                transaction.set_rollback(True)

    def test_occupied_week_rejection_has_no_partial_correction_or_status_change(self):
        occupied = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 1, 5), polyp_count=0, ephyrae_count=0,
        )
        before = self.state()
        with self.assertRaises(services.MeasurementWeekConflict) as caught:
            self.correct({"measured_on": "2026-01-11", "polyp_count": 7})
        self.assertEqual(caught.exception.measurement_id, occupied.pk)
        self.assertEqual(caught.exception.week_start, date(2026, 1, 5))
        self.assertEqual(self.state(), before)
        self.assert_history_preserved("confirmed")

    def test_audit_failure_rolls_back_saved_correction_pending_status_and_audit(self):
        before = self.state()
        record = services._record_measurement_audit

        def fail_after_audit(**arguments):
            record(**arguments)
            reading = HistoricalTemperatureReading.objects.get(pk=self.reading.pk)
            biology = BiologicalMeasurement.objects.get(pk=self.biology.pk)
            self.assertEqual(reading.association_status, "pending_review")
            self.assertEqual((biology.polyp_count, biology.measured_on), (7, date(2026, 1, 5)))
            self.assertEqual(AuditLog.objects.count(), len(before["audits"]) + 1)
            raise RuntimeError("Synthetic correction audit failure")

        with patch.object(services, "_record_measurement_audit", side_effect=fail_after_audit):
            with self.assertRaisesMessage(RuntimeError, "Synthetic correction audit failure"):
                self.correct({"polyp_count": 7, "measured_on": "2026-01-05"})
        self.assertEqual(self.state(), before)
        self.assert_history_preserved("confirmed")

    def test_denied_roles_inactive_membership_and_foreign_admin_cannot_correct(self):
        foreign_actor = get_user_model().objects.create_user(username="synthetic-foreign-admin", email="synthetic-foreign-admin@example.org")
        OrganizationMembership.objects.create(
            user=foreign_actor, organization=self.other, role=OrganizationMembership.Role.ADMIN,
        )
        cases = (
            (self.actor, OrganizationMembership.Role.VIEWER, True),
            (self.actor, OrganizationMembership.Role.ADMIN, False),
            (foreign_actor, OrganizationMembership.Role.ADMIN, True),
            (None, OrganizationMembership.Role.ADMIN, True),
        )
        for actor, role, active in cases:
            with self.subTest(actor=actor, role=role, active=active), transaction.atomic():
                OrganizationMembership.objects.filter(pk=self.membership.pk).update(role=role, is_active=active)
                before = self.state()
                data = Mock(side_effect=AssertionError("Denied data must not be parsed"))
                with self.assertRaises(services.MeasurementWriteForbidden) as caught:
                    self.correct(data, user=actor, organization_ids=[self.organization.pk, self.other.pk])
                self.assertEqual(caught.exception.error_code, "role_read_only")
                data.assert_not_called()
                self.assertEqual(self.state(), before)
                transaction.set_rollback(True)

    def test_foreign_scope_and_box_identifiers_cannot_broaden_correction(self):
        foreign_box, foreign_biology = self.foreign_biology()
        OrganizationMembership.objects.create(
            user=self.actor, organization=self.other, role=OrganizationMembership.Role.ADMIN,
        )
        before = self.state()
        cases = (
            {"box_id": foreign_box.pk, "measurement_id": foreign_biology.pk},
            {"measurement_id": foreign_biology.pk},
            {"organization_ids": [self.other.pk]},
            {"organization_ids": []},
            {"box_id": foreign_box.pk, "organization_ids": [self.organization.pk, self.other.pk]},
            {"measurement_id": foreign_biology.pk, "organization_ids": [self.organization.pk, self.other.pk]},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                data = Mock(side_effect=AssertionError("Foreign data must not be parsed"))
                with self.assertRaises(services.MeasurementNotFound):
                    self.correct(data, **overrides)
                data.assert_not_called()
                self.assertEqual(self.state(), before)

    def test_api_ignores_box_and_box_id_payload_without_reassigning_link(self):
        foreign_box, foreign_biology = self.foreign_biology()
        foreign_before = BiologicalMeasurement.objects.values().get(pk=foreign_biology.pk)
        before_history = self.history()
        self.client.force_login(self.actor)
        url = reverse("api_box_measurement_detail", args=[self.box.pk, self.biology.pk])
        cases = (
            ({"box": foreign_box.pk}, "confirmed"),
            ({"box_id": foreign_box.pk}, "confirmed"),
            ({"box": foreign_box.pk, "box_id": foreign_box.pk, "polyp_count": 7}, "pending_review"),
        )
        for payload, status in cases:
            with self.subTest(payload=payload), transaction.atomic():
                response = self.client.patch(url, data=payload, content_type="application/json")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["id"], self.biology.pk)
                biology = BiologicalMeasurement.objects.get(pk=self.biology.pk)
                self.assertEqual(biology.box_id, self.box.pk)
                self.assertEqual(biology.polyp_count, payload.get("polyp_count", 0))
                self.assertEqual(BiologicalMeasurement.objects.values().get(pk=foreign_biology.pk), foreign_before)
                self.assert_history_preserved(status, before=before_history)
                self.assert_correction_audit(
                    self.original_audit_values,
                    {**self.original_audit_values, "polypes": payload.get("polyp_count", 0)},
                    status=status,
                )
                transaction.set_rollback(True)

    def test_api_foreign_box_path_cannot_reach_local_historical_measurement(self):
        foreign_box, _ = self.foreign_biology()
        self.client.force_login(self.actor)
        before = self.state()
        response = self.client.patch(
            reverse("api_box_measurement_detail", args=[foreign_box.pk, self.biology.pk]),
            data={"box_id": self.box.pk, "polyp_count": 7}, content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.state(), before)

    def test_technician_keeps_exact_24h_deadline_on_inactive_historical_box(self):
        technician = get_user_model().objects.create_user(username="synthetic-temperature-technician", email="synthetic-temperature-technician@example.org")
        OrganizationMembership.objects.create(
            user=technician, organization=self.organization, role=OrganizationMembership.Role.LAB_TECHNICIAN,
        )
        created_at = datetime(2026, 1, 1, 8, tzinfo=datetime_timezone.utc)
        BiologicalMeasurement.objects.filter(pk=self.biology.pk).update(created_at=created_at)
        Box.objects.filter(pk=self.box.pk).update(status=Box.Status.INACTIVE)
        deadline = created_at + timedelta(hours=24)
        with patch("apps.measurements.services.timezone.now", return_value=deadline - timedelta(microseconds=1)):
            corrected = self.correct({"polyp_count": 7}, user=technician)
        self.assertEqual(corrected.created_at, created_at)
        self.assertEqual(corrected.user_id, technician.pk)
        self.assertEqual(corrected.polyp_state_sequence, self.original_biology["polyp_state_sequence"])
        self.assert_history_preserved("pending_review")
        self.assert_correction_audit(
            self.original_audit_values, {**self.original_audit_values, "polypes": 7}, user=technician,
        )
        before = self.state()
        for now in (deadline, deadline + timedelta(microseconds=1), deadline + timedelta(days=365)):
            with self.subTest(now=now), patch("apps.measurements.services.timezone.now", return_value=now):
                with self.assertRaises(services.MeasurementWriteForbidden) as caught:
                    self.correct({"polyp_count": 0}, user=technician)
                self.assertEqual(caught.exception.error_code, "edit_window_expired")
                self.assertEqual(self.state(), before)
        self.assertEqual(Box.objects.get(pk=self.box.pk).status, Box.Status.INACTIVE)

    def test_expired_technician_cannot_invalidate_confirmed_association(self):
        OrganizationMembership.objects.filter(pk=self.membership.pk).update(role=OrganizationMembership.Role.LAB_TECHNICIAN)
        deadline = self.biology.created_at + timedelta(hours=24)
        before = self.state()
        with patch("apps.measurements.services.timezone.now", return_value=deadline):
            with self.assertRaises(services.MeasurementWriteForbidden) as caught:
                self.correct({"polyp_count": 7})
        self.assertEqual(caught.exception.error_code, "edit_window_expired")
        self.assertEqual(self.state(), before)
        self.assert_history_preserved("confirmed")

    def test_admin_can_correct_expired_historical_measurement_on_inactive_box(self):
        created_at = datetime(2020, 1, 1, tzinfo=datetime_timezone.utc)
        BiologicalMeasurement.objects.filter(pk=self.biology.pk).update(created_at=created_at)
        Box.objects.filter(pk=self.box.pk).update(status=Box.Status.INACTIVE)
        with patch("apps.measurements.services.timezone.now", return_value=created_at + timedelta(days=3650)):
            corrected = self.correct({"polyp_count": 7})
        self.assertEqual(corrected.created_at, created_at)
        self.assertEqual(corrected.user_id, self.actor.pk)
        self.assertEqual(Box.objects.get(pk=self.box.pk).status, Box.Status.INACTIVE)
        self.assert_history_preserved("pending_review")
        self.assert_correction_audit(self.original_audit_values, {**self.original_audit_values, "polypes": 7})

    def test_pending_projection_stays_excluded_after_counts_reset_and_later_notes(self):
        point = historical_temperature_point(**self.projection_values())
        self.assertEqual(point["temperature_c"], "0.000")
        self.assertEqual(point["evidence_count"], 2)
        self.assertEqual((point["iso_year"], point["iso_week"]), (2026, 1))
        self.assertIsNone(point["measured_on"])
        self.correct({"polyp_count": 7, "ephyrae_count": 3})
        self.assertIsNone(historical_temperature_point(**self.projection_values()))
        self.correct({"polyp_count": 0, "ephyrae_count": 0})
        self.assert_history_preserved("pending_review")
        self.assertIsNone(historical_temperature_point(**self.projection_values()))
        self.assert_correction_audit(
            {**self.original_audit_values, "polypes": 7, "ephyrules": 3},
            self.original_audit_values, previous_status="pending_review",
        )
        self.correct({"notes": "Synthetic review remains outstanding"})
        self.assert_correction_audit(
            self.original_audit_values,
            {**self.original_audit_values, "note": "Synthetic review remains outstanding"},
            previous_status="pending_review",
        )
        self.assert_history_preserved("pending_review")
        self.assertIsNone(historical_temperature_point(**self.projection_values()))

    def test_return_to_original_date_does_not_reconfirm_association(self):
        self.correct({"measured_on": "2026-01-05"})
        corrected = self.correct({"measured_on": "2026-01-01"})
        self.assertEqual(corrected.week_start, self.original_biology["week_start"])
        self.assert_history_preserved("pending_review")
        self.assert_correction_audit(
            {**self.original_audit_values, "date": "2026-01-05"},
            self.original_audit_values, previous_status="pending_review",
        )
        self.assertIsNone(historical_temperature_point(**self.projection_values()))

    def test_pending_importer_replay_is_stale_then_requires_review_after_numbers_reset(self):
        self.correct({"polyp_count": 7, "ephyrae_count": 3})
        before = self.state()
        with self.assertRaisesMessage(PartialImportError, "stale_baseline:biological_measurements"):
            self.accept()
        self.assertEqual(self.state(), before)
        self.correct({"polyp_count": 0, "ephyrae_count": 0})
        self.assertEqual(apply_tests._current_snapshot(self.organization.pk), {
            key: self.snapshot[key] for key in ("boxes", "biological_measurements")
        })
        before = self.state()
        for _ in range(2):
            with self.assertRaisesMessage(PartialImportError, "pending_association_requires_review"):
                self.accept()
            self.assertEqual(self.state(), before)
        self.assert_history_preserved("pending_review")
        self.assertEqual(HistoricalTemperatureReading.objects.count(), 1)
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 2)
        self.assertEqual(HistoricalTemperatureImportReceipt.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(object_type="historical_temperature_import").count(), 1)
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.UPDATE).count(), 2)

    def test_projection_requires_persisted_status_and_rejects_unknown_status(self):
        values = self.projection_values()
        values.pop("association_status")
        with self.assertRaises(TypeError):
            historical_temperature_point(**values)
        for status in ("", "unknown", None):
            with self.subTest(status=status), self.assertRaises(ValueError):
                historical_temperature_point(**values, association_status=status)

    def test_new_readings_cannot_start_pending_or_with_unknown_status(self):
        biology = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 1, 5), polyp_count=0, ephyrae_count=0,
        )
        before = self.state()
        for status in ("pending_review", "unknown"):
            with self.subTest(status=status), self.assertRaises(ValidationError):
                HistoricalTemperatureReading.objects.create(
                    biological_measurement=biology, iso_year=2026, iso_week=2,
                    temperature_c="0", association_status=status,
                )
            self.assertEqual(self.state(), before)

    def test_pending_reading_evidence_and_receipt_remain_orm_immutable(self):
        self.correct({"polyp_count": 7})
        self.correct({"polyp_count": 0})
        self.reading.refresh_from_db()
        before = self.state()
        mutations = (
            (self.reading, {"association_status": "confirmed"}),
            (self.reading, {"temperature_c": "99", "iso_week": 2}),
            (self.reading.evidences.first(), {"polyp_value": "7", "source_value": "99"}),
            (self.receipt, {"source_archive": b"changed-synthetic-source", "accepted_plan": {}}),
        )
        for item, changes in mutations:
            with self.subTest(model=type(item).__name__, changes=changes):
                for field, value in changes.items():
                    setattr(item, field, value)
                with self.assertRaises(ValidationError):
                    item.save()
                with self.assertRaises(ValidationError):
                    type(item).objects.filter(pk=item.pk).update(**changes)
                with self.assertRaises(ValidationError):
                    item.delete()
                self.assertEqual(self.state(), before)
        self.reading.refresh_from_db()
        with self.assertRaises(ValidationError):
            HistoricalTemperatureEvidence.objects.create(
                reading=self.reading, **evidence(source_value="0.000", polyp_cell="F11", ephyrae_cell="F12"),
            )
        self.assertEqual(self.state(), before)
