"""Laboratory configuration writes under real PostgreSQL concurrency.

Opt-in disposable PostgreSQL only: run with config.test_settings and
POLYPBASE_TEST_POSTGRES=1. Each racer uses its own connection; blocking is
verified through pg_blocking_pids, not timing alone. These tests never create
or start a server and never use a shared database.
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.organizations.models import Organization

from .api_views import ThermalZoneDetailAPIView, ThermalZoneListCreateAPIView, _save_thermal_zone
from .models import ThermalZone
from .serializers import THERMAL_ZONE_DUPLICATE_NAME_MESSAGE, ThermalZoneCreateSerializer

DISPOSABLE_POSTGRES = (
    os.getenv("POLYPBASE_TEST_POSTGRES") == "1"
    and connection.vendor == "postgresql"
    and connection.settings_dict.get("HOST") == "127.0.0.1"
    and str(connection.settings_dict.get("PORT")) == "55439"
    and connection.settings_dict.get("NAME") in {"subculture_qa", "test_subculture_qa"}
)


def _normalized(sql):
    return " ".join(sql.upper().replace('"', "").split())


def _zone_lock(sql):
    return sql.startswith("SELECT") and "FOR UPDATE" in sql and "FROM CULTURES_THERMALZONE" in sql


def _zone_insert(sql):
    return sql.startswith("INSERT INTO CULTURES_THERMALZONE")


def _zone_update(sql):
    return sql.startswith("UPDATE CULTURES_THERMALZONE")


@skipUnless(DISPOSABLE_POSTGRES, "Requires opt-in disposable PostgreSQL at 127.0.0.1:55439.")
class LaboratoryConfigurationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.settings_dict["NAME"], "test_subculture_qa")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), inet_server_port()")
            self.assertEqual(cursor.fetchone(), ("test_subculture_qa", 55439))
        self.organization = Organization.objects.create(name="Concurrency laboratory", slug="concurrency-lab")
        self.admin = get_user_model().objects.create_user(
            username="zone-racer", email="zone-racer@example.org", password="secret",
        )
        OrganizationMembership.objects.create(
            user=self.admin, organization=self.organization, role=OrganizationMembership.Role.ADMIN,
        )
        self.zone = ThermalZone.objects.create(
            organization=self.organization, name="Armoire 15", capacity=10, salinity_psu="32.25",
            target_temperature_c="15.0",
        )
        self.other_zone = ThermalZone.objects.create(organization=self.organization, name="Armoire 20")

    def _fixture_teardown(self):
        # Existing append-only provenance guards intentionally reject TRUNCATE.
        # Bypass only those cleanup guards, only in this disposable test DB
        # (same approach as apps.measurements.test_probe_concurrency).
        self.assertTrue(DISPOSABLE_POSTGRES)
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), inet_server_port()")
            self.assertEqual(cursor.fetchone(), ("test_subculture_qa", 55439))
            cursor.execute(
                "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                "WHERE NOT t.tgisinternal AND (t.tgtype & 32) <> 0"
            )
            triggers = cursor.fetchall()
            quote = connection.ops.quote_name
            try:
                for table, trigger in triggers:
                    cursor.execute(f"ALTER TABLE {quote(table)} DISABLE TRIGGER {quote(trigger)}")
                super()._fixture_teardown()
            finally:
                for table, trigger in triggers:
                    cursor.execute(f"ALTER TABLE {quote(table)} ENABLE TRIGGER {quote(trigger)}")

    # -- request helpers --------------------------------------------------------

    def _patch(self, zone_id, payload):
        request = APIRequestFactory().patch(
            f"/qa/thermal-zones/{zone_id}/", payload, format="json",
            HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        )
        force_authenticate(request, user=self.admin)
        response = ThermalZoneDetailAPIView.as_view()(request, pk=zone_id)
        response.render()
        return response

    def _create(self, name):
        request = APIRequestFactory().post(
            "/qa/thermal-zones/", {"organization": self.organization.pk, "name": name}, format="json",
            HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        )
        force_authenticate(request, user=self.admin)
        response = ThermalZoneListCreateAPIView.as_view()(request)
        response.render()
        return response

    # -- race harness -------------------------------------------------------------

    def _worker(self, action, pids, *, entered=None, release=None, hold_sql=None, attempted=None, attempt_sql=None):
        close_old_connections()
        held = False
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '15s'")
                cursor.execute("SET statement_timeout = '25s'")
                cursor.execute("SELECT pg_backend_pid()")
                pids.put(cursor.fetchone()[0])

            def observe(execute, sql, params, many, context):
                nonlocal held
                normalized = _normalized(sql)
                if attempted is not None and attempt_sql(normalized):
                    attempted.set()
                result = execute(sql, params, many, context)
                if hold_sql is not None and not held and hold_sql(normalized):
                    held = True
                    entered.set()
                    if not release.wait(15):
                        raise TimeoutError("First transaction was not released.")
                return result

            with connection.execute_wrapper(observe):
                result = action()
            # The request left no open or broken transaction behind.
            self.assertFalse(connection.in_atomic_block)
            self.assertFalse(connection.needs_rollback)
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                self.assertEqual(cursor.fetchone(), (1,))
            return result
        finally:
            connections.close_all()

    def _race(self, first_action, second_action, *, hold_sql, attempt_sql):
        entered, release, attempted = threading.Event(), threading.Event(), threading.Event()
        first_pids, second_pids = Queue(), Queue()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                self._worker, first_action, first_pids, entered=entered, release=release, hold_sql=hold_sql,
            )
            try:
                first_pid = first_pids.get(timeout=8)
                self.assertTrue(entered.wait(8), "First connection did not reach the intended SQL boundary.")
                second = pool.submit(
                    self._worker, second_action, second_pids, attempted=attempted, attempt_sql=attempt_sql,
                )
                second_pid = second_pids.get(timeout=8)
                self.assertNotEqual(first_pid, second_pid)
                self.assertTrue(attempted.wait(8), "Second connection did not reach the contended statement.")
                deadline = time.monotonic() + 5
                blocked = False
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [first_pid, second_pid])
                        blocked = cursor.fetchone()[0]
                    if blocked:
                        break
                    time.sleep(0.02)
                self.assertTrue(blocked, "PostgreSQL did not report the expected lock dependency.")
            finally:
                release.set()
            return first.result(timeout=15), second.result(timeout=15)

    def _updates(self):
        return list(
            AuditLog.objects.filter(action=AuditLog.Action.UPDATE, object_type="thermal_zone").order_by("id")
        )

    # -- 1. concurrent partial updates ---------------------------------------------

    def test_partial_updates_of_different_fields_are_serialized_without_lost_update(self):
        first, second = self._race(
            lambda: self._patch(self.zone.pk, {"capacity": 40}),
            lambda: self._patch(self.zone.pk, {"salinity_psu": "35.50"}),
            hold_sql=_zone_lock,
            attempt_sql=_zone_lock,
        )

        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.zone.refresh_from_db()
        self.assertEqual(self.zone.capacity, 40)
        self.assertEqual(str(self.zone.salinity_psu), "35.50")
        self.assertEqual(str(self.zone.target_temperature_c), "15.0")
        capacity_log, salinity_log = self._updates()
        self.assertEqual(capacity_log.metadata["modifications"], {"capacite": {"avant": 10, "apres": 40}})
        # The second writer read the row only after the first committed.
        self.assertEqual(salinity_log.metadata["modifications"],
                         {"salinite_psu": {"avant": "32.25", "apres": "35.50"}})
        self.assertEqual(salinity_log.metadata["valeurs"]["capacite"], 40)

    def test_concurrent_rename_and_capacity_edit_keep_both_changes(self):
        first, second = self._race(
            lambda: self._patch(self.zone.pk, {"name": "Armoire 16"}),
            lambda: self._patch(self.zone.pk, {"capacity": 0}),
            hold_sql=_zone_lock,
            attempt_sql=_zone_lock,
        )

        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.zone.refresh_from_db()
        self.assertEqual((self.zone.name, self.zone.capacity), ("Armoire 16", 0))
        rename_log, capacity_log = self._updates()
        self.assertEqual(rename_log.metadata["modifications"], {"nom": {"avant": "Armoire 15", "apres": "Armoire 16"}})
        self.assertEqual(capacity_log.metadata["modifications"], {"capacite": {"avant": 10, "apres": 0}})
        self.assertEqual(capacity_log.metadata["valeurs"]["nom"], "Armoire 16")

    # -- 2. concurrent duplicate names ---------------------------------------------

    def test_concurrent_creations_with_the_same_name_return_a_controlled_error(self):
        first, second = self._race(
            lambda: self._create("Étuve 25"),
            lambda: self._create("Étuve 25"),
            hold_sql=_zone_insert,
            attempt_sql=_zone_insert,
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(second.data, {"name": [THERMAL_ZONE_DUPLICATE_NAME_MESSAGE]})
        self.assertEqual(ThermalZone.objects.filter(organization=self.organization, name="Étuve 25").count(), 1)
        self.assertEqual(
            AuditLog.objects.filter(action=AuditLog.Action.CREATION, object_type="thermal_zone").count(), 1,
        )

    def test_concurrent_renames_to_the_same_name_return_a_controlled_error(self):
        first, second = self._race(
            lambda: self._patch(self.zone.pk, {"name": "Armoire 30"}),
            lambda: self._patch(self.other_zone.pk, {"name": "Armoire 30"}),
            hold_sql=_zone_update,
            attempt_sql=_zone_update,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(second.data, {"name": [THERMAL_ZONE_DUPLICATE_NAME_MESSAGE]})
        self.other_zone.refresh_from_db()
        self.assertEqual(self.other_zone.name, "Armoire 20")
        self.assertEqual(len(self._updates()), 1)

    # -- 3. audit failure rollback ---------------------------------------------------

    def test_audit_failure_rolls_back_update_and_creation(self):
        with patch("apps.cultures.api_views.AuditLog.objects.create", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError):
                self._patch(self.zone.pk, {"capacity": 99, "salinity_psu": "0"})
            with self.assertRaises(RuntimeError):
                self._create("Audit rollback")

        self.assertFalse(connection.in_atomic_block)
        self.zone.refresh_from_db()
        self.assertEqual((self.zone.capacity, str(self.zone.salinity_psu)), (10, "32.25"))
        self.assertFalse(ThermalZone.objects.filter(name="Audit rollback").exists())
        self.assertFalse(AuditLog.objects.filter(object_type="thermal_zone").exists())

    # -- 4. transaction usable after a uniqueness failure ----------------------------

    def test_transaction_remains_usable_after_a_uniqueness_failure(self):
        serializer = ThermalZoneCreateSerializer(
            instance=self.other_zone, data={"name": "Armoire 15"}, partial=True,
        )
        # Skip validation to reach the database constraint, as a concurrent writer would.
        with patch.object(ThermalZoneCreateSerializer, "validate", lambda _serializer, attrs: attrs):
            self.assertTrue(serializer.is_valid(), serializer.errors)

        with transaction.atomic():
            with self.assertRaises(DRFValidationError) as raised:
                _save_thermal_zone(serializer, organization=self.organization)
            self.assertEqual(raised.exception.detail, {"name": [THERMAL_ZONE_DUPLICATE_NAME_MESSAGE]})
            # Only the savepoint was rolled back: the outer transaction still works.
            self.assertFalse(connection.needs_rollback)
            ThermalZone.objects.filter(pk=self.other_zone.pk).update(capacity=5)

        self.other_zone.refresh_from_db()
        self.assertEqual((self.other_zone.name, self.other_zone.capacity), ("Armoire 20", 5))
