from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.cultures.models import Box
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain


class DemoStrainOwnershipTests(TestCase):
    def seed(self):
        call_command("seed_demo_data", stdout=StringIO())

    def test_seed_assigns_strains_to_box_organizations_and_is_idempotent(self):
        self.seed()
        self.assertEqual(Strain.objects.count(), 4)
        self.assertEqual(Box.objects.count(), 6)
        for box in Box.objects.select_related("strain"):
            self.assertEqual(box.strain.organization_id, box.organization_id)
        self.seed()
        self.assertEqual(Strain.objects.count(), 4)
        self.assertEqual(Box.objects.count(), 6)

    def test_foreign_strain_collision_does_not_reassign_or_mutate_it(self):
        other = Organization.objects.create(name="Other", slug="other")
        species = Species.objects.create(scientific_name="Aurelia aurita")
        strain = Strain.objects.create(species=species, code="1-ATL", organization=other, notes="Keep")
        with self.assertRaisesMessage(CommandError, "conflicts with another owner"):
            self.seed()
        strain.refresh_from_db()
        self.assertEqual(strain.organization_id, other.pk)
        self.assertEqual(strain.notes, "Keep")
        self.assertFalse(Box.objects.exists())
        self.assertFalse(Organization.objects.filter(slug="aquarium-de-paris").exists())

    def test_foreign_box_collision_does_not_reassign_it(self):
        other = Organization.objects.create(name="Other", slug="other")
        species = Species.objects.create(scientific_name="Unrelated species")
        strain = Strain.objects.create(species=species, code="OTHER", organization=other)
        box = Box.objects.create(
            organization=other, global_code="AAU-1.001-ATL", box_number="001",
            strain=strain, notes="Keep"
        )
        with self.assertRaisesMessage(CommandError, "Demo box AAU-1.001-ATL conflicts"):
            self.seed()
        box.refresh_from_db()
        self.assertEqual(box.organization_id, other.pk)
        self.assertEqual(box.notes, "Keep")
        self.assertEqual(Strain.objects.count(), 1)
        self.assertFalse(Organization.objects.filter(slug="aquarium-de-paris").exists())

    def test_unowned_strain_collision_is_not_claimed(self):
        species = Species.objects.create(scientific_name="Aurelia aurita")
        strain = Strain.objects.create(species=species, code="1-ATL")
        with self.assertRaises(CommandError):
            self.seed()
        strain.refresh_from_db()
        self.assertIsNone(strain.organization_id)
        self.assertFalse(Box.objects.exists())
