"""Synthetic coverage for the read-only scientific anomaly diagnostic."""

import json
from datetime import date
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.test import TestCase

from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import (
    BiologicalProvenance, LocalStrainIdentity, OrganizationProvenanceCode, OrganizationSpeciesCode,
    Species, Strain,
)

from .historical_2026 import anomaly_diagnostic as diagnostic
from .historical_2026 import decisions
from .historical_2026 import species_correction as correction
from .models import Box


def codes(entry):
    return {b["code"] for b in entry["verdict"]["blockers"]}


class DiagnosticBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Diagnostic laboratory", slug="diag-lab")
        cls.other_org = Organization.objects.create(name="Other laboratory", slug="other-diag-lab")
        cls.old_cac_species = Species.objects.create(scientific_name='Chrysaora chesapeakei "white"')
        cls.old_oce_species = Species.objects.create(scientific_name="Pennaria disticha")
        cls.cac_species = Species.objects.create(scientific_name="Chrysaora achlyos")
        cls.oce_species = Species.objects.create(scientific_name="Oceaniidae sp.")
        cls.old_cac = Strain.objects.create(organization=cls.org, species=cls.old_cac_species, code="CAC-JKA-2")
        cls.old_oce = Strain.objects.create(organization=cls.org, species=cls.old_oce_species, code="OCE-TAI-1")
        cls.cac = Strain.objects.create(organization=cls.org, species=cls.cac_species, code="CAC-JKA-2")
        cls.oce = Strain.objects.create(organization=cls.org, species=cls.oce_species, code="OCE-TAI-1")
        cls.cac_assignment = OrganizationSpeciesCode.objects.create(
            organization=cls.org, species=cls.cac_species, code="CAC")
        cls.oce_assignment = OrganizationSpeciesCode.objects.create(
            organization=cls.org, species=cls.oce_species, code="OCE")
        LocalStrainIdentity.objects.create(strain=cls.cac, species_code_assignment=cls.cac_assignment)
        LocalStrainIdentity.objects.create(strain=cls.oce, species_code_assignment=cls.oce_assignment)
        cls.codes = tuple(f"CAC-JKA-2.{n:03}" for n in range(1, 6)) + ("OCE-TAI-1.002",)
        cls.boxes = {}
        for index, code in enumerate(cls.codes):
            cls.boxes[code] = Box.objects.create(
                organization=cls.org, global_code=code, box_number=code.rsplit(".", 1)[1],
                strain=cls.old_cac if index < 5 else cls.old_oce,
            )

    def run_report(self, org=None, **kwargs):
        report = diagnostic.diagnose((org or self.org).pk, **kwargs)
        return json.loads(json.dumps(report, sort_keys=True, default=str))

    def urgent(self, **kwargs):
        report = self.run_report(mode="urgent", **kwargs)
        return {e["global_code"]: e for e in report["urgent_species"]["entries"]}

    def targets(self):
        return {"cac_strain_id": self.cac.pk, "oce_strain_id": self.oce.pk}

    def database_snapshot(self):
        with connection.cursor() as cursor:
            result = {}
            for table in connection.introspection.table_names(cursor):
                cursor.execute(f"SELECT * FROM {connection.ops.quote_name(table)}")
                result[table] = sorted(cursor.fetchall(), key=repr)
            return result


class UrgentDiagnosticTests(DiagnosticBase):
    def test_all_ready_with_explicit_reviewed_targets(self):
        entries = self.urgent(**self.targets())
        self.assertEqual(set(entries), set(self.codes))
        for entry in entries.values():
            self.assertEqual(entry["verdict"]["correct_urgent_species"], "READY", entry["verdict"])
            self.assertEqual(entry["verdict"]["mechanism_classification"], "correction needed")
            self.assertEqual(entry["verdict"]["blockers"], [])
            self.assertTrue(entry["verdict"]["diagnostic_consistent_with_mechanism"])
            self.assertEqual(entry["persisted"]["box"]["pk"], self.boxes[entry["global_code"]].pk)
            self.assertEqual(entry["persisted"]["current_strain"]["species_name"].split()[0] in
                             ("Chrysaora", "Pennaria"), True)
        self.assertTrue(self.run_report(mode="urgent", **self.targets())["urgent_species"]["summary"]["batch_ready"])

    def test_verdict_matches_the_real_mechanism(self):
        plan = correction.build_plan(self.org.pk, **self.targets())
        entries = self.urgent(**self.targets())
        for planned in plan["entries"]:
            self.assertEqual(entries[planned["global_code"]]["verdict"]["mechanism_classification"],
                             planned["classification"])

    def test_without_target_blocks_and_reports_candidates_only(self):
        entries = self.urgent()
        for code, entry in entries.items():
            self.assertEqual(entry["verdict"]["correct_urgent_species"], "BLOCKED")
            self.assertIn("TARGET_NOT_SUPPLIED", codes(entry))
            self.assertTrue(entry["verdict"]["diagnostic_consistent_with_mechanism"])
            self.assertIsNone(entry["target"]["supplied_strain_id"])
            self.assertTrue(entry["candidate_references"]["candidate_only"])
            candidate = entry["candidate_references"]["exact_reference"]
            self.assertEqual(candidate["id"], (self.cac if code.startswith("CAC") else self.oce).pk)
            hypothetical = entry["readiness_if_candidate_supplied"]
            self.assertTrue(hypothetical["candidate_only"])
            self.assertEqual(hypothetical["correct_urgent_species"], "READY")
        self.assertEqual(self.run_report(mode="urgent")["urgent_species"]["explicit_target_strain_ids"],
                         {"CAC": None, "OCE": None})

    def test_h1_assignment_points_to_another_species(self):
        OrganizationSpeciesCode.objects.filter(pk=self.cac_assignment.pk).update(species=self.old_cac_species)
        other = Strain.objects.create(
            organization=self.org, species=Species.objects.create(scientific_name="Chrysaora lactea"),
            code="CLC-JKA-1")
        Box.objects.create(organization=self.org, global_code="CAC-JKA-1.001", box_number="001", strain=other)
        report = self.run_report(mode="urgent", **self.targets())
        survey = report["urgent_species"]["aaa_code_survey"]["CAC"]
        self.assertTrue(survey["h1_indicator_multiple_species_under_code"])
        self.assertEqual(survey["species_code_assignments"][0]["species_name"], 'Chrysaora chesapeakei "white"')
        entry = {e["global_code"]: e for e in report["urgent_species"]["entries"]}["CAC-JKA-2.001"]
        self.assertEqual(entry["verdict"]["correct_urgent_species"], "BLOCKED")
        self.assertIn("SPECIES_CODE_POINTS_TO_OTHER_SPECIES", codes(entry))
        self.assertIn("TARGET_LOCAL_IDENTITY_INCONSISTENT", codes(entry))
        self.assertTrue(entry["verdict"]["diagnostic_consistent_with_mechanism"])
        self.assertEqual(self.urgent(**self.targets())["OCE-TAI-1.002"]["verdict"]["correct_urgent_species"], "READY")

    def test_absent_target_strain(self):
        LocalStrainIdentity.objects.filter(strain=self.cac).delete()
        missing_pk = self.cac.pk
        self.cac.delete()
        entries = self.urgent(cac_strain_id=missing_pk, oce_strain_id=self.oce.pk)
        entry = entries["CAC-JKA-2.001"]
        self.assertIn("TARGET_UNAVAILABLE_IN_ORGANIZATION", codes(entry))
        self.assertEqual(entry["verdict"]["mechanism_classification"], "ambiguous target/reference")
        self.assertTrue(entry["verdict"]["diagnostic_consistent_with_mechanism"])
        no_target = self.urgent()["CAC-JKA-2.001"]
        self.assertIn("NO_ELIGIBLE_CANDIDATE_TARGET", codes(no_target))
        self.assertIsNone(no_target["candidate_references"]["exact_reference"])
        self.assertNotIn("readiness_if_candidate_supplied", no_target)

    def test_wrong_organization_species_code(self):
        OrganizationSpeciesCode.objects.filter(pk=self.cac_assignment.pk).update(code="ACH")
        entry = self.urgent(**self.targets())["CAC-JKA-2.003"]
        self.assertEqual(entry["verdict"]["correct_urgent_species"], "BLOCKED")
        self.assertTrue({"SPECIES_CODE_ASSIGNMENT_ABSENT", "REQUIRED_SPECIES_HAS_OTHER_CODE"} <= codes(entry))
        self.assertTrue(entry["verdict"]["diagnostic_consistent_with_mechanism"])

    def test_conflicting_local_identity_on_target_and_current(self):
        other_assignment = OrganizationSpeciesCode.objects.create(
            organization=self.org, species=self.old_cac_species, code="CCH")
        LocalStrainIdentity.objects.filter(strain=self.cac).update(species_code_assignment=other_assignment)
        foreign_provenance = OrganizationProvenanceCode.objects.create(
            organization=self.other_org, biological_provenance=BiologicalProvenance.objects.create(name="X"),
            code="ZQY")
        LocalStrainIdentity.objects.create(
            strain=self.old_cac, species_code_assignment=other_assignment,
            provenance_code_assignment=foreign_provenance)
        entry = self.urgent(**self.targets())["CAC-JKA-2.002"]
        self.assertIn("TARGET_LOCAL_IDENTITY_INCONSISTENT", codes(entry))
        self.assertIn("CURRENT_LOCAL_IDENTITY_INCONSISTENT", codes(entry))
        self.assertIn("SPECIES_ASSIGNMENT_SPECIES_MISMATCH", json.dumps(entry["target"]))
        self.assertIn("PROVENANCE_ASSIGNMENT_OUTSIDE_ORGANIZATION", json.dumps(entry["persisted"]))
        self.assertNotIn("ZQY", json.dumps(entry))
        self.assertEqual(entry["verdict"]["correct_urgent_species"], "BLOCKED")
        self.assertTrue(entry["verdict"]["diagnostic_consistent_with_mechanism"])

    def test_already_correct_box(self):
        Box.objects.filter(pk=self.boxes["CAC-JKA-2.004"].pk).update(strain=self.cac)
        entry = self.urgent(**self.targets())["CAC-JKA-2.004"]
        self.assertEqual(entry["verdict"]["correct_urgent_species"], "READY")
        self.assertEqual(entry["verdict"]["mechanism_classification"], "already correct")
        self.assertTrue(entry["persisted"]["current_matches_required_reference"])

    def test_unexpected_current_identity(self):
        stranger = Strain.objects.create(
            organization=self.org, species=Species.objects.create(scientific_name="Unrelated species"),
            code="CAC-JKA-2")
        Box.objects.filter(pk=self.boxes["CAC-JKA-2.005"].pk).update(strain=stranger)
        entry = self.urgent(**self.targets())["CAC-JKA-2.005"]
        self.assertIn("CURRENT_IDENTITY_UNEXPECTED", codes(entry))
        self.assertEqual(entry["verdict"]["correct_urgent_species"], "BLOCKED")

    def test_missing_box_and_box_in_another_organization(self):
        Box.objects.filter(global_code="CAC-JKA-2.001").delete()
        Box.objects.filter(global_code="CAC-JKA-2.002").update(organization=self.other_org)
        entries = self.urgent(**self.targets())
        missing, foreign = entries["CAC-JKA-2.001"], entries["CAC-JKA-2.002"]
        self.assertEqual(codes(missing), {"BOX_MISSING"})
        self.assertFalse(missing["persisted"]["box"]["exists"])
        # A Box of another institution is indistinguishable from an absent Box.
        self.assertEqual(codes(foreign), {"BOX_MISSING"})
        self.assertEqual(foreign["persisted"]["box"], {**missing["persisted"]["box"],
                                                       "global_code": "CAC-JKA-2.002"})
        self.assertNotIn("exists_in_another_organization", json.dumps(foreign))
        for entry in (missing, foreign):
            self.assertEqual(entry["verdict"]["correct_urgent_species"], "BLOCKED")
            self.assertTrue(entry["verdict"]["diagnostic_consistent_with_mechanism"])
        self.assertFalse(self.run_report(mode="urgent", **self.targets())["urgent_species"]["summary"]["batch_ready"])

    def test_institution_isolation(self):
        foreign_species = Species.objects.create(scientific_name="Foreign species")
        foreign = Strain.objects.create(organization=self.other_org, species=foreign_species, code="CAC-JKA-2")
        Box.objects.create(organization=self.other_org, global_code="CAC-JKA-2.900", box_number="900",
                           strain=foreign)
        entries = self.urgent(**self.targets())
        entry = entries["CAC-JKA-2.001"]
        self.assertNotIn(foreign.pk, [s["id"] for s in entry["candidate_references"]["same_code_other_species"]])
        survey = self.run_report(mode="urgent")["urgent_species"]["aaa_code_survey"]["CAC"]
        self.assertEqual(sum(g["box_count"] for g in survey["box_groups"]), 5)
        explicit = self.urgent(cac_strain_id=foreign.pk, oce_strain_id=self.oce.pk)["CAC-JKA-2.001"]
        self.assertIn("TARGET_UNAVAILABLE_IN_ORGANIZATION", codes(explicit))
        self.assertNotIn("strain", explicit["target"])
        self.assertEqual(explicit["verdict"]["correct_urgent_species"], "BLOCKED")
        other_view = self.urgent_for(self.other_org)
        for code in self.codes[:5]:
            self.assertIn("BOX_MISSING", codes(other_view[code]))
        self.assertNotIn("pk", other_view["CAC-JKA-2.001"]["persisted"]["box"])
        self.assertEqual(other_view["CAC-JKA-2.001"]["candidate_references"]["same_code_other_species"][0]["id"],
                         foreign.pk)

    def urgent_for(self, org):
        report = self.run_report(org=org, mode="urgent")
        return {e["global_code"]: e for e in report["urgent_species"]["entries"]}

    def test_unknown_organization_is_not_guessed(self):
        with self.assertRaises(LookupError):
            diagnostic.diagnose(999999)


class HsDiagnosticTests(DiagnosticBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.hs_strain = Strain.objects.create(
            organization=cls.org, species=Species.objects.create(scientific_name="Hs species"), code="HSS-AAA-1")

    def make(self, code, status=Box.Status.ACTIVE):
        return Box.objects.create(organization=self.org, global_code=code, box_number="1",
                                  strain=self.hs_strain, status=status)

    def hs(self, **kwargs):
        report = self.run_report(mode="hs", **kwargs)
        return {e["listed_code"]: e for e in report["hs_deactivation"]["entries"]}, report

    def test_all_35_literal_codes_in_order(self):
        entries, report = self.hs()
        self.assertEqual(report["hs_deactivation"]["listed_code_count"], 35)
        self.assertEqual([e["listed_code"] for e in report["hs_deactivation"]["entries"]],
                         list(decisions.HS_BOXES))
        self.assertEqual(report["hs_deactivation"]["approved_remaps"], decisions.HS_CODE_CORRECTIONS)
        self.assertEqual(entries["AAL-FGU-1.002"]["deactivation_plan_status"], "NOT_FOUND")
        self.assertEqual(entries["AAL-FGU-1.002"]["deactivation_safety"], "NOT_APPLICABLE")
        self.assertEqual(entries["CTU-CFC-2.007"]["deactivation_plan_status"], "PENDING_CONFIRMATION")

    def test_remapped_codes_resolve_to_active_boxes_and_are_unsafe(self):
        ato, pca = self.make("ATO-JIS-1.001"), self.make("PCA-JKA-1.005")
        entries, _ = self.hs()
        for listed, resolved, box in (("COR-JIS-1.001", "ATO-JIS-1.001", ato),
                                      ("PCA-CHO-1.005", "PCA-JKA-1.005", pca)):
            entry = entries[listed]
            self.assertTrue(entry["approved_remap"])
            self.assertEqual(entry["resolved_code"], resolved)
            self.assertEqual(entry["deactivation_plan_status"], "READY")
            self.assertEqual(entry["resolved_box"]["pk"], box.pk)
            self.assertEqual(entry["resolved_box"]["status"], "active")
            self.assertEqual(entry["deactivation_safety"], "UNSAFE")
            self.assertEqual(entry["source_evidence"]["origin"], "v2_workbook_static")
            self.assertIn("V2_ROW_FOR_LISTED_IDENTITY", {w["code"] for w in entry["warnings"]})
            self.assertTrue(entry["diagnostic_only"])
        evidence = entries["PCA-CHO-1.005"]["source_evidence"]
        self.assertEqual(evidence["v2_literal_code"], "PCA-JKA-1.05")
        self.assertEqual(evidence["v2_canonical_code"], "PCA-JKA-1.005")
        self.assertEqual(evidence["merged_range"], "Semaestomeae!B149:B152")
        self.assertEqual(evidence["blocks"], [
            {"temperature_c": 5, "rows": "149-150", "first_week": 1, "last_week": 2},
            {"temperature_c": 10, "rows": "151-152", "first_week": 3, "last_week": 40},
        ])
        self.assertEqual(evidence["last_week_with_value"], 40)
        self.assertEqual(evidence["last_iso_week"], "2026-W40")
        self.assertNotIn("recent", evidence["note"])
        self.assertNotIn("Only weeks 1 and 2", json.dumps(evidence))
        self.assertIn("Source observations only", evidence["note"])
        self.assertNotIn("status", evidence)  # source evidence never claims a Box status
        self.assertIn("persisted", evidence["note"])

    def test_labels_with_recent_v2_observations_are_unsafe_even_when_ready(self):
        entries, _ = self.hs()
        for listed in ("CAC-JKA-2.004", "OCE-TAI-1.002"):
            self.assertEqual(entries[listed]["deactivation_plan_status"], "READY")
            self.assertEqual(entries[listed]["deactivation_safety"], "UNSAFE")
            self.assertTrue(entries[listed]["in_urgent_species_set"])
        self.assertEqual(entries["CAC-JKA-2.004"]["source_evidence"]["last_week_with_value"], 41)

    def test_unapproved_candidate_remap_is_only_a_candidate(self):
        self.make("CLC-JKA-1.009")
        entries, _ = self.hs()
        entry = entries["CLA-JKA-1.009"]
        self.assertFalse(entry["approved_remap"])
        self.assertEqual(entry["resolved_code"], "CLA-JKA-1.009")
        self.assertEqual(entry["deactivation_plan_status"], "NOT_FOUND")
        self.assertEqual(entry["deactivation_safety"], "NOT_APPLICABLE")
        candidate = entry["candidate_remap"]
        self.assertTrue(candidate["candidate_only"])
        self.assertFalse(candidate["approved"])
        self.assertEqual(candidate["code"], "CLC-JKA-1.009")
        self.assertTrue(candidate["persisted_box"]["exists"])
        self.assertEqual(entry["source_evidence"]["v2_literal_code"], "CLC-JKA-1.09")

    def test_ready_label_without_contradiction_is_unverified_never_safe(self):
        self.make("AAL-FGU-1.002")
        entries, report = self.hs()
        entry = entries["AAL-FGU-1.002"]
        self.assertEqual(entry["deactivation_plan_status"], "READY")
        self.assertEqual(entry["deactivation_safety"], "UNVERIFIED")
        self.assertIsNone(entry["source_evidence"])
        self.assertNotIn("SAFE", set(report["hs_deactivation"]["summary"]["deactivation_safety"]))

    def test_inactive_and_non_active_statuses_are_not_applicable(self):
        self.make("AFL-TAI-1.002", Box.Status.INACTIVE)
        self.make("AVA-TAI-1.001", Box.Status.PENDING_REVIEW)
        entries, _ = self.hs()
        self.assertEqual(entries["AFL-TAI-1.002"]["deactivation_plan_status"], "ALREADY_INACTIVE")
        self.assertEqual(entries["AVA-TAI-1.001"]["deactivation_plan_status"], "NOT_ACTIVE")
        for code in ("AFL-TAI-1.002", "AVA-TAI-1.001"):
            self.assertEqual(entries[code]["deactivation_safety"], "NOT_APPLICABLE")

    def test_latest_measurement_keeps_zero_distinct_from_absence(self):
        zero_box, empty_box = self.make("EMA-FGU-1.001"), self.make("LAO-JKA-1.002")
        BiologicalMeasurement.objects.create(box=zero_box, measured_on=date(2026, 3, 2), polyp_count=5,
                                             ephyrae_count=1, strobila_count=None)
        BiologicalMeasurement.objects.create(box=zero_box, measured_on=date(2026, 9, 14), polyp_count=0,
                                             ephyrae_count=0, strobila_count=0)
        entries, _ = self.hs()
        zero = entries["EMA-FGU-1.001"]["listed_box"]["measurements"]
        self.assertEqual(zero["count"], 2)
        self.assertEqual(zero["latest"]["measured_on"], "2026-09-14")
        self.assertEqual(zero["latest"]["iso_week"], 38)
        self.assertEqual((zero["latest"]["polyp_count"], zero["latest"]["ephyrae_count"],
                          zero["latest"]["strobila_count"]), (0, 0, 0))
        BiologicalMeasurement.objects.filter(box=zero_box, measured_on=date(2026, 9, 14)).update(strobila_count=None)
        entries, _ = self.hs()
        self.assertIsNone(entries["EMA-FGU-1.001"]["listed_box"]["measurements"]["latest"]["strobila_count"])
        empty = entries["LAO-JKA-1.002"]["listed_box"]["measurements"]
        self.assertEqual((empty["count"], empty["latest"]), (0, None))
        self.assertEqual(empty_box.pk, entries["LAO-JKA-1.002"]["listed_box"]["pk"])

    def test_other_institution_box_is_not_reported(self):
        Box.objects.create(organization=self.other_org, global_code="PYU-CNI-1.001", box_number="1",
                           strain=Strain.objects.create(
                               organization=self.other_org, species=Species.objects.create(scientific_name="Z"),
                               code="Z-1"))
        entries, report = self.hs()
        self.assertEqual(entries["PYU-CNI-1.001"]["deactivation_plan_status"], "NOT_FOUND")
        self.assertFalse(entries["PYU-CNI-1.001"]["listed_box"]["exists"])


class SafetyAndCommandTests(DiagnosticBase):
    def test_no_database_mutation_in_any_mode(self):
        for code in ("AAL-FGU-1.002", "ATO-JIS-1.001"):
            Box.objects.create(organization=self.org, global_code=code, box_number="1", strain=self.cac)
        BiologicalMeasurement.objects.create(box=self.boxes["CAC-JKA-2.001"], measured_on=date(2026, 9, 7),
                                             polyp_count=0, ephyrae_count=0, strobila_count=0)
        before, audits = self.database_snapshot(), AuditLog.objects.count()
        for mode in diagnostic.MODES:
            self.run_report(mode=mode, **self.targets())
            self.run_report(mode=mode)
            call_command("diagnose_scientific_anomalies", organization_id=self.org.pk, mode=mode,
                         stdout=StringIO(), stderr=StringIO())
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(AuditLog.objects.count(), audits)

    def test_mutation_services_are_never_called(self):
        with patch("apps.cultures.services.deactivate_box", side_effect=AssertionError("lifecycle")), \
                patch("apps.cultures.historical_2026.deactivation.deactivate_box", side_effect=AssertionError("x")), \
                patch.object(correction, "apply_plan", side_effect=AssertionError("apply")):
            self.run_report(**self.targets())

    def test_write_and_lock_statements_are_refused(self):
        for sql in ("UPDATE cultures_box SET status = 'inactive'", "  delete from cultures_box",
                    "INSERT INTO x VALUES (1)", "SELECT * FROM cultures_box FOR UPDATE",
                    "SELECT 1 FROM t FOR NO KEY UPDATE"):
            with self.assertRaises(diagnostic.WriteAttempt):
                diagnostic._refuse_writes(lambda *a: None, sql, (), False, {})
        diagnostic._refuse_writes(lambda *a: "ok", "SELECT * FROM cultures_box WHERE status = %s", ("a",), False, {})
        with connection.execute_wrapper(diagnostic._refuse_writes):
            with self.assertRaises(diagnostic.WriteAttempt), transaction.atomic():
                Box.objects.filter(pk=self.boxes["CAC-JKA-2.001"].pk).update(notes="changed")
        self.assertEqual(Box.objects.get(pk=self.boxes["CAC-JKA-2.001"].pk).notes, "")

    def test_output_is_deterministic_and_source_is_fingerprinted(self):
        first, second = self.run_report(**self.targets()), self.run_report(**self.targets())
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertEqual(first["report_sha256"], diagnostic.fingerprint(first))
        self.assertEqual(first["source"]["v2_workbook_sha256"],
                         "0d5cd16eee88845ea51fbd4f122be15c12e5896b5237458573fc432ebda8b160")
        self.assertFalse(first["source"]["workbook_required_at_runtime"])
        self.assertTrue(first["read_only"])
        self.assertNotIn("password", json.dumps(first).lower())

    def test_command_outputs_json_and_summary_separately(self):
        out, err = StringIO(), StringIO()
        call_command("diagnose_scientific_anomalies", organization_id=self.org.pk, cac_strain_id=self.cac.pk,
                     oce_strain_id=self.oce.pk, stdout=out, stderr=err)
        report = json.loads(out.getvalue())
        self.assertEqual(report["mode"], "all")
        self.assertIn("6 READY", err.getvalue())
        summary = StringIO()
        call_command("diagnose_scientific_anomalies", organization_id=self.org.pk, mode="hs",
                     summary_only=True, stdout=summary)
        self.assertIn("HS labels (35)", summary.getvalue())
        self.assertNotIn("{", summary.getvalue())

    def test_command_requires_an_explicit_existing_organization(self):
        with self.assertRaises(CommandError):
            call_command("diagnose_scientific_anomalies", stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("diagnose_scientific_anomalies", organization_id=999999, stdout=StringIO())


class CrossTenantIsolationTests(DiagnosticBase):
    FORBIDDEN = ("Foreign Secret Species", "FOR-ZZZ-9", "ZQX", "ZQY", "Foreign secret source")

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.foreign_species = Species.objects.create(scientific_name="Foreign Secret Species")
        cls.foreign = Strain.objects.create(organization=cls.other_org, species=cls.foreign_species,
                                            code="FOR-ZZZ-9")
        cls.foreign_assignment = OrganizationSpeciesCode.objects.create(
            organization=cls.other_org, species=cls.foreign_species, code="ZQX")
        cls.foreign_provenance = OrganizationProvenanceCode.objects.create(
            organization=cls.other_org, code="ZQY",
            biological_provenance=BiologicalProvenance.objects.create(name="Foreign secret source"))
        LocalStrainIdentity.objects.create(
            strain=cls.foreign, species_code_assignment=cls.foreign_assignment,
            provenance_code_assignment=cls.foreign_provenance)
        cls.foreign_box = Box.objects.create(
            organization=cls.other_org, global_code="FOR-ZZZ-9.001", box_number="001", strain=cls.foreign)

    def assert_clean(self, text):
        for forbidden in self.FORBIDDEN:
            self.assertNotIn(forbidden, text)

    def everything(self, **kwargs):
        """JSON report plus the terminal summary, the only two outputs of the command."""
        report = self.run_report(**kwargs)
        return report, json.dumps(report, sort_keys=True) + diagnostic.render_summary(report)

    def comparable(self, report, supplied):
        """Drop the operator-supplied echo so two reports can be compared structurally."""
        body = json.loads(json.dumps(report))
        body.pop("report_sha256")
        urgent = body["urgent_species"]
        urgent.pop("explicit_target_strain_ids")
        for entry in urgent["entries"]:
            self.assertEqual(entry["target"].pop("supplied_strain_id"), supplied)
        return body

    def test_foreign_target_pk_exposes_no_foreign_attribute(self):
        report, text = self.everything(mode="urgent", cac_strain_id=self.foreign.pk, oce_strain_id=self.oce.pk)
        self.assert_clean(text)
        self.assertNotIn(f"Strain {self.foreign.pk}", text)
        for entry in report["urgent_species"]["entries"]:
            if not entry["global_code"].startswith("CAC"):
                continue
            self.assertEqual(entry["target"], {"supplied_strain_id": self.foreign.pk,
                                               "availability": "UNAVAILABLE_IN_ORGANIZATION"})
            self.assertEqual(entry["verdict"]["correct_urgent_species"], "BLOCKED")
            self.assertIn("TARGET_UNAVAILABLE_IN_ORGANIZATION", codes(entry))
            self.assertTrue(entry["verdict"]["diagnostic_consistent_with_mechanism"])

    def test_foreign_and_nonexistent_target_pks_are_indistinguishable(self):
        missing_pk = Strain.objects.order_by("-pk").first().pk + 1000
        foreign = self.run_report(mode="urgent", cac_strain_id=self.foreign.pk, oce_strain_id=self.foreign.pk)
        absent = self.run_report(mode="urgent", cac_strain_id=missing_pk, oce_strain_id=missing_pk)
        self.assertEqual(self.comparable(foreign, self.foreign.pk), self.comparable(absent, missing_pk))

    def test_unavailable_target_matches_planner_with_no_target(self):
        """READY/BLOCKED stays that of the real planner, which receives no foreign PK."""
        mechanism = correction.build_plan(self.org.pk, cac_strain_id=None, oce_strain_id=self.oce.pk)
        entries = self.urgent(cac_strain_id=self.foreign.pk, oce_strain_id=self.oce.pk)
        for planned in mechanism["entries"]:
            entry = entries[planned["global_code"]]
            self.assertEqual(entry["verdict"]["mechanism_classification"], planned["classification"])
            self.assertEqual(entry["verdict"]["correct_urgent_species"] == "READY",
                             planned["classification"] in {"already correct", "correction needed"})

    def test_foreign_box_is_not_probed(self):
        Box.objects.filter(global_code="CAC-JKA-2.003").update(organization=self.other_org)
        report, text = self.everything(mode="all", **self.targets())
        entry = {e["global_code"]: e for e in report["urgent_species"]["entries"]}["CAC-JKA-2.003"]
        self.assertEqual(codes(entry), {"BOX_MISSING"})
        for forbidden in ("exists_in_another_organization", "BOX_IN_OTHER_ORGANIZATION", "other organization"):
            self.assertNotIn(forbidden, text)
        hs = {e["listed_code"]: e for e in report["hs_deactivation"]["entries"]}["CAC-JKA-2.003"]
        self.assertEqual(hs["deactivation_plan_status"], "NOT_FOUND")
        self.assertEqual(hs["listed_box"], {"exists": False})

    def test_box_pointing_at_a_foreign_strain_describes_nothing(self):
        Box.objects.filter(global_code="CAC-JKA-2.001").update(strain=self.foreign)
        report, text = self.everything(mode="urgent", **self.targets())
        self.assert_clean(text)
        self.assertNotIn(f"Strain {self.foreign.pk}", text)
        entry = {e["global_code"]: e for e in report["urgent_species"]["entries"]}["CAC-JKA-2.001"]
        self.assertEqual(entry["persisted"]["current_strain"], {"available_in_organization": False})
        self.assertIn("CURRENT_STRAIN_UNAVAILABLE_IN_ORGANIZATION", codes(entry))
        self.assertEqual(entry["verdict"]["correct_urgent_species"], "BLOCKED")
        self.assertTrue(entry["verdict"]["diagnostic_consistent_with_mechanism"])
        survey = report["urgent_species"]["aaa_code_survey"]["CAC"]
        self.assertIn({"available_in_organization": False, "box_count": 1}, survey["box_groups"])

    def test_shared_legacy_strain_with_foreign_identity_keeps_value_without_leak(self):
        legacy_species = Species.objects.create(scientific_name="Legacy shared species")
        legacy = Strain.objects.create(organization=None, species=legacy_species, code="CAC-JKA-2")
        Box.objects.create(organization=self.org, global_code="CAC-JKA-2.050", box_number="050", strain=legacy)
        LocalStrainIdentity.objects.create(
            strain=legacy, species_code_assignment=self.foreign_assignment,
            provenance_code_assignment=self.foreign_provenance)
        report, text = self.everything(mode="urgent", cac_strain_id=legacy.pk, oce_strain_id=self.oce.pk)
        self.assert_clean(text)
        entry = {e["global_code"]: e for e in report["urgent_species"]["entries"]}["CAC-JKA-2.001"]
        self.assertEqual(entry["target"]["availability"], "AVAILABLE")
        self.assertEqual(entry["target"]["strain"]["ownership"], "unowned_legacy")
        identity = entry["target"]["strain"]["local_identity"]
        self.assertEqual(identity["species_code_assignment"], {"available_in_organization": False})
        self.assertEqual(identity["provenance_code_assignment"], {"available_in_organization": False})
        self.assertIn("SPECIES_ASSIGNMENT_OUTSIDE_ORGANIZATION", identity["problems"])
        self.assertEqual(entry["verdict"]["correct_urgent_species"], "BLOCKED")
        candidates = {s["id"] for s in entry["candidate_references"]["same_code_other_species"]}
        self.assertIn(legacy.pk, candidates)

    def test_foreign_exact_reference_is_not_a_candidate_nor_a_probe(self):
        """A foreign Strain with the exact reviewed species and code must not be hinted at."""
        LocalStrainIdentity.objects.filter(strain=self.cac).delete()
        Strain.objects.filter(pk=self.cac.pk).update(organization=self.other_org)
        report, text = self.everything(mode="urgent")
        entry = {e["global_code"]: e for e in report["urgent_species"]["entries"]}["CAC-JKA-2.001"]
        self.assertIsNone(entry["candidate_references"]["exact_reference"])
        self.assertNotIn("exists_but_not_eligible", text)
        self.assertIn("NO_ELIGIBLE_CANDIDATE_TARGET", codes(entry))
        self.assertNotIn("readiness_if_candidate_supplied", entry)

    def test_same_organization_and_shared_references_remain_supported(self):
        entry = self.urgent(**self.targets())["CAC-JKA-2.001"]
        self.assertEqual(entry["target"]["availability"], "AVAILABLE")
        self.assertEqual(entry["target"]["strain"]["id"], self.cac.pk)
        self.assertEqual(entry["verdict"]["correct_urgent_species"], "READY")
        legacy_species = Species.objects.create(scientific_name="Another legacy species")
        legacy = Strain.objects.create(organization=None, species=legacy_species, code="LEG-1")
        Box.objects.create(organization=self.org, global_code="LEG-1.001", box_number="001", strain=legacy)
        shared = self.urgent(cac_strain_id=legacy.pk, oce_strain_id=self.oce.pk)["CAC-JKA-2.001"]
        self.assertEqual(shared["target"]["availability"], "AVAILABLE")
        self.assertNotIn("TARGET_UNAVAILABLE_IN_ORGANIZATION", codes(shared))
        unused_legacy = Strain.objects.create(organization=None, species=legacy_species, code="LEG-2")
        unused = self.urgent(cac_strain_id=unused_legacy.pk, oce_strain_id=self.oce.pk)["CAC-JKA-2.001"]
        self.assertEqual(unused["target"]["availability"], "UNAVAILABLE_IN_ORGANIZATION")

    def test_command_output_and_summary_never_leak(self):
        out, err = StringIO(), StringIO()
        call_command("diagnose_scientific_anomalies", organization_id=self.org.pk,
                     cac_strain_id=self.foreign.pk, oce_strain_id=self.foreign.pk, stdout=out, stderr=err)
        self.assert_clean(out.getvalue() + err.getvalue())
        self.assertNotIn(f"Strain {self.foreign.pk}", out.getvalue())

    def test_hs_mode_stays_scoped_and_conservative(self):
        Box.objects.create(organization=self.other_org, global_code="AAL-FGU-1.002", box_number="1",
                           strain=self.foreign)
        report, text = self.everything(mode="hs")
        self.assert_clean(text)
        entry = {e["listed_code"]: e for e in report["hs_deactivation"]["entries"]}["AAL-FGU-1.002"]
        self.assertEqual((entry["deactivation_plan_status"], entry["deactivation_safety"]),
                         ("NOT_FOUND", "NOT_APPLICABLE"))
        self.assertNotIn("SAFE", set(report["hs_deactivation"]["summary"]["deactivation_safety"]))

    def test_diagnostics_with_foreign_inputs_write_nothing(self):
        before, audits = self.database_snapshot(), AuditLog.objects.count()
        self.run_report(cac_strain_id=self.foreign.pk, oce_strain_id=self.foreign.pk)
        call_command("diagnose_scientific_anomalies", organization_id=self.org.pk,
                     cac_strain_id=self.foreign.pk, stdout=StringIO(), stderr=StringIO())
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(AuditLog.objects.count(), audits)


class SourceEvidenceTests(DiagnosticBase):
    def test_static_evidence_is_internally_consistent(self):
        for listed, item in diagnostic.V2_KNOWN_CONTRADICTIONS.items():
            self.assertEqual(item["last_week"], max(b["last_week"] for b in item["blocks"]), listed)
            first, last = item["merged_range"].split("!")[1].split(":")
            self.assertEqual(first, item["cell"].split("!")[1], listed)
            self.assertTrue(last.startswith("B"), listed)

    def test_last_source_weeks_match_independently_verified_workbook_values(self):
        """Literal values read from the V2 week headers; deliberately not derived from the constants.

        OCE-TAI-1.02 ends at Hydrozoa!AT173:AT174 (header AT2 = week 40, AU is empty), not week 41.
        """
        verified = {"CAC-JKA-2.004": 41, "COR-JIS-1.001": 41, "CLA-JKA-1.009": 40,
                    "OCE-TAI-1.002": 40, "PCA-CHO-1.005": 40}
        report = self.run_report(mode="hs")
        entries = {e["listed_code"]: e for e in report["hs_deactivation"]["entries"]}
        for listed, week in verified.items():
            evidence = entries[listed]["source_evidence"]
            self.assertEqual(evidence["last_week_with_value"], week, listed)
            self.assertEqual(evidence["last_iso_week"], f"2026-W{week}", listed)
            self.assertEqual(max(b["last_week"] for b in evidence["blocks"]), week, listed)
        oce = entries["OCE-TAI-1.002"]["source_evidence"]
        self.assertEqual(oce["blocks"], [{"temperature_c": 25, "rows": "173-174", "first_week": 1, "last_week": 40}])
        self.assertNotIn("W41", json.dumps(oce))

    def test_oce_source_week_stays_separate_from_persisted_measurements(self):
        box = self.boxes["OCE-TAI-1.002"]
        BiologicalMeasurement.objects.create(box=box, measured_on=date(2026, 10, 5), polyp_count=0,
                                             ephyrae_count=0, strobila_count=0)  # ISO week 41, persisted
        before = self.database_snapshot()
        entry = {e["listed_code"]: e for e in
                 self.run_report(mode="hs")["hs_deactivation"]["entries"]}["OCE-TAI-1.002"]
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(entry["source_evidence"]["last_iso_week"], "2026-W40")
        latest = entry["listed_box"]["measurements"]["latest"]
        self.assertEqual((latest["iso_week"], latest["polyp_count"], latest["strobila_count"]), (41, 0, 0))
        self.assertEqual(entry["deactivation_plan_status"], "READY")
        self.assertEqual(entry["deactivation_safety"], "UNSAFE")

    def test_pca_source_reaches_w40_while_persisted_measurements_stay_separate(self):
        strain = Strain.objects.create(
            organization=self.org, species=Species.objects.create(scientific_name="Pca species"),
            code="PCA-JKA-1")
        box = Box.objects.create(organization=self.org, global_code="PCA-JKA-1.005", box_number="005",
                                 strain=strain, status=Box.Status.ACTIVE)
        for measured_on, polyps in ((date(2026, 1, 5), 1), (date(2026, 1, 12), 0)):
            BiologicalMeasurement.objects.create(box=box, measured_on=measured_on, polyp_count=polyps,
                                                 ephyrae_count=0, strobila_count=0)
        before = self.database_snapshot()
        report = self.run_report(mode="hs")
        self.assertEqual(self.database_snapshot(), before)
        entry = {e["listed_code"]: e for e in report["hs_deactivation"]["entries"]}["PCA-CHO-1.005"]
        self.assertEqual(entry["source_evidence"]["last_iso_week"], "2026-W40")
        latest = entry["resolved_box"]["measurements"]["latest"]
        self.assertEqual((latest["iso_week"], latest["polyp_count"], latest["strobila_count"]), (3, 0, 0))
        self.assertEqual(entry["resolved_box"]["status"], "active")  # persisted state, never altered
        self.assertEqual(entry["deactivation_safety"], "UNSAFE")
        self.assertEqual(Box.objects.get(pk=box.pk).status, Box.Status.ACTIVE)
        for warning in entry["warnings"]:
            self.assertNotIn("eligible", warning["message"])
