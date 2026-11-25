# Application translation register

This directory tracks what was actually reviewed and changed in the application's FR/EN text (React catalogs, Django `.po` files, and inline strings). It does not cover the Google Sheets workbook, which is no longer updated, or Japanese.

## Three distinct concepts

| Concept | Where it is recorded |
|---|---|
| B01 decisions and corrections | `register.csv`: decision references, before/after values, statuses, and rationales. The historical proposals AC01–AC28 are archived outside the repository. |
| Value present in the code | The sources themselves (`frontend/src/i18n/*.ts`, `backend/locale/*/LC_MESSAGES/django.po`, inline source files). |
| Change actually made in the application, or a decision not to make it | `register.csv` (this directory). |

Historical approval of a proposal does not prove that the application was changed. `register.csv` distinguishes applied changes, values that already matched, deliberately preserved exceptions, and deferred corrections. `check_register.py` checks them against the current code.

## Starting baseline (batch B01)

- Git `HEAD`: `6b9813f7c2ece99ecc8fbbb67670697afceeede5`.
- Historical spreadsheet: `../cleanup/Traduction Polypbase(4).xlsx`, SHA-256 `e80b409fbea485d97fbccf66d06da83eb46374883d1e3343f50e51e656b8c307`. It is reference evidence, not a synchronized source. `sheet_value` records the cell from that version with leading and trailing spaces removed.

## `register.csv` columns

`entry_id`, `batch`, `surface` (`react`, `django`, `inline`), `key_or_msgid` (exact key or Django msgid), `language`, `source_file`, `initial_value`, `final_value`, `sheet_value`, `decision_ref`, `status`, `reason`, `baseline_git_head`, `baseline_sheet_sha256`.

For Django, the value is what the user sees: the `msgstr`, or the msgid itself if no entry exists.

Statuses:

- `APPLIED`: the value was changed in this batch (`initial_value` ≠ `final_value`).
- `ALREADY_MATCHING`: the code already contained the approved value.
- `PRESERVED`: the value differs from the spreadsheet and a decision was made to keep it.
- `DEFERRED`: reviewed but not changed because there was no approval for the application or no actual usage; revisit later.
- `NOT_FOUND`: the key or msgid does not exist in the specified source. `initial_value` and `final_value` stay empty (an empty string represents absence). Temporary restriction: `NOT_FOUND` is rejected for `surface=inline` because the checker cannot reliably prove that an inline string is absent from a file. It is still checked for `react` (key absent from the catalog) and `django` (no entry in the `.po` file). This restriction will be lifted only when presence can be detected reliably in the source.
- `REMOVED` (since B03): the key was deliberately removed from a React catalog. Applies only to `surface=react`, with the corresponding language catalog (`frontend/src/i18n/fr.ts` or `en.ts`). `initial_value` is the exact, non-empty value before removal; `final_value` is empty and means the identity is absent; `sheet_value` stays empty. The FR and EN removals of the same key form a pair in the same batch. Only `NOT_FOUND` may follow `REMOVED`: any other event, including a second `REMOVED`, contradicts the removal. A key whose last event is `REMOVED` is rejected if it reappears in the catalog. Restoring a key will require a new explicit status.

## Batch B01 (2026-10-28) at a glance

- 21 `APPLIED`: 11 EN and 3 FR in the React catalogs, 5 EN and 2 FR in the Django `.po` files.
- 5 `ALREADY_MATCHING`: AC11, AC22, AC23, AC24, AC26.
- 2 `PRESERVED`: `movementKindUnclassified` (AC25), `auditValuePendingReview` (AC28).
- 6 `DEFERRED`: AC17 (unused key), AC10 (unused Django form), React `pending_review` labels (outside the scope of AC14 approval), and the Django label `Armoire` (not exposed by the API).
- No keys were removed or restored, and no source msgids were rewritten.

## Workflow for a subsequent batch

1. Inspect the key, its usage, and the exact decision (`docs/reference/terminology-i18n.md`).
2. Change the source if the decision is confirmed and safe. Preserve `{x}` variables. After editing a `.po` file, run `cd backend; uv run python ../scripts/compile_django_messages.py`.
3. Append one row to `register.csv` for each identity reviewed, with a new `batch`, the starting `HEAD`, and sequential `entry_id` values.
4. From the repository root, run `python docs/translation-review/application/check_register.py`, then run the regression tests with `python -m unittest discover -s docs/translation-review/application -p "test_*.py"`. `--show <clé>` displays a key's history.

## Append-only history

The register is an event log. Rows in a batch that has already been recorded (including all 34 B01 rows and their decision references) are never edited, reordered, or deleted. Correct an error by adding a new event in a later batch. The test `test_h_b01_register_is_unchanged_and_valid` pins a canonical digest of the 34 B01 rows (based on field values and independent of line endings or later batches) and checks that they remain at the top of the file. Adding a batch therefore does not require changing this test; editing B01 makes it fail. `test_h_b02_register_is_unchanged` likewise pins the 86 B02 rows (T035–T120) immediately after B01.

Rules enforced by `check_register.py`:

- **Event identity**: `surface` + `key_or_msgid` + `language` + `source_file`. The same key on two surfaces or in two languages has two independent histories.
- **One event per identity per batch**, even if the events are identical: a duplicate or contradiction within a batch is rejected and cannot hide the previous event.
- **Explicit chronology**: `batch` follows `Bnn-AAAA-MM-JJ`. Each batch forms a contiguous block. Blocks are ordered by strictly increasing batch number and nondecreasing date. A number identifies only one batch. `entry_id` (`Tnnn`) increases strictly in file order. A batch declares a single `baseline_git_head`. If the order is invalid or ambiguous, continuity is not checked and the register is rejected.
- **Continuity**: for two successive events with the same identity, the new `initial_value` equals the previous `final_value`. `APPLIED` requires a value change. `ALREADY_MATCHING`, `PRESERVED`, and `DEFERRED` carry the value forward unchanged. `NOT_FOUND` has empty values, and the next event must start from absence. `REMOVED` starts from the last known value and can only be followed by `NOT_FOUND`.
- **Reading React catalogs**: strict lexical analysis without execution. Only the actual catalog structure is accepted (type imports, a single `export const` object literal, identifier or string keys, string values, and comments). Any other construct (spread, computed key, nonliteral value, interpolation, or code outside the object) fails the check. Unusual formatting cannot make a present key appear absent.
- **Current state**: only the last event for each identity is compared with the code. An older `final_value` may differ from the current code if a later event accounts for the difference.
- The checker also validates statuses, surfaces, FR/EN-only languages, digests, preservation of `{x}` variables and their occurrence counts, and FR/EN catalog parity for keys and variables.

## Removal of former administration sections (B50, 2026-11-24)

Batch `B50-2026-11-24` (`T1865–T2030`) records 83 `REMOVED` pairs from `origin/main` (`9e00e2c6e0eb581334f9238ce041a7d3d2fafb19`). It covers the 79 keys that became unused and four previously retained keys from the former Taxonomy section. Earlier exceptions unrelated to this removal remain in place. The previously recorded spreadsheet digest serves as a documentary reference; the workbook was not reviewed again.

The 78 approved Japanese values removed from the runtime catalog remain in the unchanged historical manifests. Tests distinguish absence from the UI from historical provenance. `admin-removal-test-fixture.json` contains only the row indices and digests needed to reconstruct the baseline catalogs from the register and manifest values.

## DNA: addition and subsequent removal of the status chip (2026-11-16)

The React key `dnaStateValidated` was added in FR (`Validée`) and EN (`Validated`) in the B30 batch in preparation, then removed along with the redundant chip in the popover. The addition events `T1452/T1453`, deleted in the previous session, are restored to their place in chronological order: deleting them is not a way to record a removal from the application. The subsequent events `T1454–T1457` are not renumbered.

The new batch `B31-2026-11-16` adds the `REMOVED` pair `T1458/T1459`, with the exact values before removal and an empty final value. The key remains absent from the catalogs; it was not reintroduced, and Japanese was not changed. The baseline history `T001–T1401` remains unchanged, including the CSV representation of `T841/T842`. No digests or checker rules were changed. This register update does not constitute a new frontend or browser QA result.

<a id="intégration-laboratoire-du-2026-11-05"></a>

## Laboratory integration on 2026-11-05

The 32 incoming FR/EN events from `8f7cf04` (location deletion and laboratory audit) collided with historical movement events already on `main`. The integration preserves `T001–T974` byte for byte, including thermal events `T845–T858`, laboratory events `T859–T886`, and historical events `T887–T974`.

The incoming events are appended in their original order:

- Batch `B14-2026-11-05` → `B17-2026-11-05` (date unchanged).
- IDs `T887–T918` → `T975–T1006`, inclusive: each `Tn` becomes `T(n + 88)`.
- Only `entry_id` and `batch` change. FR/EN values, sources, decisions, reasons, statuses, and baseline digests match the incoming events. The reference `B14-lab-config-inline-actions-pending-review` deliberately stays unchanged: it records provenance, not the integrated batch number.
- The 32 events remain `DEFERRED`: no wording approval or Japanese translation was added.

The canonical digest of all fields across the 32 renumbered events is `c9e9c5d39cb1f27fc2bceddd7e40436cc2d79fdc31125ceb17f06d9d48b11134` (using the same calculation as `rows_digest` in `test_check_register.py`). It protects this batch independently of the unchanged thermal, initial laboratory, and historical digests. The integrated register contains 1 006 events in 17 batches. Any later correction must be appended after this history; it must not rewrite an existing event.

## Movement and thermal integration on 2026-11-09

The local integration preserves the register from `5b1299b` (`T001–T1036`, including movements B20/B21) and adds the 26 thermal events from `ee492bd` without changing wording or provenance. The incoming thermal batch `B20-2026-11-09` becomes `B22-2026-11-09`; `T1011–T1036` become `T1037–T1062` (an exact offset of +26). Only `entry_id` and `batch` change, following the laboratory precedent above.

The three complete source registers are preserved separately in the new files `integration-2026-11-09-{base,movements,thermal}.csv`. The Git objects, digests, row-by-row mappings, and validation results are documented in [`integration-2026-11-09.md`](../../archive/2026-11-traductions/integration-2026-11-09.md). Earlier evidence remains unchanged. The tests pin the complete `HEAD` history and the thermal projection independently of the current catalogs. Any later correction must be recorded as an appended event.

## Batch B02 (2026-10-29)

Git baseline: `947105e605f979017e5915f80820ed356c411e4b`. The 34 B01 events remain unchanged at the top of the file; B02 adds **86 events, T035–T120**:

- **21 `ALREADY_MATCHING`**: 18 FR entries whose msgid is already in French, and the three EN date validation messages. The entries were absent, but gettext already displayed the same source text. Explicit coverage increases without changing the displayed text.
- **16 `APPLIED`**: a prompt to contact an administrator on seven React keys in both languages, and on the Django permission denial `The parent box is outside the active organization.` in both languages. No msgid or permission check was changed.
- **49 `DEFERRED`**: still-missing language/msgid identities reviewed for exact coverage exemptions. Revisiting a B01 deferral here documents the new scope of the check, not a new translation approval.

Approval references: the B02 writer's explicit request, parts 1A/1B (`B02-safe-identities`), parts 1C/1D and the standing rule for permission denials (`B02-permission-refusals`), and the explicit exclusion of unapproved wording (`B02-scope-deferred`). `sheet_value` stays empty for all B02 events. `baseline_sheet_sha256` retains only the fixed historical B01 reference: no new spreadsheet review, value, or approval is claimed.

The active-institution message is a `PermissionDenied` in `cultures/services.py`. The B02 events correct this classification without rewriting the historical description of T030.

### Coverage and exemptions

Production inventory: **88 distinct msgids**. Before B02: 51 missing in FR and 19 in EN. After B02: **33 in FR and 16 in EN**, or 49 missing identities, all explicitly exempted. This result does not mean that translation is complete or that all user-facing messages conform.

`scripts/check_i18n.py` distinguishes raw coverage, exempted omissions, and blocking errors. Each exemption specifies the language, msgid, source paths and occurrence counts, a reason, and a decision reference. It requires the most recent event for the exact identity (`django`, msgid, language, PO file) to be `DEFERRED`. A new omission, a different scope, an incorrect reference, or an exemption that is no longer needed causes the check to fail. This list is technical; the register remains the sole history of decisions and values.

Still deferred: the FR translations of the three date errors; the Django role `Lecture seule` / `Viewer`; the Django choices for `Armoire`, provenance, and Transplant; integrity guards without approved translations; and legacy scientific forms and choices not exposed by current clients. DRF metadata can still expose some choices. The fact that React does not display them does not prove they are inaccessible.

Also outside the scope of the corrections: `movementErrorForbidden`, shared by 401 and 403; `csvErrorPermission`; protections for workflows and the last active manager; `manualTemperatureForbidden`, for which no usage was found; camera permissions; and non-gettext transplant denials in API views. A passing check does not implicitly approve any of these cases.

### B02 checks

- Strict PO compiler for singular entries, with file and line in error messages, multiline support, and UTF-8 escapes. Plurals, contexts, fuzzy entries, obsolete entries, and unknown syntax are explicitly rejected, not ignored. All catalogs are validated before the first write.
- `--check` recompiles only in memory and compares the bytes of the tracked MO files; it never writes. CI checks the existing files without regenerating them first.
- AST extraction of Django `gettext`/`gettext_lazy` imports and literal `trans`/`translate` tags (the `as` option is supported). Tests, migrations, and caches are excluded from the production inventory. Dynamic calls, indirect references, and unsupported forms fail explicitly.
- Django placeholders: format fields with conversions and format specifications, named or positional printf parameters and their types, occurrence counts, and escapes. Percent signs in prose remain valid; the PO flags `python-format` and `c-format` enable strict printf checks. This does not provide full GNU gettext support.
- Frontend: TypeScript AST analysis without executing the catalog, parity, duplicates, empty keys or values, leading and trailing whitespace, middle dots, simple `{name}` variables and their occurrence counts, protected context-specific wording, and nine classified permission denials (seven B02, two B01).
- No blanket ban on `Organization`, `Subculture`, or `Measurement`. General terminology searches and classification of new denials remain advisory.

Validation commands are in `docs/operations/development-qa.md`. For future changes, also check the FR/EN diff against the new events: the checker validates recorded identities but cannot by itself prove that the entire diff is covered. B02 removed or restored no keys, used no `REMOVED` status, did not synchronize the spreadsheet, and did not change Japanese.

## Batch B03 (2026-10-29)

Git baseline: `f50765ee07a8e6add7db5b11652f4e141acc194d`. The 120 B01/B02 events remain unchanged. B03 adds **320 `REMOVED` events, T121–T440**, one per key and language, FR followed by EN for each key.

- Removed **160 unused React keys** from `fr.ts` and `en.ts`: 1 353 → 1 193 keys per language. The other key/value pairs, their order, and their wording are unchanged. No application component was changed.
- The exact set authorized by the B03 writer's request (`B03-safe-removal`) was revalidated before removal using the TypeScript AST across `frontend/src` and `frontend/scripts`. No string, identifier, template fragment, concatenation, or regular expression refers to these keys. Dynamic translation calls and the six untyped translator paths take their keys from literal tables. Occurrences of `prototype` are accesses to `Object.prototype`.
- `initial_value` records the exact value before removal. `sheet_value` stays empty. `baseline_sheet_sha256` retains the fixed historical B01 reference, with no new spreadsheet review or approval.
- Out of scope and retained: the 132 `csv*` keys still used by `MeasurementImportPanel` and its tests, `auditSummaryAlertResolved`, `adminTransferText`, `manualTemperatureForbidden`, candidates used only by tests, other candidates reserved for later batches, Django gettext entries, and B02 deferred translations. No general blocking rule for unused keys was introduced, and the untyped translators were not given new types.

## Final B03: three independent batches (2026-10-29)

Verified baseline: `dc69aceda71cbd6b802ef5b3090b2ccc4d8d9647`, branch `chore/i18n-b03-final`. The working tree was initially clean and matched the local `main` and `origin/main` refs. Those refs were not synchronized over the network, and no other working tree was changed.

The existing schema requires strictly increasing batch numbers (`Bnn-AAAA-MM-JJ`), so the working names B03-2, B03-3, and B03-4 map to **B04**, **B05**, and **B06**, respectively, without changing the checker or the earlier B03 batch.

| Working batch | Register ID | `decision_ref` | Events | Keys removed per language | Remaining FR / EN keys |
|---|---|---|---|---:|---:|
| B03-2 | `B04-2026-10-29` | `B03-2-audit-measurements` | T441–T502 | 31 | 1 162 / 1 162 |
| B03-3 | `B05-2026-10-29` | `B03-3-box-creation` | T503–T534 | 16 | 1 146 / 1 146 |
| B03-4 | `B06-2026-10-29` | `B03-4-test-only` | T535–T558 | 12 | 1 134 / 1 134 |

**Total: 59 removals per catalog, 118 new events, 558 events overall.** Each pair is recorded FR then EN. Initial values come from the exact Git baseline; final and spreadsheet values are empty. The spreadsheet hash is only the historical B01 reference, not evidence of a new review or approval of the spreadsheet. The final B03 writer's explicit request authorized the removals on condition that usage was revalidated.

### Candidate revalidation

Targeted inspection covered components, hooks, helpers, tests, scripts, and retained components not mounted in the application. This was followed by TypeScript AST analysis of 222 frontend source and script files, with no parse errors. Dynamic calls, literal tables, direct catalog accesses, and six untyped translator signatures were inspected without refactoring them. An independent review repeated the catalog and event comparisons against the baseline and the AST inspection of consumers.

**B03-2 — 31 `SAFE_REMOVE`, no candidates kept:**

```text
auditAccountActive auditAccountDisabled auditDescriptionFromApp
auditSummarySpeciesCreated auditSummarySpeciesUpdated
auditSummaryStrainCreated auditSummaryStrainUpdated
auditDetailOrganization auditDetailType auditDetailDescription
auditDetailValues auditDetailChanges auditCorrectionMarker
auditCorrectionDetails auditRelationInitialPolyps
manualTemperatureSaved weeklyMeasurementRecorded editLastMeasurement
editLastMeasurementHelp overviewLatestReading overviewNoMeasurement
overviewTemperature overviewWithEphyrae salinityShort zoneSalinityMissing
noZoneChart recentMeasurementMissing temperatureManualReading
temperatureContinuousReading temperatureSamples measuredTemperature
```

- `auditPresentation.ts` selects the current descriptions, metadata tables, and reactivation/deactivation labels, not these old summary and detail keys. Species names and strain codes are still interpolated into the current descriptions.
- Corrections and before/after values are still displayed by `AuditTimeline`. The full variants `auditRelationInitialPolypsZero/One/Many` remain in use and were kept. The key without a suffix is not constructed dynamically.
- `BoxPage`, `OverviewView`, `ZonesView`, and their helpers use the current record, temperature, and salinity labels. No-data states, aggregates, and historical data are unchanged.
- Occurrences of `measuredTemperature` are numeric properties or variables, including an assertion for a scientific zero in `test-alert-removal.mjs`, not translation requests. They remain unchanged.

**B03-3 — 16 `SAFE_REMOVE`, no candidates kept:**

```text
confirmDetailStrain confirmDetailLocation quickStrainSearch
createBoxGlobalCode createBoxNumber createBoxNumberMismatch createBoxOrigin
createBoxIdentityShortLocal createBoxIdentityShortSpecies
createBoxIdentityShortOrigin createBoxIdentityShortNumber
createBoxIdentityShortFormat createBoxIdentityUnusableShort
createBoxIdentityUnusable latestMovementEmpty latestMovementUnavailable
```

- `CreateBoxPanel` does not ask for an editable code or number and does not add a confirmation step. `CreatedBoxIdentifier` displays the code confirmed by the server. Validation, immutable intents, retry behavior, and freshness guards are unchanged.
- `StrainPicker` uses `createBoxStrainSearch` and filters out unusable identities without displaying these old labels for the reasons. Provenance remains searchable.
- The retained component `QuickStrainCreator` and the `boxCode` helpers and tests were also inspected. They use none of these keys and remain in place.
- `LatestMovementBanner` does not render a banner when history is empty, unavailable, or limited to lifecycle events, except for recovery feedback that uses other keys. Tests for these states and for recovery are unchanged.

**B03-4 — 12 `TEST_ONLY_SAFE`, no product decision required:**

The following seven keys are used only for negative assertions. The assertions are kept in full and do not require the translations to be present:

```text
boxInventorySelectAllFiltered boxInventorySelectVisiblePage quickStrainAdd
weeklyUpToDate zoneSummaryAlive latestCounts zoneActivityTitle
```

Only the following justified adjustments were made to the other five test dependencies:

| Key | Previous dependency | Adjustment and preserved contract |
|---|---|---|
| `manualSalinityAction` | Presence checklist in `test-zone-detail.mjs` | Only this entry was removed. Current salinity creation and correction actions and server capabilities are unchanged. |
| `temperatureMeasurementCount` | Same checklist | Only this entry was removed. The measurement count, mean versus individual measurement, available range, and distinction between zero and missing data are unchanged. |
| `zoneMovementHistoryAction` | Same checklist | Only this entry was removed. Current history entries use entry and exit directions. |
| `zoneMovementHistoryBack` | Presence checklist in `test-zone-history.mjs` | Only this entry was removed. The current button uses `back`; navigation and history coverage are unchanged. |
| `moveDialogMovedAt` | Dynamically translated list in `test-move-box-modal.mjs` | The key and its only positive expectation were removed. The date strings remain in the fixture to verify that they are absent from the form. The absence of date fields and the exact payload without a date are unchanged. |

No candidates were classified as `REFERENCED`, `UNCERTAIN`, or `TEST_ONLY_REQUIRES_DECISION` after revalidation. The 47 removals in the first two batches were based on proven absence of consumers; the 12 in the last batch were based on a close review of their test-only usage, not on a target number of removals.

### Integrity and local checks

- Events **T001–T440 are unchanged**, as verified against the baseline by fields and content, excluding checkout line-ending conversion. The B01/B02 digests remain intact. The B03 historical test now covers T121–T440 rather than the entire remainder of the file and adds their canonical digest pinned to the baseline.
- Programmatic comparison after each batch verified the exact set of removals, FR/EN parity, unchanged remaining values and order, a one-to-one correspondence between events and removals, exact initial values, pairs in the same batch, and sequential IDs. The checker and 60 register tests passed after each of the three batches.
- The 132 `csv*` keys, `auditSummaryAlertResolved`, `adminTransferText`, and `manualTemperatureForbidden` remain unchanged. No application components, Django gettext entries, Japanese text, B02 deferred translations, scientific rules, permissions, or data were changed.
- Targeted frontend tests: **511 passed** after B03-2, **472 passed** after B03-3, and **295 passed** after B03-4. These sets overlap and do not represent a total of distinct tests.
- Final validation: `npm run test:i18n` and `npm run build` passed (CSS, TypeScript, Vite). `npm run test:all` started but hit the 300-second limit after test 1 130, with no final summary: **full validation was not established**. There is no claim that 2 205 tests passed.
- Database-free gettext checks: MO compilation in `--check` mode, `check_i18n.py` inventory (0 blocking errors), 25 compiler tests and 20 inventory tests passed. Runtime tests `apps.accounts.test_i18n`: **5 passed**, with `POSTGRES_DB` empty and `config.test_settings`.
- Independent read-only review: no defects identified; 37 tests across the three changed files and 60 register tests passed. The review also confirmed that the 84 negative assertions across the six relevant test files were unchanged.

No commits, pushes, merges, cherry-picks, deployments, access to Neon or production, or spreadsheet changes. Work stopped for independent review; rerunning the complete suite with a longer timeout still requires authorization.
