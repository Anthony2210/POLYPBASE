"""Species integration: documentary English fallbacks, without Japanese generation."""
import csv
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'docs/translation-review/application'))
from test_check_register import (  # noqa: E402
    ADMIN_RETIRED_KEYS, B49_ADDED_KEYS, B49_REMOVED_KEYS, SPECIES_EVIDENCE,
    b44_events, b45_events, b47_events, b48_events, before_admin_removal,
    before_species_integration, pre_b50_catalog_source,
)
import check_register as reg  # noqa: E402

# Main 5e28969 manifest (adds the five B32 thermalMonitoring rows); 5651ffb digest kept for the older prefix.
MAIN_MANIFEST_SHA256 = 'd6c48d94c2362f7a04a64463b9460a7b8944e35d178e08e5f9af238357200d94'
PRE_THERMAL_MAIN_MANIFEST_SHA256 = '6084ae89dacf2e834371941a8b4f9c452f5be76bda1d32b96c06f63325e9afdd'
THERMAL_B32_KEYS = ('thermalMonitoringPeriod', 'thermalMonitoringDeviation', 'thermalMonitoringExtrema',
                    'thermalMonitoringEmpty', 'thermalMonitoringKeyboard')
MAIN_JA_SHA256 = 'fbf104463753a848ca7d2442d7689f5ac1be554ee14f0e7cc370fdf0c9ba6652'


class SpeciesStrainsManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with Path(__file__).with_name('ja-manifest.csv').open(encoding='utf-8', newline='') as stream:
            cls.manifest = list(csv.DictReader(stream))
        # Species additions still present after B49 removed four of them.
        cls.added = {line.strip().split(':', 1)[0] for line in SPECIES_EVIDENCE['catalog_additions']['fr']}
        cls.added -= set(B49_REMOVED_KEYS)

    def test_exact_new_membership_current_values_and_plain_english_fallback(self):
        fr = reg.read_catalog(ROOT / 'frontend/src/i18n/fr.ts')
        en = reg.read_catalog(ROOT / 'frontend/src/i18n/en.ts')
        react = [row for row in self.manifest if row['surface'] == 'react']
        rows = reg.load_register()
        unmanifested = {row['key_or_msgid'] for events in (b44_events, b45_events, b47_events, b48_events)
                        for row in events(rows) if row['status'] != 'REMOVED'}
        self.assertEqual([row['identity'] for row in react if row['identity'] not in ADMIN_RETIRED_KEYS],
                         [key for key in fr if key not in unmanifested])
        self.assertEqual(set(fr), set(en))
        added = [row for row in react if row['identity'] in self.added]
        self.assertEqual(len(added), 266 - len(B49_REMOVED_KEYS))
        self.assertFalse(set(B49_REMOVED_KEYS) & {row['identity'] for row in react})
        for key in B49_ADDED_KEYS:
            row = next(row for row in react if row['identity'] == key)
            self.assertEqual((row['current_fr'], row['current_en']), (fr[key], en[key]), key)
            self.assertEqual((row['status'], row['reason_code'], row['takeru_review'], row['ja_validated'],
                              row['ja_ai_reviewed']), ('FALLBACK_EN', 'ABSENT_FROM_WORKBOOK', 'no', '', ''), key)
        self.assertEqual({row['identity'] for row in added}, self.added)
        baseline = {language: reg.parse_catalog(pre_b50_catalog_source(language, rows), language)
                    for language in ('fr', 'en')}
        for row in added:
            key = row['identity']
            self.assertEqual((row['current_fr'], row['current_en']),
                             (baseline['fr'][key], baseline['en'][key]), key)
            self.assertEqual((row['status'], row['reason_code'], row['takeru_review']),
                             ('FALLBACK_EN', 'ABSENT_FROM_WORKBOOK', 'no'), key)
            for field in ('sheet_row', 'sheet_section', 'sheet_fr', 'sheet_en', 'ja_validated', 'ja_ai_reviewed'):
                self.assertEqual(row[field], '', (key, field))

    def test_all_main_manifest_fields_remain_frozen_except_registered_english_metadata(self):
        original = [dict(row) for row in self.manifest
                    if not (row['surface'] == 'react' and row['identity'] in self.added | set(B49_ADDED_KEYS))]
        changed = next(row for row in original if row['identity'] == 'firstMeasurementOn')
        self.assertEqual(changed['current_en'], 'First record')
        self.assertEqual(changed['reason_code'], 'EN_FORM_ONLY')
        self.assertEqual(changed['note'], 'Register T1724 uses Record for the same biological record; existing validated Japanese is unchanged.')
        changed.update(current_en='First reading', reason_code='EXACT_MATCH', note='')
        digest = hashlib.sha256(json.dumps(original, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(digest, MAIN_MANIFEST_SHA256)
        thermal = [row for row in original if row['identity'] in THERMAL_B32_KEYS]
        self.assertEqual([row['identity'] for row in thermal], list(THERMAL_B32_KEYS))
        before_thermal = [row for row in original if row['identity'] not in THERMAL_B32_KEYS]
        digest = hashlib.sha256(json.dumps(before_thermal, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(digest, PRE_THERMAL_MAIN_MANIFEST_SHA256)

    def test_historical_japanese_is_frozen_and_retired_keys_do_not_ship(self):
        source = (ROOT / 'frontend/src/i18n/ja.ts').read_text(encoding='utf-8')
        validated = {row['identity']: row['ja_validated'] for row in self.manifest
                     if row['surface'] == 'react' and row['status'] == 'REUSED'}
        historical = before_admin_removal(source, 'JA', [], japanese_values=validated)
        self.assertEqual(hashlib.sha256(historical.encode('utf-8')).hexdigest(), MAIN_JA_SHA256)
        ja = reg.parse_catalog(source, 'ja')
        self.assertFalse((self.added | set(B49_ADDED_KEYS) | ADMIN_RETIRED_KEYS.keys()) & ja.keys())
        self.assertEqual(len(validated.keys() & ADMIN_RETIRED_KEYS.keys()), 78)

    def test_feature_reconstruction_keeps_unknown_changes_and_rejects_changed_values(self):
        rows = reg.load_register()
        for language in ('fr', 'en'):
            source = pre_b50_catalog_source(language, rows)
            restored = before_species_integration(source, rows, language)
            extra = "  unknownAddition: 'Unapproved',\n"
            self.assertEqual(before_species_integration(source + extra, rows, language), restored + extra)
            for change in SPECIES_EVIDENCE['catalog_changes'][language]:
                with self.assertRaises(AssertionError):
                    before_species_integration(source.replace(change['after'], change['before']), rows, language)


if __name__ == '__main__':
    unittest.main()
