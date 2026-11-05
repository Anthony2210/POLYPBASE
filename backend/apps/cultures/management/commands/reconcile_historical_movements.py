"""Local-only preview/accept entry point; no production execution path."""

import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.accounts.permissions import user_can_administer_organization
from apps.cultures.historical_movements import accept_historical_movements, plan_historical_movements
from apps.organizations.models import Organization


class Command(BaseCommand):
    help = "Preview or explicitly accept week-only reconstructions in isolated test settings only."

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument("--box-id", type=int, action="append", required=True)
        parser.add_argument("--actor-id", type=int, required=True)
        parser.add_argument("--accept", action="store_true")
        parser.add_argument("--candidate-id", action="append", default=[])
        parser.add_argument("--expected-fingerprint")
        parser.add_argument("--notes", default="")

    def handle(self, *args, **options):
        # Fail before the first query. A future operational workflow needs its
        # own reviewed environment attestation, backup and authorization policy.
        if (settings.SETTINGS_MODULE != "config.test_settings" or connection.vendor != "sqlite"
                or not connection.is_in_memory_db()):
            raise CommandError("Historical movement execution is restricted to isolated in-memory test settings.")
        try:
            actor = get_user_model().objects.get(pk=options["actor_id"], is_active=True)
            organization = Organization.objects.get(pk=options["organization_id"])
            if not user_can_administer_organization(actor, organization):
                raise PermissionDenied("An active institution administrator is required.")
            arguments = {"organization_id": organization.pk, "box_ids": options["box_id"]}
            if options["accept"]:
                result = {"accepted_ids": accept_historical_movements(
                    **arguments, actor=actor, candidate_ids=options["candidate_id"],
                    expected_fingerprint=options["expected_fingerprint"], notes=options["notes"],
                )}
            else:
                result = plan_historical_movements(**arguments)
            self.stdout.write(json.dumps(result, sort_keys=True))
        except (ObjectDoesNotExist, PermissionDenied, ValidationError) as exc:
            raise CommandError(str(exc)) from exc
