"""Stage C: the one-time sealed partial import, through the reviewed atomic service."""

import json
from datetime import datetime, timezone
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.measurements import historical_temperature_ui as ui
from apps.measurements.historical_temperature_apply import APPLY_CONFIRMATION, apply_approved_partial
from apps.measurements.historical_temperature_partial import APPROVED_PLAN_FINGERPRINT
from apps.measurements.models import HistoricalTemperatureImportReceipt, HistoricalTemperatureReading


class Command(BaseCommand):
    help = ("Stage C (POLYPBASE_UI_TEMP_STAGE=C, POLYPBASE_UI_TEMP_ENABLE_APPLY=1, restricted role): "
            "import exactly the approved 40,291 readings in one transaction (about 13 minutes).")
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--raw-directory", required=True, type=Path)
        parser.add_argument("--snapshot", required=True, type=Path)
        parser.add_argument("--preflight", required=True, type=Path, help="Fresh post-migration stage A record.")
        parser.add_argument("--output-dir", required=True, type=Path)
        parser.add_argument("--backup-record", required=True, type=Path,
                            help="Operator record of the verified, restorable pg_dump of the target.")
        parser.add_argument("--actor", required=True, help="Active administrator of institution 1.")
        parser.add_argument("--expected-plan-hash", required=True)
        parser.add_argument("--acknowledged-other-sessions", required=True, type=int,
                            help="Exact number of other sessions on the target you accept for this window.")
        parser.add_argument("--confirmation", required=True)

    def handle(self, *args, **options):
        try:
            if options["confirmation"] != APPLY_CONFIRMATION or options["expected_plan_hash"] != APPROVED_PLAN_FINGERPRINT:
                raise ui.UiOperationError("confirmation_or_plan_hash_mismatch")
            ui.require_stage("C", switch="HISTORICAL_TEMPERATURE_LOCAL_UI_APPLY_ENABLED")
            ui.check_backup_record(options["backup_record"])  # before any connection
            before = ui.load_preflight(options["preflight"])
            snapshot_bytes = options["snapshot"].read_bytes()
            with ui.read_only():
                now = ui.verify(options["raw_directory"], snapshot_bytes, migrated=True, runtime=True)
            ui.compare_preflight(before, now)
            if now["sessions"]["other_sessions"] != options["acknowledged_other_sessions"]:
                raise ui.UiOperationError(f"other_sessions_differ:{now['sessions']['other_sessions']}")
            actor = get_user_model().objects.filter(username=options["actor"], is_active=True).first()
            if actor is None or options["actor"] not in now["administrators"]:
                raise ui.UiOperationError("actor_is_not_an_active_institution_administrator")
        except (ui.UiOperationError, ValueError, OSError) as error:
            raise CommandError(f"STOP before any write: {error}") from error
        self.stdout.write("Preconditions verified. Starting the single transaction; do not interrupt.")
        try:
            receipt, replayed = apply_approved_partial(
                raw_directory=options["raw_directory"], snapshot_bytes=snapshot_bytes,
                expected_plan_fingerprint=APPROVED_PLAN_FINGERPRINT, organization_id=ui.ORGANIZATION_ID,
                actor=actor, confirmation=options["confirmation"])
        except BaseException as error:
            # The service owns one atomic block: PostgreSQL rolls everything back, receipt and audit included.
            state = self._state_after_failure()
            raise CommandError(f"IMPORT FAILED AND ROLLED BACK ({type(error).__name__}: {error}); {state}") from error
        name = f"import-result-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        path = ui.write_private_json(options["output_dir"], name, {
            "receipt_id": receipt.pk, "replayed": replayed, "plan_fingerprint": APPROVED_PLAN_FINGERPRINT})
        self.stdout.write(f"Committed. Receipt {receipt.pk} (replayed={replayed}). Result: {path}. Now run stage D.")

    def _state_after_failure(self):
        try:
            readings = HistoricalTemperatureReading.objects.count()
            receipts = HistoricalTemperatureImportReceipt.objects.count()
        except Exception as error:  # report, never hide
            return f"state check failed ({type(error).__name__}); run stage A before any retry"
        clean = not readings and not receipts
        return json.dumps({"readings": readings, "receipts": receipts}) + (
            "; clean, rerun stage A before any retry" if clean else "; NOT CLEAN, STOP")
