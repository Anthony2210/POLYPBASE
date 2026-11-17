"""One-off, evidence-matching org-1 historical reconfirmation maintenance."""

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from apps.measurements.historical_temperature_reconfirm import ATTESTATION, run


class Command(BaseCommand):
    help = "Dry-run by default. Exceptional org-1 reconfirmation; apply requires schema-owner maintenance access."

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--actor-id", type=int)
        parser.add_argument("--reason", default="")
        parser.add_argument("--owner-attestation", default="", help=ATTESTATION)
        parser.add_argument("--expected-count", type=int)
        parser.add_argument("--expected-fingerprint")

    def handle(self, *args, **options):
        try:
            report = run(
                organization_id=options["organization_id"], apply=options["apply"],
                actor_id=options["actor_id"], reason=options["reason"],
                attestation=options["owner_attestation"],
                expected_count=options["expected_count"],
                expected_fingerprint=options["expected_fingerprint"],
            )
        except (ValueError, DatabaseError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, sort_keys=True, indent=2))
        if report["blockers"]:
            raise CommandError("Blocking anomalies; no rows changed. Entire batch must remain pending.")
