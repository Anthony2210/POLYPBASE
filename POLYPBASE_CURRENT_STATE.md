# POLYPBASE CURRENT STATE

Last updated: 2026-09-17

## Repository

- Canonical sanitized repository: `C:\Users\antoc\POLYPBASE`
- Canonical branch: `main`
- Current `main` / `origin/main` HEAD: `4856076` - `feat: enforce weekly biological measurements`
- `main` is clean and synchronized with `origin/main` after the 2026-09-17 push.
- Archive-retention review baseline: `0c7bf77848da147e6aa1cbfb36563f2a0fd2647f`
- `origin` fetch/push: `https://github.com/Anthony2210/POLYPBASE.git`
- The public history rewrite is complete and independently verified. Normal development continues from sanitized `main`.

## History migration

- The public history rewrite (privacy cleanup) is DONE and independently verified.
- Current remote heads verified on 2026-09-16: `main`, `feat/phone-bottom-navigation`, `feat/tablet-navigation-qr`, `fix/admin-team-access-ux`.
- Earlier cleanup removed `ajout-notebook-nettoyage`, `docs/mcd`, and `import-excel-historique`, but GitHub does not currently contain only `main`. Any cleanup of the remaining three remote branches requires separate review and Anthony approval.
- `C:\Users\antoc\POLYPBASE` is again the canonical development repository; it no longer holds the old public history.
- Prior operational records state that GitHub Support cleanup for the old pull-request and history references was completed under request `#4748399`, including PR #3/#4 and representative historical references. This status was **not revalidated by the 2026-09-16 local Git review**.

## Old history archive

- The previously documented path `C:\Users\antoc\POLYPBASE_OLD_HISTORY_20260911` is **not present on the current local machine as verified on 2026-09-16**.
- Earlier continuity notes described a quarantined old-history archive with branches/stashes and a disabled push URL. Those details are historical attestations and must not be treated as currently verified local state.
- Before any archive-retirement or deletion action, first confirm where that archive is actually stored now and re-verify its retention status.
- No old-history ref should be merged, rebased, or transplanted directly into sanitized history unless separately inspected and intentionally recreated on sanitized ancestry.

### Old archive deletion blockers

- Confirm the archive's current storage location and existence.
- Perform a fresh read-only preflight against the actual archive before any deletion.
- Explicit destructive-action approval from Anthony is required.

## Private retention

- Prior cleanup documentation records that required private security material was retained outside Git in encrypted storage and independently reviewed.
- This is an operational attestation from the privacy-cleanup sequence, not something revalidated from the current Git workspace on 2026-09-16.
- It is not part of the public repository.
- Reconfirm the retention location before any destructive old-archive retirement step.

## Private analysis repository

- The following are prior operational records from the project sequence; they were **not revalidated by the 2026-09-16 local Git review**.
- `Anthony2210/POLYPBASE-ANALYSES` Phase A is complete and published.
- `POLYPBASE-ANALYSES` is the private companion repository for notebooks, analysis scripts, ML/statistical experiments, scientific figures/results, and analysis-specific documentation/dependencies.
- Real raw institutional datasets remain outside Git by default, including outside `POLYPBASE-ANALYSES`.
- Internal engineering docs and audits are not scientific analysis material and should not automatically move to `POLYPBASE-ANALYSES`.

## BoxInsights

- The following are prior project records; they were **not revalidated by the 2026-09-16 local Git review**.
- The BoxInsights experiment was intentionally discarded and is fully retired locally.
- The linked worktree was force-removed and local branch `experiment/boxinsights-scientific-soft` was deleted.
- Its uncommitted frontend work and Playwright output were intentionally discarded.
- BoxInsights is no longer a blocker for repository path normalization and is no longer an active chantier.

## Temporary residual

- The previously documented path `C:\Users\antoc\POLYPBASE_SPLIT_RESIDUAL_20260911` is **not present on the current local machine as verified on 2026-09-16**.
- Do not assume it was safely retired or deleted based only on its absence here. Confirm its actual storage/removal status before any recovery-cleanup action.

## Production

- Current production state was **not revalidated from this local Git review**.
- Last documented operational attestation: no deployment occurred during the history redesign, the 2026-09-16 integration, or the 2026-09-17 weekly integration.
- Last documented deployed old-history source: `68771909ebf163401bb439d6cb412bfdec72eecb`.
- Mapped sanitized equivalent recorded in prior continuity notes: `3c691217ce406c279b2b36288f22a3a7b419b7d8`.
- Recent integrated commits `045c608` and `4856076` are pushed to GitHub, but neither deployment is confirmed.
- Current `main` at `4856076` is confirmed PUSHED to GitHub; deployment status must be verified separately before any production action.
- Production realignment/deployment remains a separate operation and must not implicitly deploy current `main`.

## PostgreSQL QA

- Prior project validation established disposable local PostgreSQL 17 Docker QA as the approved method for transaction, locking, and concurrency checks.
- Historical recorded result for S-01: 5/5 real PostgreSQL concurrency tests passed; accounts suite 78/78 passed on PostgreSQL.
- These historical QA results were not rerun as part of the CURRENT_STATE verification.
- PostgreSQL concurrency for weekly biological measurements was validated earlier in the disposable local PostgreSQL QA environment. The final independent review inspected concurrency statically because its own local credentials were unavailable (do not claim the final independent reviewer reran PostgreSQL concurrency).
- Never use Neon or production for QA.

## Completed milestones

- Milestone hashes below from the privacy sequence are pre-rewrite references; verified sanitized equivalents are shown where relevant.
- Deployment labels below are **historical operational records** and were not revalidated by the local Git review.
- `9910b15` -> `e317dd568b63a99208ee336f43a564f76b623574` - improve team access management - DONE, PUSHED, NOT DEPLOYED.
- `23fa202` -> `1ad2788373fcb4a54d529f47964931de3b651d4e` - align sidebar brand and navigation - DONE, PUSHED, NOT DEPLOYED.
- `c6972a2` -> `85ca53f0f30565f136cce5105748ec4fe308b186` - make invitation audit atomic (S-03) - DONE, independently reviewed, integrated to `main`, PUSHED, no migration, NOT DEPLOYED.
- `df31743` -> `c13583f0678a659bf11b2210593e4a5f36af2778` - prevent last admin concurrency race (S-01) - DONE, independently reviewed, PostgreSQL validated, integrated to `main`, PUSHED, no migration, NOT DEPLOYED.
- `9e9836b` - add account and institution action APIs - DONE, PUSHED, NOT DEPLOYED.
- `141758b` - add institution Responsable authority - DONE, PUSHED, NOT DEPLOYED.
- `8ac61cf` - add personal and institution action history - DONE, PUSHED, NOT DEPLOYED.
- `f1937a0` - improve beta label printing - DONE, PUSHED, NOT DEPLOYED.
- `69457f9` - unify global page layout - DONE, PUSHED, NOT DEPLOYED.
- `84c4730` - redesign action history - DONE, independently reviewed, cherry-picked into `main`, PUSHED, no migration, NOT DEPLOYED.
- `045c608` - improve action history presentation - DONE, independently reviewed, integrated to `main`, PUSHED, no migration, NOT DEPLOYED.
- `4856076` - enforce weekly biological measurements - DONE, independently reviewed, cherry-picked into `main`, PUSHED, migration `0005_biologicalmeasurement_week_start.py`, NOT DEPLOYED.

## Action history redesign - CLOSED

- The following are recorded completion and validation results from the 2026-09-16 action-history delivery. They were **not independently rerun as part of the CURRENT_STATE repository-consistency review**.
- Implementation worktree: `C:\Users\antoc\worktrees\POLYPBASE\history-timeline-redesign\POLYPBASE`
- Worktree branch: `feat/history-timeline-redesign`
- Reviewed worktree commit: `eaed8d698fac451f5d4237426cbcca58be295f5c`
- Cherry-picked `main` commit: `84c4730`
- Do not later merge the feature branch into `main`; this chantier was integrated by cherry-pick.
- Profile `Mes actions` and Administration `Actions de l'institution` are active.
- Administration history uses direct business summaries with stable hour / author / action columns, safe box previews, minimal disclosure, canonical `•••` contextual actions, and in-place linked measurement chains.
- Measurement corrections from historical rows always target the current live measurement and append a new immutable audit event.
- Linked measurement actions use explicit stable `measurement_id` only and remain institution-scoped.
- Personal-history payload is intentionally narrower; alert/species/strain PK-backed identifiers are suppressed from `resource.identifier` / `resource.label`.
- Visible Administration family filters currently show 8 families; `Référentiels` remains supported by the backend taxonomy and appears under `Toutes`, but its dedicated pill is intentionally hidden for now.
- Operational attestation from the delivery sequence: final manual visual QA was accepted by Anthony.
- Final independent review and targeted verification ended with no remaining findings.

### Final validation on `main` (84c4730)

- Recorded delivery validation results (not rerun by the CURRENT_STATE repository-consistency review):
- Frontend audit suite: 63 passed, 0 failed.
- Frontend TypeScript: passed.
- CSS architecture check: passed, 30 files.
- Frontend production build: passed, 2201 modules transformed.
- Backend full suite: 340 tests run, 14 skipped, 0 failed.
- Django system check: passed.
- `makemigrations --check --dry-run`: no changes detected.
- `git diff --check`: passed.
- `main` was clean and synchronized with `origin/main` after push.

### Action history follow-up (045c608) - CLOSED

- The action-history follow-up is CLOSED.
- Main commit: `045c608` - `fix: improve action history presentation`.
- It followed the earlier action-history redesign at `84c4730`.
- Relevant completed corrections include:
  - Grouped subculture wording with parent/children preservation.
  - Fallback parent visibility when rich child summary cannot render.
  - No duplicate parent rendering.
  - Current child code resolution remains organization-scoped.
  - Legacy archived/lost/stopped display normalization.
  - Account/audit presentation improvements.
  - Linked-action author presentation.
  - Scientific zero preserved.
  - Audit history remains append-only.
- Final independent targeted review:
  - Critical: none
  - High: none
  - Medium: none
  - Low: none
  - READY TO COMMIT
- After integration on `main`, action-history frontend tests were: 74 passed, 0 failed.

## Weekly biological measurements - CLOSED

- This chantier is CLOSED, IMPLEMENTED, REVIEWED, INTEGRATED and PUSHED.
- Feature worktree commit: `2535a4c` - `feat: enforce weekly biological measurements`.
- Cherry-picked `main` commit: `4856076` - `feat: enforce weekly biological measurements`.
- Do not later merge the feature branch into `main`; this chantier was integrated by cherry-pick.
- Implemented product rules:
  - Maximum one `BiologicalMeasurement` per box per ISO week, Monday–Sunday.
  - Persisted `week_start` with database uniqueness.
  - Scientific 0 remains a real measurement.
  - 0/0 occupies the weekly slot.
  - Lab technician may correct only during the first 24h after original `created_at`.
  - Exact `created_at` + 24h equality is locked.
  - Correction does not reset the deadline.
  - Active-institution admin may correct regardless of age.
  - Viewer is read-only.
  - Inactive box cannot receive a new measurement.
  - Authorized admin may correct an existing historical measurement on an inactive box.
  - Imports may create historical measurements and still occupy weekly slots.
  - Backend active-institution role is authoritative.
  - Frontend uses server capabilities rather than recomputing role/deadline.
  - FR/EN supported.
  - Weekly form and manual browser QA accepted.
  - `+/-` stepper pressed state is isolated per control.
- Migration: `backend/apps/measurements/migrations/0005_biologicalmeasurement_week_start.py`
  - Independently reviewed for: nullable add, conflict/backfill guard, non-null transition, and replacement of per-date uniqueness by per-week uniqueness.
  - PostgreSQL concurrency was validated earlier in the disposable local PostgreSQL QA environment. The final independent review inspected concurrency statically because its own local credentials were unavailable (do not claim the final independent reviewer reran PostgreSQL concurrency).
- Final integrated validation on `main` (after cherry-picking weekly onto `main` already containing `045c608`):
  - Backend targeted integrated suite: 227 tests run, 8 skipped, 0 failed.
  - Django check: passed.
  - `makemigrations --check --dry-run`: no changes detected.
  - Frontend `test:audit`: 74/74 passed.
  - Frontend `test:measurements`: 5/5 passed.
  - Frontend `typecheck`: passed.
  - CSS architecture check: passed, 30 files.
  - Frontend production build: passed.
  - `git diff --check` for `origin/main..HEAD` before push: passed.
  - `main` was pushed successfully; current synchronized HEAD: `4856076`.

## Emplacement detail redesign - AUDIT COMPLETE, IMPLEMENTATION NOT STARTED

- A read-only product/UX/architecture audit of the Emplacement detail page is complete.
- The page should be treated as a substantial redesign, not a tablet-CSS bugfix.
- Étienne feedback to preserve in the future writer scope includes: tablet usability, visible back navigation, adaptive measurement/temperature visualization, occupancy/capacity instead of the redundant progress indicator, manual salinity request, removal/replacement of `Derniers comptages` and `Activités récentes`, and clarification of misleading labels such as `vivantes` / `À vérifier`.
- Current code semantics verified by the audit: temperature Min/Max are measured daily extrema, not configurable tolerance thresholds; `ThermalZone.capacity` exists; current backend `box_count` counts active boxes; zone-level `SalinityMeasurement` exists but has no write API/form yet.
- Do not invent final salinity semantics or capacity blocking rules before product arbitration.
- A dedicated implementation worktree should be created only after the remaining product decisions are fixed.

### Product decisions still needed before Emplacement writer

1. Manual zone salinity: confirm whether it represents a real recurring zone-level measurement using `SalinityMeasurement`, distinct from per-box biological-measurement salinity.
2. Temperature limits: keep the current ±1 °C alert rule and treat Min/Max only as measured extrema, or introduce future configurable tolerance thresholds.
3. Capacity overflow: informational/warning only versus backend blocking when capacity is reached/exceeded.
4. Final redesign direction/layout to implement after the audit.

## Homepage / Suivi labo redesign - QUEUED FOR IMPLEMENTATION

- Homepage / Suivi labo is now the next queued implementation chantier.
- Locked product direction already decided:
  - Desktop and tablet only; mobile remains untouched.
  - Desktop page has no QR code.
  - Tablet page has no QR code because persistent tablet navigation owns QR.
  - Search is the primary visual anchor.
  - Suggestions must be first-class and not awkward overlays.
  - Recents should not use rigid equal cells.
  - Create-box action is tertiary.
  - Desktop wording should be "Recherche", not "Recherche ou scan".
  - Stale global search state must be cleared on genuine home arrival.
  - Remove navigation-side writes that cause stale search state.
- Technical implementation details must not be invented prematurely; a dedicated worktree should be created from current `main` when implementation starts.

## Other upcoming product work

- Weekly biological measurement rule is implemented, integrated, and pushed (see section above).
- Emplacement detail redesign remains audited but waiting for product decisions (see section above).
- Probe work remains future scope: two probes per thermal zone/cabinet, combined monitoring and divergence/target alerts are product direction, not an instruction to change current probe behavior opportunistically.
- Global ID/lineage across institutions remains a later architecture chantier.

## Active / frozen work

- Action history follow-up (`045c608`) is closed and integrated.
- Weekly biological measurements (`4856076`) is closed, integrated, and pushed.
- The worktrees for action history and weekly measurements may still physically exist locally, but they are no longer active writers.
- Neither feature branch should later be merged into `main` because integration was done via cherry-pick.
- Homepage / Suivi labo redesign is the next queued implementation chantier.
- Emplacement detail redesign remains audited but blocked on product arbitration before implementation.
- Several other local worktrees also remain present (associated with `admin-actions`, `admin-team-access`, `audit`, `beta-label`, `global-page-layout`, and `institution-responsable`); they must not be assumed obsolete or deleted without separate inspection.
- Remote branches currently present besides `main`: `feat/phone-bottom-navigation`, `feat/tablet-navigation-qr`, `fix/admin-team-access-ux`. Their cleanup or integration is a separate task.
- S-01 and S-03 remain complete according to prior project records; deployment status is not revalidated here.
- BoxInsights remains retired according to prior project records.

## Contractual roadmap

1. Fix minor and major bugs.
2. Refine scientific exports with Delphine Bonnet.
3. Finalize English and Japanese translations.
4. Connect control temperature probes.
5. Open/connect POLYPBASE to partner institutions in France and abroad.
6. Build large-dataset visualization.
7. Build ephyrae prediction and culture-follow-up assistance.
8. Build climatic-accident analysis.

## Next operational work

1. Finish this CURRENT_STATE update.
2. Verify and clean up the two closed action-history / weekly worktrees when Anthony chooses.
3. Create a fresh homepage redesign worktree from current `main`.
4. Implement the already-decided desktop/tablet Suivi labo redesign.
5. Keep Emplacement pending its product decisions.
6. Production verification/deployment remains a separate and explicit operation.
7. Continue the contractual roadmap after the current bug/refactor sequence.

## Important local state

- `main` / `origin/main` are synchronized at `4856076` (`feat: enforce weekly biological measurements`).
- Recent integrated commits on `main`: `045c608` (action history follow-up) and `4856076` (weekly biological measurements).
- Both recent chantiers were integrated into `main` via cherry-pick; their feature branches must not later be merged into `main`.
- Neither `045c608` nor `4856076` has been confirmed as deployed to production; deployment remains a separate explicit operation.
- Current remote heads verified on 2026-09-16: `main`, `feat/phone-bottom-navigation`, `feat/tablet-navigation-qr`, `fix/admin-team-access-ux`.
- Multiple local worktrees remain. Inspect them individually before deletion or branch cleanup.
- The previously documented old-history archive and split-residual paths are not present on the current local machine as verified on 2026-09-16; their actual storage/retention state must be reconfirmed before cleanup.
- GitHub Support request `#4748399`, PR #3/#4 cleanup, private-retention state, `POLYPBASE-ANALYSES` publication state, BoxInsights retirement state, manual QA acceptance, recorded validation results, and production deployment state are historical/operational attestations unless explicitly marked as verified by the current local repository check.
- `test_membership_concurrency.py` is part of the S-01 tracked work if present; it is not local-only state.
- The history worktree branch contains the reviewed source commit `eaed8d6`; because `main` received it by cherry-pick as `84c4730`, do not later merge the branch into `main`.
