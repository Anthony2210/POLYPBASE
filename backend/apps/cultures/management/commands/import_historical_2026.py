"""Import the reviewed 2026 historical manifest. Dry-run unless --apply."""

import json

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError

from apps.cultures.historical_2026 import importer
from apps.cultures.historical_2026.manifest import (
    DEFAULT_MANIFEST_PATH,
    ManifestError,
    load_manifest,
)
from apps.organizations.models import Organization


class Command(BaseCommand):
    help = (
        "Classify (default) or apply the reviewed 2026 historical manifest for one "
        "explicit organization. Nothing is written without --apply."
    )

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH))
        parser.add_argument(
            "--expected-fingerprint",
            help="Manifest fingerprint reviewed beforehand. Required with --apply.",
        )
        parser.add_argument("--apply", action="store_true", help="Write the reviewed plan.")
        parser.add_argument(
            "--expected-plan-hash",
            help="Plan hash printed by the reviewed dry-run. Required with --apply.",
        )
        parser.add_argument(
            "--actor",
            help="Username of an active administrator of the organization. Required with --apply.",
        )
        parser.add_argument("--json", action="store_true", help="Print the full report as JSON.")

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        if apply_changes and not (
            options["expected_fingerprint"] and options["expected_plan_hash"] and options["actor"]
        ):
            raise CommandError(
                "--expected-fingerprint, --expected-plan-hash and --actor are required with --apply."
            )
        try:
            manifest = load_manifest(
                options["manifest"], expected_fingerprint=options["expected_fingerprint"]
            )
        except ManifestError as error:
            raise CommandError(str(error)) from error
        try:
            organization = Organization.objects.get(pk=options["organization_id"])
        except Organization.DoesNotExist as error:
            raise CommandError("Organization not found.") from error
        if organization.name != manifest["target_organization"]["name"]:
            raise CommandError(
                f"Manifest targets {manifest['target_organization']['name']!r}, "
                f"not {organization.name!r}."
            )

        if not apply_changes:
            plan = importer.build_plan(manifest, organization)
            self._print(plan.report(), options["json"])
            self.stdout.write(self.style.WARNING("Dry-run only: nothing was written."))
            if not plan.blockers:
                self.stdout.write(
                    "Apply with --apply "
                    f"--expected-fingerprint {manifest['fingerprint']} "
                    f"--expected-plan-hash {plan.plan_hash} --actor <administrator>."
                )
            return

        User = get_user_model()
        try:
            actor = User.objects.get(**{User.USERNAME_FIELD: options["actor"]})
        except User.DoesNotExist as error:
            raise CommandError("Actor not found.") from error
        try:
            plan, receipt = importer.apply_import(
                manifest,
                organization,
                actor=actor,
                expected_plan_hash=options["expected_plan_hash"],
            )
        except importer.HistoricalImportBlocked as error:
            self._print(error.plan.report(), options["json"])
            raise CommandError(str(error)) from error
        except (importer.HistoricalImportError, PermissionDenied) as error:
            raise CommandError(str(error)) from error
        self._print(plan.report(), options["json"])
        if receipt is None:
            self.stdout.write(self.style.SUCCESS("Nothing to apply: every row is already satisfied."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Applied. Receipt audit entry #{receipt.pk}."))

    def _print(self, report, as_json):
        if as_json:
            self.stdout.write(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
            return
        self.stdout.write(f"manifest: {report['manifest_fingerprint']}")
        self.stdout.write(f"organization: {report['organization']}")
        self.stdout.write(f"plan hash: {report['plan_hash']}")
        for key, value in report["classification_counts"].items():
            self.stdout.write(f"  {key}: {value}")
        self.stdout.write(f"box states: {report['box_states']}")
        self.stdout.write(f"new measurements: {report['new_measurements']}")
        self.stdout.write(f"boxes to materialize ({len(report['boxes_to_materialize'])}): "
                          f"{', '.join(report['boxes_to_materialize'])}")
        self.stdout.write(f"identity corrections: {report['identity_corrections']}")
        self.stdout.write(f"explicit corrections: {report['explicit_corrections']}")
        self.stdout.write(f"excluded source: {report['excluded_source']}")
        blockers = report["blockers"]
        self.stdout.write(f"blockers: {len(blockers)}")
        for blocker in blockers[:50]:
            self.stdout.write(f"  - {blocker}")
