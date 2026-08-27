from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from apps.cultures.models import Box
from apps.organizations.models import Organization


class Command(BaseCommand):
    help = (
        "Report box status counts for one explicit organization. "
        "This command can be run before the lifecycle migration."
    )

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, required=True)

    def handle(self, *args, **options):
        organization_id = options["organization_id"]
        try:
            organization = Organization.objects.only("id", "name").get(pk=organization_id)
        except Organization.DoesNotExist as exc:
            raise CommandError(f"Organization {organization_id} does not exist.") from exc

        rows = list(
            Box.objects.filter(organization_id=organization.id)
            .order_by("global_code")
            .values_list("global_code", "status")
        )
        counts = Counter(status for _, status in rows)

        self.stdout.write(f"Organization: {organization.name} (id={organization.id})")
        self.stdout.write(f"Boxes found: {len(rows)}")
        self.stdout.write("Status counts:")
        for status in sorted(counts):
            self.stdout.write(f"  - {status}: {counts[status]}")
        for legacy_status in ("archived", "lost", "stopped"):
            if legacy_status not in counts:
                self.stdout.write(f"  - {legacy_status}: 0")

        self.stdout.write("Boxes:")
        for code, status in rows:
            self.stdout.write(f"  - {code}: {status}")
