"""Explicit one-off production plan/apply, separate from the strict local command."""

import json
from types import SimpleNamespace

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from config import pca_protected_artifacts as artifacts
from apps.cultures.historical_2026 import cca_eob_correction as correction
from apps.cultures.historical_2026 import cca_eob_production as production
from apps.cultures.historical_2026.source import SourceError
from apps.cultures.historical_2026.manifest import ManifestError


class Command(BaseCommand):
    help = "CCA-EOB organization 1 only: production read-only plan or separately authorized application."
    requires_system_checks = ()
    requires_migrations_checks = False

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("plan", "apply"))
        parser.add_argument("--production", action="store_true")
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument("--actor-id", type=int, required=True)
        parser.add_argument("--target", required=True)
        parser.add_argument("--workbook", required=True)
        parser.add_argument("--plan-file", required=True)
        parser.add_argument("--expected-plan-hash")
        parser.add_argument("--authorization")

    def handle(self, *args, **options):
        try:
            if not options["production"]:
                raise ValueError("The separate production workflow requires explicit --production.")
            applying = options["action"] == "apply"
            if applying:
                if not options["expected_plan_hash"] or not options["authorization"]:
                    raise ValueError("Apply requires a reviewed hash and deliberate production authorization.")
            elif options["expected_plan_hash"] is not None or options["authorization"] is not None:
                raise ValueError("Application options are forbidden during read-only planning.")
            actor = SimpleNamespace(pk=options["actor_id"])
            production._scope(options["organization_id"], actor)
            target = production.read_target(options["target"])
            production.require_target(target)
            scope = {"organization_id": options["organization_id"], "actor": actor,
                     "workbook_path": options["workbook"]}
            if applying:
                document = artifacts.read_json(options["plan_file"])
                if not isinstance(document, dict) or document.get("target") != target:
                    raise ValueError("Reviewed plan differs from the independent target attestation.")
                plan = production.apply_plan(
                    **scope, document=document, approved_hash=options["expected_plan_hash"],
                    authorization=options["authorization"],
                )
                result = {"applied": len(plan["move_order"]), "production_plan_hash": options["expected_plan_hash"]}
            else:
                document = production.build_plan(**scope, target=target)
                artifacts.write_json(options["plan_file"], document)
                result = {"plan_hash": correction.fingerprint(document),
                          "ready": document["plan"]["correction"]["ready"],
                          "counts": document["plan"]["correction"]["counts"]}
            self.stdout.write(json.dumps(result, sort_keys=True))
            if not applying and not result["ready"]:
                raise CommandError("Blocked: inspect the complete private plan; no partial correction is permitted.")
        except DatabaseError:
            raise CommandError("PostgreSQL verification or transaction failed; stop without retry or privilege escalation.") from None
        except (OSError, ObjectDoesNotExist, PermissionDenied, SourceError, ManifestError, ValueError) as error:
            raise CommandError(str(error)) from error
