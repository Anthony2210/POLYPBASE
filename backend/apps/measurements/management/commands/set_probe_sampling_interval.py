from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.audit.models import AuditLog
from apps.cultures.models import ThermalZone
from apps.measurements.models import Probe
from apps.organizations.models import Organization


class Command(BaseCommand):
    help = (
        "Set the freshness sampling interval for one exact probe, not its cloud configuration. "
        "Requires an active Django superuser actor. The default mode is a dry run."
    )

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument("--zone-id", type=int, required=True)
        parser.add_argument("--probe-id", type=int, required=True)
        parser.add_argument("--probe-code", required=True, help="Exact, case-sensitive probe code guard.")
        parser.add_argument("--sampling-interval-seconds", type=int, choices=(60, 600, 900), required=True)
        parser.add_argument("--actor-user-id", type=int, required=True)
        parser.add_argument("--reason", required=True)
        parser.add_argument("--apply", action="store_true", help="Apply the change instead of reporting it.")

    def handle(self, *args, **options):
        interval = options["sampling_interval_seconds"]
        reason = options["reason"].strip()
        if interval not in (60, 600, 900):
            raise CommandError("--sampling-interval-seconds must be 60, 600 or 900.")
        if not reason:
            raise CommandError("--reason must not be blank.")
        if not options["probe_code"].strip():
            raise CommandError("--probe-code must not be blank.")

        with transaction.atomic():
            try:
                organization = Organization.objects.select_for_update().get(pk=options["organization_id"])
            except Organization.DoesNotExist as error:
                raise CommandError("The exact organization does not exist.") from error
            try:
                actor = get_user_model().objects.get(pk=options["actor_user_id"])
            except get_user_model().DoesNotExist as error:
                raise CommandError("The actor user does not exist.") from error
            if not actor.is_active or not actor.is_superuser:
                raise CommandError("An active Django superuser actor is required.")
            try:
                zone = ThermalZone.objects.select_for_update().get(
                    pk=options["zone_id"], organization=organization,
                )
            except ThermalZone.DoesNotExist as error:
                raise CommandError("The exact zone does not belong to this organization.") from error
            try:
                probe = Probe.objects.select_for_update().get(
                    pk=options["probe_id"], organization=organization, thermal_zone=zone,
                )
            except Probe.DoesNotExist as error:
                raise CommandError("The exact probe does not belong to this organization and zone.") from error
            if probe.code != options["probe_code"]:
                raise CommandError("--probe-code does not match the exact probe code.")

            before = {"sampling_interval_seconds": probe.sampling_interval_seconds}
            after = {"sampling_interval_seconds": interval}
            label = f"probe {probe.pk} ({probe.code}), organization {organization.pk}, zone {zone.pk}"
            if before == after:
                self.stdout.write(f"No change required for {label}.")
                return
            if not options["apply"]:
                self.stdout.write(
                    f"DRY RUN: {label}: sampling_interval_seconds "
                    f"{probe.sampling_interval_seconds} -> {interval}."
                )
                return

            probe.sampling_interval_seconds = interval
            probe.save(update_fields=["sampling_interval_seconds"])
            AuditLog.objects.create(
                organization=organization, user=actor, action=AuditLog.Action.UPDATE,
                object_type="probe", object_id=probe.code,
                description="Probe sampling interval updated",
                metadata={
                    "probe_id": probe.pk, "thermal_zone_id": zone.pk,
                    "before": before, "after": after,
                    "source": "management_command", "command": "set_probe_sampling_interval",
                    "reason": reason,
                },
            )
            self.stdout.write(self.style.SUCCESS(
                f"Updated {label}: sampling_interval_seconds "
                f"{before['sampling_interval_seconds']} -> {interval}."
            ))
