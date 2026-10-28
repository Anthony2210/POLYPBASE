"""Plan or explicitly apply additive CLA/SME taxonomy reference preparation."""

import json
from types import SimpleNamespace

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from apps.cultures.historical_2026.identity_references import apply_plan, build_plan, safe_summary


class Command(BaseCommand):
    help = "Prepare exact CLA/SME references, read-only by default; never change Boxes or existing taxonomy."
    requires_system_checks = []
    requires_migrations_checks = False

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument("--family", choices=("cla", "sme"), required=True)
        parser.add_argument("--source-strain-id", type=int, required=True)
        parser.add_argument("--target-strain-id", type=int)
        parser.add_argument("--actor-id", type=int, help="Active institutional administrator PK, required for apply.")
        parser.add_argument("--expected-plan-hash")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        scope = {key: options[key] for key in ("organization_id", "family", "source_strain_id", "target_strain_id")}
        try:
            if options["apply"]:
                if not options["actor_id"] or options["actor_id"] <= 0 or not options["expected_plan_hash"]:
                    raise ValueError("--apply requires --actor-id and --expected-plan-hash.")
                # The service reloads and authorizes this PK inside its owned
                # session. No business query may precede that boundary.
                plan = apply_plan(**scope, actor=SimpleNamespace(pk=options["actor_id"]),
                                  expected_plan_hash=options["expected_plan_hash"])
            else:
                plan = build_plan(**scope)
        except (ValueError, PermissionDenied) as error:
            raise CommandError(str(error)) from error
        except ObjectDoesNotExist as error:
            raise CommandError("A required authorized reference is unavailable.") from error
        except DatabaseError as error:
            raise CommandError("Reference preparation failed and was rolled back; inspect protected server diagnostics.") from error
        self.stdout.write(json.dumps(safe_summary(plan), indent=2, sort_keys=True))
        self.stdout.write("Applied reference preparation only." if options["apply"] else "Dry-run only: no writes.")
