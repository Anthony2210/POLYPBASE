"""Explicit plan, backup or authorized apply; never runs during deployment."""

import json
from types import SimpleNamespace

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from apps.cultures import pca_identity_unification as merge
from apps.cultures import pca_production as production


class Command(BaseCommand):
    help = "Separate PCA production workflow: plan, backup, then explicitly authorized apply."
    requires_system_checks = ()
    requires_migrations_checks = False

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("plan", "backup", "apply"))
        parser.add_argument("--production", action="store_true")
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument("--actor-id", type=int, required=True)
        parser.add_argument("--target", required=True, help="Pinned root-owned production attestation path")
        parser.add_argument("--plan-file", required=True, help="Exclusive output for plan; reviewed input otherwise")
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
            allowed = {
                "plan": set(),
                "backup": {"expected_plan_hash", "backup_evidence", "backup_dir"},
                "apply": {"expected_plan_hash", "backup_evidence", "review_ref", "authorization"},
            }[action]
            all_fields = {"expected_plan_hash", "backup_evidence", "backup_dir", "review_ref", "authorization"}
            if any(options[field] is not None for field in all_fields - allowed):
                raise ValueError("Options from another action are forbidden; planning never applies.")
            if any(not options[field] for field in allowed):
                raise ValueError("Missing required evidence or authorization for this action.")
            target = production.read_target(options["target"])
            production.require_target(target)
            scope = {"organization_id": options["organization_id"],
                     "actor": SimpleNamespace(pk=options["actor_id"])}
            if action == "plan":
                document = production.build_plan(**scope, target=target)
                production.write_artifact(options["plan_file"], document)
                result = {"plan_hash": merge.fingerprint(document), "plan": document}
            else:
                document = production.read_artifact(options["plan_file"])
                if not isinstance(document, dict) or document.get("target") != target:
                    raise ValueError("The reviewed plan target differs from the supplied independent attestation.")
                if action == "backup":
                    evidence = production.backup_plan(
                        **scope, document=document, approved_hash=options["expected_plan_hash"],
                        output_dir=options["backup_dir"],
                    )
                    production.write_artifact(options["backup_evidence"], evidence)
                    result = {"backup_evidence": evidence}
                else:
                    evidence = production.read_artifact(options["backup_evidence"])
                    receipt = production.apply_plan(
                        **scope, document=document, approved_hash=options["expected_plan_hash"],
                        backup_evidence=evidence, review_ref=options["review_ref"],
                        authorization=options["authorization"],
                    )
                    result = {"receipt_id": receipt.pk, "receipt": merge.snapshot(receipt)}
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
            if action == "plan" and not document["plan"]["ready"]:
                raise CommandError("Blocked: the read-only plan contains conflicts; no apply is allowed.")
        except DatabaseError:
            raise CommandError("Database verification or transaction failed; stop and investigate without privilege escalation.") from None
        except (OSError, ObjectDoesNotExist, PermissionDenied, ValueError) as error:
            raise CommandError(str(error)) from error
