from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError

from apps.cultures.models import ThermalZone
from apps.measurements.probe_linking import link_probe
from apps.organizations.models import Organization


class Command(BaseCommand):
    help = (
        "Create or update the probe collecting one channel of an iMinilide logger "
        "for a thermal zone. Use --list to show zones and existing links."
    )

    def add_arguments(self, parser):
        parser.add_argument("--organization", help="Organization name (exact, case-insensitive).")
        parser.add_argument("--list", action="store_true", help="List thermal zones and linked probes.")
        parser.add_argument("--zone", help="Thermal zone name (exact, case-insensitive).")
        parser.add_argument("--serial", help="Logger serial number, e.g. 1162040825.")
        parser.add_argument("--channel", type=int, help="Logger channel number (1-99).")
        parser.add_argument("--code", help="Probe code shown in Polypbase, unique per organization.")
        parser.add_argument("--location", default="", help="Optional free-text location.")
        parser.add_argument("--replace", action="store_true", help="Deactivate the old probe and create a new one, preserving history. Requires a new code.")

    def _organization(self, name):
        if not name:
            raise CommandError("--organization is required.")
        try:
            return Organization.objects.get(name__iexact=name)
        except Organization.DoesNotExist:
            raise CommandError(f"Unknown organization: {name}") from None

    def handle(self, *args, **options):
        organization = self._organization(options.get("organization"))
        if options["list"]:
            self._list(organization)
            return

        missing = [name for name in ("zone", "serial", "channel", "code") if not options.get(name)]
        if missing:
            raise CommandError("Missing options: " + ", ".join(f"--{name}" for name in missing))
        serial = options["serial"].strip()
        channel = options["channel"]
        if not serial.isdigit():
            raise CommandError("--serial must contain digits only.")
        if not 1 <= channel <= 99:
            raise CommandError("--channel must be between 1 and 99.")
        try:
            zone = ThermalZone.objects.get(organization=organization, name__iexact=options["zone"])
        except ThermalZone.DoesNotExist:
            raise CommandError(f"Unknown thermal zone in {organization.name}: {options['zone']}") from None

        try:
            probe, created = link_probe(
                organization=organization, zone=zone, serial=serial, channel=channel,
                code=options["code"], location=options["location"], replace=options["replace"],
            )
        except ValidationError as error:
            raise CommandError("; ".join(error.messages)) from None
        except IntegrityError:
            raise CommandError("The channel or probe code changed concurrently. Retry with a unique code.") from None
        verb = "Created" if created else "Updated"
        self.stdout.write(f"{verb} probe {probe.code}: logger {serial} channel {channel} -> {zone.name}")

    def _list(self, organization):
        zones = ThermalZone.objects.filter(organization=organization).order_by("name")
        for zone in zones:
            self.stdout.write(f"{zone.name} (id {zone.id}, target {zone.target_temperature_c})")
            for probe in zone.probes.filter(organization=organization).order_by("external_serial", "external_channel", "code"):
                link = (
                    f"logger {probe.external_serial} channel {probe.external_channel}"
                    if probe.is_collected
                    else "not collected"
                )
                state = "active" if probe.is_active else "inactive"
                self.stdout.write(f"  - {probe.code} [{probe.probe_type}, {state}] {link}")
