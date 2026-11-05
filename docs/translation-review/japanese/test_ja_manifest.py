"""Workbook-free consistency checks between the Japanese manifest and catalogs.

Run from the repository root:
    python -m unittest discover -s docs/translation-review/japanese -p "test_*.py"
"""
import csv
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "docs/translation-review/application"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_i18n  # noqa: E402
import check_register  # noqa: E402
import compile_django_messages  # noqa: E402
from test_check_register import (  # noqa: E402
    B13_POSITION, LAB_POSITION, b13_integrity_errors, lab_integrity_errors,
)


def rows(name):
    with (HERE / name).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


# Commit whose FR/EN bytes and manifest the historical fingerprints freeze.
FROZEN_BASELINE = "c857a592e170ae6933969098c90fed95d0eb3ea5"


def post_baseline_react_keys(register=None):
    """React keys registered as new FR/EN pairs after the frozen baseline.

    Only pure additions qualify: any other registered change since the
    baseline fails here instead of being silently excluded from history.
    Thermal and lab events share B13 and the baseline but have independent
    fingerprints; neither can redefine what is excluded by a coordinated edit.
    """
    register = check_register.load_register() if register is None else register
    integrity = b13_integrity_errors(register) + lab_integrity_errors(register)
    if integrity:
        raise AssertionError("; ".join(integrity))
    entries = [r for r in register if r["baseline_git_head"] == FROZEN_BASELINE]
    expected = register[B13_POSITION] + register[LAB_POSITION]
    if entries != expected or [r for r in register if r["batch"] == "B13-2026-11-04"] != expected:
        raise AssertionError("only the frozen thermal and lab events may use B13 or the frozen baseline")
    languages = {}
    for entry in entries:
        thermal = entry in register[B13_POSITION]
        expected_status = "ALREADY_MATCHING" if thermal or entry["key_or_msgid"] == "adminLabReferenceSalinity" else "DEFERRED"
        reason = "New key added" if thermal else "New Administration > Laboratory configuration key"
        if (entry["surface"] != "react" or entry["status"] != expected_status
                or entry["initial_value"] != entry["final_value"] or reason not in entry["reason"]):
            raise AssertionError(f"{entry['entry_id']}: not a registered new React key")
        languages.setdefault(entry["key_or_msgid"], []).append(entry["language"])
    for key, found in languages.items():
        if sorted(found) != ["EN", "FR"]:
            raise AssertionError(f"{key}: expected exactly one FR and one EN registration, got {found}")
    return {key: {e["language"]: e["final_value"] for e in entries if e["key_or_msgid"] == key} for key in languages}


class JapaneseManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = rows("ja-manifest.csv")
        cls.fr = check_register.read_catalog(ROOT / "frontend/src/i18n/fr.ts")
        cls.ja_ts = check_register.parse_catalog((ROOT / "frontend/src/i18n/ja.ts").read_text(encoding="utf-8"), "ja")
        cls.ja_po = {k: v for k, v in compile_django_messages.parse_po_file(
            ROOT / "backend/locale/ja/LC_MESSAGES/django.po").items() if k}
        cls.sources = check_i18n.discover_sources(ROOT)

    def by(self, surface):
        return [row for row in self.manifest if row["surface"] == surface]

    def test_rewritten_b13_events_cannot_redefine_the_reconstruction(self):
        original = check_register.load_register()
        self.assertEqual(post_baseline_react_keys(original)["temperatureDailyMean"]["EN"], "Daily mean")
        rewritten = [dict(row) for row in original]
        t856 = next(row for row in rewritten if row["entry_id"] == "T856")
        t856.update(initial_value="Daily average", final_value="Daily average")
        with self.assertRaisesRegex(AssertionError, "B13 history was modified"):
            post_baseline_react_keys(rewritten)
        extra = dict(original[-1], entry_id="T887", batch="B14-2026-11-05")
        with self.assertRaisesRegex(AssertionError, "only the frozen thermal and lab events"):
            post_baseline_react_keys(original + [extra])

    def test_lab_history_cannot_be_omitted_reordered_or_promoted_to_approval(self):
        original = check_register.load_register()
        self.assertEqual(len(post_baseline_react_keys(original)), 21)
        variants = {
            "deleted lab": original[:858],
            "missing FR": original[:858] + original[859:],
            "reordered": original[:858] + [original[859], original[858]] + original[860:],
            "duplicate": original + [dict(original[-1])],
        }
        for field, value in {
            "status": "ALREADY_MATCHING", "decision_ref": "invented-approval",
            "reason": "New key added; approved", "baseline_git_head": "2" * 40,
            "initial_value": "changed", "final_value": "changed",
        }.items():
            changed = [dict(row) for row in original]
            changed[858][field] = value
            variants[field] = changed
        for label, register in variants.items():
            with self.subTest(label=label), self.assertRaises(AssertionError):
                post_baseline_react_keys(register)

    def test_post_baseline_keys_fall_back_to_english_without_japanese(self):
        added = post_baseline_react_keys()
        self.assertEqual(len(added), 21)
        en = check_register.read_catalog(ROOT / "frontend/src/i18n/en.ts")
        react = {r["identity"]: r for r in self.by("react")}
        for key, values in added.items():
            with self.subTest(key=key):
                self.assertEqual((self.fr[key], en[key]), (values["FR"], values["EN"]))
                self.assertEqual((react[key]["current_fr"], react[key]["current_en"]), (values["FR"], values["EN"]))
                row = react[key]
                self.assertEqual((row["status"], row["reason_code"]), ("FALLBACK_EN", "ABSENT_FROM_WORKBOOK"))
                self.assertEqual((row["sheet_row"], row["ja_validated"], row["ja_ai_reviewed"]), ("", "", ""))
                self.assertNotIn(key, self.ja_ts)

    def test_exact_counts(self):
        # Historical counts are frozen; only registered post-baseline keys are added.
        self.assertEqual(Counter((r["surface"], r["status"]) for r in self.manifest), Counter({
            ("react", "REUSED"): 930, ("react", "AI_REVIEWED"): 59,
            ("react", "FALLBACK_EN"): 164, ("react", "FALLBACK_EN_REVIEW"): 36,
            ("django", "REUSED"): 81, ("django", "FALLBACK_EN"): 11, ("django", "FALLBACK_EN_REVIEW"): 6,
            ("local", "REUSED"): 67, ("local", "FALLBACK_EN_REVIEW"): 1, ("inline", "FALLBACK_EN_INLINE"): 14,
        }))
        self.assertEqual(Counter(r["surface"] for r in rows("ja-obsolete-workbook-keys.csv")),
                         Counter({"react": 217, "django": 32}))

    def test_manifest_covers_every_current_identity_once(self):
        identities = [r["identity"] for r in self.manifest]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertEqual([r["identity"] for r in self.by("react")], list(self.fr))
        self.assertEqual({r["identity"] for r in self.by("django")},
                         {f"django:gettext:{msgid}" for msgid in self.sources})

    def test_catalogs_equal_their_authorized_provenance_rows(self):
        self.assertEqual(self.ja_ts, {r["identity"]: r["ja_validated"] if r["status"] == "REUSED" else r["ja_ai_reviewed"]
                                             for r in self.by("react") if r["status"] in {"REUSED", "AI_REVIEWED"}})
        self.assertEqual(self.ja_po, {r["identity"].removeprefix("django:gettext:"): r["ja_validated"]
                                      for r in self.by("django") if r["status"] == "REUSED"})
        self.assertTrue(set(self.ja_po) <= set(self.sources))
        self.assertTrue(set(self.ja_ts) <= set(self.fr))

    def test_obsolete_workbook_keys_are_not_current(self):
        for row in rows("ja-obsolete-workbook-keys.csv"):
            with self.subTest(identity=row["identity"]):
                self.assertNotIn(row["identity"], self.fr)
                self.assertNotIn(row["identity"], self.ja_ts)
                self.assertNotIn(row["identity"].removeprefix("django:gettext:"), self.ja_po)

    def test_every_reuse_comes_from_validated_japanese(self):
        for row in self.manifest:
            if row["status"] == "REUSED":
                with self.subTest(identity=row["identity"]):
                    self.assertTrue(row["ja_validated"].strip())
                    self.assertTrue(row["sheet_row"].isdigit())
                    self.assertTrue(row["reason_code"])

    def test_takeru_review_lists_every_deferred_or_open_identity(self):
        review = rows("ja-takeru-review.csv")
        expected = [r["identity"] for r in self.manifest if r["takeru_review"] == "yes"]
        self.assertEqual([r["identity"] for r in review], expected)
        self.assertEqual(len(review), 43)
        for row in review:
            with self.subTest(identity=row["identity"]):
                self.assertTrue(row["note"].strip())
                self.assertEqual(row["status"], "FALLBACK_EN_REVIEW")

    def test_open_questions_are_deferred_and_keep_the_original_japanese(self):
        import build_ja_catalogs as builder

        questions = {r["identity"]: r for r in self.manifest if r["reason_code"] == "OPEN_QUESTION"}
        self.assertEqual(set(questions), set(builder.OPEN_QUESTIONS))
        self.assertEqual(len(questions), 8)
        for key, row in questions.items():
            with self.subTest(key=key):
                self.assertEqual(row["status"], "FALLBACK_EN_REVIEW")
                self.assertNotIn(key, self.ja_ts)
                self.assertTrue(row["ja_validated"])
                entry = {"row": row["sheet_row"], "section": row["sheet_section"],
                         "fr": row["sheet_fr"], "en": row["sheet_en"],
                         "ja": row["ja_validated"], "ja_status": "Fait", "en_status": "Fait"}
                classified = builder.classify(key, row["current_fr"], row["current_en"], entry, "react")
                self.assertEqual(classified["status"], "FALLBACK_EN_REVIEW")
                self.assertEqual(classified["ja_validated"], row["ja_validated"])


if __name__ == "__main__":
    unittest.main()
