from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("measurements", "0003_biologicalmeasurement_salinity_psu"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="biologicalmeasurement",
            constraint=models.UniqueConstraint(
                fields=("box", "measured_on"),
                name="unique_biological_measurement_per_box_date",
            ),
        ),
    ]
