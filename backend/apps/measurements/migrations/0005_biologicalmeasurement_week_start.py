from collections import defaultdict
from datetime import timedelta

from django.db import migrations, models


def populate_week_start(apps, schema_editor):
    measurement_model = apps.get_model("measurements", "BiologicalMeasurement")
    groups = defaultdict(list)
    measurements = list(
        measurement_model.objects.order_by("box_id", "measured_on", "id")
    )

    for measurement in measurements:
        week_start = measurement.measured_on - timedelta(
            days=measurement.measured_on.weekday()
        )
        groups[(measurement.box_id, week_start)].append(measurement)

    conflicts = [items for items in groups.values() if len(items) > 1]
    if conflicts:
        examples = "; ".join(
            f"box={items[0].box_id}, week_start={items[0].measured_on - timedelta(days=items[0].measured_on.weekday())}, "
            f"measurements={','.join(f'{item.id}:{item.measured_on}' for item in items)}"
            for items in conflicts[:10]
        )
        raise RuntimeError(
            "Weekly biological measurement conflicts must be reviewed before this "
            "migration can be applied. Run check_biological_measurement_duplicates. "
            f"Examples: {examples}"
        )

    for (box_id, week_start), items in groups.items():
        measurement_model.objects.filter(pk=items[0].pk).update(week_start=week_start)


def clear_week_start(apps, schema_editor):
    measurement_model = apps.get_model("measurements", "BiologicalMeasurement")
    measurement_model.objects.update(week_start=None)


class Migration(migrations.Migration):
    dependencies = [
        ("measurements", "0004_biologicalmeasurement_unique_box_date"),
    ]

    operations = [
        migrations.AddField(
            model_name="biologicalmeasurement",
            name="week_start",
            field=models.DateField(editable=False, null=True),
        ),
        migrations.RunPython(populate_week_start, clear_week_start),
        migrations.AlterField(
            model_name="biologicalmeasurement",
            name="week_start",
            field=models.DateField(editable=False),
        ),
        migrations.RemoveConstraint(
            model_name="biologicalmeasurement",
            name="unique_biological_measurement_per_box_date",
        ),
        migrations.AddConstraint(
            model_name="biologicalmeasurement",
            constraint=models.UniqueConstraint(
                fields=("box", "week_start"),
                name="unique_biological_measurement_per_box_week",
            ),
        ),
    ]
