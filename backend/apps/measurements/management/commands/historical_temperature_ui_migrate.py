"""Stage B: integrated schema changes after a fresh read-only check."""

from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.measurements import historical_temperature_ui as ui


class Command(BaseCommand):
    help = ("Stage B (POLYPBASE_UI_TEMP_STAGE=B, POLYPBASE_UI_TEMP_ALLOW_MIGRATE=1): apply exactly "
            "the reviewed historical and canonical migrations to the explicitly configured local target.")
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--raw-directory", required=True, type=Path)
        parser.add_argument("--snapshot", required=True, type=Path)
        parser.add_argument("--backup-record", required=True, type=Path,
                            help="Operator record of the verified, restorable pg_dump of the target.")
        parser.add_argument("--confirmation", required=True)

    def handle(self, *args, **options):
        try:
            if options["confirmation"] != ui.MIGRATE_CONFIRMATION:
                raise ui.UiOperationError("separate_migration_confirmation_required")
            ui.require_stage("B", switch="HISTORICAL_TEMPERATURE_UI_MIGRATE_ENABLED")
            ui.check_backup_record(options["backup_record"])  # before any connection
            with ui.read_only():  # drift, identity and the exact pending set, before any DDL
                ui.verify(options["raw_directory"], options["snapshot"].read_bytes(), migrated=False, runtime=False)
            self.stdout.write("Pre-migration state verified; applying historical and canonical migrations.")
            # Django applies each migration atomically on PostgreSQL. A failure after 0011 or 0012
            # leaves only empty tables and guards: stop, do not retry blindly, report the state.
            call_command("migrate", "measurements", "0014_measurement_canonical_evidence", interactive=False, verbosity=1)
            with ui.read_only():
                ui.check_migrations(migrated=True)
                ui.check_guards()
                counts = ui.table_counts(migrated=True)
            if any(counts[table] for table in ui.HISTORICAL_TABLES):
                raise ui.UiOperationError("historical_tables_not_empty_after_migration")
        except (ui.UiOperationError, ValueError, OSError) as error:
            raise CommandError(f"STOP: {error}") from error
        self.stdout.write("OK stage B: integrated migrations applied, guards enabled, historical tables empty.")
