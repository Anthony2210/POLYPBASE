"""Probe integrity regressions using synthetic measurements only."""

import io
import json
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import OrganizationMembership
from apps.audit.models import Alert, AuditLog
from apps.cultures.models import ThermalZone
from apps.organizations.models import Organization

from .iminilide import IminilideClient, Reading, parse_readings
from .models import DailyTemperature, Probe, ProbeTemperatureBucket, TemperatureMeasurement
from .probe_collection import (
    collect_iminilide, collection_start, condense_probe_readings,
    recompute_daily_temperature, store_readings,
)
from .probe_readings import (
    latest_zone_reading_at, probe_series, series_resolution_minutes, zone_probe_monitoring,
)
from .test_probe_collection import FakeClient, FakeOpener, NOW, UTC, ProbeFixtureMixin, paris


class ProbeIntegrityTests(ProbeFixtureMixin, TestCase):
    def test_all_inserted_days_rollback_when_one_recalculation_fails(self):
        readings = [Reading(paris(2026, 10, day, 10), Decimal("0")) for day in (27, 28)]
        original = recompute_daily_temperature

        def fail_second_day(zone_id, day, **kwargs):
            if day.day == 28:
                raise RuntimeError("Synthetic aggregation failure")
            return original(zone_id, day, **kwargs)

        with patch("apps.measurements.probe_collection.recompute_daily_temperature", side_effect=fail_second_day):
            with self.assertRaisesRegex(RuntimeError, "Synthetic aggregation failure"):
                store_readings(self.left, readings)
        self.assertFalse(TemperatureMeasurement.objects.exists())
        self.assertFalse(DailyTemperature.objects.exists())
        store_readings(self.left, readings)
        self.assertEqual(TemperatureMeasurement.objects.count(), 2)
        self.assertEqual(DailyTemperature.objects.count(), 2)
        self.assertTrue(all(day.average_temperature_c == 0 for day in DailyTemperature.objects.all()))

    def test_replayed_minutes_repair_an_existing_incomplete_day(self):
        reading = Reading(paris(2026, 10, 28, 10), Decimal("0"))
        TemperatureMeasurement.objects.create(probe=self.left, measured_at=reading.measured_at, temperature_c=0)
        created, existing, _days = store_readings(self.left, [reading])
        self.assertEqual((created, existing), (0, 1))
        daily = DailyTemperature.objects.get()
        self.assertEqual(daily.average_temperature_c, Decimal("0"))
        self.assertEqual(daily.measurement_count, 1)

    def test_daily_mean_weights_unequal_left_right_sample_counts(self):
        collect_iminilide(client=FakeClient({
            12: [["2026/10/28 10:00:00", 0]],
            11: [["2026/10/28 10:00:00", 12], ["2026/10/28 10:01:00", 12], ["2026/10/28 10:02:00", 12]],
        }), now=NOW)
        daily = DailyTemperature.objects.get()
        self.assertEqual(daily.average_temperature_c, Decimal("9.00"))
        self.assertEqual(daily.measurement_count, 4)

    def test_condensation_preserves_exact_sum_and_daily_and_hourly_means(self):
        start = paris(2026, 10, 1, 10)
        readings = [Reading(start + timedelta(minutes=i), Decimal(v)) for i, v in (
            (0, "0"), (1, "0"), (2, "0.02"), (15, "0"), (16, "0"),
        )]
        store_readings(self.left, readings)
        before = DailyTemperature.objects.get()
        self.assertEqual(before.average_temperature_c, Decimal("0.00"))
        condense_probe_readings(now=NOW)
        self.assertEqual(sum(ProbeTemperatureBucket.objects.values_list("temperature_sum_c", flat=True)), Decimal("0.02"))
        recompute_daily_temperature(self.zone.pk, start.date(), organization_id=self.organization.pk)
        after = DailyTemperature.objects.get()
        self.assertEqual(after.average_temperature_c, before.average_temperature_c)
        self.assertEqual(after.measurement_count, 5)
        points = probe_series(self.left, start, start + timedelta(hours=1), 60)
        self.assertEqual(points[0][1:], [0.0, 0.0, 0.02])
        self.assertFalse(TemperatureMeasurement.objects.exists())

    def test_condensation_merge_uses_exact_sum_not_rounded_average(self):
        start = paris(2026, 10, 1, 10)
        bucket = ProbeTemperatureBucket.objects.create(
            probe=self.left, last_measured_at=start, period_start=start, period_minutes=15, measurement_count=3,
            min_temperature_c=0, max_temperature_c=Decimal("0.02"),
            average_temperature_c=Decimal("0.01"), temperature_sum_c=Decimal("0.02"),
        )
        TemperatureMeasurement.objects.create(probe=self.left, measured_at=start + timedelta(minutes=3), temperature_c=0)
        condense_probe_readings(now=NOW)
        bucket.refresh_from_db()
        self.assertEqual(bucket.temperature_sum_c, Decimal("0.02"))
        self.assertEqual(bucket.measurement_count, 4)
        self.assertEqual(bucket.average_temperature_c, Decimal("0.01"))

    def test_retention_keeps_partial_boundary_bucket_and_recent_minutes(self):
        cutoff = NOW - timedelta(days=14)
        for instant in (cutoff - timedelta(minutes=1), cutoff, NOW):
            TemperatureMeasurement.objects.create(probe=self.left, measured_at=instant, temperature_c=0)
        condense_probe_readings(now=NOW + timedelta(minutes=7))
        remaining = list(TemperatureMeasurement.objects.order_by("measured_at").values_list("measured_at", flat=True))
        self.assertEqual(remaining, [cutoff, NOW])
        self.assertEqual(ProbeTemperatureBucket.objects.get().measurement_count, 1)

    def test_collection_and_condensation_preserve_alerts_and_do_not_add_audits(self):
        Alert.objects.create(organization=self.organization, thermal_zone=self.zone, alert_type="temperature", message="Historical fact")
        before = list(Alert.objects.values())
        store_readings(self.left, [Reading(paris(2026, 10, 1, 10), Decimal("0"))])
        condense_probe_readings(now=NOW)
        self.assertEqual(list(Alert.objects.values()), before)
        self.assertFalse(AuditLog.objects.exists())

    def test_ambiguous_response_without_backwards_jump_is_not_guessed(self):
        parsed = parse_readings([["2026/10/25 02:10:00", 0], ["2026/10/25 02:11:00", 25]])
        self.assertEqual(parsed.readings, [])
        self.assertEqual(parsed.invalid_rows, 2)

    def test_equal_timestamps_do_not_prove_a_dst_backwards_jump(self):
        parsed = parse_readings([["2026/10/25 02:10:00", 1], ["2026/10/25 02:10:00", 2]])
        self.assertEqual(parsed.readings, [])

    def test_nonexistent_spring_time_is_not_stored(self):
        parsed = parse_readings([["2026/03/29 02:10:00", 0], ["2026/03/29 03:10:00", 5]])
        self.assertEqual(parsed.invalid_rows, 1)
        self.assertEqual(len(parsed.readings), 1)

    def test_missing_value_still_establishes_dst_jump_and_zero_survives(self):
        parsed = parse_readings([["2026/10/25 02:59:00", None], ["2026/10/25 02:00:00", 0]])
        self.assertEqual(parsed.missing_values, 1)
        self.assertEqual(parsed.readings[0].temperature_c, 0)
        self.assertEqual(parsed.readings[0].measured_at.astimezone(UTC), datetime(2026, 10, 25, 1, tzinfo=UTC))

    def test_successive_cloud_collections_rewind_before_repeated_hour(self):
        self.right.is_active = False
        self.right.save()
        payload = [["2026/10/25 01:59:00", 1], ["2026/10/25 02:10:00", 2],
                   ["2026/10/25 02:59:00", 3], ["2026/10/25 02:00:00", 4], ["2026/10/25 02:10:00", 0]]
        opener = FakeOpener([b"ok", json.dumps(payload).encode(), json.dumps(payload + [["2026/10/25 02:11:00", 5]]).encode()])
        client = IminilideClient(email="qa@example.org", password="synthetic", opener=opener)
        now = datetime(2026, 10, 25, 1, 15, tzinfo=UTC)
        self.assertEqual(collect_iminilide(client=client, now=now).created, 5)
        self.assertEqual(collection_start(self.left, now=now), paris(2026, 10, 25, 0))
        self.assertEqual(collect_iminilide(client=client, now=now).created, 1)
        self.assertTrue(opener.requests[-1].full_url.endswith("_20261025000000"))
        values = dict(TemperatureMeasurement.objects.values_list("measured_at", "temperature_c"))
        self.assertEqual(values[datetime(2026, 10, 25, 0, 10, tzinfo=UTC)], 2)
        self.assertEqual(values[datetime(2026, 10, 25, 1, 10, tzinfo=UTC)], 0)
        self.assertEqual(values[datetime(2026, 10, 25, 1, 11, tzinfo=UTC)], 5)

    def test_retention_bound_during_repeated_hour_is_not_exceeded(self):
        now = datetime(2026, 11, 4, 1, 15, tzinfo=UTC)
        self.assertEqual(collection_start(self.left, now=now), now - timedelta(days=10))

    def test_effective_resolution_increases_only_for_old_or_long_windows(self):
        self.assertEqual(series_resolution_minutes(NOW - timedelta(hours=24), NOW, now=NOW), 1)
        self.assertEqual(series_resolution_minutes(NOW - timedelta(days=20), NOW - timedelta(days=19), now=NOW), 15)
        self.assertEqual(series_resolution_minutes(NOW - timedelta(days=15), NOW - timedelta(days=13), now=NOW), 15)
        self.assertEqual(series_resolution_minutes(NOW - timedelta(days=90), NOW, now=NOW), 60)


class ProbeScopedApiTests(ProbeFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username="probe-integrity", email="probe-integrity@example.org")
        OrganizationMembership.objects.create(user=self.user, organization=self.organization, role="lab_technician")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.headers = {"HTTP_X_ORGANIZATION_ID": str(self.organization.pk)}
        self.url = f"/api/thermal-zones/{self.zone.pk}/probe-readings/"

    def test_impossible_iso_dates_return_400_for_both_bounds(self):
        for field in ("start", "end"):
            for value in ("2026-02-30T10:00:00Z", "2026-10-28T25:00:00Z", "2026-10-28T10:00:00+99:00"):
                with self.subTest(field=field, value=value):
                    response = self.client.get(self.url, {field: value}, **self.headers)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(field, response.json())

    def test_old_short_window_has_one_effective_quarter_hour_resolution(self):
        start = paris(2026, 10, 1, 10)
        store_readings(self.left, [Reading(start, Decimal("0")), Reading(start + timedelta(minutes=15), Decimal("2"))])
        condense_probe_readings(now=NOW)
        # A raw series for the other probe must use the same announced resolution.
        TemperatureMeasurement.objects.create(probe=self.right, measured_at=start + timedelta(minutes=1), temperature_c=5)
        with patch("apps.measurements.probe_views.timezone.now", return_value=NOW):
            response = self.client.get(self.url, {"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat()}, **self.headers)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["resolution_minutes"], 15)
        left = next(probe for probe in data["probes"] if probe["id"] == self.left.pk)
        right = next(probe for probe in data["probes"] if probe["id"] == self.right.pk)
        self.assertEqual([point[1] for point in left["points"]], [0.0, 2.0])
        self.assertEqual(left["points"][1][0] - left["points"][0][0], 15 * 60 * 1000)
        self.assertEqual(right["points"][0][1:], [5.0, 5.0, 5.0])

    def test_stored_buckets_raise_resolution_even_inside_recent_retention(self):
        start = NOW - timedelta(hours=1)
        ProbeTemperatureBucket.objects.create(
            probe=self.left, last_measured_at=start, period_start=start, period_minutes=15,
            min_temperature_c=0, average_temperature_c=0, max_temperature_c=0,
            measurement_count=1, temperature_sum_c=0,
        )
        with patch("apps.measurements.probe_views.timezone.now", return_value=NOW):
            response = self.client.get(self.url, {"start": start.isoformat(), "end": NOW.isoformat()}, **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["resolution_minutes"], 15)

    def test_cross_organization_probe_is_excluded_from_collection_and_reading(self):
        other = Organization.objects.create(name="Foreign probe organization")
        foreign = Probe.objects.create(
            organization=other, thermal_zone=self.zone, code="FOREIGN", probe_type="iminilide",
            external_serial="99990000", external_channel=1,
        )
        TemperatureMeasurement.objects.create(probe=foreign, measured_at=NOW, temperature_c=100)
        ProbeTemperatureBucket.objects.create(
            probe=foreign, last_measured_at=NOW - timedelta(days=20), period_start=NOW - timedelta(days=20), period_minutes=15,
            min_temperature_c=100, average_temperature_c=100, max_temperature_c=100,
            temperature_sum_c=100, measurement_count=1,
        )
        self.assertTrue(zone_probe_monitoring(self.zone, now=NOW)["manual_entry_available"])
        self.assertIsNone(latest_zone_reading_at(self.zone.pk, organization_id=self.organization.pk))
        client = FakeClient({})
        collect_iminilide(client=client, now=NOW)
        self.assertEqual({serial for serial, _channel, _since in client.calls}, {self.left.external_serial})
        outcome = recompute_daily_temperature(self.zone.pk, NOW.date(), organization_id=self.organization.pk)
        self.assertEqual(outcome, "empty")
        self.assertFalse(DailyTemperature.objects.exists())
        condense_probe_readings(now=NOW + timedelta(days=30))
        self.assertTrue(TemperatureMeasurement.objects.filter(probe=foreign).exists())
        with self.assertRaises(ThermalZone.DoesNotExist):
            store_readings(foreign, [Reading(NOW, Decimal("0"))])
        with self.assertRaises(ThermalZone.DoesNotExist):
            probe_series(foreign, NOW - timedelta(hours=1), NOW + timedelta(hours=1), 1)
        response = self.client.get(self.url, **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(foreign.pk, [probe["id"] for probe in response.json()["probes"]])
        self.assertIsNone(response.json()["last_reading_at"])
        summaries = self.client.get("/api/thermal-zones/", **self.headers).json()["results"]
        summary = next(zone for zone in summaries if zone["id"] == self.zone.pk)
        self.assertNotIn(foreign.pk, [probe["id"] for probe in summary["probes"]])
        output = io.StringIO()
        call_command("link_iminilide_probe", organization=self.organization.name, list=True, stdout=output)
        self.assertNotIn("FOREIGN", output.getvalue())

    def test_active_organization_is_required_even_for_a_multi_organization_member(self):
        other = Organization.objects.create(name="Other active membership")
        zone = ThermalZone.objects.create(organization=other, name="Other zone")
        OrganizationMembership.objects.create(user=self.user, organization=other, role="viewer")
        url = f"/api/thermal-zones/{zone.pk}/probe-readings/"
        self.assertEqual(self.client.get(url, **self.headers).status_code, 404)
        self.assertEqual(self.client.get(url, HTTP_X_ORGANIZATION_ID=str(other.pk)).status_code, 200)
        with self.assertRaises(ThermalZone.DoesNotExist):
            recompute_daily_temperature(zone.pk, NOW.date(), organization_id=self.organization.pk)

    def test_manual_entry_freezes_probe_day_and_audit_failure_rolls_back(self):
        day = NOW.date()
        store_readings(self.left, [Reading(NOW - timedelta(hours=7), Decimal("0"))])
        # The stale reading falls on the previous local day.
        daily = DailyTemperature.objects.create(
            thermal_zone=self.zone, date=day, min_temperature_c=0,
            average_temperature_c=0, max_temperature_c=0, measurement_count=1,
            computed_from_probes=True,
        )
        url = f"/api/thermal-zones/{self.zone.pk}/temperature/"
        payload = {"measured_on": day.isoformat(), "temperature_c": "2"}
        with patch("apps.measurements.probe_views.timezone.now", return_value=NOW):
            with patch("apps.cultures.api_views.AuditLog.objects.create", side_effect=RuntimeError("Synthetic audit failure")):
                with self.assertRaisesRegex(RuntimeError, "Synthetic audit failure"):
                    self.client.post(url, payload, format="json", **self.headers)
            daily.refresh_from_db()
            self.assertTrue(daily.computed_from_probes)
            self.assertEqual(daily.measurement_count, 1)
            response = self.client.post(url, payload, format="json", **self.headers)
        self.assertEqual(response.status_code, 201)
        daily.refresh_from_db()
        self.assertFalse(daily.computed_from_probes)
        self.assertEqual(daily.average_temperature_c, Decimal("1.00"))
        store_readings(self.left, [Reading(NOW, Decimal("100"))])
        daily.refresh_from_db()
        self.assertEqual(daily.average_temperature_c, Decimal("1.00"))
        self.assertEqual(daily.measurement_count, 2)


class ProbeWindowRegressionTests(ProbeFixtureMixin, TestCase):
    def test_resume_after_repeated_hour_still_requests_full_transition_day(self):
        TemperatureMeasurement.objects.create(
            probe=self.left, measured_at=paris(2026, 10, 25, 3, 10), temperature_c=0,
        )
        start = collection_start(self.left, now=datetime(2026, 10, 25, 4, tzinfo=UTC))
        self.assertEqual(start, paris(2026, 10, 25, 0))

    def test_missing_minutes_remain_holes_and_zero_is_a_real_measurement(self):
        start = paris(2026, 10, 28, 10)
        parsed = parse_readings([
            ["2026/10/28 10:00:00", 0], ["2026/10/28 10:01:00", None], ["2026/10/28 10:02:00", 2],
        ])
        store_readings(self.left, parsed.readings)
        points = probe_series(self.left, start, start + timedelta(hours=1), 1)
        self.assertEqual([point[1] for point in points], [0.0, 2.0])
        self.assertEqual(points[1][0] - points[0][0], 2 * 60 * 1000)
        self.assertEqual(DailyTemperature.objects.get().measurement_count, 2)


class ProbeBucketConstraintTests(ProbeFixtureMixin, TestCase):
    def bucket_values(self):
        return dict(probe=self.left, last_measured_at=NOW, period_start=NOW, period_minutes=15,
                    min_temperature_c=0, average_temperature_c=0, max_temperature_c=0,
                    measurement_count=1, temperature_sum_c=0)

    def test_bucket_period_is_unique_per_probe(self):
        ProbeTemperatureBucket.objects.create(**self.bucket_values())
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProbeTemperatureBucket.objects.create(**self.bucket_values())

    def test_empty_count_or_period_is_rejected_by_database(self):
        for field in ("measurement_count", "period_minutes"):
            with self.subTest(field=field):
                values = self.bucket_values()
                values[field] = 0
                with self.assertRaises(IntegrityError), transaction.atomic():
                    ProbeTemperatureBucket.objects.create(**values)

    def test_channel_without_serial_is_rejected_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Probe.objects.create(organization=self.organization, thermal_zone=self.zone,
                                 code="incomplete channel", external_channel=1)
