"""Box creation records an explicit initial measurement in the same transaction."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone as datetime_timezone
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements import services as measurement_services
from apps.measurements.models import BiologicalMeasurement

from . import automatic_box_creation as service
from .models import AutomaticBoxCreationRequest, Box, BoxCodeNamespace, BoxLocation, ThermalZone
from .services import move_box_to_thermal_zone
from apps.taxonomy.test_species_review_regressions import SpeciesReviewTransactionTestCase

from .test_automatic_box_creation import AutomaticBoxFixture


@override_settings(SECURE_SSL_REDIRECT=False)
class BoxCreationInitialMeasurementTests(AutomaticBoxFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def post(self, payload=None):
        return self.client.post(
            reverse("api_box_automatic"), payload if payload is not None else self.payload,
            content_type="application/json", HTTP_X_ORGANIZATION_ID=str(self.org.pk),
        )

    def assert_nothing_written(self):
        for model in (Box, BoxLocation, AuditLog, AutomaticBoxCreationRequest, BiologicalMeasurement):
            self.assertFalse(model.objects.exists(), model.__name__)

    def test_initial_measurement_values_origin_observer_date_and_audit(self):
        response = self.post({**self.payload, "entered_on": "2026-09-28", "initial_polyp_count": 37})
        self.assertEqual(response.status_code, 201, response.content)
        box = Box.objects.get()
        measurement = BiologicalMeasurement.objects.get()
        self.assertEqual(measurement.box_id, box.pk)
        self.assertEqual((measurement.polyp_count, measurement.ephyrae_count), (37, 0))
        # Only polyps and the approved ephyrae 0 are recorded; nothing else is invented.
        self.assertIsNone(measurement.strobila_count)
        self.assertIsNone(measurement.salinity_psu)
        self.assertEqual(measurement.notes, "")
        # The scientific date is the editable Box date, never the insertion timestamp.
        self.assertEqual((measurement.measured_on, box.entered_on), (date(2026, 9, 28), date(2026, 9, 28)))
        self.assertEqual(measurement.week_start, date(2026, 9, 28))
        self.assertEqual((measurement.origin, measurement.observer_status, measurement.temporal_precision),
                         ("box_creation", "known", "exact_date"))
        self.assertEqual((measurement.user_id, measurement.original_observer_id), (self.user.pk, self.user.pk))
        self.assertEqual((measurement.import_format, measurement.imported_by_id), ("", None))
        entry = AuditLog.objects.get(action="entry")
        self.assertEqual((entry.user_id, entry.organization_id, entry.object_id), (self.user.pk, self.org.pk, box.global_code))
        self.assertEqual(entry.metadata["origin"], "box_creation")
        self.assertEqual(entry.metadata["measurement_id"], measurement.pk)
        self.assertEqual((entry.metadata["valeurs"]["polypes"], entry.metadata["valeurs"]["ephyrules"]), (37, 0))
        self.assertEqual(AuditLog.objects.get(action="creation").metadata["valeurs"]["polypes_initiaux"], 37)

    def test_initial_location_and_measurement_use_selected_local_date(self):
        inserted_at = datetime(2026, 11, 17, 23, 30, tzinfo=datetime_timezone.utc)
        cases = [
            ("Europe/Paris", "2026-11-10", 1),  # Backdated creation.
            ("Europe/Paris", "2026-11-18", 1),  # Today locally, next day relative to UTC.
            ("Europe/Paris", "2026-11-19", 1),  # Future dates remain accepted.
            ("Europe/Paris", "2026-03-29", 1),  # Midnight before the DST jump.
            ("Europe/Paris", "2026-03-30", 2),
            ("Europe/Paris", "2026-10-25", 2),  # Midnight before the DST fallback.
            ("Europe/Paris", "2026-10-26", 1),
            ("Asia/Tokyo", "2026-11-10", 9),
            ("Pacific/Honolulu", "2026-11-10", -10),
        ]
        for zone_name, selected_date, offset_hours in cases:
            with self.subTest(zone=zone_name, date=selected_date), timezone.override(zone_name):
                before = timezone.now()
                with patch("django.utils.timezone.now", return_value=inserted_at):
                    response = self.post({**self.payload, "entered_on": selected_date,
                                          "request_id": str(uuid.uuid4())})
                self.assertEqual(response.status_code, 201, response.content)
                box = Box.objects.get(pk=response.json()["id"])
                measurement = BiologicalMeasurement.objects.get(box=box)
                location = BoxLocation.objects.get(box=box)
                expected = timezone.make_aware(datetime.combine(date.fromisoformat(selected_date), time.min))
                self.assertTrue(timezone.is_aware(location.starts_at))
                self.assertEqual(location.starts_at, expected)
                self.assertEqual(expected.utcoffset(), timedelta(hours=offset_hours))
                self.assertEqual(timezone.localtime(location.starts_at).date(), measurement.measured_on)
                self.assertEqual(measurement.measured_on, box.entered_on)
                self.assertEqual(response.json()["locations"][0]["starts_at"], expected.isoformat())
                # Technical registration timestamps are not scientific dates.
                self.assertEqual(box.automatic_creation_request.created_at, inserted_at)
                # This field's default captures the original clock callable.
                self.assertLessEqual(before, box.global_identity.created_at)
                self.assertLessEqual(box.global_identity.created_at, timezone.now())

    def test_later_movement_and_retry_preserve_the_initial_history(self):
        with timezone.override("Europe/Paris"):
            payload = {**self.payload, "entered_on": "2026-11-10"}
            self.assertEqual(self.post(payload).status_code, 201)
            box = Box.objects.get()
            initial = BoxLocation.objects.get(box=box)
            initial_start = initial.starts_at
            destination = ThermalZone.objects.create(organization=self.org, name="Next zone")
            moved_at = timezone.make_aware(datetime(2026, 11, 12, 14, 30))
            move_box_to_thermal_zone(
                box=box, thermal_zone=destination, moved_at=moved_at, user=self.user,
                notes="", expected_thermal_zone_id=self.zone.pk,
            )
            initial.refresh_from_db()
            self.assertEqual(initial.starts_at, initial_start)
            self.assertEqual(initial.ends_at, moved_at)
            self.assertEqual(BoxLocation.objects.get(box=box, thermal_zone=destination).starts_at, moved_at)
            movement = box.movements.get()
            self.assertEqual((movement.moved_at, movement.origin_location_id), (moved_at, initial.pk))
            before = list(BoxLocation.objects.filter(box=box).values())
            self.assertEqual(self.post(payload).status_code, 200)
            self.assertEqual(list(BoxLocation.objects.filter(box=box).values()), before)
            self.assertEqual(BiologicalMeasurement.objects.get(box=box).measured_on, date(2026, 11, 10))

    def test_zero_initial_polyps_is_a_real_count(self):
        response = self.post({**self.payload, "initial_polyp_count": 0})
        self.assertEqual(response.status_code, 201, response.content)
        measurement = BiologicalMeasurement.objects.get()
        self.assertEqual((measurement.polyp_count, measurement.ephyrae_count), (0, 0))
        detail = self.client.get(reverse("api_box_measurements", args=[measurement.box_id]),
                                 HTTP_X_ORGANIZATION_ID=str(self.org.pk)).json()
        rows = detail["results"] if isinstance(detail, dict) and "results" in detail else detail
        self.assertEqual((rows[0]["polyp_count"], rows[0]["ephyrae_count"]), (0, 0))

    def test_missing_or_invalid_initial_count_is_refused_without_writes(self):
        without = {key: value for key, value in self.payload.items() if key != "initial_polyp_count"}
        for payload in (without, {**self.payload, "initial_polyp_count": None},
                        {**self.payload, "initial_polyp_count": ""},
                        {**self.payload, "initial_polyp_count": -1},
                        {**self.payload, "initial_polyp_count": 1.5},
                        {**self.payload, "initial_polyp_count": "beaucoup"}):
            with self.subTest(payload=payload.get("initial_polyp_count", "missing")):
                response = self.post({**payload, "request_id": str(uuid.uuid4())})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("initial_polyp_count", response.json())
        self.assert_nothing_written()

    def test_replay_and_conflicting_count_never_add_a_measurement(self):
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(self.post().status_code, 200)
        conflict = self.post({**self.payload, "initial_polyp_count": 13})
        self.assertEqual((conflict.status_code, conflict.json()["code"]), (409, "box_creation_request_conflict"))
        self.assertEqual(BiologicalMeasurement.objects.count(), 1)
        self.assertEqual(BoxLocation.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="entry").count(), 1)

    def test_measurement_failure_rolls_back_the_whole_creation(self):
        with patch.object(BiologicalMeasurement.objects, "create", side_effect=IntegrityError("boom")):
            with self.assertRaises(IntegrityError):
                self.post()
        self.assert_nothing_written()
        self.assertFalse(BoxCodeNamespace.objects.filter(high_water__gt=0).exists())
        # The same key then succeeds once.
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(BiologicalMeasurement.objects.count(), 1)
        self.assertEqual(BoxLocation.objects.count(), 1)

    def test_actor_without_measurement_role_creates_nothing(self):
        self.membership.role = "viewer"
        self.membership.save()
        self.assertEqual(self.post().status_code, 403)
        self.assert_nothing_written()

    def test_measurement_api_exposes_the_initial_origin_and_ordinary_entries_stay_direct(self):
        box_id = self.post().json()["id"]
        url = reverse("api_box_measurements", args=[box_id])
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.org.pk)}
        rows = self.client.get(url, **headers).json()
        rows = rows["results"] if isinstance(rows, dict) and "results" in rows else rows
        self.assertEqual([(row["origin"], row["polyp_count"], row["ephyrae_count"]) for row in rows],
                         [("box_creation", 12, 0)])
        # Weekly uniqueness still applies to the initial measurement.
        same_week = self.client.post(url, {"measured_on": "2026-10-02", "polyp_count": 3, "ephyrae_count": 0},
                                     content_type="application/json", **headers)
        self.assertEqual(same_week.status_code, 409, same_week.content)
        later = self.client.post(url, {"measured_on": "2026-10-08", "polyp_count": 0, "ephyrae_count": 0},
                                 content_type="application/json", **headers)
        self.assertEqual(later.status_code, 201, later.content)
        self.assertEqual(sorted(BiologicalMeasurement.objects.values_list("origin", flat=True)),
                         ["box_creation", "direct_entry"])

    def test_box_list_first_measurement_is_the_box_date(self):
        self.post({**self.payload, "entered_on": "2026-09-30"})
        response = self.client.get(reverse("api_box_list"), HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        rows = response.json()
        rows = rows["results"] if isinstance(rows, dict) and "results" in rows else rows
        self.assertEqual(rows[0]["first_measurement_on"], "2026-09-30")

    def test_existing_history_is_never_relabeled_or_extended(self):
        self.post()
        box = Box.objects.get()
        before = list(BiologicalMeasurement.objects.values())
        with self.assertRaises(measurement_services.MeasurementWriteInvalid):
            measurement_services.create_box_creation_measurement(
                box=box, user=self.user, measured_on=date(2026, 11, 2), polyp_count=4,
            )
        self.assertEqual(list(BiologicalMeasurement.objects.values()), before)

    def test_database_allows_one_initial_measurement_with_known_observer(self):
        self.post()
        box = Box.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            BiologicalMeasurement.objects.create(
                box=box, measured_on=date(2026, 12, 7), polyp_count=1, ephyrae_count=0, user=self.user,
                original_observer=self.user, observer_status="known", origin="box_creation",
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            BiologicalMeasurement.objects.create(
                box=box, measured_on=date(2026, 12, 14), polyp_count=1, ephyrae_count=0,
                observer_status="not_recorded", origin="box_creation",
            )
        self.assertEqual(BiologicalMeasurement.objects.count(), 1)


class BoxCreationIntentInitialMeasurementTests(AutomaticBoxFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.payload = {**self.payload, "resume_token": str(uuid.uuid4())}

    def test_registered_intent_executes_once_with_its_initial_measurement(self):
        service.register_automatic_box_intent(organization=self.org, actor=self.user, data=self.payload)
        key = {field: self.payload[field] for field in ("request_id", "resume_token")}
        receipt = service.execute_automatic_box_intent(organization=self.org, actor=self.user, data=key)
        again = service.execute_automatic_box_intent(organization=self.org, actor=self.user, data=key)
        self.assertEqual(receipt.box_id, again.box_id)
        self.assertEqual(BoxLocation.objects.count(), 1)
        self.assertEqual(BiologicalMeasurement.objects.count(), 1)
        measurement = BiologicalMeasurement.objects.get()
        self.assertEqual((measurement.box_id, measurement.origin, measurement.polyp_count, measurement.ephyrae_count),
                         (receipt.box_id, "box_creation", 12, 0))

    def test_invalid_initial_count_is_a_durable_refusal(self):
        receipt = service.register_automatic_box_intent(
            organization=self.org, actor=self.user, data={**self.payload, "initial_polyp_count": -3},
        )
        self.assertTrue(receipt.cancelled)
        self.assertIn("initial_polyp_count", receipt.failure)
        self.assertFalse(Box.objects.exists())

    def test_lost_measurement_permission_leaves_intent_pending(self):
        service.register_automatic_box_intent(organization=self.org, actor=self.user, data=self.payload)
        OrganizationMembership.objects.filter(pk=self.membership.pk).update(role="viewer")
        self.user.is_superuser = True
        self.user.save()
        key = {field: self.payload[field] for field in ("request_id", "resume_token")}
        from rest_framework.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            service.execute_automatic_box_intent(organization=self.org, actor=self.user, data=key)
        receipt = AutomaticBoxCreationRequest.objects.get()
        self.assertEqual((receipt.box_id, receipt.cancelled), (None, False))
        self.assertFalse(Box.objects.exists())
        self.assertFalse(BiologicalMeasurement.objects.exists())


@skipUnless(connection.vendor == "postgresql", "Requires a new verified disposable PostgreSQL instance.")
class BoxCreationInitialMeasurementConcurrencyTests(AutomaticBoxFixture, SpeciesReviewTransactionTestCase):
    """Real row locks: retries and parallel creations never duplicate an initial measurement."""

    def run_parallel(self, payloads):
        barrier = Barrier(len(payloads))

        def worker(payload):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                box, created = service.create_automatic_box(organization=self.org, actor=self.user, data=payload)
                return box.pk, created
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
            return [future.result(timeout=60) for future in [pool.submit(worker, payload) for payload in payloads]]

    def test_identical_concurrent_requests_create_one_box_and_one_initial_measurement(self):
        results = self.run_parallel([dict(self.payload) for _ in range(3)])
        self.assertEqual(len({pk for pk, _created in results}), 1)
        self.assertEqual(sum(1 for _pk, created in results if created), 1)
        self.assertEqual(Box.objects.count(), 1)
        self.assertEqual(BiologicalMeasurement.objects.filter(origin="box_creation").count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="entry").count(), 1)

    def test_distinct_concurrent_creations_each_get_exactly_one_initial_measurement(self):
        payloads = [{**self.payload, "request_id": str(uuid.uuid4()), "initial_polyp_count": count}
                    for count in (0, 7, 30)]
        results = self.run_parallel(payloads)
        self.assertEqual(len({pk for pk, _created in results}), 3)
        rows = sorted(BiologicalMeasurement.objects.values_list("box_id", "polyp_count", "ephyrae_count", "origin"))
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({row[0] for row in rows}), 3)
        self.assertEqual(sorted(row[1] for row in rows), [0, 7, 30])
        self.assertEqual({(row[2], row[3]) for row in rows}, {(0, "box_creation")})
        self.assertEqual(sorted(Box.objects.values_list("box_number", flat=True)), ["001", "002", "003"])
