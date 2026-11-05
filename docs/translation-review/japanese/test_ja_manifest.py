"""Workbook-free consistency checks between the Japanese manifest and catalogs.

Run from the repository root:
    python -m unittest discover -s docs/translation-review/japanese -p "test_*.py"
"""
import csv
import json
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
HISTORICAL_MOVEMENT_KEYS = json.loads((HERE / "historical-movement-keys.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(ROOT / "docs/translation-review/application"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_i18n  # noqa: E402
import check_register  # noqa: E402
import compile_django_messages  # noqa: E402


def rows(name):
    with (HERE / name).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


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

    def test_exact_counts(self):
        self.assertEqual(Counter((r["surface"], r["status"]) for r in self.manifest), Counter({
            ("react", "REUSED"): 930, ("react", "AI_REVIEWED"): 59,
            ("react", "FALLBACK_EN"): 208, ("react", "FALLBACK_EN_REVIEW"): 36,
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

    def test_historical_movement_additions_are_exact_untranslated_sources(self):
        self.assertEqual(len(HISTORICAL_MOVEMENT_KEYS), 44)
        self.assertEqual(len(set(HISTORICAL_MOVEMENT_KEYS)), 44)
        en = check_register.read_catalog(ROOT / "frontend/src/i18n/en.ts")
        added = [r for r in self.by("react") if r["identity"] in HISTORICAL_MOVEMENT_KEYS]
        self.assertEqual([r["identity"] for r in added], HISTORICAL_MOVEMENT_KEYS)
        for row in added:
            key = row["identity"]
            with self.subTest(key=key):
                self.assertEqual(row["current_fr"], self.fr[key])
                self.assertEqual(row["current_en"], en[key])
                self.assertEqual(row["status"], "FALLBACK_EN")
                self.assertEqual(row["reason_code"], "ABSENT_FROM_WORKBOOK")
                self.assertEqual(row["takeru_review"], "no")
                for column in ("sheet_row", "sheet_section", "sheet_fr", "sheet_en", "ja_validated", "ja_ai_reviewed"):
                    self.assertEqual(row[column], "")
                self.assertNotIn(key, self.ja_ts)

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
