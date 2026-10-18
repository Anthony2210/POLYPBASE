"""Preview or apply the bounded local CCA-EOB correction after independent review."""

import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.management.base import BaseCommand, CommandError

from apps.cultures.historical_2026 import cca_eob_correction as correction
from apps.cultures.historical_2026.source import SourceError
from apps.cultures.historical_2026.manifest import ManifestError


class Command(BaseCommand):
    help = "Local-only CCA-EOB V2 attribution correction; read-only JSON plan by default."

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument("--workbook", required=True, help="Read-only path to the fingerprinted V2 workbook.")
        parser.add_argument("--actor", help="Username of an active administrator in the explicit organization.")
        parser.add_argument("--expected-plan-hash")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        scope = {"organization_id": options["organization_id"], "workbook_path": options["workbook"]}
        try:
            correction.require_local_database()
            if options["apply"]:
                if not options["actor"] or not options["expected_plan_hash"]:
                    raise CommandError("--apply requires --actor and --expected-plan-hash from the reviewed dry-run.")
                correction._verify_connection()
                user_model = get_user_model()
                actor = user_model.objects.get(**{user_model.USERNAME_FIELD: options["actor"]})
                plan = correction.apply_plan(
                    **scope, actor=actor, expected_plan_hash=options["expected_plan_hash"],
                )
            else:
                plan = correction.build_plan(**scope)
        except (ObjectDoesNotExist, PermissionDenied, SourceError, ManifestError, ValueError, OSError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps({"plan_hash": correction.fingerprint(plan), "plan": plan},
                                    indent=2, sort_keys=True, ensure_ascii=False))
        if options["apply"]:
            self.stderr.write(
                f"Applied {len(plan['move_order'])} reassignment(s); no scientific values, status, "
                "location or movement history changed. Environmental context follows the target box "
                "under the approved product decision."
            )
        else:
            self.stderr.write(f"Dry-run only: no writes. Ready={plan['ready']}; counts={plan['counts']}.")
            if not plan["ready"]:
                raise CommandError("Blocked: inspect the JSON matrix; no partial application is permitted.")
