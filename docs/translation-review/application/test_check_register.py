"""Regression tests for check_register.py, on synthetic histories and on the real register.

Run from the repository root:
    python -m unittest discover -s docs/translation-review/application -p "test_*.py"
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_register as reg  # noqa: E402

HEAD_1 = "1" * 40
HEAD_2 = "2" * 40
SHEET = "a" * 64
B01 = "B01-2026-10-28"
B02 = "B02-2026-11-05"
# Canonical digest of the 34 B01 rows (parsed fields, independent of line endings and of later batches).
B01_ROWS_SHA256 = "938718e0b044503fd19a48bef9c013ccbd2de4653277f05b9b54aba078c434be"


def rows_digest(rows):
    canonical = json.dumps([[row[name] for name in reg.COLUMNS] for row in rows], ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def event(entry, batch, key, initial, final, status="APPLIED", surface="react", language="EN", source=None):
    head = HEAD_1 if batch == B01 else HEAD_2
    default_source = {
        "react": f"frontend/src/i18n/{language.lower()}.ts",
        "django": f"backend/locale/{language.lower()}/LC_MESSAGES/django.po",
        "inline": "frontend/src/boxStatus.ts",
    }[surface]
    return {
        "entry_id": f"T{entry:03d}", "batch": batch, "surface": surface, "key_or_msgid": key,
        "language": language, "source_file": source or default_source, "initial_value": initial,
        "final_value": final, "sheet_value": "", "decision_ref": "test", "status": status,
        "reason": "synthetic", "baseline_git_head": head, "baseline_sheet_sha256": SHEET,
    }


def fake_sources(values):
    """values maps (surface, key, language) to the current application value; missing means absent."""
    def state(row):
        value = values.get((row["surface"], row["key_or_msgid"], row["language"]))
        return reg.SourceState(value is not None, value)
    return state


def roundtrip(rows):
    """Write and reload through CSV so the tests exercise the real file format."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "register.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, reg.COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        return reg.load_register(path)


class SyntheticHistoryTests(unittest.TestCase):
    def assertValid(self, rows, sources):
        self.assertEqual(reg.validate(roundtrip(rows), fake_sources(sources)), [])

    def assertRejected(self, rows, sources, fragment):
        errors = reg.validate(roundtrip(rows), fake_sources(sources))
        self.assertTrue(errors, "invalid history was accepted")
        self.assertTrue(any(fragment in error for error in errors), errors)

    def test_a_b02_continuing_b01_is_valid(self):
        rows = [
            event(1, B01, "recentAccess", "Recent access", "Latest access"),
            event(2, B02, "recentAccess", "Latest access", "Last access"),
        ]
        self.assertValid(rows, {("react", "recentAccess", "EN"): "Last access"})

    def test_b_b02_initial_value_must_continue_b01_final_value(self):
        rows = [
            event(1, B01, "recentAccess", "Recent access", "Latest access"),
            event(2, B02, "recentAccess", "Recent access", "Last access"),
        ]
        self.assertRejected(rows, {("react", "recentAccess", "EN"): "Last access"}, "does not continue")

    def test_c_b02_rows_before_b01_are_rejected(self):
        rows = [
            event(1, B02, "recentAccess", "Latest access", "Last access"),
            event(2, B01, "recentAccess", "Recent access", "Latest access"),
        ]
        self.assertRejected(rows, {("react", "recentAccess", "EN"): "Latest access"}, "not numbered after")

    def test_c_interleaved_batches_are_rejected(self):
        rows = [
            event(1, B01, "recentAccess", "Recent access", "Latest access"),
            event(2, B02, "loginIntro", "Old", "New"),
            event(3, B01, "overviewEmpty", "No box.", "No box.", status="ALREADY_MATCHING"),
        ]
        sources = {("react", "recentAccess", "EN"): "Latest access", ("react", "loginIntro", "EN"): "New",
                   ("react", "overviewEmpty", "EN"): "No box."}
        self.assertRejected(rows, sources, "is split by another batch")

    def test_c_entry_ids_must_follow_file_order(self):
        rows = [
            event(2, B01, "recentAccess", "Recent access", "Latest access"),
            event(1, B02, "loginIntro", "Old", "New"),
        ]
        sources = {("react", "recentAccess", "EN"): "Latest access", ("react", "loginIntro", "EN"): "New"}
        self.assertRejected(rows, sources, "entry_id not strictly increasing")

    def test_c_later_batch_dated_earlier_is_rejected(self):
        rows = [
            event(1, B01, "recentAccess", "Recent access", "Latest access"),
            event(2, "B02-2026-10-01", "loginIntro", "Old", "New"),
        ]
        sources = {("react", "recentAccess", "EN"): "Latest access", ("react", "loginIntro", "EN"): "New"}
        self.assertRejected(rows, sources, "dated before")

    def test_c_impossible_calendar_dates_are_rejected(self):
        for batch in ("B02-2026-99-99", "B02-2026-02-29", "B02-2026-04-31"):
            with self.subTest(batch=batch):
                rows = [event(1, batch, "key", "Value", "Value", status="ALREADY_MATCHING")]
                self.assertRejected(rows, {("react", "key", "EN"): "Value"}, "invalid calendar date")
        self.assertValid([event(1, "B02-2028-02-29", "key", "Value", "Value", status="ALREADY_MATCHING")],
                         {("react", "key", "EN"): "Value"})

    def test_c_same_batch_number_with_two_ids_is_ambiguous(self):
        rows = [
            event(1, B01, "recentAccess", "Recent access", "Latest access"),
            event(2, "B01-2026-10-30", "loginIntro", "Old", "New"),
        ]
        sources = {("react", "recentAccess", "EN"): "Latest access", ("react", "loginIntro", "EN"): "New"}
        self.assertRejected(rows, sources, "batch number 1 is used by")

    def test_d_duplicate_event_in_same_batch_is_rejected(self):
        rows = [
            event(1, B01, "recentAccess", "Recent access", "Latest access"),
            event(2, B01, "recentAccess", "Latest access", "Last access"),
        ]
        self.assertRejected(rows, {("react", "recentAccess", "EN"): "Last access"}, "duplicate event")

    def test_d_identical_duplicate_is_also_rejected(self):
        row = event(1, B01, "recentAccess", "Recent access", "Latest access")
        duplicate = dict(row, entry_id="T002")
        self.assertRejected([row, duplicate], {("react", "recentAccess", "EN"): "Latest access"}, "duplicate event")

    def test_d_later_contradiction_does_not_mask_earlier_event(self):
        rows = [
            event(1, B01, "recentAccess", "Recent access", "Latest access"),
            event(2, B01, "recentAccess", "Recent access", "Recent access", status="DEFERRED"),
        ]
        # The code still shows the first event's value; the contradictory second event must not win silently.
        self.assertRejected(rows, {("react", "recentAccess", "EN"): "Latest access"}, "duplicate event")

    def test_e_same_key_on_different_surfaces_is_independent(self):
        rows = [
            event(1, B01, "Pending", "Pending", "Pending", status="DEFERRED", surface="react"),
            event(2, B01, "Pending", "À vérifier", "Pending", surface="django"),
            event(3, B01, "Pending", "Pending", "Pending", status="ALREADY_MATCHING", surface="inline"),
        ]
        sources = {("react", "Pending", "EN"): "Pending", ("django", "Pending", "EN"): "Pending",
                   ("inline", "Pending", "EN"): "Pending"}
        self.assertValid(rows, sources)

    def test_e_same_key_in_two_languages_is_independent(self):
        rows = [
            event(1, B01, "subcultureForbidden", "Old FR", "New FR", language="FR"),
            event(2, B01, "subcultureForbidden", "Old EN", "New EN", language="EN"),
        ]
        sources = {("react", "subcultureForbidden", "FR"): "New FR", ("react", "subcultureForbidden", "EN"): "New EN"}
        self.assertValid(rows, sources)

    def test_f_historical_b01_may_differ_from_code_when_b02_matches(self):
        rows = [
            event(1, B01, "loginIntro", "Access your cultures.", "Access your boxes."),
            event(2, B02, "loginIntro", "Access your boxes.", "Access your boxes and records."),
        ]
        self.assertValid(rows, {("react", "loginIntro", "EN"): "Access your boxes and records."})

    def test_f_latest_event_must_still_match_code(self):
        rows = [
            event(1, B01, "loginIntro", "Access your cultures.", "Access your boxes."),
            event(2, B02, "loginIntro", "Access your boxes.", "Access your boxes and records."),
        ]
        self.assertRejected(rows, {("react", "loginIntro", "EN"): "Access your boxes."}, "latest event expects")

    def test_g_no_op_statuses_carry_the_value_forward(self):
        rows = [
            event(1, B01, "overviewEmpty", "No living box.", "No living box.", status="ALREADY_MATCHING"),
            event(2, B01, "movementKindUnclassified", "Provenance", "Provenance", status="PRESERVED"),
            event(3, B01, "adminTransferText", "Old", "Old", status="DEFERRED"),
            event(4, B02, "adminTransferText", "Old", "New"),
            event(5, B02, "movementKindUnclassified", "Provenance", "Provenance", status="PRESERVED"),
        ]
        sources = {("react", "overviewEmpty", "EN"): "No living box.",
                   ("react", "movementKindUnclassified", "EN"): "Provenance",
                   ("react", "adminTransferText", "EN"): "New"}
        self.assertValid(rows, sources)

    def test_g_no_op_status_cannot_change_value(self):
        for status in ("ALREADY_MATCHING", "PRESERVED", "DEFERRED"):
            with self.subTest(status=status):
                rows = [event(1, B01, "adminTransferText", "Old", "New", status=status)]
                self.assertRejected(rows, {("react", "adminTransferText", "EN"): "New"}, "must not change the value")

    def test_g_deferred_cannot_hide_an_unrecorded_code_change(self):
        rows = [event(1, B01, "adminTransferText", "Old", "Old", status="DEFERRED")]
        self.assertRejected(rows, {("react", "adminTransferText", "EN"): "Edited"}, "latest event expects")

    def test_g_applied_requires_a_change(self):
        rows = [event(1, B01, "recentAccess", "Latest access", "Latest access")]
        self.assertRejected(rows, {("react", "recentAccess", "EN"): "Latest access"}, "APPLIED without a change")

    def test_g_not_found_has_no_values_and_must_be_absent(self):
        rows = [event(1, B01, "missingKey", "", "", status="NOT_FOUND")]
        self.assertValid(rows, {})
        self.assertRejected(rows, {("react", "missingKey", "EN"): "Here"}, "marked NOT_FOUND but present")
        invented = [event(1, B01, "missingKey", "", "Invented", status="NOT_FOUND")]
        self.assertRejected(invented, {}, "NOT_FOUND must leave")

    def test_i_inline_not_found_is_rejected(self):
        rows = [event(1, B01, "boxStatus.ts#statusLabels.en.pending_review", "", "", status="NOT_FOUND",
                      surface="inline")]
        # Rejected whatever the source reports: absence of an inline string cannot be proven.
        self.assertRejected(rows, {}, "NOT_FOUND is not supported for inline translations")
        self.assertRejected(rows, {("inline", "boxStatus.ts#statusLabels.en.pending_review", "EN"): "Pending review"},
                            "NOT_FOUND is not supported for inline translations")
        self.assertTrue(any("not supported for inline" in error for error in reg.validate(roundtrip(rows))))

    def test_i_react_not_found_is_accepted_when_key_is_absent(self):
        rows = [event(1, B01, "retiredKey", "", "", status="NOT_FOUND", surface="react")]
        self.assertValid(rows, {("react", "otherKey", "EN"): "Other"})

    def test_i_react_not_found_is_rejected_when_key_exists(self):
        rows = [event(1, B01, "recentAccess", "", "", status="NOT_FOUND", surface="react")]
        self.assertRejected(rows, {("react", "recentAccess", "EN"): "Latest access"}, "marked NOT_FOUND but present")

    def test_i_inline_supported_statuses_remain_valid(self):
        key = "boxStatus.ts#statusLabels.en.pending_review"
        rows = [
            event(1, B01, key, "Pending review", "Pending review", status="DEFERRED", surface="inline"),
            event(2, B02, key, "Pending review", "Pending", status="APPLIED", surface="inline"),
        ]
        self.assertValid(rows, {("inline", key, "EN"): "Pending"})
        for status in ("ALREADY_MATCHING", "PRESERVED", "DEFERRED"):
            with self.subTest(status=status):
                single = [event(1, B01, key, "Pending review", "Pending review", status=status, surface="inline")]
                self.assertValid(single, {("inline", key, "EN"): "Pending review"})

    def test_g_event_after_not_found_starts_from_absence(self):
        rows = [
            event(1, B01, "missingKey", "", "", status="NOT_FOUND"),
            event(2, B02, "missingKey", "Was there", "Now", status="APPLIED"),
        ]
        self.assertRejected(rows, {("react", "missingKey", "EN"): "Now"}, "does not continue")

    def test_placeholders_must_be_preserved(self):
        rows = [event(1, B01, "auditDescriptionManualTemperature", "Recorded for {zone}", "Recorded for {code}")]
        self.assertRejected(rows, {("react", "auditDescriptionManualTemperature", "EN"): "Recorded for {code}"},
                            "placeholders differ")

    def test_placeholder_multiplicity_must_be_preserved(self):
        rows = [event(1, B01, "key", "{count} / {count}", "{count}")]
        self.assertRejected(rows, {("react", "key", "EN"): "{count}"}, "placeholders differ")

    def test_django_presence_is_independent_of_translation_value(self):
        row = event(1, B01, "empty", "", "", status="NOT_FOUND", surface="django")
        # Isolate presence from strict PO validation, which separately rejects empty translations.
        with patch.object(reg, "read_po", return_value={"empty": ""}):
            state = reg.read_source_state(row)
            self.assertEqual(state, reg.SourceState(True, "empty"))
            self.assertTrue(any("marked NOT_FOUND but present" in error for error in reg.validate([row])))
        with patch.object(reg, "read_po", return_value={}):
            self.assertEqual(reg.read_source_state(row), reg.SourceState(False, "empty"))
            self.assertEqual(reg.validate([row]), [])

    def test_register_reuses_strict_po_parser(self):
        row = event(1, B01, "key", "Value", "Value", status="DEFERRED", surface="django")
        with patch.object(reg, "parse_po_file", side_effect=ValueError("invalid PO")):
            with self.assertRaisesRegex(ValueError, "invalid PO"):
                reg.read_source_state(row)

    def test_japanese_is_not_accepted(self):
        rows = [event(1, B01, "boxes", "箱", "容器", language="JA", source="frontend/src/i18n/ja.ts")]
        self.assertRejected(rows, {("react", "boxes", "JA"): "容器"}, "unsupported language")


class RealRegisterTests(unittest.TestCase):
    def test_h_b01_register_is_unchanged_and_valid(self):
        rows = reg.load_register()
        b01 = [row for row in rows if row["batch"] == B01]
        self.assertEqual(len(b01), 34)
        self.assertEqual(rows[:34], b01, "B01 rows must stay first and contiguous")
        self.assertEqual(rows_digest(b01), B01_ROWS_SHA256, "B01 history was modified")
        self.assertEqual(reg.validate(rows), [])
        self.assertEqual(reg.check_catalog_parity(), [])

    def test_h_corrupted_copy_of_b01_is_rejected(self):
        rows = reg.load_register()
        corrupted = [dict(row) for row in rows]
        corrupted[0]["final_value"] = corrupted[0]["initial_value"]
        self.assertTrue(reg.validate(corrupted))
        self.assertNotEqual(rows_digest(corrupted[:34]), B01_ROWS_SHA256)

    def test_h_valid_b02_can_be_appended_without_touching_b01(self):
        rows = [row for row in reg.load_register() if row["batch"] == B01]
        self.assertEqual(len(rows), 34)
        last = next(row for row in rows if row["key_or_msgid"] == "recentAccess")
        b02 = dict(last, entry_id="T035", batch=B02, initial_value=last["final_value"],
                   final_value=last["final_value"], status="ALREADY_MATCHING", baseline_git_head=HEAD_2)
        appended = rows + [b02]
        self.assertEqual(rows_digest(appended[:34]), B01_ROWS_SHA256)
        historical_sources = lambda row: reg.SourceState(True, row["final_value"])
        self.assertEqual(reg.validate(appended, historical_sources), [])
        broken = dict(b02, initial_value="Recent access")
        self.assertTrue(any("does not continue" in e
                            for e in reg.validate(rows + [broken], historical_sources)))
        b03 = dict(b02, entry_id="T036", batch="B03-2026-11-06")
        self.assertEqual(reg.validate(appended + [b03], historical_sources), [])
        self.assertEqual(rows_digest((appended + [b03])[:34]), B01_ROWS_SHA256)


if __name__ == "__main__":
    unittest.main()
