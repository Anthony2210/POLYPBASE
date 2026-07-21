from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cultures", "0004_thermalzone_salinity_psu"),
    ]

    operations = [
        migrations.AddField(
            model_name="boxtransfer",
            name="polyp_count",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
