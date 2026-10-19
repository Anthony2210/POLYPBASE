"""Actual populated rollback refusal on the authorized PostgreSQL QA database."""

import os
from unittest import skipUnless

from django.conf import settings
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from . import test_historical_temperature_apply as acceptance
from . import test_historical_temperature_postgres as postgres
from .models import HistoricalTemperatureImportReceipt, HistoricalTemperatureReading


_ENABLED = (getattr(settings, "HISTORICAL_TEMPERATURE_PGQA", False)
            and os.getenv("POLYPBASE_TEST_POSTGRES") == "1"
            and os.getenv("POLYPBASE_HISTORICAL_POSTGRES_QA") == "1")


@skipUnless(_ENABLED, "Requires the dedicated disposable PostgreSQL migration harness")
class HistoricalTemperatureMigrationTests(TransactionTestCase):
    accept = acceptance.AcceptanceTests.accept
    _sql = postgres.PostgreSQLHistoricalTemperatureTests._sql
    _insert_reading = postgres.PostgreSQLHistoricalTemperatureTests._insert_reading
    _insert_evidence = postgres.PostgreSQLHistoricalTemperatureTests._insert_evidence

    def setUp(self):
        from config.historical_temperature_pgqa import assert_disposable, verify_schema

        assert_disposable(connection)
        verify_schema(connection)
        acceptance.AcceptanceTests.setUp(self)

    def _fixture_teardown(self):
        from config.historical_temperature_pgqa import cleanup_test_data

        cleanup_test_data()

    def test_populated_receipt_refuses_actual_executor_reverse_without_schema_loss(self):
        from config.historical_temperature_pgqa import verify_schema

        receipt, _ = self.accept()
        with self.assertRaisesMessage(RuntimeError, "Historical receipts exist"), transaction.atomic():
            MigrationExecutor(connection).migrate([("measurements", "0012_historical_temperature_guards")])
        self.assertEqual(HistoricalTemperatureImportReceipt.objects.get().pk, receipt.pk)
        self.assertEqual(HistoricalTemperatureReading.objects.count(), 1)
        verify_schema(connection)

    def test_populated_readings_refuse_reverse_atomically_even_with_empty_receipts(self):
        from config.historical_temperature_pgqa import verify_schema
        from .models import HistoricalTemperatureEvidence
        from .test_historical_temperature_plan import evidence

        with transaction.atomic():
            reading = HistoricalTemperatureReading.objects.create(
                biological_measurement=self.biology, iso_year=2026, iso_week=1, temperature_c="0",
            )
            HistoricalTemperatureEvidence.objects.create(reading=reading, **evidence(source_value="0"))
        self.assertFalse(HistoricalTemperatureImportReceipt.objects.exists())
        # Executor migrations are individually atomic by default. The outer
        # transaction also restores the earlier empty 0013 reverse on refusal.
        with self.assertRaisesMessage(RuntimeError, "Historical storage is not empty"), transaction.atomic():
            MigrationExecutor(connection).migrate([("measurements", "0011_historical_temperature_storage")])
        self.assertEqual(HistoricalTemperatureReading.objects.get().pk, reading.pk)
        self.assertEqual(HistoricalTemperatureEvidence.objects.count(), 1)
        verify_schema(connection)
