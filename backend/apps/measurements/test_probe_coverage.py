"""Stored interval coverage and lossless chart provenance, using synthetic data."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.accounts.models import OrganizationMembership
from apps.cultures.models import ThermalZone
from apps.organizations.models import Organization

from .models import Probe, ProbeTemperatureBucket, TemperatureMeasurement
from .probe_readings import overlapping_probe_buckets, probe_series, probe_series_with_sources
from .test_probe_collection import ProbeFixtureMixin


BASE = datetime(2026, 10, 28, 10, tzinfo=timezone.utc)


def moment(minutes):
    return BASE + timedelta(minutes=minutes)


def ms(minutes):
    return int(moment(minutes).timestamp() * 1000)


class CoverageFixtureMixin(ProbeFixtureMixin):
    def bucket(self, start=0, duration=15, *, probe=None, total="6.00", count=3,
               minimum="0.00", maximum="4.00", last=2, average="2.00"):
        return ProbeTemperatureBucket.objects.create(
            probe=probe or self.left, period_start=moment(start), period_minutes=duration,
            last_measured_at=moment(last), temperature_sum_c=Decimal(total),
            measurement_count=count, min_temperature_c=Decimal(minimum),
            max_temperature_c=Decimal(maximum), average_temperature_c=Decimal(average),
        )

    def raw(self, minute, value, *, probe=None):
        return TemperatureMeasurement.objects.create(
            probe=probe or self.left, measured_at=moment(minute), temperature_c=Decimal(value),
        )

    def series(self, start, end, resolution=1, *, probe=None):
        return probe_series_with_sources(probe or self.left, moment(start), moment(end), resolution)


class ProbeCoverageTests(CoverageFixtureMixin, TestCase):
    def test_left_right_and_both_bounds_inside_keep_whole_bucket(self):
        self.bucket()
        for start, end in ((5, 20), (-5, 5), (5, 10), (0, 15)):
            with self.subTest(start=start, end=end):
                points, sources = self.series(start, end)
                self.assertEqual(points, [[ms(0), 2.0, 0.0, 4.0]])
                self.assertEqual(sources, [[[ms(0), ms(15), "6.00", 3, 0.0, 4.0, ms(2)]]])
                self.assertEqual(probe_series(self.left, moment(start), moment(end), 1), points)

    def test_exact_half_open_contacts_exclude_both_neighbors(self):
        self.bucket(-15, last=-13)
        self.bucket(0)
        self.bucket(15, last=17)
        points, sources = self.series(0, 15)
        self.assertEqual(points, [[ms(0), 2.0, 0.0, 4.0]])
        self.assertEqual([source[0] for group in sources for source in group], [ms(0)])
        self.assertEqual(self.series(30, 35), ([], []))

    @override_settings(PROBE_BUCKET_MINUTES=15)
    def test_overlap_uses_each_rows_duration_not_setting_or_last_sample(self):
        long = self.bucket(-80, 90, last=-79)
        self.bucket(-30, 5, last=-29)
        self.bucket(10, 45, last=11)
        selected = overlapping_probe_buckets(moment(5), moment(10)).filter(probe=self.left)
        self.assertEqual(list(selected.values_list("pk", flat=True)), [long.pk])
        points, sources = self.series(5, 10)
        # The point is a resolution anchor, never an invented sample timestamp.
        self.assertEqual(points, [[ms(-150), 2.0, 0.0, 4.0]])
        self.assertEqual(sources, [[[ms(-80), ms(10), "6.00", 3, 0.0, 4.0, ms(-79)]]])

    def test_one_minute_stored_bucket_is_not_dropped_at_raw_resolution(self):
        self.bucket(duration=1, last=0)
        points, sources = self.series(0, 1)
        self.assertEqual(points, [[ms(0), 2.0, 0.0, 4.0]])
        self.assertEqual(sources, [[[ms(0), ms(1), "6.00", 3, 0.0, 4.0, ms(0)]]])

    def test_adjacent_buckets_and_gaps_are_not_duplicated_or_filled(self):
        for start in (0, 15, 60):
            self.bucket(start, last=start + 2)
        points, sources = self.series(5, 70)
        self.assertEqual([point[0] for point in points], [ms(0), ms(15), ms(60)])
        self.assertEqual([group[0][0] for group in sources], [ms(0), ms(15), ms(60)])
        self.assertEqual(sum(len(group) for group in sources), 3)
        self.assertEqual(self.series(30, 60), ([], []))

    def test_raw_half_open_bounds_zero_and_missing_minutes(self):
        for minute, value in ((-1, "99"), (0, "0"), (2, "2.34"), (3, "99")):
            self.raw(minute, value)
        points, sources = self.series(0, 3)
        self.assertEqual(points, [[ms(0), 0.0, None, None], [ms(2), 2.34, None, None]])
        self.assertEqual(sources, [
            [[ms(0), None, "0.00", 1, 0.0, 0.0, ms(0)]],
            [[ms(2), None, "2.34", 1, 2.34, 2.34, ms(2)]],
        ])
        self.assertEqual(self.series(0, 3, probe=self.right), ([], []))

    def test_mixed_raw_and_buckets_preserve_sources_in_point_order(self):
        self.bucket()
        self.raw(16, "0")
        self.raw(18, "2")
        self.bucket(30, total="0.00", count=1, minimum="0", maximum="0", last=30, average="0")
        points, sources = self.series(5, 35)
        self.assertEqual(points, [
            [ms(0), 2.0, 0.0, 4.0], [ms(15), 1.0, 0.0, 2.0], [ms(30), 0.0, 0.0, 0.0],
        ])
        self.assertEqual(sources, [
            [[ms(0), ms(15), "6.00", 3, 0.0, 4.0, ms(2)]],
            [[ms(16), None, "0.00", 1, 0.0, 0.0, ms(16)],
             [ms(18), None, "2.00", 1, 2.0, 2.0, ms(18)]],
            [[ms(30), ms(45), "0.00", 1, 0.0, 0.0, ms(30)]],
        ])

    def test_hourly_weighting_uses_exact_sums_not_rounded_bucket_averages(self):
        self.bucket(total="0.02", count=3, maximum="0.02", average="0.01")
        self.bucket(15, total="0.02", count=3, maximum="0.02", last=17, average="0.01")
        self.raw(31, "0")
        self.raw(60, "3")
        points, sources = self.series(5, 61, 60)
        self.assertEqual(points, [[ms(0), 0.01, 0.0, 0.02], [ms(60), 3.0, 3.0, 3.0]])
        self.assertEqual(len(sources[0]), 3)
        self.assertEqual([source[0] for source in sources[0]], [ms(0), ms(15), ms(31)])
        self.assertEqual(sum(Decimal(source[2]) for source in sources[0]), Decimal("0.04"))
        self.assertEqual(sum(source[3] for source in sources[0]), 7)
        self.assertEqual([source[6] for source in sources[0]], [ms(2), ms(17), ms(31)])
        self.assertEqual(len(points), len(sources))
        self.assertTrue(all(len(point) == 4 for point in points))

    def test_large_persisted_sum_is_a_decimal_string_not_a_float(self):
        self.bucket(total="400000000000.01", count=2000000000, maximum="250", average="200")
        _points, sources = self.series(5, 10)
        self.assertEqual(sources[0][0][2], "400000000000.01")
        self.assertIsInstance(sources[0][0][2], str)


class ProbeCoverageApiTests(CoverageFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="probe-coverage", email="probe-coverage@example.org",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.organization, role="lab_technician",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.headers = {"HTTP_X_ORGANIZATION_ID": str(self.organization.pk)}
        self.url = f"/api/thermal-zones/{self.zone.pk}/probe-readings/"

    def request_series(self, start=5, end=10, *, headers=None, url=None):
        with patch("apps.measurements.probe_views.timezone.now", return_value=moment(60)):
            return self.client.get(
                url or self.url,
                {"start": moment(start).isoformat(), "end": moment(end).isoformat()},
                **(headers or self.headers),
            )

    def test_overlapping_bucket_sets_shared_resolution_and_additive_json_contract(self):
        self.bucket(-30, 45, last=-29)
        self.raw(6, "0", probe=self.right)
        response = self.request_series()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["resolution_minutes"], 45)
        probes = {probe["id"]: probe for probe in data["probes"]}
        left, right = probes[self.left.pk], probes[self.right.pk]
        self.assertEqual(left["points"], [[ms(-60), 2.0, 0.0, 4.0]])
        self.assertEqual(left["point_sources"], [[[ms(-30), ms(15), "6.00", 3, 0.0, 4.0, ms(-29)]]])
        self.assertEqual(right["points"], [[ms(-15), 0.0, 0.0, 0.0]])
        self.assertEqual(right["point_sources"], [[[ms(6), None, "0.00", 1, 0.0, 0.0, ms(6)]]])
        for probe in probes.values():
            self.assertEqual(len(probe["points"]), len(probe["point_sources"]))
            self.assertTrue(all(len(point) == 4 for point in probe["points"]))

    def test_nonoverlapping_buckets_do_not_raise_effective_resolution(self):
        self.bucket(-60, 60, last=-59)
        self.bucket(10, 90, last=11)
        self.raw(5, "0")
        data = self.request_series(0, 10).json()
        self.assertEqual(data["resolution_minutes"], 1)
        left = next(probe for probe in data["probes"] if probe["id"] == self.left.pk)
        self.assertEqual(left["points"], [[ms(5), 0.0, None, None]])
        right = next(probe for probe in data["probes"] if probe["id"] == self.right.pk)
        self.assertEqual((right["points"], right["point_sources"]), ([], []))

    def test_foreign_probe_cannot_leak_sources_or_raise_resolution(self):
        other = Organization.objects.create(name="Coverage foreign lab")
        foreign = Probe.objects.create(
            organization=other, thermal_zone=self.zone, code="FOREIGN", probe_type="iminilide",
            external_serial="coverage-foreign", external_channel=1,
        )
        self.bucket(-30, 120, probe=foreign, last=-29)
        self.raw(6, "99", probe=foreign)
        self.raw(6, "0")
        response = self.request_series()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["resolution_minutes"], 1)
        self.assertEqual({probe["id"] for probe in data["probes"]}, {self.left.pk, self.right.pk})
        with self.assertRaises(ThermalZone.DoesNotExist):
            self.series(5, 10, probe=foreign)
        zone = ThermalZone.objects.create(organization=other, name="Coverage other zone")
        OrganizationMembership.objects.create(user=self.user, organization=other, role="viewer")
        url = f"/api/thermal-zones/{zone.pk}/probe-readings/"
        self.assertEqual(self.request_series(url=url).status_code, 404)
        self.assertEqual(self.request_series(
            url=url, headers={"HTTP_X_ORGANIZATION_ID": str(other.pk)},
        ).status_code, 200)

    def test_inactive_probe_keeps_its_sources_visible(self):
        self.bucket()
        self.left.is_active = False
        self.left.save(update_fields=["is_active"])
        data = self.request_series().json()
        left = next(probe for probe in data["probes"] if probe["id"] == self.left.pk)
        self.assertFalse(left["is_active"])
        self.assertEqual(left["point_sources"][0][0][6], ms(2))


class ProbeGroupedSourcesApiTests(CoverageFixtureMixin, TestCase):
    """The chart's bounded response keeps exact statistics and institution scope."""

    def setUp(self):
        super().setUp()
        user = get_user_model().objects.create_user(
            username="grouped@example.test", email="grouped@example.test", password="pw",
        )
        OrganizationMembership.objects.create(user=user, organization=self.organization, role="admin")
        self.client = APIClient()
        self.client.force_authenticate(user)
        self.headers = {"HTTP_X_ORGANIZATION_ID": str(self.organization.pk)}
        self.url = f"/api/thermal-zones/{self.zone.pk}/probe-readings/"

    def get(self, start, end, **params):
        return self.client.get(
            self.url, {"start": moment(start).isoformat(), "end": moment(end).isoformat(), **params},
            **self.headers,
        )

    def test_default_contract_keeps_every_source_and_exposes_cadence(self):
        Probe.objects.filter(pk=self.left.pk).update(sampling_interval_seconds=600)
        self.raw(0, "1.00")
        self.raw(1, "3.00")
        body = self.get(0, 4 * 24 * 60).json()
        left = next(probe for probe in body["probes"] if probe["id"] == self.left.pk)
        self.assertEqual(left["sampling_interval_seconds"], 600)
        self.assertEqual(len(left["point_sources"][0]), 2)

    def test_grouped_raw_readings_keep_exact_statistics_and_last_instant(self):
        self.raw(0, "0.00")
        self.raw(1, "-1.50")
        self.raw(14, "2.00")
        body = self.get(0, 4 * 24 * 60, group_sources="1").json()
        left = next(probe for probe in body["probes"] if probe["id"] == self.left.pk)
        self.assertEqual(body["resolution_minutes"], 15)
        self.assertEqual(left["points"], [[ms(0), 0.17, -1.5, 2.0]])
        self.assertEqual(left["point_sources"], [[[ms(0), ms(15), "0.50", 3, -1.5, 2.0, ms(14)]]])

    def test_grouped_all_zero_readings_remain_measurements(self):
        self.raw(0, "0.00")
        self.raw(1, "0.00")
        body = self.get(0, 4 * 24 * 60, group_sources="1").json()
        left = next(probe for probe in body["probes"] if probe["id"] == self.left.pk)
        self.assertEqual(left["point_sources"], [[[ms(0), ms(15), "0.00", 2, 0.0, 0.0, ms(1)]]])

    def test_grouped_raw_bounds_stay_inside_the_request(self):
        self.raw(5, "1.00")
        body = self.get(4, 4 * 24 * 60, group_sources="1").json()
        left = next(probe for probe in body["probes"] if probe["id"] == self.left.pk)
        self.assertEqual(left["point_sources"][0][0][:2], [ms(4), ms(15)])

    def test_grouped_stored_bucket_keeps_its_whole_interval_and_extrema(self):
        self.bucket(0, 15, total="6.00", count=3, minimum="0.00", maximum="9.00", last=2)
        body = self.get(5, 4 * 24 * 60, group_sources="1").json()
        left = next(probe for probe in body["probes"] if probe["id"] == self.left.pk)
        self.assertEqual(left["point_sources"], [[[ms(0), ms(15), "6.00", 3, 0.0, 9.0, ms(2)]]])

    def test_grouped_mixed_tiers_sum_raw_and_bucket_without_inventing_points(self):
        self.bucket(0, 15, total="6.00", count=3, minimum="1.00", maximum="3.00", last=2)
        self.raw(20, "10.00")
        body = self.get(0, 4 * 24 * 60, group_sources="1").json()
        left = next(probe for probe in body["probes"] if probe["id"] == self.left.pk)
        self.assertEqual(body["resolution_minutes"], 15)
        self.assertEqual([point[0] for point in left["points"]], [ms(0), ms(15)])
        self.assertEqual(left["point_sources"][1], [[ms(15), ms(30), "10.00", 1, 10.0, 10.0, ms(20)]])

    def test_unrelated_group_source_values_keep_the_detailed_contract(self):
        self.raw(0, "1.00")
        self.raw(1, "3.00")
        body = self.get(0, 4 * 24 * 60, group_sources="0").json()
        left = next(probe for probe in body["probes"] if probe["id"] == self.left.pk)
        self.assertEqual(len(left["point_sources"][0]), 2)

    def test_grouped_mode_stays_scoped_to_the_active_institution(self):
        other = Organization.objects.create(name="Other lab")
        zone = ThermalZone.objects.create(organization=other, name="Foreign")
        response = self.client.get(
            f"/api/thermal-zones/{zone.pk}/probe-readings/", {"group_sources": "1"}, **self.headers,
        )
        self.assertEqual(response.status_code, 404)


class ProbeReadPathCostTests(CoverageFixtureMixin, TestCase):
    """The chart GET takes no lock and its cost does not follow the probe count."""

    def setUp(self):
        super().setUp()
        user = get_user_model().objects.create_user(
            username="reader@example.test", email="reader@example.test", password="pw",
        )
        OrganizationMembership.objects.create(user=user, organization=self.organization, role="viewer")
        self.client = APIClient()
        self.client.force_authenticate(user)
        self.headers = {"HTTP_X_ORGANIZATION_ID": str(self.organization.pk)}
        self.url = f"/api/thermal-zones/{self.zone.pk}/probe-readings/"
        self.params = {"start": moment(0).isoformat(), "end": moment(24 * 60).isoformat(), "group_sources": "1"}

    def add_probe(self, channel):
        return Probe.objects.create(
            organization=self.organization, thermal_zone=self.zone, code=f"EXTRA {channel}",
            probe_type=Probe.ProbeType.IMINILIDE, external_serial="1162040825", external_channel=channel,
        )

    def read(self):
        response = self.client.get(self.url, self.params, **self.headers)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_a_read_never_requests_a_row_lock(self):
        # SQLite drops FOR UPDATE, so assert the ORM call rather than the SQL text.
        self.raw(0, "1.00")
        self.bucket(60, 15)
        with patch("django.db.models.query.QuerySet.select_for_update", side_effect=AssertionError("Read path locked a row")):
            self.read()
            probe_series(self.left, moment(0), moment(90), 1)

    def test_a_read_has_no_historical_extent_query(self):
        self.raw(0, "1.00")
        self.bucket(60, 15)
        with CaptureQueriesContext(connection) as queries:
            body = self.read()
        self.assertNotIn("first_reading_at", body)
        self.assertNotIn("last_reading_at", body)
        statements = [query["sql"].upper() for query in queries]
        self.assertEqual([sql for sql in statements if "MIN(" in sql], [])
        self.assertEqual(len([sql for sql in statements if "UNION ALL" in sql]), 1)

    def test_query_count_does_not_grow_with_probes(self):
        self.raw(0, "1.00")
        with CaptureQueriesContext(connection) as two:
            self.read()
        for channel in range(20, 28):
            self.add_probe(channel)
        with CaptureQueriesContext(connection) as ten:
            self.assertEqual(len(self.read()["probes"]), 10)
        self.assertEqual(len(ten), len(two))

    def test_tiers_read_together_keep_decimal_values_and_zero(self):
        self.raw(0, "0.00")
        self.bucket(60, 15, total="6.00", count=3, minimum="0.00", maximum="4.00", last=62)
        left = next(probe for probe in self.read()["probes"] if probe["id"] == self.left.pk)
        self.assertEqual(left["point_sources"][0], [[ms(0), ms(15), "0.00", 1, 0.0, 0.0, ms(0)]])
        self.assertEqual(left["point_sources"][1], [[ms(60), ms(75), "6.00", 3, 0.0, 4.0, ms(62)]])

    def test_common_resolution_follows_stored_buckets_of_any_probe(self):
        self.raw(0, "1.00", probe=self.right)
        self.bucket(60, 15)
        self.params["end"] = moment(2 * 60).isoformat()
        with patch("apps.measurements.probe_readings.timezone.now", return_value=moment(120)):
            body = self.read()
        self.assertEqual(body["resolution_minutes"], 15)

    def test_authorization_precedes_any_series_statement(self):
        other = Organization.objects.create(name="Other lab")
        zone = ThermalZone.objects.create(organization=other, name="Foreign")
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(f"/api/thermal-zones/{zone.pk}/probe-readings/", self.params, **self.headers)
        self.assertEqual(response.status_code, 404)
        self.assertEqual([q for q in queries if "MEASUREMENTS_TEMPERATUREMEASUREMENT" in q["sql"].upper()], [])
