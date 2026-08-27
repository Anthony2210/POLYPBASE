import hashlib
from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.audit.models import AuditLog
from apps.cultures.models import Box, BoxInventoryInitialization
from apps.organizations.models import Organization


def _selection_hash(rows):
    payload = "\n".join(f"{box_id}|{code}|{status}" for box_id, code, status in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Command(BaseCommand):
    help = (
        "Initialize the historical box inventory of one explicit organization. "
        "The default mode is a dry run; --apply requires its count and selection hash."
    )

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the initialization. Without this flag, only prints a dry run.",
        )
        parser.add_argument(
            "--expected-count",
            type=int,
            help="Required with --apply and must match the current organization box count.",
        )
        parser.add_argument(
            "--expected-hash",
            help="Required with --apply and must match the selection hash printed by the dry run.",
        )

    def handle(self, *args, **options):
        organization_id = options["organization_id"]
        apply_changes = options["apply"]
        expected_count = options["expected_count"]
        expected_hash = options["expected_hash"]

        if apply_changes and (expected_count is None or not expected_hash):
            raise CommandError("--expected-count and --expected-hash are required with --apply.")
        organization = self._organization(organization_id)

        if apply_changes:
            self._apply(
                organization_id=organization.id,
                expected_count=expected_count,
                expected_hash=expected_hash,
            )
            return

        if BoxInventoryInitialization.objects.filter(organization=organization).exists():
            marker = BoxInventoryInitialization.objects.get(organization=organization)
            self.stdout.write(
                self.style.WARNING(
                    f"Inventory already initialized on {marker.initialized_at.isoformat()}; no change planned."
                )
            )
            return

        rows = self._rows(organization.id)
        selection_hash = self._write_report(
            organization=organization,
            rows=rows,
            applying=False,
        )
        self.stdout.write(
            self.style.WARNING(
                "Simulation only. Apply with "
                f"--organization-id {organization.id} --apply "
                f"--expected-count {len(rows)} --expected-hash {selection_hash}."
            )
        )

    def _apply(self, *, organization_id, expected_count, expected_hash):
        with transaction.atomic():
            try:
                organization = (
                    Organization.objects.select_for_update()
                    .only("id", "name")
                    .get(pk=organization_id)
                )
            except Organization.DoesNotExist as exc:
                raise CommandError(f"Organization {organization_id} does not exist.") from exc

            marker = BoxInventoryInitialization.objects.filter(
                organization=organization
            ).first()
            if marker is not None:
                self.stdout.write(
                    self.style.WARNING(
                        f"Inventory already initialized on {marker.initialized_at.isoformat()}; no changes applied."
                    )
                )
                return

            rows = list(
                Box.objects.select_for_update()
                .filter(organization_id=organization.id)
                .order_by("global_code")
                .values_list("id", "global_code", "status")
            )
            if len(rows) != expected_count:
                raise CommandError(
                    f"Expected {expected_count} boxes but found {len(rows)}. Run the dry run again."
                )
            if not rows:
                raise CommandError("No box was found for this organization.")

            status_counts = dict(sorted(Counter(status for _, _, status in rows).items()))
            selection_hash = _selection_hash(rows)
            if selection_hash != expected_hash:
                raise CommandError(
                    "The box selection changed since the dry run. Run the dry run again."
                )
            Box.objects.filter(id__in=[box_id for box_id, _, _ in rows]).update(
                status=Box.Status.PENDING_REVIEW
            )
            BoxInventoryInitialization.objects.create(
                organization=organization,
                box_count=len(rows),
                previous_status_counts=status_counts,
                selection_hash=selection_hash,
            )
            AuditLog.objects.create(
                organization=organization,
                action=AuditLog.Action.IMPORT,
                object_type="box_inventory_initialization",
                object_id=str(organization.id),
                description=f"Historical box inventory initialized for {organization.name}",
                metadata={
                    "box_count": len(rows),
                    "previous_status_counts": status_counts,
                    "selection_hash": selection_hash,
                    "target_status": Box.Status.PENDING_REVIEW,
                },
            )

            self._write_report(organization=organization, rows=rows, applying=True)
            self.stdout.write(
                self.style.SUCCESS(
                    f"{len(rows)} box(es) initialized as {Box.Status.PENDING_REVIEW}."
                )
            )

    def _organization(self, organization_id):
        try:
            return Organization.objects.only("id", "name").get(pk=organization_id)
        except Organization.DoesNotExist as exc:
            raise CommandError(f"Organization {organization_id} does not exist.") from exc

    def _rows(self, organization_id):
        return list(
            Box.objects.filter(organization_id=organization_id)
            .order_by("global_code")
            .values_list("id", "global_code", "status")
        )

    def _write_report(self, *, organization, rows, applying):
        counts = Counter(status for _, _, status in rows)
        action = "Applying initialization" if applying else "Initialization dry run"
        self.stdout.write(f"{action}: {organization.name} (id={organization.id})")
        self.stdout.write(f"Boxes found: {len(rows)}")
        self.stdout.write("Current status counts:")
        for status in sorted(counts):
            self.stdout.write(f"  - {status}: {counts[status]}")
        selection_hash = _selection_hash(rows)
        self.stdout.write(f"Selection hash: {selection_hash}")
        self.stdout.write("Boxes:")
        for _, code, status in rows:
            self.stdout.write(f"  - {code}: {status} -> {Box.Status.PENDING_REVIEW}")
        return selection_hash
