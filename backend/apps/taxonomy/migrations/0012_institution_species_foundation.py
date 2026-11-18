import apps.taxonomy.species_names
import django.db.models.deletion
import django.db.models.lookups
from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError


def refuse_populated_rollback(apps, schema_editor):
    InstitutionSpecies = apps.get_model("taxonomy", "InstitutionSpecies")
    if InstitutionSpecies.objects.using(schema_editor.connection.alias).exists():
        raise IrreversibleError(
            "InstitutionSpecies contains local information. Refusing to drop it; "
            "prepare an explicit preservation and rollback plan first."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0002_organization_portable_id"),
        ("taxonomy", "0011_species_normalized_name"),
    ]

    operations = [
        migrations.CreateModel(
            name="InstitutionSpecies",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("scientific_name", models.CharField(max_length=150)),
                ("worms_aphia_id", models.PositiveIntegerField(blank=True, null=True)),
                ("revision", models.PositiveBigIntegerField(default=1, editable=False)),
                ("organization", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="institution_species", to="organizations.organization",
                )),
                ("species", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="institution_information", to="taxonomy.species",
                )),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "species"), name="unique_institution_species_reference",
                    ),
                    models.UniqueConstraint(
                        models.F("organization"), apps.taxonomy.species_names.ScientificNameKey("scientific_name"),
                        name="unique_institution_species_name",
                    ),
                    models.CheckConstraint(
                        condition=~django.db.models.lookups.Exact(
                            apps.taxonomy.species_names.ScientificNameKey("scientific_name"), models.Value(""),
                        ),
                        name="institution_species_name_not_empty",
                    ),
                    models.CheckConstraint(
                        condition=(models.Q(worms_aphia_id__isnull=True)
                                   | models.Q(worms_aphia_id__gte=1, worms_aphia_id__lte=2147483647)),
                        name="institution_species_aphia_positive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(revision__gte=1), name="institution_species_revision_positive",
                    ),
                ],
            },
        ),
        migrations.RunPython(migrations.RunPython.noop, refuse_populated_rollback),
    ]
