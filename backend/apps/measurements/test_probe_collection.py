import io
import json
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import ThermalZone
from apps.organizations.models import Organization

from .iminilide import (
    IminilideAuthenticationError,
    IminilideClient,
    IminilideConfigurationError,
    IminilideError,
    format_since,
    parse_readings,
)
from .models import DailyTemperature, Probe, ProbeTemperatureBucket, TemperatureMeasurement
from .probe_collection import (
    collect_iminilide,
    collection_start,
    condense_probe_readings,
    recompute_daily_temperature,
)
from .probe_readings import zone_probe_monitoring

PARIS = ZoneInfo("Europe/Paris")
UTC = datetime_timezone.utc


def paris(*args):
    return datetime(*args, tzinfo=PARIS)


class ParseReadingsTests(TestCase):
    def test_parses_local_minutes_and_keeps_zero(self):
        # Raw cloud answer: the JSON escapes the slashes of its dates.
        payload = json.loads(
            '[["2026\\/10\\/28 00:00:00",25.4],["2026\\/10\\/28 00:01:00",0],["2026\\/10\\/28 00:02:00","4,5"]]'
        )
        parsed = parse_readings(payload, time_zone="Europe/Paris")
        self.assertEqual(
            [(reading.measured_at, reading.temperature_c) for reading in parsed.readings],
            [
                (paris(2026, 10, 28, 0, 0), Decimal("25.4")),
                (paris(2026, 10, 28, 0, 1), Decimal("0")),
                (paris(2026, 10, 28, 0, 2), Decimal("4.5")),
            ],
        )
        self.assertEqual(parsed.readings[0].measured_at.astimezone(UTC), datetime(2026, 10, 27, 23, 0, tzinfo=UTC))

    def test_absent_values_are_skipped_not_converted_to_zero(self):
        parsed = parse_readings([["2026/10/28 00:00:00", None], ["2026/10/28 00:01:00", ""]])
        self.assertEqual(parsed.readings, [])
        self.assertEqual(parsed.missing_values, 2)

    def test_values_outside_the_logger_range_and_bad_rows_are_counted(self):
        parsed = parse_readings(
            [
                ["2026/10/28 00:00:00", 999.9],
                ["2026/10/28 00:01:00", -150],
                ["not a date", 4],
                ["2026/10/28 00:02:00", "abc"],
                "garbage",
                ["2026/10/28 00:03:00", 250],
            ]
        )
        self.assertEqual([reading.temperature_c for reading in parsed.readings], [Decimal("250")])
        self.assertEqual(parsed.out_of_range_values, 2)
        self.assertEqual(parsed.invalid_rows, 3)

    def test_repeated_hour_at_the_end_of_summer_time_gets_distinct_instants(self):
        parsed = parse_readings(
            [
                ["2026/10/25 02:58:00", 1],
                ["2026/10/25 02:59:00", 2],
                ["2026/10/25 02:00:00", 3],
                ["2026/10/25 02:01:00", 4],
                ["2026/10/25 03:00:00", 5],
            ]
        )
        instants = [reading.measured_at.astimezone(UTC) for reading in parsed.readings]
        self.assertEqual(
            instants,
            [
                datetime(2026, 10, 25, 0, 58, tzinfo=UTC),
                datetime(2026, 10, 25, 0, 59, tzinfo=UTC),
                datetime(2026, 10, 25, 1, 0, tzinfo=UTC),
                datetime(2026, 10, 25, 1, 1, tzinfo=UTC),
                datetime(2026, 10, 25, 2, 0, tzinfo=UTC),
            ],
        )
        self.assertEqual(len(set(instants)), 5)

    def test_rejects_a_payload_that_is_not_a_list(self):
        with self.assertRaises(IminilideError):
            parse_readings({"error": "login"})

    def test_since_is_formatted_in_local_time(self):
        self.assertEqual(format_since(datetime(2026, 10, 28, 23, 0, tzinfo=UTC), "Europe/Paris"), "20261029000000")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class FakeOpener:
    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        return FakeResponse(self.answers.pop(0))


class IminilideClientTests(TestCase):
    def test_requires_credentials(self):
        with self.assertRaises(IminilideConfigurationError):
            IminilideClient(email="", password="")

    def test_signs_in_then_fetches_one_channel(self):
        opener = FakeOpener([b"<html>ok</html>", json.dumps([["2026/10/28 00:00:00", 25.4]]).encode()])
        client = IminilideClient(email="lab@example.org", password="secret", base_url="https://cloud.test", opener=opener)
        parsed = client.fetch_readings(serial="1162040825", channel=12, since=paris(2026, 10, 28, 0, 0))

        login, readings = opener.requests
        self.assertEqual(login.get_method(), "POST")
        self.assertEqual(login.full_url, "https://cloud.test/en/")
        self.assertEqual(
            parse_qs(login.data.decode()),
            {"action": ["se_connecter"], "email": ["lab@example.org"], "mdp": ["secret"]},
        )
        self.assertEqual(readings.full_url, "https://cloud.test/en/mesuresjson_1162040825_12_20261028000000")
        self.assertEqual(len(parsed.readings), 1)

    def test_retries_sign_in_once_then_reports_refused_session(self):
        opener = FakeOpener([b"<html></html>", b"<html>login</html>", b"<html></html>", b"<html>login</html>"])
        client = IminilideClient(email="lab@example.org", password="wrong", base_url="https://cloud.test", opener=opener)
        with self.assertRaises(IminilideAuthenticationError):
            client.fetch_readings(serial="1162040825", channel=1, since=paris(2026, 10, 28, 0, 0))
        self.assertEqual(len(opener.requests), 4)

    def test_rejects_unexpected_serial_or_channel(self):
        client = IminilideClient(email="a", password="b", opener=FakeOpener([]))
        with self.assertRaises(IminilideError):
            client.fetch_readings(serial="../admin", channel=1, since=paris(2026, 10, 28))
        with self.assertRaises(IminilideError):
            client.fetch_readings(serial="1162040825", channel=0, since=paris(2026, 10, 28))


class FakeClient:
    """Answers per channel with fixed payloads, recording each request."""

    def __init__(self, payloads, failing_channels=()):
        self.payloads = payloads
        self.failing_channels = set(failing_channels)
        self.calls = []

    def fetch_readings(self, *, serial, channel, since):
        self.calls.append((serial, channel, since))
        if channel in self.failing_channels:
            raise IminilideError("cloud unreachable")
        return parse_readings(self.payloads.get(channel, []))


class ProbeFixtureMixin:
    def setUp(self):
        self.organization = Organization.objects.create(name="Probe lab")
        self.zone = ThermalZone.objects.create(
            organization=self.organization, name="ETUVE 25", target_temperature_c=Decimal("25.0")
        )
        self.left = Probe.objects.create(
            organization=self.organization,
            thermal_zone=self.zone,
            code="ETUVE 25 G",
            probe_type=Probe.ProbeType.IMINILIDE,
            external_serial="1162040825",
            external_channel=12,
        )
        self.right = Probe.objects.create(
            organization=self.organization,
            thermal_zone=self.zone,
            code="ETUVE 25 D",
            probe_type=Probe.ProbeType.IMINILIDE,
            external_serial="1162040825",
            external_channel=11,
        )


NOW = datetime(2026, 10, 29, 1, 0, tzinfo=UTC)  # 02:00 in Paris


class ProbeConstraintTests(ProbeFixtureMixin, TestCase):
    def test_a_logger_channel_is_linked_once(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Probe.objects.create(
                organization=self.organization,
                thermal_zone=self.zone,
                code="duplicate",
                probe_type=Probe.ProbeType.IMINILIDE,
                external_serial="1162040825",
                external_channel=12,
            )

    def test_serial_and_channel_are_set_together(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Probe.objects.create(
                organization=self.organization,
                thermal_zone=self.zone,
                code="half",
                probe_type=Probe.ProbeType.IMINILIDE,
                external_serial="1162040825",
            )


class CollectionTests(ProbeFixtureMixin, TestCase):
    def payloads(self):
        return {
            12: [["2026/10/28 10:00:00", 25.0], ["2026/10/28 10:01:00", 26.0]],
            11: [["2026/10/28 10:00:00", 23.0], ["2026/10/28 10:01:00", 0]],
        }

    def test_stores_minutes_and_computes_the_zone_day_from_both_probes(self):
        result = collect_iminilide(client=FakeClient(self.payloads()), now=NOW)

        self.assertEqual(result.created, 4)
        self.assertEqual(TemperatureMeasurement.objects.filter(source="iminilide").count(), 4)
        daily = DailyTemperature.objects.get(thermal_zone=self.zone, date=date(2026, 10, 28))
        self.assertTrue(daily.computed_from_probes)
        self.assertEqual(daily.measurement_count, 4)
        self.assertEqual(daily.average_temperature_c, Decimal("18.50"))
        self.assertEqual(daily.min_temperature_c, Decimal("0.00"))
        self.assertEqual(daily.max_temperature_c, Decimal("26.00"))

    def test_running_twice_does_not_duplicate_readings(self):
        collect_iminilide(client=FakeClient(self.payloads()), now=NOW)
        result = collect_iminilide(client=FakeClient(self.payloads()), now=NOW)
        self.assertEqual(result.created, 0)
        self.assertEqual(TemperatureMeasurement.objects.count(), 4)

    def test_resumes_after_the_last_stored_reading_within_cloud_retention(self):
        self.assertEqual(collection_start(self.left, now=NOW), NOW - timedelta(days=10))
        collect_iminilide(client=FakeClient(self.payloads()), now=NOW)
        self.assertEqual(collection_start(self.left, now=NOW), paris(2026, 10, 28, 10, 1))

    def test_a_day_with_a_manual_value_is_never_overwritten(self):
        DailyTemperature.objects.create(
            thermal_zone=self.zone,
            date=date(2026, 10, 28),
            average_temperature_c=Decimal("24.00"),
            min_temperature_c=Decimal("24.00"),
            max_temperature_c=Decimal("24.00"),
            measurement_count=1,
        )
        result = collect_iminilide(client=FakeClient(self.payloads()), now=NOW)
        daily = DailyTemperature.objects.get(thermal_zone=self.zone, date=date(2026, 10, 28))
        self.assertEqual(result.days_kept_manual, 1)
        self.assertFalse(daily.computed_from_probes)
        self.assertEqual(daily.average_temperature_c, Decimal("24.00"))
        self.assertEqual(TemperatureMeasurement.objects.count(), 4)

    def test_one_failing_channel_does_not_stop_the_others(self):
        result = collect_iminilide(client=FakeClient(self.payloads(), failing_channels={12}), now=NOW)
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(TemperatureMeasurement.objects.filter(probe=self.right).count(), 2)

    def test_inactive_and_non_collected_probes_are_not_requested(self):
        self.right.is_active = False
        self.right.save()
        Probe.objects.create(organization=self.organization, thermal_zone=self.zone, code="manual")
        client = FakeClient(self.payloads())
        collect_iminilide(client=client, now=NOW)
        self.assertEqual([call[1] for call in client.calls], [12])

    def test_command_fails_clearly_without_credentials(self):
        with override_settings(IMINILIDE_EMAIL="", IMINILIDE_PASSWORD=""), self.assertRaises(CommandError):
            call_command("collect_iminilide", stdout=io.StringIO(), stderr=io.StringIO())

    def test_command_reports_failed_probes(self):
        with patch(
            "apps.measurements.probe_collection.IminilideClient",
            return_value=FakeClient(self.payloads(), failing_channels={11}),
        ):
            out, err = io.StringIO(), io.StringIO()
            with self.assertRaises(CommandError):
                call_command("collect_iminilide", stdout=out, stderr=err)
        self.assertIn("ETUVE 25 D: FAILED", err.getvalue())
        self.assertIn("ETUVE 25 G: 2 new", out.getvalue())


class CondensationTests(ProbeFixtureMixin, TestCase):
    def test_old_minutes_become_quarter_hour_buckets_and_the_day_is_unchanged(self):
        day = date(2026, 10, 1)
        start = paris(2026, 10, 1, 10, 0)
        values = [Decimal("24.0"), Decimal("25.0"), Decimal("26.5")]
        TemperatureMeasurement.objects.bulk_create(
            [
                TemperatureMeasurement(probe=self.left, measured_at=start + timedelta(minutes=index), temperature_c=value)
                for index, value in enumerate(values)
            ]
            + [
                TemperatureMeasurement(
                    probe=self.left, measured_at=start + timedelta(minutes=20), temperature_c=Decimal("0")
                )
            ]
        )
        recompute_daily_temperature(self.zone.id, day, organization_id=self.organization.pk)
        before = DailyTemperature.objects.get(thermal_zone=self.zone, date=day)

        result = condense_probe_readings(now=NOW)

        self.assertEqual(result.readings_condensed, 4)
        self.assertFalse(TemperatureMeasurement.objects.filter(probe=self.left).exists())
        buckets = list(ProbeTemperatureBucket.objects.filter(probe=self.left).order_by("period_start"))
        self.assertEqual(len(buckets), 2)
        self.assertEqual(buckets[0].period_start, start)
        self.assertEqual(buckets[0].measurement_count, 3)
        self.assertEqual(buckets[0].min_temperature_c, Decimal("24.00"))
        self.assertEqual(buckets[0].average_temperature_c, Decimal("25.17"))
        self.assertEqual(buckets[0].max_temperature_c, Decimal("26.50"))
        self.assertEqual(buckets[1].min_temperature_c, Decimal("0.00"))

        recompute_daily_temperature(self.zone.id, day, organization_id=self.organization.pk)
        after = DailyTemperature.objects.get(thermal_zone=self.zone, date=day)
        self.assertEqual(after.min_temperature_c, before.min_temperature_c)
        self.assertEqual(after.max_temperature_c, before.max_temperature_c)
        self.assertEqual(after.measurement_count, before.measurement_count)
        self.assertEqual(after.average_temperature_c, before.average_temperature_c)

    def test_recent_minutes_are_kept(self):
        TemperatureMeasurement.objects.create(
            probe=self.left, measured_at=NOW - timedelta(days=13), temperature_c=Decimal("25.0")
        )
        result = condense_probe_readings(now=NOW)
        self.assertEqual(result.readings_condensed, 0)
        self.assertEqual(TemperatureMeasurement.objects.count(), 1)

    def test_condensed_periods_never_receive_minutes_again(self):
        TemperatureMeasurement.objects.create(
            probe=self.left, measured_at=paris(2026, 10, 1, 10, 0), temperature_c=Decimal("25.0")
        )
        condense_probe_readings(now=NOW)
        client = FakeClient({12: [["2026/10/01 10:05:00", 30], ["2026/10/28 10:00:00", 25]]})
        collect_iminilide(client=client, now=NOW)
        self.assertEqual(
            list(TemperatureMeasurement.objects.filter(probe=self.left).values_list("measured_at", flat=True)),
            [paris(2026, 10, 28, 10, 0)],
        )

    def test_condensing_twice_is_harmless(self):
        TemperatureMeasurement.objects.create(
            probe=self.left, measured_at=paris(2026, 10, 1, 10, 0), temperature_c=Decimal("25.0")
        )
        condense_probe_readings(now=NOW)
        second = condense_probe_readings(now=NOW)
        self.assertEqual(second.readings_condensed, 0)
        self.assertEqual(ProbeTemperatureBucket.objects.count(), 1)


class ProbeApiTests(ProbeFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username="tech", email="tech@example.org")
        OrganizationMembership.objects.create(
            user=self.user, organization=self.organization, role=OrganizationMembership.Role.LAB_TECHNICIAN
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.headers = {"HTTP_X_ORGANIZATION_ID": str(self.organization.pk)}

    def add_minutes(self, probe, start, values):
        TemperatureMeasurement.objects.bulk_create(
            TemperatureMeasurement(probe=probe, measured_at=start + timedelta(minutes=index), temperature_c=value)
            for index, value in enumerate(values)
        )

    def test_zone_without_collected_probe_keeps_manual_entry(self):
        zone = ThermalZone.objects.create(organization=self.organization, name="Manual zone")
        monitoring = zone_probe_monitoring(zone)
        self.assertFalse(monitoring["has_collected_probes"])
        self.assertTrue(monitoring["manual_entry_available"])

    def test_fresh_probe_readings_block_manual_entry(self):
        now = datetime.now(tz=UTC)
        self.add_minutes(self.left, now - timedelta(minutes=5), [Decimal("25.0")])

        zones = self.client.get("/api/thermal-zones/", **self.headers).json()["results"]
        zone = next(item for item in zones if item["id"] == self.zone.pk)
        self.assertTrue(zone["probe_monitoring"]["has_collected_probes"])
        self.assertFalse(zone["probe_monitoring"]["manual_entry_available"])
        self.assertTrue(all(probe["is_collected"] for probe in zone["probes"]))

        response = self.client.post(
            f"/api/thermal-zones/{self.zone.pk}/temperature/",
            {"measured_on": now.date().isoformat(), "temperature_c": "24.5"},
            format="json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 409)
        self.assertFalse(AuditLog.objects.filter(object_type="thermal_zone").exists())

    def test_stale_probes_offer_manual_entry_again(self):
        now = datetime.now(tz=UTC)
        self.add_minutes(self.left, now - timedelta(hours=7), [Decimal("25.0")])
        response = self.client.post(
            f"/api/thermal-zones/{self.zone.pk}/temperature/",
            {"measured_on": now.date().isoformat(), "temperature_c": "24.5"},
            format="json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["probe_monitoring"]["manual_entry_available"])

    def test_manual_value_on_a_probe_day_freezes_it(self):
        now = datetime.now(tz=UTC)
        today = now.astimezone(PARIS).date()
        DailyTemperature.objects.create(
            thermal_zone=self.zone, date=today, average_temperature_c=Decimal("25.00"),
            min_temperature_c=Decimal("25.00"), max_temperature_c=Decimal("25.00"),
            measurement_count=10, computed_from_probes=True,
        )
        response = self.client.post(
            f"/api/thermal-zones/{self.zone.pk}/temperature/",
            {"measured_on": today.isoformat(), "temperature_c": "24.5"},
            format="json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 201)
        self.assertFalse(DailyTemperature.objects.get(thermal_zone=self.zone, date=today).computed_from_probes)

    @patch("apps.measurements.probe_views.timezone.now", return_value=NOW)
    def test_series_uses_minutes_for_short_periods(self, _now):
        start = paris(2026, 10, 28, 10, 0)
        self.add_minutes(self.left, start, [Decimal("25.0"), Decimal("0"), Decimal("26.0")])
        response = self.client.get(
            f"/api/thermal-zones/{self.zone.pk}/probe-readings/",
            {"start": "2026-10-28T09:00:00+01:00", "end": "2026-10-28T12:00:00+01:00"},
            **self.headers,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["resolution_minutes"], 1)
        left = next(probe for probe in data["probes"] if probe["code"] == "ETUVE 25 G")
        self.assertEqual([point[1] for point in left["points"]], [25.0, 0.0, 26.0])
        self.assertEqual(left["points"][0][0], int(start.timestamp() * 1000))

    def test_series_groups_long_periods_with_minimum_and_maximum(self):
        start = paris(2026, 10, 20, 10, 0)
        self.add_minutes(self.left, start, [Decimal("24.0"), Decimal("26.0")])
        ProbeTemperatureBucket.objects.create(
            probe=self.left, last_measured_at=paris(2026, 10, 10, 10, 0), period_start=paris(2026, 10, 10, 10, 0), period_minutes=15,
            min_temperature_c=Decimal("20"), average_temperature_c=Decimal("21"),
            max_temperature_c=Decimal("22"), measurement_count=15, temperature_sum_c=Decimal("315"),
        )
        response = self.client.get(
            f"/api/thermal-zones/{self.zone.pk}/probe-readings/",
            {"start": "2026-10-01T00:00:00+02:00", "end": "2026-10-28T00:00:00+01:00"},
            **self.headers,
        )
        data = response.json()
        self.assertEqual(data["resolution_minutes"], 15)
        left = next(probe for probe in data["probes"] if probe["code"] == "ETUVE 25 G")
        self.assertEqual(left["points"][0][1:], [21.0, 20.0, 22.0])
        self.assertEqual(left["points"][1][1:], [25.0, 24.0, 26.0])

    def test_series_is_scoped_to_the_active_organization(self):
        other = Organization.objects.create(name="Other lab")
        other_zone = ThermalZone.objects.create(organization=other, name="ETUVE 25")
        response = self.client.get(f"/api/thermal-zones/{other_zone.pk}/probe-readings/", **self.headers)
        self.assertEqual(response.status_code, 404)

    def test_series_rejects_invalid_periods(self):
        url = f"/api/thermal-zones/{self.zone.pk}/probe-readings/"
        self.assertEqual(
            self.client.get(url, {"start": "2026-10-28T12:00:00Z", "end": "2026-10-28T10:00:00Z"}, **self.headers).status_code,
            400,
        )
        self.assertEqual(
            self.client.get(url, {"start": "2024-01-01T00:00:00Z", "end": "2026-10-28T10:00:00Z"}, **self.headers).status_code,
            400,
        )
        self.assertEqual(self.client.get(url, {"start": "yesterday"}, **self.headers).status_code, 400)


class LinkCommandTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Aquarium de Paris")
        self.zone = ThermalZone.objects.create(organization=self.organization, name="Etuve 25")

    def link(self, **options):
        defaults = {
            "organization": "aquarium de paris",
            "zone": "etuve 25",
            "serial": "1162040825",
            "channel": 11,
            "code": "ETUVE 25 D",
        }
        defaults.update(options)
        call_command("link_iminilide_probe", stdout=io.StringIO(), **defaults)

    def test_creates_then_updates_the_same_channel_with_audit(self):
        self.link()
        self.link(code="ETUVE 25 DROITE")
        probe = Probe.objects.get()
        self.assertEqual(probe.code, "ETUVE 25 DROITE")
        self.assertEqual(probe.external_channel, 11)
        self.assertEqual(probe.probe_type, Probe.ProbeType.IMINILIDE)
        self.assertEqual(AuditLog.objects.filter(object_type="probe").count(), 2)

    def test_refuses_unknown_zone_and_bad_channel(self):
        with self.assertRaises(CommandError):
            self.link(zone="nowhere")
        with self.assertRaises(CommandError):
            self.link(channel=0)
        self.assertFalse(Probe.objects.exists())
