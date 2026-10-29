"""Probe replacement regressions with synthetic data and no cloud access."""

import io
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.audit.models import AuditLog
from apps.cultures.models import ThermalZone
from apps.organizations.models import Organization

from .iminilide import Reading
from .models import DailyTemperature, Probe, ProbeTemperatureBucket, TemperatureMeasurement
from .probe_collection import (
    collect_iminilide, collection_start, condense_probe_readings,
    recompute_daily_temperature, store_readings,
)
from .test_probe_collection import FakeClient, NOW, UTC, ProbeFixtureMixin, paris


class ReplacementFixtureMixin(ProbeFixtureMixin):
    def setUp(self):
        super().setUp()
        self.destination = ThermalZone.objects.create(
            organization=self.organization, name="Replacement zone"
        )
        self.right.is_active = False
        self.right.save(update_fields=["is_active"])

    def minute(self, probe=None, measured_at=None, value="0"):
        return TemperatureMeasurement.objects.create(
            probe=probe or self.left,
            measured_at=measured_at or paris(2026, 10, 28, 10, 7),
            temperature_c=Decimal(value), source="iminilide",
        )

    def bucket(self, probe=None, start=None, last=None):
        start = start or paris(2026, 10, 28, 10)
        return ProbeTemperatureBucket.objects.create(
            probe=probe or self.left, period_start=start, period_minutes=15,
            last_measured_at=last or start + timedelta(minutes=7),
            min_temperature_c=Decimal("0"), average_temperature_c=Decimal("0"),
            max_temperature_c=Decimal("0"), measurement_count=1,
            temperature_sum_c=Decimal("0"),
        )

    def link(self, **options):
        defaults = {
            "organization": self.organization.name,
            "zone": self.destination.name,
            "serial": self.left.external_serial,
            "channel": self.left.external_channel,
            "code": "Replacement G",
        }
        defaults.update(options)
        call_command("link_iminilide_probe", stdout=io.StringIO(), **defaults)

    def active_channel(self):
        return Probe.objects.get(
            probe_type=self.left.probe_type,
            external_serial=self.left.external_serial,
            external_channel=self.left.external_channel, is_active=True,
        )

    def manual_successor(self, predecessor, zone, code):
        predecessor.is_active = False
        predecessor.save(update_fields=["is_active"])
        return Probe.objects.create(
            organization=self.organization, thermal_zone=zone, code=code,
            probe_type=predecessor.probe_type,
            external_serial=predecessor.external_serial,
            external_channel=predecessor.external_channel,
        )


class ProbeHistoryZoneTests(ReplacementFixtureMixin, TestCase):
    def test_clean_and_save_refuse_zone_changes_with_minutes_or_buckets(self):
        for history in ("minute", "bucket"):
            getattr(self, history)()
            for method in ("clean", "save"):
                with self.subTest(history=history, method=method):
                    probe = Probe.objects.get(pk=self.left.pk)
                    probe.thermal_zone = self.destination
                    with self.assertRaises(ValidationError):
                        getattr(probe, method)()
                    self.left.refresh_from_db()
                    self.assertEqual(self.left.thermal_zone_id, self.zone.pk)
            TemperatureMeasurement.objects.filter(probe=self.left).delete()
            ProbeTemperatureBucket.objects.filter(probe=self.left).delete()

    def test_save_update_fields_cannot_move_a_probe_with_history(self):
        self.bucket()
        self.left.thermal_zone = self.destination
        with self.assertRaises(ValidationError):
            self.left.save(update_fields=["thermal_zone"])
        self.left.refresh_from_db()
        self.assertEqual(self.left.thermal_zone_id, self.zone.pk)

    def test_code_location_and_active_state_remain_editable_with_history(self):
        self.minute()
        self.bucket()
        self.left.code = "Renamed G"
        self.left.location = "Shelf 2"
        self.left.is_active = False
        self.left.full_clean()
        self.left.save(update_fields=["code", "location", "is_active"])
        self.left.refresh_from_db()
        self.assertEqual(self.left.code, "Renamed G")
        self.assertEqual(self.left.location, "Shelf 2")
        self.assertFalse(self.left.is_active)
        self.left.is_active = True
        self.left.full_clean()
        self.left.save(update_fields=["is_active"])
        self.assertTrue(Probe.objects.get(pk=self.left.pk).is_active)

    def test_probe_without_history_can_change_zone(self):
        self.left.thermal_zone = self.destination
        self.left.full_clean()
        self.left.save()
        self.left.refresh_from_db()
        self.assertEqual(self.left.thermal_zone_id, self.destination.pk)

    def test_inactive_duplicates_are_allowed_but_reactivation_is_rejected_by_db(self):
        successor = self.manual_successor(self.left, self.destination, "Successor")
        inactive = Probe.objects.create(
            organization=self.organization, thermal_zone=self.zone,
            code="Other inactive", probe_type=self.left.probe_type,
            external_serial=self.left.external_serial,
            external_channel=self.left.external_channel, is_active=False,
        )
        for probe in (self.left, inactive):
            with self.subTest(probe=probe.pk):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Probe.objects.filter(pk=probe.pk).update(is_active=True)
                probe.refresh_from_db()
                self.assertFalse(probe.is_active)
        self.assertEqual(self.active_channel().pk, successor.pk)


class LinkReplacementTests(ReplacementFixtureMixin, TestCase):
    def test_normal_link_refuses_to_move_minutes_or_buckets_without_mutation(self):
        for history in ("minute", "bucket"):
            getattr(self, history)()
            with self.subTest(history=history), self.assertRaises(CommandError):
                self.link()
            self.left.refresh_from_db()
            self.assertEqual(self.left.thermal_zone_id, self.zone.pk)
            self.assertTrue(self.left.is_active)
            self.assertEqual(Probe.objects.count(), 2)
            self.assertFalse(AuditLog.objects.filter(object_type="probe").exists())
            TemperatureMeasurement.objects.filter(probe=self.left).delete()
            ProbeTemperatureBucket.objects.filter(probe=self.left).delete()

    def test_normal_link_can_update_descriptive_fields_without_moving_history(self):
        self.minute()
        self.bucket()
        self.link(zone=self.zone.name, code="Renamed G", location="Shelf 2")
        self.left.refresh_from_db()
        self.assertEqual(self.left.code, "Renamed G")
        self.assertEqual(self.left.location, "Shelf 2")
        self.assertEqual(self.left.thermal_zone_id, self.zone.pk)
        self.assertEqual(Probe.objects.count(), 2)
        self.assertEqual(AuditLog.objects.get(object_type="probe").action, AuditLog.Action.UPDATE)

    def test_replace_preserves_history_and_audits_both_probes(self):
        minute = self.minute()
        bucket = self.bucket(start=paris(2026, 10, 27, 10))
        recompute_daily_temperature(self.zone.pk, date(2026, 10, 28), organization_id=self.organization.pk)
        before = list(DailyTemperature.objects.values())
        self.link(replace=True)
        successor = self.active_channel()
        self.assertNotEqual(successor.pk, self.left.pk)
        self.assertEqual(successor.code, "Replacement G")
        self.assertEqual(successor.organization_id, self.organization.pk)
        self.assertEqual(successor.thermal_zone_id, self.destination.pk)
        self.left.refresh_from_db()
        self.assertFalse(self.left.is_active)
        self.assertEqual(self.left.code, "ETUVE 25 G")
        self.assertEqual(self.left.thermal_zone_id, self.zone.pk)
        minute.refresh_from_db()
        bucket.refresh_from_db()
        self.assertEqual(minute.probe_id, self.left.pk)
        self.assertEqual(bucket.probe_id, self.left.pk)
        self.assertEqual(bucket.last_measured_at, paris(2026, 10, 27, 10, 7))
        self.assertEqual(list(DailyTemperature.objects.values()), before)
        self.assertFalse(TemperatureMeasurement.objects.filter(probe=successor).exists())
        self.assertFalse(ProbeTemperatureBucket.objects.filter(probe=successor).exists())
        audits = AuditLog.objects.filter(object_type="probe")
        self.assertEqual(audits.count(), 2)
        old_audit = audits.get(action=AuditLog.Action.UPDATE)
        new_audit = audits.get(action=AuditLog.Action.CREATION)
        self.assertEqual(old_audit.organization_id, self.organization.pk)
        self.assertEqual(new_audit.organization_id, self.organization.pk)
        self.assertEqual(old_audit.metadata["probe_id"], self.left.pk)
        self.assertEqual(old_audit.metadata["replacement_probe_id"], successor.pk)
        self.assertEqual(new_audit.metadata["probe_id"], successor.pk)
        self.assertEqual(new_audit.metadata["replaced_probe_id"], self.left.pk)
        recompute_daily_temperature(self.zone.pk, date(2026, 10, 28), organization_id=self.organization.pk)
        self.assertEqual(DailyTemperature.objects.get().measurement_count, 1)

    def test_each_audit_failure_rolls_back_deactivation_creation_and_audits(self):
        self.minute()
        original = AuditLog.objects.create
        for failure_number in (1, 2):
            calls = []

            def fail_audit(*args, **kwargs):
                calls.append(kwargs)
                if len(calls) == failure_number:
                    raise RuntimeError("Synthetic replacement audit failure")
                return original(*args, **kwargs)

            with self.subTest(failure_number=failure_number):
                with patch(
                    "apps.measurements.probe_linking.AuditLog.objects.create",
                    side_effect=fail_audit,
                ):
                    with self.assertRaisesRegex(RuntimeError, "Synthetic replacement audit failure"):
                        self.link(replace=True)
                self.left.refresh_from_db()
                self.assertTrue(self.left.is_active)
                self.assertEqual(self.left.thermal_zone_id, self.zone.pk)
                self.assertEqual(Probe.objects.count(), 2)
                self.assertEqual(TemperatureMeasurement.objects.get().probe_id, self.left.pk)
                self.assertFalse(AuditLog.objects.filter(object_type="probe").exists())

    def test_replace_requires_a_new_code_unique_even_among_inactive_probes(self):
        self.minute()
        for code in (self.left.code, self.right.code):
            with self.subTest(code=code), self.assertRaises(CommandError):
                self.link(replace=True, code=code)
            self.left.refresh_from_db()
            self.assertTrue(self.left.is_active)
            self.assertEqual(Probe.objects.count(), 2)
            self.assertFalse(AuditLog.objects.filter(object_type="probe").exists())

    def test_normal_link_without_active_probe_creates_instead_of_moving_history(self):
        self.bucket()
        self.left.is_active = False
        self.left.save(update_fields=["is_active"])
        self.link()
        successor = self.active_channel()
        self.assertNotEqual(successor.pk, self.left.pk)
        self.left.refresh_from_db()
        self.assertFalse(self.left.is_active)
        self.assertEqual(self.left.thermal_zone_id, self.zone.pk)
        self.assertEqual(ProbeTemperatureBucket.objects.get().probe_id, self.left.pk)
        self.assertEqual(collection_start(successor, now=NOW), paris(2026, 10, 28, 10, 7))

    def test_channel_owned_by_another_organization_is_never_reused(self):
        self.minute()
        other = Organization.objects.create(name="Other replacement lab")
        other_zone = ThermalZone.objects.create(organization=other, name="Other zone")
        for active in (True, False):
            Probe.objects.filter(pk=self.left.pk).update(is_active=active)
            for replace in (False, True):
                with self.subTest(active=active, replace=replace), self.assertRaises(CommandError):
                    self.link(organization=other.name, zone=other_zone.name, replace=replace)
                self.assertFalse(Probe.objects.filter(organization=other).exists())
                self.left.refresh_from_db()
                self.assertEqual(self.left.is_active, active)
                self.assertEqual(self.left.thermal_zone_id, self.zone.pk)
                self.assertFalse(AuditLog.objects.filter(object_type="probe").exists())

    def test_foreign_destination_zone_is_rejected(self):
        other = Organization.objects.create(name="Other zone owner")
        ThermalZone.objects.create(organization=other, name="Foreign destination")
        with self.assertRaises(CommandError):
            self.link(zone="Foreign destination", replace=True)
        self.assertEqual(self.active_channel().pk, self.left.pk)
        self.assertFalse(AuditLog.objects.filter(object_type="probe").exists())


class ReplacementCollectionTests(ReplacementFixtureMixin, TestCase):
    def test_old_payload_and_last_zero_are_not_reimported_into_the_new_zone(self):
        last = self.minute()
        self.link(replace=True)
        successor = self.active_channel()
        client = FakeClient({12: [
            ["2026/10/28 09:00:00", 15],
            ["2026/10/28 10:07:00", 0],
            ["2026/10/28 10:08:00", 0],
        ]})
        self.assertEqual(collection_start(successor, now=NOW), last.measured_at)
        self.assertEqual(collect_iminilide(client=client, now=NOW).created, 1)
        self.assertEqual(client.calls[0][2], last.measured_at)
        self.assertEqual(list(TemperatureMeasurement.objects.filter(probe=successor).values_list(
            "measured_at", "temperature_c"
        )), [(paris(2026, 10, 28, 10, 8), Decimal("0"))])
        self.assertEqual(collect_iminilide(client=client, now=NOW).created, 0)
        daily = DailyTemperature.objects.get(thermal_zone=self.destination)
        self.assertEqual(daily.measurement_count, 1)
        self.assertEqual(daily.average_temperature_c, Decimal("0"))
        self.assertEqual(TemperatureMeasurement.objects.filter(probe=self.left).count(), 1)

    def test_store_readings_enforces_the_fence_without_trusting_the_client_window(self):
        last = self.minute()
        successor = self.manual_successor(self.left, self.destination, "Manual replacement")
        created, _, _ = store_readings(successor, [
            Reading(last.measured_at - timedelta(days=1), Decimal("40")),
            Reading(last.measured_at, Decimal("0")),
            Reading(last.measured_at + timedelta(minutes=1), Decimal("0")),
        ])
        self.assertEqual(created, 1)
        self.assertEqual(TemperatureMeasurement.objects.filter(probe=successor).get().measured_at,
                         last.measured_at + timedelta(minutes=1))
        self.assertEqual(DailyTemperature.objects.get(thermal_zone=self.destination).measurement_count, 1)

    def test_dst_rewind_does_not_copy_either_previous_occurrence(self):
        last = datetime(2026, 10, 25, 1, 1, tzinfo=UTC)
        self.minute(measured_at=last)
        self.link(replace=True)
        successor = self.active_channel()
        client = FakeClient({12: [
            ["2026/10/25 02:58:00", 10],
            ["2026/10/25 02:59:00", 11],
            ["2026/10/25 02:00:00", 12],
            ["2026/10/25 02:01:00", 0],
            ["2026/10/25 02:02:00", 0],
        ]})
        result = collect_iminilide(client=client, now=NOW)
        self.assertLess(client.calls[0][2].astimezone(UTC), last)
        self.assertEqual(result.created, 1)
        self.assertEqual(TemperatureMeasurement.objects.filter(probe=successor).get().measured_at,
                         datetime(2026, 10, 25, 1, 2, tzinfo=UTC))

    def test_multiple_replacements_use_all_predecessors_not_only_the_last_one(self):
        last = self.minute()
        self.link(replace=True, code="Second G")
        second = self.active_channel()
        self.minute(probe=second, measured_at=last.measured_at - timedelta(minutes=2))
        self.link(replace=True, code="Third G", zone=self.zone.name)
        third = self.active_channel()
        self.assertNotIn(third.pk, (self.left.pk, second.pk))
        self.assertEqual(collection_start(third, now=NOW), last.measured_at)
        result = collect_iminilide(client=FakeClient({12: [
            ["2026/10/28 10:05:00", 10],
            ["2026/10/28 10:07:00", 0],
            ["2026/10/28 10:08:00", 0],
        ]}), now=NOW)
        self.assertEqual(result.created, 1)
        self.assertEqual(TemperatureMeasurement.objects.filter(probe=third).get().measured_at,
                         last.measured_at + timedelta(minutes=1))
        self.assertEqual(TemperatureMeasurement.objects.filter(probe=second).count(), 1)
        self.assertEqual(AuditLog.objects.filter(object_type="probe").count(), 4)

    def test_bucket_fence_is_exact_and_does_not_skip_the_rest_of_its_period(self):
        bucket = self.bucket()
        self.minute(measured_at=paris(2026, 10, 28, 9, 59))
        self.link(replace=True)
        successor = self.active_channel()
        self.assertEqual(collection_start(successor, now=NOW), bucket.last_measured_at)
        result = collect_iminilide(client=FakeClient({12: [
            ["2026/10/28 09:59:00", 10],
            ["2026/10/28 10:00:00", 10],
            ["2026/10/28 10:07:00", 0],
            ["2026/10/28 10:08:00", 0],
        ]}), now=NOW)
        self.assertEqual(result.created, 1)
        self.assertEqual(TemperatureMeasurement.objects.filter(probe=successor).get().measured_at,
                         paris(2026, 10, 28, 10, 8))
        bucket.refresh_from_db()
        self.assertEqual(bucket.probe_id, self.left.pk)
        self.assertEqual(bucket.measurement_count, 1)

    def test_collection_start_compares_own_minutes_with_all_channel_buckets(self):
        bucket = self.bucket()
        successor = self.manual_successor(self.left, self.destination, "Manual G")
        self.minute(probe=successor, measured_at=paris(2026, 10, 28, 9))
        self.assertEqual(collection_start(successor, now=NOW), bucket.last_measured_at)

    def test_unrelated_channels_do_not_advance_the_replacement_fence(self):
        last = self.minute()
        self.minute(probe=self.right, measured_at=paris(2026, 10, 28, 11))
        successor = self.manual_successor(self.left, self.destination, "Manual G")
        self.assertEqual(collection_start(successor, now=NOW), last.measured_at)
        result = collect_iminilide(client=FakeClient({12: [["2026/10/28 10:08:00", 0]]}), now=NOW)
        self.assertEqual(result.created, 1)

    def test_condensation_keeps_exact_last_instant_for_later_manual_replacement(self):
        last = paris(2026, 10, 1, 10, 7)
        self.minute(measured_at=last - timedelta(minutes=7), value="20")
        self.minute(measured_at=last)
        condense_probe_readings(now=NOW)
        bucket = ProbeTemperatureBucket.objects.get(probe=self.left)
        self.assertEqual(bucket.last_measured_at, last)
        self.assertFalse(TemperatureMeasurement.objects.filter(probe=self.left).exists())
        successor = self.manual_successor(self.left, self.destination, "After condensation")
        replay_now = paris(2026, 10, 2, 12)
        self.assertEqual(collection_start(successor, now=replay_now), last)
        result = collect_iminilide(client=FakeClient({12: [
            ["2026/10/01 10:00:00", 20],
            ["2026/10/01 10:07:00", 0],
            ["2026/10/01 10:08:00", 0],
        ]}), now=replay_now)
        self.assertEqual(result.created, 1)
        self.assertEqual(TemperatureMeasurement.objects.filter(probe=successor).get().measured_at,
                         last + timedelta(minutes=1))

    def test_condensation_merge_never_regresses_the_exact_last_instant(self):
        start = paris(2026, 10, 1, 10)
        bucket = self.bucket(start=start, last=start + timedelta(minutes=12))
        self.minute(measured_at=start + timedelta(minutes=2), value="2")
        condense_probe_readings(now=NOW)
        bucket.refresh_from_db()
        self.assertEqual(bucket.last_measured_at, start + timedelta(minutes=12))
        self.assertEqual(bucket.measurement_count, 2)
        self.minute(measured_at=start + timedelta(minutes=14), value="4")
        condense_probe_readings(now=NOW)
        bucket.refresh_from_db()
        self.assertEqual(bucket.last_measured_at, start + timedelta(minutes=14))
        self.assertEqual(bucket.measurement_count, 3)
        self.assertEqual(bucket.temperature_sum_c, Decimal("6"))

    def test_empty_intermediate_replacement_keeps_the_oldest_bucket_fence(self):
        bucket = self.bucket()
        self.link(replace=True, code="Empty second G")
        second = self.active_channel()
        self.link(replace=True, code="Third G", zone=self.zone.name)
        third = self.active_channel()
        self.assertFalse(TemperatureMeasurement.objects.filter(probe=second).exists())
        self.assertFalse(ProbeTemperatureBucket.objects.filter(probe=second).exists())
        self.assertEqual(collection_start(third, now=NOW), bucket.last_measured_at)
        created, _, _ = store_readings(third, [
            Reading(bucket.last_measured_at - timedelta(minutes=1), Decimal("15")),
            Reading(bucket.last_measured_at, Decimal("0")),
            Reading(bucket.last_measured_at + timedelta(minutes=1), Decimal("0")),
        ])
        self.assertEqual(created, 1)
        self.assertEqual(TemperatureMeasurement.objects.filter(probe=third).get().measured_at,
                         bucket.last_measured_at + timedelta(minutes=1))
        bucket.refresh_from_db()
        self.assertEqual(bucket.probe_id, self.left.pk)


class ReplacementServiceTests(ReplacementFixtureMixin, TestCase):
    def test_service_refuses_to_move_historical_probe_directly(self):
        from .probe_linking import link_probe
        self.minute()
        with self.assertRaises(ValidationError):
            link_probe(organization=self.organization, zone=self.destination,
                       serial=self.left.external_serial, channel=self.left.external_channel,
                       code="Direct move")
        self.left.refresh_from_db()
        self.assertEqual(self.left.thermal_zone_id, self.zone.pk)
        self.assertTrue(self.left.is_active)
        self.assertFalse(AuditLog.objects.exists())

    def test_replacement_during_cloud_request_does_not_write_old_probe(self):
        self.minute()
        owner = self

        class ReplacingClient(FakeClient):
            def fetch_readings(self, **kwargs):
                parsed = super().fetch_readings(**kwargs)
                owner.link(replace=True)
                return parsed

        result = collect_iminilide(client=ReplacingClient({12: [
            ["2026/10/28 10:07:00", 0], ["2026/10/28 10:08:00", 10],
        ]}), now=NOW)
        self.assertEqual(result.created, 0)
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(TemperatureMeasurement.objects.filter(probe=self.left).count(), 1)
        self.assertFalse(TemperatureMeasurement.objects.filter(probe=self.active_channel()).exists())
        second = collect_iminilide(client=FakeClient({12: [
            ["2026/10/28 10:07:00", 0], ["2026/10/28 10:08:00", 10],
        ]}), now=NOW)
        self.assertEqual(second.created, 1)
        self.assertEqual(TemperatureMeasurement.objects.filter(probe=self.active_channel()).get().temperature_c, 10)
