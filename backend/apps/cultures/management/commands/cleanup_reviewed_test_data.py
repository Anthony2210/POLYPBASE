"""Remove the reviewed test data. Dry-run unless --apply. Never part of the import."""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError

from apps.cultures.historical_2026 import reviewed_cleanup
from apps.organizations.models import Organization


class Command(BaseCommand):
    help = (
        "Report (default) or apply the guarded removal of the exact reviewed test "
        "measurements and test Box island of one explicit organization."
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
            report = reviewed_cleanup.build_cleanup_plan(organization).report()
            self.stdout.write(f"plan hash: {report['plan_hash']}")
            self.stdout.write(f"present ({len(report['present'])}): {', '.join(report['present'])}")
            self.stdout.write(f"already absent ({len(report['absent'])}): {', '.join(report['absent'])}")
            self.stdout.write(f"dependency closure of the test Box: {report['dependency_closure']}")
            self.stdout.write(f"blockers: {len(report['blockers'])}")
            for blocker in report["blockers"]:
                self.stdout.write(f"  - {blocker}")
            self.stdout.write(self.style.WARNING("Dry-run only: nothing was written."))
            if not report["blockers"]:
                self.stdout.write(
                    f"Apply with --apply --expected-plan-hash {report['plan_hash']} --actor <administrator>, "
                    "then repeat the import dry-run: the EXPECTED_TEST_DATA_COLLISION rows become CREATE."
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
            plan, receipt = reviewed_cleanup.apply_cleanup(
                organization, actor=actor, expected_plan_hash=options["expected_plan_hash"]
            )
        except reviewed_cleanup.CleanupBlocked as error:
            for blocker in error.plan.blockers:
                self.stdout.write(f"  - {blocker}")
            raise CommandError(str(error)) from error
        except (ValueError, PermissionDenied) as error:
            raise CommandError(str(error)) from error
        if receipt is None:
            self.stdout.write(self.style.SUCCESS("Nothing to remove: the reviewed test data is already gone."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Removed. Receipt audit entry #{receipt.pk}."))
