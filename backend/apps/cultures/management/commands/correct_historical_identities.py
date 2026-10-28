"""Read-only identity plans and separately authorized atomic application."""

import json
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from apps.cultures.historical_2026 import identity_execution as execution
from apps.cultures.historical_2026.identity_reconciliation import (
    apply_plan, build_plan, fingerprint, require_local_database, summary,
)


class Command(BaseCommand):
    help = "Read-only exact CLA/SME plan; PostgreSQL apply requires separate root authorization and backup."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument("--family", choices=("cla", "sme"), required=True)
        parser.add_argument("--target-strain-id", type=int, required=True)
        actors = parser.add_mutually_exclusive_group()
        actors.add_argument("--actor-id", type=int, help="Active institution administrator PK; required for PostgreSQL apply.")
        actors.add_argument("--actor", help="Compatibility username lookup for isolated SQLite tests only.")
        parser.add_argument("--expected-plan-hash")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        scope = {key: options[key] for key in ("organization_id", "family", "target_strain_id")}
        try:
            require_local_database()
            if options["apply"]:
                if not (options["actor_id"] or options["actor"]) or not options["expected_plan_hash"]:
                    raise ValueError("--apply requires --actor-id and --expected-plan-hash.")
                if options["actor_id"]:
                    if options["actor_id"] <= 0:
                        raise ValueError("An explicit positive actor PK is required.")
                    actor = SimpleNamespace(pk=options["actor_id"])
                else:
                    if execution.require_database() != "sqlite":
                        raise ValueError("PostgreSQL application requires --actor-id, never an unverified account lookup.")
                    with execution.session():
                        User = get_user_model()
                        actor = User.objects.get(**{User.USERNAME_FIELD: options["actor"]})
                plan = apply_plan(**scope, actor=actor, expected_plan_hash=options["expected_plan_hash"])
            else:
                plan = build_plan(**scope)
        except DatabaseError:
            raise CommandError("Database state, schema or locks prevented the operation; no partial correction was committed.") from None
        except (ObjectDoesNotExist, PermissionDenied, ValueError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(summary(plan), indent=2, sort_keys=True))
        self.stdout.write(f"Plan SHA-256: {fingerprint(plan)}")
        self.stdout.write("Applied atomically; no physical Box was created or merged." if options["apply"] else "Dry-run only: no writes.")
