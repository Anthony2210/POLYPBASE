"""Durable private acceptance receipt; no import or migration activation."""

import django.db.models.deletion

from django.db import migrations, models


def guards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("""
CREATE TRIGGER historical_receipt_immutable BEFORE UPDATE OR DELETE
ON measurements_historicaltemperatureimportreceipt FOR EACH ROW EXECUTE FUNCTION historical_temperature_immutable();
CREATE TRIGGER historical_receipt_no_truncate BEFORE TRUNCATE
ON measurements_historicaltemperatureimportreceipt FOR EACH STATEMENT EXECUTE FUNCTION historical_temperature_immutable();
""")


def remove_guards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("LOCK TABLE measurements_historicaltemperatureimportreceipt IN ACCESS EXCLUSIVE MODE")
            cursor.execute("SELECT EXISTS (SELECT 1 FROM measurements_historicaltemperatureimportreceipt)")
            if cursor.fetchone()[0]:
                raise RuntimeError("Historical receipts exist; data-preserving maintenance is required.")
        schema_editor.execute("""
DROP TRIGGER historical_receipt_immutable ON measurements_historicaltemperatureimportreceipt;
DROP TRIGGER historical_receipt_no_truncate ON measurements_historicaltemperatureimportreceipt;
""")


class Migration(migrations.Migration):
    dependencies = [
        ("measurements", "0012_historical_temperature_guards"),

    ]
    operations = [
        migrations.CreateModel(
            name="HistoricalTemperatureImportReceipt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("plan_fingerprint", models.CharField(max_length=64, unique=True)),
                ("snapshot_sha256", models.CharField(max_length=64)),
                ("accepted_plan", models.JSONField()),
                ("source_archive", models.BinaryField()),
                ("results", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor_id", models.PositiveBigIntegerField()),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="organizations.organization")),
            ],
        ),
        migrations.RunPython(guards, remove_guards),
    ]
