import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from . import movement_corrections, services
from .api_views import BoxDeactivateAPIView, BoxMoveAPIView, BoxMovementDetailAPIView
from .models import MOVEMENT_WEEK_TIME_ZONE, Box, BoxLocation, BoxMovement, ThermalZone

PARIS = MOVEMENT_WEEK_TIME_ZONE


@skipUnless(
    connection.vendor == "postgresql",
    "Weekly movement concurrency requires PostgreSQL row locks.",
)
class BoxMovementWeekConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        user_model = get_user_model()
        self.user_one = user_model.objects.create_user(username="week-race-one", email="one@example.org")
        self.user_two = user_model.objects.create_user(username="week-race-two", email="two@example.org")
        self.organization = Organization.objects.create(name="Weekly movement concurrency QA")
        for user in (self.user_one, self.user_two):
            OrganizationMembership.objects.create(
                user=user, organization=self.organization, role=OrganizationMembership.Role.ADMIN,
            )
        species = Species.objects.create(scientific_name="Aurelia week race", genus_species_code="AWR")
        strain = Strain.objects.create(species=species, code="AWR-QA-1", organization=self.organization, number=1)
        self.zone_a = ThermalZone.objects.create(organization=self.organization, name="Zone A")
        self.zone_b = ThermalZone.objects.create(organization=self.organization, name="Zone B")
        self.zone_c = ThermalZone.objects.create(organization=self.organization, name="Zone C")
        self.box = Box.objects.create(
            organization=self.organization, global_code="AWR-QA-1.001", box_number="001",
            strain=strain, status=Box.Status.ACTIVE, thermal_zone=self.zone_a,
        )
        now = timezone.now()
        self.week_start = datetime.combine(BoxMovement.week_start_for(now), datetime.min.time(), tzinfo=PARIS)
        BoxLocation.objects.create(box=self.box, thermal_zone=self.zone_a, starts_at=now - timedelta(days=30))

    # Request helpers ---------------------------------------------------------

    def _call(self, view, method, path, payload, user, **kwargs):
        close_old_connections()
        try:
            factory = getattr(APIRequestFactory(), method)
            request = factory(path, payload, format="json", HTTP_X_ORGANIZATION_ID=str(self.organization.pk))
            force_authenticate(request, user=user)
            response = view.as_view()(request, **kwargs)
            response.render()
            return response
        finally:
            connections.close_all()

    def _post_move(self, user, destination, moved_at, expected):
        return self._call(BoxMoveAPIView, "post", "/qa/move/", {
            "expected_thermal_zone_id": expected.pk,
            "thermal_zone_id": destination.pk,
            "moved_at": moved_at.isoformat(),
        }, user, box_id=self.box.pk)

    def _patch(self, user, movement, payload):
        return self._call(BoxMovementDetailAPIView, "patch", "/qa/correct/", payload, user,
                          box_id=self.box.pk, pk=movement.pk)

    def _deactivate(self, user):
        return self._call(BoxDeactivateAPIView, "post", "/qa/deactivate/", {"reason": "Race"}, user,
                          box_id=self.box.pk)

    def _race(self, first, second, *, barrier_targets):
        """Run two callables after both reached the service entry point."""
        barrier = threading.Barrier(2, timeout=10)
        patches = []
        for module, name in barrier_targets:
            original = getattr(module, name)

            def synchronized(*args, _original=original, **kwargs):
                barrier.wait()
                return _original(*args, **kwargs)

            patches.append(patch.object(module, name, synchronized))
        for active in patches:
            active.start()
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(first), executor.submit(second)]
                return [future.result(timeout=20) for future in futures]
        finally:
            for active in patches:
                active.stop()

    def _ordinary_move(self, destination, moved_at):
        return services.move_box_to_thermal_zone(
            box=self.box, thermal_zone=destination, moved_at=moved_at, user=self.user_one, notes="",
        )

    # Tests -------------------------------------------------------------------

    def test_concurrent_same_week_service_moves_keep_one_ordinary_movement(self):
        moved_at = timezone.now()

        def attempt(destination):
            close_old_connections()
            try:
                self._ordinary_move(destination, moved_at)
                return "moved"
            except services.BoxMovementWeekConflict:
                return "week_conflict"
            finally:
                connections.close_all()

        import apps.cultures.services as service_module
        results = self._race(
            lambda: attempt(self.zone_b), lambda: attempt(self.zone_c),
            barrier_targets=[(service_module, "_locked_box")],
        )
        self.assertCountEqual(results, ["moved", "week_conflict"])
        self.assertEqual(self.box.movements.filter(kind=BoxMovement.Kind.ORDINARY).count(), 1)
        self.assertEqual(BoxLocation.objects.filter(box=self.box, ends_at__isnull=True).count(), 1)

    def test_concurrent_same_week_posts_leave_one_movement_and_one_conflict(self):
        moved_at = timezone.now()
        from . import api_views
        responses = self._race(
            lambda: self._post_move(self.user_one, self.zone_b, moved_at, self.zone_a),
            lambda: self._post_move(self.user_two, self.zone_c, moved_at, self.zone_a),
            barrier_targets=[(api_views, "move_box_to_thermal_zone")],
        )
        self.assertCountEqual([response.status_code for response in responses], [200, 409])
        self.assertEqual(self.box.movements.count(), 1)
        self.box.refresh_from_db()
        self.assertEqual(self.box.thermal_zone_id, self.box.movements.get().to_thermal_zone_id)

    def test_concurrent_patches_with_the_same_revision(self):
        movement = self._ordinary_move(self.zone_b, timezone.now() - timedelta(minutes=5))
        responses = self._race(
            lambda: self._patch(self.user_one, movement, {"expected_revision": 0, "notes": "one"}),
            lambda: self._patch(self.user_two, movement, {"expected_revision": 0, "notes": "two"}),
            barrier_targets=[(movement_corrections, "_validated_correction_data")],
        )
        self.assertCountEqual([response.status_code for response in responses], [200, 409])
        conflict = next(response for response in responses if response.status_code == 409)
        self.assertEqual(json.loads(conflict.content)["code"], "box_movement_changed")
        movement.refresh_from_db()
        self.assertEqual(movement.revision, 1)
        self.assertEqual(AuditLog.objects.filter(metadata__event="box_movement_correction").count(), 1)

    def test_post_racing_a_patch_into_the_same_week(self):
        now = timezone.now()
        # Terminal movement of the previous week, then both writers target this week.
        movement = self._ordinary_move(self.zone_b, self.week_start - timedelta(days=2))
        patch_time = self.week_start + (now - self.week_start) / 2
        from . import api_views
        responses = self._race(
            lambda: self._post_move(self.user_one, self.zone_c, now, self.zone_b),
            lambda: self._patch(self.user_two, movement, {"expected_revision": 0, "moved_at": patch_time.isoformat()}),
            barrier_targets=[
                (api_views, "move_box_to_thermal_zone"),
                (movement_corrections, "_validated_correction_data"),
            ],
        )
        statuses = sorted(response.status_code for response in responses)
        self.assertEqual(statuses, [200, 409])
        conflict = next(response for response in responses if response.status_code == 409)
        self.assertEqual(json.loads(conflict.content)["code"], "box_movement_week_conflict")
        current_week = BoxMovement.week_start_for(now)
        self.assertEqual(
            self.box.movements.filter(kind=BoxMovement.Kind.ORDINARY, iso_week_start=current_week).count(), 1,
        )

    def test_patch_racing_a_location_change_keeps_history_consistent(self):
        movement = self._ordinary_move(self.zone_b, self.week_start - timedelta(days=2))
        from . import api_views
        responses = self._race(
            lambda: self._post_move(self.user_one, self.zone_a, timezone.now(), self.zone_b),
            lambda: self._patch(self.user_two, movement, {"expected_revision": 0, "to_thermal_zone_id": self.zone_c.pk}),
            barrier_targets=[
                (api_views, "move_box_to_thermal_zone"),
                (movement_corrections, "_validated_correction_data"),
            ],
        )
        self.assertCountEqual([response.status_code for response in responses], [200, 409])
        self._assert_consistent_history()

    def test_patch_racing_a_deactivation_keeps_history_consistent(self):
        movement = self._ordinary_move(self.zone_b, self.week_start - timedelta(days=2))
        from . import api_views
        responses = self._race(
            lambda: self._deactivate(self.user_one),
            lambda: self._patch(self.user_two, movement, {"expected_revision": 0, "to_thermal_zone_id": self.zone_c.pk}),
            barrier_targets=[
                (api_views, "deactivate_box"),
                (movement_corrections, "_validated_correction_data"),
            ],
        )
        statuses = sorted(response.status_code for response in responses)
        self.assertIn(statuses, ([200, 200], [200, 409]))
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.INACTIVE)
        self.assertIsNone(self.box.thermal_zone_id)
        self._assert_consistent_history()

    def _assert_consistent_history(self):
        self.box.refresh_from_db()
        open_periods = list(BoxLocation.objects.filter(box=self.box, ends_at__isnull=True, end_date_unknown=False))
        self.assertLessEqual(len(open_periods), 1)
        if open_periods:
            self.assertEqual(open_periods[0].thermal_zone_id, self.box.thermal_zone_id)
        for movement in self.box.movements.exclude(destination_location=None):
            destination = movement.destination_location
            self.assertEqual(destination.thermal_zone_id, movement.to_thermal_zone_id)
            self.assertEqual(destination.starts_at, movement.moved_at)
            if movement.origin_location_id:
                self.assertEqual(movement.origin_location.ends_at, movement.moved_at)
