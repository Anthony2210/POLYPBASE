import json
from dataclasses import is_dataclass
from datetime import date
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from apps.cultures.models import (
    Box, BoxLineage, BoxLocation, BoxMovement, BoxTransfer, SubcultureEvent,
    ThermalZone, TransferEnvelope, TransferItem,
)
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization

from .diagnostics import _duplicate_groups, _reference_counts, diagnose_global_strain_identity_state
from .models import (
    BiologicalProvenance, GlobalStrainIdentity, LocalStrainIdentity,
    OrganizationProvenanceCode, OrganizationSpeciesCode, Species, Strain,
)


CATEGORY_CODES = {
    "CANONICAL_OWNED_DUPLICATES", "GLOBAL_ID_MULTIPLE_SPECIES",
    "UNOWNED_GLOBAL_REPRESENTATIONS", "LOCAL_AAA_INCONSISTENCY",
    "LOCAL_BBB_INCONSISTENCY", "OWNED_WITHOUT_LOCAL_IDENTITY",
    "CURRENT_SPECIES_CODE_DUPLICATES", "FUTURE_ORGANIZATION_CODE_COLLISIONS",
    "FUTURE_ORGANIZATION_SPECIES_CODE_COLLISIONS",
    "NORMALIZED_ORGANIZATION_CODE_COLLISIONS", "BOX_PREFIX_COUPLING",
    "BOX_GLOBAL_CODE_DUPLICATES", "CONSOLIDATION_IMPACT",
}
CLASSIFICATIONS = {
    "DIRECT_CONSTRAINT_BLOCKER", "INTEGRITY_REVIEW_REQUIRED",
    "INFORMATIONAL_FUTURE_NAMESPACE", "SCHEMA_INTEGRITY_BLOCKER",
}
COMMAND = "diagnose_global_strain_identity"


class DuplicateGroupTests(SimpleTestCase):
    def test_only_repeated_composite_keys_are_groups(self):
        rows = [
            {"species_id": 1, "code": "SAME", "strain_id": 11},
            {"species_id": 1, "code": "SAME", "strain_id": 12},
            {"species_id": 2, "code": "SAME", "strain_id": 13},
            {"species_id": 1, "code": "OTHER", "strain_id": 14},
        ]
        before = [dict(row) for row in rows]
        groups = _duplicate_groups(rows, ("species_id", "code"))
        self.assertEqual(len(groups), 1)
        self.assertEqual(rows, before)
        self.assertEqual(groups, _duplicate_groups(rows, ("species_id", "code")))
        self.assertFalse(_duplicate_groups(rows[1:], ("species_id", "code")))

    def test_box_duplicate_classifier_and_empty_input(self):
        self.assertFalse(_duplicate_groups([], ("global_code",)))
        rows = [
            {"global_code": "BOX.001", "id": 1},
            {"global_code": "BOX.001", "id": 2},
            {"global_code": "BOX.002", "id": 3},
        ]
        self.assertEqual(len(_duplicate_groups(rows, ("global_code",))), 1)


class GlobalStrainDiagnosticTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Diagnostic institution")
        self.other_organization = Organization.objects.create(name="Other institution")
        self.species = Species.objects.create(scientific_name="Diagnostic species")
        self.other_species = Species.objects.create(scientific_name="Other species")
        self.assignment = OrganizationSpeciesCode.objects.create(
            organization=self.organization, species=self.species, code="AAA",
        )

    def strain(self, code="AAA-BBB-1", *, organization=None, species=None,
               identity=None, local=True, unowned=False):
        strain = Strain.objects.create(
            organization=None if unowned else (organization or self.organization),
            species=species or self.species, code=code, number=0,
            global_identity=identity or GlobalStrainIdentity.objects.create(),
        )
        if local:
            assignment, _ = OrganizationSpeciesCode.objects.get_or_create(
                organization=organization or self.organization,
                species=species or self.species,
                defaults={"code": "CCC" if species == self.other_species else "AAA"},
            )
            LocalStrainIdentity.objects.create(
                strain=strain, species_code_assignment=assignment,
            )
        return strain

    def report(self, readiness=None):
        report = diagnose_global_strain_identity_state()
        self.assertTrue(is_dataclass(report))
        payload = report.to_dict()
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["scan_status"], "COMPLETE")
        self.assertEqual(payload["errors"], [])
        self.assertIsInstance(payload["constraint_applicable"], bool)
        if readiness is not None:
            self.assertEqual(payload["readiness"], readiness)
        categories = payload["categories"]
        self.assertEqual({item["code"] for item in categories}, CATEGORY_CODES)
        self.assertEqual(len(categories), len(CATEGORY_CODES))
        for item in categories:
            self.assertIn(item["classification"], CLASSIFICATIONS)
            self.assertIsInstance(item["phase_3c_relevant"], bool)
            self.assertIsInstance(item["reason"], str)
            self.assertTrue(item["reason"])
            self.assertEqual(item["count"], len(item["records"]))
        impact = next(item for item in categories if item["code"] == "CONSOLIDATION_IMPACT")
        eligible_ids = set()
        for item in categories:
            if item["code"] in impact["scope"]["eligible_categories"]:
                for record in item["records"]:
                    members = record["strains"] if "strains" in record else [record]
                    eligible_ids.update(member["strain_id"] for member in members)
        self.assertEqual({row["strain_id"] for row in impact["records"]}, eligible_ids)
        # The command must be able to serialize the complete evidence without a
        # custom encoder or ORM objects leaking into its public payload.
        self.assertEqual(json.loads(json.dumps(payload)), payload)
        return payload

    def category(self, payload, code, count=None, classification=None):
        item = next(item for item in payload["categories"] if item["code"] == code)
        if count is not None:
            self.assertEqual(item["count"], count)
        if classification is not None:
            self.assertEqual(item["classification"], classification)
        return item["records"]

    def evidence(self, record, strain, box_count=0, box_organization_count=0):
        strain.refresh_from_db()
        local_id = LocalStrainIdentity.objects.filter(strain=strain).values_list(
            "id", flat=True,
        ).first()
        expected = {
            "strain_id": strain.pk,
            "organization_id": strain.organization_id,
            "organization_uuid": str(strain.organization.portable_id)
            if strain.organization_id else None,
            "global_identity_id": strain.global_identity_id,
            "global_uuid": str(strain.global_identity.global_id)
            if strain.global_identity_id else None,
            "species_id": strain.species_id,
            "species_name": strain.species.scientific_name,
            "code": strain.code, "number": strain.number,
            "local_identity_id": local_id, "box_count": box_count,
            "box_organization_count": box_organization_count,
        }
        for key, value in expected.items():
            self.assertEqual(record[key], value, key)

    def box(self, strain, code, status=Box.Status.ACTIVE, organization=None):
        return Box.objects.create(
            strain=strain, organization=organization or self.organization,
            global_code=code, box_number=code.rsplit(".", 1)[-1], status=status,
        )

    def test_empty_database_is_ready(self):
        payload = self.report("READY")
        self.assertTrue(payload["constraint_applicable"])
        self.assertTrue(all(item["count"] == 0 for item in payload["categories"]))

    def test_clean_representations_across_organizations_are_ready_and_repeatable(self):
        first = self.strain()
        self.strain("OTHER-1", organization=self.other_organization,
                    identity=first.global_identity)
        before = list(Strain.objects.order_by("id").values())
        payload = self.report("READY")
        self.assertTrue(payload["constraint_applicable"])
        self.category(payload, "CANONICAL_OWNED_DUPLICATES", 0)
        self.category(payload, "GLOBAL_ID_MULTIPLE_SPECIES", 0)
        self.assertEqual(payload, self.report("READY"))
        self.assertEqual(before, list(Strain.objects.order_by("id").values()))

    def test_canonical_owned_duplicates_are_direct_blockers(self):
        first = self.strain("FIRST")
        second = self.strain("SECOND", identity=first.global_identity)
        payload = self.report("BLOCKED")
        self.assertFalse(payload["constraint_applicable"])
        records = self.category(payload, "CANONICAL_OWNED_DUPLICATES", 1,
                                "DIRECT_CONSTRAINT_BLOCKER")
        group = records[0]
        self.assertEqual(group["organization_id"], self.organization.pk)
        self.assertEqual(group["global_identity_id"], first.global_identity_id)
        self.assertEqual(group["global_uuid"], str(first.global_identity.global_id))
        evidence = {row["strain_id"]: row for row in group["strains"]}
        self.assertEqual(set(evidence), {first.pk, second.pk})
        self.evidence(evidence[first.pk], first)
        self.evidence(evidence[second.pk], second)

    def test_global_identity_with_multiple_species_requires_review(self):
        first = self.strain("FIRST")
        second = self.strain("SECOND", species=self.other_species,
                             organization=self.other_organization,
                             identity=first.global_identity)
        payload = self.report("REVIEW_REQUIRED")
        self.assertTrue(payload["constraint_applicable"])
        group = self.category(payload, "GLOBAL_ID_MULTIPLE_SPECIES", 1,
                              "INTEGRITY_REVIEW_REQUIRED")[0]
        self.assertEqual(group["global_identity_id"], first.global_identity_id)
        self.assertEqual({row["strain_id"] for row in group["strains"]},
                         {first.pk, second.pk})
        self.assertEqual({row["species_id"] for row in group["strains"]},
                         {self.species.pk, self.other_species.pk})

    def test_unowned_global_representation_and_unlinked_legacy_are_distinct(self):
        strain = self.strain(unowned=True, local=False)
        Strain.objects.create(species=self.species, code="LEGACY")
        payload = self.report("REVIEW_REQUIRED")
        record = self.category(payload, "UNOWNED_GLOBAL_REPRESENTATIONS", 1,
                               "INTEGRITY_REVIEW_REQUIRED")[0]
        self.evidence(record, strain)
        self.category(payload, "CANONICAL_OWNED_DUPLICATES", 0)
        self.category(payload, "OWNED_WITHOUT_LOCAL_IDENTITY", 0)

    def test_unowned_only_keeps_basic_evidence_without_detailed_impact(self):
        strain = self.strain(unowned=True, local=False)
        self.box(strain, "AAA-BBB-1.001")
        self.box(strain, "AAA-BBB-1.002", organization=self.other_organization)
        payload = self.report("REVIEW_REQUIRED")
        record = self.category(payload, "UNOWNED_GLOBAL_REPRESENTATIONS", 1)[0]
        self.evidence(record, strain, box_count=2, box_organization_count=2)
        self.category(payload, "CONSOLIDATION_IMPACT", 0)
        self.assertTrue(payload["constraint_applicable"])

    def test_missing_local_identity_only_is_readiness_not_detailed_impact(self):
        strain = self.strain(local=False)
        self.box(strain, "AAA-BBB-1.001")
        payload = self.report("REVIEW_REQUIRED")
        record = self.category(payload, "OWNED_WITHOUT_LOCAL_IDENTITY", 1,
                               "INTEGRITY_REVIEW_REQUIRED")[0]
        self.evidence(record, strain, box_count=1, box_organization_count=1)
        category = next(row for row in payload["categories"]
                        if row["code"] == "OWNED_WITHOUT_LOCAL_IDENTITY")
        self.assertEqual(category["reason"], "NORMALIZATION_READINESS_NOT_BIOLOGICAL_DUPLICATE")
        self.category(payload, "CONSOLIDATION_IMPACT", 0)
        self.assertTrue(payload["constraint_applicable"])

    def test_excluded_states_receive_impact_when_also_in_eligible_category(self):
        cases = (
            (True, "UNOWNED_GLOBAL_REPRESENTATIONS", "GLOBAL_ID_MULTIPLE_SPECIES", "REVIEW_REQUIRED"),
            (False, "OWNED_WITHOUT_LOCAL_IDENTITY", "CANONICAL_OWNED_DUPLICATES", "BLOCKED"),
        )
        for unowned, excluded_code, eligible_code, readiness in cases:
            with self.subTest(category=excluded_code), self.atomic_fixture():
                strain = self.strain("FIRST", unowned=unowned, local=False)
                self.strain("SECOND", identity=strain.global_identity,
                            species=self.other_species if unowned else self.species)
                self.box(strain, "FIRST.001", status=Box.Status.INACTIVE)
                payload = self.report(readiness)
                self.category(payload, excluded_code, 1)
                group = self.category(payload, eligible_code, 1)[0]
                self.assertIn(strain.pk, {row["strain_id"] for row in group["strains"]})
                impacts = self.category(payload, "CONSOLIDATION_IMPACT", 2)
                record = next(row for row in impacts if row["strain_id"] == strain.pk)
                self.evidence(record, strain, box_count=1, box_organization_count=1)
                self.assertEqual(record["inactive_box_count"], 1)

    def test_serialized_impact_scope_is_explicit_even_without_findings(self):
        payload = self.report("READY")
        impact = next(row for row in payload["categories"] if row["code"] == "CONSOLIDATION_IMPACT")
        self.assertEqual(impact["scope"], {
            "eligible_categories": [
                "CANONICAL_OWNED_DUPLICATES", "GLOBAL_ID_MULTIPLE_SPECIES",
                "LOCAL_AAA_INCONSISTENCY", "LOCAL_BBB_INCONSISTENCY",
                "CURRENT_SPECIES_CODE_DUPLICATES",
            ],
            "not_automatically_included_categories": [
                "UNOWNED_GLOBAL_REPRESENTATIONS", "OWNED_WITHOUT_LOCAL_IDENTITY",
            ],
            "inclusion_rule": "STRAINS_IN_ANY_ELIGIBLE_CATEGORY",
            "overlap_rule": "INCLUDED_IF_ALSO_IN_ELIGIBLE_CATEGORY",
            "excluded_only_evidence": "CATEGORY_SPECIFIC_EVIDENCE",
            "implies_consolidation_required": False,
        })
        self.assertEqual(impact["reason"], "REFERENCE_COUNTS_NOT_A_MERGE_PLAN")
        self.assertTrue(all("scope" not in row for row in payload["categories"]
                            if row["code"] != "CONSOLIDATION_IMPACT"))
        stdout = StringIO()
        call_command(COMMAND, stdout=stdout)
        self.assertEqual(json.loads(stdout.getvalue()), payload)

    def test_aaa_mismatch_types_are_precise(self):
        wrong_organization = OrganizationSpeciesCode.objects.create(
            organization=self.other_organization, species=self.species, code="AAA",
        )
        wrong_species = OrganizationSpeciesCode.objects.create(
            organization=self.organization, species=self.other_species, code="CCC",
        )
        both = OrganizationSpeciesCode.objects.create(
            organization=self.other_organization, species=self.other_species, code="CCC",
        )
        cases = [
            ("ORG", wrong_organization, False, {"ORGANIZATION_MISMATCH"}),
            ("SPECIES", wrong_species, False, {"SPECIES_MISMATCH"}),
            ("BOTH", both, False, {"ORGANIZATION_MISMATCH", "SPECIES_MISMATCH"}),
            ("UNOWNED", self.assignment, True, {"UNOWNED_STRAIN"}),
        ]
        for code, assignment, unowned, expected in cases:
            with self.subTest(code=code), self.atomic_fixture():
                strain = self.strain(code, local=False, unowned=unowned)
                local = LocalStrainIdentity.objects.create(
                    strain=strain, species_code_assignment=assignment,
                )
                record = self.category(self.report("REVIEW_REQUIRED"),
                                       "LOCAL_AAA_INCONSISTENCY", 1,
                                       "INTEGRITY_REVIEW_REQUIRED")[0]
                self.evidence(record, strain)
                self.assertEqual(record["local_identity_id"], local.pk)
                self.assertEqual(record["assignment_id"], assignment.pk)
                self.assertEqual(record["assignment_organization_id"], assignment.organization_id)
                self.assertEqual(record["assignment_species_id"], assignment.species_id)
                self.assertEqual(set(record["mismatch_types"]), expected)

    def atomic_fixture(self):
        # Roll back each subtest's fixture without touching constraints or schema.
        from contextlib import contextmanager
        from django.db import transaction

        @contextmanager
        def isolated():
            with transaction.atomic():
                try:
                    yield
                finally:
                    transaction.set_rollback(True)
        return isolated()

    def test_bbb_mismatch_and_absent_optional_assignment(self):
        provenance = BiologicalProvenance.objects.create(name="Diagnostic source")
        assignment = OrganizationProvenanceCode.objects.create(
            organization=self.other_organization, biological_provenance=provenance,
            code="BBB",
        )
        clean = self.strain("CLEAN")
        self.category(self.report("READY"), "LOCAL_BBB_INCONSISTENCY", 0)
        for unowned, expected in [(False, {"ORGANIZATION_MISMATCH"}),
                                  (True, {"UNOWNED_STRAIN"})]:
            with self.subTest(unowned=unowned), self.atomic_fixture():
                strain = self.strain("BBB-MISMATCH", unowned=unowned)
                LocalStrainIdentity.objects.filter(strain=strain).update(
                    provenance_code_assignment=assignment,
                )
                record = self.category(self.report("REVIEW_REQUIRED"),
                                       "LOCAL_BBB_INCONSISTENCY", 1,
                                       "INTEGRITY_REVIEW_REQUIRED")[0]
                self.evidence(record, strain)
                self.assertEqual(record["assignment_id"], assignment.pk)
                self.assertEqual(record["assignment_organization_id"], assignment.organization_id)
                self.assertEqual(set(record["mismatch_types"]), expected)
                self.assertNotIn("assignment_species_id", record)
        self.assertIsNone(clean.local_identity.provenance_code_assignment_id)

    def test_owned_without_local_identity_reports_global_presence(self):
        for linked in (False, True):
            with self.subTest(linked=linked), self.atomic_fixture():
                strain = self.strain(local=False)
                if not linked:
                    Strain.objects.filter(pk=strain.pk).update(global_identity=None)
                record = self.category(self.report("REVIEW_REQUIRED"),
                                       "OWNED_WITHOUT_LOCAL_IDENTITY", 1,
                                       "INTEGRITY_REVIEW_REQUIRED")[0]
                self.evidence(record, strain)
                self.assertEqual(record["global_id_present"], linked)
                self.assertTrue(record["aaa_assignment_present"])

    def test_future_organization_code_collision_is_informational(self):
        first = self.strain("SAME")
        second = self.strain("SAME", species=self.other_species)
        payload = self.report("READY")
        self.assertTrue(payload["constraint_applicable"])
        group = self.category(payload, "FUTURE_ORGANIZATION_CODE_COLLISIONS", 1,
                              "INFORMATIONAL_FUTURE_NAMESPACE")[0]
        self.assertEqual(group["organization_id"], self.organization.pk)
        self.assertEqual({row["strain_id"] for row in group["strains"]},
                         {first.pk, second.pk})
        self.category(payload, "FUTURE_ORGANIZATION_SPECIES_CODE_COLLISIONS", 0)
        self.category(payload, "CURRENT_SPECIES_CODE_DUPLICATES", 0)
        stdout = StringIO()
        call_command(COMMAND, stdout=stdout)
        self.assertEqual(json.loads(stdout.getvalue()), payload)

    def test_normalization_is_ascii_space_and_uppercase_observation_only(self):
        first = self.strain(" mixed ")
        second = self.strain("MIXED")
        tabbed = self.strain("\tmixed\t")
        other = self.strain("MIXED", organization=self.other_organization,
                            species=self.other_species)
        payload = self.report("READY")
        group = self.category(payload, "NORMALIZED_ORGANIZATION_CODE_COLLISIONS", 1,
                              "INFORMATIONAL_FUTURE_NAMESPACE")[0]
        self.assertEqual(group["normalized_code"], "MIXED")
        self.assertEqual({row["strain_id"] for row in group["strains"]},
                         {first.pk, second.pk})
        for strain, code in [(first, " mixed "), (second, "MIXED"),
                             (tabbed, "\tmixed\t"), (other, "MIXED")]:
            strain.refresh_from_db()
            self.assertEqual(strain.code, code)

    def test_box_prefix_uses_literal_startswith_across_all_boxes(self):
        strain = self.strain("A_%")
        other = self.strain("OTHER", organization=self.other_organization)
        self.box(strain, "NOT-THE-PREFIX")
        self.box(other, "A_%.001", organization=self.other_organization)
        self.box(other, "A_%.002", organization=self.other_organization)
        self.box(other, "AXY.003", organization=self.other_organization)
        self.box(other, "A_%X.004", organization=self.other_organization)
        payload = self.report()
        records = self.category(payload, "BOX_PREFIX_COUPLING")
        record = next(row for row in records if row["code"] == strain.code)
        self.assertEqual(record["prefix"], "A_%.")
        self.assertEqual(record["box_prefix_count"], 2)
        self.assertEqual(record["referenced_box_count"], 1)
        self.evidence(record["strains"][0], strain, 1, 1)

    def test_constrained_strain_duplicates_via_mocked_values_read(self):
        first = self.strain("FIRST")
        second = self.strain("SECOND")
        real_values = Strain.objects.values

        def duplicate_read(*fields, **expressions):
            rows = list(real_values(*fields, **expressions))
            for row in rows:
                if row.get("id", row.get("strain_id")) == second.pk:
                    row["code"] = first.code
            return rows

        with patch.object(Strain.objects, "values", side_effect=duplicate_read):
            payload = self.report("BLOCKED")
        group = self.category(payload, "CURRENT_SPECIES_CODE_DUPLICATES", 1,
                              "SCHEMA_INTEGRITY_BLOCKER")[0]
        self.assertEqual({row["strain_id"] for row in group["strains"]},
                         {first.pk, second.pk})
        self.category(payload, "FUTURE_ORGANIZATION_SPECIES_CODE_COLLISIONS", 1,
                      "INFORMATIONAL_FUTURE_NAMESPACE")
        second.refresh_from_db()
        self.assertEqual(second.code, "SECOND")

    def test_constrained_box_duplicates_via_mocked_values_read(self):
        strain = self.strain()
        first = self.box(strain, "FIRST.001")
        second = self.box(strain, "SECOND.001")
        real_values = Box.objects.values

        def duplicate_read(*fields, **expressions):
            rows = list(real_values(*fields, **expressions))
            for row in rows:
                if row.get("id", row.get("box_id")) == second.pk:
                    row["global_code"] = first.global_code
            return rows

        with patch.object(Box.objects, "values", side_effect=duplicate_read):
            payload = self.report("BLOCKED")
        self.category(payload, "BOX_GLOBAL_CODE_DUPLICATES", 1,
                      "SCHEMA_INTEGRITY_BLOCKER")
        second.refresh_from_db()
        self.assertEqual(second.global_code, "SECOND.001")

    def test_impact_includes_inactive_zero_measurements_and_historical_relations(self):
        strain = self.strain("FIRST")
        self.strain("DUPLICATE", identity=strain.global_identity)
        active = self.box(strain, "FIRST.001")
        inactive = self.box(strain, "FIRST.002", Box.Status.INACTIVE)
        pending = self.box(strain, "FIRST.003", Box.Status.PENDING_REVIEW,
                           organization=self.other_organization)
        zone = ThermalZone.objects.create(organization=self.organization, name="History")
        BoxLocation.objects.create(box=inactive, thermal_zone=zone)
        BoxLocation.objects.create(box=active, thermal_zone=zone)
        BoxMovement.objects.create(box=inactive, to_thermal_zone=zone)
        event = SubcultureEvent.objects.create(parent_box=inactive)
        BoxLineage.objects.create(parent_box=inactive, child_box=active, subculture_event=event)
        BoxLineage.objects.create(parent_box=active, child_box=pending)
        zero = BiologicalMeasurement.objects.create(
            box=inactive, measured_on=date(2026, 1, 5), polyp_count=0,
            ephyrae_count=0, strobila_count=0,
        )
        BiologicalMeasurement.objects.create(
            box=active, measured_on=date(2026, 1, 5), polyp_count=3,
        )
        envelope = TransferEnvelope.objects.create(
            source_organization=self.organization,
            source_institution_id=self.organization.portable_id,
            source_institution_name=self.organization.name,
        )
        TransferItem.objects.create(
            envelope=envelope, source_box=inactive, source_box_code=inactive.global_code,
            source_strain_code=strain.code, species_scientific_name=self.species.scientific_name,
            global_strain_id=strain.global_identity.global_id, declared_polyp_quantity=0,
        )
        BoxTransfer.objects.create(
            box=inactive, from_organization=self.organization,
            to_organization=self.other_organization, polyp_count=0,
        )
        payload = self.report("BLOCKED")
        records = self.category(payload, "CONSOLIDATION_IMPACT")
        record = next(row for row in records if row["strain_id"] == strain.pk)
        self.evidence(record, strain, 3, 2)
        expected = {
            "box_count": 3, "active_box_count": 1, "inactive_box_count": 1,
            "pending_review_box_count": 1, "measurement_count": 2,
            "box_location_count": 2, "movement_count": 1,
            "parent_lineage_count": 2, "child_lineage_count": 2,
            "subculture_event_count": 1, "transfer_item_source_count": 1,
            "box_transfer_count": 1,
        }
        for key, value in expected.items():
            self.assertEqual(record[key], value, key)
        zero.refresh_from_db()
        self.assertEqual((zero.polyp_count, zero.ephyrae_count, zero.strobila_count), (0, 0, 0))
        inactive.refresh_from_db()
        self.assertEqual(inactive.status, Box.Status.INACTIVE)
        self.assertEqual(payload, self.report("BLOCKED"))

    def test_scan_is_read_only_and_does_not_acquire_row_locks(self):
        strain = self.strain()
        self.strain("DUPLICATE", identity=strain.global_identity)
        self.box(strain, "AAA-BBB-1.001")
        with CaptureQueriesContext(connection) as queries:
            payload = diagnose_global_strain_identity_state().to_dict()
        self.assertEqual(payload["scan_status"], "COMPLETE")
        self.assertTrue(queries.captured_queries)
        for query in queries.captured_queries:
            sql = query["sql"].strip().upper()
            self.assertTrue(sql.startswith("SELECT"), sql)
            self.assertNotRegex(sql, r"\b(INSERT|UPDATE|DELETE|REPLACE|ALTER|DROP|CREATE|TRUNCATE)\b")
            self.assertNotIn("FOR UPDATE", sql)
            self.assertNotIn("FOR SHARE", sql)

    def test_failed_service_read_is_sanitized_and_incomplete(self):
        with patch.object(Strain.objects, "values", side_effect=DatabaseError("private detail")):
            payload = diagnose_global_strain_identity_state().to_dict()
        self.assertEqual(payload, {
            "schema_version": 1, "scan_status": "INCOMPLETE", "readiness": "INCOMPLETE",
            "constraint_applicable": False, "categories": [], "errors": ["SCAN_FAILED"],
        })
        self.assertNotIn("private detail", json.dumps(payload))

    def test_referential_failures_and_duplicate_local_rows_are_incomplete(self):
        strain = self.strain()
        box = self.box(strain, "AAA-BBB-1.001")
        provenance = BiologicalProvenance.objects.create(name="Source")
        bbb = OrganizationProvenanceCode.objects.create(
            organization=self.organization, biological_provenance=provenance, code="BBB",
        )
        cases = [
            (Strain, strain.pk, "species_id"),
            (Strain, strain.pk, "organization_id"),
            (Strain, strain.pk, "global_identity_id"),
            (Box, box.pk, "strain_id"),
            (Box, box.pk, "organization_id"),
            (LocalStrainIdentity, strain.local_identity.pk, "species_code_assignment_id"),
            (LocalStrainIdentity, strain.local_identity.pk, "provenance_code_assignment_id"),
            (OrganizationSpeciesCode, self.assignment.pk, "organization_id"),
            (OrganizationSpeciesCode, self.assignment.pk, "species_id"),
            (OrganizationProvenanceCode, bbb.pk, "biological_provenance_id"),
        ]
        for model, pk, field in cases:
            real_values = model.objects.values

            def broken_read(*fields, **expressions):
                rows = list(real_values(*fields, **expressions))
                for row in rows:
                    if row["id"] == pk:
                        row[field] = 999999
                return rows

            with self.subTest(model=model.__name__, field=field), patch.object(
                model.objects, "values", side_effect=broken_read,
            ):
                payload = diagnose_global_strain_identity_state().to_dict()
                self.assertEqual(payload["scan_status"], "INCOMPLETE")
                self.assertFalse(payload["constraint_applicable"])
                self.assertEqual(payload["categories"], [])
                self.assertEqual(payload["errors"], ["SCAN_FAILED"])
        rows = list(LocalStrainIdentity.objects.values())
        rows.append({**rows[0], "id": rows[0]["id"] + 1000})
        with patch.object(LocalStrainIdentity.objects, "values", return_value=rows):
            self.assertEqual(diagnose_global_strain_identity_state().scan_status, "INCOMPLETE")

    def test_late_failure_discards_partial_findings_and_exits_two(self):
        first = self.strain("FIRST")
        self.strain("SECOND", identity=first.global_identity)
        stdout = StringIO()
        with patch("apps.taxonomy.diagnostics._reference_counts", side_effect=DatabaseError("private")):
            with self.assertRaises(CommandError) as caught:
                call_command(COMMAND, stdout=stdout)
        self.assertEqual(caught.exception.returncode, 2)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["categories"], [])
        self.assertEqual(payload["readiness"], "INCOMPLETE")

    def test_shuffled_reads_preserve_category_group_and_member_order(self):
        first = self.strain("FIRST")
        self.strain("SECOND", identity=first.global_identity)
        self.strain("FIRST", species=self.other_species)
        expected = self.report("BLOCKED")
        real_strains, real_boxes = Strain.objects.values, Box.objects.values
        with patch.object(Strain.objects, "values", side_effect=lambda *args: list(real_strains(*args))[::-1]), patch.object(
            Box.objects, "values", side_effect=lambda *args: list(real_boxes(*args))[::-1],
        ):
            self.assertEqual(self.report("BLOCKED"), expected)

    def test_review_only_command_succeeds_and_box_references_do_not_own_legacy(self):
        strain = self.strain(unowned=True, local=False)
        self.box(strain, "AAA-BBB-1.001")
        self.box(strain, "AAA-BBB-1.002", organization=self.other_organization)
        payload = self.report("REVIEW_REQUIRED")
        record = self.category(payload, "UNOWNED_GLOBAL_REPRESENTATIONS", 1)[0]
        self.evidence(record, strain, 2, 2)
        stdout = StringIO()
        call_command(COMMAND, stdout=stdout)
        self.assertEqual(json.loads(stdout.getvalue()), payload)
        strain.refresh_from_db()
        self.assertIsNone(strain.organization_id)

    def test_null_global_id_is_not_a_canonical_group(self):
        for code in ("FIRST", "SECOND"):
            Strain.objects.create(organization=self.organization, species=self.species, code=code)
        payload = self.report("REVIEW_REQUIRED")
        self.category(payload, "CANONICAL_OWNED_DUPLICATES", 0)
        self.assertTrue(payload["constraint_applicable"])

    def test_coherent_bbb_is_not_flagged_and_missing_aaa_is_reported(self):
        strain = self.strain()
        provenance = BiologicalProvenance.objects.create(name="Source")
        bbb = OrganizationProvenanceCode.objects.create(
            organization=self.organization, biological_provenance=provenance, code="BBB",
        )
        LocalStrainIdentity.objects.filter(strain=strain).update(provenance_code_assignment=bbb)
        self.category(self.report("READY"), "LOCAL_BBB_INCONSISTENCY", 0)
        missing = Strain.objects.create(
            organization=self.other_organization, species=self.other_species, code="NO-AAA",
        )
        record = self.category(self.report("REVIEW_REQUIRED"), "OWNED_WITHOUT_LOCAL_IDENTITY", 1)[0]
        self.evidence(record, missing)
        self.assertFalse(record["aaa_assignment_present"])

    def test_reference_counts_batch_large_affected_sets(self):
        strain = self.strain()
        box = self.box(strain, "AAA-BBB-1.001")
        ids = {strain.pk, *range(strain.pk + 1, strain.pk + 1201)}
        with CaptureQueriesContext(connection) as queries:
            counts = _reference_counts(Box, "strain_id", ids)
        self.assertEqual(counts, {strain.pk: 1})
        self.assertEqual(len(queries), 3)
        self.assertTrue(Box.objects.filter(pk=box.pk).exists())

    def test_command_defaults_to_json_on_success(self):
        self.strain()
        stdout = StringIO()
        stderr = StringIO()
        call_command(COMMAND, stdout=stdout, stderr=stderr)
        self.assertEqual(json.loads(stdout.getvalue()), self.report("READY"))
        self.assertEqual(stderr.getvalue(), "")

    def test_command_writes_blocked_json_before_raising_exit_one(self):
        first = self.strain("FIRST")
        self.strain("SECOND", identity=first.global_identity)
        stdout = StringIO()
        with self.assertRaises(CommandError) as caught:
            call_command(COMMAND, stdout=stdout)
        self.assertEqual(caught.exception.returncode, 1)
        self.assertEqual(json.loads(stdout.getvalue()), self.report("BLOCKED"))

    def test_command_writes_failed_read_json_before_raising_exit_two(self):
        stdout = StringIO()
        with patch.object(Strain.objects, "values", side_effect=DatabaseError("private detail")):
            with self.assertRaises(CommandError) as caught:
                call_command(COMMAND, stdout=stdout)
        self.assertEqual(caught.exception.returncode, 2)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["scan_status"], "INCOMPLETE")
        self.assertEqual(payload["readiness"], "INCOMPLETE")
        self.assertFalse(payload["constraint_applicable"])
        self.assertEqual(payload["categories"], [])
        self.assertEqual(payload["errors"], ["SCAN_FAILED"])
        self.assertNotIn("private detail", stdout.getvalue())
