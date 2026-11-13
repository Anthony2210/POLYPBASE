"""Dry-run or apply the exceptional five-measurement CCA-EOB 2026 reassignment."""

import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from apps.cultures.historical_2026 import cca_eob_five_reassignment as operation


class Command(BaseCommand):
    help = ("Move exactly five approved measurements from CCA-EOB-2.006 to CCA-EOB-2.004. "
            "Dry-run by default; apply needs an administrator, a reason, the count and the approved fingerprint.")

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--actor", help="Username of an active administrator of organization 1.")
        parser.add_argument("--reason", help="Why this correction is made; cite the researcher confirmation.")
        parser.add_argument("--expected-count", type=int)
        parser.add_argument("--expected-plan-hash")

    def handle(self, *args, **options):
        try:
            if options["apply"]:
                missing = [name for name in ("actor", "reason", "expected_count", "expected_plan_hash")
                           if options[name] in (None, "")]
                if missing:
                    raise CommandError("--apply requires " + ", ".join("--" + n.replace("_", "-") for n in missing) + ".")
                user_model = get_user_model()
                actor = user_model.objects.get(**{user_model.USERNAME_FIELD: options["actor"]})
                plan = operation.apply_plan(
                    actor=actor, reason=options["reason"], expected_count=options["expected_count"],
                    expected_plan_hash=options["expected_plan_hash"])
            else:
                plan = operation.build_plan()
        except DatabaseError:
            raise CommandError("Database state, schema or locks blocked the operation; nothing was committed.") from None
        except (ObjectDoesNotExist, PermissionDenied, ValueError) as error:
            raise CommandError(str(error)) from error
        digest = operation.fingerprint({k: v for k, v in plan.items() if k != "receipt_id"})
        self.stdout.write(json.dumps({"plan_hash": digest, "plan": plan}, indent=2, sort_keys=True, ensure_ascii=False))
        if options["apply"]:
            self.stderr.write(f"Applied {len(plan['move_order'])} reassignment(s) atomically; receipt {plan['receipt_id']}.")
        else:
            self.stderr.write(f"Dry-run only: no writes. Ready={plan['ready']}.")
            if not plan["ready"]:
                raise CommandError("Blocked: " + " | ".join(plan["blockers"]))
