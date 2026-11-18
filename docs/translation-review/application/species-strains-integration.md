# Species/strains translation integration — 2026-11-17

## Scope and sources

Local integration of `6455727` onto `5651ffb`, without commit, production access or translation generation. The original feature parent is `f50765ee07a8e6add7db5b11652f4e141acc194d`.

`register.csv` remains at 1,731 events: the complete 1,459-event main prefix is unchanged byte-for-byte, followed by the 272 reviewed feature events. Their integration IDs/batches and provenance were established in the preceding conflict resolution; this follow-up does not edit the register.

`species-strains-integration.json` locks the main register byte/field digests, the exact integrated register tail digest, 266 additive lines per language from the original feature, and the one existing-key change (`firstMeasurementOn`, EN: `First reading` → `First record`, T1714). Its SHA-256 is fixed independently in both Python and frontend tests. The old B01–B31 and catalog fingerprints are not replaced. Only those exact feature lines can be reversed before applying the old fingerprints; missing, duplicated or changed lines fail, and unknown additions remain visible.

Python register tests now expect T001–T1731, retain the old batch-count assertion on its original prefix, and append synthetic future corrections under fresh IDs/B43 instead of reusing T1460/B32. New regressions lock the entire main prefix, the entire incoming tail and the original main bytes, and reject field edits, deletion and extension of frozen batches.

## Documentary Japanese manifest

- Insert 266 new React rows in canonical FR order.
- Use `FALLBACK_EN` / `ABSENT_FROM_WORKBOOK`, empty Japanese/workbook/review fields and `takeru_review=no`.
- Do not claim a fresh workbook inspection: the private frozen workbook is not present in this checkout. These are additions absent from the existing documentary manifest, following its established fallback convention.
- Update only the registered English metadata for `firstMeasurementOn` to `First record`; retain the exact previously validated Japanese. `EN_FORM_ONLY` documents the same biological-record concept with the approved English terminology.
- Preserve all other pre-existing manifest fields. A new regression reconstructs and hashes the complete main manifest, including all non-React rows, rather than replacing its historical digest.
- `frontend/src/i18n/ja.ts` stays byte-unchanged (LF-normalized SHA-256 `fbf104463753a848ca7d2442d7689f5ac1be554ee14f0e7cc370fdf0c9ba6652`). No Japanese, AI review CSV, Django translation or translation generator output is changed.

## Source-unused copy audit

`species-strains-usage-audit.json` records the comparison before adding retention exceptions, using the same AST usage detector on 160 production source files read from `5651ffb` Git blobs, not the integrated working tree.

- Main has no unreviewed orphans or stale exceptions.
- None of the 68 integration failures is pre-existing.
- Eight existing keys lose their consumers when QuickStrainCreator/direct taxonomy strain creation is retired: four `quickStrain*` keys and `taxonomyCreateSpeciesFirst`, `taxonomyGoToSharedReferences`, `taxonomyNewStrain`, `taxonomyStrainMissingAAA`.
- Sixty newly introduced keys are not displayed by the final simplified Species workflow: retained code-change preview explanations, historical-storage labels, old location-based strain labels, filtered WoRMS-rank explanations and the compact initial-count hint.

The original labels are preserved through 68 exact-key, reasoned exceptions in `frontend/scripts/species-strains-retained-copy.mjs`. They do not claim runtime reachability or enable hidden controls. Unknown keys are not exempt; a returned production consumer or a removed catalog entry makes an exception stale and fails the checker. Tests cover these expiration rules. No meaningful code or translation was removed to silence the check.

The current scan has 1,719 FR/EN keys, 1,604 with exact production-source references, and 115 source-unreferenced keys: 47 earlier retention decisions plus the 68 explicitly reviewed here.

## Focused validation commands

From `frontend/`:

```sh
npm run check:i18n-usage
node --test scripts/test-i18n-catalogs.mjs scripts/test-i18n-types.mjs scripts/test-i18n-usage.mjs
node --test --test-name-pattern='^(ja catalog|deleted and obsolete|registered post-baseline|ja values|glossary-sensitive|FR and EN translators|Japanese create-box|Japanese probe|Japanese is selectable|local presentation|box status labels|Japanese dates|the error screen|additive keys|original FR and EN|Species additions|original review inputs|historical human translations|AI-reviewed runtime|all seven REVISE|all six BLOCK)' scripts/test-i18n-japanese.mjs
```

From the repository root:

```sh
python docs/translation-review/application/check_register.py
python -m unittest discover -s docs/translation-review/application -p test_check_register.py -k test_later_corrections_must_be_appended -v
python -m unittest discover -s docs/translation-review/application -p test_check_register.py -k test_integration_has_exact_count -v
python -m unittest discover -s docs/translation-review/application -p test_check_register.py -k test_species_integration -v
python -m unittest discover -s docs/translation-review/japanese -p test_species_strains_manifest.py -v
```

The frontend commands pass respectively with no usage errors, 246 core tests, and 22 selected Japanese tests. The three selected integration-register tests, the B30/B31 source/history mutation test, four new documentary-manifest tests and the existing current-identity coverage test pass. These are scoped checks, not a claim that every historical translation mutation suite or generator check has completed. Broad application suites need not be rerun for this follow-up. The private-workbook generator (including `--check`) is deliberately not invoked.
