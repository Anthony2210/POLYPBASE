"""Deactivate the stakeholder-approved HS boxes. Dry-run unless --apply."""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.cultures.historical_2026 import deactivation
from apps.organizations.models import Organization


class Command(BaseCommand):
    help = (
        "Report (default) or apply the bounded deactivation of the reviewed HS list "
        "for one explicit organization, using the audited deactivation service."
    )

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--expected-plan-hash")
        parser.add_argument("--actor", help="Username of an active administrator.")

    def handle(self, *args, **options):
        try:
            organization = Organization.objects.get(pk=options["organization_id"])
        except Organization.DoesNotExist as error:
            raise CommandError("Organization not found.") from error

        if not options["apply"]:
            entries = deactivation.build_deactivation_plan(organization)
            for status, codes in sorted(deactivation.summarize(entries).items()):
                self.stdout.write(f"{status} ({len(codes)}): {', '.join(codes)}")
            for entry in entries:
                if entry["corrected"] or entry["detail"]:
                    self.stdout.write(
                        f"  {entry['listed_code']} -> {entry['resolved_code']}: {entry['detail']}"
                    )
            self.stdout.write(self.style.WARNING("Dry-run only: nothing was written."))
            self.stdout.write(
                f"Apply with --apply --expected-plan-hash {deactivation.plan_hash(organization, entries)} "
                "--actor <administrator>."
            )
            return

        if not (options["expected_plan_hash"] and options["actor"]):
            raise CommandError("--expected-plan-hash and --actor are required with --apply.")
        User = get_user_model()
        try:
            actor = User.objects.get(**{User.USERNAME_FIELD: options["actor"]})
        except User.DoesNotExist as error:
            raise CommandError("Actor not found.") from error
        try:
            entries, done = deactivation.apply_deactivations(
                organization, actor=actor, expected_plan_hash=options["expected_plan_hash"]
            )
        except (ValueError, ValidationError, PermissionDenied) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(f"Deactivated {len(done)} box(es): {', '.join(done)}"))
