"""Five preservation-prototype scenarios, executed by the genuine-role v2 class.

The former public *_v1 helpers/tables no longer exist. Each method keeps its
original science/identity assertions and uses the real independent v2 approval
and writer. V2 retains validated private context as immutable provenance instead
of deleting the prototype's temporary context.
"""

import psycopg
from django.db import connection

from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement, HistoricalTemperatureEvidence, HistoricalTemperatureReading

from .box_1902_test_support import runtime_test
from .models import Box


class Box1902PreservationPostgreSQLTests:
    def _reading_statuses(self):
        return dict(HistoricalTemperatureReading.objects.filter(pk__in=self.reading_ids)
                    .values_list("pk", "association_status"))

    def _evidence_rows(self):
        return list(HistoricalTemperatureEvidence.objects.filter(reading_id__in=self.reading_ids)
                    .order_by("pk").values())

    @runtime_test
    def test_exact_correction_preserves_all_41_confirmations(self):
        before_evidence = self._evidence_rows()
        before_measurements = list(BiologicalMeasurement.objects.filter(box=self.box).order_by("pk").values())
        digest = self.approve(plan=self.plan())
        before = self.snapshot()
        self.write(digest)
        self.assert_preserved(before)
        self.box.refresh_from_db()
        self.assertEqual(self.box.global_code, "CLC-JKA-1.009")
        self.assertEqual(self.box.strain_id, 285)
        self.assertEqual(self.box.local_code, "CLA-JKA-1.09")
        self.assertEqual(self.box.status, Box.Status.INACTIVE)
        self.assertEqual(self._reading_statuses(), {pk: "confirmed" for pk in self.reading_ids})
        self.assertEqual(self._evidence_rows(), before_evidence)
        self.assertEqual(list(BiologicalMeasurement.objects.filter(box=self.box).order_by("pk").values()),
                         before_measurements)
        self.assertEqual(self.execute("SELECT count(*) FROM box1902_private.context_v2 WHERE validated"), [(1,)])
        self.assertEqual(self.execute("SELECT count(*) FROM box1902_private.approval_v2 WHERE consumed_at IS NOT NULL"), [(1,)])
        self.assertTrue(AuditLog.objects.filter(
            organization=self.org, object_type="box", object_id="CLC-JKA-1.009",
            metadata__operation="cla_box_1902_preservation_20261109_v2",
        ).exists())

    @runtime_test
    def test_replay_is_rejected_without_new_writes(self):
        digest = self.approve(plan=self.plan())
        self.write(digest)
        before = self.snapshot()
        statuses = self._reading_statuses()
        with self.assertRaises(psycopg.Error):
            self.write(digest)
        self.assertEqual(self._reading_statuses(), statuses)
        self.assertEqual(self.snapshot(), before)

    @runtime_test
    def test_direct_sql_without_approval_is_rejected(self):
        before = self.snapshot()
        with self.assertRaises(psycopg.Error):
            self.write("a" * 64)
        self.box.refresh_from_db()
        self.assertEqual(self.box.global_code, "CLA-JKA-1.009")
        self.assertEqual(self._reading_statuses(), {pk: "confirmed" for pk in self.reading_ids})
        self.assertEqual(self.snapshot(), before)

    @runtime_test
    def test_ordinary_identity_change_still_invalidates(self):
        with connection.cursor() as cursor:
            cursor.execute("UPDATE public.cultures_box SET global_code='CLC-JKA-1.009', strain_id=285 "
                           "WHERE id=1902 AND organization_id=1")
        self.assertEqual(self._reading_statuses(), {pk: "pending_review" for pk in self.reading_ids})

    @runtime_test
    def test_other_boxes_and_organizations_are_untouched(self):
        before = list(Box.objects.order_by("pk").values())
        digest = self.approve(plan=self.plan())
        snapshot = self.snapshot()
        self.write(digest)
        self.assert_preserved(snapshot)
        after = list(Box.objects.order_by("pk").values())
        changed = [row for row in after if row not in before]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["id"], 1902)
