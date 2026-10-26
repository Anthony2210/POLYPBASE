"""Read-only diagnosis of persisted state blocking historical scientific corrections."""

import json

from django.core.management.base import BaseCommand, CommandError

from apps.cultures.historical_2026 import anomaly_diagnostic as diagnostic


class Command(BaseCommand):
    help = (
        "Read-only report (JSON on stdout, summary on stderr) explaining whether "
        "correct_urgent_species is READY or BLOCKED and how the HS deactivation plan "
        "classifies each listed label, and which approved identity corrections are persisted "
        "or blocked pending a decision. Never writes, never selects a target Strain."
    )

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument("--mode", choices=diagnostic.MODES, default="all")
        parser.add_argument("--cac-strain-id", type=int, help="Explicit, reviewed target Strain PK.")
        parser.add_argument("--oce-strain-id", type=int, help="Explicit, reviewed target Strain PK.")
        parser.add_argument("--summary-only", action="store_true", help="Print only the terminal summary.")

    def handle(self, *args, **options):
        try:
            report = diagnostic.diagnose(
                options["organization_id"], mode=options["mode"],
                cac_strain_id=options["cac_strain_id"], oce_strain_id=options["oce_strain_id"],
            )
        except LookupError as error:
            raise CommandError(str(error)) from error
        summary = diagnostic.render_summary(report)
        if options["summary_only"]:
            self.stdout.write(summary)
            return
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True, default=str))
        self.stderr.write(summary)
