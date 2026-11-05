"""Laboratory configuration writes under real PostgreSQL concurrency.

Opt-in disposable PostgreSQL only: run with config.test_settings and
POLYPBASE_TEST_POSTGRES=1. Each racer uses its own connection; blocking is
verified through pg_blocking_pids, not timing alone. These tests never create
or start a server and never use a shared database.
"""

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.exports.models import DataExport
from apps.exports.views import WeeklyMeasurementCSVExportAPIView
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from . import location_deletion
from .api_views import ThermalZoneDetailAPIView, ThermalZoneListCreateAPIView, _save_thermal_zone
from .automatic_box_creation import register_automatic_box_intent
from .models import AutomaticBoxCreationRequest, Box, BoxLocation, ThermalZone
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


def _intent_zone_lock(sql):
    return sql.startswith("SELECT") and "FOR NO KEY UPDATE" in sql and "FROM CULTURES_THERMALZONE" in sql


def _export_zone_lock(sql):
    # The export's lock reads the selected locations in id order.
    return _intent_zone_lock(sql) and "ORDER BY" in sql


def _organization_lock(sql):
    return sql.startswith("SELECT") and "FOR NO KEY UPDATE" in sql and "FROM ORGANIZATIONS_ORGANIZATION" in sql


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

    # -- deletion of never-used locations ----------------------------------------------

    def _delete(self, zone_id):
        request = APIRequestFactory().delete(
            f"/qa/thermal-zones/{zone_id}/", HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        )
        force_authenticate(request, user=self.admin)
        response = ThermalZoneDetailAPIView.as_view()(request, pk=zone_id)
        response.render()
        return response

    def _strain(self):
        species = Species.objects.create(scientific_name="Aurelia racing", genus_species_code="ARC")
        return Strain.objects.create(
            species=species, code="ARC-LAB-1", organization=self.organization, number=1, origin_code="LAB",
        )

    def _place_box(self, strain, zone_id, number):
        box = Box.objects.create(
            organization=self.organization, global_code=f"ARC-LAB-1.{number}", box_number=number,
            strain=strain, thermal_zone_id=zone_id,
        )
        BoxLocation.objects.create(box=box, thermal_zone_id=zone_id)
        return box

    def _assert_no_dangling_reference(self, zone_id):
        self.assertFalse(Box.objects.filter(thermal_zone_id=zone_id).exists())
        self.assertFalse(BoxLocation.objects.filter(thermal_zone_id=zone_id).exists())

    def test_box_placed_while_deletion_holds_the_lock_fails_and_nothing_dangles(self):
        strain = self._strain()
        zone_id = self.other_zone.pk

        def place():
            try:
                with transaction.atomic():
                    self._place_box(strain, zone_id, "001")
            except IntegrityError:
                return "refused"
            return "placed"

        deletion, placement = self._race(
            lambda: self._delete(zone_id), place,
            hold_sql=_zone_lock,
            attempt_sql=lambda sql: sql.startswith("INSERT INTO CULTURES_BOXLOCATION"),
        )
        # The placement waits for the deletion lock; the deleted row then
        # fails its foreign key check: one side wins, never both.
        self.assertEqual(deletion.status_code, 204, deletion.data)
        self.assertEqual(placement, "refused")
        self.assertFalse(ThermalZone.objects.filter(pk=zone_id).exists())
        self._assert_no_dangling_reference(zone_id)
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.DELETION).count(), 1)

    def test_uncommitted_placement_during_deletion_never_survives_with_the_location_gone(self):
        strain = self._strain()
        zone_id = self.other_zone.pk
        inserted, release = threading.Event(), threading.Event()

        def place():
            close_old_connections()
            try:
                with transaction.atomic():
                    self._place_box(strain, zone_id, "002")
                    inserted.set()
                    if not release.wait(15):
                        raise TimeoutError("Placement was not released.")
            except IntegrityError:
                return "refused"
            finally:
                connections.close_all()
            return "placed"

        with ThreadPoolExecutor(max_workers=1) as pool:
            placement = pool.submit(place)
            try:
                self.assertTrue(inserted.wait(8))
                deletion = self._delete(zone_id)
            finally:
                release.set()
            outcome = placement.result(timeout=20)

        zone_exists = ThermalZone.objects.filter(pk=zone_id).exists()
        if deletion.status_code == 204:
            self.assertEqual(outcome, "refused")
            self.assertFalse(zone_exists)
            self._assert_no_dangling_reference(zone_id)
        else:
            self.assertEqual(deletion.status_code, 409, deletion.data)
            self.assertEqual(outcome, "placed")
            self.assertTrue(zone_exists)
        self.assertEqual(
            AuditLog.objects.filter(action=AuditLog.Action.DELETION).count(), int(deletion.status_code == 204),
        )

    def test_edit_and_deletion_are_serialized_and_both_audited(self):
        zone_id = self.other_zone.pk
        edit, deletion = self._race(
            lambda: self._patch(zone_id, {"capacity": 0}), lambda: self._delete(zone_id),
            hold_sql=_zone_lock, attempt_sql=_zone_lock,
        )
        self.assertEqual(edit.status_code, 200, edit.data)
        self.assertEqual(deletion.status_code, 204, deletion.data)
        entry = AuditLog.objects.get(action=AuditLog.Action.DELETION)
        # The deletion read the committed edit: zero capacity, not missing.
        self.assertEqual(entry.metadata["valeurs"]["capacite"], 0)
        self.assertEqual(len(self._updates()), 1, "the edit audit is kept")

    def test_use_committed_before_deletion_is_refused(self):
        strain = self._strain()
        box = self._place_box(strain, self.other_zone.pk, "003")
        BoxLocation.objects.filter(box=box).update(ends_at=timezone.now())
        Box.objects.filter(pk=box.pk).update(thermal_zone=self.zone)
        response = self._delete(self.other_zone.pk)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["reasons"], ["history"])
        self.assertTrue(ThermalZone.objects.filter(pk=self.other_zone.pk).exists())

    # -- intents naming a location versus its deletion -----------------------------------

    def _register(self, zone_id, *, request_id=None, token=None):
        return register_automatic_box_intent(
            organization=self.organization, actor=self.admin,
            data={
                "request_id": str(request_id or uuid.uuid4()), "resume_token": str(token or uuid.uuid4()),
                "strain": self._intent_strain.pk, "thermal_zone": zone_id,
                "entered_on": "2026-11-05", "volume_liters": "0.00", "notes": "",
            },
        )

    @property
    def _intent_strain(self):
        strain = Strain.objects.filter(code="ARC-LAB-1").first()
        return strain or self._strain()

    def _intents_naming(self, zone_id):
        return [
            receipt for receipt in AutomaticBoxCreationRequest.objects.all()
            if location_deletion._references(receipt.intent, zone_id, set(), under_location_key=False)
        ]

    def test_intent_registered_first_blocks_the_deletion_and_keeps_its_reference(self):
        zone_id = self.other_zone.pk
        self._intent_strain  # created before the race, outside both transactions
        registration, deletion = self._race(
            lambda: self._register(zone_id), lambda: self._delete(zone_id),
            hold_sql=_intent_zone_lock, attempt_sql=_organization_lock,
        )
        # The deletion waited on the registration's locks, then saw its intent.
        self.assertFalse(registration.cancelled)
        self.assertEqual(registration.intent["thermal_zone"], zone_id)
        self.assertEqual(deletion.status_code, 409, deletion.data)
        self.assertEqual(deletion.data["reasons"], ["history"])
        self.assertTrue(ThermalZone.objects.filter(pk=zone_id).exists())
        self.assertEqual(len(self._intents_naming(zone_id)), 1)
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.DELETION).exists())

    def test_deletion_first_refuses_the_later_intent_without_storing_the_location(self):
        zone_id = self.other_zone.pk
        self._intent_strain
        deletion, registration = self._race(
            lambda: self._delete(zone_id), lambda: self._register(zone_id),
            hold_sql=_zone_lock, attempt_sql=_organization_lock,
        )
        self.assertEqual(deletion.status_code, 204, deletion.data)
        self.assertFalse(ThermalZone.objects.filter(pk=zone_id).exists())
        # Durable refusal, as for any invalid intent, and no reference to the
        # deleted location is committed.
        self.assertTrue(registration.cancelled)
        self.assertIn("thermal_zone", registration.failure)
        self.assertIsNone(registration.intent["thermal_zone"])
        self.assertEqual(self._intents_naming(zone_id), [])
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.DELETION).count(), 1)

    def test_invalid_intent_after_deletion_does_not_store_the_deleted_location(self):
        zone_id = self.other_zone.pk
        self.assertEqual(self._delete(zone_id).status_code, 204)
        receipt = register_automatic_box_intent(
            organization=self.organization, actor=self.admin,
            data={"request_id": str(uuid.uuid4()), "resume_token": str(uuid.uuid4()),
                  "strain": "not-a-strain", "thermal_zone": str(zone_id), "entered_on": "2026-11-05"},
        )
        self.assertTrue(receipt.cancelled)
        self.assertIsNone(receipt.intent["invalid_parameters"]["thermal_zone"])
        self.assertEqual(self._intents_naming(zone_id), [])

    def test_rolled_back_registration_releases_the_location_for_deletion(self):
        zone_id = self.other_zone.pk
        self._intent_strain

        def failing_registration():
            with patch.object(AutomaticBoxCreationRequest.objects, "create", side_effect=RuntimeError("boom")):
                try:
                    self._register(zone_id)
                except RuntimeError:
                    return "rolled back"
            return "committed"

        registration, deletion = self._race(
            failing_registration, lambda: self._delete(zone_id),
            hold_sql=_intent_zone_lock, attempt_sql=_organization_lock,
        )
        self.assertEqual(registration, "rolled back")
        self.assertEqual(deletion.status_code, 204, deletion.data)
        self.assertFalse(AutomaticBoxCreationRequest.objects.exists())

    def test_refused_deletion_releases_the_location_for_the_waiting_intent(self):
        zone_id = self.other_zone.pk
        self._intent_strain
        # Already used: the deletion is refused and rolls back.
        AuditLog.objects.create(
            organization=self.organization, action=AuditLog.Action.UPDATE, object_type="box",
            object_id="ARC-LAB-1.099", metadata={"to_thermal_zone_id": zone_id},
        )
        deletion, registration = self._race(
            lambda: self._delete(zone_id), lambda: self._register(zone_id),
            hold_sql=_zone_lock, attempt_sql=_organization_lock,
        )
        self.assertEqual(deletion.status_code, 409, deletion.data)
        self.assertFalse(registration.cancelled)
        self.assertEqual(registration.intent["thermal_zone"], zone_id)
        self.assertTrue(ThermalZone.objects.filter(pk=zone_id).exists())

    # -- authority at deletion time -----------------------------------------------------------

    def test_membership_revoked_during_the_request_refuses_the_deletion(self):
        zone_id = self.other_zone.pk

        def revoke():
            # Member management locks the organization first, then the membership.
            with transaction.atomic():
                Organization.objects.select_for_update().get(pk=self.organization.pk)
                OrganizationMembership.objects.filter(
                    organization=self.organization, user=self.admin,
                ).update(is_active=False)
            return "revoked"

        revocation, deletion = self._race(
            revoke, lambda: self._delete(zone_id),
            hold_sql=lambda sql: sql.startswith("UPDATE ACCOUNTS_ORGANIZATIONMEMBERSHIP"),
            attempt_sql=_organization_lock,
        )
        self.assertEqual(revocation, "revoked")
        # Entry check passed on the old state; the locked revalidation refuses.
        self.assertEqual(deletion.status_code, 403, deletion.data)
        self.assertTrue(ThermalZone.objects.filter(pk=zone_id).exists())
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.DELETION).exists())

    # -- recorded exports naming a location versus its deletion -------------------------------

    def _export_fixture(self):
        """Measurements in self.zone (used); self.other_zone stays never used."""
        strain = self._strain()
        box = self._place_box(strain, self.zone.pk, "050")
        BoxLocation.objects.filter(box=box).update(starts_at=timezone.make_aware(timezone.datetime(2025, 1, 1)))
        BiologicalMeasurement.objects.create(
            box=box, measured_on=timezone.datetime(2025, 1, 8).date(), polyp_count=0, ephyrae_count=0, user=self.admin,
        )

    def _export(self, zone_ids):
        request = APIRequestFactory().get(
            "/qa/exports/measurements.csv",
            {"date_from": "2025-01-01", "date_to": "2025-01-31", "zones": ",".join(str(pk) for pk in zone_ids)},
            HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
        )
        force_authenticate(request, user=self.admin)
        return WeeklyMeasurementCSVExportAPIView.as_view()(request)

    def _recorded_zone_lists(self):
        exports = [export.filters.get("zones") for export in DataExport.objects.order_by("pk")]
        audits = [entry.metadata.get("zones") for entry in AuditLog.objects.filter(action=AuditLog.Action.EXPORT)]
        return exports, audits

    def test_export_recorded_first_blocks_the_deletion_of_a_named_location(self):
        self._export_fixture()
        zone_id = self.other_zone.pk
        export, deletion = self._race(
            lambda: self._export([self.zone.pk, zone_id]), lambda: self._delete(zone_id),
            hold_sql=_export_zone_lock, attempt_sql=_zone_lock,
        )
        # The deletion waited on the export's location lock, then saw the record.
        self.assertEqual(export.status_code, 200)
        self.assertEqual(deletion.status_code, 409, deletion.data)
        self.assertEqual(deletion.data["reasons"], ["history"])
        self.assertTrue(ThermalZone.objects.filter(pk=zone_id).exists())
        self.assertEqual(self._recorded_zone_lists(), ([[self.zone.pk, zone_id]], [[self.zone.pk, zone_id]]))
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.DELETION).exists())

    def test_deletion_first_keeps_the_deleted_location_out_of_the_later_export(self):
        self._export_fixture()
        zone_id = self.other_zone.pk
        deletion, export = self._race(
            lambda: self._delete(zone_id), lambda: self._export([self.zone.pk, zone_id]),
            hold_sql=_zone_lock, attempt_sql=_export_zone_lock,
        )
        self.assertEqual(deletion.status_code, 204, deletion.data)
        self.assertFalse(ThermalZone.objects.filter(pk=zone_id).exists())
        # Same file, recorded without the deleted location; no dangling id anywhere.
        self.assertEqual(export.status_code, 200)
        self.assertEqual(self._recorded_zone_lists(), ([[self.zone.pk]], [[self.zone.pk]]))
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.DELETION).count(), 1)

    def test_failed_export_rolls_back_and_releases_the_location(self):
        self._export_fixture()
        zone_id = self.other_zone.pk

        def failing_export():
            with patch.object(DataExport.objects, "create", side_effect=RuntimeError("disk full")):
                try:
                    self._export([self.zone.pk, zone_id])
                except RuntimeError:
                    return "rolled back"
            return "recorded"

        export, deletion = self._race(
            failing_export, lambda: self._delete(zone_id),
            hold_sql=_export_zone_lock, attempt_sql=_zone_lock,
        )
        self.assertEqual(export, "rolled back")
        self.assertEqual(deletion.status_code, 204, deletion.data)
        self.assertFalse(DataExport.objects.exists())
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.EXPORT).exists())
