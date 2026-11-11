"""Database grouping of probe series against the Python reference path.

The PostgreSQL class needs config.test_settings with POLYPBASE_TEST_POSTGRES=1
and the disposable QA endpoint; it never starts a server or uses a shared
database. SQLite only proves that it keeps the reference path.
"""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.organizations.models import Organization
from apps.cultures.models import ThermalZone

from . import probe_readings
from .models import Probe, ProbeTemperatureBucket, TemperatureMeasurement
from .test_probe_collection import ProbeFixtureMixin

DISPOSABLE_POSTGRES = (
    os.getenv("POLYPBASE_TEST_POSTGRES") == "1"
    and connection.vendor == "postgresql"
    and connection.settings_dict.get("HOST") == "127.0.0.1"
    and str(connection.settings_dict.get("PORT")) == "55439"
    and connection.settings_dict.get("NAME") in {"subculture_qa", "test_subculture_qa"}
)
# Aligned on every resolution, well before the raw retention limit of the reads.
ORIGIN = datetime(2026, 9, 1, tzinfo=UTC)


def reference_series(probes, start, end):
    """The Python path, which reads one row per raw reading."""
    with patch.object(probe_readings, "sql_grouping_available", return_value=False):
        return probe_readings.zone_probe_series(probes, start, end, group_sources=True)


class ProbeAggregationFixture(ProbeFixtureMixin):
    def setUp(self):
        super().setUp()
        self.left.sampling_interval_seconds = 60
        self.left.save()
        self.right.sampling_interval_seconds = 600
        self.right.save()

    def minute(self, probe, at, value):
        TemperatureMeasurement.objects.create(probe=probe, measured_at=at, temperature_c=Decimal(value), source="test")

    def bucket(self, probe, start, minutes, values, last_offset=None):
        values = [Decimal(value) for value in values]
        total = sum(values, Decimal(0))
        last = start + (last_offset if last_offset is not None else timedelta(minutes=minutes - 1))
        ProbeTemperatureBucket.objects.create(
            probe=probe, period_start=start, period_minutes=minutes, last_measured_at=last,
            last_temperature_c=None, min_temperature_c=min(values), max_temperature_c=max(values),
            average_temperature_c=(total / len(values)).quantize(Decimal("0.01")),
            measurement_count=len(values), temperature_sum_c=total,
        )

    def representative_history(self):
        # Left: one reading per minute over three days, with zero, negatives and a 2 h gap.
        for index in range(3 * 24 * 60):
            if 600 <= index < 720:
                continue
            value = Decimal("0.00") if index % 97 == 0 else Decimal(index % 41 - 7) / Decimal(4)
            self.minute(self.left, ORIGIN + timedelta(minutes=index), value)
        # Right: an asynchronous 10-minute cadence, offset by 17 seconds, never aligned.
        for index in range(3 * 24 * 6):
            self.minute(self.right, ORIGIN + timedelta(minutes=10 * index, seconds=17), Decimal("21.33") - index % 5)
        # Condensed history before the raw readings, for both probes.
        for index in range(1, 9):
            self.bucket(self.left, ORIGIN - timedelta(minutes=15 * index), 15, ["-1.25", "0.00", "3.10"])
            self.bucket(self.right, ORIGIN - timedelta(minutes=15 * index), 15, ["22.00", "22.50"],
                        last_offset=timedelta(minutes=10, seconds=17))


class SqliteKeepsReferencePathTests(ProbeAggregationFixture, TestCase):
    def test_sqlite_never_groups_in_sql(self):
        if connection.vendor == "postgresql":
            self.skipTest("SQLite behaviour only")
        self.assertFalse(probe_readings.sql_grouping_available(True, 15))
        self.minute(self.left, ORIGIN, "0.00")
        self.minute(self.left, ORIGIN + timedelta(minutes=1), "-0.50")
        resolution, series = probe_readings.zone_probe_series(
            [self.left], ORIGIN, ORIGIN + timedelta(days=3), group_sources=True,
        )
        self.assertEqual(resolution, 15)
        # The zero reading keeps its weight: mean (0 - 0.5) / 2, minimum -0.5, maximum 0.
        self.assertEqual(series[self.left.pk][0], [[int(ORIGIN.timestamp() * 1000), -0.25, -0.5, 0.0]])
        self.assertEqual(series[self.left.pk][1][0][0][2:6], ["-0.50", 2, -0.5, 0.0])

    def test_grouping_is_limited_to_grouped_coarse_responses(self):
        with patch.object(connection, "vendor", "postgresql"):
            self.assertTrue(probe_readings.sql_grouping_available(True, 15))
            self.assertFalse(probe_readings.sql_grouping_available(False, 15))
            self.assertFalse(probe_readings.sql_grouping_available(True, 1))


@skipUnless(DISPOSABLE_POSTGRES, "Requires opt-in disposable PostgreSQL at 127.0.0.1:55439.")
class PostgresGroupingTests(ProbeAggregationFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(connection.settings_dict["NAME"], "test_subculture_qa")
        self.representative_history()
        self.probes = [self.left, self.right]

    def grouped(self, start, end):
        with CaptureQueriesContext(connection) as queries:
            result = probe_readings.zone_probe_series(self.probes, start, end, group_sources=True)
        return result, queries

    def test_grouped_series_equal_the_python_reference(self):
        windows = [
            (ORIGIN - timedelta(hours=2), ORIGIN + timedelta(days=3)),  # 15 min, buckets and raw
            (ORIGIN + timedelta(hours=7, minutes=7), ORIGIN + timedelta(days=2, hours=9, seconds=13)),  # unaligned bounds
            (ORIGIN - timedelta(days=20), ORIGIN + timedelta(days=20)),  # 60 min
            (ORIGIN + timedelta(days=5), ORIGIN + timedelta(days=9)),  # no reading at all
        ]
        for start, end in windows:
            with self.subTest(start=start, end=end):
                (resolution, series), queries = self.grouped(start, end)
                self.assertEqual((resolution, series), reference_series(self.probes, start, end))
                self.assertEqual(len(queries), 1, "Both storage tiers stay one statement")
                self.assertIn("GROUP BY", queries[0]["sql"])

    def test_sql_transfers_one_row_per_period_instead_of_one_per_reading(self):
        start, end = ORIGIN, ORIGIN + timedelta(days=3)
        grouped_rows = probe_readings._zone_series_rows([self.left.pk, self.right.pk], start, end, 15)
        raw_rows = probe_readings._zone_series_rows([self.left.pk, self.right.pk], start, end)
        self.assertEqual(len(raw_rows), 3 * 24 * 60 - 120 + 3 * 24 * 6)
        self.assertEqual(len(grouped_rows), 2 * 3 * 24 * 4 - 8)  # the 2 h gap leaves 8 empty periods
        self.assertTrue(all(row[1] == probe_readings.RAW_GROUP_ROW for row in grouped_rows))

    def test_zero_negative_extrema_and_gaps_are_preserved(self):
        (_resolution, series), _queries = self.grouped(ORIGIN, ORIGIN + timedelta(days=3))
        points, sources = series[self.left.pk]
        quarter = 15 * 60_000
        first = int(ORIGIN.timestamp() * 1000)
        times = [point[0] for point in points]
        # Absent readings are absent periods, never zero-valued points.
        self.assertNotIn(first + 40 * quarter, times)
        self.assertNotIn(first + 47 * quarter, times)
        self.assertIn(first + 39 * quarter, times)
        self.assertIn(first + 48 * quarter, times)
        # The first period holds 0.00 at minute 0 and the negative values that follow.
        values = [Decimal(0)] + [Decimal(index % 41 - 7) / Decimal(4) for index in range(1, 15)]
        total = sum(values, Decimal(0))
        self.assertEqual(sources[0][0][2:6], [str(total.quantize(Decimal("0.01"))), 15, float(min(values)), float(max(values))])
        self.assertEqual(points[0][2:], [float(min(values)), float(max(values))])
        self.assertEqual(sources[0][0][6], first + 14 * 60_000, "Latest real instant, not the period end")

    def test_asynchronous_probes_keep_their_own_instants(self):
        (_resolution, series), _queries = self.grouped(ORIGIN, ORIGIN + timedelta(days=3))
        right = series[self.right.pk][1]
        # One reading per 10 minutes: periods hold one or two readings, never another probe's.
        self.assertEqual({source[0][3] for source in right}, {1, 2})
        self.assertTrue(all((source[0][6] - int(ORIGIN.timestamp() * 1000)) % 60_000 == 17_000 for source in right))

    def test_condensed_intervals_stay_whole_and_marked(self):
        start = ORIGIN - timedelta(minutes=50)
        (_resolution, series), _queries = self.grouped(start, ORIGIN + timedelta(days=3))
        first = series[self.left.pk][1][0][0]
        # The first stored interval starts before the request and is reported whole.
        self.assertEqual(first[0], int((ORIGIN - timedelta(minutes=60)).timestamp() * 1000))
        self.assertEqual(first[1], int((ORIGIN - timedelta(minutes=45)).timestamp() * 1000))
        self.assertEqual(first[2:6], ["1.85", 3, -1.25, 3.1])

    def test_a_coarser_unaligned_interval_raises_the_period_in_one_statement(self):
        # A stored 20-minute interval does not divide the 15-minute request:
        # the period becomes their least common multiple (60), so the interval
        # fits one period whole and is never split or misattributed. The whole
        # response still comes from one statement, so no second read can see a
        # different snapshot.
        self.bucket(self.left, ORIGIN - timedelta(days=1), 20, ["5.00"])
        start, end = ORIGIN - timedelta(days=2), ORIGIN + timedelta(days=3)
        (resolution, series), queries = self.grouped(start, end)
        self.assertEqual(resolution, 60)
        self.assertEqual(len(queries), 1, "No second read is needed")
        self.assertEqual((resolution, series), reference_series(self.probes, start, end))

    def test_a_stored_interval_never_spans_two_periods(self):
        # 20- and 15-minute stored intervals with a 15-minute request: the
        # period is their least common multiple, so each interval stays inside
        # exactly one period instead of being assigned to its start period.
        self.bucket(self.left, ORIGIN + timedelta(minutes=15), 15, ["30.00"])
        self.bucket(self.right, ORIGIN, 20, ["10.00"])
        start, end = ORIGIN, ORIGIN + timedelta(days=1)
        (resolution, series), queries = self.grouped(start, end)
        self.assertEqual(resolution, 60)
        self.assertEqual(len(queries), 1)
        for probe in self.probes:
            points, sources = series[probe.pk]
            for point, group in zip(points, sources):
                period_start = point[0]
                for source in group:
                    self.assertGreaterEqual(source[0], period_start)
                    self.assertLessEqual(source[1], period_start + resolution * 60_000)

    def test_other_organizations_and_zones_never_contribute(self):
        other = Organization.objects.create(name="Other lab")
        zone = ThermalZone.objects.create(organization=other, name="Other", target_temperature_c=Decimal("20.0"))
        stranger = Probe.objects.create(
            organization=other, thermal_zone=zone, code="X", probe_type=Probe.ProbeType.IMINILIDE,
            external_serial="999", external_channel=1,
        )
        self.minute(stranger, ORIGIN + timedelta(seconds=30), "99.00")
        start, end = ORIGIN, ORIGIN + timedelta(days=3)
        (_resolution, series), _queries = self.grouped(start, end)
        self.assertNotIn(stranger.pk, series)
        self.assertNotIn(99.0, [point[3] for points, _sources in series.values() for point in points])
