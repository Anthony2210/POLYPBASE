"""Add independent weekly temperature storage without rewriting existing rows."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("measurements", "0010_measurement_import_receipt"),
    ]

    operations = [
        migrations.CreateModel(
            name="HistoricalTemperatureReading",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("iso_year", models.PositiveSmallIntegerField()),
                ("iso_week", models.PositiveSmallIntegerField()),
                ("temperature_c", models.TextField()),
                ("association_status", models.CharField(
                    choices=[("confirmed", "Confirmed"), ("pending_review", "Pending review")],
                    default="confirmed", editable=False, max_length=20,
                )),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("biological_measurement", models.OneToOneField(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="historical_temperature", to="measurements.biologicalmeasurement",
                )),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(iso_year__gte=1, iso_year__lte=9999),
                        name="historical_temp_year_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(association_status__in=["confirmed", "pending_review"]),
                        name="historical_temp_status_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(iso_week__gte=1, iso_week__lte=53),
                        name="historical_temp_week_valid",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="HistoricalTemperatureEvidence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_file", models.CharField(max_length=255)),
                ("source_sha256", models.CharField(max_length=64)),
                ("sheet", models.CharField(max_length=255)),
                ("temperature_cell", models.CharField(max_length=16)),
                ("temperature_merged_range", models.CharField(blank=True, default="", max_length=35)),
                ("source_value", models.TextField()),
                ("source_box", models.TextField()),
                ("polyp_cell", models.CharField(max_length=16)),
                ("polyp_value", models.TextField()),
                ("ephyrae_cell", models.CharField(max_length=16)),
                ("ephyrae_value", models.TextField()),
                ("week_cell", models.CharField(max_length=16)),
                ("week_value", models.TextField()),
                ("source_iso_year", models.PositiveSmallIntegerField()),
                ("source_iso_week", models.PositiveSmallIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reading", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="evidences", to="measurements.historicaltemperaturereading",
                )),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("reading", "source_sha256", "sheet", "polyp_cell", "ephyrae_cell"),
                        name="unique_historical_temp_evidence",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(source_iso_year__gte=1, source_iso_year__lte=9999),
                        name="historical_temp_src_year_valid",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(source_iso_week__gte=1, source_iso_week__lte=53),
                        name="historical_temp_src_week_valid",
                    ),
                ],
            },
        ),
    ]
