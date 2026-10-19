"""Preview the exact PCA merge, or apply a reviewed plan on independent QA only."""

import json
from types import SimpleNamespace

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.management.base import BaseCommand, CommandError

from apps.cultures import pca_identity_unification as unification


class Command(BaseCommand):
    help = "Local-only PCA identity unification; read-only plan by default, no historical writes."
    requires_system_checks = []
    requires_migrations_checks = False

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument("--actor-id", type=int, required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--expected-plan-hash")
        parser.add_argument("--backup-ref")
        parser.add_argument("--review-ref")

    def handle(self, *args, **options):
        try:
            # No actor lookup or ORM access before the pre-connect target guard.
            unification.require_local_database(apply=options["apply"])
            scope = {"organization_id": options["organization_id"],
                     "actor": SimpleNamespace(pk=options["actor_id"])}
            if options["apply"]:
                receipt = unification.apply_plan(
                    **scope, expected_plan_hash=options["expected_plan_hash"],
                    backup_ref=options["backup_ref"], review_ref=options["review_ref"],
                )
                result = {"receipt_id": receipt.pk, "receipt": unification.snapshot(receipt)}
            else:
                if any(options[name] for name in ("expected_plan_hash", "backup_ref", "review_ref")):
                    raise ValueError("Apply-only references require --apply; dry-run never writes.")
                plan = unification.build_plan(**scope)
                result = {"plan_hash": unification.fingerprint(plan), "plan": plan}
            self.stdout.write(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
            if not options["apply"] and not plan["ready"]:
                raise CommandError("Blocked: inspect the plan; no partial merge is permitted.")
        except (ObjectDoesNotExist, PermissionDenied, ValueError) as error:
            raise CommandError(str(error)) from error
