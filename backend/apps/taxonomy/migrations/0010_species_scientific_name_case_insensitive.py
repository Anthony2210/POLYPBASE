from django.db import migrations, models
from django.db.models.functions import Lower


def refuse_case_insensitive_duplicates(apps, schema_editor):
    """Stop before the constraint rather than merging or renaming historical Species."""
    Species = apps.get_model("taxonomy", "Species")
    seen = {}
    collisions = []
    for pk, name in Species.objects.order_by("pk").values_list("pk", "scientific_name"):
        key = name.lower()
        if key in seen:
            collisions.append((seen[key], pk, name))
        else:
            seen[key] = pk
    if collisions:
        detail = "; ".join(f"Species {first} and {second} ({name!r})" for first, second, name in collisions)
        raise RuntimeError(
            "Case-insensitive duplicate scientific names must be resolved manually before "
            f"this migration: {detail}"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("taxonomy", "0009_localstrainnumbercounter"),
    ]

    operations = [
        migrations.RunPython(refuse_case_insensitive_duplicates, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="species",
            constraint=models.UniqueConstraint(Lower("scientific_name"), name="unique_species_scientific_name_ci"),
        ),
    ]
