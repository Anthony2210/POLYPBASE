"""Read-only weekly movement diagnostic; never performs corrective actions."""

import json

from django.core.management.base import BaseCommand, CommandError

from apps.cultures.movement_week_diagnostics import diagnose_box_movement_weeks
from apps.organizations.models import Organization


class Command(BaseCommand):
    help = (
        "Read-only diagnostic of ordinary-movement weekly uniqueness (Europe/Paris ISO "
        "weeks), unclassified movement provenance, movement/period links, open periods, "
        "current zone pointers and foreign zone relations for one explicit organization. "
        "Nothing is written, classified, linked or repaired."
    )

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument("--format", choices=("text", "json"), default="text")

    def handle(self, *args, **options):
        organization_id = options["organization_id"]
        try:
            organization = Organization.objects.only("id", "name").get(pk=organization_id)
        except Organization.DoesNotExist as exc:
            raise CommandError(f"Organization {organization_id} does not exist.") from exc

        report = diagnose_box_movement_weeks(organization=organization)
        if options["format"] == "json":
            self.stdout.write(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
            return
        self.stdout.write(
            f"Organization #{report['organization']['id']} \"{report['organization']['name']}\": "
            f"{report['box_count']} boxes, {report['movement_count']} movements "
            f"(read-only; database read-only transaction: {report['database_read_only_transaction']})"
        )
        self.stdout.write(
            "Movement kinds: "
            + (", ".join(f"{kind}={count}" for kind, count in report["movement_kind_counts"].items()) or "none")
        )
        for section, count in report["summary"].items():
            self.stdout.write(f"{section}: {count}")
            for item in report[section]:
                self.stdout.write("  " + json.dumps(item, sort_keys=True, ensure_ascii=True))
