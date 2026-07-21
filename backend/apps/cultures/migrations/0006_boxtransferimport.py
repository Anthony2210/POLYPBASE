from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("cultures", "0005_boxtransfer_polyp_count"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BoxTransferImport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("format_version", models.CharField(max_length=80)),
                ("source_transfer_id", models.CharField(max_length=120)),
                ("source_organization_name", models.CharField(max_length=180)),
                ("source_global_code", models.CharField(max_length=100)),
                ("imported_at", models.DateTimeField(auto_now_add=True)),
                ("source_data", models.JSONField(default=dict)),
                ("created_box", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="transfer_import", to="cultures.box")),
                ("destination_organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="box_transfer_imports", to="organizations.organization")),
                ("imported_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="box_transfer_imports", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="boxtransferimport",
            constraint=models.UniqueConstraint(fields=("format_version", "source_organization_name", "source_transfer_id"), name="unique_imported_box_transfer"),
        ),
    ]
