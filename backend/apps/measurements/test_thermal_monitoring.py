"""Four-hour monitoring API regressions with synthetic, isolated data."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.accounts.models import OrganizationMembership
from apps.cultures.models import ThermalZone
from apps.organizations.models import Organization

from .models import DailyTemperature, Probe, ProbeTemperatureBucket, TemperatureMeasurement


NOW = datetime(2026, 11, 17, 1, 12, 34, 123000, tzinfo=timezone.utc)


class ThermalMonitoringTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Monitoring lab")
        self.other = Organization.objects.create(name="Other lab")
        self.zone = ThermalZone.objects.create(
            organization=self.organization, name="Monitoring", target_temperature_c=Decimal("10.0"),
        )
        self.other_zone = ThermalZone.objects.create(organization=self.other, name="Other")
        self.user = get_user_model().objects.create_user(
                    username="monitoring-viewer", email="monitoring@example.test",
                )
        self.membership = OrganizationMembership.objects.create(
            organization=self.organization, user=self.user, role=OrganizationMembership.Role.VIEWER,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.url = f"/api/thermal-zones/{self.zone.pk}/thermal-monitoring/"

    def probe(self, **kwargs):
        values = {
            "organization": self.organization, "thermal_zone": self.zone,
            "code": f"probe-{Probe.objects.count()}",
        }
        values.update(kwargs)
        return Probe.objects.create(**values)

    def raw(self, probe, value, age=0):
        return TemperatureMeasurement.objects.create(
            probe=probe, temperature_c=Decimal(value), measured_at=NOW - timedelta(seconds=age),
        )

    def response(self, **kwargs):
        with patch("apps.measurements.thermal_monitoring.timezone.now", return_value=NOW):
            return self.client.get(self.url, HTTP_X_ORGANIZATION_ID=str(self.organization.pk), **kwargs)

    def test_irregular_cadence_equal_probe_weights_and_exact_sources(self):
        first = self.probe()
        second = self.probe(is_active=False, sampling_interval_seconds=900)
        for value, age in (("0", 20), ("10", 21), ("10", 7000)):
            self.raw(first, value, age)
        self.raw(second, "20", 22)
        response = self.response()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        data = response.json()
        self.assertEqual(set(data), {
            "zone_id", "start", "end", "resolution_minutes", "target_temperature_c",
            "probes", "probe_average", "summary",
        })
        self.assertEqual(data["summary"], {
            "mean_temperature_c": 13.33, "target_deviation_c": 3.33,
            "min_temperature_c": 0, "max_temperature_c": 20,
            "contributing_probe_count": 2, "measurement_count": 4,
        })
        self.assertEqual(data["probe_average"], [[
            int(NOW.replace(second=0, microsecond=0).timestamp() * 1000), 12.5, 2,
        ]])
        points = data["probes"][0]["points"]
        sources = data["probes"][0]["point_sources"]
        self.assertEqual(len(points), 3)
        for point, source in zip(points, sources):
            self.assertEqual(point[2:], [None, None])
            self.assertEqual(source, [[point[0], None, f"{point[1]:.2f}", 1,
                                       point[1], point[1], point[0]]])
        self.assertFalse(data["probes"][1]["is_active"])

    def test_single_probe_zero_negative_and_no_target(self):
        probe = self.probe()
        self.raw(probe, "-2", 600)
        self.raw(probe, "0", 5)
        self.zone.target_temperature_c = None
        self.zone.save(update_fields=["target_temperature_c"])
        data = self.response().json()
        self.assertEqual(data["summary"], {
            "mean_temperature_c": -1, "target_deviation_c": None,
            "min_temperature_c": -2, "max_temperature_c": 0,
            "contributing_probe_count": 1, "measurement_count": 2,
        })
        self.assertEqual(data["probe_average"], [])

    def test_zero_mean_and_zero_target_are_not_missing(self):
        self.zone.target_temperature_c = 0
        self.zone.save(update_fields=["target_temperature_c"])
        self.raw(self.probe(), "0")
        summary = self.response().json()["summary"]
        for field in ("mean_temperature_c", "target_deviation_c", "min_temperature_c", "max_temperature_c"):
            self.assertEqual(summary[field], 0)

    def test_round_deviation_from_unrounded_mean_half_up(self):
        probe = self.probe()
        self.raw(probe, "0.09", 1)
        self.raw(probe, "0.10", 2)
        self.zone.target_temperature_c = Decimal("0.1")
        self.zone.save(update_fields=["target_temperature_c"])
        summary = self.response().json()["summary"]
        self.assertEqual(summary["mean_temperature_c"], 0.10)
        self.assertEqual(summary["target_deviation_c"], -0.01)

    def test_inclusive_four_hours_cross_midnight_and_future_exclusion(self):
        probe = self.probe()
        for age in (14400.000001, 14400, 7200, 0, -0.000001):
            self.raw(probe, "3", age)
        data = self.response(data={"start": "invalid", "end": "invalid", "group_sources": "1"}).json()
        self.assertEqual(data["resolution_minutes"], 1)
        self.assertEqual(datetime.fromisoformat(data["start"].replace("Z", "+00:00")), NOW - timedelta(hours=4))
        self.assertEqual(datetime.fromisoformat(data["end"].replace("Z", "+00:00")), NOW)
        self.assertEqual(data["summary"]["measurement_count"], 3)
        self.assertEqual([point[0] for point in data["probes"][0]["points"]], [
            int((NOW - timedelta(seconds=age)).timestamp() * 1000) for age in (14400, 7200, 0)
        ])

    def test_no_probes_and_absent_or_stale_readings(self):
        for state in ("no probes", "absent", "stale"):
            if state == "absent":
                probe = self.probe()
            elif state == "stale":
                self.raw(probe, "42", 14401)
            with self.subTest(state=state):
                data = self.response().json()
                self.assertEqual(data["summary"], {
                    "mean_temperature_c": None, "target_deviation_c": None,
                    "min_temperature_c": None, "max_temperature_c": None,
                    "contributing_probe_count": 0, "measurement_count": 0,
                })
                self.assertEqual(data["probe_average"], [])
                for metadata in data["probes"]:
                    self.assertEqual(metadata["points"], [])
                    self.assertEqual(metadata["point_sources"], [])

    def test_daily_and_buckets_never_substitute_raw_readings(self):
        probe = self.probe()
        for computed in (False, True):
            DailyTemperature.objects.create(
                thermal_zone=self.zone, date=NOW.date() - timedelta(days=int(computed)),
                average_temperature_c=99, min_temperature_c=99, max_temperature_c=99,
                measurement_count=100, computed_from_probes=computed,
            )
        ProbeTemperatureBucket.objects.create(
            probe=probe, period_start=NOW - timedelta(minutes=15), period_minutes=15,
            last_measured_at=NOW, last_temperature_c=99, temperature_sum_c=9900,
            average_temperature_c=99, min_temperature_c=99, max_temperature_c=99,
            measurement_count=100,
        )
        self.assertEqual(self.response().json()["summary"]["measurement_count"], 0)
        self.raw(probe, "0")
        self.assertEqual(self.response().json()["summary"]["mean_temperature_c"], 0)

    def test_all_probe_types_empty_metadata_and_historical_order(self):
        probes = []
        for index, (probe_type, _) in enumerate(Probe.ProbeType.choices):
            probe = self.probe(
                probe_type=probe_type, external_serial="logger" if index else "",
                external_channel=index if index else None, is_active=False,
                location="Shelf", sampling_interval_seconds=600,
            )
            probes.append(probe)
            self.raw(probe, str(index))
        empty = self.probe(code="empty", external_serial="aaa", external_channel=1)
        expected = list(Probe.objects.filter(pk__in=[p.pk for p in probes] + [empty.pk]).order_by(
            "external_serial", "external_channel", "code",
        ).values_list("pk", flat=True))
        data = self.response().json()
        self.assertEqual([p["id"] for p in data["probes"]], expected)
        self.assertEqual(data["summary"]["contributing_probe_count"], len(probes))
        self.assertEqual(next(p for p in data["probes"] if p["id"] == empty.pk)["points"], [])
        self.assertEqual(data["probes"][-1]["sampling_interval_seconds"], 600)

    def test_organization_scope_including_malformed_foreign_probe(self):
        own = self.probe()
        foreign = self.probe(organization=self.other, thermal_zone=self.other_zone)
        malformed = self.probe(organization=self.other)
        same_org_other_zone = self.probe(thermal_zone=ThermalZone.objects.create(
            organization=self.organization, name="Another zone",
        ))
        for probe in (foreign, malformed, same_org_other_zone):
            self.raw(probe, "99")
        self.raw(own, "1")
        data = self.response().json()
        self.assertEqual([p["id"] for p in data["probes"]], [own.pk])
        self.assertEqual(data["summary"]["mean_temperature_c"], 1)
        OrganizationMembership.objects.create(
            organization=self.other, user=self.user, role=OrganizationMembership.Role.VIEWER,
        )
        self.assertEqual(self.client.get(
            f"/api/thermal-zones/{self.other_zone.pk}/thermal-monitoring/",
            HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        ).status_code, 404)
        self.assertEqual(self.client.get(self.url, HTTP_X_ORGANIZATION_ID=str(self.other.pk)).status_code, 404)

    def test_authentication_membership_and_read_only_methods(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.response().status_code, 403)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get(self.url, HTTP_X_ORGANIZATION_ID=str(self.other.pk)).status_code, 403)
        self.assertEqual(self.client.get(self.url, HTTP_X_ORGANIZATION_ID="invalid").status_code, 403)
        for method in (self.client.post, self.client.put, self.client.patch, self.client.delete):
            self.assertEqual(method(self.url).status_code, 405)
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        self.assertEqual(self.response().status_code, 403)

    def test_one_raw_query_no_other_measurement_queries_or_writes(self):
        self.raw(self.probe(), "2")
        with CaptureQueriesContext(connection) as captured:
            response = self.response()
        self.assertEqual(response.status_code, 200)
        queries = [query["sql"] for query in captured.captured_queries]
        raw_queries = [sql for sql in queries if TemperatureMeasurement._meta.db_table in sql]
        self.assertEqual(len(raw_queries), 1)
        self.assertFalse(any(ProbeTemperatureBucket._meta.db_table in sql for sql in queries))
        self.assertFalse(any(DailyTemperature._meta.db_table in sql for sql in queries))
        self.assertTrue(all(sql.lstrip().upper().startswith("SELECT") for sql in queries), queries)
