from django.db import migrations, models

from apps.taxonomy.species_names import ScientificNameKey


def refuse_normalized_duplicates(apps, schema_editor):
    """Report all conflicting IDs/names; never repair historical scientific data."""
    Species = apps.get_model("taxonomy", "Species")
    groups = {}
    queryset = Species.objects.using(schema_editor.connection.alias).annotate(
        name_key=ScientificNameKey("scientific_name"),
    ).order_by("pk").values_list("pk", "scientific_name", "name_key")
    for pk, name, key in queryset:
        groups.setdefault(key, []).append((pk, name))
    collisions = [items for items in groups.values() if len(items) > 1]
    if collisions:
        detail = "; ".join(
            ", ".join(f"Species {pk} ({name!r})" for pk, name in items)
            for items in collisions
        )
        raise RuntimeError(
            "Normalized duplicate scientific names must be resolved manually before "
            f"this migration; no records were changed: {detail}"
        )


class Migration(migrations.Migration):
    dependencies = [("taxonomy", "0010_species_scientific_name_case_insensitive")]

    operations = [
        migrations.RunPython(refuse_normalized_duplicates, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="species",
            constraint=models.UniqueConstraint(
                ScientificNameKey("scientific_name"),
                name="unique_species_scientific_name_normalized",
            ),
        ),
    ]
