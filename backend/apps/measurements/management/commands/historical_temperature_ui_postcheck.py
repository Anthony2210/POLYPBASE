"""Stage D: read-only, exact post-import verification against the post-migration preflight."""

from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.measurements import historical_temperature_ui as ui


class Command(BaseCommand):
    help = "Stage D (POLYPBASE_UI_TEMP_STAGE=D): read-only post-import verification."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--raw-directory", required=True, type=Path)
        parser.add_argument("--snapshot", required=True, type=Path)
        parser.add_argument("--preflight", required=True, type=Path)
        parser.add_argument("--output-dir", required=True, type=Path)

    def handle(self, *args, **options):
        try:
            ui.require_stage("D")
            before = ui.load_preflight(options["preflight"])
            with ui.read_only():
                result = ui.postcheck(options["raw_directory"], options["snapshot"].read_bytes(), before)
            name = f"postcheck-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
            path = ui.write_private_json(options["output_dir"], name, result)
        except (ui.UiOperationError, ValueError, OSError) as error:
            raise CommandError(f"STOP: {error}") from error
        self.stdout.write(f"OK stage D: 40,291 readings, 40,292 evidences, receipt {result['receipt_id']}. Record: {path}")
