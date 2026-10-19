"""Add canonical evidence and extend historical guards, without correcting data."""

from importlib import import_module

import django.db.models.deletion
from django.db import migrations, models


_ORIGINAL = import_module(
    "apps.measurements.migrations.0012_historical_temperature_guards"
).SQL


def _function(name):
    start = _ORIGINAL.index(f"CREATE FUNCTION {name}()")
    end = _ORIGINAL.index("END $$;", start) + len("END $$;")
    return _ORIGINAL[start:end].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


_NAMES = (
    "historical_temperature_reading_valid",
    "historical_temperature_evidence_valid",
    "historical_temperature_biology_guard",
    "historical_temperature_box_guard",
)
REVERSE_SQL = "\n".join(_function(name) for name in _NAMES)

_reading = _function(_NAMES[0]).replace(
    "IF b.box_id <> target_box OR",
    "IF b.duplicate_of_id IS NOT NULL OR EXISTS (SELECT 1 FROM cultures_box "
    "WHERE id = target_box AND merged_into_id IS NOT NULL) OR b.box_id <> target_box OR",
)
_evidence = _function(_NAMES[1]).replace(
    "IF r.association_status IS DISTINCT FROM 'confirmed'",
    "IF b.duplicate_of_id IS NOT NULL OR EXISTS (SELECT 1 FROM cultures_box "
    "WHERE id = target_box AND merged_into_id IS NOT NULL) "
    "OR r.association_status IS DISTINCT FROM 'confirmed'",
).replace(
    "label_parts[1] || '.' || lpad((label_parts[2]::integer)::text, 3, '0') <> code",
    "NOT (label_parts[1] || '.' || lpad((label_parts[2]::integer)::text, 3, '0') = code "
    "OR (label_parts[1] = 'PCA-CHO-1' AND label_parts[2]::integer = 5 "
    "AND code = 'PCA-JKA-1.005'))",
)
_biology = _function(_NAMES[2]).replace(
    "(NEW.box_id, NEW.week_start,", "(NEW.duplicate_of_id, NEW.box_id, NEW.week_start,",
).replace(
    "(OLD.box_id, OLD.week_start,", "(OLD.duplicate_of_id, OLD.box_id, OLD.week_start,",
).replace(
    "IF NEW.box_id IS DISTINCT FROM OLD.box_id AND EXISTS (",
    "IF (NEW.box_id IS DISTINCT FROM OLD.box_id OR NEW.duplicate_of_id IS NOT NULL) AND EXISTS (",
)
_box = _function(_NAMES[3]).replace(
    "(NEW.organization_id, NEW.global_code,", "(NEW.merged_into_id, NEW.organization_id, NEW.global_code,",
).replace(
    "(OLD.organization_id, OLD.global_code,", "(OLD.merged_into_id, OLD.organization_id, OLD.global_code,",
)
SQL = "\n".join((_reading, _evidence, _biology, _box))


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(SQL)


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(REVERSE_SQL)


class Migration(migrations.Migration):
    dependencies = [
        ("measurements", "0013_historical_temperature_receipt"),
        ("cultures", "0015_box_merged_into_box_previous_global_code_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="biologicalmeasurement",
            name="duplicate_of",
            field=models.ForeignKey(
                blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="duplicate_measurements",
                to="measurements.biologicalmeasurement",
            ),
        ),
        migrations.AddField(
            model_name="biologicalmeasurement",
            name="environment_source_box",
            field=models.ForeignKey(
                blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="environment_source_measurements", to="cultures.box",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="biologicalmeasurement",
            name="unique_biological_measurement_per_box_week",
        ),
        migrations.AddConstraint(
            model_name="biologicalmeasurement",
            constraint=models.UniqueConstraint(
                fields=("box", "week_start"),
                condition=models.Q(duplicate_of__isnull=True),
                name="unique_biological_measurement_per_box_week",
            ),
        ),
        migrations.AddConstraint(
            model_name="biologicalmeasurement",
            constraint=models.CheckConstraint(
                condition=~models.Q(duplicate_of=models.F("pk")),
                name="measurement_duplicate_not_self",
            ),
        ),
        # Restore old functions before removing their referenced columns on reversal.
        migrations.RunPython(forwards, backwards),
    ]
