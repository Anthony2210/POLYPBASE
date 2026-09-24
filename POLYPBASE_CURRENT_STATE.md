# POLYPBASE CURRENT STATE

Last updated: 2026-09-24

## Repository

- Canonical repository: `C:\Users\antoc\POLYPBASE`.
- `main` and `origin/main` are synchronized at `0d7985a` (`feat: improve thermal location management`); no local commits ahead or behind were found.
- Working tree was clean except for the pre-existing, user-edited changes to this file. Those edits were inspected and relevant analysis-repository information was retained and corrected below.
- The sanitized history migration is complete. Do not transplant commits from the old history into this ancestry without separate review.

## Integrated milestones since the prior documented baseline

The previous committed state document is `e3982db` (`docs: update current project state`). The following first-parent commits are now integrated after the previously documented `4856076` weekly-measurements baseline:

- `cfd0a8e` - corrected weekly measurement and administration workflows; improved measurement correction/audit presentation and inventory preview. Backend-authoritative weekly conflicts remain non-mutating.
- `d99b117` - improved weekly measurement summary/editor UX.
- `b8e5647` - redesigned Pilotage / Suivi labo and refined frontend UX, including Profile, search/box lookup and route safety. This also moves the previously queued Pilotage redesign to DONE.
- `0d7985a` - delivered the Emplacement refinement described below.

Other meaningful earlier integrated changes not reflected in the old milestone list:

- `e2ae924` - labels page alignment.
- `09623b3` - extracted and corrected subculture child-code generation.
- `f41b0ee` - hardened laboratory configuration integrity.

The established action history redesign/follow-up (`84c4730`, `045c608`) and weekly biological measurement rule (`4856076`) remain integrated. Action history remains append-only; measurement correction targets the current record and preserves audit history.

## Product state

### Biological measurements

- Maximum one biological measurement per box per ISO week (Monday–Sunday); backend/database enforce the rule. A true `0/0` occupies that week.
- Interactive POST never silently changes an existing measurement; conflict is returned, and corrections use the update flow. `0` remains a real scientific value.
- The UI uses server-provided correction capabilities. The detailed edit permissions and deadline remain those implemented by Django; see `docs/context/measurements-integrity.md`.
- Measurement summary/editor UX was refined in `d99b117`; corrections and audit/inventory presentation were further fixed in `cfd0a8e`.

### Emplacement - DONE / integrated

`0d7985a` is present in both `main` and `origin/main`. The separate feature worktree commit is `4e02c2f`; do not merge it later. Delivery notes record manual visual QA accepted by Anthony and independent review with 0 findings at every severity. The recorded post-cherry-pick validation was: backend 408 tests (15 skipped, 0 failed), Django check and migration check passed; frontend zones 28/28, charts 14/14, API 8/8, Inventory 8/8, TypeScript, CSS architecture (30 files), production build (2,206 modules) and `git diff --check` passed. These delivery validations were not rerun during this state review.

- Thermal: adaptive factual temperature scale; target/consigne is distinct from observed values; Min/Max are observed extrema, not invented alert thresholds. Compact action/modal supports manual temperature entry. Scientific zero is preserved.
- Salinity: each observation creates a recurring `SalinityMeasurement`; new readings do not overwrite prior readings. Emplacement currently shows only the latest reading; persisted history is not exposed in this UI. Correction is backend-authorized strictly before `created_at + 24h`; equality is locked. Editable rows show a pencil; locked state offers `+` for a new reading. No admin override; corrections are audited; zero PSU is valid.
- Occupancy: capacity is informational, does not block movements, and over-capacity remains factual; zero and null are distinct.
- Movement: `BoxLocation` is canonical (`starts_at` arrival, `ends_at` departure); repeated stays and inactive historical boxes are retained. UI includes recent entries/exits, an 8-ISO-week movement chart, and paginated direction-filtered history; operational movement UI does not show actor.
- Zone boxes group Species > Strain, use canonical current-location start and latest biological measurement, preserve zero, and use the rich `BoxTrackingPreview` in Inventory.
- Limitation: date-only biological measurements cannot be reliably attributed to a location stay when a box moved on that same day. Do not infer P/E variation by stay in this ambiguous case.

### Other current behavior

- Institution Responsable authority, Profile/action-history workflows and the labels/subculture fixes listed above are integrated. No specific institution is part of reusable product rules.
- Probe integration/combined monitoring and divergence alerts remain future product direction, not current connected behavior.
- Global ID/lineage across institutions and the remaining contractual roadmap are future work.

## POLYPBASE-ANALYSES

- Private companion repository for notebooks, analysis scripts, ML/statistical work, scientific figures/results, and analysis-specific docs/dependencies. Raw institutional datasets remain outside Git by default.
- Current `main` and `origin/main` are synchronized at `43552ad` (`fix: correct EDA species box context`); repository status was clean. Latest log includes `57e5f65` (shared measurement parser in EDA), following `3d7dd43` (historical EDA week mapping correction) and `565e8cb` (EDA temperature-context integration).
- Major completed refactor milestones: reusable analysis foundation; historical Excel week and temperature contracts; anomaly notebook week-parser integration; import notebook temperature integration; EDA temperature context and historical week mapping; EDA shared measurement parsing; EDA species/box context correction. Detailed scope and parity results are recorded in `POLYPBASE-ANALYSES/docs/ANALYSIS_REFACTOR_LOG.md`.
- Latest validation recorded in that log: 74 pytest tests passed and `git diff --check` clean. Historical parity audits were recorded for the relevant notebook changes; they were not rerun in this state review.
- Next documented analysis step: read-only audit of remaining `Pennaria disticha` taxonomy/attribution cases, excluding the confirmed 2026 Hydrozoa row 161 association. Excel color handling and consumer harmonization remain deferred; no unconfirmed taxonomic rule should be generalized.
- Additional ANALYSES worktrees/branches exist (`docs/analysis-refactor-log` and several `refactor/*`); they are historical milestone branches/worktrees, not evidence of work pending integration into `main`. `local-import-20260922` is also present and diverged; its purpose/status requires separate inspection before action.

## Branches and worktrees

`POLYPBASE` worktrees registered in the inspected Git state (canonical worktree plus five secondary worktrees):

- Canonical `main` at `0d7985a`: dirty only because `POLYPBASE_CURRENT_STATE.md` has documentation edits; main matches `origin/main`.
- `feat/emplacement-refinement` at `4e02c2f`: clean. Its feature change is confirmed integrated by cherry-pick as `0d7985a`; the source commit itself is not an ancestor of `main`. Completed worktree, still present.
- `feat/measurement-summary-ux` at `fcbdd13`: clean; branch is ahead 1 / behind 3 relative to `origin/main`. The worktree HEAD is not an ancestor of `main`; the corresponding summary UX milestone is present on `main` as `d99b117`. Clean historical branch/worktree; no local uncommitted work found.
- `feat/pilotage-home-redesign` at `5b16714`: clean. Its HEAD is not an ancestor of `main`; the delivered Pilotage redesign is on `main` as `b8e5647`. Clean historical branch/worktree; no local uncommitted work found.
- `fix/action-journal-cleanup` at `b8e5647`: **dirty**. Seventeen tracked files are modified (accounts/audit/cultures/taxonomy APIs and tests, audit presentation components/utilities, translations and frontend audit tests; 980 insertions / 244 deletions). The committed HEAD `b8e5647` is integrated in `main`, but these uncommitted changes are not part of that commit and are not proven integrated. Classify as **unintegrated local work exists**; preserve it and do not treat the branch as simply completed.
- `fix/measurement-history-inventory` at `11bd79c`: clean. Its HEAD is not an ancestor of `main`; `cfd0a8e` is the corresponding integrated workflow milestone on `main`. No local uncommitted work found.

All five secondary worktrees were individually checked with `status --short --branch` and `log -1`; diffs were checked where status was dirty. No worktree or branch was changed. Apart from the action-journal local diff, no uncommitted work was found. That action-journal diff is the previously undocumented active/unintegrated work requiring separate review. Do not merge or clean any of these worktrees based only on these classifications. `main` has no divergence from `origin/main`; the previous remote feature-ref list was not revalidated.

## History retention and private material

- Prior operational records say GitHub history cleanup (including request `#4748399` and PR #3/#4 references) and encrypted private retention were completed/reviewed. These attestations were not revalidated in this task.
- The old-history archive and split-residual paths were previously absent from this machine; their actual storage and retention are unknown. Confirm the real location and perform a fresh read-only preflight before any retirement/deletion. Destructive action requires explicit approval.
- BoxInsights retirement is an older operational attestation, not revalidated here. Do not treat it as a current blocker or initiate cleanup from this document.

## Production and PostgreSQL QA

- **Production: NOT REVALIDATED.** No production or Neon access was performed. Integration/push does not establish deployment. Current deployed commit and production state are unknown; verify separately through the authorized deployment procedure before any operation.
- Never use Neon or production for QA.
- Disposable local PostgreSQL QA is the approved approach for transaction/locking/concurrency validation. Previously recorded S-01 and weekly-measurement results are historical validations, not rerun here. SQLite alone does not establish PostgreSQL concurrency behavior.

## Next operational work

1. Continue the documented read-only analysis of remaining `Pennaria disticha` attribution cases in `POLYPBASE-ANALYSES`.
2. Address fixes/refinements surfaced by that analysis or normal product use; do not infer biology or authorization rules.
3. Keep probe connectivity, cross-institution global identity/lineage, exports and longer-term roadmap items as separate future decisions/work.
4. Inspect leftover worktrees before any cleanup; none were cleaned during this task.
5. Verify production separately only when explicitly requested and through the deployment workflow.
