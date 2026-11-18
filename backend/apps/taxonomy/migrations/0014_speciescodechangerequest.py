import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError


def refuse_receipt_loss(apps, schema_editor):
    connection = schema_editor.connection
    receipt = apps.get_model("taxonomy", "SpeciesCodeChangeRequest")
    if connection.vendor == "postgresql":
        if not connection.in_atomic_block:
            raise IrreversibleError("Species code receipt rollback requires an atomic migration.")
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_isolation")
            if cursor.fetchone()[0] != "read committed":
                raise IrreversibleError("Species code receipt rollback requires READ COMMITTED isolation.")
            # Exclude inserts before checking emptiness. Retain this lock through
            # DropModel and commit; NOWAIT refuses existing readers/writers.
            table = connection.ops.quote_name(receipt._meta.db_table)
            cursor.execute(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE NOWAIT")
    if receipt.objects.using(connection.alias).exists():
        raise IrreversibleError("Species code change receipts must be retained.")


class Migration(migrations.Migration):
    dependencies = [
        ("taxonomy", "0013_institutionstraincreationrequest"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SpeciesCodeChangeRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("request_id", models.UUIDField()),
                ("intent", models.JSONField()),
                ("result", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="species_code_change_requests", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="species_code_change_requests", to="organizations.organization")),
                ("profile", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="code_change_requests", to="taxonomy.institutionspecies")),
            ],
            options={
                "constraints": [models.UniqueConstraint(fields=("organization", "actor", "request_id"), name="unique_species_code_change_request")],
            },
        ),
        # Reverse runs before DropModel: a schema rollback cannot erase receipts
        # while leaving committed identifier changes in place.
        migrations.RunPython(migrations.RunPython.noop, refuse_receipt_loss),
    ]
