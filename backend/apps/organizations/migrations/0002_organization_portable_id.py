import uuid

from django.db import migrations, models


def populate_portable_ids(apps, schema_editor):
    organization_model = apps.get_model("organizations", "Organization")
    organizations = organization_model.objects.using(schema_editor.connection.alias)
    for pk in organizations.filter(portable_id__isnull=True).values_list("pk", flat=True).iterator():
        organizations.filter(pk=pk, portable_id__isnull=True).update(portable_id=uuid.uuid4())


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="portable_id",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(populate_portable_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="organization",
            name="portable_id",
            field=models.UUIDField(default=uuid.uuid4, unique=True, editable=False),
        ),
    ]
