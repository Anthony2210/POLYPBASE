import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import date
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.db.models.query import QuerySet
from django.test import TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import DailyTemperature
from apps.organizations.models import Organization

from .api_views import ThermalZoneManualTemperatureAPIView
from .models import ThermalZone


@skipUnless(
    connection.vendor == "postgresql",
    "Manual temperature concurrency requires PostgreSQL row locks.",
)
class ManualTemperatureConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        user_model = get_user_model()
        self.user_one = user_model.objects.create_user(username="temperature-user-one")
        self.user_two = user_model.objects.create_user(username="temperature-user-two")
        self.organization = Organization.objects.create(name="Temperature concurrency QA")
        for user in (self.user_one, self.user_two):
            OrganizationMembership.objects.create(
                user=user,
                organization=self.organization,
                role=OrganizationMembership.Role.LAB_TECHNICIAN,
            )
        self.zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Temperature concurrency zone",
            target_temperature_c=Decimal("15.0"),
        )
        self.measured_on = date(2026, 9, 1)

    def _record_temperature(self, user, temperature_c):
        close_old_connections()
        try:
            request = APIRequestFactory().post(
                "/qa/manual-temperature/",
                {
                    "measured_on": self.measured_on.isoformat(),
                    "temperature_c": str(temperature_c),
                },
                format="json",
                HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
            )
            force_authenticate(request, user=user)
            response = ThermalZoneManualTemperatureAPIView.as_view()(request, pk=self.zone.pk)
            response.render()
            return response
        finally:
            connections.close_all()

    def _record_two_concurrent_temperatures(self):
        first_aggregate_ready = threading.Event()
        release_first_request = threading.Event()
        first_call_lock = threading.Lock()
        first_call_pending = True
        original_get_or_create = QuerySet.get_or_create

        def hold_first_aggregate(queryset, *args, **kwargs):
            nonlocal first_call_pending
            result = original_get_or_create(queryset, *args, **kwargs)
            if queryset.model is not DailyTemperature:
                return result
            with first_call_lock:
                should_hold = first_call_pending
                first_call_pending = False
            if should_hold:
                first_aggregate_ready.set()
                if not release_first_request.wait(timeout=10):
                    raise TimeoutError("Timed out while holding the first temperature transaction.")
            return result

        with patch.object(
            QuerySet,
            "get_or_create",
            hold_first_aggregate,
        ), ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                self._record_temperature,
                self.user_one,
                Decimal("20.00"),
            )
            self.assertTrue(first_aggregate_ready.wait(timeout=10))
            second = executor.submit(
                self._record_temperature,
                self.user_two,
                Decimal("30.00"),
            )
            try:
                with self.assertRaises(FutureTimeoutError):
                    second.result(timeout=0.25)
            finally:
                release_first_request.set()
            responses = [first.result(timeout=10), second.result(timeout=10)]

        self.assertEqual([response.status_code for response in responses], [201, 201])
        self.assertEqual(
            AuditLog.objects.filter(
                organization=self.organization,
                object_type="thermal_zone",
                description__startswith="Manual temperature recorded",
            ).count(),
            2,
        )

    def test_concurrent_updates_preserve_every_temperature(self):
        DailyTemperature.objects.create(
            thermal_zone=self.zone,
            date=self.measured_on,
            min_temperature_c=Decimal("10.00"),
            average_temperature_c=Decimal("10.00"),
            max_temperature_c=Decimal("10.00"),
            measurement_count=1,
        )

        self._record_two_concurrent_temperatures()

        aggregate = DailyTemperature.objects.get(
            thermal_zone=self.zone,
            date=self.measured_on,
        )
        self.assertEqual(aggregate.measurement_count, 3)
        self.assertEqual(aggregate.average_temperature_c, Decimal("20.00"))
        self.assertEqual(aggregate.min_temperature_c, Decimal("10.00"))
        self.assertEqual(aggregate.max_temperature_c, Decimal("30.00"))

    def test_concurrent_first_readings_create_one_exact_aggregate(self):
        self._record_two_concurrent_temperatures()

        aggregates = DailyTemperature.objects.filter(
            thermal_zone=self.zone,
            date=self.measured_on,
        )
        self.assertEqual(aggregates.count(), 1)
        aggregate = aggregates.get()
        self.assertEqual(aggregate.measurement_count, 2)
        self.assertEqual(aggregate.average_temperature_c, Decimal("25.00"))
        self.assertEqual(aggregate.min_temperature_c, Decimal("20.00"))
        self.assertEqual(aggregate.max_temperature_c, Decimal("30.00"))
