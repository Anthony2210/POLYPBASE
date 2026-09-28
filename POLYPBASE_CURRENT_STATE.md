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
- `cc4c980` - added the institution-local code schema foundation; it is not yet connected to Strain runtime behavior.

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

- `GlobalStrainIdentity` is a shared opaque biological identity. `Strain.global_identity` is nullable; legacy Strains were not backfilled. It is independent of local operational codes, is not an authorization boundary, and must never be inferred from local-code equality.
- New operational Strains are institution-owned. Runtime references, writes, and Box association are institution-scoped. Approved compatibility for legacy `Strain.organization=NULL` remains active; no automatic ownership or Global ID inference is made.
- Species/Strain mutations, translations, and `AuditLog` writes are atomic.
- `OrganizationSpeciesCode` provides an Organization + shared Species + AAA assignment, unique per Organization/Species; AAA is local to the Organization.
- `BiologicalProvenance` is a shared curated biological-source concept, distinct from provider, acquisition event, and transfer history. Unknown provenance means no relation. Historical BBB strings do not identify provenance automatically.
- `OrganizationProvenanceCode` provides an Organization + BiologicalProvenance + BBB assignment, unique per Organization/provenance; BBB is local to the Organization.
- These local-code models are schema foundations only, not connected to Strain runtime behavior. No Strain FK to AAA, BBB, or provenance is active. No legacy data was backfilled, no `Origin` row was converted to `BiologicalProvenance`, and no AAA/BBB assignment was inferred from historical fields.
- Unchanged legacy fields: `Species.genus_species_code`, `Strain.code`, `Strain.number`, `Strain.origin`, `Strain.origin_code`, `Box.origin`, `Box.global_code`, and `Box.box_number`. Existing global `(species, code)` uniqueness remains active; institutions cannot yet independently use a duplicate representation.
- Product decisions: AAA and BBB are institution-local and may be modified in future; already-issued identifiers must remain preservable as stable snapshots. Normal new Strains will automatically receive X; controlled imports/migrations may preserve explicit historical X. X scope is Organization + Species + provenance, including a distinct scope for unresolved provenance. Acquisition/provider/transfer data remains separate from curated biological provenance.

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

- Canonical worktree: `main` at `cc4c980` (`origin/main`), clean before this document update.
- `git worktree list --porcelain` registers 16 secondary POLYPBASE worktrees, including `local-code-schema-foundation` and the global-strain-identity and strain-organization foundation worktrees. Their individual status and cleanup readiness were not checked; inspect before any cleanup. No worktree was removed or changed.

## History retention and private material

- Prior operational records say GitHub history cleanup (including request `#4748399` and PR #3/#4 references) and encrypted private retention were completed/reviewed. These attestations were not revalidated in this task.
- The old-history archive and split-residual paths were previously absent from this machine; their actual storage and retention are unknown. Confirm the real location and perform a fresh read-only preflight before any retirement/deletion. Destructive action requires explicit approval.
- BoxInsights retirement is an older operational attestation, not revalidated here. Do not treat it as a current blocker or initiate cleanup from this document.

## Production and PostgreSQL QA

- **Production: NOT REVALIDATED.** No production or Neon access was performed. Integration/push does not establish deployment. Current deployed commit and production state are unknown; verify separately through the authorized deployment procedure before any operation.
- Never use Neon or production for QA.
- Disposable local PostgreSQL QA is the approved approach for transaction/locking/concurrency validation. Previously recorded S-01 and weekly-measurement results are historical validations, not rerun here. SQLite alone does not establish PostgreSQL concurrency behavior.

## Next operational work

1. **Next architectural stage:** design how institution-owned Strain safely connects to `OrganizationSpeciesCode`, nullable `BiologicalProvenance`, nullable `OrganizationProvenanceCode`, future X, and a stable issued local-code snapshot. Determine integrity constraints that prevent cross-organization AAA/BBB assignments, Species/AAA mismatches, and provenance/BBB mismatches. Do not begin X allocation until this relationship/integrity model is settled.
2. Later: implement a safe X allocator and scoped uniqueness; local Strain writer; legacy diagnostics/reconciliation; consumer migration; eventual replacement of global `(species, code)` uniqueness; versioned import protocol; and Global-ID-aware transfer protocol. DNA/provenance enrichment and WoRMS integration remain future work.
3. Continue the documented read-only analysis of remaining `Pennaria disticha` attribution cases in `POLYPBASE-ANALYSES`; do not infer biology or authorization rules.
4. Keep probe connectivity, exports and longer-term roadmap items as separate future decisions/work. Inspect secondary worktrees before any cleanup.
5. Verify production separately only when explicitly requested and through the deployment workflow.
