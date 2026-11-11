"""Persistence of probe cadence and exact last readings through condensation."""

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from config.migration_test_utils import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from apps.cultures.models import ThermalZone
from apps.organizations.models import Organization

from .models import DailyTemperature, Probe, ProbeTemperatureBucket, TemperatureMeasurement
from .probe_collection import condense_probe_readings, recompute_daily_temperature


START = datetime(2026, 10, 1, 10, tzinfo=timezone.utc)
NOW = START + timedelta(days=30)
AGGREGATE_FIELDS = (
    "min_temperature_c", "average_temperature_c", "max_temperature_c",
    "measurement_count", "temperature_sum_c",
)


class ProbePersistenceTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Persistence lab")
        self.zone = ThermalZone.objects.create(
            organization=self.organization, name="Persistence zone", target_temperature_c=25,
        )
        self.probe = Probe.objects.create(
            organization=self.organization, thermal_zone=self.zone, code="Persistence probe",
            probe_type=Probe.ProbeType.IMINILIDE, external_serial="persistence", external_channel=1,
        )

    def reading(self, minute, value):
        return TemperatureMeasurement.objects.create(
            probe=self.probe, measured_at=START + timedelta(minutes=minute), temperature_c=Decimal(value),
        )

    def bucket(self, minute, value):
        return ProbeTemperatureBucket.objects.create(
            probe=self.probe, period_start=START, period_minutes=15,
            last_measured_at=START + timedelta(minutes=minute), last_temperature_c=value,
            min_temperature_c=10, average_temperature_c=20, max_temperature_c=30,
            measurement_count=2, temperature_sum_c=40,
        )

    def condense(self):
        return condense_probe_readings(now=NOW, retention_days=14, bucket_minutes=15)

    def test_sampling_interval_defaults_to_one_minute(self):
        self.probe.refresh_from_db()
        self.assertEqual(self.probe.sampling_interval_seconds, 60)

    def test_allowed_sampling_intervals_validate_and_persist(self):
        for interval in (60, 600, 900):
            with self.subTest(interval=interval):
                self.probe.sampling_interval_seconds = interval
                self.probe.full_clean()
                self.probe.save(update_fields=["sampling_interval_seconds"])
                self.probe.refresh_from_db()
                self.assertEqual(self.probe.sampling_interval_seconds, interval)

    def test_invalid_sampling_intervals_fail_model_validation(self):
        for interval in (None, -60, 0, 1, 120, 601, 901):
            with self.subTest(interval=interval):
                self.probe.sampling_interval_seconds = interval
                with self.assertRaises(ValidationError) as error:
                    self.probe.full_clean()
                self.assertIn("sampling_interval_seconds", error.exception.message_dict)

    def test_database_rejects_invalid_sampling_intervals_without_model_validation(self):
        for interval in (None, -60, 0, 1, 120, 601, 901):
            with self.subTest(interval=interval):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Probe.objects.filter(pk=self.probe.pk).update(sampling_interval_seconds=interval)
        self.probe.refresh_from_db()
        self.assertEqual(self.probe.sampling_interval_seconds, 60)

    def test_new_bucket_keeps_exact_latest_value_including_zero_and_negative(self):
        for latest in ("0", "-2.75", "22.34"):
            with self.subTest(latest=latest):
                # Insert out of time order; latest is neither the minimum nor the mean in general.
                self.reading(12, latest)
                self.reading(1, "-10")
                self.reading(5, "40")
                result = self.condense()
                bucket = ProbeTemperatureBucket.objects.get(probe=self.probe)
                self.assertEqual(bucket.last_measured_at, START + timedelta(minutes=12))
                self.assertEqual(bucket.last_temperature_c, Decimal(latest))
                self.assertEqual(bucket.measurement_count, 3)
                self.assertEqual(bucket.temperature_sum_c, Decimal("30") + Decimal(latest))
                self.assertEqual(bucket.min_temperature_c, Decimal("-10"))
                self.assertEqual(bucket.max_temperature_c, Decimal("40"))
                self.assertEqual(result.buckets_created, 1)
                self.assertEqual(result.readings_condensed, 3)
                self.assertFalse(TemperatureMeasurement.objects.filter(probe=self.probe).exists())
                bucket.delete()

    def test_last_values_are_kept_separately_for_each_period(self):
        self.reading(14, "0")
        self.reading(16, "-3.25")
        self.reading(20, "11.25")
        self.condense()
        self.assertEqual(
            list(ProbeTemperatureBucket.objects.filter(probe=self.probe).values_list(
                "last_measured_at", "last_temperature_c",
            )),
            [(START + timedelta(minutes=14), Decimal("0")),
             (START + timedelta(minutes=20), Decimal("11.25"))],
        )

    def test_merges_choose_latest_timestamp_and_do_not_guess_tied_values(self):
        cases = (
            # existing minute, existing value, incoming minute, incoming value, expected value
            (5, Decimal("25"), 10, "0", Decimal("0")),
            (10, Decimal("0"), 5, "25", Decimal("0")),
            (5, None, 10, "-2", Decimal("-2")),
            (10, None, 5, "25", None),
            (10, Decimal("0"), 10, "0", Decimal("0")),
            (10, Decimal("25"), 10, "25", Decimal("25")),
            (10, Decimal("25"), 10, "26", None),
            (10, Decimal("26"), 10, "25", None),
            (10, None, 10, "25", None),
            (10, None, 10, "0", None),
        )
        for old_minute, old_value, new_minute, new_value, expected in cases:
            with self.subTest(case=(old_minute, old_value, new_minute, new_value)):
                bucket = self.bucket(old_minute, old_value)
                self.reading(new_minute, new_value)
                result = self.condense()
                bucket.refresh_from_db()
                self.assertEqual(bucket.last_measured_at, START + timedelta(minutes=max(old_minute, new_minute)))
                self.assertEqual(bucket.last_temperature_c, expected)
                self.assertEqual(bucket.measurement_count, 3)
                self.assertEqual(bucket.temperature_sum_c, Decimal("40") + Decimal(new_value))
                self.assertEqual(bucket.min_temperature_c, min(Decimal("10"), Decimal(new_value)))
                self.assertEqual(bucket.max_temperature_c, max(Decimal("30"), Decimal(new_value)))
                self.assertEqual(result.buckets_merged, 1)
                self.assertFalse(TemperatureMeasurement.objects.filter(probe=self.probe).exists())
                bucket.delete()

    def test_equal_timestamp_conflict_stays_unknown_until_a_later_reading(self):
        bucket = self.bucket(10, Decimal("25"))
        for value in ("26", "25", "26"):
            self.reading(10, value)
            self.condense()
            bucket.refresh_from_db()
            self.assertIsNone(bucket.last_temperature_c)
        self.reading(11, "0")
        self.condense()
        bucket.refresh_from_db()
        self.assertEqual(bucket.last_measured_at, START + timedelta(minutes=11))
        self.assertEqual(bucket.last_temperature_c, Decimal("0"))

    def test_historical_null_is_not_inferred_from_aggregates(self):
        bucket = self.bucket(10, None)
        before = tuple(getattr(bucket, name) for name in AGGREGATE_FIELDS)
        result = self.condense()
        bucket.refresh_from_db()
        self.assertIsNone(bucket.last_temperature_c)
        self.assertEqual(bucket.last_measured_at, START + timedelta(minutes=10))
        self.assertEqual(tuple(getattr(bucket, name) for name in AGGREGATE_FIELDS), before)
        self.assertEqual(result.readings_condensed, 0)

    def test_daily_aggregates_and_repeat_condensation_are_unchanged(self):
        for minute, value in ((1, "24"), (5, "26.50"), (12, "0"), (20, "21.23")):
            self.reading(minute, value)
        day = START.date()
        daily_fields = AGGREGATE_FIELDS[:-1]
        recompute_daily_temperature(self.zone.pk, day, organization_id=self.organization.pk)
        before = DailyTemperature.objects.values(*daily_fields).get(thermal_zone=self.zone, date=day)
        self.condense()
        recompute_daily_temperature(self.zone.pk, day, organization_id=self.organization.pk)
        after = DailyTemperature.objects.values(*daily_fields).get(thermal_zone=self.zone, date=day)
        self.assertEqual(after, before)
        persisted = list(ProbeTemperatureBucket.objects.filter(probe=self.probe).values())
        result = self.condense()
        self.assertEqual(result.readings_condensed, 0)
        self.assertEqual(list(ProbeTemperatureBucket.objects.filter(probe=self.probe).values()), persisted)


class ProbePersistenceMigrationTests(TransactionTestCase):
    def _assert_disposable_postgres(self):
        self.assertEqual(os.getenv("POLYPBASE_TEST_POSTGRES"), "1")
        self.assertEqual(connection.settings_dict["HOST"], "127.0.0.1")
        self.assertEqual(str(connection.settings_dict["PORT"]), "55439")
        self.assertEqual(connection.settings_dict["NAME"], "test_subculture_qa")
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), inet_server_port()")
            self.assertEqual(cursor.fetchone(), ("test_subculture_qa", 55439))

    def setUp(self):
        super().setUp()
        if connection.vendor == "postgresql":
            self._assert_disposable_postgres()

    def _fixture_teardown(self):
        if connection.vendor != "postgresql":
            return super()._fixture_teardown()
        # Only bypass TRUNCATE guards for cleanup in the opt-in disposable DB.
        self._assert_disposable_postgres()
        with connection.cursor() as cursor:
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

    def test_additive_migration_preserves_history_without_inventing_last_value(self):
        previous = [("measurements", "0015_iminilide_probe_collection")]
        target = [("measurements", "0016_probe_sampling_interval_and_last_temperature")]
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(previous)
            old_apps = executor.loader.project_state(previous).apps
            organization = old_apps.get_model("organizations", "Organization").objects.create(name="Migration lab")
            zone = old_apps.get_model("cultures", "ThermalZone").objects.create(
                organization_id=organization.pk, name="Migration zone", target_temperature_c=25,
            )
            probe = old_apps.get_model("measurements", "Probe").objects.create(
                organization_id=organization.pk, thermal_zone_id=zone.pk, code="Historical probe",
            )
            old_bucket = old_apps.get_model("measurements", "ProbeTemperatureBucket").objects.create(
                probe_id=probe.pk, period_start=START, period_minutes=15, last_measured_at=START,
                min_temperature_c=0, average_temperature_c=0, max_temperature_c=0,
                measurement_count=1, temperature_sum_c=0,
            )
            before = {name: getattr(old_bucket, name) for name in AGGREGATE_FIELDS}
            executor = MigrationExecutor(connection)
            executor.migrate(target)
            new_apps = executor.loader.project_state(target).apps
            new_probe = new_apps.get_model("measurements", "Probe").objects.get(pk=probe.pk)
            new_bucket = new_apps.get_model("measurements", "ProbeTemperatureBucket").objects.get(pk=old_bucket.pk)
            self.assertEqual(new_probe.sampling_interval_seconds, 60)
            self.assertIsNone(new_bucket.last_temperature_c)
            self.assertEqual(new_bucket.last_measured_at, START)
            self.assertEqual({name: getattr(new_bucket, name) for name in AGGREGATE_FIELDS}, before)
        finally:
            MigrationExecutor(connection).migrate(latest)
