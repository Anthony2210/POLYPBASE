"""Plan or apply the reviewed six-Box colour-variant Species correction."""

import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError

from apps.cultures.historical_2026 import variant_species_correction as correction


def _pk_list(value):
    if value is None or isinstance(value, list):
        return value  # call_command may pass already-parsed values
    try:
        return [int(item) for item in value.split(",") if item.strip()]
    except ValueError as error:
        raise CommandError("Strain PK lists must be comma-separated integers.") from error


class Command(BaseCommand):
    help = ("Read-only plan by default. Step 'references' only adds exact missing Species and "
            "technical Strains; step 'boxes' only moves the six reviewed Box.strain relations.")

    def add_arguments(self, parser):
        parser.add_argument("step", choices=("references", "boxes"))
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument("--source-strain-ids", type=_pk_list, required=True,
                            help="Comma-separated reviewed current shared Strain PKs.")
        parser.add_argument("--target-strain-ids", type=_pk_list,
                            help="Comma-separated reviewed target Strain PKs (step 'boxes').")
        parser.add_argument("--actor", help="Username of an active institution administrator.")
        parser.add_argument("--expected-plan-hash")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        scope = {"source_strain_ids": _pk_list(options["source_strain_ids"])}
        if options["step"] == "boxes":
            if not options["target_strain_ids"]:
                raise CommandError("Step 'boxes' requires explicit --target-strain-ids.")
            scope["target_strain_ids"] = _pk_list(options["target_strain_ids"])
            build, apply = correction.build_box_plan, correction.apply_box_plan
        else:
            build, apply = correction.build_reference_plan, correction.apply_reference_plan
        try:
            plan = build(options["organization_id"], **scope)
        except ValueError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(plan, indent=2, sort_keys=True, default=str))
        self.stdout.write(f"Plan SHA-256: {correction.fingerprint(plan)}")
        self.stdout.write(f"Classification: {plan['classification']}")
        if not options["apply"]:
            self.stdout.write("Dry-run only: no writes. Review every entry before applying.")
            return
        if not (options["actor"] and options["expected_plan_hash"]):
            raise CommandError("--apply requires --actor and --expected-plan-hash.")
        try:
            User = get_user_model()
            actor = User.objects.get(**{User.USERNAME_FIELD: options["actor"]})
            applied = apply(options["organization_id"], **scope, actor=actor,
                            expected_plan_hash=options["expected_plan_hash"])
        except (ObjectDoesNotExist, PermissionDenied, ValueError, IntegrityError) as error:
            raise CommandError(str(error)) from error
        if options["step"] == "boxes":
            count = sum(e["classification"] == correction.NEEDED for e in applied["entries"])
            self.stdout.write(f"Corrected {count} Box relation(s); already-correct entries left untouched.")
        else:
            self.stdout.write("Prepared target Strain PKs: "
                              + json.dumps(applied["prepared_target_strain_ids"], sort_keys=True))
