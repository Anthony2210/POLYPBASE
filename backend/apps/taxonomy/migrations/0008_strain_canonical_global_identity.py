from django.db import migrations, models


def check_owned_global_duplicates(apps, schema_editor):
    strain_model = apps.get_model("taxonomy", "Strain")
    duplicate = (
        strain_model.objects.using(schema_editor.connection.alias)
        .filter(organization__isnull=False, global_identity__isnull=False)
        .values("organization_id", "global_identity_id")
        .annotate(strain_count=models.Count("pk"))
        .filter(strain_count__gt=1)
        .order_by("organization_id", "global_identity_id")
        .first()
    )
    if duplicate is not None:
        raise RuntimeError(
            "Cannot add unique_owned_strain_per_global_identity: "
            f"organization_id={duplicate['organization_id']}, "
            f"global_identity_id={duplicate['global_identity_id']} "
            f"has {duplicate['strain_count']} Strains. "
            "No rows were changed. Run the Phase 3B gate on this database "
            "and resolve canonical owned duplicates before retrying."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("taxonomy", "0007_localstrainidentity_provenance_code_assignment"),
    ]

    operations = [
        migrations.RunPython(check_owned_global_duplicates, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="strain",
            constraint=models.UniqueConstraint(
                fields=["organization", "global_identity"],
                condition=models.Q(
                    organization__isnull=False,
                    global_identity__isnull=False,
                ),
                name="unique_owned_strain_per_global_identity",
            ),
        ),
    ]
