"""Standalone unittest coverage: no Django setup, settings or database required."""

import hashlib
import itertools
import json
import subprocess
import sys
import unittest
from copy import deepcopy
from pathlib import Path

from .historical_temperature_plan import plan


BOX = "ABC-DEF-1.001"


def evidence(**changes):
    if ("polyp_cell" in changes and "temperature_cell" not in changes
            and changes["polyp_cell"].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ").isdecimal()):
        row = int(changes["polyp_cell"].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
        changes = {"temperature_cell": f"C{row}", "temperature_merged_range": f"C{row}:C{row+1}", **changes}
    return {
        "source_file": "synthetic.xlsx", "source_sha256": "a" * 64,
        "sheet": "Synthetic", "temperature_cell": "C3", "temperature_merged_range": "C3:C4",
        "source_value": "15.000", "source_box": "ABC-DEF-1.01",
        "polyp_cell": "F3", "polyp_value": "0", "ephyrae_cell": "F4", "ephyrae_value": "0",
        "week_cell": "F2", "week_value": "1", "source_iso_year": 2026, "source_iso_week": 1,
        **changes,
    }


def candidate(*, source=None, **changes):
    source = evidence() if source is None else source
    digest = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
    return {
        "candidate_id": digest, "iso_year": 2026, "iso_week": 1,
        "source_box": source["source_box"], "canonical_box": BOX,
        "temperature_c": source["source_value"], "biological_state": "complete",
        "polyps": 0, "ephyrae": 0, "evidence": source, **changes,
    }


def baseline():
    return {
        "organization_id": 7,
        "boxes": [{"id": 11, "organization_id": 7, "global_code": BOX}],
        "biological_measurements": [{
            "id": 21, "box_id": 11, "week_start": "2025-12-29", "polyp_count": 0, "ephyrae_count": 0,
        }],
        "historical_temperatures": [],
    }


def accepted_baseline():
    result = baseline()
    result["historical_temperatures"] = [{
        "id": 31, "biological_measurement_id": 21, "iso_year": 2026,
        "iso_week": 1, "temperature_c": "15.000", "evidences": [evidence()],
    }]
    return result


def apply_to_snapshot(snapshot, proposals):
    """Simulate only the documented proposal schema, without importing storage."""
    snapshot = deepcopy(snapshot)
    for change in proposals:
        payload = deepcopy(change["payload"])
        if change["operation"] == "CREATE_READING":
            payload["id"] = 100 + len(snapshot["historical_temperatures"])
            snapshot["historical_temperatures"].append(payload)
        elif change["operation"] == "ADD_EVIDENCE":
            reading = next(row for row in snapshot["historical_temperatures"]
                           if row["biological_measurement_id"] == payload["biological_measurement_id"])
            reading["evidences"].extend(payload["evidences"])
        else:
            raise AssertionError(change["operation"])
    return snapshot


class HistoricalTemperaturePlanTests(unittest.TestCase):
    def test_pair_may_be_covered_by_larger_merge_but_not_outside_it(self):
        row = candidate(source=evidence(polyp_cell="F7", ephyrae_cell="F8", temperature_cell="C3",
                                        temperature_merged_range="C3:C8"))
        self.assert_status(self.run_plan([row]), "CREATE_READING")
        invalid = candidate(source=evidence(polyp_cell="F7", ephyrae_cell="F8", temperature_cell="C3",
                                            temperature_merged_range=""))
        self.assert_status(self.run_plan([invalid]), "BLOCKED", "EVIDENCE_OUTSIDE_TEMPERATURE_BLOCK")

    def test_source_identity_blocker_blocks_even_when_baseline_counts_match(self):
        row = candidate()
        result = plan([row], organization_id=7, baseline=baseline(), source_blockers={
            row["candidate_id"]: ["V2_IDENTITY_DIFFERS_REVIEW_REQUIRED"],
        })
        self.assert_status(result, "BLOCKED", "V2_IDENTITY_DIFFERS_REVIEW_REQUIRED")
        self.assertFalse(result["summary"]["ready"])

    def test_malformed_competing_occurrence_blocks_both_identity_claims(self):
        first = candidate()
        second = candidate(source=evidence(source_box="ABC-DEF-1.02", ephyrae_value=""),
                           canonical_box="ABC-DEF-1.002", ephyrae=None, biological_state="partial")
        result = plan([first, second], organization_id=7, baseline=baseline())
        self.assertEqual(result["proposed_changes"], [])
        for association in result["associations"]:
            self.assertIn("SOURCE_OCCURRENCE_CONFLICT", association["issues"])

    def test_contradictory_literal_week_or_coordinates_block_matching_counts(self):
        for changes in ({"week_value": "2"}, {"week_cell": "G2"},
                        {"ephyrae_cell": "G4"}, {"ephyrae_cell": "F5"},
                                                {"polyp_cell": "F7", "ephyrae_cell": "F8", "temperature_cell": "C3",
                                                 "temperature_merged_range": "C3:C4"}):
            with self.subTest(changes=changes):
                result = self.run_plan([candidate(source=evidence(**changes))])
                self.assert_status(result, "BLOCKED")
    def run_plan(self, candidates=None, snapshot=None):
        return plan([candidate()] if candidates is None else candidates, organization_id=7,
                    baseline=baseline() if snapshot is None else snapshot)

    def assert_status(self, result, status, issue=None):
        self.assertEqual([row["status"] for row in result["associations"]], [status])
        if issue:
            self.assertIn(issue, result["associations"][0]["issues"])
        if status not in {"CREATE_READING", "ADD_EVIDENCE"}:
            self.assertEqual(result["proposed_changes"], [])

    def test_zero_counts_resolve_existing_biology_without_creating_biology(self):
        result = self.run_plan()
        self.assert_status(result, "CREATE_READING")
        self.assertTrue(result["summary"]["ready"])
        self.assertEqual(result["proposed_changes"], [{"operation": "CREATE_READING", "payload": {
            "biological_measurement_id": 21, "iso_year": 2026, "iso_week": 1,
            "temperature_c": "15.000", "evidences": [evidence()],
        }}])
        self.assertEqual(result["associations"][0]["baseline_counts"], {"polyps": 0, "ephyrae": 0})

    def test_missing_baseline_is_not_an_empty_database(self):
        result = plan([candidate()], organization_id=7)
        self.assertEqual(result["baseline_status"], "BASELINE_UNAVAILABLE")
        self.assertFalse(result["summary"]["ready"])
        self.assert_status(result, "BASELINE_UNAVAILABLE")

    def test_conflicts_and_duplicates_are_analyzed_without_baseline(self):
        first = candidate()
        other = candidate(source=evidence(polyp_cell="F7", ephyrae_cell="F8", source_value="20"), polyps=100)
        result = plan([first, deepcopy(first), other], organization_id=7)
        self.assert_status(result, "CONFLICT", "SOURCE_TEMPERATURE_CONFLICT")
        self.assertIn("SOURCE_BIOLOGY_CONFLICT", result["associations"][0]["issues"])
        self.assertEqual(result["summary"]["duplicate_source_occurrences"], 1)
        self.assertEqual(result["associations"][0]["source_occurrence_count"], 3)

    def test_zero_temperature_is_not_missing_and_exact_precision_is_preserved(self):
        for text in ("0", "0.000", "-12.123456789012345678901234567890", "1000.12345"):
            with self.subTest(text=text):
                result = self.run_plan([candidate(source=evidence(source_value=text))])
                self.assert_status(result, "CREATE_READING")
                self.assertEqual(result["proposed_changes"][0]["payload"]["temperature_c"], text)

    def test_agreeing_spellings_and_distinct_evidence_coalesce_without_duplicate_rows(self):
        first = candidate()
        second = candidate(source=evidence(polyp_cell="F7", ephyrae_cell="F8", source_value="1.5e1"))
        result = self.run_plan([first, deepcopy(first), second])
        self.assert_status(result, "CREATE_READING")
        self.assertEqual(len(result["proposed_changes"]), 1)
        payload = result["proposed_changes"][0]["payload"]
        self.assertEqual(payload["temperature_c"], "15.000")
        self.assertEqual(len(payload["evidences"]), 2)
        self.assertEqual({row["source_value"] for row in payload["evidences"]}, {"15.000", "1.5e1"})
        self.assertEqual(result["associations"][0]["duplicate_source_occurrences"], 1)

    def test_same_temperature_different_biology_blocks_instead_of_choosing_matching_row(self):
        second = candidate(source=evidence(polyp_cell="F7", ephyrae_cell="F8", polyp_value="9"), polyps=9)
        self.assert_status(self.run_plan([candidate(), second]), "CONFLICT", "SOURCE_BIOLOGY_CONFLICT")

    def test_different_temperature_blocks_even_when_only_one_biology_matches(self):
        second = candidate(source=evidence(polyp_cell="F7", ephyrae_cell="F8", source_value="16", polyp_value="9"), polyps=9)
        self.assert_status(self.run_plan([candidate(), second]), "CONFLICT", "SOURCE_TEMPERATURE_CONFLICT")

    def test_partial_or_malformed_biology_blocks_entire_group(self):
        for changes in ({"biological_state": "partial", "polyps": None},
                        {"biological_state": "malformed"}, {"polyps": None},
                        {"ephyrae": -1}, {"polyps": True}, {"polyps": "0"},
                        {"ephyrae": 0.0}):
            with self.subTest(changes=changes):
                second = candidate(source=evidence(polyp_cell="F7", ephyrae_cell="F8"), **changes)
                self.assert_status(self.run_plan([candidate(), second]), "BLOCKED")

    def test_counts_must_match_exactly_and_cannot_be_swapped_as_a_historical_correction(self):
        snapshot = baseline()
        snapshot["biological_measurements"][0].update(polyp_count=8, ephyrae_count=80)
        row = candidate(source=evidence(polyp_value="80", ephyrae_value="8"), polyps=80, ephyrae=8)
        self.assert_status(self.run_plan([row], snapshot), "CONFLICT", "BIOLOGICAL_COUNT_MISMATCH")

    def test_missing_box_and_missing_week_do_not_propose_creation(self):
        for field, status in (("boxes", "MISSING_BOX"), ("biological_measurements", "MISSING_WEEK")):
            snapshot = baseline()
            snapshot[field] = []
            if field == "boxes":
                snapshot["biological_measurements"] = []
            with self.subTest(field=field):
                self.assert_status(self.run_plan(snapshot=snapshot), status)

    def test_box_normalization_only_pads_dot_number_and_never_uses_aliases(self):
        for source_box in ("ABC-DEF-1.1", "ABC-DEF-1.01", BOX, "ABC-DEF-1.0001"):
            with self.subTest(source_box=source_box):
                self.assert_status(self.run_plan([candidate(source=evidence(source_box=source_box))]), "CREATE_READING")
        for source_box in ("ASP-EVA1.01", "CLA-JKA1.10", " ABC-DEF-1.01", "ABC-DEF-1.01 ",
                           "abc-DEF-1.01", "ABC-DEF-1.1000", "ABC-DEF-1", "ABC-DEF-1.1\n"):
            with self.subTest(source_box=source_box):
                row = candidate(source=evidence(source_box=source_box), canonical_box=None)
                self.assert_status(self.run_plan([row]), "BLOCKED", "INVALID_BOX_IDENTITY")
        self.assert_status(self.run_plan([candidate(canonical_box="OTHER-BOX")]), "BLOCKED", "INVALID_BOX_IDENTITY")
        renamed = candidate(source=evidence(source_box="COR-JIS-1.01"), canonical_box="COR-JIS-1.001")
        snapshot = baseline()
        snapshot["boxes"][0]["global_code"] = "ATO-JIS-1.001"
        self.assert_status(self.run_plan([renamed], snapshot), "MISSING_BOX")

    def test_invalid_weeks_do_not_resolve_to_other_year_or_week(self):
        for year, week in ((2021, 53), (2026, 0), (2026, 54), (0, 1), (True, 1), (2026, "1")):
            with self.subTest(year=year, week=week):
                self.assert_status(self.run_plan([candidate(iso_year=year, iso_week=week)]), "BLOCKED", "INVALID_ISO_WEEK")
        result = self.run_plan()
        self.assertEqual(result["proposed_changes"][0]["payload"]["iso_year"], 2026)

    def test_invalid_temperatures_and_evidence_are_blocked_not_coerced(self):
        for value in (None, "", "NaN", "Infinity", "15,5", "1_000", 15.0, True):
            with self.subTest(value=value):
                self.assert_status(self.run_plan([candidate(temperature_c=value)]), "BLOCKED", "INVALID_TEMPERATURE")
        for changes in ({"source_value": "16"}, {"polyp_value": "1"}, {"ephyrae_value": ""},
                        {"source_iso_week": 2}, {"source_box": "ABC-DEF-1.02"},
                        {"source_sha256": "bad"}, {"temperature_cell": "D3"},
                        {"polyp_cell": "F0"}, {"temperature_merged_range": "C4:C5"},
                        {"temperature_merged_range": "C3:C2"}, {"temperature_merged_range": "C3:C3"}):
            with self.subTest(changes=changes):
                row = candidate()
                row["evidence"].update(changes)
                self.assert_status(self.run_plan([row]), "BLOCKED")

    def test_candidate_id_collision_and_source_occurrence_metadata_conflict(self):
        first = candidate()
        second = candidate(source=evidence(polyp_cell="F7", ephyrae_cell="F8"), candidate_id=first["candidate_id"])
        self.assert_status(self.run_plan([first, second]), "CONFLICT", "CANDIDATE_ID_COLLISION")
        second = candidate(source=evidence(source_file="renamed.xlsx"))
        result = self.run_plan([first, second])
        self.assert_status(result, "CONFLICT", "SOURCE_OCCURRENCE_CONFLICT")
        self.assertEqual(result["summary"]["duplicate_source_occurrences"], 1)
        self.assertEqual(len(result["associations"][0]["evidences"]), 2)

    def test_already_present_and_add_new_evidence_without_rewriting_accepted_spelling(self):
        snapshot = accepted_baseline()
        self.assert_status(self.run_plan(snapshot=snapshot), "ALREADY_PRESENT")
        new = candidate(source=evidence(polyp_cell="F7", ephyrae_cell="F8", source_value="15.00000"))
        result = self.run_plan([candidate(), new, deepcopy(new)], snapshot)
        self.assert_status(result, "ADD_EVIDENCE")
        payload = result["proposed_changes"][0]["payload"]
        self.assertEqual(payload["temperature_c"], "15.000")
        self.assertEqual(payload["evidences"], [new["evidence"]])

    def test_existing_different_temperature_conflicts(self):
        snapshot = accepted_baseline()
        row = candidate(source=evidence(polyp_cell="F7", ephyrae_cell="F8", source_value="16"))
        self.assert_status(self.run_plan([row], snapshot), "CONFLICT", "EXISTING_TEMPERATURE_CONFLICT")

    def test_existing_same_occurrence_with_different_evidence_is_a_conflict(self):
        for changes in ({"source_file": "other-name.xlsx"}, {"source_value": "15.0"},
                        {"temperature_merged_range": ""}, {"week_value": "1.0"},
                        {"polyp_value": "0.0"}):
            with self.subTest(changes=changes):
                row = candidate(source=evidence(**changes))
                self.assert_status(self.run_plan([row], accepted_baseline()), "CONFLICT", "EXISTING_EVIDENCE_CONFLICT")

    def test_create_then_add_evidence_are_idempotent_using_only_proposed_state(self):
        source = candidate()
        initial = baseline()
        created = self.run_plan([source], initial)
        after_create = apply_to_snapshot(initial, created["proposed_changes"])
        self.assert_status(self.run_plan([source], after_create), "ALREADY_PRESENT")
        new = candidate(source=evidence(polyp_cell="F7", ephyrae_cell="F8"))
        added = self.run_plan([source, new], after_create)
        self.assert_status(added, "ADD_EVIDENCE")
        after_add = apply_to_snapshot(after_create, added["proposed_changes"])
        self.assert_status(self.run_plan([new, source, deepcopy(new)], after_add), "ALREADY_PRESENT")
        self.assertEqual(initial["biological_measurements"], after_add["biological_measurements"])
        self.assertEqual(initial["boxes"], after_add["boxes"])

    def test_output_is_deterministic_json_and_inputs_are_not_mutated(self):
        rows = [candidate(), candidate(source=evidence(polyp_cell="F7", ephyrae_cell="F8")), candidate()]
        snapshot = baseline()
        before = deepcopy((rows, snapshot))
        expected = self.run_plan(rows, snapshot)
        self.assertEqual(json.loads(json.dumps(expected, allow_nan=False)), expected)
        for permutation in itertools.permutations(rows):
            self.assertEqual(self.run_plan(list(permutation), snapshot), expected)
        self.assertEqual((rows, snapshot), before)
        expected["proposed_changes"][0]["payload"]["evidences"][0]["source_file"] = "changed"
        self.assertEqual((rows, snapshot), before)

    def test_unrelated_valid_group_can_be_planned_but_readiness_is_false(self):
        other = candidate(source=evidence(source_box="ABC-DEF-1.02", polyp_cell="F7", ephyrae_cell="F8"), canonical_box="ABC-DEF-1.002")
        result = self.run_plan([candidate(), other])
        self.assertEqual(len(result["proposed_changes"]), 1)
        self.assertEqual(result["summary"]["status_counts"], {"CREATE_READING": 1, "MISSING_BOX": 1})
        self.assertFalse(result["summary"]["ready"])

    def test_bad_candidate_schema_is_reported_without_crashing(self):
        for row in ({}, None, [], 1, {"candidate_id": "bad", "iso_year": [], "evidence": []}):
            with self.subTest(row=row):
                self.assert_status(self.run_plan([row]), "BLOCKED", "INVALID_CANDIDATE_SCHEMA")

    def test_missing_evidence_is_not_reported_as_a_duplicate_occurrence(self):
        result = self.run_plan([{}])
        self.assertEqual(result["summary"]["duplicate_source_occurrences"], 0)

    def test_malformed_rows_are_also_order_independent(self):
        rows = [{}, {"source_box": "bad"}, {"temperature_c": "NaN"}, {"biological_state": "complete"}]
        expected = self.run_plan(rows)
        for permutation in itertools.permutations(rows):
            self.assertEqual(self.run_plan(list(permutation)), expected)

    def test_source_occurrence_cannot_be_reused_for_another_box(self):
        first = candidate()
        second = candidate(source=evidence(source_box="ABC-DEF-1.02"), canonical_box="ABC-DEF-1.002")
        result = self.run_plan([first, second])
        self.assertEqual(result["summary"]["status_counts"], {"CONFLICT": 2})
        self.assertEqual(result["proposed_changes"], [])
        for association in result["associations"]:
            self.assertIn("SOURCE_OCCURRENCE_CONFLICT", association["issues"])

    def test_api_argument_errors_do_not_fall_back_to_an_empty_baseline(self):
        for organization in (None, True, 0, "", [], {}):
            with self.subTest(organization=organization), self.assertRaises(ValueError):
                plan([], organization_id=organization)
        for rows in (None, {}, iter([])):
            with self.subTest(rows=rows), self.assertRaises(TypeError):
                plan(rows, organization_id=7)
        for value in (float("nan"), object()):
            with self.subTest(value=value), self.assertRaises(ValueError):
                plan([value], organization_id=7)

    def test_module_runs_when_all_django_imports_are_forbidden(self):
        script = (
            "import builtins, unittest\n"
            "original = builtins.__import__\n"
            "def guarded(name, *args, **kwargs):\n"
            "    if name == 'django' or name.startswith('django.'):\n"
            "        raise AssertionError('Django import is forbidden')\n"
            "    return original(name, *args, **kwargs)\n"
            "builtins.__import__ = guarded\n"
            "from apps.measurements.historical_temperature_plan import plan\n"
            "assert plan([], organization_id=7)['baseline_status'] == 'BASELINE_UNAVAILABLE'\n"
        )
        result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2],
                                capture_output=True, text=True, timeout=20, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


class HistoricalTemperatureBaselineValidationTests(unittest.TestCase):
    def assert_invalid(self, snapshot, code):
        result = plan([candidate()], organization_id=7, baseline=snapshot)
        self.assertEqual(result["baseline_status"], "BASELINE_INVALID")
        self.assertEqual(result["proposed_changes"], [])
        self.assertFalse(result["summary"]["ready"])
        self.assertIn(code, {error["code"] for error in result["baseline_errors"]})
        return result

    def test_malformed_snapshot_and_collections_fail_closed(self):
        for value in ({}, [], False, "baseline", {**baseline(), "unknown": []}):
            with self.subTest(value=value):
                self.assert_invalid(value, "INVALID_BASELINE_SCHEMA")
        for collection in ("boxes", "biological_measurements", "historical_temperatures"):
            for value in (None, {}, "", [None], [{"id": []}]):
                with self.subTest(collection=collection, value=value):
                    snapshot = baseline()
                    snapshot[collection] = value
                    self.assert_invalid(snapshot, "INVALID_BASELINE_SCHEMA")

    def test_institution_mismatch_is_not_filtered_away(self):
        for location in ("root", "box", "unrelated_box"):
            with self.subTest(location=location):
                snapshot = baseline()
                if location == "root":
                    snapshot["organization_id"] = 8
                elif location == "box":
                    snapshot["boxes"][0]["organization_id"] = 8
                else:
                    snapshot["boxes"].append({"id": 12, "organization_id": 8, "global_code": "ABC-DEF-1.002"})
                self.assert_invalid(snapshot, "ORGANIZATION_MISMATCH")

    def test_duplicate_ids_in_each_table_fail_closed(self):
        for table in ("boxes", "biological_measurements", "historical_temperatures"):
            with self.subTest(table=table):
                snapshot = accepted_baseline()
                snapshot[table].append(deepcopy(snapshot[table][0]))
                self.assert_invalid(snapshot, "DUPLICATE_ID")

    def test_duplicate_box_code_and_biological_week_fail_closed(self):
        snapshot = baseline()
        snapshot["boxes"].append({**snapshot["boxes"][0], "id": 12})
        self.assert_invalid(snapshot, "DUPLICATE_BOX_CODE")
        snapshot = baseline()
        snapshot["biological_measurements"].append({**snapshot["biological_measurements"][0], "id": 22})
        self.assert_invalid(snapshot, "DUPLICATE_BIOLOGICAL_WEEK")

    def test_duplicate_reading_or_evidence_fails_closed(self):
        snapshot = accepted_baseline()
        snapshot["historical_temperatures"].append({**deepcopy(snapshot["historical_temperatures"][0]), "id": 32})
        self.assert_invalid(snapshot, "DUPLICATE_HISTORICAL_TEMPERATURE")
        snapshot = accepted_baseline()
        snapshot["historical_temperatures"][0]["evidences"].append(evidence())
        self.assert_invalid(snapshot, "DUPLICATE_BASELINE_EVIDENCE")

    def test_orphans_are_not_discarded_even_when_unrelated_to_candidates(self):
        snapshot = baseline()
        snapshot["biological_measurements"].append({**snapshot["biological_measurements"][0], "id": 22, "box_id": 999})
        self.assert_invalid(snapshot, "ORPHAN_BIOLOGICAL_MEASUREMENT")
        snapshot = accepted_baseline()
        snapshot["historical_temperatures"].append({**deepcopy(snapshot["historical_temperatures"][0]), "id": 32, "biological_measurement_id": 999})
        self.assert_invalid(snapshot, "ORPHAN_HISTORICAL_TEMPERATURE")

    def test_dates_are_canonical_monday_keys_not_observation_dates(self):
        for value in ("2026-01-01", "2025-12-30", "20251229", "2026-W01-1", "2025-12-29T00:00:00", "2025-02-30", None, 0, []):
            with self.subTest(value=value):
                snapshot = baseline()
                snapshot["biological_measurements"][0]["week_start"] = value
                self.assert_invalid(snapshot, "INVALID_MONDAY_WEEK_KEY")

    def test_invalid_baseline_counts_and_ids_fail_closed(self):
        for value in (None, -1, True, "0", 0.0, []):
            with self.subTest(value=value):
                snapshot = baseline()
                snapshot["biological_measurements"][0]["polyp_count"] = value
                self.assert_invalid(snapshot, "INVALID_BIOLOGICAL_COUNTS")
        for value in (None, True, 0, 1.0, "", []):
            with self.subTest(value=value):
                snapshot = baseline()
                snapshot["boxes"][0]["id"] = value
                self.assert_invalid(snapshot, "INVALID_BASELINE_SCHEMA")

    def test_invalid_reading_week_temperature_and_evidence_fail_closed(self):
        for changes, code in (({"iso_week": 2}, "HISTORICAL_WEEK_MISMATCH"),
                              ({"iso_year": 2021, "iso_week": 53}, "HISTORICAL_WEEK_MISMATCH"),
                              ({"temperature_c": "NaN"}, "INVALID_TEMPERATURE"),
                              ({"evidences": []}, "MISSING_HISTORICAL_EVIDENCE"),
                              ({"evidences": [None]}, "INVALID_EVIDENCE_SCHEMA"),
                              ({"evidences": [evidence(source_value="20")]}, "EVIDENCE_TEMPERATURE_MISMATCH"),
                              ({"evidences": [evidence(polyp_value="5")]}, "EVIDENCE_BIOLOGY_MISMATCH"),
                              ({"evidences": [evidence(source_box="ABC-DEF-1.02")]}, "EVIDENCE_BOX_MISMATCH")):
            with self.subTest(changes=changes):
                snapshot = accepted_baseline()
                snapshot["historical_temperatures"][0].update(changes)
                self.assert_invalid(snapshot, code)

    def test_arbitrary_wrong_json_field_types_fail_closed_without_crashing(self):
        for table in ("boxes", "biological_measurements", "historical_temperatures"):
            for field in accepted_baseline()[table][0]:
                for value in (None, [], {}, False, "", 1.25):
                    with self.subTest(table=table, field=field, value=value):
                        snapshot = accepted_baseline()
                        snapshot[table][0][field] = value
                        result = plan([candidate()], organization_id=7, baseline=snapshot)
                        self.assertEqual(result["baseline_status"], "BASELINE_INVALID")
                        self.assertEqual(result["proposed_changes"], [])

    def test_cross_institution_graph_cannot_authorize_a_reading(self):
        snapshot = accepted_baseline()
        snapshot["boxes"][0]["organization_id"] = 8
        self.assert_invalid(snapshot, "ORGANIZATION_MISMATCH")

    def test_invalid_baseline_still_reports_source_conflict(self):
        snapshot = baseline()
        snapshot["organization_id"] = 8
        rows = [candidate(), candidate(source=evidence(polyp_cell="F7", ephyrae_cell="F8", source_value="20"))]
        result = plan(rows, organization_id=7, baseline=snapshot)
        self.assertEqual(result["associations"][0]["status"], "CONFLICT")
        self.assertIn("BASELINE_INVALID", result["associations"][0]["issues"])
        self.assertEqual(result["proposed_changes"], [])

    def test_string_ids_and_baseline_order_have_deterministic_semantics(self):
        snapshot = baseline()
        snapshot["organization_id"] = "org"
        snapshot["boxes"][0].update(id="box", organization_id="org")
        snapshot["boxes"].append({"id": "other", "organization_id": "org", "global_code": "ABC-DEF-1.002"})
        snapshot["biological_measurements"][0].update(id="measurement", box_id="box")
        result = plan([candidate()], organization_id="org", baseline=snapshot)
        self.assertEqual(result["proposed_changes"][0]["payload"]["biological_measurement_id"], "measurement")
        snapshot["boxes"].reverse()
        self.assertEqual(plan([candidate()], organization_id="org", baseline=snapshot), result)


if __name__ == "__main__":
    unittest.main()
