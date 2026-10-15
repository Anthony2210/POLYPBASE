"""Plan or apply only the six approved scientific identity corrections."""

import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.management.base import BaseCommand, CommandError

from apps.cultures.historical_2026.species_correction import apply_plan, build_plan, fingerprint


class Command(BaseCommand):
    help = "Read-only plan by default; correct six approved Box.strain relations with an exact reviewed hash."

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument("--cac-strain-id", type=int)
        parser.add_argument("--oce-strain-id", type=int)
        parser.add_argument("--actor", help="Username of an active institution administrator.")
        parser.add_argument("--expected-plan-hash")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        scope = {k: options[k] for k in ("organization_id", "cac_strain_id", "oce_strain_id")}
        plan = build_plan(**scope)
        self.stdout.write(json.dumps(plan, indent=2, sort_keys=True, default=str))
        self.stdout.write(f"Plan SHA-256: {fingerprint(plan)}")
        if not options["apply"]:
            self.stdout.write("Dry-run only: no writes. Review all six entries and target references.")
            return
        if not all(options[k] for k in ("actor", "expected_plan_hash", "cac_strain_id", "oce_strain_id")):
            raise CommandError("--apply requires --actor, --expected-plan-hash and both explicit target Strain PKs.")
        try:
            User = get_user_model()
            actor = User.objects.get(**{User.USERNAME_FIELD: options["actor"]})
            applied = apply_plan(**scope, actor=actor, expected_plan_hash=options["expected_plan_hash"])
        except (ObjectDoesNotExist, PermissionDenied, ValueError) as error:
            raise CommandError(str(error)) from error
        count = sum(e["classification"] == "correction needed" for e in applied["entries"])
        self.stdout.write(f"Corrected {count} Box relation(s); already-correct entries left untouched.")
