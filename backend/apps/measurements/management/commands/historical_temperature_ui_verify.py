"""Stage A: read-only verification of the local UI database. Writes nothing to it."""

from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.measurements import historical_temperature_ui as ui


class Command(BaseCommand):
    help = "Stage A (POLYPBASE_UI_TEMP_STAGE=A): read-only preflight of the explicit local target."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--raw-directory", required=True, type=Path)
        parser.add_argument("--snapshot", required=True, type=Path)
        parser.add_argument("--output-dir", required=True, type=Path,
                            help="Existing private directory outside the repository.")
        parser.add_argument("--expect-migrated", action="store_true",
                            help="Post-migration check (historical and canonical migrations applied); otherwise the exact integrated set must be pending.")
        parser.add_argument("--migrator-role", action="store_true",
                            help="Run as the owner/administrator for the pre-migration check; skips runtime-role checks.")

    def handle(self, *args, **options):
        migrated = options["expect_migrated"]
        if migrated and options["migrator_role"]:
            raise CommandError("The post-migration preflight must use the restricted runtime role.")
        try:
            ui.require_stage("A")
            with ui.read_only():
                record = ui.verify(options["raw_directory"], options["snapshot"].read_bytes(),
                                   migrated=migrated, runtime=not options["migrator_role"])
            label = "post" if migrated else "pre"
            name = f"preflight-{label}-migration-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
            path = ui.write_private_json(options["output_dir"], name, record)
        except (ui.UiOperationError, ValueError, OSError) as error:
            raise CommandError(f"STOP: {error}") from error
        self.stdout.write(f"OK stage A ({label}-migration). Record: {path}")
        self.stdout.write(f"Other sessions on the target: {record['sessions']['other_sessions']}; "
                          f"administrators: {record['administrators']}")
