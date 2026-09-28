"""Institution ownership checks for the normalized historical CSV import."""

from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.cultures.models import Box
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain


class HistoricalStrainOwnershipImportTests(TestCase):
    def setUp(self):
        self.target = Organization.objects.create(name="Import target")
        self.other = Organization.objects.create(name="Import other")
        self.species = Species.objects.create(
            scientific_name="Historical import species", genus_species_code="HIS"
        )

    def import_csv(
        self, directory, *, strain_rows, box_rows="", reset=False, dry_run=False
    ):
        tables = {
            "espece.csv": (
                "id_espece,nom_scientifique,code_espece\n"
                "1,Historical import species,HIS\n"
            ),
            "souche.csv": (
                "id_souche,id_espece,code_souche,numero_souche_local,code_provenance\n"
                + strain_rows
            ),
            "zone_thermique.csv": "id_zone,nom_zone,temperature_cible\n",
            "boite.csv": "id_boite,id_souche,code_local,numero_boite_local\n" + box_rows,
            "range.csv": "id_boite,annee,semaine,id_zone\n",
            "saisir_releve.csv": (
                "id_boite,annee,semaine,nombre_polypes,nombre_ephyrules\n"
            ),
        }
        for filename, contents in tables.items():
            (Path(directory) / filename).write_text(contents, encoding="utf-8")
        call_command(
            "import_bdd_csv",
            path=directory,
            organization=self.target.name,
            reset_boxes=reset,
            dry_run=dry_run,
            stdout=StringIO(),
        )

    def test_new_strain_is_owned_and_target_owned_strain_can_be_updated(self):
        with TemporaryDirectory() as directory:
            self.import_csv(
                directory,
                strain_rows="1,1,HIS-LAB-1,1,OLD\n",
                box_rows="1,1,first,001\n",
            )
            strain = Strain.objects.get(species=self.species, code="HIS-LAB-1")
            box = Box.objects.get(global_code="HIS-LAB-1.001")
            self.assertEqual(strain.organization, self.target)
            self.assertEqual(box.strain, strain)

            self.import_csv(
                directory,
                strain_rows="1,1,HIS-LAB-1,2,NEW\n",
                box_rows="1,1,first,001\n",
            )
            strain.refresh_from_db()
            self.assertEqual((strain.number, strain.origin_code), (2, "NEW"))
            self.assertEqual(Strain.objects.filter(species=self.species).count(), 1)
            self.assertEqual(Box.objects.filter(organization=self.target).count(), 1)

    def test_foreign_strain_collision_rolls_back_earlier_strain_creation(self):
        foreign = Strain.objects.create(
            species=self.species,
            code="HIS-LAB-2",
            organization=self.other,
            number=2,
            origin_code="KEEP",
        )
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CommandError, "another organization"):
                self.import_csv(
                    directory,
                    strain_rows="1,1,HIS-LAB-1,1,NEW\n2,1,HIS-LAB-2,9,CHANGE\n",
                )
        foreign.refresh_from_db()
        self.assertEqual(
            (foreign.organization, foreign.number, foreign.origin_code),
            (self.other, 2, "KEEP"),
        )
        self.assertEqual(Strain.objects.count(), 1)

    def test_unlinked_legacy_strain_is_not_reused(self):
        legacy = Strain.objects.create(
            species=self.species, code="HIS-LAB-1", number=1, origin_code="OLD"
        )
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CommandError, "no preexisting box"):
                self.import_csv(directory, strain_rows="1,1,HIS-LAB-1,1,OLD\n")
        legacy.refresh_from_db()
        self.assertIsNone(legacy.organization_id)

    def test_shared_legacy_strain_is_reused_without_mutation_after_reset(self):
        legacy = Strain.objects.create(
            species=self.species, code="HIS-LAB-1", number=1, origin_code="OLD"
        )
        target_box = Box.objects.create(
            organization=self.target,
            global_code="HIS-LAB-1.001",
            box_number="001",
            strain=legacy,
            status=Box.Status.INACTIVE,
        )
        other_box = Box.objects.create(
            organization=self.other,
            global_code="HIS-LAB-1.002",
            box_number="002",
            strain=legacy,
        )
        with TemporaryDirectory() as directory:
            self.import_csv(
                directory,
                strain_rows="1,1,HIS-LAB-1,1,OLD\n",
                box_rows="1,1,recreated,001\n",
                reset=True,
            )
        legacy.refresh_from_db()
        other_box.refresh_from_db()
        recreated = Box.objects.get(global_code="HIS-LAB-1.001")
        self.assertNotEqual(recreated.pk, target_box.pk)
        self.assertEqual(recreated.strain, legacy)
        self.assertEqual(other_box.strain, legacy)
        self.assertIsNone(legacy.organization_id)
        self.assertEqual((legacy.number, legacy.origin_code), (1, "OLD"))

    def test_active_target_box_allows_matching_legacy_reuse_without_reset(self):
        legacy = Strain.objects.create(
            species=self.species, code="HIS-LAB-1", number=1, origin_code="OLD"
        )
        box = Box.objects.create(
            organization=self.target,
            global_code="HIS-LAB-1.001",
            box_number="001",
            strain=legacy,
            status=Box.Status.ACTIVE,
        )
        with TemporaryDirectory() as directory:
            self.import_csv(
                directory,
                strain_rows="1,1,HIS-LAB-1,1,OLD\n",
                box_rows="1,1,existing,001\n",
            )
        legacy.refresh_from_db()
        box.refresh_from_db()
        self.assertIsNone(legacy.organization_id)
        self.assertEqual(box.strain, legacy)
        self.assertEqual(box.status, Box.Status.ACTIVE)

    def test_conflicting_legacy_fields_roll_back_reset_and_leave_shared_strain(self):
        legacy = Strain.objects.create(
            species=self.species, code="HIS-LAB-1", number=1, origin_code="OLD"
        )
        target_box = Box.objects.create(
            organization=self.target,
            global_code="HIS-LAB-1.001",
            box_number="001",
            strain=legacy,
            status=Box.Status.ACTIVE,
        )
        other_box = Box.objects.create(
            organization=self.other,
            global_code="HIS-LAB-1.002",
            box_number="002",
            strain=legacy,
        )
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(CommandError, "conflicts with imported fields"):
                self.import_csv(
                    directory, strain_rows="1,1,HIS-LAB-1,2,NEW\n", reset=True
                )
        legacy.refresh_from_db()
        self.assertEqual(
            (legacy.organization_id, legacy.number, legacy.origin_code),
            (None, 1, "OLD"),
        )
        self.assertTrue(Box.objects.filter(pk=target_box.pk).exists())
        self.assertTrue(Box.objects.filter(pk=other_box.pk).exists())

    def test_dry_run_reset_preserves_original_box_and_shared_strain(self):
        legacy = Strain.objects.create(
            species=self.species, code="HIS-LAB-1", number=1, origin_code="OLD"
        )
        box = Box.objects.create(
            organization=self.target,
            global_code="HIS-LAB-1.001",
            box_number="001",
            strain=legacy,
            status=Box.Status.INACTIVE,
        )
        with TemporaryDirectory() as directory:
            self.import_csv(
                directory,
                strain_rows="1,1,HIS-LAB-1,1,OLD\n",
                box_rows="1,1,temporary,001\n",
                reset=True,
                dry_run=True,
            )
        box.refresh_from_db()
        legacy.refresh_from_db()
        self.assertEqual(box.status, Box.Status.INACTIVE)
        self.assertIsNone(legacy.organization_id)
        self.assertEqual(Box.objects.count(), 1)

    def test_dry_run_rolls_back_new_owned_strain_and_box(self):
        with TemporaryDirectory() as directory:
            self.import_csv(
                directory,
                strain_rows="1,1,HIS-LAB-1,1,NEW\n",
                box_rows="1,1,temporary,001\n",
                dry_run=True,
            )
        self.assertFalse(Strain.objects.exists())
        self.assertFalse(Box.objects.exists())
