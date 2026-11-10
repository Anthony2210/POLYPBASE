"""Dry-run by default; this command never runs the ten-Box CLA operation."""

import json
from types import SimpleNamespace

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from apps.cultures.historical_2026 import cla_box_1902 as correction
from apps.measurements.box_1902_sql import ContractError


class Command(BaseCommand):
    help = "Plan only Box 1902; v2 apply requires its dedicated SQL executor, independent approval and reviewed hash."
    requires_system_checks = []

    def add_arguments(self, parser):
        for name in ("organization-id", "box-id", "source-strain-id", "source-species-id", "target-strain-id"):
            parser.add_argument("--" + name, type=int, required=True)
        parser.add_argument("--actor-id", type=int)
        parser.add_argument("--expected-plan-hash")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        scope = {key: options[key] for key in ("organization_id", "box_id", "source_strain_id",
                                               "source_species_id", "target_strain_id")}
        try:
            if options["apply"]:
                if not options["actor_id"] or options["actor_id"] <= 0 or not options["expected_plan_hash"]:
                    raise ValueError("--apply requires a positive --actor-id and --expected-plan-hash.")
                plan = correction.apply_plan(**scope, actor=SimpleNamespace(pk=options["actor_id"]),
                                             expected_plan_hash=options["expected_plan_hash"])
            else:
                plan = correction.build_plan(**scope)
        except DatabaseError:
            raise CommandError("Database state, schema or locks blocked the operation; no partial correction was committed.") from None
        except (ObjectDoesNotExist, PermissionDenied, ValueError, ContractError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(correction.summary(plan), indent=2, sort_keys=True))
        self.stdout.write("Applied atomically; only Box 1902 was eligible." if options["apply"] else "Dry-run only: no writes.")
