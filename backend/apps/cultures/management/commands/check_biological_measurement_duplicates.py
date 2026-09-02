from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from apps.measurements.models import BiologicalMeasurement


class Command(BaseCommand):
    help = (
        "Read-only check for duplicate biological measurements sharing a box and date."
    )

    def handle(self, *args, **options):
        groups = list(
            BiologicalMeasurement.objects.values(
                "box_id",
                "box__global_code",
                "box__organization_id",
                "box__organization__name",
                "measured_on",
            )
            .annotate(measurement_count=Count("id"))
            .filter(measurement_count__gt=1)
            .order_by("box__organization_id", "box__global_code", "measured_on")
        )
        rows_involved = sum(group["measurement_count"] for group in groups)

        self.stdout.write(f"Duplicate groups: {len(groups)}")
        self.stdout.write(f"Rows involved: {rows_involved}")
        for group in groups:
            measurement_ids = list(
                BiologicalMeasurement.objects.filter(
                    box_id=group["box_id"],
                    measured_on=group["measured_on"],
                )
                .order_by("id")
                .values_list("id", flat=True)
            )
            self.stdout.write(
                " | ".join(
                    [
                        (
                            f"organization={group['box__organization__name']} "
                            f"({group['box__organization_id']})"
                        ),
                        f"box={group['box__global_code']} ({group['box_id']})",
                        f"date={group['measured_on'].isoformat()}",
                        f"count={group['measurement_count']}",
                        f"ids={','.join(str(pk) for pk in measurement_ids)}",
                    ]
                )
            )

        if groups:
            raise CommandError(
                "Duplicate biological measurements must be reviewed explicitly "
                "before applying the uniqueness migration."
            )

        self.stdout.write(self.style.SUCCESS("No duplicate biological measurements found."))
