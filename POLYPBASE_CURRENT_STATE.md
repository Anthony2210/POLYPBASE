# POLYPBASE current state

## Snapshot and evidence scope — 2026-10-14

This is the living project summary. Durable contracts belong in [`docs/context/`](docs/context/); [`AGENTS.md`](AGENTS.md) remains authoritative for invariants. This refresh replaces the **2026-10-06** snapshot in this file. The four conversations used an older **2026-10-03** copy; those dates describe different snapshots, not conflicting current HEADs.

- All four supplied transmissions were read completely: **IMPORTS**, **DEPLACEMENTS_UI**, **GIT_WORKTREES**, **APP_ARCHITECTURE_CONCURRENCE**, dated 2026-10-14. Input copies are in the pre-existing, untracked `_transmissions_2026-10-14/` directory; they are evidence, not rewritten living documentation.
- Inspection: local Git refs/history, canonical and registered worktree statuses/diffs, relevant code, migrations and test sources, current domain documents, and the companion Species/AAA decision ledger. Other worktrees and the companion repository were read-only.
- Canonical repository: `C:\Users\antoc\POLYPBASE`. **Canonical HEAD = main = local origin/main = `ec81a187291dbf7a930e8e03d62a81082455b224`**, `feat: add scientific week interval comparison foundation`. Canonical tracked/untracked status and index were clean; **0 ahead / 0 behind** the inspected local upstream.
- Documentation worktree: `C:\Users\antoc\worktrees\POLYPBASE\docs-sync-2026-10-14\POLYPBASE`, branch `docs/sync-2026-10-14`, starting at the same commit. No existing tracked documentation diff; the four input transmissions were already untracked.
- **No fetch or fresh remote/CI query.** The transmissions separately report successful pushes and green GitHub CI, including [CI for ec81a18](https://github.com/Anthony2210/POLYPBASE/actions/runs/38544101545). Local tracking alignment is not a new remote verification.
- **No application tests/builds, database access, migrations, imports or production operations during this documentation task.** Test/review results below are dated milestone evidence, not reruns. Deployment and applied production migrations remain unverified.

Historical snapshots remain in Git: `f72838a` contains the 2026-10-06 state (`main e69b24d`, local upstream `4bdd338`, plus the older ten-worktree inventory); its evidence describes that date only. Dated repository audits and the supplied transmissions were not rewritten or removed.

Status vocabulary: **A** integrated into main; **B** committed but not integrated; **C** local/uncommitted; **D** awaiting independent review; **E** reviewed, awaiting integration; **F** planned/blocked; **G** abandoned/superseded; **H** unverifiable. These axes may combine (for example C+D). A source SHA outside main ancestry is not automatically B: integration is checked by patch equivalence as well as ancestry.

## Integrated product capabilities

### Scientific measurements and quantitative subculture

- **One biological measurement per Box per ISO week**, including `0/0`. Interactive POST creates only in a free week (`201`); an occupied week, even the same date, returns **`409 measurement_week_conflict` without mutation**. Correction uses authorized PATCH. Inactive boxes reject new readings but retain correctable history. The old same-date upsert contract is historical, not current.
- Shared create/correct services are integrated (`016712b`). Box-first locking, weekly uniqueness and mandatory audit protect normal writes. Database uniqueness protects `(box, week_start)`; current `save()` computes its Monday key. It does not establish safe permissions/audits or a dedicated date/key CHECK for arbitrary bulk/SQL writers.
- **Observer/origin foundation** (`681af82`, measurements migration `0008`): direct entry captures the authenticated observer; correction preserves that observer and origin. Legacy `measurement.user` remains actor/latest corrector. Importer is not inferred as observer. Historical rows remain `legacy_unestablished` / `legacy_unclassified`, without copied users or fabricated identities. Account deletion nulls references, not attribution status; no extra personal-name snapshot exists.
- **Temporal precision foundation** (`41b79a8`, migration `0009`): persisted `legacy_unverified` or `exact_date` only; **`measured_on` remains NOT NULL and `iso_week` is rejected**. Historical Mondays are neither certified nor converted. Generic PATCH, including explicit date correction, does not promote precision.
- **Read preparation** (`18b5cd0`): additive `week_start`; latest-per-Box ordering by week, creation time and PK; Inventory absence uses existence rather than nullable date. Mixed timeline, cross-Box ordering, freshness, frontend and exports still need work before week-only activation.
- **Scientific interval helper** (`ec81a18`): pure immutable `ObservationTime`, ISO boundaries and `before/after/same_day/unresolved`. The relation describes **the observation relative to the operation day**. It is **not called by `cultures/polyp_state.py`**, does not change stock resolution, and activates no week-only persistence or new operation guard.
- **Quantitative subculture** (`8adea6c`, cultures migrations `0010`–`0012`): atomic parent before/allocated/after snapshot, immutable ordered child allocations, source sequences and revision checks, server-reserved child codes. Explicit child zero is known; omitted/NULL is unknown. Partial allocation keeps before known, total/after NULL and the previous authoritative parent state, while incrementing intention revision. It creates no artificial measurement or weekly slot. Corrections of consumed measurements do not rewrite accepted snapshots. Optional deactivation at parent zero is a separate operation.
- Latest ordinary reading and `current_polyp_state` are distinct. Current resolver returns a known count (including zero) or absence, without an explicit unresolved-state contract. See [`measurements-integrity.md`](docs/context/measurements-integrity.md) and [`QUANTITATIVE_SUBCULTURE.md`](backend/QUANTITATIVE_SUBCULTURE.md).

### Imports — delivered foundations, not a generic import workflow

| Milestone | Main commit | Implemented scope (A) |
|---|---|---|
| Phase 1 | `016712b` | Shared ordinary measurement write services |
| Phase 2 | `2d3d617` | Strict canonical CSV preparation/mapping, source coordinates, zero/NULL distinctions |
| Phase 3 | `794e29c` | Authorized, institution-scoped, read-only deterministic planner |
| Phase 4A | `681af82` | Observer/origin persistence, reads and audit |
| Phase 4B.1 | `41b79a8` | Legacy/exact temporal precision, without week-only writes |
| Phase 4B.2 | `186c458` | Planner 1.1 subculture chronology evidence/conflicts |
| Phase 4B.3a | `18b5cd0` | First temporal read compatibility tranche |
| Phase 4B.3b | `ec81a18` | Pure interval comparison foundation |

- Canonical preparation is pure; planner **1.1** is an ephemeral Python preview, not a durable plan, upload, API apply endpoint or import UI. States include `IGNORED`, `INVALID`, `UNKNOWN_BOX`, `CREATE`, `ALREADY_MATCHES`, `CONFLICT`, `BLOCKED`. Duplicate source Box/week slots block even if identical. `ALREADY_MATCHES` performs no write or precision/provenance promotion.
- A free week-only row can be `CREATE` but still carries **`week_precision_persistence_required`, ready=False**. A relevant subculture in its week yields `week_subculture_chronology_conflict`. A parent operation with confirmed occurrence remains relevant even if partial; a known child allocation includes zero. Legacy unknown occurrences/allocations are not thereby certified safe.
- Approved target: shared preparation/review, **separate authorized applicators** for entry, bulk correction and onboarding. Any unresolved error blocks the whole file; regenerate preparation/plan after resolution. Bulk correction uses expected-old-value guards, not overwrite. Laboratory entry/import will live outside desktop-only Administration; bulk correction/onboarding remain administrative. **These applicators and UI are not implemented.**
- Separate existing historical workflows remain active backend tools: `import_bdd_csv`, guarded **2026 manifest import** (`0f6fdab`) and reviewed cleanup/deactivation commands. Their permissions, source conventions and guarded corrections are not a generic import contract. Legacy CSV still upserts some measurements and may collapse local Strain X=0 via `or None`; neither behavior is a new approved scientific rule.
- Contracts and remaining prerequisites: [`imports-exports.md`](docs/context/imports-exports.md), [`canonical-measurement-csv.md`](docs/canonical-measurement-csv.md), [`measurement-import-planner.md`](docs/measurement-import-planner.md).

### Environmental readings

- Temperature display separates target from observed average/extrema; manual entries update a daily aggregate transactionally, not an individual-per-entry history. Connected probe ingestion is not established by schema presence.
- Salinity persists dated zone readings (duplicate zone/date rejected); UI currently shows latest, not full history. Corrections are audited and allowed strictly before `created_at + 24h`, equality locked, no Admin override; zero PSU is valid. Historical milestone `0d7985a` and `test_zone_salinity_lifecycle.py` remain the evidence, not a new rule from movement corrections.

### Movements, periods and audited correction

- **`b387621`**, cultures migrations **`0013` / `0014`**: at most one **ordinary** BoxMovement per Box per ISO week **Europe/Paris**. Initial location, qualification, reactivation and legacy `unclassified` are distinct traceable kinds, not ordinary quota consumption. No historical reclassification or period-link reconstruction.
- `Box.thermal_zone` is current location; `BoxLocation` is a period; `BoxMovement` is a recorded event. A period boundary is not automatic evidence of a physical movement. Capacity/occupancy remains informational, not a movement blocker.
- POST move requires `expected_thermal_zone_id`, including explicit NULL; `box_location_changed` takes precedence over `box_movement_week_conflict`. Box lock, partial UNIQUE and Paris-week CHECK protect ordinary movement consistency.
- **PATCH `/api/boxes/<box_id>/movements/<movement_id>/`** requires `expected_revision`. Authorized institution Admin/technician may correct without time limit or original-authorship requirement. Preserve event identity/kind/original actor; increment revision and append before/after audit atomically. Notes include legacy; date is ordinary-only with proven period links/chronology; destination is classified terminal-only. No cascading history rewrite or second movement.
- Generic Django admin is read-only for movements/periods; existing Box location cannot be edited there. Backend can refuse a structural correction even when the conservative UI shows a pencil.
- **`b60620b`**: charts distinguish persisted `movement` from derived `location_change`; Emplacement history describes period entries/exits. Event timestamps retain precision for detail/identity (JavaScript millisecond comparison), while chart positions remain day-based. Overview passes periods, not persisted movement events. Timestamp range bounds use browser-local calendar days, including the whole final selected day; this is not the backend Paris-week rule.
- **`0703f19` + `9d20dba`**: latest persisted movement banner under latest reading; correction **inline, not modal**; full **Origin → Destination**, one arrow, no individual chevrons/truncation. Paris datetime editing preserves an unchanged original timestamp and rejects changed DST gaps/folds. Save hidden without normalized change, Cancel retained; revision conflict requires explicit reload/re-examination, no silent rebase.
- Historical investigation reported discrepancies for ten boxes without established structural corruption. No automatic reconstruction from temperatures or blanket correction was approved. The chart period-marker UTC/Paris question was explicitly deferred to another agent; its final result is H, not a new task here.
- Durable details: [`boxes-lifecycle-locations.md`](docs/context/boxes-lifecycle-locations.md).

### Frontend architecture and request state

- Integrated extraction sequence: formatters `e24ed08`, labels `a33edb2`, primitives `839999a`; **M8** leaves `184f656`, CreateBoxPanel `3b1fc92`, PilotageView `d1c9ca1`, test-owner correction `5cf3879`; **M9** QrSearchModal `dd154bf`, BoxPage `c299df3`.
- `App` retains authentication/bootstrap, organization generation, navigation, shared caches, mutations and recovery. Extracted components own local UI state; BoxPage mounting key remains Organization + Box ID. Strict null checks (`f1ab87e`) and complete frontend test discovery in CI (`e48601e`) are integrated.
- Current `App.tsx`: **2,653 lines** at `ec81a18`; **2,628** was the post-M9 baseline before inline movement integration. Line/prop/state counts are historical measurements, not architecture contracts. Recommendation after audit: stop decomposition merely to reduce lines; no M9.3 or orchestration-hook rewrite approved.
- Full Box collection is deferred (`178ce33`), with explicit partial/ready state; zone refresh preserves complete paginated collections (`ab96234`); list-query optimization is integrated (`785ff3f`, [`performance evidence`](docs/BOX_LIST_QUERY_PERFORMANCE.md)). Raw retained QA JSON is historical, not a new main benchmark.
- Organization-generation guard (`07c69c2`) prevents old-context publication, **not stale reads within one institution**. Main still shares in-flight GETs by institution/path, with no Box resource freshness barrier in App. A pre-mutation GET or delayed mutation snapshot can restore an obsolete frontend projection. The transmission reproduced this for zero corrections, subculture and location; **no server data loss was demonstrated**.
- **M10.1 is C+D, not integrated**; see active work below. Zones/pagination M10.2 is F. Single-slot `refreshRecovery`, AST/source-owned test fragility and duplicated profile helper are separate debts, not reasons for opportunistic refactoring.

### Charts, exports, QR and labels

- Shared `BiologicalTrendChart`: zero and `0/0` plotted, absent salinity remains absent, line breaks over gaps >10 calendar days, at least one biological series visible. Keyboard/focus/pin interactions and overflow markers preserve observations above the 0–1000 plotting scale. Historical chart milestones `efcb3bc`, `ff2fab9`, `fa33e32` remain integrated.
- **Overview full-history correction `2942966` supersedes the old bounded-history limitation.** Initial API payload is bounded to six months but reports earliest evidence/completeness; a partial chart loads full Box history immediately for the default range, at least three months wide, with loading/error/retry states. This is not proof that every older consumer supports future NULL dates.
- Weekly exports remain organization-scoped, ordinary-measurement-only, with zero versus empty cells preserved. Child initialization alone is not an exported reading. Zone filtering uses recorded-date location; daily temperature falls back to zone target when no daily aggregate exists. Preview `measurement_count=0` distinguishes empty-week aggregate zeros from a real zero reading. Week-only period/location/temperature policy is not implemented.
- Physical Box labels: **41 × 28 mm**, 25 mm vector QR (`f999bd9`), existing literal Box identifier/species, `/bac/<id>/` target; filters preserve global selection. Eligibility is active Box with latest reading within 15 months, including zero. No taxonomy/provenance inference at render time.
- QR isolation (`69b39f4`): login-protected `/bac/<id>/` handoff performs no Box lookup or SCAN; authorized `/api/boxes/<id>/scan/` resolves within explicit active Organization and audits success. Protected SVG uses the same context. Foreign membership is not permission in the active institution.
- Consistency/QR handling (`96c5593`) is integrated: request-scoped Inventory/export eligibility, guarded history replacement, focus-safe MoveBox/Subculture dialogs, distinct `prepared/cancelled/failed` preparation outcomes. Prepared is browser handoff, never physical printing success. Phone QR-focus fix `83700f4` and tablet/mobile polish `23a9f3e` are integrated; the old `tablet-box-ui-polish` pending claim is superseded.
- Measurement-history modal `2292a61` is integrated, preserving progressive loading and scientific values. Physical printer and real laboratory tablet QA are not established by this refresh.

### Actions, Alerts and Administration

- Personal Actions are actor + active-institution scoped; Administration audit is institution-scoped and admin-only. Business mutations, append-only correction evidence and readable structured identities are preserved; login/view/scan are not these Actions streams. Presentation `cc2d466` → `3d9f96e` → `673db92` and identities `4bdd338` are integrated.
- **Alerts abandoned (G), runtime removal integrated at `5f773ff` (A).** No active producer/API/UI or inferred decline warning. Dormant model/table/migrations/data and historical audit remain. Any schema/data removal requires a separate explicit decision.
- Administration remains **desktop-only**: tablet entry absent, `/administration...` replaced with `/` without intermediary UI. Backend permissions still apply. References is disabled (`284c033` supersedes temporary `6bad008`); the large shared catalog is abandoned for current direction. `DISABLED_ADMIN_SECTIONS` also contains **environment, transfers, organizations**; Accounts/Team, Inventory and History remain active in React. Disabled sections fall back to Accounts; backend APIs remain.
- **Équipe `e69b24d`**, later layout **`9199871`**: members and invitations are distinct lists within the team screen, invitations below members. Persisted **24h** validity, backend expiry, resend invalidating prior links, proven legacy invitations expired without fabricated times. Unusable password alone does not prove invitation. Hidden technical membership flag affects lists/counters, not permissions or last-Admin/Responsable rules.
- Environment designation of technical memberships is **not reverified**; no name-based inference or operation occurred. Inactive unaccepted memberships currently appear in neither list and cannot be resent; changing this requires a product decision.
- Organization create/update + required audit atomicity (`b95cb08`) is integrated. No-op updates produce no update audit; this is not universal audit atomicity.

### Taxonomy, AAA/BBB and Transfer v2

- Institution-owned operational Strains; eligible unowned legacy only via a pre-existing Box in the institution. Normal Strain POST requires institution AAA and atomically creates local identity/audit (`dcb6c81`); no fallback from global Species code. AAA API is active (`ce4e66b`), but its disabled References management screen is not. Missing-AAA guidance (`519e601`) still links to disabled References; contextual maintenance design is pending.
- AAA means nonempty code ≤3 characters at API level, not an exact three-uppercase-letter grammar. Manual Strain code/number fields and global `(species, code)` uniqueness remain. Local identity creation `7dd9847` / `fc679b6` and Species-change protection `1ea84e5` are integrated; identity-less owned legacy behavior remains. No automatic historical ownership/global identity/local identity reconciliation.
- BBB/provenance models (`54e115d`) and GET/POST APIs (`6ee81d4`) remain backend foundations. No detail/PATCH/DELETE or active frontend consumer. Normal creation is AAA-only; routine provenance/BBB UI is deferred, not authorized for removal. Biological provenance is not provider, acquisition, Origin or transfer history.

| Transfer milestone | Verified main commit | Scope (A) |
|---|---|---|
| Phase 1 | `6e92674` | Dedicated atomic v1 boundary/replay handling |
| 2A | `71e7d91` | Opaque Organization portable UUID; historical migration test-harness fix `a99b986` |
| 2B | `20adf0a` | Source-owned envelope/items, frozen snapshots, protocol 2.0, required source GlobalStrainIdentity |
| 3A | `030a2da` | Read-only destination resolver; no reservation/materialization |
| 3B | `c82e6b2` | Read-only identity diagnostic/readiness evidence |
| 3C | `9ac0034` | At most one owned Strain per Organization + non-null global identity; NULL legacy allowed |
| 4 | `5a29b06` | Internal locked X allocator per Organization + Species + explicit provenance/NULL |
| 5 | `8fced89` | Portable source lineage projection and frozen snapshots; protocol 2.1 opt-in, default 2.0 |

- Portable IDs identify, never authorize. Missing source GlobalStrainIdentity rejects v2 construction; no inferred/attached identity. Lineage projects known predecessor ancestry only; disconnected families are valid; no inference from codes/Species/Strain/global ID. Nullable local Box bridges avoid fake foreign Boxes. Source 2.1 snapshot uses PostgreSQL SERIALIZABLE with retries.
- X counter starts at 1 in an empty namespace and above coherent explicit values/persisted counter thereafter, respects stored zero, explicit NULL provenance and rollback; gaps allowed. It is not universal manual-writer locking, Strain number uniqueness, issued AAA–BBB–X codes or destination materialization.
- **Phase 6 F — not implemented:** destination preflight, atomic/idempotent acceptance, incoming graph merge/continuation edge, Strain reuse/materialization and destination Boxes. No v2 acceptance endpoint/UI, receipt, inbox, file adapter or source-stock/lifecycle effect. V1 remains a separate one-data-row CSV path resolving Species + code, without mandatory AAA/local identity or portable lineage; foreign/unowned code collisions remain conflicts.
- Durable contract: [`transferts_csv.md`](docs/transferts_csv.md). Source-active-only policy, source quantity/status effect and cross-instance trust/authenticity remain unresolved.

## Species/AAA decisions and companion repository

- Read-only local verification: `C:\Users\antoc\POLYPBASE-ANALYSES` is clean; **main = local origin/main = `4623fb1a9f689ce1d65d32154bbdd1306e245cc2`**, `docs: record species AAA stakeholder decisions`. No fetch. Technical tooling milestone **`346512d`** remains integrated; historical post-integration validation **89 pytest tests passed**, independent technical review GREEN. Those tests were not rerun here.
- The committed `docs/SPECIES_AAA_ISSUES_AND_PROPOSED_CORRECTIONS.md` now records Étienne's answers, **superseding blanket “waiting for Étienne/Anaïs” assertions**. No reply date or separate Anaïs reply is established.
- Resolved examples: CLA retained for Cyanea lamarckii, CLC for Chrysaora lactea; gracilis retained for Lobonemoides; OBE for Obelia; AVA for Aurelia malayensis; CMU/CLV separated; COR/ATO handling; ATH-THA as Aurelia sp.4 and APH-PHI excluded from initialization; helvola and bellis retained locally; DD = Direct Développement; literal operational color/form/hybrid qualifiers retained **without biological interpretation of hybrid**.
- D22 confirmed **Thyroscyphus sp.2 / THY / strain 2**, example `THY-FGU-2.001`; earlier THI was a stakeholder typo, not another AAA. No new sp.1 identifier follows. D8/D9 historical corrections have explicit temporal/strain scope. Anthony's D5, D13–D16 and T03 future-name normalizations are already accepted, not pending taxonomy proposals.
- Source identifiers/labels remain literal evidence. Decisions about future references, local assignments, reviewed historical corrections, physical labels and persisted records are separate. The 2026 historical importer has its own narrow approved corrections; the companion ledger does not authorize generic database rewrites.
- **Final Species/AAA manifest approval remains F.** No approved assignment manifest or dedicated POLYPBASE Species/AAA importer verified. R02 Chrysaoa spelling, T04 Ralpharia formatting and T06/unidentified labels retain review limits; qualifier architecture and any biological meaning of hybrid are not inferred. Targets and institution scope of further persisted corrections remain unverified. Private generated CSV approval contents/hashes were not reopened in this refresh.
- Historical extraction baseline: 1,376 observations, 1,337 parseable, 39 unparseable, 126 AAA, 106 clean, 9 variants, 11 possible collisions, 6 reverse conflicts, 30 exact-Box conflicts, 22 malformed strings. Historical check-only/hash preservation and 15 targeted tests were reported at tooling delivery, not rerun now. Technical review/integration does not make the manifest import-ready.

## Git/worktrees and local work — verified 2026-10-14

**12 local branches, 9 registered worktrees**, including this documentation task (the transmission's 11/8 predates it). No staged changes in inspected worktrees. Ahead/behind numbers below are ancestry counts against `main`, **not pending patch counts**.

Worktree roots are under `C:\Users\antoc\worktrees\POLYPBASE\<name>\POLYPBASE`, except canonical main.

| Worktree / branch | HEAD | Ahead/behind main | Actual state |
|---|---|---|---|
| Canonical / `main` | `ec81a18` | 0/0 | Clean, matches local origin/main |
| `docs-sync-2026-10-14` / `docs/sync-2026-10-14` | `ec81a18` | 0/0 | Documentation synchronization; input transmissions pre-existing/untracked |
| `app-box-page` / `refactor/app-box-page` | `de23ed7` | 1/7 | Patch equivalent to integrated `c299df3`; untracked 271-line extraction audit retained |
| `box-dialog-redesign` / `fix/box-dialog-redesign` | `23a9f3e` | 0/41 | **C:** 12 tracked frontend files modified, no staged changes; unfinished proposal, not integrated |
| `box-movement-banner-polish` / `fix/box-movement-banner-polish` | `3643665` | 1/2 | Clean; patch equivalent to `9d20dba` |
| `box-read-freshness` / `fix/box-read-freshness` | `0703f19` | 0/2 | **C+D:** five tracked paths modified + new freshness test, no commit |
| `import-week-interval-science` / `feat/import-week-interval-science` | `06e6985` | 1/2 | Clean; patch equivalent to `ec81a18`, no later implementation inferred |
| `perf-box-list-query` / `perf/box-list-query` | `48e77b6` | 1/34 | Patch equivalent to `785ff3f`; three untracked `.qa-box-list/*.json` retained |
| `species-labels` / `fix/species-labels` | `ba9d5e4` | 0/45 | **C:** CURRENT_STATE notes + QR script diff; three proposed tests, not integrated |

Three additional branches, without registered worktrees or expected directories, remain: `refactor/app-create-box-panel` **`6d48d55`**, `fix/chart-deplacements` **`6eae3f6`**, `fix/pilotage-ci-tests` **`bfe11d7`**. `git cherry` reports all three patches already integrated (respectively `3b1fc92`, `49e7870`, `5cf3879`). Cause/date/actor of directory disappearance and fate of ignored/uncommitted artifacts are **H**, not proof of loss or archival. No outstanding unique committed patch was found among the remaining source branches.

### Active and retained work

- **M10.1 C+D:** `App.tsx`, `api/client.ts`, `app-operation-test-harness.mjs`, `test-app-history-routing.mjs`, `test-box-movement-inline.mjs`, new `test-box-read-freshness.mjs`. Read-only diff confirms resource barriers, scoped GET invalidation, guarded mutation/React publication, synchronous revision cleanup on organization-generation changes and scalar mutation watermark. These are local technical choices, **not main guarantees or server commit ordering**.
- Review sequence supplied: first High for delayed mutation responses → corrected and independently confirmed resolved; later Medium for unbounded cross-generation retention/full-map dispatch copy → writer reports corrected, **last independent re-review still missing**. Latest writer: **113/113 freshness and 449/449 targeted tests**, TS/CSS/build/diff green, sensitivity checks 5/4 expected failures. Not a fresh execution, mounted-browser QA, integration result or CI for M10.1.
- M10.1 base `0703f19` precedes both `9d20dba` and `ec81a18`. Combined integration is not verified; the inline movement test overlaps the polish and must preserve its final UX. Main PATCH movement currently ignores returned detail before GET refresh; the local patch changes publication handling, not movement business rules.
- **box-dialog-redesign C:** older MoveBox/Subculture/QR modal proposals and tests/styles/i18n. Transmission audit identifies incompatibility with current quantitative revision/allocations, destination revalidation and physical print sheet. Twelve-file local diff persists. Not accepted wholesale, not declared abandoned; selectively reconcile only after a separate decision.
- **species-labels C:** three QR tests cover literal scientific qualifiers/code, no identifier-derived taxonomy, and independence of label content from zero/NULL measurements. The latter does not directly test scientific zero semantics. Local state notes are not authoritative; ledger checked separately. Do not overwrite newer main files from this old worktree.
- **Retained evidence, not unfinished features:** `.qa-box-list/{initial-before,before,after}.json` (~18 MB; confidentiality not audited/copied) and `docs/audits/box-page-extraction.md` (271 lines). Its old build-timeout BLOCKED verdict is historical; later reviewer build and M9 integration/CI passed. No archival operation verified.
- **Cleanup suspended by Anthony.** Git transmission reports controlled removal totals of 42 branches/42 worktrees, with cherry-pick/adaptation evidence and a resolved interrupted removal. `.git/polypbase-local-cleanup-report.json` exists, but its entire contents/reflogs were not re-audited here. Do not infer deleted source SHAs are main ancestors, recreate worktrees, or resume cleanup from this documentation task.

## Independent review and historical validation evidence

These are **supplied milestone results**, not tests run during this refresh and not an independent review of this documentation diff.

| Milestone | Reported validation | Review / limitation |
|---|---|---|
| Imports 4A | Backend 1,269 total / 1,210 passed / 59 skipped; disposable PG 17.4 52/52 | Final GREEN; export N+1 fixed before closure |
| Temporal 4B.1 | Backend 1,348 total / 1,283 passed / 65 skipped; PG 200/200 | GREEN |
| Chronology 4B.2 | Initial 887 passed / 42 skipped; parent-partial correction 65/65 | Independent correction validation; no new migration |
| Reads 4B.3a | Writer 230 passed / 1 skipped; reviewer 73/73 | READY TO COMMIT, integrated |
| Interval 4B.3b | Writer 124 passed / 13 skipped; reviewer 122 passed / 1 skipped | READY TO COMMIT; main check/drift/push reported, no supplied post-integration test output |
| Weekly movements | Writer SQLite 1,314 passed / 65 skipped; PG 17.4 69 tests, 6 concurrency scenarios, 12 lock waits | CHECK bypass corrected, re-review green; unrelated global PG failures reproduced on baseline, **not a globally green PG suite** |
| Event distinction | Final writer 160 targeted; reviewer 24/24, 17/17, 14/14 | Export final-day finding corrected; READY TO COMMIT |
| M9 BoxPage | Reviewer 554/554, TS/diff/build passed | Old writer timeout superseded; integrated CI reported green |
| Inline movement/portage | Writer 1,589 full frontend + 83 targeted; reviewer 43 targeted | READY TO COMMIT; Chromium synthetic desktop/tablet |
| Movement polish | Worktree 1,592 full frontend + 46 targeted; build passed | Main build 1m01s + TS/CSS reported; **1,592 not claimed rerun on main**; no deployment |
| Transfer v1 | Main SQLite 18/18; pre-integration PG 2/2, no skips | GREEN; PG not rerun after cherry-pick |
| Transfer 2B | Main backend 579 total / 559 passed / 20 skipped | Check/drift/diff passed |
| Transfer 4 | PG 106 passed, including 8 concurrency scenarios; full 686 / 658 passed / 28 skipped | GREEN; reviewer did not rerun PG |
| Transfer 5 | PG 36 targeted; full 819 / 780 passed / 39 skipped | GREEN; reviewer did not rerun PG/full suite |
| M10.1 latest local | Writer 113/113 + 449/449, TS/CSS/build/diff | **Final Medium correction not independently re-reviewed**; not integrated |

Older integrated milestone evidence is retained historically: Emplacement `0d7985a` (408 backend, 15 skipped); Alerts removal (writer 514 backend passed/18 skipped plus targeted/frontend checks, independent READY FOR COMMIT); frontend consistency `96c5593` (main Inventory 24, Confirm 25, Labels 69, Zones 50; TS/CSS/build); measurement-history `2292a61` (review 28 measurement, 28 chart, 4 date tests, TS/CSS/build); Species integrity (2 PG concurrency tests); Équipe (independent stale-response corrections/re-review and PG invitation concurrency reported). These counts validate their milestones, not all subsequent main commits. No new physical-printer/tablet, screen-reader or production validation is claimed.

## Decisions, blockers and next logical steps

### Confirmed decisions to preserve

- Zero is real; tenant isolation and Django authority mandatory; preserve histories, authors, lineage and append-only audits. Administration desktop-only; French/English i18n; no implicit biological or operational inference.
- Week-only observation target is `iso_week`, **NULL day**, ISO Monday key, display “Semaine 40 · 2026” / “Week 40 · 2026”. Exact known dates stay exact. Chronologically relevant same-week subculture requires the **real exact observation date**, cancellation or date entry, **no force/keep-weekly bypass**; unresolved file blocked in full. Implementation remains gated.
- Ordinary movement quota/correction and final inline Origin → Destination UX are delivered decisions, not proposed redesigns.
- Species answers apply only within their recorded scope; historical strings unchanged, no inferred BBB/global identity/ownership. Retain backend BBB foundation; defer routine UI. References catalog and Alerts are abandoned, not normal pending merges.

### Product decisions still needed

1. Scientific unresolved-state display/operation policy, inverse subculture guard, legacy unknown occurrences and unknown child allocations; proof/permission/audit for precision correction. Same-day exact convention does not establish intra-day order.
2. Week-only period inclusion/overlap, age/freshness thresholds, movement-week location attribution and unknown daily temperature; no Monday/zone/temperature fabrication.
3. External observer names, account-deletion retention/presentation and source-file lifecycle. Private source storage/durable plan is planned; **30-day unapplied-upload retention is a proposal, not approved policy**.
4. Final Species/AAA manifest review/approval, remaining named issues and contextual AAA maintenance. No blanket waiting on already resolved Étienne answers and no generic importer authorization.
5. Transfer v2 source active-only policy, stock/quantity/status effect and cross-instance package trust. Phase 6 remains blocked on required decisions, not silently implemented.
6. Inactive unaccepted invitations if a change is needed; technical membership environment designation and historical location corrections require separate verified targets/authorization.

### Proposed technical ordering, not new product rules

1. Independent read-only review of this complete documentation diff, then Anthony decides integration. No commit/push here.
2. Close **M10.1**: final independent review, authorized commit/integration against actual main, preserve `9d20dba`/`ec81a18`, combined tests and CI. Only then address **M10.2** paginated-zone freshness; retain full-collection publication and GET-only recovery. No new writer launched by this refresh.
3. Scientific track: **4B.3c** explicit known/absent/unresolved contract without schema activation; then bidirectional guard, consumers/UI/exports, additive week-only schema with disposable PG QA, versioned planner baselines/readiness, durable guarded applicators and UI. Later tranche labels 4B.3d–g are provisional; dependencies, not labels, determine safe activation.
4. Species/AAA approved manifest and small contextual management design; Transfer v2 Phase 6 after its decisions; selective old-dialog/test/evidence preservation only if Anthony reopens those topics. Git cleanup remains suspended.
5. Release readiness and real-device QA are separate tasks. Probe connectivity, DNA enrichment and application WoRMS integration remain future/exploratory; existing fields/models do not prove connected features.

## Reconciliation notes and unverifiable scope

- **Time drift resolved:** old main/worktree inventory, ahead-of-upstream push step, pre-5-October urgency and uncommitted tablet polish are superseded by inspected Git and integrated commits. No old worktree was recreated.
- **Imports transmission orientation corrected:** its helper description reverses the relation at one point; implementation/tests define observation relative to operation, documented above. No biological decision inferred.
- **No overstatement of consumers:** Overview full-history is now delivered, but it does not receive persisted movement events. Week-only helper/read contracts do not mean persistence or scientific resolver integration.
- **Review drift resolved:** M9 old build BLOCKED is superseded; M10.1 latest writer success is not final independent approval. No B/E unique pending source patch found; C/D work remains.
- **Species drift resolved:** companion `4623fb1` decisions replace blanket pending claims; neither ledger nor local QR notes prove approved CSV fields or completed environment corrections. Anaïs response, private artifact status and actual corrected database records remain H where not evidenced.
- **Production remains H:** no current deployed SHA, applied migration list or later production diagnostics verified. A reported historical `016712b` deployment reference is not today's production state. Local main, reported push and green CI do not prove release, migrations or laboratory readiness.

Documentation-only validation and final diff are handed to an independent reviewer; this writer does not claim independent approval. No commit, push, merge, cherry-pick, cleanup, deployment or production/Neon access was performed.
