"""Isolated operational sampling-interval command regressions."""

from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import ThermalZone
from apps.organizations.models import Organization

from .models import DailyTemperature, Probe, ProbeTemperatureBucket, TemperatureMeasurement


class SetProbeSamplingIntervalTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Sampling laboratory")
        self.other_organization = Organization.objects.create(name="Other laboratory")
        self.zone = ThermalZone.objects.create(organization=self.organization, name="Zone")
        self.other_zone = ThermalZone.objects.create(organization=self.organization, name="Other zone")
        self.foreign_zone = ThermalZone.objects.create(organization=self.other_organization, name="Zone")
        self.probe = Probe.objects.create(organization=self.organization, thermal_zone=self.zone, code="Probe")
        self.neighbor = Probe.objects.create(organization=self.organization, thermal_zone=self.zone, code="Neighbor")
        self.foreign_probe = Probe.objects.create(
            organization=self.other_organization, thermal_zone=self.foreign_zone, code="Probe",
        )
        self.actor = get_user_model().objects.create_user(
                    username="operator", email="operator@example.com", is_superuser=True,
                )
        self.options = {
            "organization_id": self.organization.pk, "zone_id": self.zone.pk,
            "probe_id": self.probe.pk, "probe_code": self.probe.code,
            "actor_user_id": self.actor.pk, "sampling_interval_seconds": 600,
            "reason": "Confirmed ten-minute sensor cadence",
        }

    def run_command(self, **overrides):
        output = StringIO()
        call_command("set_probe_sampling_interval", stdout=output, **(self.options | overrides))
        return output.getvalue()

    def assert_unchanged(self):
        self.probe.refresh_from_db()
        self.assertEqual(self.probe.sampling_interval_seconds, 60)
        self.assertFalse(AuditLog.objects.exists())

    def test_dry_run_is_default_and_validates_without_writing(self):
        output = self.run_command()
        self.assertIn("DRY RUN", output)
        self.assertIn("60 -> 600", output)
        self.assert_unchanged()

    def test_all_supported_cadences_and_real_before_after_audit(self):
        previous = 60
        for interval in (600, 900, 60):
            with self.subTest(interval=interval):
                self.run_command(apply=True, sampling_interval_seconds=interval)
                self.probe.refresh_from_db()
                self.assertEqual(self.probe.sampling_interval_seconds, interval)
                audit = AuditLog.objects.first()
                self.assertEqual(audit.organization_id, self.organization.pk)
                self.assertEqual(audit.user_id, self.actor.pk)
                self.assertEqual(audit.action, AuditLog.Action.UPDATE)
                self.assertEqual(audit.object_type, "probe")
                self.assertEqual(audit.object_id, self.probe.code)
                self.assertEqual(audit.metadata, {
                    "probe_id": self.probe.pk, "thermal_zone_id": self.zone.pk,
                    "before": {"sampling_interval_seconds": previous},
                    "after": {"sampling_interval_seconds": interval},
                    "source": "management_command", "command": "set_probe_sampling_interval",
                    "reason": self.options["reason"],
                })
                previous = interval
        self.assertEqual(AuditLog.objects.count(), 3)

    def test_noop_and_repeated_apply_do_not_audit(self):
        for apply in (False, True):
            self.assertIn("No change required", self.run_command(apply=apply, sampling_interval_seconds=60))
        self.assert_unchanged()
        self.run_command(apply=True)
        self.assertIn("No change required", self.run_command(apply=True))
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_invalid_intervals_are_rejected_even_for_call_command_keywords(self):
        for interval in (0, -60, 1, 300, 601, 901):
            with self.subTest(interval=interval), self.assertRaises(CommandError):
                self.run_command(apply=True, sampling_interval_seconds=interval)
            self.assert_unchanged()

    def test_required_and_malformed_cli_arguments(self):
        argv = []
        for key, value in self.options.items():
            argv.extend(["--" + key.replace("_", "-"), str(value)])
        for index in range(0, len(argv), 2):
            with self.subTest(missing=argv[index]), self.assertRaises(CommandError):
                call_command("set_probe_sampling_interval", *argv[:index], *argv[index + 2:], stdout=StringIO())
        for flag, value in (("--sampling-interval-seconds", "bad"), ("--sampling-interval-seconds", "300"),
                            ("--probe-id", "bad"), ("--actor-user-id", "bad")):
            invalid = argv.copy()
            invalid[invalid.index(flag) + 1] = value
            with self.subTest(flag=flag, value=value), self.assertRaises(CommandError):
                call_command("set_probe_sampling_interval", *invalid, stdout=StringIO())
        self.assert_unchanged()

    def test_blank_reason_or_code_rejected(self):
        for options in ({"reason": " \t"}, {"probe_code": " "}):
            with self.subTest(options=options), self.assertRaises(CommandError):
                self.run_command(apply=True, **options)
        self.assert_unchanged()

    def test_missing_or_inactive_actor_rejected_in_both_modes(self):
        for apply in (False, True):
            with self.subTest(apply=apply), self.assertRaises(CommandError):
                self.run_command(apply=apply, actor_user_id=-1)
        self.actor.is_active = False
        self.actor.save(update_fields=["is_active"])
        for apply in (False, True):
            with self.subTest(apply=apply), self.assertRaisesMessage(CommandError, "active Django superuser"):
                self.run_command(apply=apply)
        self.assert_unchanged()

    def test_product_roles_staff_and_foreign_membership_are_not_operator_authority(self):
        user = get_user_model().objects.create_user(
                    username="product-user", email="product-user@example.com", is_staff=True,
                )
        for organization in (self.organization, self.other_organization):
            for role in OrganizationMembership.Role.values:
                membership = OrganizationMembership.objects.create(
                    organization=organization, user=user, role=role,
                    is_responsable=role == OrganizationMembership.Role.ADMIN,
                )
                for apply in (False, True):
                    with self.subTest(organization=organization.pk, role=role, apply=apply):
                        with self.assertRaisesMessage(CommandError, "active Django superuser"):
                            self.run_command(apply=apply, actor_user_id=user.pk)
                membership.delete()
        self.assert_unchanged()

    def test_superuser_uses_established_break_glass_without_membership(self):
        self.assertFalse(OrganizationMembership.objects.filter(user=self.actor).exists())
        self.run_command(apply=True)
        self.probe.refresh_from_db()
        self.assertEqual(self.probe.sampling_interval_seconds, 600)

    def test_exact_organization_zone_probe_and_case_sensitive_code_guards(self):
        mismatches = (
            {"organization_id": -1}, {"organization_id": self.other_organization.pk},
            {"zone_id": -1}, {"zone_id": self.foreign_zone.pk}, {"zone_id": self.other_zone.pk},
            {"probe_id": -1}, {"probe_id": self.foreign_probe.pk},
            {"probe_id": self.neighbor.pk}, {"probe_code": "probe"}, {"probe_code": "Probe "},
        )
        for options in mismatches:
            for apply in (False, True):
                with self.subTest(options=options, apply=apply), self.assertRaises(CommandError):
                    self.run_command(apply=apply, **options)
                self.assert_unchanged()

    def test_inconsistent_probe_organization_is_not_accepted(self):
        Probe.objects.filter(pk=self.foreign_probe.pk).update(thermal_zone=self.zone)
        with self.assertRaises(CommandError):
            self.run_command(apply=True, probe_id=self.foreign_probe.pk)
        self.assert_unchanged()

    def test_audit_failure_rolls_back_probe_write(self):
        with patch("apps.measurements.management.commands.set_probe_sampling_interval.AuditLog.objects.create",
                   side_effect=RuntimeError("Audit unavailable")):
            with self.assertRaisesMessage(RuntimeError, "Audit unavailable"):
                self.run_command(apply=True)
        self.assert_unchanged()

    def test_only_interval_changes_and_all_history_and_unrelated_rows_remain_untouched(self):
        now = timezone.now()
        TemperatureMeasurement.objects.create(probe=self.probe, measured_at=now, temperature_c=0)
        ProbeTemperatureBucket.objects.create(
            probe=self.probe, period_start=now, period_minutes=15, last_measured_at=now,
            last_temperature_c=0, min_temperature_c=0, average_temperature_c=0,
            max_temperature_c=0, measurement_count=1, temperature_sum_c=0,
        )
        DailyTemperature.objects.create(
            thermal_zone=self.zone, date=now.date(), average_temperature_c=0,
            min_temperature_c=0, max_temperature_c=0, measurement_count=1,
        )
        models = (Organization, ThermalZone, Probe, TemperatureMeasurement, ProbeTemperatureBucket, DailyTemperature)
        before = {model: list(model.objects.order_by("pk").values()) for model in models}
        self.run_command(apply=True)
        for row in before[Probe]:
            if row["id"] == self.probe.pk:
                row["sampling_interval_seconds"] = 600
        for model in models:
            with self.subTest(model=model.__name__):
                self.assertEqual(list(model.objects.order_by("pk").values()), before[model])
