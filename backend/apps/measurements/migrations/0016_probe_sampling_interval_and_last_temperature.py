import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("measurements", "0015_iminilide_probe_collection"),
    ]

    operations = [
        migrations.AddField(
            model_name="probe",
            name="sampling_interval_seconds",
            field=models.PositiveSmallIntegerField(
                choices=[(60, "60"), (600, "600"), (900, "900")],
                default=60,
                validators=[django.core.validators.MinValueValidator(1)],
            ),
        ),
        migrations.AddField(
            model_name="probetemperaturebucket",
            name="last_temperature_c",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True),
        ),
        migrations.AddConstraint(
            model_name="probe",
            constraint=models.CheckConstraint(
                condition=models.Q(sampling_interval_seconds__in=[60, 600, 900]),
                name="probe_sampling_interval_allowed",
            ),
        ),
    ]
