"""Snapshot consistency under concurrent condensation, and the mean benchmark.

The concurrency class needs config.test_settings with POLYPBASE_TEST_POSTGRES=1
and the disposable QA endpoint; it never starts a server or uses a shared
database. The benchmark is opt-in through POLYPBASE_PROBE_BENCHMARK=1 and only
reports measurements, never an asserted end-to-end speed claim.
"""

import json
import os
import threading
import time
from datetime import timedelta
from decimal import Decimal
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import OrganizationMembership

from . import probe_readings
from .models import ProbeTemperatureBucket, TemperatureMeasurement
from .probe_collection import condense_probe_readings
from .test_probe_aggregation import DISPOSABLE_POSTGRES, ORIGIN, ProbeAggregationFixture

# The single read statement is slowed down so the condensation below commits
# while it is still running. Only the test's copy of the statement is changed.
SLOW_AVERAGE_SQL = probe_readings.AVERAGE_SQL.replace(
    "request (requested_seconds) AS (VALUES (%s::integer)),",
    "request (requested_seconds) AS (SELECT %s::integer + (SELECT 0 FROM pg_sleep(0.6))),",
)
assert SLOW_AVERAGE_SQL != probe_readings.AVERAGE_SQL, "the slow statement must differ"


class _patched_average_sql:
    """Temporarily replace the module-level statement used by the read."""

    def __init__(self, sql):
        self.sql = sql

    def __enter__(self):
        self.original = probe_readings.AVERAGE_SQL
        probe_readings.AVERAGE_SQL = self.sql
        return self

    def __exit__(self, *exc):
        probe_readings.AVERAGE_SQL = self.original
        return False


@skipUnless(DISPOSABLE_POSTGRES, "Requires opt-in disposable PostgreSQL at 127.0.0.1:55439.")
class PostgresSnapshotConsistencyTests(ProbeAggregationFixture, TransactionTestCase):
    """A condensation committed mid-statement cannot mix two snapshots.

    The response is one SQL statement, so PostgreSQL gives it one snapshot: a
    reading is read from exactly one tier. The test slows the statement down,
    commits a real condensation while it runs, and checks that no reading is
    counted twice or lost.
    """

    def setUp(self):
        super().setUp()
        self.old = ORIGIN - timedelta(days=30)
        self.readings = 30
        for index in range(self.readings):
            self.minute(self.left, self.old + timedelta(minutes=index), Decimal("20.00"))
            self.minute(self.right, self.old + timedelta(minutes=index), Decimal("22.00"))
        self.start = self.old - timedelta(minutes=5)
        self.end = self.old + timedelta(hours=1)

    def probe_counts(self, series):
        return {
            probe.pk: sum(source[0][3] for source in series[probe.pk][1])
            for probe in (self.left, self.right)
        }

    def test_condensation_committed_during_the_read_keeps_one_snapshot(self):
        outcome = {}

        def read():
            try:
                outcome["result"] = probe_readings.zone_probe_series_and_average(
                    [self.left, self.right], self.start, self.end, group_sources=True,
                )
            except BaseException as error:  # noqa: BLE001 - surfaced on the main thread
                outcome["error"] = error

        reader = threading.Thread(target=read)
        with _patched_average_sql(SLOW_AVERAGE_SQL):
            reader.start()
            self._wait_for_active_statement()
            condensed = condense_probe_readings(
                now=timezone.now(), retention_days=14, bucket_minutes=15,
            )
            reader.join(timeout=30)
        self.assertFalse(reader.is_alive(), "the read must finish")
        if "error" in outcome:
            raise outcome["error"]
        self.assertGreater(condensed.readings_condensed, 0, "the condensation must commit")

        resolution, series, average = outcome["result"]
        self.assertEqual(resolution, 15)
        # Every reading is counted exactly once, whether it was read as a raw
        # row or as a stored interval: no double counting, nothing missing.
        self.assertEqual(self.probe_counts(series), {self.left.pk: self.readings, self.right.pk: self.readings})
        # Two 15-minute periods, each fed by both probes, at the exact mean.
        self.assertEqual([point[2] for point in average], [2, 2])
        self.assertEqual({point[1] for point in average}, {21.0})
        # The condensation really committed: the raw rows are now buckets.
        self.assertFalse(TemperatureMeasurement.objects.filter(probe=self.left).exists())
        self.assertTrue(ProbeTemperatureBucket.objects.filter(probe=self.left).exists())

    def _wait_for_active_statement(self):
        deadline = time.monotonic() + 10
        seen = []
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pid, state, query FROM pg_stat_activity"
                    " WHERE pid <> pg_backend_pid() AND datname = current_database()",
                )
                seen = cursor.fetchall()
                # pg_stat_activity truncates the query text, so match its start.
                if any(row[1] == "active" and "request (requested_seconds)" in (row[2] or "")
                       for row in seen):
                    return
            time.sleep(0.02)
        self.fail(f"the read statement never became active: {seen}")


BENCHMARK = os.getenv("POLYPBASE_PROBE_BENCHMARK") == "1"


@skipUnless(DISPOSABLE_POSTGRES and BENCHMARK, "Set POLYPBASE_PROBE_BENCHMARK=1 on disposable PostgreSQL.")
class ProbeAverageBenchmark(ProbeAggregationFixture, TransactionTestCase):
    """Measure the corrected pipeline with and without the mean. No speed claim."""

    def setUp(self):
        super().setUp()
        self.left.sampling_interval_seconds = 60
        self.left.save()
        self.right.sampling_interval_seconds = 600
        self.right.save()
        self.start = ORIGIN
        self.end = ORIGIN + timedelta(days=3)
        rows = []
        for index in range(3 * 24 * 60):
            rows.append(TemperatureMeasurement(
                probe=self.left, measured_at=ORIGIN + timedelta(minutes=index),
                temperature_c=Decimal(index % 41 - 7) / Decimal(4),
            ))
        for index in range(3 * 24 * 6):
            rows.append(TemperatureMeasurement(
                probe=self.right, measured_at=ORIGIN + timedelta(minutes=10 * index, seconds=17),
                temperature_c=Decimal("21.33") - index % 5,
            ))
        TemperatureMeasurement.objects.bulk_create(rows, batch_size=2000)
        user = get_user_model().objects.create_user(username="bench", email="bench@example.org")
        OrganizationMembership.objects.create(
            user=user, organization=self.organization, role=OrganizationMembership.Role.LAB_TECHNICIAN,
        )
        self.client = APIClient()
        self.client.force_authenticate(user)
        self.headers = {"HTTP_X_ORGANIZATION_ID": str(self.organization.pk)}
        self.url = f"/api/thermal-zones/{self.zone.pk}/probe-readings/"
        self.params = {"start": self.start.isoformat(), "end": self.end.isoformat(), "group_sources": "1"}

    def measure(self, series_only):
        with CaptureQueriesContext(connection) as queries:
            started = time.perf_counter()
            if series_only:
                resolution, series = probe_readings.zone_probe_series(
                    [self.left, self.right], self.start, self.end, group_sources=True,
                )
                average = []
            else:
                resolution, series, average = probe_readings.zone_probe_series_and_average(
                    [self.left, self.right], self.start, self.end, group_sources=True,
                )
            elapsed = time.perf_counter() - started
        # Partial payload only (series and average); the full API response size
        # is reported separately as api_response_bytes.
        payload = json.dumps({"resolution_minutes": resolution, "series": {
            str(probe.pk): series[probe.pk] for probe in (self.left, self.right)}, "probe_average": average})
        return {
            "queries": len(queries),
            "db_seconds": sum(float(query["time"]) for query in queries),
            "backend_seconds": elapsed,
            "response_bytes": len(payload.encode("utf-8")),
            "points": sum(len(series[probe.pk][0]) for probe in (self.left, self.right)),
            "average_points": len(average),
        }

    def test_report_pipeline_measurements(self):
        # Warm up both paths so the comparison is not dominated by first-run effects.
        self.measure(series_only=True)
        self.measure(series_only=False)
        before = self.measure(series_only=True)
        after = self.measure(series_only=False)
        response = self.client.get(self.url, self.params, **self.headers)
        self.assertEqual(response.status_code, 200)
        report = {
            "dataset": {"left_minutes": 3 * 24 * 60, "right_minutes": 3 * 24 * 6, "window_days": 3},
            "series_only": before,
            "series_and_average": after,
            "api_response_bytes": len(response.content),
            "api_average_points": len(response.json()["probe_average"]),
        }
        print("PROBE_AVERAGE_BENCHMARK " + json.dumps(report, sort_keys=True))
