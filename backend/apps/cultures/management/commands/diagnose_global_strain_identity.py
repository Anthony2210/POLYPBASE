"""Administrative-only observation; never performs corrective actions."""

import json

from django.core.management.base import BaseCommand, CommandError

from apps.taxonomy.diagnostics import diagnose_global_strain_identity_state


class Command(BaseCommand):
    help = (
        "Read-only, cross-organization canonicality diagnostic (JSON). "
        "Observation only, not a reservation or a consistent snapshot guarantee. "
        "Run authorized pre-migration scans in an operationally stable context."
    )
    # Keep stdout exclusively JSON; a schema/query failure is an INCOMPLETE scan.
    requires_system_checks = []

    def handle(self, *args, **options):
        report = diagnose_global_strain_identity_state()
        self.stdout.write(json.dumps(report.to_dict(), indent=2, sort_keys=True, ensure_ascii=True))
        if report.scan_status != "COMPLETE":
            raise CommandError("SCAN_INCOMPLETE", returncode=2)
        if not report.constraint_applicable:
            raise CommandError("PHASE_3C_BLOCKED", returncode=1)
