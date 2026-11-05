"""Thermal control regressions using isolated, synthetic observations."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import OrganizationMembership
from apps.cultures.models import ThermalZone
from apps.cultures.serializers import ThermalZoneSerializer
from apps.organizations.models import Organization

from .models import DailyTemperature, Probe, ProbeTemperatureBucket, TemperatureMeasurement
from .thermal_control import thermal_control_probes, zone_thermal_control


NOW = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)


class ThermalControlTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Thermal lab")
        self.zone = ThermalZone.objects.create(organization=self.organization, name="Control")
        self.other = Organization.objects.create(name="Other lab")
        self.other_zone = ThermalZone.objects.create(organization=self.other, name="Other control")

    def probe(self, **kwargs):
        return Probe.objects.create(
            organization=self.organization, thermal_zone=self.zone,
            code=f"probe-{Probe.objects.count()}", **kwargs,
        )

    def raw(self, probe, value="0", age=0):
        return TemperatureMeasurement.objects.create(
            probe=probe, temperature_c=Decimal(value), measured_at=NOW - timedelta(seconds=age),
        )

    def bucket(self, probe, value="0", age=0, start_age=900):
        return ProbeTemperatureBucket.objects.create(
            probe=probe, period_start=NOW - timedelta(seconds=start_age), period_minutes=15,
            last_measured_at=NOW - timedelta(seconds=age),
            last_temperature_c=Decimal(value) if value is not None else None,
            min_temperature_c=0, max_temperature_c=99, average_temperature_c=42,
            temperature_sum_c=4200, measurement_count=100,
        )

    def summary(self):
        return zone_thermal_control(self.zone, now=NOW)

    def test_no_probes_is_distinct_from_no_readings(self):
        empty = self.summary()
        self.assertEqual(empty["eligible_probe_count"], 0)
        self.assertIsNone(empty["temperature_c"])
        self.assertIsNone(empty["expires_at"])
        self.probe()
        missing = self.summary()
        self.assertEqual(missing["eligible_probe_count"], 1)
        self.assertEqual(missing["no_reading_probe_count"], 1)
        self.assertEqual(missing["included_probe_count"], 0)

    def test_one_two_ten_probes_equal_weight_and_constant_query_count(self):
        for count in range(1, 11):
            probe = self.probe()
            # Many old observations must not increase this probe's weight.
            for age in (120, 60, 0):
                self.raw(probe, str(count - 1), age)
            if count in (1, 2, 10):
                with self.subTest(count=count), self.assertNumQueries(1):
                    result = self.summary()
                self.assertEqual(result["included_probe_count"], count)
                self.assertIsInstance(result["temperature_c"], Decimal)
                self.assertEqual(result["temperature_c"], Decimal(count - 1) / 2)
                self.assertEqual(result["oldest_measured_at"], NOW)
                self.assertEqual(result["newest_measured_at"], NOW)

    def test_latest_bucket_selection_with_history_and_distinct_probe_ids(self):
        # Offset bucket and probe primary keys to expose incorrect nested OuterRef.
        first = self.probe()
        second = self.probe()
        ProbeTemperatureBucket.objects.bulk_create([
            ProbeTemperatureBucket(
                probe=first, period_start=NOW - timedelta(minutes=15 * (i + 2)),
                period_minutes=15, last_measured_at=NOW - timedelta(minutes=15 * (i + 2)),
                last_temperature_c=None if i % 2 else Decimal("99"),
                min_temperature_c=0, max_temperature_c=99, average_temperature_c=42,
                temperature_sum_c=42, measurement_count=1,
            ) for i in range(200)
        ])
        self.bucket(first, "0", age=1)
        self.bucket(second, "10", age=2)
        self.bucket(second, "10", age=2, start_age=1800)
        with self.assertNumQueries(1):
            result = self.summary()
        self.assertEqual(result["temperature_c"], Decimal("5"))
        self.assertEqual(result["included_probe_count"], 2)
        self.assertEqual(result["oldest_measured_at"], NOW - timedelta(seconds=2))
        self.assertEqual(result["newest_measured_at"], NOW - timedelta(seconds=1))

    def test_bucket_latest_lookup_index_is_installed(self):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor, ProbeTemperatureBucket._meta.db_table,
            )
        index = constraints["bucket_probe_last_measured_idx"]
        self.assertTrue(index["index"])
        self.assertEqual(index["columns"], ["probe_id", "last_measured_at"])

    def test_sqlite_query_plan_uses_index_for_latest_and_exact_timestamp_ties(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite EXPLAIN QUERY PLAN regression; PostgreSQL needs its own QA plan")
        self.probe()
        plan = thermal_control_probes(self.zone).explain()
        index_lookups = [line for line in plan.splitlines() if "bucket_probe_last_measured_idx" in line]
        # Two ORDER BY ... LIMIT 1 scalar lookups, plus the exact-instant aggregate.
        self.assertEqual(len(index_lookups), 3, plan)
        self.assertTrue(any(
            "probe_id=? AND last_measured_at=?" in line for line in index_lookups
        ), plan)
        self.assertNotIn("USE TEMP B-TREE", plan)

    def test_all_probe_types_are_eligible_without_collection(self):
        for probe_type, _label in Probe.ProbeType.choices:
            self.raw(self.probe(probe_type=probe_type), "0")
        result = self.summary()
        self.assertEqual(result["included_probe_count"], len(Probe.ProbeType.choices))
        self.assertEqual(result["temperature_c"], Decimal(0))

    def test_freshness_boundaries_for_each_cadence(self):
        for cadence in (60, 600, 900):
            probe = self.probe(sampling_interval_seconds=cadence)
            for age, category in ((0, "included"), (3 * cadence, "included"),
                                  (3 * cadence + 0.000001, "stale"), (-0.000001, "future")):
                with self.subTest(cadence=cadence, age=age):
                    TemperatureMeasurement.objects.filter(probe=probe).delete()
                    self.raw(probe, age=age)
                    result = self.summary()
                    self.assertEqual(result[f"{category}_probe_count"], 1)
            probe.is_active = False
            probe.save(update_fields=["is_active"])

    def test_changed_cadence_is_used_at_evaluation_time(self):
        probe = self.probe()
        self.raw(probe, age=1000)
        self.assertEqual(self.summary()["stale_probe_count"], 1)
        Probe.objects.filter(pk=probe.pk).update(sampling_interval_seconds=600)
        self.assertEqual(self.summary()["included_probe_count"], 1)
        Probe.objects.filter(pk=probe.pk).update(sampling_interval_seconds=60)
        self.assertEqual(self.summary()["stale_probe_count"], 1)

    def test_actual_timestamps_not_import_order_or_bucket_start(self):
        probe = self.probe()
        self.raw(probe, "9", age=10)
        self.raw(probe, "99", age=100)
        self.bucket(probe, "0", age=5, start_age=900)
        result = self.summary()
        self.assertEqual(result["temperature_c"], 0)
        self.assertEqual(result["newest_measured_at"], NOW - timedelta(seconds=5))
        self.raw(probe, "3", age=1)
        self.assertEqual(self.summary()["temperature_c"], 3)

    def test_latest_bucket_uses_max_actual_instant_not_period_start(self):
        probe = self.probe()
        self.bucket(probe, "7", age=1, start_age=1800)
        self.bucket(probe, "99", age=10, start_age=900)
        self.assertEqual(self.summary()["temperature_c"], 7)

    def test_newest_unknown_bucket_blocks_raw_and_older_known_bucket(self):
        probe = self.probe()
        self.bucket(probe, "8", age=20, start_age=1800)
        self.raw(probe, "9", age=10)
        bucket = self.bucket(probe, None, age=5)
        result = self.summary()
        self.assertEqual(result["unavailable_probe_count"], 1)
        self.assertIsNone(result["temperature_c"])
        self.assertIsNone(result["expires_at"])
        bucket.last_temperature_c = Decimal(0)
        bucket.save(update_fields=["last_temperature_c"])
        self.assertEqual(self.summary()["temperature_c"], 0)

    def test_raw_bucket_equal_timestamp_overlap_is_one_vote_or_unknown(self):
        probe = self.probe()
        self.raw(probe, "0")
        bucket = self.bucket(probe, "0")
        for value, included in ((Decimal(0), 1), (Decimal(1), 0), (None, 0)):
            with self.subTest(value=value):
                bucket.last_temperature_c = value
                bucket.save(update_fields=["last_temperature_c"])
                result = self.summary()
                self.assertEqual(result["included_probe_count"], included)
                self.assertEqual(result["unavailable_probe_count"], 1 - included)

    def test_multiple_bucket_ties_require_all_known_values_to_agree(self):
        probe = self.probe()
        self.bucket(probe, "0", start_age=1800)
        second = self.bucket(probe, "0")
        for value, included in ((Decimal(0), 1), (Decimal(1), 0), (None, 0)):
            with self.subTest(value=value):
                second.last_temperature_c = value
                second.save(update_fields=["last_temperature_c"])
                result = self.summary()
                self.assertEqual(result["included_probe_count"], included)
                self.assertEqual(result["unavailable_probe_count"], 1 - included)

    def test_timestamp_range_and_expiry_use_only_included_probes(self):
        self.raw(self.probe(sampling_interval_seconds=900), "10", age=1000)
        self.raw(self.probe(), "0", age=100)
        self.raw(self.probe(), "99", age=181)
        self.raw(self.probe(), "99", age=-1)
        result = self.summary()
        self.assertEqual(result["temperature_c"], 5)
        self.assertEqual(result["oldest_measured_at"], NOW - timedelta(seconds=1000))
        self.assertEqual(result["newest_measured_at"], NOW - timedelta(seconds=100))
        self.assertEqual(result["expires_at"], NOW + timedelta(seconds=80))
        self.assertEqual(result["evaluated_at"], NOW)
        self.assertEqual(result["stale_probe_count"], 1)
        self.assertEqual(result["future_probe_count"], 1)

    def test_latest_future_observation_does_not_fall_back(self):
        probe = self.probe()
        self.raw(probe, age=1)
        self.bucket(probe, "20", age=-1)
        self.assertEqual(self.summary()["future_probe_count"], 1)
        self.assertIsNone(self.summary()["temperature_c"])

    def test_invalid_intervals_are_safely_excluded(self):
        # Database constraints disallow these; still fail closed for legacy rows.
        for interval in (None, 0, -60, 61):
            with self.subTest(interval=interval), patch(
                "apps.measurements.thermal_control.thermal_control_probes",
                return_value=[{"sampling_interval_seconds": interval}],
            ):
                result = self.summary()
                self.assertEqual(result["invalid_interval_probe_count"], 1)
                self.assertIsNone(result["temperature_c"])

    def test_inactive_and_cross_organization_probes_are_excluded(self):
        self.raw(self.probe(), "0")
        self.raw(self.probe(is_active=False), "99")
        foreign = Probe.objects.create(
            organization=self.other, thermal_zone=self.other_zone, code="foreign",
        )
        self.raw(foreign, "99")
        self.bucket(foreign, "99")
        # Simulate a legacy inconsistent relation without changing historical data.
        Probe.objects.filter(pk=foreign.pk).update(thermal_zone=self.zone)
        result = self.summary()
        self.assertEqual(result["eligible_probe_count"], 1)
        self.assertEqual(result["temperature_c"], 0)

    def test_api_auth_scoping_and_existing_fields_unchanged(self):
        client = APIClient()
        url = f"/api/thermal-zones/{self.zone.pk}/"
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.organization.pk)}
        self.assertIn(client.get(url, **headers).status_code, (401, 403))
        user = get_user_model().objects.create_user(
                    username="thermal-viewer", email="thermal-viewer@example.org",
                )
        OrganizationMembership.objects.create(
            user=user, organization=self.organization, role=OrganizationMembership.Role.VIEWER,
        )
        client.force_authenticate(user)
        self.raw(self.probe(), "0")
        daily = DailyTemperature.objects.create(
            thermal_zone=self.zone, date=NOW.date(), average_temperature_c=42,
            min_temperature_c=40, max_temperature_c=44, measurement_count=100,
        )
        before = ThermalZoneSerializer(self.zone).data
        with patch("apps.measurements.thermal_control.timezone.now", return_value=NOW):
            response = client.get(url, **headers)
            listed = client.get("/api/thermal-zones/", **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(listed.status_code, 200)
        data = next(item for item in listed.json()["results"] if item["id"] == self.zone.pk)
        self.assertEqual(data["thermal_control"]["temperature_c"], 0)
        self.assertEqual(data["thermal_control"]["included_probe_count"], 1)
        self.assertEqual(data["thermal_control"]["evaluated_at"], NOW.isoformat().replace("+00:00", "Z"))
        self.assertEqual(data["latest_temperature"]["average_temperature_c"], 42)
        self.assertEqual(data["latest_temperature"]["measurement_count"], 100)
        self.assertEqual(data["probe_monitoring"], before["probe_monitoring"])
        self.assertEqual(data["probes"], before["probes"])
        daily.refresh_from_db()
        self.assertEqual(daily.average_temperature_c, 42)
        self.assertEqual(daily.measurement_count, 100)

        self.assertEqual(client.get(f"/api/thermal-zones/{self.other_zone.pk}/", **headers).status_code, 404)
        self.assertEqual(client.get(url, HTTP_X_ORGANIZATION_ID=str(self.other.pk)).status_code, 403)

    def thermal_client(self):
        user = get_user_model().objects.create_user(
            username="thermal-refresh", email="thermal-refresh@example.org",
        )
        OrganizationMembership.objects.create(
            user=user, organization=self.organization, role=OrganizationMembership.Role.VIEWER,
        )
        client = APIClient()
        client.force_authenticate(user)
        return client, user

    def test_thermal_control_endpoint_requires_authentication(self):
        response = APIClient().get(
            f"/api/thermal-zones/{self.zone.pk}/thermal-control/",
            HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        )
        self.assertEqual(response.status_code, 403)

    def test_thermal_control_endpoint_matches_summary_and_refreshes_at_expiry(self):
        client, _user = self.thermal_client()
        self.raw(self.probe(), "0")
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.organization.pk)}
        url = f"/api/thermal-zones/{self.zone.pk}/thermal-control/"
        for age, included in ((0, 1), (180, 1), (180.000001, 0)):
            instant = NOW + timedelta(seconds=age)
            with self.subTest(age=age), patch(
                "apps.measurements.thermal_control.timezone.now", return_value=instant,
            ):
                response = client.get(url, **headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Cache-Control"], "no-store")
                self.assertEqual(response.data, zone_thermal_control(self.zone, now=instant))
                self.assertEqual(response.json()["included_probe_count"], included)
                listed = client.get("/api/thermal-zones/", **headers).json()["results"]
                zone = next(item for item in listed if item["id"] == self.zone.pk)
                self.assertEqual(response.json(), zone["thermal_control"])

    def test_thermal_control_endpoint_enforces_active_organization_scope(self):
        client, user = self.thermal_client()
        url = f"/api/thermal-zones/{self.zone.pk}/thermal-control/"
        foreign_url = f"/api/thermal-zones/{self.other_zone.pk}/thermal-control/"
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.organization.pk)}
        self.assertEqual(client.get(foreign_url, **headers).status_code, 404)
        self.assertEqual(client.get(url, HTTP_X_ORGANIZATION_ID=str(self.other.pk)).status_code, 403)
        membership = OrganizationMembership.objects.create(
            user=user, organization=self.other, role=OrganizationMembership.Role.VIEWER,
        )
        self.assertEqual(client.get(foreign_url, **headers).status_code, 404)
        self.assertEqual(client.get(url, HTTP_X_ORGANIZATION_ID=str(self.other.pk)).status_code, 404)
        self.assertEqual(client.get(foreign_url, HTTP_X_ORGANIZATION_ID=str(self.other.pk)).status_code, 200)
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        self.assertEqual(client.get(foreign_url, HTTP_X_ORGANIZATION_ID=str(self.other.pk)).status_code, 403)
