"""Explicit plan, backup, authorized apply or postcheck; never runs during deployment."""

import json
from pathlib import Path
from types import SimpleNamespace

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from apps.cultures import pca_production as target_tools
from apps.measurements import historical_temperature_production as production

# Options each action accepts besides the common ones; anything else is refused.
ACTION_OPTIONS = {
    "plan": set(),
    "backup": {"expected_plan_hash", "backup_evidence", "backup_dir"},
    "apply": {"expected_plan_hash", "backup_evidence", "review_ref", "authorization"},
    "postcheck": {"expected_plan_hash"},
}
ALL_OPTIONS = {"expected_plan_hash", "backup_evidence", "backup_dir", "review_ref", "authorization"}


class Command(BaseCommand):
    help = "Production partial historical temperature import: plan, backup, apply, postcheck."
    requires_system_checks = ()
    requires_migrations_checks = False

    def add_arguments(self, parser):
        parser.add_argument("action", choices=tuple(ACTION_OPTIONS))
        parser.add_argument("--production", action="store_true")
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument("--actor-id", type=int, required=True)
        parser.add_argument("--target", required=True, help="Pinned root-owned production attestation path")
        parser.add_argument("--plan-file", required=True, help="Exclusive output for plan; reviewed input otherwise")
        parser.add_argument("--raw-directory", type=Path,
                            help="Directory with the seven fingerprinted workbooks (plan, backup, apply)")
        parser.add_argument("--expected-plan-hash")
        parser.add_argument("--backup-evidence", help="Exclusive output for backup; verified input for apply")
        parser.add_argument("--backup-dir")
        parser.add_argument("--review-ref")
        parser.add_argument("--authorization")

    def handle(self, *args, **options):
        try:
            if not options["production"]:
                raise ValueError("This separate workflow requires explicit --production.")
            action = options["action"]
            allowed = ACTION_OPTIONS[action]
            if any(options[field] is not None for field in ALL_OPTIONS - allowed):
                raise ValueError("Options from another action are forbidden; planning never applies.")
            if any(not options[field] for field in allowed):
                raise ValueError("Missing required evidence or authorization for this action.")
            if action != "postcheck" and options["raw_directory"] is None:
                raise ValueError("--raw-directory is required for this action.")
            if action == "postcheck" and options["raw_directory"] is not None:
                raise ValueError("Postcheck reads the receipt, not the workbooks.")
            target = target_tools.read_target(options["target"])
            target_tools.require_target(target)
            scope = {"organization_id": options["organization_id"],
                     "actor": SimpleNamespace(pk=options["actor_id"])}
            if action == "plan":
                document = production.build_plan(**scope, target=target, raw_directory=options["raw_directory"])
                target_tools.write_artifact(options["plan_file"], document)
                plan = document["plan"]
                result = {"plan_hash": production.plan_hash(document), "ready": document["ready"],
                          "blockers": document["blockers"], "baseline": document["baseline"],
                          "summary": plan["summary"] if plan else None,
                          "review": document["review"], "plan_file": options["plan_file"]}
            else:
                document = target_tools.read_artifact(options["plan_file"])
                if not isinstance(document, dict) or document.get("target") != target:
                    raise ValueError("The reviewed plan target differs from the supplied independent attestation.")
                if action == "backup":
                    evidence = production.backup_plan(
                        **scope, document=document, approved_hash=options["expected_plan_hash"],
                        raw_directory=options["raw_directory"], output_dir=options["backup_dir"])
                    target_tools.write_artifact(options["backup_evidence"], evidence)
                    result = {"backup_evidence": evidence}
                elif action == "apply":
                    evidence = target_tools.read_artifact(options["backup_evidence"])
                    receipt, replayed = production.apply_plan(
                        **scope, document=document, approved_hash=options["expected_plan_hash"],
                        backup_evidence=evidence, review_ref=options["review_ref"],
                        authorization=options["authorization"], raw_directory=options["raw_directory"])
                    result = {"receipt_id": receipt.pk, "replayed": replayed, "actor_id": receipt.actor_id,
                              "plan_fingerprint": receipt.plan_fingerprint,
                              "readings": len(receipt.results),
                              "evidences": document["plan"]["summary"]["accepted_evidences"]}
                else:
                    result = {"postcheck": production.postcheck(
                        **scope, document=document, approved_hash=options["expected_plan_hash"])}
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
            if action == "plan" and not document["ready"]:
                raise CommandError("Blocked: the plan cannot be applied; inspect blockers and review.")
        except DatabaseError:
            raise CommandError("Database verification or transaction failed; stop and investigate without privilege escalation.") from None
        except (OSError, ObjectDoesNotExist, PermissionDenied, ValueError) as error:
            raise CommandError(str(error)) from error
