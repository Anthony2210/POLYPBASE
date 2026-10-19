"""Synthetic offline policy tests; no Django/database setup."""

import unittest
from copy import deepcopy
from unittest.mock import patch

from .historical_temperature_partial import PartialImportError, compare_approval, prepare_approved_partial, select_partial
from .historical_temperature_plan import plan
from .test_historical_temperature_plan import baseline, candidate, evidence


def report(rows=None, snapshot=None):
    association_plan = plan(rows or [candidate()], organization_id=7, baseline=snapshot or baseline())
    return {
        "association_plan": association_plan, "organization_id": 7,
        "summary": {"ready": association_plan["summary"]["ready"]},
        "report_fingerprint": "a" * 64, "baseline_fingerprint": "b" * 64,
        "source_extraction": {"exclusions": [{"reason": "MISSING_TEMPERATURE"}], "header_issues": []},
    }


class PartialPolicyTests(unittest.TestCase):
    def test_only_create_is_eligible_including_when_global_ready_is_false(self):
        original = report()
        for status in ("BLOCKED", "CONFLICT", "BASELINE_UNAVAILABLE", "BASELINE_INVALID", "MISSING_BOX",
                       "MISSING_WEEK", "ADD_EVIDENCE", "ALREADY_PRESENT", "UNKNOWN_FUTURE_STATUS"):
            item = deepcopy(original["association_plan"]["associations"][0])
            item["status"] = status
            original["association_plan"]["associations"].append(item)
        original["summary"]["ready"] = False
        selected = select_partial(original)
        self.assertEqual(selected["summary"]["accepted_readings"], 1)
        self.assertEqual(selected["summary"]["excluded_groups"], 9)
        self.assertFalse(selected["global_ready"])
        self.assertEqual(selected["source_exclusions"], original["source_extraction"]["exclusions"])
        self.assertEqual(selected["payloads"], [original["association_plan"]["proposed_changes"][0]["payload"]])

    def test_agreeing_multi_source_evidence_is_not_lost(self):
        rows = [candidate(), candidate(source=evidence(polyp_cell="F7", ephyrae_cell="F8", source_value="15"))]
        result = select_partial(report(rows))
        self.assertEqual(result["summary"]["accepted_evidences"], 2)
        self.assertEqual(len(result["payloads"][0]["evidences"]), 2)

    def test_temperature_conflict_and_missing_are_excluded_not_repaired(self):
        for source in (evidence(source_value="20"), evidence(source_value="")):
            result = select_partial(report([candidate(), candidate(source=source)]))
            self.assertEqual(result["payloads"], [])

    def test_zero_is_a_real_temperature(self):
        result = select_partial(report([candidate(source=evidence(source_value="0.000"))]))
        self.assertEqual(result["payloads"][0]["temperature_c"], "0.000")

    def test_create_with_issue_or_missing_evidence_never_executes(self):
        for field, value in (("issues", ["UNEXPECTED"]), ("evidences", [])):
            original = report()
            original["association_plan"]["associations"][0][field] = value
            with self.assertRaises(PartialImportError):
                select_partial(original)

    def test_invalid_whole_baseline_is_rejected(self):
        original = report()
        original["association_plan"]["baseline_status"] = "BASELINE_INVALID"
        with self.assertRaises(PartialImportError):
            select_partial(original)

    def test_changed_snapshot_is_rejected_before_source_extraction(self):
        with patch("apps.measurements.historical_temperature_partial.build_report") as build:
            with self.assertRaisesMessage(PartialImportError, "approved_snapshot_changed"):
                prepare_approved_partial("not-opened", b"changed")
        build.assert_not_called()

    def test_changed_approval_is_rejected(self):
        selected = select_partial(report())
        compare_approval(selected, selected["plan_fingerprint"])
        with self.assertRaises(PartialImportError):
            compare_approval(selected, "changed")

    def assertRaisesMessage(self, exception, message):
        return self.assertRaisesRegex(exception, message)
