from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase

from apps.cultures.models import Box
from apps.organizations.models import Organization

from .models import (
    GlobalStrainIdentity,
    LocalStrainIdentity,
    OrganizationSpeciesCode,
    Origin,
    Species,
    Strain,
)
from .services import create_local_strain_identity


class LocalStrainServiceTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Institution A")
        self.other_organization = Organization.objects.create(name="Institution B")
        self.species = Species.objects.create(
            scientific_name="Species one", genus_species_code="OLD"
        )
        self.other_species = Species.objects.create(scientific_name="Species two")
        self.assignment = OrganizationSpeciesCode.objects.create(
            organization=self.organization, species=self.species, code="AAA"
        )
        self.global_identity = GlobalStrainIdentity.objects.create()
        self.origin = Origin.objects.create(description="Historical acquisition")
        self.strain = Strain.objects.create(
            organization=self.organization,
            species=self.species,
            global_identity=self.global_identity,
            origin=self.origin,
            code="ISSUED-1",
            number=7,
            origin_code="OLD",
        )

    def create_identity(self, *, strain=None, organization=None, assignment=None):
        return create_local_strain_identity(
            strain=strain or self.strain,
            organization=organization or self.organization,
            species_code_assignment=assignment or self.assignment,
        )

    def assert_rejected(self, code, **kwargs):
        before = LocalStrainIdentity.objects.count()
        with self.assertRaises(ValidationError) as raised:
            self.create_identity(**kwargs)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(LocalStrainIdentity.objects.count(), before)

    def test_creates_exact_assignment_without_changing_issued_identifiers(self):
        box = Box.objects.create(
            organization=self.organization, strain=self.strain, origin=self.origin,
            global_code="ISSUED-1.001", box_number="001",
        )
        identity = self.create_identity()
        self.assertEqual(LocalStrainIdentity.objects.count(), 1)
        self.assertEqual(identity.strain_id, self.strain.pk)
        self.assertEqual(identity.species_code_assignment_id, self.assignment.pk)
        self.assertEqual(identity.species_code_assignment.organization_id, self.organization.pk)
        self.assertEqual(identity.species_code_assignment.species_id, self.species.pk)
        self.strain.refresh_from_db()
        box.refresh_from_db()
        self.assertEqual(
            (self.strain.code, self.strain.number, self.strain.origin_code,
             self.strain.organization_id, self.strain.species_id,
             self.strain.global_identity_id),
            ("ISSUED-1", 7, "OLD", self.organization.pk, self.species.pk,
             self.global_identity.pk),
        )
        self.assertEqual((box.global_code, box.box_number), ("ISSUED-1.001", "001"))

    def test_unowned_strain_is_ineligible_even_with_an_assignment(self):
        legacy = Strain.objects.create(species=self.species, code="LEGACY")
        self.assert_rejected("unowned_strain", strain=legacy)
        legacy.refresh_from_db()
        self.assertIsNone(legacy.organization_id)

    def test_foreign_active_organization_is_rejected(self):
        self.assert_rejected("foreign_organization", organization=self.other_organization)

    def test_foreign_assignment_object_and_pk_are_rejected(self):
        foreign = OrganizationSpeciesCode.objects.create(
            organization=self.other_organization, species=self.species, code="AAA"
        )
        self.assert_rejected("foreign_assignment", assignment=foreign)
        self.assert_rejected(
            "foreign_assignment", assignment=OrganizationSpeciesCode(pk=foreign.pk)
        )

    def test_assignment_for_another_species_is_rejected(self):
        other = OrganizationSpeciesCode.objects.create(
            organization=self.organization, species=self.other_species, code="BBB"
        )
        self.assert_rejected("species_mismatch", assignment=other)

    def test_duplicate_identity_is_rejected_cleanly(self):
        identity = self.create_identity()
        self.assert_rejected("identity_exists")
        self.assertEqual(LocalStrainIdentity.objects.get(strain=self.strain).pk, identity.pk)

    def test_shared_global_identity_does_not_grant_foreign_assignment_access(self):
        foreign_strain = Strain.objects.create(
            organization=self.other_organization, species=self.species,
            global_identity=self.global_identity, code="FOREIGN-1",
        )
        foreign_assignment = OrganizationSpeciesCode.objects.create(
            organization=self.other_organization, species=self.species, code="AAA"
        )
        self.assert_rejected("foreign_assignment", assignment=foreign_assignment)
        self.assert_rejected("foreign_organization", strain=foreign_strain)

    def test_reloads_stale_assignment_before_validating(self):
        assignment = OrganizationSpeciesCode.objects.get(pk=self.assignment.pk)
        OrganizationSpeciesCode.objects.filter(pk=assignment.pk).update(
            organization=self.other_organization
        )
        self.assert_rejected("foreign_assignment", assignment=assignment)

    def test_reloads_stale_strain_before_validating(self):
        strain = Strain.objects.get(pk=self.strain.pk)
        Strain.objects.filter(pk=strain.pk).update(organization=self.other_organization)
        self.assert_rejected("foreign_organization", strain=strain)


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row locks are required")
class LocalStrainServiceConcurrencyTests(TransactionTestCase):
    def test_concurrent_creation_has_one_winner_and_a_clean_duplicate_error(self):
        organization = Organization.objects.create(name="Concurrency institution")
        species = Species.objects.create(scientific_name="Concurrency species")
        assignment = OrganizationSpeciesCode.objects.create(
            organization=organization, species=species, code="AAA"
        )
        strain = Strain.objects.create(
            organization=organization, species=species, code="ISSUED-CONCURRENT"
        )
        start = Barrier(2)

        def create():
            close_old_connections()
            try:
                start.wait(timeout=10)
                try:
                    create_local_strain_identity(
                        strain=strain, organization=organization,
                        species_code_assignment=assignment,
                    )
                    return "created"
                except ValidationError as error:
                    return error.code
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(create) for _ in range(2)]
            self.assertCountEqual([future.result(timeout=15) for future in results],
                                  ["created", "identity_exists"])
        self.assertEqual(LocalStrainIdentity.objects.filter(strain=strain).count(), 1)
