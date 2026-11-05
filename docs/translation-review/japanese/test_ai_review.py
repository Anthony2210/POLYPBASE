"""AI review provenance, immutable historical Japanese and generation regressions."""
import copy

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_ja_catalogs as builder
from test_ja_manifest import ROOT, post_baseline_react_keys, rows


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


# Additions only: cbc513f thermal control and bb40685 Laboratory configuration.
# Preserve the original AI-integration source and historical-manifest fingerprints.
LATER_FALLBACK_KEYS = frozenset({
    "temperatureRecentMean", "temperatureNoRecent", "temperatureRecentPeriod", "temperatureRecentError",
    "temperatureDailyPeriod", "temperatureDailyMean", "temperatureDailyGap",
    "adminLabLocationsTitle", "adminLabAddLocation", "adminLabEditLocation", "adminLabEditTitle",
    "adminLabActiveBoxes", "adminLabMaxCapacity", "adminLabTargetTemperatureField", "adminLabReferenceSalinity",
    "adminLabInactive",
    "adminLabNoLocations", "adminLabSaved", "adminLabCapacityInvalid", "adminLabTemperatureInvalid",
    "adminLabSalinityInvalid",
})


def historical_source(source):
    lines = source.replace("\r\n", "\n").split("\n")
    prefixes = {f"  {key}: '" for key in LATER_FALLBACK_KEYS}
    additive = [line for line in lines if line.partition(": '")[0] + ": '" in prefixes]
    for prefix in prefixes:
        if sum(line.startswith(prefix) for line in additive) != 1:
            raise AssertionError(f"{prefix}: expected one additive line")
    return "\n".join(line for line in lines if line.partition(": '")[0] + ": '" not in prefixes)


class AiReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = rows("ja-manifest.csv")
        cls.fr = builder.check_register.read_catalog(ROOT / "frontend/src/i18n/fr.ts")
        cls.en = builder.check_register.read_catalog(ROOT / "frontend/src/i18n/en.ts")
        cls.reviewed = builder.read_ai_csv(builder.AI_REVIEW)
        cls.blocked = builder.read_ai_csv(builder.AI_BLOCKED)

    def historical(self):
        # Independently frozen thermal and lab additions are not baseline history.
        added = post_baseline_react_keys()
        self.assertEqual(set(added), LATER_FALLBACK_KEYS)
        records = [row for row in copy.deepcopy(self.manifest)
                   if not (row["surface"] == "react" and row["identity"] in added)]
        for row in records:
            row.pop("ja_ai_reviewed")
            if row["status"] == "AI_REVIEWED":
                row.update(status="FALLBACK_EN", reason_code="ABSENT_FROM_WORKBOOK",
                           note="Current key added or not tracked in the workbook.")
        return records

    def test_entire_historical_manifest_is_unchanged(self):
        # Baseline a841519: includes all human values, all deferred rows and Django.
        self.assertEqual(digest(self.historical()),
                         "bc098900a872d90cda3df08707409192dc8641b6a48a5425f1ba7a9a11294453")
        pairs = [[r["identity"], r["ja_validated"]] for r in self.manifest
                 if r["surface"] == "react" and r["status"] == "REUSED"]
        self.assertEqual(digest(pairs), "ab1705b630daaf621bcf152b1af3e6a7cdb098d342c5282fc2ec79ccb2330939")

    def test_later_keys_are_plain_english_fallbacks(self):
        later = [row for row in self.manifest if row["identity"] in LATER_FALLBACK_KEYS]
        self.assertEqual({row["identity"] for row in later}, LATER_FALLBACK_KEYS)
        for row in later:
            self.assertEqual((row["surface"], row["status"], row["reason_code"]),
                             ("react", "FALLBACK_EN", "ABSENT_FROM_WORKBOOK"), row["identity"])
            self.assertEqual((row["ja_validated"], row["ja_ai_reviewed"]), ("", ""), row["identity"])

    def test_exact_membership_and_separate_provenance(self):
        expected = {r["key"]: r for r in self.reviewed}
        actual = {r["identity"]: r for r in self.manifest if r["status"] == "AI_REVIEWED"}
        self.assertEqual(set(actual), set(expected))
        self.assertEqual(len(expected), len(self.reviewed))
        for key, row in actual.items():
            with self.subTest(key=key):
                source = expected[key]
                self.assertEqual(row["surface"], "react")
                self.assertEqual(row["ja_ai_reviewed"], source["ja_reviewed_candidate"])
                self.assertEqual(row["reason_code"], "AI_" + source["review_decision"])
                self.assertEqual(source["human_approved"], "NO")
                self.assertEqual(row["ja_validated"], "")
                self.assertEqual(row["sheet_row"], "")
                self.assertEqual(row["takeru_review"], "no")
        for row in self.manifest:
            if row["status"] != "AI_REVIEWED":
                self.assertEqual(row["ja_ai_reviewed"], "")

    def test_frozen_fr_en_bytes(self):
        # Removing exactly the registered post-baseline key lines must restore
        # the frozen baseline bytes; any other FR/EN change still fails.
        added = post_baseline_react_keys()
        for lang, expected in {
            "fr": "1b3486aaea5eea3f8628e96bc88dac9835d3a89da88668ca020ea9119ee757dc",
            "en": "62989721d2528126ab48704fe783597dba0d484d2f24ce05a7391e0e9bdbfa27",
        }.items():
            source = (ROOT / f"frontend/src/i18n/{lang}.ts").read_text(encoding="utf-8")
            self.assertEqual(set(added), LATER_FALLBACK_KEYS)
            self.assertEqual(hashlib.sha256(historical_source(source).encode()).hexdigest(), expected)
            for key, values in added.items():
                line = f"  {key}: '{values[lang.upper()]}',\n"
                self.assertEqual(source.count(line), 1, line)
                source = source.replace(line, "")
            self.assertEqual(hashlib.sha256(source.encode()).hexdigest(), expected)

    def test_source_fingerprint_scope_rejects_each_historical_value_change(self):
        for lang in ("fr", "en"):
            source = (ROOT / f"frontend/src/i18n/{lang}.ts").read_text(encoding="utf-8")
            fingerprint = hashlib.sha256(historical_source(source).encode()).hexdigest()
            for key in getattr(self, lang):
                line = next(line for line in source.split("\n") if line.lstrip().startswith(f"{key}: "))
                mutated = line.replace(": '", ": 'changed ", 1).replace(': "', ': "changed ', 1)
                self.assertNotEqual(mutated, line, f"{lang}.{key}: mutation must change the value")
                changed = source.replace(line, mutated, 1)
                actual = hashlib.sha256(historical_source(changed).encode()).hexdigest()
                self.assertEqual(actual == fingerprint, key in LATER_FALLBACK_KEYS, f"{lang}.{key}")
            unknown = source.replace("\n}", "\n  unapprovedAddition: 'Value',\n}", 1)
            self.assertNotEqual(hashlib.sha256(historical_source(unknown).encode()).hexdigest(), fingerprint)


    def test_authorized_files_reject_any_unreviewed_change(self):
        for source in (builder.AI_REVIEW, builder.AI_BLOCKED):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / source.name
                path.write_bytes(source.read_bytes() + b"\n")
                with self.assertRaisesRegex(SystemExit, "not the authorized"):
                    builder.read_ai_csv(path)

    def validate(self, reviewed=None, blocked=None, records=None, fr=None, en=None):
        return builder.validate_ai_review(
            self.historical() if records is None else records,
            self.fr if fr is None else fr, self.en if en is None else en,
            self.reviewed if reviewed is None else reviewed,
            self.blocked if blocked is None else blocked)

    def test_source_mismatch_fails_before_mutation(self):
        for language in ("fr", "en"):
            source = dict(getattr(self, language))
            source[self.reviewed[0]["key"]] += " changed"
            records = self.historical()
            before = copy.deepcopy(records)
            with self.assertRaisesRegex(SystemExit, "source mismatch"):
                self.validate(records=records, **{language: source})
            self.assertEqual(records, before)

    def test_invalid_reviews_are_rejected(self):
        mutations = [
            ("review_decision", "PENDING", "provenance"),
            ("human_approved", "YES", "provenance"),
            ("review_comment", "", "provenance"),
            ("ja_reviewed_candidate", "", "invalid reviewed"),
            ("ja_reviewed_candidate", "再試行·", "invalid reviewed"),
        ]
        for field, value, error in mutations:
            reviewed = copy.deepcopy(self.reviewed)
            reviewed[0][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(SystemExit, error):
                self.validate(reviewed=reviewed)
        with self.assertRaisesRegex(SystemExit, "duplicate"):
            self.validate(reviewed=self.reviewed + [self.reviewed[0]])
        with self.assertRaisesRegex(SystemExit, "blocked"):
            self.validate(reviewed=self.reviewed + [self.blocked[0]])
        reviewed = copy.deepcopy(self.reviewed)
        reviewed[0]["key"] = "deletedOrInventedKey"
        with self.assertRaisesRegex(SystemExit, "not current React"):
            self.validate(reviewed=reviewed)

    def test_human_and_deferred_entries_are_authoritative(self):
        for status in ("REUSED", "FALLBACK_EN_REVIEW"):
            target = next(r for r in self.manifest if r["surface"] == "react" and r["status"] == status)
            reviewed = copy.deepcopy(self.reviewed)
            reviewed[0]["key"] = target["identity"]
            with self.subTest(status=status), self.assertRaisesRegex(SystemExit, "cannot replace historical"):
                self.validate(reviewed=reviewed)

    def test_all_seven_revisions_reject_original_drafts(self):
        revised = [r for r in self.reviewed if r["review_decision"] == "REVISE"]
        self.assertEqual(len(revised), 7)
        for source in revised:
            reviewed = copy.deepcopy(self.reviewed)
            next(r for r in reviewed if r["key"] == source["key"])["ja_reviewed_candidate"] = source["ja_first_proposal"]
            with self.subTest(key=source["key"]), self.assertRaisesRegex(SystemExit, "original draft"):
                self.validate(reviewed=reviewed)

    def test_placeholders_glossary_and_scientific_meaning_are_guarded(self):
        def changed(key, ja, *, fr=None, en=None):
            reviewed = copy.deepcopy(self.reviewed)
            row = next(r for r in reviewed if r["key"] == key)
            row.update(ja_reviewed_candidate=ja, ja_first_proposal=ja, review_decision="ACCEPT")
            french, english = dict(self.fr), dict(self.en)
            if fr is not None:
                french[key] = row["fr_current"] = fr
            if en is not None:
                english[key] = row["en_current"] = en
            return reviewed, french, english
        cases = [
            ("probeElapsedMinutes", "分", {}, "placeholders"),
            ("probeElapsedMinutes", "{count}分 {count}", {}, "placeholders"),
            ("probeRawReading", "測定記録", {"en": "Salinity measurement"}, "biological Record"),
            ("auditDescriptionCcaEobV3Correction", "測定値72件", {}, "72 biological records"),
            ("createBoxRetry", "継代", {}, "G18"),
            ("createBoxRetry", "再試行", {"fr": "Repiquage"}, "G18"),
            ("createBoxRetry", "再試行", {"en": "Read-only mode"}, "G36"),
            ("createBoxRetry", "再試行", {"en": "Manager"}, "G33"),
            ("createBoxRetry", "再試行", {"fr": "À vérifier", "en": "Pending"}, "G28"),
            ("createBoxRetry", "再試行", {"fr": "Institution de travail"}, "G32"),
            ("createBoxRetry", "再試行", {"en": "Please contact your administrator."}, "administrator invitation"),
        ]
        for key, ja, sources, error in cases:
            reviewed, fr, en = changed(key, ja, **sources)
            with self.subTest(key=key, error=error), self.assertRaisesRegex(SystemExit, error):
                self.validate(reviewed=reviewed, fr=fr, en=en)

    def test_fallback_inventory_and_abandoned_features_stay_excluded(self):
        blocked = {r["key"] for r in self.blocked}
        react = {r["identity"]: r for r in self.manifest if r["surface"] == "react"}
        absent = {k for k, r in react.items() if r["status"] == "FALLBACK_EN"}
        deferred = {k for k, r in react.items() if r["status"] == "FALLBACK_EN_REVIEW"}
        self.assertTrue(blocked <= absent)
        self.assertFalse(blocked & deferred)
        # 137 at the AI integration, plus 7 thermal and 14 Laboratory configuration keys.
        self.assertEqual(len(absent - blocked), 158)
        added = set(post_baseline_react_keys())
        self.assertEqual(added, LATER_FALLBACK_KEYS)
        self.assertTrue(added <= absent)
        self.assertEqual(len(absent - blocked - added), 137)
        for key in react:
            if key.startswith("csv"):
                self.assertEqual(react[key]["status"], "FALLBACK_EN", key)
        obsolete = {r["identity"] for r in rows("ja-obsolete-workbook-keys.csv")}
        self.assertFalse(obsolete & set(react))

    def test_generator_reproduces_all_outputs_without_external_workbook(self):
        # Reconstruct the already-validated workbook facts, not the generated JA.
        # The actual workbook SHA and complete build are separately checked by CLI.
        entries = {}
        for row in sorted((r for r in self.manifest if r["sheet_row"]), key=lambda r: int(r["sheet_row"])):
            entries[row["identity"]] = {"row": int(row["sheet_row"]), "section": row["sheet_section"],
                "fr": row["sheet_fr"], "en": row["sheet_en"], "ja": row["ja_validated"],
                "ja_status": "Fait", "en_status": "Fait"}
        for row in rows("ja-obsolete-workbook-keys.csv"):
            entries[row["identity"]] = {"row": int(row["sheet_row"]), "section": row["sheet_section"]}
        entries = dict(sorted(entries.items(), key=lambda pair: pair[1]["row"]))
        with patch.object(builder, "read_workbook", return_value=entries):
            first = builder.build(Path("unused-test-workbook"))
            self.assertEqual(first, builder.build(Path("unused-test-workbook")))
        for path, text in first.items():
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                self.assertEqual(text, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
