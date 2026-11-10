# Combined integration validation — 2026-11-10

## State and boundaries

- HEAD: `5b1299b02b5a84be8a6008cb9d9233bc9c7aed5e`.
- Incoming CHERRY_PICK_HEAD: `ee492bd4d591b787d9147e913531a89e015ed9dc`.
- Common base: `eea90d12ab6ece48a5f75635a4b58912befa86bf`.
- No staging, commit, cherry-pick continuation/abort/skip, merge, push or deployment.
- No production, Neon or shared database access. PostgreSQL testing used fresh
  disposable local clusters, verified as loopback-only and cleaned afterwards.

## Resolutions

Four textual conflicts were encountered, not seven:

1. `application/register.csv`: original movement history preserved; incoming
   thermal rows appended with ID +26 and batch B22 only. Exact mapping, original
   snapshots and verified canonical hashes are in `integration-2026-11-09.md`.
2. `japanese/test_ai_review.py`: preserve original historical fingerprints while
   excluding movement B20/B21 and incoming thermal B22 additions separately.
3. `japanese/test_ja_manifest.py`: combine counts and verify exact membership,
   language pairs, ID ranges and untranslated provenance for both 13-key sets.
4. `frontend/scripts/test-i18n-japanese.mjs`: reconstruct historical catalogs by
   excluding the two independent sets exactly once; regression checks detect
   missing and duplicate additions.

Additional working-tree changes beyond textual conflicts:

- `application/test_check_register.py`: distinguish thermal B22 from movement
  B20/B21 during baseline reconstruction, plus eight source/mapping/mutation
  integrity tests. No historical fingerprint was replaced.
- `application/README.md`, three original source CSV snapshots and the two
  integration documents: additive evidence and mapping documentation.
- `frontend/scripts/i18n-usage.mjs`: retain incoming thermal exceptions and the
  movement key `historyPartial`, whose final consumer was removed by 5b1299b.
- `frontend/scripts/test-contextual-subviews.mjs`: three coexistence regressions
  for success, chart failure and history failure, with zero measurement and
  historical interval semantics.

Automatically merged feature implementations were preserved. Reviewed backend
historical files match movement HEAD, and the six modified backend probe files
match incoming thermal source (allowing checkout line endings). Reviewed frontend
catalogs, types, styling and zone composition retain both features. No scientific,
permission, lifecycle, concurrency or thermal-alert rule was added.

## Provenance outcome

- Register: 1,062 events / 22 batches, unique ordered IDs T001–T1062.
- Original main T001–T1036 retained as exact original LF-byte prefix.
- Thermal source T1011–T1036 becomes T1037–T1062 in B22-2026-11-09.
- Only event ID and batch change; all other source fields remain exact.
- Manifest React statuses: REUSED 930; AI_REVIEWED 59; FALLBACK_EN 250;
  FALLBACK_EN_REVIEW 36.
- Both disjoint sets of 13 new entries remain FALLBACK_EN /
  ABSENT_FROM_WORKBOOK. Original manifest rows and reviewed Japanese unchanged.
- `post_baseline_react_keys()` remains byte-for-byte unchanged, with its historical
  81-key scope.

## Completed validation

| Check | Result |
|---|---|
| Register CLI | PASS: 1,062 events, 22 batches |
| Application register tests | 79 passed |
| Focused Japanese integration checks | 5 passed |
| Python Japanese combined suite | 28 run: 21 passed, 6 failures, 1 error; see below |
| Frontend i18n suite | 275 passed |
| Frontend i18n usage | PASS |
| Contextual subviews, including new combined tests | 23 passed |
| Full frontend discovery, serialized | 2,575 passed, 0 failed/cancelled/skipped |
| TypeScript | PASS |
| CSS | PASS: 32 files / 13,029 non-empty lines |
| Production build | PASS: Vite transformed 2,262 modules |
| Historical/location/movement SQLite suite | 257 run: 256 passed, 1 PostgreSQL-specific skip |
| Probe/thermal SQLite suite | 179 run: 164 passed, 15 PostgreSQL-specific skips |
| Django preflight | PASS |
| Migration drift preflight | PASS: No changes detected |
| Disposable PostgreSQL concurrency assertions | 15 run, all passed; command teardown timed out |
| Working-tree and cached diff checks | PASS; LF/CRLF warning only |

Test counts from focused frontend sets overlap the full suite and are not added.
Frontend dependencies/build outputs were confined to an external disposable
snapshot; source equality with the worktree was verified. No lockfile change.

The normal frontend runner timed out at 240 seconds and again at 540 seconds,
without a completed summary. A diagnostic parallel run also timed out, including
a per-test timeout in test-box-read-freshness.mjs. The final run used the existing
`discoverTestEntries()` coverage with `--test-concurrency=1 --test-reporter=tap`:
all 98 modules / 92 deduplicated entry points completed in 440.55 seconds, within
a 600-second bound. These earlier partial runs are not reported as passes.

Backend commands used the existing `.venv/Scripts/python.exe`, explicit
`POLYPBASE_TEST_POSTGRES=0 DJANGO_DEBUG=1 PYTHONDONTWRITEBYTECODE=1`, and
`--settings=config.test_settings --noinput`. Modules:

- Historical: apps.cultures.test_historical_location_transitions,
  test_zone_history_api, test_box_lifecycle, test_box_movement_corrections,
  test_box_movement_weekly, test_box_movement_week_integrity,
  test_historical_movements, test_location_history_diagnostic.
- Probe: apps.measurements.test_probe_collection, test_probe_coverage,
  test_probe_integrity, test_probe_persistence, test_probe_replacement,
  test_thermal_control, test_set_probe_sampling_interval, test_probe_concurrency.
- `manage.py check --settings=config.preflight_settings`.
- `manage.py makemigrations --check --dry-run --settings=config.preflight_settings`.

## Independently reproduced prior Japanese failures

The exact same six failures and one error were reproduced on both 5b1299b and
 ee492bd (27 tests each: 20 passed), using their actual Git-source snapshots. The
integrated suite adds one passing test, not a new failure. Existing evidence was
not rewritten to silence them.

| Test | Reproduced cause |
|---|---|
| test_generator_reproduces_all_outputs_without_external_workbook | SystemExit: movementKindOrdinary requires a reviewed FR/EN decision |
| test_later_keys_are_plain_english_fallbacks | historyAcceptedReconstruction historical/current wording mismatch |
| test_original_fr_en_bytes_remain_frozen | Reconstruction misses recent additions and registered movementKindOrdinary rename; removing reference-specific additions yields identical residual hashes, undoing that rename restores original frozen hashes |
| test_historical_events_and_full_schema_cannot_redefine_the_reconstruction | Duplicate final nonhistorical register event accepted, contrary to assertion |
| test_historical_movement_additions_are_exact_untranslated_sources | historyAcceptedReconstruction historical/current wording mismatch |
| test_lab_refinement_history_cannot_redefine_the_reconstruction | Nonhistorical duplicate accepted, including T1007 variant, contrary to assertion |
| test_post_baseline_keys_fall_back_to_english_without_japanese | Current historyAcceptedReconstruction FR/EN differs from frozen registration |

## Remaining limitations and findings

1. **PostgreSQL command incomplete:** the focused concurrency suite reported
   `Ran 15 tests in 66.895s / OK`, then exceeded its 120-second overall bound
   during test-database destruction. Assertions passed, but a clean command exit
   is not claimed. All temporary servers/clusters were subsequently stopped and
   removed; port 55439 was verified unbound.
2. A broader isolated PostgreSQL attempt (140 discovered tests) produced errors
   and was interrupted without a final summary. A standalone reproduction
   established `HistoricalLocationTransitionTests.test_09_source_box_identity_unavailable_or_different`
   fails in setUp: explicit Organization pk=1 followed by a sequence-generated
   insert collides with pk=1. The fixture file is unchanged from 5b1299b. It fails
   before the feature assertion; no unrelated fixture repair was made.
3. SQLite migration round-trips and fresh PostgreSQL migration application were
   exercised. No completed PostgreSQL migration round-trip result is claimed.
4. **Pre-existing thermal accessibility defect:** ZoneProbeCharts.tsx range
   button appends a formatted range to `t('probeRangeEdit')`, leaving `{range}`
   unresolved in its accessible name. Present in ee492bd, not created by merge.
   Left unchanged under the instruction not to reopen validated implementations.
5. **Browser QA not run:** no persistent disposable visual-QA database/browser
   workflow was verified. Actual desktop/tablet/narrow layout, clipping, touch,
   focus, keyboard and FR/EN rendering remain for independent review. Component
   and source tests are not presented as real-browser evidence.

## Handoff

Working-tree conflict content is resolved. The four original entries remain UU
because their unmerged index stages were deliberately untouched. HEAD and
CHERRY_PICK_HEAD remain unchanged. Anthony must inspect and stage the four
resolved conflict files, the additional modified files and the five new evidence
files; the incoming auto-merged entries are already in the cherry-pick index.

**Verdict: READY FOR INDEPENDENT INTEGRATION REVIEW**, with the explicitly listed
prior failures and unverified browser/teardown behavior. This is not approval to
finalize, deploy, or claim a completely green validation matrix.
