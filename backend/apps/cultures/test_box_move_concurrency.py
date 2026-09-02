import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
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

from . import api_views as cultures_api
from .api_views import BoxMoveAPIView
from .models import Box, BoxLocation, BoxMovement, ThermalZone


@skipUnless(
    connection.vendor == "postgresql",
    "Box movement concurrency requires PostgreSQL row locks.",
)
class BoxMoveConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        user_model = get_user_model()
        self.user_one = user_model.objects.create_user(username="move-user-one")
        self.user_two = user_model.objects.create_user(username="move-user-two")
        self.organization = Organization.objects.create(name="Box movement concurrency QA")
        for user in (self.user_one, self.user_two):
            OrganizationMembership.objects.create(
                user=user,
                organization=self.organization,
                role=OrganizationMembership.Role.LAB_TECHNICIAN,
            )
        species = Species.objects.create(
            scientific_name="Aurelia movement concurrency",
            genus_species_code="AMC",
        )
        strain = Strain.objects.create(species=species, code="AMC-QA-1")
        self.zone_a = ThermalZone.objects.create(
            organization=self.organization,
            name="Zone A",
        )
        self.zone_b = ThermalZone.objects.create(
            organization=self.organization,
            name="Zone B",
        )
        self.zone_c = ThermalZone.objects.create(
            organization=self.organization,
            name="Zone C",
        )
        self.box = Box.objects.create(
            organization=self.organization,
            global_code="AMC-QA-1.001",
            box_number="001",
            strain=strain,
            status=Box.Status.ACTIVE,
            thermal_zone=self.zone_a,
        )
        self.initial_location = BoxLocation.objects.create(
            box=self.box,
            thermal_zone=self.zone_a,
            starts_at=timezone.now() - timedelta(days=1),
        )

    def _move(self, user, destination, moved_at):
        close_old_connections()
        try:
            request = APIRequestFactory().post(
                "/qa/box-move/",
                {
                    "expected_thermal_zone_id": self.zone_a.pk,
                    "thermal_zone_id": destination.pk,
                    "moved_at": moved_at,
                    "notes": f"Concurrent request to {destination.name}",
                },
                format="json",
                HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
            )
            force_authenticate(request, user=user)
            response = BoxMoveAPIView.as_view()(request, box_id=self.box.pk)
            response.render()
            return response
        finally:
            connections.close_all()

    def _run_concurrent_moves(self, first_destination, second_destination):
        barrier = threading.Barrier(2, timeout=10)
        original_move = cultures_api.move_box_to_thermal_zone

        def synchronized_move(*args, **kwargs):
            barrier.wait()
            return original_move(*args, **kwargs)

        moved_at = timezone.now().isoformat()
        with patch.object(
            cultures_api,
            "move_box_to_thermal_zone",
            synchronized_move,
        ), ThreadPoolExecutor(max_workers=2) as executor:
            responses = [
                future.result(timeout=15)
                for future in (
                    executor.submit(self._move, self.user_one, first_destination, moved_at),
                    executor.submit(self._move, self.user_two, second_destination, moved_at),
                )
            ]

        success = next(response for response in responses if response.status_code == 200)
        conflict = next(response for response in responses if response.status_code == 409)
        return success, conflict

    def _assert_single_committed_move(self, success, conflict):
        self.box.refresh_from_db()
        self.initial_location.refresh_from_db()
        open_locations = BoxLocation.objects.filter(
            box=self.box,
            ends_at__isnull=True,
            end_date_unknown=False,
        )
        movement = BoxMovement.objects.get(box=self.box)
        audit = AuditLog.objects.get(
            object_type="box",
            object_id=self.box.global_code,
            description__startswith="Box moved to",
        )

        self.assertEqual(open_locations.count(), 1)
        current_location = open_locations.get()
        self.assertEqual(self.box.thermal_zone_id, current_location.thermal_zone_id)
        self.assertEqual(movement.from_thermal_zone_id, self.zone_a.id)
        self.assertEqual(movement.to_thermal_zone_id, current_location.thermal_zone_id)
        self.assertEqual(audit.metadata["movement_id"], movement.id)
        self.assertEqual(success.data["thermal_zone"]["id"], current_location.thermal_zone_id)
        self.assertEqual(conflict.data["code"], "box_location_changed")
        self.assertEqual(
            conflict.data["current_thermal_zone_id"],
            current_location.thermal_zone_id,
        )
        self.assertIsNotNone(self.initial_location.ends_at)
        self.assertEqual(BoxLocation.objects.filter(box=self.box).count(), 2)
        self.assertEqual(BoxMovement.objects.filter(box=self.box).count(), 1)
        self.assertEqual(
            AuditLog.objects.filter(
                object_type="box",
                object_id=self.box.global_code,
                description__startswith="Box moved to",
            ).count(),
            1,
        )

    def test_concurrent_moves_to_different_destinations_reject_stale_request(self):
        success, conflict = self._run_concurrent_moves(self.zone_b, self.zone_c)

        self._assert_single_committed_move(success, conflict)

    def test_concurrent_moves_to_same_destination_reject_stale_request(self):
        success, conflict = self._run_concurrent_moves(self.zone_b, self.zone_b)

        self._assert_single_committed_move(success, conflict)
        self.box.refresh_from_db()
        self.assertEqual(self.box.thermal_zone, self.zone_b)
