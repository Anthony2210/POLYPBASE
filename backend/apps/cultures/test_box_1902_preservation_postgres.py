"""Isolated PostgreSQL proofs for the protected Box 1902 preservation capability."""

from datetime import date, timedelta
from unittest import skipUnless

from django.db import DatabaseError, connection, transaction
from django.test import TransactionTestCase

from apps.audit.models import AuditLog
from apps.measurements.models import (
    BiologicalMeasurement, HistoricalTemperatureEvidence, HistoricalTemperatureReading,
)
from apps.taxonomy.models import Strain

from .models import Box
from .test_cla_box_1902 import ClaBox1902Fixture
from .test_identity_reconciliation_postgres import PostgreSQLFixture


@skipUnless(connection.vendor == "postgresql", "Requires the disposable native PostgreSQL cluster.")
class Box1902PreservationPostgreSQLTests(ClaBox1902Fixture, PostgreSQLFixture, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        TransactionTestCase.setUp(self)
        self.assertFalse(connection.in_atomic_block)
        self.create_fixture()
        self.target = Strain.objects.create(
            pk=285, organization=self.org, species=self.target_species, code="CLC-JKA-2",
            origin_code="JKA", number=2,
        )
        self.readings = []
        for week in range(12, 53):
            biology = BiologicalMeasurement.objects.create(
                box=self.box, measured_on=date.fromisocalendar(2025, week, 1),
                polyp_count=0, ephyrae_count=0, strobila_count=None,
                salinity_psu=None, user=self.admin,
            )
            with transaction.atomic():
                reading = HistoricalTemperatureReading.objects.create(
                    biological_measurement=biology, iso_year=2025, iso_week=week,
                    temperature_c="0.0000",
                )
                HistoricalTemperatureEvidence.objects.create(
                    reading=reading, source_file="Suivi_2025.xlsx",
                    source_sha256="0" * 64, sheet="Semaestomeae",
                    temperature_cell="C61", temperature_merged_range="C61:C62",
                    source_value="0.0000", source_box="CLA-JKA-1.09",
                    polyp_cell="Q61", polyp_value="0",
                    ephyrae_cell="Q62", ephyrae_value="0",
                    week_cell="Q1", week_value=str(week),
                    source_iso_year=2025, source_iso_week=week,
                )
            self.readings.append(reading.pk)

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM public.box_1902_preservation_context_v1")
            cursor.execute("DELETE FROM public.box_1902_approval_v1")
        super().tearDown()

    def _approve(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.box_1902_approval_v1 "
                "(singleton, operation, document, manifest_hash, expires_at) "
                "VALUES (true, %s, %s::jsonb, %s, clock_timestamp() + interval '1 hour')",
                ["box_1902_preservation_20261109_v1", "{}", "a" * 64],
            )

    def _apply(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT public.box_1902_preserve_v1(%s)", [self.admin.pk])

    def _reading_statuses(self):
        return dict(HistoricalTemperatureReading.objects.filter(pk__in=self.readings)
                    .values_list("pk", "association_status"))

    def _evidence_rows(self):
        return list(HistoricalTemperatureEvidence.objects.filter(reading_id__in=self.readings)
                    .order_by("pk").values())

    def test_exact_correction_preserves_all_41_confirmations(self):
        before_evidence = self._evidence_rows()
        before_measurements = list(BiologicalMeasurement.objects.filter(box=self.box)
                                   .order_by("pk").values())
        self._approve()
        self._apply()
        self.box.refresh_from_db()
        self.assertEqual(self.box.global_code, "CLC-JKA-1.009")
        self.assertEqual(self.box.strain_id, 285)
        self.assertEqual(self.box.local_code, "CLA-JKA-1.09")
        self.assertEqual(self.box.status, Box.Status.INACTIVE)
        self.assertEqual(self._reading_statuses(), {pk: "confirmed" for pk in self.readings})
        self.assertEqual(self._evidence_rows(), before_evidence)
        self.assertEqual(list(BiologicalMeasurement.objects.filter(box=self.box)
                              .order_by("pk").values()), before_measurements)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM public.box_1902_preservation_context_v1")
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute("SELECT count(*) FROM public.box_1902_approval_v1 WHERE consumed_at IS NOT NULL")
            self.assertEqual(cursor.fetchone()[0], 1)
        self.assertTrue(AuditLog.objects.filter(
            organization=self.org, object_type="box", object_id="CLC-JKA-1.009",
            metadata__operation="box_1902_preservation_20261109_v1",
        ).exists())

    def test_replay_is_rejected_without_new_writes(self):
        self._approve()
        self._apply()
        before = self._reading_statuses()
        with self.assertRaises(DatabaseError):
            self._apply()
        self.assertEqual(self._reading_statuses(), before)

    def test_direct_sql_without_approval_is_rejected(self):
        with self.assertRaises(DatabaseError):
            self._apply()
        self.box.refresh_from_db()
        self.assertEqual(self.box.global_code, "CLA-JKA-1.009")
        self.assertEqual(self._reading_statuses(), {pk: "confirmed" for pk in self.readings})

    def test_ordinary_identity_change_still_invalidates(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE public.cultures_box SET global_code = 'CLC-JKA-1.009', strain_id = %s "
                "WHERE id = 1902 AND organization_id = 1",
                [self.target.pk],
            )
        self.assertEqual(self._reading_statuses(), {pk: "pending_review" for pk in self.readings})

    def test_other_boxes_and_organizations_are_untouched(self):
        before = list(Box.objects.order_by("pk").values())
        self._approve()
        self._apply()
        after = list(Box.objects.order_by("pk").values())
        changed = [row for row in after if row not in before]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["id"], 1902)
