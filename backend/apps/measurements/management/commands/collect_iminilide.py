from django.core.management.base import BaseCommand, CommandError

from apps.measurements.iminilide import IminilideConfigurationError
from apps.measurements.probe_collection import collect_iminilide, condense_probe_readings


class Command(BaseCommand):
    help = (
        "Collect iMinilide probe readings from the cloud, refresh the affected daily "
        "zone temperatures, then condense per-minute readings past their retention."
    )

    def add_arguments(self, parser):
        parser.add_argument("--serial", help="Only collect the probes of this logger serial number.")
        parser.add_argument(
            "--skip-condense",
            action="store_true",
            help="Do not condense old per-minute readings during this run.",
        )

    def handle(self, *args, **options):
        try:
            result = collect_iminilide(serial=options.get("serial"))
        except IminilideConfigurationError as error:
            raise CommandError(str(error)) from None

        for probe in result.probes:
            if probe.error:
                self.stderr.write(f"{probe.probe_code}: FAILED - {probe.error}")
                continue
            details = [f"{probe.created} new", f"{probe.skipped_existing} already stored"]
            if probe.missing_values:
                details.append(f"{probe.missing_values} without value")
            if probe.out_of_range_values:
                details.append(f"{probe.out_of_range_values} outside the logger range")
            if probe.invalid_rows:
                details.append(f"{probe.invalid_rows} unreadable rows")
            self.stdout.write(f"{probe.probe_code}: " + ", ".join(details))
        self.stdout.write(
            f"Collected {result.created} readings from {len(result.probes)} probes; "
            f"{result.days_recomputed} zone days recomputed, "
            f"{result.days_kept_manual} manual zone days kept."
        )

        if not options["skip_condense"]:
            condensed = condense_probe_readings()
            if condensed.readings_condensed:
                self.stdout.write(
                    f"Condensed {condensed.readings_condensed} old readings into "
                    f"{condensed.buckets_created} new buckets."
                )

        if result.failed:
            raise CommandError(f"{len(result.failed)} probe(s) could not be collected.")
