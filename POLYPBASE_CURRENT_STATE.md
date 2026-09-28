# POLYPBASE CURRENT STATE

Last updated: 2026-09-28

## Repository

- Canonical repository: `C:\Users\antoc\POLYPBASE`.
- `main` and `origin/main` are synchronized at `cf8f2fc` (`feat: scope strains by institution`), per current Git status and log.
- Working tree was clean before this document update.
- The sanitized history migration is complete. Do not transplant commits from the old history into this ancestry without separate review.

## Integrated milestones since the prior documented baseline

Integrated milestones after the previously documented `4856076` weekly-measurements baseline include:

- `cfd0a8e` - corrected weekly measurement and administration workflows; improved measurement correction/audit presentation and inventory preview. Backend-authoritative weekly conflicts remain non-mutating.
- `d99b117` - improved weekly measurement summary/editor UX.
- `b8e5647` - redesigned Pilotage / Suivi labo and refined frontend UX, including Profile, search/box lookup and route safety. This also moves the previously queued Pilotage redesign to DONE.
- `0d7985a` - delivered the Emplacement refinement described below.
- `5fec1f6` - added the global strain identity foundation.
- `ca94726` - added the strain organization ownership foundation.
- `a85dd47` - made Species and Strain API mutations, translations, and AuditLog writes atomic.
- `cf8f2fc` - scoped Strain reads and writes by institution and enforced ownership in operational references.

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

### Taxonomy / Strain identity and institution scoping

- `GlobalStrainIdentity` is a shared biological identity foundation with an opaque UUID. `Strain.global_identity` is nullable; legacy Strains were not backfilled. Global identity is not an authorization boundary and does not expose another institution's Strains or Boxes.
- `Strain.organization` is nullable. New interactive Strains are assigned server-side to the authoritative active institution. Owned Strains are institution-scoped in operational references and writes; Strain PATCH is limited to the owning institution, and Box creation validates the submitted Strain PK server-side.
- Historical `Strain.organization=NULL` rows remain unresolved/shared: no automatic ownership or Global ID inference. They are read-only through institution APIs. An institution may reuse one operationally only if it already has at least one Box referencing it; historical/inactive Boxes count. Existing Boxes remain readable through `Box.organization`, and subculture from an authorized legacy Box continues. Transfer v1 does not infer biological identity from a NULL code match.
- Historical imports respect target institution ownership and reject unsafe ownership collisions; transfer v1 respects destination ownership and rejects foreign/NULL ambiguous collisions. Demo seeding is ownership-guarded. The future versioned import protocol is not implemented.
- The existing global `(species, code)` uniqueness remains unchanged. Two institutions therefore cannot yet independently create the same local representation; this is a current limitation, not the final target architecture.
- Product direction for local codes remains `AAA-BBB-X.YYY`: AAA is institution-local Species code, BBB institution-local provenance/origin code, X is the strain number scoped to Species + Provenance within the institution, and YYY is the box number within the local strain. Existing historical Box identifiers should remain stable. GlobalStrainIdentity remains separate from local operational codes; allocation and migration rules are unresolved.

### Other current behavior

- Institution Responsable authority, Profile/action-history workflows and the labels/subculture fixes listed above are integrated. No specific institution is part of reusable product rules.
- Probe integration/combined monitoring and divergence alerts remain future product direction, not current connected behavior.
- The global identity foundation exists, but a transfer protocol carrying GlobalStrainIdentity, DNA/provenance enrichment, and WoRMS integration remain future work.

## POLYPBASE-ANALYSES

- Private companion repository for notebooks, analysis scripts, ML/statistical work, scientific figures/results, and analysis-specific docs/dependencies. Raw institutional datasets remain outside Git by default.
- Current `main` and `origin/main` are synchronized at `43552ad` (`fix: correct EDA species box context`); repository status was clean. Latest log includes `57e5f65` (shared measurement parser in EDA), following `3d7dd43` (historical EDA week mapping correction) and `565e8cb` (EDA temperature-context integration).
- Major completed refactor milestones: reusable analysis foundation; historical Excel week and temperature contracts; anomaly notebook week-parser integration; import notebook temperature integration; EDA temperature context and historical week mapping; EDA shared measurement parsing; EDA species/box context correction. Detailed scope and parity results are recorded in `POLYPBASE-ANALYSES/docs/ANALYSIS_REFACTOR_LOG.md`.
- Latest validation recorded in that log: 74 pytest tests passed and `git diff --check` clean. Historical parity audits were recorded for the relevant notebook changes; they were not rerun in this state review.
- Next documented analysis step: read-only audit of remaining `Pennaria disticha` taxonomy/attribution cases, excluding the confirmed 2026 Hydrozoa row 161 association. Excel color handling and consumer harmonization remain deferred; no unconfirmed taxonomic rule should be generalized.
- Additional ANALYSES worktrees/branches exist (`docs/analysis-refactor-log` and several `refactor/*`); they are historical milestone branches/worktrees, not evidence of work pending integration into `main`. `local-import-20260922` is also present and diverged; its purpose/status requires separate inspection before action.

## Branches and worktrees

- Canonical worktree: `main` at `ca94726`, synchronized with `origin/main`; clean before this document update.
- `git worktree list --porcelain` currently registers 13 secondary POLYPBASE worktrees, including the global-strain-identity and strain-organization foundation worktrees. Their individual status and integration/cleanup readiness were not checked here; inspect before any cleanup. No worktree was removed or changed.

## History retention and private material

- Prior operational records say GitHub history cleanup (including request `#4748399` and PR #3/#4 references) and encrypted private retention were completed/reviewed. These attestations were not revalidated in this task.
- The old-history archive and split-residual paths were previously absent from this machine; their actual storage and retention are unknown. Confirm the real location and perform a fresh read-only preflight before any retirement/deletion. Destructive action requires explicit approval.
- BoxInsights retirement is an older operational attestation, not revalidated here. Do not treat it as a current blocker or initiate cleanup from this document.

## Production and PostgreSQL QA

- **Production: NOT REVALIDATED.** No production or Neon access was performed. Integration/push does not establish deployment. Current deployed commit and production state are unknown; verify separately through the authorized deployment procedure before any operation.
- Never use Neon or production for QA.
- Disposable local PostgreSQL QA is the approved approach for transaction/locking/concurrency validation. Previously recorded S-01 and weekly-measurement results are historical validations, not rerun here. SQLite alone does not establish PostgreSQL concurrency behavior.

## Next operational work

1. **Next architectural stage:** design the institution-local coding model and safe transition away from global `(species, code)` uniqueness. Before implementation, resolve institution-local Species code representation; provenance/origin representation and local BBB code; X allocation scope and concurrency; interaction with GlobalStrainIdentity; legacy NULL/localization transition; prerequisites for changing uniqueness; historical Box-code preservation; and impacts on imports, transfers, and selectors. Do not invent unresolved allocation or migration rules.
2. Later: a versioned import protocol with validation, normalization, preflight, ambiguity resolution, transactional apply, audit/report, and idempotence; a transfer protocol carrying GlobalStrainIdentity; DNA/provenance enrichment; and WoRMS integration. None is implemented.
3. Continue the documented read-only analysis of remaining `Pennaria disticha` attribution cases in `POLYPBASE-ANALYSES`; do not infer biology or authorization rules.
4. Keep probe connectivity, exports and longer-term roadmap items as separate future decisions/work. Inspect secondary worktrees before any cleanup.
5. Verify production separately only when explicitly requested and through the deployment workflow.
