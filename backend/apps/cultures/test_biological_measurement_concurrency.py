import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from . import api_views as cultures_api
from .api_views import BoxMeasurementListCreateAPIView
from .models import Box, ThermalZone


@skipUnless(
    connection.vendor == "postgresql",
    "Biological measurement concurrency requires PostgreSQL row locks.",
)
class BiologicalMeasurementConcurrencyTests(TransactionTestCase):
    reset_sequences = True
    measured_on = date(2026, 9, 1)

    def setUp(self):
        user_model = get_user_model()
        self.first_user = user_model.objects.create_user(username="measurement-user-one")
        self.second_user = user_model.objects.create_user(username="measurement-user-two")
        self.organization = Organization.objects.create(name="Measurement concurrency QA")
        for user in (self.first_user, self.second_user):
            OrganizationMembership.objects.create(
                user=user,
                organization=self.organization,
                role=OrganizationMembership.Role.LAB_TECHNICIAN,
            )
        species = Species.objects.create(
            scientific_name="Aurelia measurement concurrency",
            genus_species_code="AQC",
        )
        strain = Strain.objects.create(species=species, code="AQC-QA-1")
        zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Zone QA",
        )
        self.box = Box.objects.create(
            organization=self.organization,
            global_code="AQC-QA-1.001",
            box_number="001",
            strain=strain,
            thermal_zone=zone,
        )

    def _post_measurement(
        self,
        user,
        *,
        polyp_count,
        ephyrae_count,
        box_lock_attempted=None,
    ):
        close_old_connections()
        try:
            request = APIRequestFactory().post(
                "/qa/measurements/",
                {
                    "measured_on": self.measured_on.isoformat(),
                    "polyp_count": polyp_count,
                    "ephyrae_count": ephyrae_count,
                },
                format="json",
                HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
            )
            force_authenticate(request, user=user)
            if box_lock_attempted is None:
                response = BoxMeasurementListCreateAPIView.as_view()(
                    request,
                    box_id=self.box.pk,
                )
            else:
                def observe_box_lock(execute, sql, params, many, context):
                    if "FOR UPDATE" in sql and "cultures_box" in sql:
                        box_lock_attempted.set()
                    return execute(sql, params, many, context)

                with connection.execute_wrapper(observe_box_lock):
                    response = BoxMeasurementListCreateAPIView.as_view()(
                        request,
                        box_id=self.box.pk,
                    )
            response.render()
            return response
        finally:
            connections.close_all()

    def _run_ordered_concurrent_posts(self, *, first_values, second_values):
        first_inside_transaction = threading.Event()
        second_box_lock_attempted = threading.Event()
        release_first = threading.Event()
        original_sync = cultures_api._sync_polyp_drop_alert

        def blocking_sync(*, box, measurement, user):
            original_sync(box=box, measurement=measurement, user=user)
            if user.pk == self.first_user.pk:
                first_inside_transaction.set()
                if not release_first.wait(timeout=10):
                    raise TimeoutError("Timed out while coordinating concurrent measurements.")

        with patch.object(
            cultures_api,
            "_sync_polyp_drop_alert",
            blocking_sync,
        ), ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(
                self._post_measurement,
                self.first_user,
                polyp_count=first_values[0],
                ephyrae_count=first_values[1],
            )
            if not first_inside_transaction.wait(timeout=10):
                raise TimeoutError("First measurement did not reach its transaction.")
            second_future = executor.submit(
                self._post_measurement,
                self.second_user,
                polyp_count=second_values[0],
                ephyrae_count=second_values[1],
                box_lock_attempted=second_box_lock_attempted,
            )
            if not second_box_lock_attempted.wait(timeout=10):
                raise TimeoutError("Second measurement did not attempt the box lock.")
            release_first.set()
            first_response = first_future.result(timeout=15)
            second_response = second_future.result(timeout=15)

        return first_response, second_response

    def _assert_serialized_result(self, first_response, second_response, expected_values):
        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(first_response.data["id"], second_response.data["id"])
        self.assertEqual(
            BiologicalMeasurement.objects.filter(
                box=self.box,
                measured_on=self.measured_on,
            ).count(),
            1,
        )
        measurement = BiologicalMeasurement.objects.get(
            box=self.box,
            measured_on=self.measured_on,
        )
        self.assertEqual(measurement.polyp_count, expected_values[0])
        self.assertEqual(measurement.ephyrae_count, expected_values[1])
        self.assertEqual(measurement.user, self.second_user)

        audit = AuditLog.objects.get(
            object_type="box",
            metadata__measurement_id=measurement.id,
        )
        self.assertEqual(audit.action, AuditLog.Action.UPDATE)
        self.assertEqual(audit.user, self.second_user)
        self.assertEqual(audit.metadata["valeurs"]["polypes"], expected_values[0])
        self.assertEqual(audit.metadata["valeurs"]["ephyrules"], expected_values[1])

    def test_concurrent_first_entries_are_serialized_as_create_then_update(self):
        first_response, second_response = self._run_ordered_concurrent_posts(
            first_values=(10, 2),
            second_values=(20, 4),
        )

        self._assert_serialized_result(first_response, second_response, (20, 4))

    def test_concurrent_update_preserves_zero_as_the_second_value(self):
        first_response, second_response = self._run_ordered_concurrent_posts(
            first_values=(20, 4),
            second_values=(0, 0),
        )

        self._assert_serialized_result(first_response, second_response, (0, 0))
