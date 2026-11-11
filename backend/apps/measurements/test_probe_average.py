"""Inter-probe average of the zone series (decision of 10 November 2026).

mean_i(P) = sum of probe i's observations in P / their count (whole stored
intervals weighted by their exact count); mean_zone(P) = average of mean_i(P)
over contributing probes, with at least two probes. References below are
computed independently with exact fractions from the fixture values.

Every class runs on SQLite (Python reference path) and, with the disposable
PostgreSQL settings, on PostgreSQL where the same statement computes it.
"""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.cultures.models import ThermalZone
from apps.accounts.models import OrganizationMembership
from apps.organizations.models import Organization

from . import probe_readings
from .models import Probe, ProbeTemperatureBucket, TemperatureMeasurement
from .test_probe_aggregation import DISPOSABLE_POSTGRES, ORIGIN, ProbeAggregationFixture

MINUTE = timedelta(minutes=1)


def ms(moment):
    return int(moment.timestamp() * 1000)


def exact_mean(values):
    """Exact mean of decimal strings, as a fraction."""
    return sum((Fraction(Decimal(value)) for value in values), Fraction(0)) / len(values)


def displayed(fraction):
    """Server display rounding (0.01, half up) of an exact fraction."""
    with localcontext() as context:
        context.prec = 80
        value = Decimal(fraction.numerator) / Decimal(fraction.denominator)
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def zone_point(period_start, probe_means):
    """Expected average point from each contributing probe's exact mean."""
    return [ms(period_start), displayed(sum(probe_means, Fraction(0)) / len(probe_means)), len(probe_means)]


class ProbeAverageFixture(ProbeAggregationFixture):
    def setUp(self):
        super().setUp()
        self.third = Probe.objects.create(
            organization=self.organization, thermal_zone=self.zone, code="ETUVE 25 C",
            probe_type=Probe.ProbeType.IMINILIDE, external_serial="1162040825", external_channel=13,
        )
        self.probes = [self.left, self.right, self.third]

    def readings(self, probe, start, values, step=MINUTE):
        TemperatureMeasurement.objects.bulk_create(
            TemperatureMeasurement(probe=probe, measured_at=start + index * step, temperature_c=Decimal(value))
            for index, value in enumerate(values)
        )

    def average(self, start, end, *, group_sources=True, probes=None):
        with CaptureQueriesContext(connection) as queries:
            resolution, series, average = probe_readings.zone_probe_series_and_average(
                self.probes if probes is None else probes, start, end, group_sources=group_sources,
            )
        # Series and average come from one statement; no probe reads nothing.
        self.assertEqual(len(queries), 0 if probes == [] else 1)
        if connection.vendor == "postgresql" and queries:
            self.assertIn("zone_periods", queries[0]["sql"])
        return resolution, series, average

    def assertAverage(self, start, end, expected, resolution=15):
        # Grouped and individual sources share the same definition.
        for group_sources in (True, False):
            with self.subTest(group_sources=group_sources):
                got_resolution, _series, average = self.average(start, end, group_sources=group_sources)
                self.assertEqual(got_resolution, resolution)
                self.assertEqual(average, expected)


class ProbeAverageSemanticsTests(ProbeAverageFixture, TestCase):
    """Scenarios 1 to 13 of the specification, at 15-minute periods unless stated."""

    day = (ORIGIN, ORIGIN + timedelta(days=1))

    def test_same_number_of_readings(self):
        left = ["20.10", "20.20", "20.30"]
        right = ["21.00", "21.40", "21.50"]
        self.readings(self.left, ORIGIN, left)
        self.readings(self.right, ORIGIN, right)
        self.assertAverage(*self.day, [zone_point(ORIGIN, [exact_mean(left), exact_mean(right)])])

    def test_different_numbers_of_readings_keep_equal_probe_weights(self):
        left = ["20.00"] * 15
        right = ["22.00"]
        self.readings(self.left, ORIGIN, left)
        self.readings(self.right, ORIGIN, right)
        # 21.00, never the pooled (15 × 20 + 22) / 16 = 20.125.
        self.assertAverage(*self.day, [[ms(ORIGIN), 21.0, 2]])

    def test_different_cadences(self):
        left = [f"{20 + index / 100:.2f}" for index in range(60)]  # every minute for one hour
        right = ["23.00", "23.10", "23.20", "23.30", "23.40", "23.50"]  # every 10 minutes
        self.readings(self.left, ORIGIN, left)
        self.readings(self.right, ORIGIN, right, step=timedelta(minutes=10))
        expected = []
        for quarter in range(4):
            period = ORIGIN + quarter * timedelta(minutes=15)
            left_values = left[15 * quarter:15 * quarter + 15]
            right_values = [value for index, value in enumerate(right) if 15 * quarter <= 10 * index < 15 * quarter + 15]
            expected.append(zone_point(period, [exact_mean(left_values), exact_mean(right_values)]))
        self.assertEqual([point[2] for point in expected], [2, 2, 2, 2])
        self.assertAverage(*self.day, expected)

    def test_three_probes_with_one_silent_in_a_period(self):
        self.readings(self.left, ORIGIN, ["20.00", "20.50"])
        self.readings(self.right, ORIGIN, ["22.00"])
        self.readings(self.third, ORIGIN + timedelta(minutes=15), ["30.00"])
        self.readings(self.left, ORIGIN + timedelta(minutes=15), ["21.00"])
        self.assertAverage(*self.day, [
            zone_point(ORIGIN, [exact_mean(["20.00", "20.50"]), Fraction(22)]),
            [ms(ORIGIN + timedelta(minutes=15)), 25.5, 2],
        ])

    def test_a_single_contributing_probe_gives_no_point(self):
        self.readings(self.left, ORIGIN, ["20.00", "20.50", "21.00"])
        self.readings(self.right, ORIGIN + timedelta(minutes=30), ["22.00"])
        self.assertAverage(*self.day, [])

    def test_no_contributing_probe_gives_no_point(self):
        self.assertAverage(*self.day, [])
        _resolution, _series, average = self.average(*self.day, probes=[])
        self.assertEqual(average, [])

    def test_zero_is_a_measurement(self):
        self.readings(self.left, ORIGIN, ["0.00", "0.00"])
        self.readings(self.right, ORIGIN, ["1.00"])
        # Zero counts: (0 + 1) / 2, not 1 from a probe treated as missing.
        self.assertAverage(*self.day, [[ms(ORIGIN), 0.5, 2]])

    def test_negative_values(self):
        self.readings(self.left, ORIGIN, ["-3.25", "-2.75"])
        self.readings(self.right, ORIGIN, ["-0.01"])
        self.readings(self.third, ORIGIN, ["1.00"])
        self.assertAverage(*self.day, [zone_point(ORIGIN, [Fraction(-3), Fraction("-0.01"), Fraction(1)])])

    def test_asynchronous_readings_share_a_period_without_alignment(self):
        self.readings(self.left, ORIGIN + timedelta(seconds=3), ["20.00"])
        self.readings(self.right, ORIGIN + timedelta(minutes=7, seconds=31), ["21.00"])
        self.assertAverage(*self.day, [[ms(ORIGIN), 20.5, 2]])

    def test_condensed_intervals_are_weighted_by_their_counts(self):
        # 60-minute periods: two stored quarters of the left probe hold 15 and 3 readings.
        self.bucket(self.left, ORIGIN, 15, ["20.00"] * 15)
        self.bucket(self.left, ORIGIN + timedelta(minutes=15), 15, ["26.00"] * 3)
        self.bucket(self.right, ORIGIN + timedelta(minutes=30), 15, ["22.00", "22.50"])
        left_mean = exact_mean(["20.00"] * 15 + ["26.00"] * 3)  # 21, not (20 + 26) / 2 = 23
        self.assertEqual(left_mean, 21)
        window = (ORIGIN - timedelta(days=20), ORIGIN + timedelta(days=20))
        self.assertAverage(*window, [zone_point(ORIGIN, [left_mean, exact_mean(["22.00", "22.50"])])], resolution=60)

    def test_raw_and_condensed_readings_of_one_probe_combine_exactly(self):
        self.bucket(self.left, ORIGIN, 15, ["19.00"] * 15)
        self.readings(self.left, ORIGIN + timedelta(minutes=15), ["25.00", "25.00", "25.00"])
        self.readings(self.right, ORIGIN + timedelta(minutes=50), ["24.00"])
        left_mean = exact_mean(["19.00"] * 15 + ["25.00"] * 3)
        window = (ORIGIN - timedelta(days=20), ORIGIN + timedelta(days=20))
        self.assertAverage(*window, [zone_point(ORIGIN, [left_mean, Fraction(24)])], resolution=60)

    def test_no_reading_is_counted_twice_or_in_two_periods(self):
        # 00:15:00 opens the second period; it is not in the first one.
        self.readings(self.left, ORIGIN + timedelta(minutes=14), ["20.00", "40.00"])
        self.readings(self.right, ORIGIN, ["30.00"])
        self.readings(self.right, ORIGIN + timedelta(minutes=15), ["30.00"])
        resolution, series, average = self.average(*self.day)
        self.assertEqual(average, [[ms(ORIGIN), 25.0, 2], [ms(ORIGIN + timedelta(minutes=15)), 35.0, 2]])
        # Same per-probe counts as the probe series of the same response.
        counts = [source[0][3] for source in series[self.left.pk][1]]
        self.assertEqual(counts, [1, 1])

    def test_request_bounds_and_partial_periods(self):
        self.readings(self.left, ORIGIN, ["10.00"] * 15)
        self.readings(self.right, ORIGIN, ["30.00"] * 15)
        self.readings(self.left, ORIGIN + timedelta(minutes=15), ["20.00"] * 15)
        self.readings(self.right, ORIGIN + timedelta(minutes=15), ["22.00"] * 15)
        # Starts at 00:07: the first period only keeps the readings inside the request.
        start = ORIGIN + timedelta(minutes=7)
        end = ORIGIN + timedelta(minutes=17)  # excluded end, readings of 00:15 and 00:16 kept
        _resolution, _series, average = self.average(start, ORIGIN + timedelta(days=1))
        self.assertEqual(average[0], [ms(ORIGIN), 20.0, 2])
        self.readings(self.left, ORIGIN + timedelta(minutes=16, seconds=30), ["99.00"])
        with patch.object(probe_readings, "series_resolution_minutes", return_value=15):
            _resolution, _series, average = self.average(start, end)
        self.assertEqual(average, [[ms(ORIGIN), 20.0, 2], zone_point(
            ORIGIN + timedelta(minutes=15), [exact_mean(["20.00", "20.00", "99.00"]), Fraction(22)],
        )])

    def test_stored_interval_crossing_the_start_counts_whole(self):
        self.bucket(self.left, ORIGIN, 15, ["10.00", "20.00"])
        self.bucket(self.right, ORIGIN, 15, ["30.00"])
        start = ORIGIN + timedelta(minutes=10)
        _resolution, _series, average = self.average(start, start + timedelta(days=1))
        self.assertEqual(average, [[ms(ORIGIN), 22.5, 2]])

    def test_minute_responses_use_the_minute_of_each_reading(self):
        with patch.object(probe_readings.timezone, "now", return_value=ORIGIN + timedelta(days=1)):
            self.readings(self.left, ORIGIN, ["20.00", "20.10", "20.20"])
            self.readings(self.right, ORIGIN + timedelta(seconds=20), ["21.00"])
            self.readings(self.right, ORIGIN + timedelta(minutes=2, seconds=59), ["22.00"])
            # Different minutes never meet: 00:01 has a single probe.
            self.assertAverage(ORIGIN, ORIGIN + timedelta(hours=2), [
                [ms(ORIGIN), 20.5, 2], [ms(ORIGIN + 2 * MINUTE), 21.1, 2],
            ], resolution=1)


class ProbeAverageScopeTests(ProbeAverageFixture, TestCase):
    """Scenario 14: other zones and organizations never contribute."""

    def test_other_zone_and_other_organization_probes_are_ignored(self):
        other_zone = ThermalZone.objects.create(organization=self.organization, name="ETUVE 30")
        neighbour = Probe.objects.create(
            organization=self.organization, thermal_zone=other_zone, code="ETUVE 30 G",
            probe_type=Probe.ProbeType.IMINILIDE, external_serial="555", external_channel=1,
        )
        other = Organization.objects.create(name="Other lab")
        stranger = Probe.objects.create(
            organization=other, thermal_zone=ThermalZone.objects.create(organization=other, name="ETUVE 25"),
            code="X", probe_type=Probe.ProbeType.IMINILIDE, external_serial="999", external_channel=1,
        )
        for probe in (neighbour, stranger):
            self.readings(probe, ORIGIN, ["90.00"])
        self.readings(self.left, ORIGIN, ["20.00"])
        self.readings(self.right, ORIGIN, ["22.00"])
        user = get_user_model().objects.create_user(username="tech", email="tech@example.org")
        OrganizationMembership.objects.create(
            user=user, organization=self.organization, role=OrganizationMembership.Role.LAB_TECHNICIAN,
        )
        client = APIClient()
        client.force_authenticate(user)
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.organization.pk)}
        url = f"/api/thermal-zones/{self.zone.pk}/probe-readings/"
        params = {"start": ORIGIN.isoformat(), "end": (ORIGIN + timedelta(days=1)).isoformat(), "group_sources": "1"}
        # Scenario 15, backend side: a rejected request, then a valid one.
        self.assertEqual(client.get(url, {**params, "end": params["start"]}, **headers).status_code, 400)
        response = client.get(url, params, **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["probe_average"], [[ms(ORIGIN), 21.0, 2]])
        self.assertEqual(
            client.get(f"/api/thermal-zones/{stranger.thermal_zone_id}/probe-readings/", params, **headers).status_code,
            404,
        )
        neighbour_average = client.get(
            f"/api/thermal-zones/{other_zone.pk}/probe-readings/", params, **headers,
        ).json()["probe_average"]
        self.assertEqual(neighbour_average, [], "A zone with one probe has no inter-probe average")


class PostgresAverageEquivalenceTests(ProbeAverageFixture, TestCase):
    """SQL against the Python reference on the representative history."""

    def setUp(self):
        if not DISPOSABLE_POSTGRES:
            self.skipTest("Requires opt-in disposable PostgreSQL at 127.0.0.1:55439.")
        super().setUp()
        self.representative_history()
        self.readings(self.third, ORIGIN + timedelta(hours=5, seconds=41), ["-4.00", "0.00", "7.77"],
                      step=timedelta(minutes=15))

    def test_sql_average_equals_the_python_reference(self):
        windows = [
            (ORIGIN - timedelta(hours=2), ORIGIN + timedelta(days=3)),
            (ORIGIN + timedelta(hours=7, minutes=7), ORIGIN + timedelta(days=2, hours=9, seconds=13)),
            (ORIGIN - timedelta(days=20), ORIGIN + timedelta(days=20)),
            (ORIGIN + timedelta(days=5), ORIGIN + timedelta(days=9)),
        ]
        for start, end in windows:
            for group_sources in (True, False):
                with self.subTest(start=start, group_sources=group_sources):
                    _resolution, _series, average = self.average(start, end, group_sources=group_sources)
                    with patch.object(probe_readings, "sql_average_available", return_value=False), \
                            patch.object(probe_readings, "sql_grouping_available", return_value=False):
                        reference = probe_readings.zone_probe_series_and_average(
                            self.probes, start, end, group_sources=group_sources,
                        )[2]
                    self.assertEqual(average, reference)
        self.assertTrue(average == [] or all(point[2] >= 2 for point in average))

    def test_coarser_unaligned_interval_keeps_one_snapshot_and_one_period(self):
        # 20- and 15-minute stored intervals with a 15-minute request: the
        # period is their least common multiple (60), computed inside the same
        # statement, so both intervals stay whole in one period and the average
        # is never recomputed from a second read.
        self.bucket(self.left, ORIGIN - timedelta(days=1), 20, ["5.00"])
        self.bucket(self.right, ORIGIN - timedelta(days=1), 15, ["7.00", "8.00"])
        start, end = ORIGIN - timedelta(days=2), ORIGIN + timedelta(days=3)
        with CaptureQueriesContext(connection) as queries:
            resolution, _series, average = probe_readings.zone_probe_series_and_average(
                self.probes, start, end, group_sources=True,
            )
        self.assertEqual((resolution, len(queries)), (60, 1))
        self.assertIn("zone_periods", queries[0]["sql"])
        self.assertIn([ms(ORIGIN - timedelta(days=1)), 6.25, 2], average)
