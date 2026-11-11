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
# Registered FR/EN additions (B25) with no Japanese, outside the frozen baselines.
ZONE_CHART_KEYS = json.loads((HERE / "zone-chart-keys.json").read_text(encoding="utf-8"))
# Registered FR/EN additions (B26): compact probe chart period control, no Japanese.
THERMAL_PERIOD_KEYS = json.loads((HERE / "thermal-period-keys.json").read_text(encoding="utf-8"))
# Registered FR/EN additions (B27): inter-probe average curve, no Japanese.
PROBE_AVERAGE_KEYS = json.loads((HERE / "probe-average-keys.json").read_text(encoding="utf-8"))
# B20/B21 are distinct from the 44 older historical movement additions.
POTENTIAL_MOVEMENT_KEYS = (
    "historyPotentialMovement", "historyPotentialAfterRecord", "historyPotentialUnordered",
    "historyHistoricalMovement", "historyBetweenRecords", "historyBetweenRecordsAcrossYears",
    "historyEnvironmentOnly", "historyTransitionReview", "zoneMovementHistoricalEntry",
    "zoneMovementHistoricalExit", "zoneMovementInterval", "zoneMovementIntervalAcrossYears",
    "zoneMovementHistoricalTotals",
)
sys.path.insert(0, str(ROOT / "docs/translation-review/application"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_i18n  # noqa: E402
import check_register  # noqa: E402
import compile_django_messages  # noqa: E402
from test_check_register import (  # noqa: E402
    B13_POSITION, B26_KEYS, B27_KEYS, LAB_POSITION, b13_integrity_errors, b24_events, b26_events, b27_events,
    before_b26, before_b27, lab_integrity_errors, rows_digest,
)


B23_RETIRED_KEYS = frozenset({
    "historyEnvironmentOnly", "historyTransitionReview", "historyProvenance", "historyReconciliation",
})
B23_ADDED_KEYS = frozenset({
    "historyRecordedViaPolypbase", "historyStartingLocation",
})
B24_ADDED_KEYS = frozenset({
    "historyReactivation", "historyLocationExit", "historyLocationEntry",
})


def b25_events(register):
    """Freeze the ID/batch-only projection of incoming b8237be thermal events."""
    events = [row for row in register if row["batch"] == "B25-2026-11-10"]
    assert events == register[1060:1086], "B25: events moved or gained a member"
    assert [row["entry_id"] for row in events] == [f"T{i}" for i in range(1061, 1087)], "B25: identities changed"
    assert all(list(row) == check_register.COLUMNS for row in events), "B25: schema changed"
    assert rows_digest(events) == "0d5243d50bebfc735a5627c768e4973d426b97843038b0e71ca80c98ab92e241", "B25: history was modified"
    assert [(row["key_or_msgid"], row["language"]) for row in events] == [
        (key, language) for key in ZONE_CHART_KEYS for language in ("FR", "EN")
    ], "B25: thermal keys changed"
    return events


def before_b25(source, register, language):
    """Remove only exact registered thermal additions, retaining unknown changes."""
    for row in b25_events(register):
        if row["language"] == language:
            line = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
            assert source.count(line) == 1, row["key_or_msgid"]
            source = source.replace(line, "")
    return source


def rows(name):
    with (HERE / name).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


# Commit whose FR/EN bytes and manifest the historical fingerprints freeze.
FROZEN_BASELINE = "c857a592e170ae6933969098c90fed95d0eb3ea5"
# Historical additions as renumbered in 7c8ce24, independent of thermal and lab.
HISTORICAL_POSITION = slice(886, 974)
HISTORICAL_ENTRY_IDS = [f"T{number}" for number in range(887, 975)]
HISTORICAL_ROWS_SHA256 = "df3d1228fa718616598ea8bf5c87f2a600007cc94a7514db773abc150960b5c7"
# Incoming 8f7cf04 events: only IDs and batch are renumbered after main history.
LAB_REFINEMENT_POSITION = slice(974, 1006)
LAB_REFINEMENT_ENTRY_IDS = [f"T{number}" for number in range(975, 1007)]
LAB_REFINEMENT_BATCH = "B17-2026-11-05"
LAB_REFINEMENT_ROWS_SHA256 = "c9e9c5d39cb1f27fc2bceddd7e40436cc2d79fdc31125ceb17f06d9d48b11134"


def post_baseline_react_keys(register=None):
    """React keys registered as new FR/EN pairs after the frozen baseline.

    Only pure additions qualify: any other registered change since the
    baseline fails here instead of being silently excluded from history.
    Thermal and lab events share B13 and the baseline but have independent
    fingerprints; neither can redefine what is excluded by a coordinated edit.
    The explicit 44 historical keys and 16 lab-refinement keys each have
    separate frozen event histories.
    """
    register = check_register.load_register() if register is None else register
    if any(list(row) != check_register.COLUMNS for row in register):
        raise AssertionError("unexpected register schema")
    integrity = b13_integrity_errors(register) + lab_integrity_errors(register)
    if integrity:
        raise AssertionError("; ".join(integrity))
    historical = [r for r in register if r["entry_id"] in HISTORICAL_ENTRY_IDS]
    if (historical != register[HISTORICAL_POSITION]
            or [r["entry_id"] for r in historical] != HISTORICAL_ENTRY_IDS
            or rows_digest(historical) != HISTORICAL_ROWS_SHA256):
        raise AssertionError("historical movement history was modified")
    refinement = [r for r in register if r["entry_id"] in LAB_REFINEMENT_ENTRY_IDS]
    if (refinement != register[LAB_REFINEMENT_POSITION]
            or [r["entry_id"] for r in refinement] != LAB_REFINEMENT_ENTRY_IDS
            or rows_digest(refinement) != LAB_REFINEMENT_ROWS_SHA256
            or [r for r in register if r["batch"] == LAB_REFINEMENT_BATCH] != refinement):
        raise AssertionError("lab refinement history was modified")
    entries = [r for r in register if r["baseline_git_head"] == FROZEN_BASELINE]
    thermal_lab = register[B13_POSITION] + register[LAB_POSITION]
    if (entries != thermal_lab + historical + refinement
            or [r for r in register if r["batch"] == "B13-2026-11-04"] != thermal_lab):
        raise AssertionError("only the frozen thermal, lab, historical and refinement events may use the frozen baseline; B13 is thermal/lab only")
    historical_keys = list(dict.fromkeys(r["key_or_msgid"] for r in historical))
    if (set(historical_keys) != set(HISTORICAL_MOVEMENT_KEYS)
                or len(historical_keys) != 44 or len(HISTORICAL_MOVEMENT_KEYS) != 44):
        raise AssertionError("expected exactly the explicit 44 historical movement keys")
    languages = {}
    for entry in entries:
        thermal = entry in register[B13_POSITION]
        movement = entry in historical
        expected_status = "ALREADY_MATCHING" if thermal or movement or entry["key_or_msgid"] == "adminLabReferenceSalinity" else "DEFERRED"
        reason = "New FR/EN" if movement else "New key added" if thermal else "New Administration > Laboratory configuration key"
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
        extra = dict(original[858], entry_id="T1007", batch="B18-2026-11-05")
        with self.assertRaisesRegex(AssertionError, "only the frozen thermal, lab, historical and refinement events"):
            post_baseline_react_keys(original + [extra])

    def test_lab_history_cannot_be_omitted_reordered_or_promoted_to_approval(self):
        original = check_register.load_register()
        self.assertEqual(len(post_baseline_react_keys(original)), 81)
        variants = {
            "deleted lab": original[:858],
            "missing FR": original[:858] + original[859:],
            "reordered": original[:858] + [original[859], original[858]] + original[860:],
            "duplicate": original + [dict(original[858])],
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

    def test_historical_events_and_full_schema_cannot_redefine_the_reconstruction(self):
        original = check_register.load_register()
        for index in range(HISTORICAL_POSITION.start, HISTORICAL_POSITION.stop):
            for field in check_register.COLUMNS:
                changed = [dict(row) for row in original]
                changed[index][field] += "x"
                with self.subTest(entry=original[index]["entry_id"], field=field), self.assertRaises(AssertionError):
                    post_baseline_react_keys(changed)
        variants = [original[:886], original[:887] + original[888:],
                    original[:886] + [original[887], original[886]] + original[888:],
                    original + [dict(original[-1])]]
        for index in (0, 844, 858, 886, 974):
            extra = [dict(row) for row in original]
            extra[index]["unknown_column"] = "unapproved"
            variants.append(extra)
            missing = [dict(row) for row in original]
            missing[index].pop("sheet_value")
            variants.append(missing)
        for register in variants:
            with self.assertRaises(AssertionError):
                post_baseline_react_keys(register)
        # Appended corrections do not rewrite original events or expand exclusions.
        correction = dict(original[855], entry_id="T1007", batch="B18-2026-11-05",
                          initial_value="Daily mean", final_value="Daily average",
                          status="APPLIED", baseline_git_head="2" * 40)
        self.assertEqual(post_baseline_react_keys(original + [correction]), post_baseline_react_keys(original))

    def test_lab_refinement_history_cannot_redefine_the_reconstruction(self):
        original = check_register.load_register()
        for index in range(LAB_REFINEMENT_POSITION.start, LAB_REFINEMENT_POSITION.stop):
            for field in check_register.COLUMNS:
                changed = [dict(row) for row in original]
                changed[index][field] += "x"
                with self.subTest(entry=original[index]["entry_id"], field=field), self.assertRaises(AssertionError):
                    post_baseline_react_keys(changed)
        for register in (
            original[:974], original[:975] + original[976:],
            original[:974] + [original[975], original[974]] + original[976:],
            original + [dict(original[-1])],
            original + [dict(original[-1], entry_id="T1007")],
        ):
            with self.assertRaises(AssertionError):
                post_baseline_react_keys(register)

    def test_post_baseline_keys_fall_back_to_english_without_japanese(self):
        added = post_baseline_react_keys()
        self.assertEqual(len(added), 81)
        en = check_register.read_catalog(ROOT / "frontend/src/i18n/en.ts")
        react = {r["identity"]: r for r in self.by("react")}
        for key, values in added.items():
            with self.subTest(key=key):
                if key in B23_RETIRED_KEYS:
                    for catalog in (self.fr, en, self.ja_ts, react):
                        self.assertNotIn(key, catalog)
                    continue
                self.assertEqual((self.fr[key], en[key]), (values["FR"], values["EN"]))
                self.assertEqual((react[key]["current_fr"], react[key]["current_en"]), (values["FR"], values["EN"]))
                row = react[key]
                self.assertEqual((row["status"], row["reason_code"]), ("FALLBACK_EN", "ABSENT_FROM_WORKBOOK"))
                self.assertEqual((row["sheet_row"], row["ja_validated"], row["ja_ai_reviewed"]), ("", "", ""))
                self.assertNotIn(key, self.ja_ts)

    def test_b23_retirements_and_additions_have_exact_provenance(self):
        register = check_register.load_register()
        events = [row for row in register if row["batch"] == "B23-2026-11-10"]
        self.assertEqual(events, register[1042:1054])
        self.assertEqual([row["entry_id"] for row in events], [f"T{i}" for i in range(1043, 1055)])
        self.assertEqual(rows_digest(events),
                         "e923b831b624a2367b92bcfd9ba3b55eec575f1ec9241333d632920dc14b8423")
        en = check_register.read_catalog(ROOT / "frontend/src/i18n/en.ts")
        react = {row["identity"]: row for row in self.by("react")}
        self.assertEqual({row["key_or_msgid"] for row in events if row["status"] == "REMOVED"}, B23_RETIRED_KEYS)
        self.assertEqual({row["key_or_msgid"] for row in events if row["status"] == "ALREADY_MATCHING"}, B23_ADDED_KEYS)
        for key in B23_RETIRED_KEYS:
            for catalog in (self.fr, en, self.ja_ts, react):
                self.assertNotIn(key, catalog)
        for event in events:
            key = event["key_or_msgid"]
            if key not in B23_ADDED_KEYS:
                continue
            row = react[key]
            language = event["language"].lower()
            self.assertEqual((self.fr if language == "fr" else en)[key], event["final_value"])
            self.assertEqual(row[f"current_{language}"], event["final_value"])
            self.assertEqual((row["status"], row["reason_code"], row["takeru_review"]),
                             ("FALLBACK_EN", "ABSENT_FROM_WORKBOOK", "no"))
            for field in ("sheet_row", "sheet_section", "sheet_fr", "sheet_en", "ja_validated", "ja_ai_reviewed"):
                self.assertEqual(row[field], "")
            self.assertNotIn(key, self.ja_ts)

    def test_b26_additions_have_exact_provenance(self):
        self.assertEqual(tuple(THERMAL_PERIOD_KEYS), B26_KEYS)
        events = b26_events(check_register.load_register())
        self.assertEqual({row["status"] for row in events}, {"ALREADY_MATCHING"})
        en = check_register.read_catalog(ROOT / "frontend/src/i18n/en.ts")
        react = {row["identity"]: row for row in self.by("react")}
        for event in events:
            key = event["key_or_msgid"]
            row = react[key]
            language = event["language"].lower()
            self.assertEqual(event["initial_value"], event["final_value"])
            self.assertEqual((self.fr if language == "fr" else en)[key], event["final_value"])
            self.assertEqual(row[f"current_{language}"], event["final_value"])
            self.assertEqual((row["surface"], row["status"], row["reason_code"], row["takeru_review"]),
                             ("react", "FALLBACK_EN", "ABSENT_FROM_WORKBOOK", "no"))
            for field in ("sheet_row", "sheet_section", "sheet_fr", "sheet_en", "ja_validated", "ja_ai_reviewed"):
                self.assertEqual(row[field], "")
            self.assertNotIn(key, self.ja_ts)

    def test_b27_additions_have_exact_provenance(self):
        self.assertEqual(tuple(PROBE_AVERAGE_KEYS), B27_KEYS)
        events = b27_events(check_register.load_register())
        self.assertEqual({row["status"] for row in events}, {"ALREADY_MATCHING"})
        en = check_register.read_catalog(ROOT / "frontend/src/i18n/en.ts")
        react = {row["identity"]: row for row in self.by("react")}
        for event in events:
            key = event["key_or_msgid"]
            row = react[key]
            language = event["language"].lower()
            self.assertEqual(event["initial_value"], event["final_value"])
            self.assertEqual((self.fr if language == "fr" else en)[key], event["final_value"])
            self.assertEqual(row[f"current_{language}"], event["final_value"])
            self.assertEqual((row["surface"], row["status"], row["reason_code"], row["takeru_review"]),
                             ("react", "FALLBACK_EN", "ABSENT_FROM_WORKBOOK", "no"))
            for field in ("sheet_row", "sheet_section", "sheet_fr", "sheet_en", "ja_validated", "ja_ai_reviewed"):
                self.assertEqual(row[field], "")
            self.assertNotIn(key, self.ja_ts)

    def test_b24_additions_have_exact_provenance(self):
        events = b24_events(check_register.load_register())
        self.assertEqual({row["key_or_msgid"] for row in events}, B24_ADDED_KEYS)
        self.assertEqual({row["status"] for row in events}, {"ALREADY_MATCHING"})
        en = check_register.read_catalog(ROOT / "frontend/src/i18n/en.ts")
        react = {row["identity"]: row for row in self.by("react")}
        for event in events:
            key = event["key_or_msgid"]
            row = react[key]
            language = event["language"].lower()
            self.assertEqual((self.fr if language == "fr" else en)[key], event["final_value"])
            self.assertEqual(row[f"current_{language}"], event["final_value"])
            self.assertEqual((row["surface"], row["status"], row["reason_code"], row["takeru_review"]),
                             ("react", "FALLBACK_EN", "ABSENT_FROM_WORKBOOK", "no"))
            for field in ("sheet_row", "sheet_section", "sheet_fr", "sheet_en", "ja_validated", "ja_ai_reviewed"):
                self.assertEqual(row[field], "")
            self.assertNotIn(key, self.ja_ts)

    def test_exact_counts(self):
        # Historical counts are frozen; only registered post-baseline keys are added.
        self.assertEqual(Counter((r["surface"], r["status"]) for r in self.manifest), Counter({
            ("react", "REUSED"): 930, ("react", "AI_REVIEWED"): 59,
            ("react", "FALLBACK_EN"): 257, ("react", "FALLBACK_EN_REVIEW"): 36,
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
        self.assertEqual([r["identity"] for r in added],
                         [key for key in HISTORICAL_MOVEMENT_KEYS if key not in B23_RETIRED_KEYS])
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

    def test_recent_movement_and_thermal_additions_are_exact_separate_fallbacks(self):
        register = check_register.load_register()
        en = check_register.read_catalog(ROOT / "frontend/src/i18n/en.ts")
        self.assertEqual(len(ZONE_CHART_KEYS), 13)
        self.assertEqual(len(set(ZONE_CHART_KEYS)), 13)
        self.assertTrue(set(POTENTIAL_MOVEMENT_KEYS).isdisjoint(ZONE_CHART_KEYS))
        historical = post_baseline_react_keys(register)
        self.assertEqual(len(historical), 81)
        for batches, keys, start in (
            ({"B20-2026-11-09", "B21-2026-11-09"}, POTENTIAL_MOVEMENT_KEYS, 1011),
            ({"B25-2026-11-10"}, ZONE_CHART_KEYS, 1061),
        ):
            self.assertTrue(set(keys).isdisjoint(historical))
            events = [r for r in register if r["batch"] in batches]
            self.assertEqual([r["entry_id"] for r in events], [f"T{n}" for n in range(start, start + 26)])
            self.assertEqual([(r["key_or_msgid"], r["language"]) for r in events],
                             [(key, lang) for key in keys for lang in ("FR", "EN")])
            for event in events:
                self.assertEqual((event["surface"], event["status"], event["initial_value"]),
                                 ("react", "ALREADY_MATCHING", event["final_value"]))
                if event["key_or_msgid"] in B23_RETIRED_KEYS:
                    self.assertNotIn(event["key_or_msgid"], self.fr)
                    self.assertNotIn(event["key_or_msgid"], en)
                    continue
                # B22 appends corrections to three movement labels, not to thermal keys.
                latest = next(r for r in reversed(register)
                              if check_register.identity(r) == check_register.identity(event))
                self.assertEqual(latest["final_value"], (self.fr if event["language"] == "FR" else en)[event["key_or_msgid"]])
            added = [r for r in self.by("react") if r["identity"] in keys]
            self.assertEqual([r["identity"] for r in added], [key for key in keys if key not in B23_RETIRED_KEYS])
            for row in added:
                key = row["identity"]
                self.assertEqual((row["current_fr"], row["current_en"]), (self.fr[key], en[key]))
                self.assertEqual((row["status"], row["reason_code"], row["takeru_review"]),
                                 ("FALLBACK_EN", "ABSENT_FROM_WORKBOOK", "no"))
                for column in ("sheet_row", "sheet_section", "sheet_fr", "sheet_en", "ja_validated", "ja_ai_reviewed"):
                    self.assertEqual(row[column], "", f"{key}.{column}")
                self.assertNotIn(key, self.ja_ts)

    def test_b25_reconstruction_rejects_rewritten_history_and_source_lines(self):
        original = check_register.load_register()
        events = b25_events(original)
        for language in ("FR", "EN"):
            source = (ROOT / check_register.REACT_CATALOGS[language]).read_text(encoding="utf-8")
            before_b25(source, original, language)
            for event in events:
                with self.subTest(entry=event["entry_id"], language=language):
                    with self.assertRaises(AssertionError):
                        before_b25(source, [row for row in original if row is not event], language)
                    for field in check_register.COLUMNS:
                        changed = [dict(row) for row in original]
                        changed[original.index(event)][field] += " changed"
                        with self.assertRaises(AssertionError):
                            before_b25(source, changed, language)
                    if event["language"] != language:
                        continue
                    line = f"  {event['key_or_msgid']}: '{event['final_value']}',\n"
                    for replacement in ("", line + line, line.replace(": '", ": 'changed ")):
                        with self.assertRaises(AssertionError):
                            before_b25(source.replace(line, replacement), original, language)
        variants = [original + [dict(events[0], entry_id="T1087")]]
        reordered = original[:]
        reordered[1060], reordered[1061] = reordered[1061], reordered[1060]
        variants.append(reordered)
        for field in ("unknown_column", "sheet_value"):
            changed = [dict(row) for row in original]
            if field == "unknown_column":
                changed[1060][field] = "unapproved"
            else:
                changed[1060].pop(field)
            variants.append(changed)
        for changed in variants:
            with self.assertRaises(AssertionError):
                b25_events(changed)
        correction = dict(events[0], entry_id="T1087", batch="B26-2026-11-10", status="APPLIED",
                          initial_value=events[0]["final_value"], final_value="Later correction")
        self.assertEqual(b25_events(original + [correction]), events)

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
