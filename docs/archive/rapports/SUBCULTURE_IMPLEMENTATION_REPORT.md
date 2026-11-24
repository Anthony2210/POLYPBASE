# Quantitative subculture and Box dialogs — implementation report

Worktree: `subculture-popup-overhaul/POLYPBASE`.
Branch: `feat/subculture-popup-overhaul`.
The initial worktree was clean. No commit, push, merge, stash, deployment, production/VM/Neon access, or shared database mutation was performed. The rejected `box-dialog-redesign` worktree was not used.

## 1. Architecture inspected

Read AGENTS, the project context router, measurements integrity, Box lifecycle/locations, frontend UX/style and the local QA skill. Inspected SubcultureEvent, Box/lineage/location models, serializer/service/API transaction boundaries, organization context and roles, ordinary measurement POST/PATCH and weekly constraint, Box code grammar/writers, audit normalization/presentation, current-state consumers, charts/history, lifecycle dialogs and the Move/Subculture/QR dialog behavior. Inventory, Labels, Team and Actions supplied the existing visual patterns. The old service created synthetic child measurements, accepted client codes/dates and did not deduct parent stock; these were the implementation gaps addressed here.

## 2. Domain model implemented

Extended the existing SubcultureEvent domain, not a generic ledger. New quantitative events have server occurrence time, immutable parent before/allocated/after balances, parent source snapshot, serialized source sequence and original author name. Nullable quantitative fields distinguish untouched legacy events from allocation-bearing events. Added protected ordered SubcultureAllocation rows and a separate BoxCodeNamespace high-water counter.

## 3. Migrations

Three additive migrations; no invented historical quantitative effect or timestamp, no reclassification/removal of old measurements, no audit rewrite. The weekly BiologicalMeasurement constraint and global Box code uniqueness remain intact. A populated migration preservation regression runs on both engines.

## 4. Parent current-state resolution

`backend/apps/cultures/polyp_state.py` resolves absolute accepted sources: an eligible ordinary measurement, the resulting parent Subculture state, or child initialization. It never computes latest measurement minus all historical allocations. Unknown remains null; zero remains known. Shared Box serialization exposes `current_polyp_state` separately from `latest_measurement`. Per-Box source sequence resolves same-clock-tick ordering. Historical corrections retain original source order and cannot restore already-distributed stock. A later eligible newly created ordinary measurement can establish another absolute state. Legacy unknown chronology is not backfilled.

## 5. Child allocation persistence

Each SubcultureAllocation protects its event and child Box and stores integer allocated polyps, stable zero-based position, original final child code and initial state sequence. Zero is valid; omission/null/fractions/booleans/strings are rejected. No synthetic BiologicalMeasurement, ephyrae or strobila is created.

## 6. Atomic Subculture workflow

The service opens one transaction, locks/rechecks parent organization, actor permission, active status and eligible Strain, checks the expected current-state revision, resolves current availability, validates allocations and total, locks distinct active same-organization zones in ID order, reserves codes, creates event/children/allocations/lineage/locations, advances revision and writes mandatory audit evidence. All effects, including first-use counter creation and reservations, roll back on failure. Tests inject later-child, location, lineage, allocation, parent-event and audit failures.

## 7. Child code allocator

The authoritative backend allocator reserves exactly N consecutive suffixes above the namespace high water, in submitted child order, with minimum three-digit padding and growth beyond 999. Holes are never filled. Counter reconciliation includes existing legacy numeric suffixes and preserves their identifiers. Ordinary Box save paths cooperate, including manual creation, admin, CSV import, normalization, demo seed and Transfer v1. Strain local X allocation is untouched. The frontend sends no global_code/box_number and displays honest creation-time identities before success; final codes come only from the response.

## 8. Concurrency strategy

Parent Box row locks are shared by Subculture and ordinary measurement saves/POST/PATCH. Opaque revisions reject stale allocation intent with `409 subculture_current_state_changed`. New source sequences are monotone; corrections invalidate intent but keep source order. Lock order is parent Box, ascending child zones, then namespace. Unique namespace creation plus row locking protects first use. Reconciliation does not lock other Boxes and avoids namespace-to-parent inversion. Real PostgreSQL tests observe the second independent connection attempting and waiting on the relevant lock. No deadlock or timeout occurred in the passing affected tests.

## 9. Typed biological timeline

`BoxDetail.biological_timeline` exposes `measurement`, `subculture`, and `subculture_initialization`, with type-qualified identity, effective day, actual timestamp where known, source sequence, author, balances/allocations and related Box identities. Only measurement entries expose measurement editing capability. Legacy Subculture entries remain unknown quantitatively.

## 10. Graph/history changes

BoxTrackingChart/Preview and BiologicalTrendChart use the typed timeline adapter. Parent-after and child initialization are absolute polyp points with square markers, distinct tooltips and allocation details. Multiple children create one parent event. Zero is plotted; absent values are not fabricated. Operations provide no synthetic ephyrae/salinity points. BoxInsights history combines typed entries, retains measurement payloads, year filtering, 24-entry pagination, notes, focus and scroll behavior. No operation correction action is introduced.

## 11. Audit/Actions changes

SUBCULTURE remains separate from MEASUREMENT. Metadata and normalized business details carry immutable before/allocated/after balances, occurrence, event identity, final child IDs/codes and ordered allocations. Personal Actions and Administration presentation show factual transitions and child code/allocation values, including zero, in FR/EN without displaying technical IDs. Legacy rows retain their existing presentation. Administration desktop-only behavior is unchanged.

## 12. Measurement-correction behavior

Source measurement corrections remain allowed through existing authorization/editing contracts. They do not change accepted Subculture snapshots, allocations or audit evidence, and do not trigger historical reconciliation. Ordinary weekly uniqueness, correction endpoints and freshness semantics remain measurement-specific.

## 13. Optional deactivation flow

After successful Subculture, App records success and authoritative child codes. A zero parent result offers deactivation only to an account already authorized by the existing lifecycle capability. Declining leaves the parent active. Accepting opens BoxLifecycleModal, requiring the existing reason and independent lifecycle write. A lifecycle failure is shown separately; it does not erase or roll back successful Subculture. No automatic deactivation or broader permission was added.

## 14. Subculture popup redesign

Compact parent code/species/Strain identity, lightweight live availability/allocation/remainder, expanded operational child rows separated by fine dividers, explicit polyp inputs, native zone selects and optional notes/reason. Add follows the list; remove has a child-specific accessible label and transfers focus to a surviving input. Maximum 20 rows matches the server contract. Codes are not edited or predicted. Standard primary/cancel controls and a small creation count replace card stacking. User copy uses the central FR/EN catalogs.

## 15. Move popup redesign

Compact identity and directional current-to-destination flow, with a dominant native select, optional notes and a collapsed lightweight location history. Movement time is set automatically by the server when the move is recorded; the dialog has no date field. Errors precede history near the form. Existing payload, allowed zones, confirmation, pending lock, keyboard trap, Escape and focus restoration remain. All visible labels now use the central catalogs, preserving their existing text.

## 16. QR popup redesign

One visible Box identity, an upright square screen QR, restrained print/download actions and secondary Labels affordances. Screen preview is independent from the hidden physical-print label. Physical 41 × 28 mm geometry, 25 × 25 mm QR, text/rotations/SVG generation, authenticated resource loading and `/bac/<id>/` targets are unchanged.

## 17. Date bug resolution

The Subculture modal has no editable date or datetime input and focuses the allocation field, not a native date segment. Its occurrence time is set by the server. Move likewise has no date field: the frontend omits `moved_at`, and the serializer supplies `timezone.now` when the request is validated, so the server records the action time in the movement, location history and audit. The user cannot choose or backdate it. Historical events retain their original dates and unknown timestamps.

## 18. Responsive/accessibility changes

Reuse safe portals/hooks; one modal body scroll owner, no footer-covered fields, normal action sizes, visible local focus rings and phone inputs at least 16px. Title, initial/return focus, Tab/Shift+Tab, Escape, pending guards, nested confirmation cancellation, add/remove focus and draft recovery were checked. Browser QA found the history year selector at 14px on phones; a scoped 16px override fixed it and was rechecked in both languages and phone sizes. No global focus token or Administration responsive change.

## 19. Exact backend tests

New modules:
- `apps.cultures.test_quantitative_subculture`: 41 science/API/state/consumer regressions, including zero/unknown/missing, 100 → 50 and full allocation, over-allocation, same-week parent/child readings, unchanged weekly conflicts, corrections, no synthetic biology, permissions/organizations/zones, audit, immutable evidence, failure rollback, code reconciliation/padding/deleted high water, same-clock sequencing and consumed future-dated source regressions.
- `apps.cultures.test_quantitative_subculture_migrations`: one populated historical preservation test.
- `apps.cultures.test_quantitative_subculture_concurrency`: 12 PostgreSQL-only races (see below).

Final additional parent validation:

```sh
POSTGRES_DB='' DJANGO_DEBUG=1 uv run python manage.py test apps.cultures.test_quantitative_subculture apps.cultures.test_quantitative_subculture_migrations apps.cultures.tests apps.measurements apps.audit --settings=config.test_settings --noinput
```

153 tests: 152 passed, one PostgreSQL SQL-delete-guard skip. Full suite below includes all new tests.

## 20. Exact PostgreSQL concurrency tests

Ran on a newly created empty disposable PostgreSQL 16 Alpine container with ephemeral tmpfs storage and fixed loopback-only test settings, not the existing shared local copy. Container and storage were removed after validation.

Twelve tests in `QuantitativeSubcultureConcurrencyTests`:
1. `test_same_parent_double_spend_is_serialized_and_stale_intent_conflicts`
2. `test_measurement_post_wins_and_invalidates_subculture_intent`
3. `test_measurement_patch_wins_and_invalidates_subculture_intent`
4. `test_subculture_wins_then_post_is_later_absolute_state`
5. `test_subculture_wins_then_old_measurement_patch_does_not_rebase_history`
6. `test_different_parents_share_one_namespace_without_deadlock`
7. `test_concurrent_namespace_first_use_reconciles_legacy_suffixes`
8. `test_first_use_rollback_releases_namespace_and_reuses_suffix`
9. `test_existing_counter_rollback_releases_namespace_and_reuses_suffix`
10. `test_existing_manual_writer_collision_waits_and_rolls_back_safely`
11. `test_parent_code_rename_obeys_parent_then_namespace_lock_order`
12. `test_rollback_same_parent_allows_second_full_allocation`

All 12 passed with actual independent-connection overlap/lock waiting, no skips, deadlocks or timeouts. The affected PostgreSQL invocation also covered existing biological-measurement, movement and Transfer v1 concurrency, science, migrations, audit, lifecycle, inventory, Overview, weekly exports and organization isolation: **268/268 passed, no skips**. Exact complete command and environment safeguards are recorded in `backend/QUANTITATIVE_SUBCULTURE.md`.

## 21. Exact frontend tests

Added:
- `scripts/test-quantitative-subculture.mjs`: actual modal/hook allocations, current state, zero versus blank, over-allocation, ordered rows, limits/zones, focus, pending states, stale revision and explicit review preserving draft.
- `scripts/test-subculture-workflow.mjs`: 26 actual extracted App operation/lifecycle tests covering committed results, authoritative codes, zero deactivation yes/no/not-authorized/failure, confirmation cancellation, stale refresh recovery and organization lifetime.
- `scripts/test-biological-timeline.mjs`: typed chart/history adapter and distinct absolute-state points.
- `scripts/test-move-box-modal.mjs`: directional native-select workflow, history and preserved mutation contract.

Updated dedicated/shared dialog, mutation, measurement-history, insights, chart, audit, Inventory, Overview, zone, phone and Box-action suites. Tests are behavioral/contract-oriented; source CSS assertions do not claim real measured geometry. Final complete frontend run executes every test file including these new modules.

## 22. Broader backend suite result

```sh
POSTGRES_DB='' DJANGO_DEBUG=1 uv run python manage.py test --settings=config.test_settings --noinput
```

Final parent rerun: **873 tests, 821 passed, 52 skipped, no failures/errors**. Skips primarily cover PostgreSQL-specific checks and other existing optional integration checks; PostgreSQL affected coverage above ran without skips.

A full PostgreSQL suite run before final future-date hardening had **8 failures and 36 errors** in unchanged portable-lineage/Transfer v2 TestCase tests, at the existing `Portable lineage requires its own transaction` guard. Representative failures reproduced after neutralizing the new save paths in memory; unrelated tests were not modified. The final full PostgreSQL suite was not rerun; the expanded final affected PostgreSQL scope passed 268/268. Do not describe the full PostgreSQL suite as green.

## 23. Complete frontend suite result

```sh
node --test scripts/test-*.mjs
```

Final run used equivalent TAP reporter/output redirection to keep the diagnostic log manageable: **1,237 passed, zero failed, zero skipped**. Earlier integration failures were corrected by updating actual dependency loaders and outdated legacy contract assertions. Temporary TAP output was removed.

## 24. Django check

`POSTGRES_DB='' DJANGO_DEBUG=1 uv run python manage.py check --settings=config.test_settings` — passed, no issues. Safe test settings were used instead of the default potentially shared DB configuration.

## 25. Migration drift check

`POSTGRES_DB='' DJANGO_DEBUG=1 uv run python manage.py makemigrations --check --dry-run --settings=config.test_settings` — passed: no changes detected.

## 26. Typecheck

`npm run typecheck` — passed. Final Move catalog integration also passed TypeScript.

## 27. CSS check

`npm run check:css` — passed. New dialog styles are local imports scoped to their dialog families, not a generic UI framework.

## 28. Build

`npm run build` — passed CSS validation, TypeScript and Vite production build. No new production dependency added.

## 29. Repository whitespace

`git diff --check` — passed. Final status/stat/name inspection performed; all changes remain uncommitted.

## 30. Changed files

Backend:
- `backend/QUANTITATIVE_SUBCULTURE.md`
- `backend/apps/audit/services.py`
- `backend/apps/cultures/admin.py`, `api_views.py`, `models.py`, `serializers.py`, `services.py`, `transfer_v1.py`
- `backend/apps/cultures/biological_timeline.py`, `box_codes.py`, `polyp_state.py`
- `backend/apps/cultures/test_box_strain_scoping.py`, `tests.py`
- `backend/apps/cultures/test_quantitative_subculture.py`, `test_quantitative_subculture_concurrency.py`, `test_quantitative_subculture_migrations.py`
- Three migrations listed below.
- `backend/apps/measurements/models.py`
- `backend/config/test_settings.py`
- `backend/locale/fr/LC_MESSAGES/django.po`, `django.mo`
- `backend/locale/en/LC_MESSAGES/django.po`, `django.mo`

Frontend production:
- `frontend/src/App.tsx`, `types.ts`
- `frontend/src/components/AuditTimeline.tsx`, `BiologicalTrendChart.tsx`, `BoxInsights.tsx`, `BoxTrackingChart.tsx`, `BoxTrackingPreview.tsx`, `MoveBoxModal.tsx`, `QrLabelModal.tsx`, `SubcultureModal.tsx`, `ZonesView.tsx`
- `frontend/src/components/box-utility-dialogs.css`, `quantitative-subculture.css`
- `frontend/src/i18n/fr.ts`, `en.ts`
- `frontend/src/styles/components/audit-timeline.css`, `biological-trend-chart.css`
- `frontend/src/styles/pages/box-insights.css`
- `frontend/src/utils/auditPresentation.ts`, `biologicalTimeline.ts`, `subculture.ts`

Frontend scripts:
- `test-audit-presentation.mjs`, `test-biological-timeline.mjs`
- `test-box-action-refinements.mjs`, `test-box-action-wording.mjs`, `test-box-dialogs.mjs`, `test-box-insights.mjs`, `test-box-inventory.mjs`, `test-box-third-pass.mjs`
- `test-chart-biology.mjs`, `test-chart-readability.mjs`
- `test-measurement-history.mjs`, `test-mobile-label-polish.mjs`, `test-move-box-modal.mjs`, `test-mutation-dialogs.mjs`
- `test-overview-history.mjs`, `test-phone-navigation.mjs`
- `test-qr-preparation.mjs`, `test-qr-preview-polish.mjs`
- `test-quantitative-subculture.mjs`, `test-subculture.mjs`, `test-subculture-workflow.mjs`, `test-zone-boxes.mjs`

Documentation:
- `docs/context/boxes-lifecycle-locations.md`
- `docs/context/measurements-integrity.md`
- `docs/SUBCULTURE_IMPLEMENTATION_REPORT.md`

## 31. Migrations created

- `backend/apps/cultures/migrations/0010_quantitative_subculture.py`: nullable parent evidence, Box revision, protected ordered allocation model, namespace counter and balance constraints.
- `backend/apps/cultures/migrations/0011_protect_subculture_history.py`: PostgreSQL immutable UPDATE/DELETE guards; SQLite UPDATE guards plus ORM deletion protection.
- `backend/apps/measurements/migrations/0006_quantitative_subculture.py`: nullable source sequence; no legacy data update.

## 32. Known limitations / unchanged consumers

- Full PostgreSQL suite has the unrelated portable-lineage/Transfer v2 transaction-guard failures described above.
- Uncooperative arbitrary SQL/ORM bulk writers bypass save-based code/state lock protocols. No current production bypass writer was found; reconciliation covers pre-existing bypassed codes but cannot serialize a future uncooperative concurrent SQL writer. Documented in the backend contract.
- SQLite relies on ORM delete protection for quantitative history because its Django flush uses DELETE; PostgreSQL also enforces SQL DELETE protection.
- Before creation the dialog intentionally does not predict canonical codes, since a preview reservation would be stale or consume identifiers. Final identifiers are shown from committed response data.
- Legacy historical reconciliation is not implemented; old events and historical synthetic measurements remain unchanged.
- `latest_measurement`, fiche/search latest-reading counts, Inventory's explicit “Latest measurement” column, measured 0/0 suggestions, reading-age/weekly freshness, Overview observation charts and exports/label eligibility remain measurement-specific.
- Zone Box rows now show authoritative current polyps; ephyrae/date remain actual measured facts. Backend adds explicit current state/totals to Box/Inventory/Overview/zones/dashboard without replacing legacy measured totals.
- This is an API contract change: clients must submit explicit `allocated_polyps` and `expected_current_state_revision`; new Subculture requests reject arbitrary event dates/client identifiers. Frontend/backend should be released together under the existing deployment procedure, not deployed by this task.

## 33. Browser/device QA and remaining coverage

Real Chromium mounted actual React components/styles/catalogs with isolated mocked resource/mutation state, in FR/EN at all seven requested sizes:
`320×568`, `390×844`, `800×1280`, `960×600`, `960×480`, `1280×800`, `1440×1000`.

Verified modal containment/one-scroll-owner, exposed fields/actions, long identities, phone input sizing, keyboard/pending/focus restoration, nested cancellation, draft/stale review, quantitative zero/unknown/over-allocation, Move history/payload, upright QR and physical print geometry. Typed chart/history checks covered one parent point for multiple children, square zero point, real measurement data, no fabricated series, tooltip bounds, initialization and type-qualified overlapping numeric IDs. No page errors or real API requests. Temporary harnesses/servers were removed. The phone history selector fix was separately rechecked in eight FR/EN/long-identity cases.

Remaining: real physical iOS/Android laboratory devices, native picker appearance and iOS focus zoom, screen-reader audit, screenshot-based product/design approval, real printer/scanner verification and full App plus live disposable-backend end-to-end browser smoke. Browser fixtures and separately tested backend/API/concurrency are not represented as a live full-stack browser test.

## 34. Unresolved product decisions

None blocked implementation. No new biological allocation semantics, ephyrae distribution, automatic lifecycle transitions, legacy backfill or historical reconciliation was introduced. Implementation and validation are complete; the next step is independent review, without commit or deployment.
