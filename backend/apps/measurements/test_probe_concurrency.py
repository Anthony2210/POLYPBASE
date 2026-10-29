"""Real row-lock races on opt-in disposable PostgreSQL only.

Run with config.test_settings and POLYPBASE_TEST_POSTGRES=1. These tests use
independent connections and verify blocking through pg_blocking_pids, not timing
alone. They never create/start a database server or use a shared database.
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from queue import Queue
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.models import OrganizationMembership
from apps.cultures.api_views import ThermalZoneManualTemperatureAPIView

from .iminilide import Reading
from .models import DailyTemperature, ProbeTemperatureBucket, TemperatureMeasurement
from .probe_collection import condense_probe_readings, recompute_daily_temperature, store_readings
from .probe_views import ThermalZoneProbeReadingsAPIView
from .test_probe_collection import NOW, ProbeFixtureMixin, paris

DISPOSABLE_POSTGRES = (
    os.getenv("POLYPBASE_TEST_POSTGRES") == "1"
    and connection.vendor == "postgresql"
    and connection.settings_dict.get("HOST") == "127.0.0.1"
    and str(connection.settings_dict.get("PORT")) == "55439"
    and connection.settings_dict.get("NAME") in {"subculture_qa", "test_subculture_qa"}
)


@skipUnless(DISPOSABLE_POSTGRES, "Requires opt-in disposable PostgreSQL at 127.0.0.1:55439.")
class ProbeConcurrencyTests(ProbeFixtureMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(connection.settings_dict["NAME"], "test_subculture_qa")
        self.user = get_user_model().objects.create_user(username="probe-racer", email="probe-racer@example.org")
        OrganizationMembership.objects.create(user=self.user, organization=self.organization, role="lab_technician")
        self.start = paris(2026, 10, 1, 10)

    def _fixture_teardown(self):
        # Existing append-only provenance guards intentionally reject TRUNCATE.
        # Bypass only those cleanup guards, only in this disposable test DB.
        self.assertTrue(DISPOSABLE_POSTGRES)
        self.assertEqual(connection.settings_dict["NAME"], "test_subculture_qa")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), inet_server_port()")
            self.assertEqual(cursor.fetchone(), ("test_subculture_qa", 55439))
            cursor.execute("SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid WHERE NOT t.tgisinternal AND (t.tgtype & 32) <> 0")
            triggers = cursor.fetchall()
            quote = connection.ops.quote_name
            try:
                for table, trigger in triggers:
                    cursor.execute(f"ALTER TABLE {quote(table)} DISABLE TRIGGER {quote(trigger)}")
                super()._fixture_teardown()
            finally:
                for table, trigger in triggers:
                    cursor.execute(f"ALTER TABLE {quote(table)} ENABLE TRIGGER {quote(trigger)}")

    def _worker(self, action, pids, *, entered=None, release=None, hold_sql=None, attempted=None):
        close_old_connections()
        held = False
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '15s'")
                cursor.execute("SET statement_timeout = '25s'")
                cursor.execute("SELECT pg_backend_pid()")
                pid = cursor.fetchone()[0]
            pids.put(pid)

            def observe(execute, sql, params, many, context):
                nonlocal held
                normalized = " ".join(sql.upper().replace('"', '').split())
                if attempted is not None and "FOR UPDATE" in normalized and "CULTURES_THERMALZONE" in normalized:
                    attempted.set()
                result = execute(sql, params, many, context)
                if hold_sql is not None and not held and hold_sql(normalized):
                    held = True
                    entered.set()
                    if not release.wait(15):
                        raise TimeoutError("Probe transaction was not released.")
                return result

            with connection.execute_wrapper(observe):
                result = action()
            self.assertFalse(connection.in_atomic_block)
            self.assertFalse(connection.needs_rollback)
            return result
        finally:
            connections.close_all()

    def _race(self, first_action, second_action, hold_sql):
        entered, release, attempted = threading.Event(), threading.Event(), threading.Event()
        first_pids, second_pids = Queue(), Queue()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self._worker, first_action, first_pids, entered=entered, release=release, hold_sql=hold_sql)
            try:
                first_pid = first_pids.get(timeout=8)
                self.assertTrue(entered.wait(8), "First connection did not reach the intended SQL boundary.")
                second = pool.submit(self._worker, second_action, second_pids, attempted=attempted)
                second_pid = second_pids.get(timeout=8)
                self.assertNotEqual(first_pid, second_pid)
                self.assertTrue(attempted.wait(8), "Second connection did not attempt the zone lock.")
                deadline = time.monotonic() + 5
                blocked = False
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [first_pid, second_pid])
                        blocked = cursor.fetchone()[0]
                    if blocked:
                        break
                    time.sleep(0.02)
                self.assertTrue(blocked, "PostgreSQL did not report the expected row-lock dependency.")
            finally:
                release.set()
            return first.result(timeout=10), second.result(timeout=10)

    def _seed_old_minutes(self):
        TemperatureMeasurement.objects.bulk_create([
            TemperatureMeasurement(probe=self.left, measured_at=self.start, temperature_c=20),
            TemperatureMeasurement(probe=self.left, measured_at=self.start + timedelta(minutes=1), temperature_c=30),
        ])

    def _recompute(self):
        return recompute_daily_temperature(self.zone.pk, self.start.date(), organization_id=self.organization.pk)

    def _condense(self):
        return condense_probe_readings(now=NOW)

    def _assert_daily(self, count=2, average="25.00"):
        daily = DailyTemperature.objects.get(thermal_zone=self.zone, date=self.start.date())
        self.assertEqual(daily.measurement_count, count)
        self.assertEqual(daily.average_temperature_c, Decimal(average))
        return daily

    def _read_api(self):
        request = APIRequestFactory().get(
            "/qa/probe-readings/",
            {"start": self.start.isoformat(), "end": (self.start + timedelta(hours=1)).isoformat()},
            HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        )
        force_authenticate(request, user=self.user)
        response = ThermalZoneProbeReadingsAPIView.as_view()(request, pk=self.zone.pk)
        self.assertEqual(response.status_code, 200)
        return response.data

    def _manual(self, day):
        request = APIRequestFactory().post(
            "/qa/manual-temperature/", {"measured_on": day.isoformat(), "temperature_c": "30"},
            format="json", HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        )
        force_authenticate(request, user=self.user)
        return ThermalZoneManualTemperatureAPIView.as_view()(request, pk=self.zone.pk).status_code

    def _assert_series(self, data):
        self.assertEqual(data["resolution_minutes"], 15)
        points = next(probe["points"] for probe in data["probes"] if probe["id"] == self.left.pk)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0][1:], [25.0, 20.0, 30.0])

    def test_recalculation_blocks_condensation_between_storage_tier_queries(self):
        self._seed_old_minutes()
        self._race(self._recompute, self._condense,
                   lambda sql: sql.startswith("SELECT") and "FROM MEASUREMENTS_TEMPERATUREMEASUREMENT" in sql)
        self._assert_daily()
        self.assertFalse(TemperatureMeasurement.objects.exists())
        self.assertEqual(ProbeTemperatureBucket.objects.get().measurement_count, 2)

    def test_condensation_blocks_recalculation_before_raw_rows_are_deleted(self):
        self._seed_old_minutes()
        self._race(self._condense, self._recompute,
                   lambda sql: sql.startswith("INSERT INTO MEASUREMENTS_PROBETEMPERATUREBUCKET"))
        self._assert_daily()
        self.assertEqual(ProbeTemperatureBucket.objects.get().temperature_sum_c, Decimal("50.00"))

    def test_api_read_blocks_condensation_between_storage_tier_queries(self):
        self._seed_old_minutes()
        data, _condensed = self._race(self._read_api, self._condense,
            lambda sql: sql.startswith("SELECT") and "FROM MEASUREMENTS_TEMPERATUREMEASUREMENT" in sql)
        self._assert_series(data)

    def test_condensation_blocks_api_read_until_both_storage_tiers_are_consistent(self):
        self._seed_old_minutes()
        _condensed, data = self._race(self._condense, self._read_api,
            lambda sql: sql.startswith("INSERT INTO MEASUREMENTS_PROBETEMPERATUREBUCKET"))
        self._assert_series(data)

    def test_duplicate_collectors_report_actual_insertions_and_one_daily_count(self):
        readings = [Reading(self.start, Decimal("0"))]
        first, second = self._race(
            lambda: store_readings(self.left, readings), lambda: store_readings(self.left, readings),
            lambda sql: sql.startswith("INSERT INTO MEASUREMENTS_TEMPERATUREMEASUREMENT"),
        )
        self.assertEqual(first[:2], (1, 0))
        self.assertEqual(second[:2], (0, 1))
        self._assert_daily(count=1, average="0.00")
        self.assertEqual(TemperatureMeasurement.objects.count(), 1)

    def test_parallel_left_right_collection_preserves_weighted_average(self):
        left = [Reading(self.start, Decimal("0"))]
        right = [Reading(self.start + timedelta(minutes=i), Decimal("12")) for i in range(3)]
        self._race(lambda: store_readings(self.left, left), lambda: store_readings(self.right, right),
                   lambda sql: sql.startswith("INSERT INTO MEASUREMENTS_TEMPERATUREMEASUREMENT"))
        daily = self._assert_daily(count=4, average="9.00")
        self.assertEqual(daily.min_temperature_c, 0)
        self.assertEqual(daily.max_temperature_c, 12)

    def test_collection_waits_for_condensation_and_does_not_reinsert_condensed_minutes(self):
        self._seed_old_minutes()
        readings = [Reading(self.start, Decimal("20")), Reading(NOW, Decimal("0"))]
        self._race(self._condense, lambda: store_readings(self.left, readings),
                   lambda sql: sql.startswith("INSERT INTO MEASUREMENTS_PROBETEMPERATUREBUCKET"))
        self.assertEqual(ProbeTemperatureBucket.objects.get().measurement_count, 2)
        self.assertEqual(list(TemperatureMeasurement.objects.values_list("measured_at", flat=True)), [NOW])

    def test_manual_entry_commits_before_collection_and_stays_protected(self):
        readings = [Reading(self.start, Decimal("0"))]
        status_code, _stored = self._race(
            lambda: self._manual(self.start.date()), lambda: store_readings(self.left, readings),
            lambda sql: sql.startswith("INSERT INTO AUDIT_AUDITLOG"),
        )
        self.assertEqual(status_code, 201)
        daily = self._assert_daily(count=1, average="30.00")
        self.assertFalse(daily.computed_from_probes)

    def test_fresh_collection_commits_before_manual_entry_and_causes_conflict(self):
        readings = [Reading(NOW - timedelta(minutes=1), Decimal("0"))]
        with patch("apps.measurements.probe_views.timezone.now", return_value=NOW):
            _stored, status_code = self._race(
                lambda: store_readings(self.left, readings), lambda: self._manual(NOW.date()),
                lambda sql: sql.startswith("INSERT INTO MEASUREMENTS_TEMPERATUREMEASUREMENT"),
            )
        self.assertEqual(status_code, 409)
        daily = DailyTemperature.objects.get()
        self.assertTrue(daily.computed_from_probes)
        self.assertEqual(daily.measurement_count, 1)
        self.assertEqual(daily.average_temperature_c, 0)


    def _replace_into(self, destination):
        from .probe_linking import link_probe
        return link_probe(organization=self.organization, zone=destination,
                          serial=self.left.external_serial, channel=self.left.external_channel,
                          code="Replacement G", replace=True)[0]

    def test_replacement_waits_for_last_collection_and_fences_its_final_minute(self):
        from apps.cultures.models import ThermalZone
        destination = ThermalZone.objects.create(organization=self.organization, name="Destination")
        readings = [Reading(self.start, Decimal("0"))]
        _stored, successor = self._race(
            lambda: store_readings(self.left, readings), lambda: self._replace_into(destination),
            lambda sql: sql.startswith("INSERT INTO MEASUREMENTS_TEMPERATUREMEASUREMENT"),
        )
        self.left.refresh_from_db()
        self.assertFalse(self.left.is_active)
        self.assertEqual(self.left.thermal_zone_id, self.zone.pk)
        created, _existing, _days = store_readings(successor, readings + [
            Reading(self.start + timedelta(minutes=1), Decimal("1")),
        ])
        self.assertEqual(created, 1)
        self.assertEqual(TemperatureMeasurement.objects.filter(probe=self.left).count(), 1)
        self.assertEqual(TemperatureMeasurement.objects.filter(probe=successor).get().temperature_c, 1)

    def test_replacement_blocks_late_collector_then_rejects_its_stale_probe(self):
        from apps.cultures.models import ThermalZone
        from .iminilide import IminilideError
        destination = ThermalZone.objects.create(organization=self.organization, name="Destination")
        self._seed_old_minutes()

        def late_collection():
            with self.assertRaises(IminilideError):
                store_readings(self.left, [Reading(self.start + timedelta(minutes=2), Decimal("100"))])

        successor, _rejected = self._race(
            lambda: self._replace_into(destination), late_collection,
            lambda sql: sql.startswith("INSERT INTO MEASUREMENTS_PROBE "),
        )
        self.assertFalse(TemperatureMeasurement.objects.filter(probe=successor).exists())
        self.assertEqual(TemperatureMeasurement.objects.filter(probe=self.left).count(), 2)
