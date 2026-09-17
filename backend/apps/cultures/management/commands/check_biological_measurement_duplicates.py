from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError

from apps.measurements.models import BiologicalMeasurement


class Command(BaseCommand):
    help = "Read-only check for multiple biological measurements in one box and ISO week."

    def handle(self, *args, **options):
        grouped = defaultdict(list)
        rows = BiologicalMeasurement.objects.values(
            "id",
            "box_id",
            "box__global_code",
            "box__organization_id",
            "box__organization__name",
            "measured_on",
        ).order_by(
            "box__organization_id",
            "box__global_code",
            "measured_on",
            "id",
        )
        for measurement in rows.iterator():
            week_start = BiologicalMeasurement.week_start_for(
                measurement["measured_on"]
            )
            grouped[(measurement["box_id"], week_start)].append(measurement)

        conflicts = [items for items in grouped.values() if len(items) > 1]
        rows_involved = sum(len(items) for items in conflicts)

        self.stdout.write(f"Duplicate weekly groups: {len(conflicts)}")
        self.stdout.write(f"Rows involved: {rows_involved}")
        for measurements in conflicts:
            first = measurements[0]
            week_start = BiologicalMeasurement.week_start_for(first["measured_on"])
            iso_year, iso_week, _iso_day = week_start.isocalendar()
            entries = ",".join(
                f"{measurement['id']}:{measurement['measured_on'].isoformat()}"
                for measurement in measurements
            )
            self.stdout.write(
                " | ".join(
                    [
                        (
                            f"organization={first['box__organization__name']} "
                            f"({first['box__organization_id']})"
                        ),
                        f"box={first['box__global_code']} ({first['box_id']})",
                        f"iso_week={iso_year}-W{iso_week:02d}",
                        f"week_start={week_start.isoformat()}",
                        f"count={len(measurements)}",
                        f"measurements={entries}",
                    ]
                )
            )

        if conflicts:
            raise CommandError(
                "Weekly biological measurement conflicts must be reviewed explicitly "
                "before applying the uniqueness migration."
            )

        self.stdout.write(
            self.style.SUCCESS("No weekly biological measurement conflicts found.")
        )
