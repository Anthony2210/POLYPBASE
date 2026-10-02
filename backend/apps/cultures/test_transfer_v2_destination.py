from dataclasses import FrozenInstanceError, asdict
from unittest.mock import patch
from uuid import uuid4

from django.db import connection
from django.db.models import QuerySet
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import ValidationError

from apps.audit.models import AuditLog
from apps.organizations.models import Organization
from apps.taxonomy.models import (
    BiologicalProvenance, GlobalStrainIdentity, LocalStrainIdentity,
    OrganizationProvenanceCode, OrganizationSpeciesCode, Species, Strain,
)

from .models import Box, BoxLineage
from .transfer_v2_destination import (
    DestinationStrainInputSerializer, DestinationStrainResolution,
    DestinationStrainStatus as Status, GlobalIdentityState as IdentityState,
    resolve_destination_strain,
)
from .transfer_v2_protocol import TransferItemSerializer


class DestinationStrainResolverTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.destination = Organization.objects.create(name="Destination", slug="resolver-destination")
        cls.foreign = Organization.objects.create(name="Foreign", slug="resolver-foreign")
        cls.species = Species.objects.create(scientific_name="Aurelia aurita")
        cls.other_species = Species.objects.create(scientific_name="Aurelia coerulea")
        cls.global_identity = GlobalStrainIdentity.objects.create()
        cls.aaa = OrganizationSpeciesCode.objects.create(
            organization=cls.destination, species=cls.species, code="AAU",
        )
        cls.foreign_aaa = OrganizationSpeciesCode.objects.create(
            organization=cls.foreign, species=cls.species, code="AAU",
        )
        cls.other_species_aaa = OrganizationSpeciesCode.objects.create(
            organization=cls.destination, species=cls.other_species, code="ACO",
        )
        cls.provenance = BiologicalProvenance.objects.create(name="Curated source")
        cls.bbb = OrganizationProvenanceCode.objects.create(
            organization=cls.destination, biological_provenance=cls.provenance, code="BBB",
        )
        cls.foreign_bbb = OrganizationProvenanceCode.objects.create(
            organization=cls.foreign, biological_provenance=cls.provenance, code="BBB",
        )

    def strain(self, **kwargs):
        return Strain.objects.create(**{
            "organization": self.destination, "species": self.species,
            "global_identity": self.global_identity, "code": "LOCAL", **kwargs,
        })

    def local_identity(self, strain, **kwargs):
        # Technical writers can store contradictory relationships; observe, do not repair.
        return LocalStrainIdentity.objects.create(**{
            "strain": strain, "species_code_assignment": self.aaa, **kwargs,
        })

    def resolve(self, **kwargs):
        with patch.object(QuerySet, "select_for_update", side_effect=AssertionError("Unexpected lock")):
            with CaptureQueriesContext(connection) as queries:
                result = resolve_destination_strain(**{
                    "destination_organization": self.destination,
                    "incoming_global_strain_uuid": self.global_identity.global_id,
                    "incoming_species_scientific_name": self.species.scientific_name,
                    **kwargs,
                })
        self.assertTrue(queries.captured_queries)
        for query in queries.captured_queries:
            self.assertTrue(query["sql"].lstrip().upper().startswith("SELECT "), query["sql"])
            self.assertNotIn("FOR UPDATE", query["sql"].upper())
        self.assertIsInstance(result, DestinationStrainResolution)
        if result.status != Status.REUSE:
            self.assertIsNone(result.destination_strain_id)
        return result

    def assert_invalid(self, field, **kwargs):
        with self.assertNumQueries(0), self.assertRaises(ValidationError) as raised:
            resolve_destination_strain(**{
                "destination_organization": self.destination,
                "incoming_global_strain_uuid": self.global_identity.global_id,
                "incoming_species_scientific_name": self.species.scientific_name,
                **kwargs,
            })
        self.assertIn(field, raised.exception.detail)

    def test_malformed_uuid_is_input_error_not_unknown_identity(self):
        for value in (None, "", "not-a-uuid", 123, True, {}, []):
            with self.subTest(value=value):
                self.assert_invalid("global_strain_id", incoming_global_strain_uuid=value)

    def test_species_snapshot_validation_matches_protocol(self):
        for value in (None, "", 123, True, {}, [], "a" * 151, "  ", " Aurelia aurita "):
            with self.subTest(value=value):
                data = {"species_scientific_name": value, "global_strain_id": uuid4()}
                resolver = DestinationStrainInputSerializer(data=data)
                protocol = TransferItemSerializer(data={
                    **data, "item_id": uuid4(), "source_box_code": "BOX.001",
                    "source_strain_code": "SOURCE", "declared_polyp_quantity": 0,
                })
                self.assertEqual(resolver.is_valid(), protocol.is_valid())
                if resolver.is_valid():
                    self.assertEqual(resolver.validated_data["species_scientific_name"], value)
                    self.resolve(incoming_species_scientific_name=value)
                else:
                    self.assert_invalid("species_scientific_name", incoming_species_scientific_name=value)
        serializer = DestinationStrainInputSerializer(data={"global_strain_id": uuid4()})
        self.assertFalse(serializer.is_valid())
        self.assertIn("species_scientific_name", serializer.errors)

    def test_missing_destination_cannot_fall_back_to_unowned_scope(self):
        for value in (None, Organization(name="Unsaved"), self.destination.pk):
            with self.subTest(value=value):
                self.assert_invalid("destination_organization", destination_organization=value)

    def test_reuses_exact_destination_pk_with_coherent_local_identity(self):
        strain = self.strain()
        self.local_identity(strain, provenance_code_assignment=self.bbb)
        result = self.resolve(incoming_global_strain_uuid=str(self.global_identity.global_id))
        self.assertEqual(result, DestinationStrainResolution(
            status=Status.REUSE, identity_state=IdentityState.KNOWN,
            destination_strain_id=strain.pk,
        ))
        with self.assertRaises(FrozenInstanceError):
            result.destination_strain_id = 0

    def test_missing_local_identity_is_reusable_not_a_new_representation(self):
        strain = self.strain()
        result = self.resolve()
        self.assertEqual(result.status, Status.REUSE)
        self.assertEqual(result.destination_strain_id, strain.pk)
        self.assertTrue(result.missing_local_identity)
        self.assertFalse(LocalStrainIdentity.objects.exists())
        self.assertEqual(Strain.objects.count(), 1)

    def test_missing_aaa_does_not_create_anything(self):
        self.aaa.delete()
        strain = self.strain()
        result = self.resolve()
        self.assertEqual(result.status, Status.REUSE)
        self.assertEqual(result.destination_strain_id, strain.pk)
        self.assertTrue(result.missing_local_identity)
        self.assertFalse(OrganizationSpeciesCode.objects.filter(organization=self.destination, species=self.species).exists())
        self.assertFalse(LocalStrainIdentity.objects.exists())

    def test_foreign_and_legacy_representations_do_not_affect_owned_reuse(self):
        strain = self.strain()
        self.strain(organization=self.foreign, code="FOREIGN", species=self.other_species)
        self.strain(organization=None, code="LEGACY")
        result = self.resolve()
        self.assertEqual(result.status, Status.REUSE)
        self.assertEqual(result.destination_strain_id, strain.pk)
        self.assertFalse(result.legacy_unowned_representation_present)

    def test_disconnected_box_families_do_not_add_candidates(self):
        strain = self.strain()
        boxes = [Box.objects.create(
            organization=self.destination, strain=strain, global_code=f"FAMILY.{index}",
            box_number=str(index),
        ) for index in range(4)]
        BoxLineage.objects.create(parent_box=boxes[0], child_box=boxes[1])
        BoxLineage.objects.create(parent_box=boxes[2], child_box=boxes[3])
        result = self.resolve()
        self.assertEqual(result.status, Status.REUSE)
        self.assertEqual(result.destination_strain_id, strain.pk)

    def test_known_identity_without_owned_representation(self):
        result = self.resolve()
        self.assertEqual(result.status, Status.NEW_LOCAL_REPRESENTATION_REQUIRED)
        self.assertEqual(result.identity_state, IdentityState.KNOWN)
        self.assertFalse(result.legacy_unowned_representation_present)

    def test_valid_unknown_uuid_is_not_materialized(self):
        unknown = uuid4()
        result = self.resolve(incoming_global_strain_uuid=unknown)
        self.assertEqual(result.status, Status.NEW_LOCAL_REPRESENTATION_REQUIRED)
        self.assertEqual(result.identity_state, IdentityState.UNKNOWN_LOCALLY)
        self.assertFalse(GlobalStrainIdentity.objects.filter(global_id=unknown).exists())

    def test_only_foreign_strains_return_no_operational_details(self):
        self.strain(organization=self.foreign, code="FOREIGN-SECRET", species=self.other_species)
        self.strain(
            organization=self.foreign, code="FOREIGN-SECOND",
            global_identity=GlobalStrainIdentity.objects.create(),
        )
        result = self.resolve()
        self.assertEqual(asdict(result), {
            "status": Status.NEW_LOCAL_REPRESENTATION_REQUIRED,
            "identity_state": IdentityState.KNOWN,
            "destination_strain_id": None, "missing_local_identity": False,
            "legacy_unowned_representation_present": False,
        })

    def test_legacy_with_destination_box_is_review_only_not_claimed(self):
        strain = self.strain(organization=None)
        Box.objects.create(
            organization=self.destination, strain=strain, global_code="LEGACY.001", box_number="1",
        )
        result = self.resolve()
        self.assertEqual(result.status, Status.NEW_LOCAL_REPRESENTATION_REQUIRED)
        self.assertEqual(result.identity_state, IdentityState.KNOWN)
        self.assertTrue(result.legacy_unowned_representation_present)
        strain.refresh_from_db()
        self.assertIsNone(strain.organization_id)

    def test_legacy_review_flag_matches_uuid_only(self):
        self.strain(organization=None, global_identity=GlobalStrainIdentity.objects.create())
        self.assertFalse(self.resolve().legacy_unowned_representation_present)

    def test_uuid_never_broadens_selected_destination(self):
        strain = self.strain()
        self.assertEqual(self.resolve().destination_strain_id, strain.pk)
        result = self.resolve(destination_organization=self.foreign)
        self.assertEqual(result.status, Status.NEW_LOCAL_REPRESENTATION_REQUIRED)
        self.assertIsNone(result.destination_strain_id)

    def test_same_species_and_code_without_global_identity_is_not_a_candidate(self):
        self.strain(global_identity=None)
        self.assertEqual(self.resolve().status, Status.NEW_LOCAL_REPRESENTATION_REQUIRED)

    def test_multiple_representations_never_select_by_code_pk_species_or_identity(self):
        for first_matches in (True, False):
            with self.subTest(first_matches=first_matches):
                Strain.objects.all().delete()
                first = self.strain(code="ZZZ", species=self.species if first_matches else self.other_species)
                second = self.strain(
                    code="AAA", species=self.other_species if first_matches else self.species,
                    global_identity=GlobalStrainIdentity.objects.create(),
                )
                matching = first if first_matches else second
                self.local_identity(matching)
                real_values = QuerySet.values

                def historical_candidates(queryset, *fields, **expressions):
                    if queryset.model is Strain and "local_identity__pk" in fields:
                        # Observe a pre-constraint candidate set without disabling the DB invariant.
                        return real_values(Strain.objects.filter(pk__in=[first.pk, second.pk]),
                                           *fields, **expressions)
                    return real_values(queryset, *fields, **expressions)

                with patch.object(QuerySet, "values", autospec=True, side_effect=historical_candidates):
                    result = self.resolve()
                self.assertEqual(result.status, Status.CONFLICT_MULTIPLE_LOCAL_REPRESENTATIONS)
                self.assertEqual(result.identity_state, IdentityState.KNOWN)
                LocalStrainIdentity.objects.all().delete()

    def test_species_mismatch_is_not_hidden_by_candidate_filtering(self):
        self.strain(species=self.other_species)
        result = self.resolve()
        self.assertEqual(result.status, Status.CONFLICT_SPECIES_SNAPSHOT_MISMATCH)
        self.assertEqual(result.identity_state, IdentityState.KNOWN)

    def test_species_evidence_is_exact_without_trim_case_or_synonym_equivalence(self):
        self.strain()
        for name in (" Aurelia aurita ", "aurelia aurita", "Aurelia sp."):
            with self.subTest(name=name):
                self.assertEqual(self.resolve(incoming_species_scientific_name=name).status,
                                 Status.CONFLICT_SPECIES_SNAPSHOT_MISMATCH)

    def test_aaa_owner_mismatch_is_conflict(self):
        self.local_identity(self.strain(), species_code_assignment=self.foreign_aaa)
        self.assertEqual(self.resolve().status, Status.CONFLICT_LOCAL_IDENTITY_INCONSISTENT)

    def test_aaa_species_mismatch_is_conflict(self):
        self.local_identity(self.strain(), species_code_assignment=self.other_species_aaa)
        self.assertEqual(self.resolve().status, Status.CONFLICT_LOCAL_IDENTITY_INCONSISTENT)

    def test_bbb_owner_mismatch_is_conflict(self):
        self.local_identity(self.strain(), provenance_code_assignment=self.foreign_bbb)
        self.assertEqual(self.resolve().status, Status.CONFLICT_LOCAL_IDENTITY_INCONSISTENT)

    def test_coherent_aaa_without_bbb_is_reusable(self):
        self.local_identity(self.strain())
        result = self.resolve()
        self.assertEqual(result.status, Status.REUSE)
        self.assertFalse(result.missing_local_identity)

    def test_species_mismatch_precedes_local_identity_inconsistency(self):
        self.local_identity(self.strain(), species_code_assignment=self.foreign_aaa)
        self.assertEqual(self.resolve(incoming_species_scientific_name=self.other_species.scientific_name).status,
                         Status.CONFLICT_SPECIES_SNAPSHOT_MISMATCH)

    def test_observation_preserves_all_identity_and_audit_rows(self):
        self.local_identity(self.strain(), species_code_assignment=self.foreign_aaa)
        models = (
            GlobalStrainIdentity, Strain, Species, LocalStrainIdentity,
            OrganizationSpeciesCode, OrganizationProvenanceCode, AuditLog,
        )
        before = [list(model.objects.order_by("pk").values()) for model in models]
        self.resolve()
        self.resolve(incoming_global_strain_uuid=uuid4())
        self.assertEqual(before, [list(model.objects.order_by("pk").values()) for model in models])
