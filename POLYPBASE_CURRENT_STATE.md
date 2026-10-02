# POLYPBASE current state

## Snapshot

- Refreshed **2026-10-02**, from supplied canonical local Git state and milestone validation evidence.
- Canonical repository: `C:\Users\antoc\POLYPBASE`.
- Current `main` and `origin/main`: **`6e92674`** (`refactor: harden transfer v1 backend boundary`), verified clean and aligned immediately after push. `HEAD` is this commit. No production state is implied.

## Integrated product state

### Actions

- Profile has personal Actions, scoped server-side to the authenticated actor and active institution; active membership roles can read their own history. Administration has institution-scoped, admin-only history with family/date filters. Business mutations are included; login, view and scan events are not included in these streams.
- Measurement correction events are append-only. Correcting a measurement updates the current authorized record and adds audit evidence; it does not rewrite earlier correction events. Linked measurement history is explicitly identified and institution-scoped.
- The latest integrated presentation sequence is `cc2d466` → `3d9f96e` → **`673db92`**: removed disclosure-based/raw-metadata presentation, unified readable account/target summaries, preserved historical salinity snapshots, and refined inline transitions, previous location, units and notes.
- Manual temperature/salinity actions identify the zone and use °C/PSU. Before/after coloring applies to populated changes; notes have a localized prefix and three-line visual clamping. Box context/previews and lineage/transfer information remain available.
- Evidence: `backend/apps/accounts/api_views.py`, `backend/apps/audit/services.py`, `backend/apps/accounts/tests_actions_api.py`, `backend/apps/accounts/tests_audit_measurement.py`, `frontend/src/components/AuditTimeline.tsx`, `frontend/src/utils/auditPresentation.ts`, `frontend/scripts/test-audit-presentation.mjs`.
- Older dirty action-history worktrees still exist. Their edits are not automatically pending product requirements and must not be merged wholesale over this later presentation.

### Alerts

- Alerts are no longer an active product feature. The integrated removal leaves no normal runtime producer, active Alert API workflow or frontend Alert UI; polyp decline is no longer turned into a frontend “À surveiller” interpretation.
- Measurements remain factual data, including scientific zero. Historical Alert persistence (model/table, migrations and stored rows) is dormant and retained for preservation only; historical AuditLog/Actions entries involving old Alert events remain displayable.
- Removing the dormant Alert schema or stored data requires a separate, explicit destructive-migration decision. No destructive Alert migration was created.

### Charts

- Shared `BiologicalTrendChart` is used by box tracking, Overview and export previews. Recent integrated milestones: `efcb3bc` (interactions/window controls), `ff2fab9` (Overview cards sized to chart content), `fa33e32` (visual refinements, including overlapping/zero points and stable box-tab layout).
- Scientific zero, including `0/0`, remains plotted; absent optional salinity stays absent. Connecting lines break across gaps greater than 10 calendar days. Both biological series cannot be hidden simultaneously.
- Point detail prioritizes pinned, then focused, then hovered values; keyboard point navigation and Escape are supported. Biological plotting uses a 0–1000 scale with clamping and explicit overflow markers, not deletion of larger observations.
- Box/Overview date scrubbers support drag, resize and keyboard interaction with calendar-day/DST-safe arithmetic. Default windows are six months for box tracking and three months for Overview.
- Overview loads a bounded six-month measurement history for active institution boxes. Its loaded extent is **not lifetime history**; existing “no history” / “full history” wording needs care when older observations exist outside that payload.
- Emplacement also has observed temperature visualization and an eight-ISO-week movement chart. These are distinct from biological charts; observed extrema are not invented alert thresholds.
- Evidence: `frontend/src/components/BiologicalTrendChart.tsx`, `BoxTrackingChart.tsx`, `ChartWindowControls.tsx`, `OverviewView.tsx`, `frontend/src/utils/chartBiology.ts`, `frontend/scripts/test-chart-*.mjs`, `backend/apps/cultures/api_views.py`.
- No chart-specific worktree is currently registered in POLYPBASE.

### Labels / étiquettes

- This feature means **physical Box QR labels**, not taxonomy code assignments or chart labels. Recent integrated sequence: `8ad925b` (printing), `b13f1d1` (text readability), `3922d55` (selection UX), **`f999bd9`** (41 × 28 mm printer stock).
- Selection has search/zone filters, species accordions, tri-state species selection, individual selection, add-results and clear. Changing filters preserves the global selection.
- Selector eligibility is an active box with a latest measurement within 15 months; zero counts do not remove eligibility.
- Print geometry is now **41 × 28 mm**, borderless, with a 25 mm vector QR, rotated code/species text, 7.5 pt code and 7 pt species text, and up to three species lines. Popup printing uses one label per page; SVG download and modal preview share the geometry. Older 40 × 30 assumptions are superseded.
- Labels use the existing Box identifier and species display, not a newly generated AAA/BBB/X identity. QR targets remain `/bac/<id>/`, leading to the box sheet; scan recording is distinct from the Actions stream.
- Evidence: `frontend/src/components/LabelsView.tsx`, `frontend/src/utils/qrLabels.ts`, `frontend/scripts/test-qr-labels.mjs`, `backend/apps/cultures/views.py`.
- Physical printer scaling, clipping and scanability were not verified in this refresh. The clean printer worktree has a patch-equivalent delivery already in main; see below.

### Measurements / Emplacement

- At most one biological measurement per Box per ISO week, enforced by Django/database. A real `0/0` occupies that week. Interactive POST returns `409 measurement_week_conflict` without mutation if occupied; correction uses the authorized PATCH flow. Inactive boxes reject new readings but retain correctable historical records under backend permissions.
- Thermal display separates target/consigne from observed values; Min/Max are factual extrema. Manual temperature entries update the daily aggregate transactionally, not an individual-per-entry temperature history.
- Salinity readings are persisted as separate dated `SalinityMeasurement` rows; new readings do not overwrite previous dates. Duplicate zone/date readings are rejected. Emplacement currently shows the latest reading, not the full persisted history. Corrections are audited and permitted strictly before `created_at + 24h`; equality is locked, with no admin override. Zero PSU is valid.
- Occupancy/capacity is informational, not a movement blocker; zero and null remain distinct. `BoxLocation` periods retain repeated stays and inactive-box history; movements preserve authors/audits even where operational UI omits the actor.
- Zone boxes group Species > Strain and use canonical current-location start/latest biological reading. Inventory retains rich Box tracking previews.
- Same-day movement plus date-only biological readings cannot reliably establish variation for a particular stay. Do not infer attribution.
- `0d7985a` remains the integrated Emplacement milestone; later Actions presentation does not replace these scientific rules. Evidence: `docs/context/measurements-integrity.md`, `docs/context/boxes-lifecycle-locations.md`, `backend/apps/cultures/test_zone_salinity_lifecycle.py` and corresponding API/services.

### Measurement history

- `2292a61` refines the box-sheet “Voir relevés” modal with a compact summary and a responsive, dense history presentation while retaining progressive loading. The API contract and scientific data are unchanged; scientific zero, `0/0`, PSU `0` and absent values remain distinct.
- The accessible dialog interaction was improved. No backend, permission, lifecycle or measurement-rule change was made.
- Independent review: **READY FOR COMMIT**, no genuine findings. Targeted measurement/chart/date tests, typecheck, CSS check and build passed; after integration on `main`, a production frontend build was observed successful in **10.44 s**.

### Frontend consistency

- **`96c5593`** is the integrated frontend-only consistency milestone; no redesign occurred.
- Request-state consistency: Zone movement history no longer presents previous direction/zone/organization results as current after context changes or failures. Inventory hides previous-filter rows and actions when a replacement request fails. Export eligibility is tied to the current request context; stale eligibility cannot validate current counts, selection or download state. Django remains authoritative for Inventory operations and actual export generation.
- MoveBox and Subculture specifically now have coherent initial focus, Tab containment, Escape/focus restoration and safe dismissal. Closure and duplicate submission are blocked while mutation submission is pending; nested confirmations retain their own keyboard ownership. This is not a claim about every application dialog.
- Affected Overview selected controls meet normal-text WCAG AA contrast using existing visual tokens and a non-color selection cue. Movement Entry/Exit bars and legend now differ by fill/outline as well as existing color semantics; exact counts, zero values and accessible text remain preserved.
- QR preparation returns explicit `prepared` / `cancelled` / `failed` outcomes, retaining empty-input behavior. User closure of the preparation popup is cancellation, not technical failure. Failed attempts clean sibling pending resources before returning; retries are isolated from earlier attempts. Labels, the Box QR modal and the Administration transfer-label caller handle technical failures explicitly; transfer QR feedback stays separate from transfer backend errors. `prepared` means browser workflow handoff, never physical printer success.
- QR geometry remains 41 × 28 mm with the existing 25 mm vector QR where defined; Box identifier/species content, `/bac/<id>/` target and Labels selection behavior are unchanged. Transfer business/backend semantics are unchanged.
- No temperature behavior changed: scientific zero and observed/target distinction remain factual. Alerts remain inactive; this milestone does not change dormant persistence, measurement-history design, Actions, Administration desktop-only, or Strain/taxonomy/provenance decisions.

### Taxonomy / Strain / AAA

- `GlobalStrainIdentity` is a shared opaque UUID foundation. Nullable `Strain.global_identity` is not an authorization boundary, is not inferred from matching local codes and is not automatically created/attached by normal Strain POST. No legacy backfill occurred.
- Normal operational Strains are institution-owned. Reference reads/counts, Box creation and subculture enforce institution eligibility. Legacy unowned Strains remain eligible only where a preexisting Box links them to that institution, including inactive boxes; foreign-only/orphan legacy Strains are excluded. Eligible unowned legacy Strains are not editable via the owned-Strain PATCH endpoint.
- `OrganizationSpeciesCode` is **runtime-active**, not merely schema: institution-scoped `/api/taxonomy/species-codes/` GET/POST and detail GET/PATCH are implemented (`ce4e66b`). Reads require laboratory-write capability; writes require active-institution Admin authorization. Uniqueness covers institution/species and institution/code; PATCH changes only the code. Writes and audit are atomic.
- AAA is a product term: backend accepts a nonempty code of at most three characters, not an exact three-uppercase-letter grammar. The existing UI uppercases/trims its input; do not assume the API does so.
- `LocalStrainIdentity` (`7dd9847`) and the locked, transactional creation service (`fc679b6`) connect an owned Strain to its institution AAA. Service checks include institution/species consistency, duplicate identity and optional BBB/provenance consistency.
- **Normal taxonomy Strain POST requires institution AAA** (`dcb6c81`) and atomically creates Strain, translations, local identity and audit. It has no fallback to shared Species code or another institution's assignment. The current normal flow creates an AAA-only identity, with unresolved provenance.
- Missing-AAA frontend loading/error/retry/blocking guidance is integrated (`519e601`) in quick creation and the retained taxonomy component. Django is authoritative. The recovery link currently points at **disabled References**, so a missing assignment has no reachable assignment-management flow in the current React UI.
- AAA management UI was implemented (`f8e8077`) inside `TaxonomyAdminSection`, but its Administration section is now disabled. **API delivery does not mean an active management screen.**
- **Not implemented:** X allocator/sequence, scoped X uniqueness, generated AAA–BBB–X identifier or dedicated immutable issued-code snapshot. Normal API still accepts existing manual code/number/origin-code fields. Global `(species, code)` Strain uniqueness remains.
- Existing Species/Strain/Box legacy identifier fields are retained. AAA/BBB changes do not recompute issued identifiers; there is no automatic ownership, Global ID or local-identity reconciliation.
- Enforcement is not yet universal: historical/transfer import writers can create owned Strains without the normal POST's AAA/local-identity flow. The integrated Species PATCH integrity milestone below protects existing local identities.
- **Integrated `1ea84e5` Species/local-identity integrity milestone:** a Strain with `LocalStrainIdentity` cannot change Species through generic PATCH; a no-op same-Species PATCH remains allowed and other fields remain editable. An identity-less owned Strain retains legacy behavior. AAA assigned to another Species cannot bypass consistency validation. Strain-first locking is coherent with local identity creation, and mutation plus audit are atomic. No dedicated Species correction workflow is implemented.
- PostgreSQL QA: `apps.taxonomy.test_strain_species_concurrency` ran **2 tests: 2 passed, 0 skipped**. Real PostgreSQL lock contention was observed; QA required no code correction.
- Evidence: `backend/apps/taxonomy/models.py`, `scoping.py`, `services.py`, `api_views.py`, `serializers.py`, migrations `0003`–`0007`, `test_species_codes_api.py`, `test_local_strain_service.py`, `test_strain_species_concurrency.py`, and `frontend/src/utils/strainSpeciesCode.ts`.

### Transfers inter-institutions

- **Phase 1 integrated at `6e92674`** (`refactor: harden transfer v1 backend boundary`): `backend/apps/cultures/transfer_v1.py` is the focused business boundary. Preparation and its mandatory source audit are atomic; destination import is atomic. The legacy replay identity is unchanged. Sequential replay retains HTTP 400; replay is checked again after the Species lock, and a late replay-constraint loss is translated only after rollback. Unrelated `IntegrityError`s are not converted to duplicate responses. Request-layer scoping continues to enforce active-organization isolation and Admin checks. Foreign-owned and `organization=NULL` Strain collisions remain conflicts. No model or migration changed.
- Validation evidence: targeted main tests after cherry-pick **18/18 passed** under isolated SQLite settings; Django system check passed; independent review **GREEN**, no findings. Isolated PostgreSQL QA ran `test_transfer_v1_concurrency.py`: **2 executed, 2 passed, 0 skipped**, with both real concurrent scenarios verified and no deadlock, timeout or transaction anomaly. That PostgreSQL QA was run on the reviewed implementation before integration; the cherry-pick applied cleanly without code conflict. The PostgreSQL tests were not rerun on main after cherry-pick.
- **Current v1 limits:** resolution by Species + `strain_code`; transfer-created Strains do not require AAA/`LocalStrainIdentity`; no `GlobalStrainIdentity`, portable Box identity or portable lineage graph. The frontend CSV path remains legacy and the parser consumes one data row. Global `(species, code)` Strain uniqueness and globally unique `Box.global_code` remain. The Transfers section is disabled in Administration. V1 is not the Transfer v2 architecture.

#### FUTURE / NOT IMPLEMENTED — Transfer v2 direction

The approved target is a versioned contract with portable transfer/item/institution identities, explicit `GlobalStrainIdentity`, and known ancestry metadata. Same-instance institutions should eventually use direct Organization-to-Organization transfer; versioned files remain an adapter for separate installations, offline exchange and external/legacy systems. Repiquage preserves `GlobalStrainIdentity`; transfer preserves it too. A coherent destination-local Strain for an incoming identity should be reused; a genuinely new one eventually receives a locally allocated X via a server-side concurrency-safe allocator. No `MAX(X)+1` implementation is specified.

Multiple disconnected Box-lineage families may exist for the same biological/local Strain. Matching identity, Species or codes never imply Box parentage. Known ancestry should travel with a Box and continue locally, with no live cross-institution synchronization; later onward/return transfers may carry enriched known ancestry, without granting access to foreign operational/scientific records. Future acceptance remains all-or-nothing: preflight may report issues without mutation; acceptance commits entirely or rolls back entirely. Mutation/hybridization rules are out of scope.

Dependency-aware roadmap: **1 DONE** v1 backend boundary hardening; **2** versioned v2 contract and portable transfer/item/institution identities; **3** diagnose then establish canonical local `GlobalStrainIdentity` representation and safely address code namespaces/legacy assumptions; **4** concurrency-safe local X allocator; **5** portable lineage nodes/edges distinguishing transfer from repiquage and excluding live sync; **6** backend preflight plus atomic/idempotent multi-item acceptance; **7** direct same-instance Organization-to-Organization workflow; **8** versioned file adapter for cross-instance/offline/external exchange; **9** user-facing inbox/contextual resolution after backend contracts stabilize. Later phases depend on stable identity and contract decisions; none beyond Phase 1 is implemented.

Unresolved product decisions: whether source Boxes must be active-only at the backend; the exact source Box/quantity/status effect of transfer; and the trust/authenticity model for cross-instance packages.

### BiologicalProvenance / BBB

- **Implemented backend foundation:** shared `BiologicalProvenance`; institution-local `OrganizationProvenanceCode` with uniqueness per institution/provenance and institution/code; nullable local-identity BBB relationship (`54e115d`); provenance-aware service validation.
- **Implemented API** (`6ee81d4`): `/api/taxonomy/biological-provenances/` and `/api/taxonomy/provenance-codes/`, each GET/POST only. Reads require laboratory-write capability; creation requires active-institution Admin authorization, strict payloads and atomic audit. BBB list is institution-filtered. No detail/PATCH/DELETE routes exist. Duplicate shared provenance names are allowed.
- A local identity reaches provenance through its BBB assignment; there is no separate Strain provenance field. Service support for known provenance is not exposed by the normal Strain API/UI.
- **Active product workflow:** normal new-Strain creation remains AAA-only. Main has no provenance/BBB frontend consumer. The branch name `provenance-bbb-admin-ui` must not be mistaken for an integrated UI.
- **Product direction for continuation:** provenance + BBB remain deferred from routine/new UI. Keep the backend foundation; removal requires a separate explicit product decision. The dirty experimental frontend is not authorization to activate it.
- Biological provenance is distinct from provider, acquisition event, transfer history and legacy `Origin`. Unknown means no relation. Historical BBB-like tokens do not establish provenance. No conversion/backfill/inference was performed.
- Evidence: `backend/apps/taxonomy/test_provenance_codes_api.py`, `test_local_code_schema.py`, `test_local_strain_service.py`, models/services/API and `backend/config/api_urls.py`.

### Administration / references

- Accounts/institution/laboratory Administration and its action history remain active under backend permissions. Administration is **desktop-only**: tablet entry is absent and `/administration...` redirects directly to `/` without intermediary Administration UI/message.
- `6bad008` temporarily enabled References; **`284c033` supersedes that decision and disables it again**. `DISABLED_ADMIN_SECTIONS` contains `references`; selection is refused and a direct reference-section request falls back to Accounts. Backend taxonomy APIs are not disabled by this UX guard.
- The large **“Référentiel partagé” Administration concept is abandoned for current product direction**, not a feature awaiting routine merge. This direction is supplied by Anthony for this refresh; the code independently confirms the section is disabled.
- The redesign worktree still has substantial uncommitted/untracked work. Its current routing keeps References disabled; no temporary browser-QA exposure remains in the inspected App/Admin routing. It is paused, not committed or integrated.
- Contextual reference maintenance within real workflows is a **direction under consideration**, not an approved replacement design. Do not enable either catalog experiment merely to bypass missing-AAA guidance.

## POLYPBASE-ANALYSES

- Companion repository exists at `C:\Users\antoc\POLYPBASE-ANALYSES`. Verified integration state supplied for this synchronization: **`main = origin/main = 346512d`** (`feat: add species AAA historical review tooling`), pushed, with clean canonical status `## main...origin/main`. Previous main was `24da481` (`docs: record Pennaria historical audit`). No repository re-audit or fetch was performed for this patch.
- Species/AAA review worktree: `C:\Users\antoc\worktrees\POLYPBASE-ANALYSES\species-aaa-review-manifest`, branch `analysis/species-aaa-review-manifest`, now at **`346512d`**, the same commit as companion main (**0 ahead / 0 behind**). The technical milestone is **committed, fast-forward integrated and pushed**; no merge commit was created. All four review/tooling files listed below are committed in companion main. Private generated CSV artifacts remain ignored, outside Git, and were not committed.
- **Post-integration validation:** `uv run pytest -q` ran on canonical POLYPBASE-ANALYSES main after fast-forward integration and returned **89 passed**.
- Historical **Suivi 2019–2026 Species/AAA extraction is completed locally**. Deterministic review tooling preserves literal evidence/conflicts and verifies baseline/source fingerprints. Following independent review, local fixes narrowed bare `Genus sp.` lexical-variant handling to the documented qualified-sp case, made CSV export use exclusive creation after preflight to prevent overwrite/truncation, and expanded regression coverage. Changes affect `src/polypbase_analysis/species_aaa_review.py`, `tests/test_species_aaa_review.py` and `docs/SPECIES_AAA_REVIEW.md`; the issue register was not modified.
- **Earlier focused validation, before integration:** targeted tests **15 passed**; full `uv run pytest -q` **89 passed**; historical `--check-only` extraction and CLA assertions passed. The 15-targeted run is not claimed to have been repeated after integration. Baseline reproduced unchanged: 1,376 observations, 1,337 parseable, 39 unparseable, 126 valid AAA, 555 parsed exact identifiers, 22 malformed strings, 106 CLEAN, 9 LABEL VARIANT, 11 POSSIBLE COLLISION, 6 reverse conflicts, 30 exact-box conflicts, and X tokens 1, 2, 3, 4, 5. SHA-256 hashes of all four existing private review CSVs remained unchanged before/after validation; no outputs were regenerated. No raw XLSX, private CSV contents or product decisions were modified; no row-level evidence is copied here.
- **Independent technical re-review completed successfully: technical review GREEN, with no remaining genuine technical findings.** Documentation is consistent; the bare `sp.` fix, atomic/exclusive export protection, regression coverage and partial-failure policy were accepted. The technical tooling milestone is now committed/integrated/pushed. Stakeholder decisions still block final manifest approval; technical approval and integration do not make the manifest import-ready.
- A WoRMS review document records research checked **2026-09-30**, separately from human approval. Anthony's mapping/exclusion/normalization decisions are documented; proposed canonical-name changes and local AAA acceptance are separate decisions. Source history remains immutable.
- **Not import-ready:** existing CSV decision/approval/review fields are still blank; documented decisions have not become an approved assignment manifest. No POLYPBASE Species/AAA importer is implemented. Extraction does not decide ownership, provenance, BBB meaning or identifier rewrites. No database/production writes were part of this inspection or the read-only review workflow inspected.
- **Waiting on Étienne and Anaïs:** Anthony sent the Species/AAA validation email on **2026-10-01**. Feedback is requested on more than D11/D12: CLA allocation between Chrysaora lactea and Cyanea lamarckii; Lobonemoides robustus / gracilis; Obelia OSP / OBE; Turritopsis references; ATH Aurelia labels; AVA Valentine / Aurelia malayensis; CMU and COR collisions; THY double label; Chrysaora helvola / fuscescens and CHE / CFU; Tubularia bellis / Ectopleura larynx and TBE / ELR; Aurelia coerulea DD; color/form qualifier policy; and special handling of “hybrid”. These are unresolved review topics, not approved mappings. A nomenclatural synonym relationship alone does not settle historical biological identity.
- The manifest remains **not import-ready until stakeholder decisions are explicitly resolved** and approvals are recorded. Older Pennaria grouping, Excel color interpretation and import-notebook species/box-context harmonization remain open/deferred; do not treat them as settled biology.
- The four files committed in companion main at `346512d`: `docs/SPECIES_AAA_REVIEW.md`, `docs/SPECIES_AAA_ISSUES_AND_PROPOSED_CORRECTIONS.md`, `src/polypbase_analysis/species_aaa_review.py`, `tests/test_species_aaa_review.py`. Committed analysis milestones/older validation: `docs/ANALYSIS_REFACTOR_LOG.md`.

## Active / paused worktrees

Verified read-only during this **2026-10-02** refresh: **19 registered POLYPBASE worktrees total at that point, including canonical main and 18 secondary worktrees**. Canonical main is at `6e92674`, with five tracked documentation changes from this uncommitted refresh. The table is the complete registered secondary-worktree inventory at the time of this refresh, not a permanent invariant; branches/HEADs come from `git worktree list --porcelain`, cleanliness from Git status, and counts from `main...HEAD` (**ahead / behind** main `6e92674`, excluding uncommitted edits). Secondary paths use the prefix `C:\Users\antoc\worktrees\POLYPBASE\` and suffix `\POLYPBASE` around each directory name. No secondary worktree was modified or removed, and no dirty secondary worktree file contents were inspected.

| Directory / branch | HEAD | Cleanliness | Ahead / behind |
|---|---|---|---|
| `action-history-cleanup-targeted` / `fix/action-history-cleanup-targeted` | `8ad925b` | 11 modified tracked files | 0 / 35 |
| `action-history-colored-deltas` / `fix/action-history-colored-deltas` | `c8fd4b0` | Clean | 1 / 10 |
| `action-history-no-disclosures-fr` / `fix/action-history-no-disclosures-fr` | `cc2d466` | Clean | 0 / 11 |
| `action-history-unified-presentation` / `fix/action-history-unified-presentation` | `3d9f96e` | Clean | 0 / 10 |
| `action-journal-cleanup` / `fix/action-journal-cleanup` | `b8e5647` | 17 modified tracked files | 0 / 39 |
| `dialog-focus-accessibility` / `fix/dialog-focus-accessibility` | `6e92674` | Clean | 0 / 0 |
| `frontend-consistency-fixes` / `fix/frontend-consistency-fixes` | `96c5593` | Clean | 0 / 2 |
| `label-printer-41x28-tuning` / `fix/label-printer-41x28-tuning` | `cfeaca1` | Clean | 1 / 11 |
| `measurement-history-modal-ui` / `feat/measurement-history-modal-ui` | `ed2ae93` | Clean | 1 / 6 |
| `organization-audit-atomicity` / `fix/organization-audit-atomicity` | `1d8b5f2` | Clean | 1 / 36 |
| `provenance-bbb-admin-ui` / `feat/provenance-bbb-admin-ui` | `6ee81d4` | 6 tracked modifications + 2 untracked entries | 0 / 14 |
| `qr-active-org-isolation` / `fix/qr-active-org-isolation` | `6693268` | Clean | 1 / 7 |
| `reference-admin-redesign` / `feat/reference-admin-redesign` | `284c033` | 9 tracked modifications + 3 untracked entries | 0 / 12 |
| `remove-alerts` / `refactor/remove-alerts` | `5f773ff` | Clean | 0 / 5 |
| `strain-legacy-diagnostic` / `feat/strain-legacy-diagnostic` | `9126ad1` | Clean | 1 / 35 |
| `strain-provenance-foundation` / `feat/strain-provenance-foundation` | `14c0f3b` | Clean | 2 / 19 |
| `strain-species-identity-integrity` / `fix/strain-species-identity-integrity` | `1ea84e5` | Clean | 0 / 6 |
| `transfer-v1-boundary` / `refactor/transfer-v1-boundary` | `e692caa` | Clean | 1 / 3 |

- Dirty historical action experiments and paused reference/provenance UI still require selective review against current main, not bulk integration. Commit counts do not establish patch equivalence or pending product work; integration milestones above remain the product-state reference.
- Companion Species/AAA branch `analysis/species-aaa-review-manifest` now points to **`346512d`**, the same commit as companion main; its four committed review/tooling files are integrated, not pending integration. Preserve the worktree; no cleanup is requested.
- Other companion analysis worktrees, as observed at the earlier pre-integration `24da481` snapshot (not re-audited here): `diag/strain-diagnostic-normalization` was clean at that companion main; older `docs/analysis-refactor-log` and four `refactor/*` worktrees are clean ancestors (6–10 commits behind), not evidence of pending integration. The clean detached `lilac-dune` worktree is two commits behind. `local-import-20260922` is not currently registered; absence from registration says nothing about private retention or other filesystem copies.
- Preserve dirty worktrees. Patch-equivalent/ancestor status establishes integration of committed work, **not permission to delete local worktrees**.

## Product decisions / constraints

- Scientific **0 is real**, never missing data. Preserve measurements, locations, movements, lineages, authors and append-only audit evidence.
- Strict active-institution isolation and backend permissions are mandatory for IDs, relations, choices, aggregates, exports and bulk actions. A frontend filter/desktop guard is not permission.
- Existing Box/Strain identifiers are not silently rewritten. No automatic historical ownership, Global ID, AAA/BBB or biological inference from source tokens.
- Biological provenance is not acquisition/provider/history. Provenance/BBB UI is deferred; keep its backend foundation unless explicitly decided otherwise.
- Species + AAA should eventually be initialized from a **reviewed and approved manifest**, not directly from exploratory CSVs. Contextual maintenance is a likely direction, still awaiting product design; the large shared-reference catalog is not the next delivery.
- Future automatic X allocation, institution/species/provenance scope (including unresolved provenance), historical X preservation and stable issued-code snapshots remain design direction, **not active implementation**. Do not improvise these rules or replace global uniqueness without reviewing every writer/consumer.
- French/English user text uses existing i18n; no invented biological or operational rules. Administration remains desktop-only.
- No production/Neon test/import/demo access. Anthony performs commit/push/merge/deploy unless explicitly delegated. This refresh performs none of them.

## Pending / blocked work

1. **Étienne / Anaïs:** Anthony sent the validation email on **2026-10-01**. Several Species/AAA mappings, collisions, naming questions and color/form/“hybrid” qualifier decisions listed above await their feedback; the blocked state is broader than D11/D12. The manifest remains **not import-ready** until those decisions are explicitly resolved and approved.
2. **Technical tooling is delivered:** committed at `346512d`, fast-forward integrated into companion main, pushed, independently reviewed GREEN with no remaining technical findings, and **89 tests passed on canonical main after integration**. Earlier focused validation also recorded 15 targeted tests, historical check-only baseline and private CSV hashes unchanged. **Product/manifest work remains blocked:** stakeholder responses and explicit decisions are still required; no final approved manifest or POLYPBASE importer exists. Do not begin importer work from unresolved mappings. Committed review tooling/documents are not an approved import contract; private CSV decision fields remain unresolved.
3. Missing-AAA recovery is incomplete because References is disabled. Design a small contextual assignment/maintenance workflow; do not resurrect the rejected catalog or introduce provenance/BBB as a prerequisite.
4. Review all import writers before claiming universal AAA consistency or implementing X/snapshot semantics; the Species/local-identity PATCH integrity gap is resolved, but transfer imports still do not require AAA/local identity.
5. Decide separately whether to integrate the organization-audit atomicity and read-only legacy-diagnostic commits. Dirty action experiments and paused reference/provenance UI require selective triage, not bulk merge.
6. Static inspection flagged two other follow-ups, not fixed here: Overview absence/full-history wording for bounded data; QR box routes authorize membership-wide institutions rather than requiring the selected active institution. Reproduce and review before changing behavior.
7. Transfer v2 Phase 2 (versioned contract and portable transfer/item/institution identities) is the next Transfer/Strain technical phase; Global-ID resolution, X allocation and portable lineage remain later dependency-ordered work. Probe connectivity/combined monitoring, DNA enrichment and application WoRMS integration remain separate future work; models/Aphia IDs do not prove connected features.

## Next operational steps

1. **Species/AAA stakeholder track:** continue waiting for Étienne/Anaïs responses to the 2026-10-01 email, then record explicit mapping/naming/qualifier decisions and review an approved manifest. No import-ready manifest exists. No importer or database import before explicit approvals; a dry-run importer contract is a later step on this track.
2. **Transfer/Strain technical track, in parallel:** Species/LocalStrainIdentity generic PATCH protection and PostgreSQL concurrency QA are delivered; Transfer v1 boundary hardening is integrated at `6e92674`, with independent review and pre-integration PostgreSQL concurrency QA GREEN. Next is **Transfer v2 Phase 2: versioned protocol/contract and portable transfer/item/institution identities**. Do not jump to Global-ID resolution, X allocation or portable lineage implementation; follow the later dependencies documented above. This contract phase is not blocked on the historical Species/AAA manifest approvals.
3. **Missing-AAA contextual UX:** remains a separate future product workflow requiring a scoped decision. Do not restore the rejected References catalog; keep provenance/BBB deferred.
4. Preserve secondary worktrees and private review artifacts. Any separately authorized implementation receives targeted isolated QA, then broader checks as needed. Review final diffs; Anthony handles integration/deployment unless delegated.

## Production / QA status

- **Production is NOT REVALIDATED.** No production or Neon access occurred. Local main/push state does not prove deployment or applied migrations. No locally inspected record proves the current deployed commit; verify only under a separately authorized deployment task using the existing workflow.
- No deployment occurred; local integration does not prove production state. This documentation-only refresh runs no application tests or builds.
- Frontend consistency independent initial review: non-QR portions accepted; QR preparation had **2 Medium + 1 Low** findings. After correction, focused independent QR re-review marked **all three RESOLVED**, found no new findings and concluded **READY FOR COMMIT**.
- Supplied post-integration validation on canonical `main` **`96c5593`**: `npm run test:inventory` **24 passed**; `npm run test:confirm` **25 passed**; `npm run test:labels` **69 passed**; `npm run test:zones` **50 passed**; `npm run typecheck`, `npm run check:css` and `npm run build` passed (**2209 modules transformed**). These commands were not rerun during this documentation refresh.
- No real-browser, screen-reader or physical-printer QA was performed for this milestone. Backend tests were not rerun because the change is frontend-only. No production, Neon or shared-database access occurred.
- Alerts-removal milestone validation before its review: targeted backend **140 passed, 6 skipped**; isolated backend **514 passed, 18 skipped**; Django system check and migration dry-run/check passed; targeted frontend scripts **232 passed**; typecheck, CSS check, production frontend build and `git diff --check` passed. Independent review was **READY FOR COMMIT** with no material findings; focused frontend **113 passed**, backend **108 passed** and additional backend **51 passed**, with frontend typecheck passed. No browser QA or PostgreSQL concurrency QA was performed in review; this does not supersede the successful writer migration check.
- Measurement-history modal, independent review before integration: **READY FOR COMMIT**, no genuine findings; `npm run test:measurements` (**28/28**), `npm run test:charts` (**28/28**), `npm run test:dates` (**4/4**), typecheck, CSS check and build passed. After integration on `main`, a production frontend build was observed successful in **10.44 s**. No other suites are claimed as rerun on `main` after integration.
- No production validation or browser QA is claimed for the modal; no production or Neon access occurred.
- The prior snapshot records **historical Emplacement delivery** validation for `0d7985a`: 408 backend tests (15 skipped, no failures), targeted frontend suites, TypeScript/CSS/build/migration checks and accepted manual QA. This was not rerun and does not validate later main commits.
- Companion `ANALYSIS_REFACTOR_LOG.md` records 74 passing tests and clean diff check at an earlier committed analysis milestone. The supplied Species/AAA integration record confirms **89 tests passed with `uv run pytest -q` on canonical companion main after fast-forward integration at `346512d`**, now pushed. Earlier focused validation recorded **15 targeted tests passed**, historical `--check-only` extraction and CLA assertions passed, the historical baseline unchanged, and unchanged SHA-256 hashes for all four private review CSVs; no outputs were regenerated. The targeted run is not claimed to have been repeated after integration. These validations were not rerun during this documentation patch; **independent technical re-review is GREEN with no remaining technical findings**. Tooling is committed/integrated/pushed, but stakeholder decisions remain pending and the manifest is not import-ready. No database import, production or Neon access occurred.
- Locking/concurrency conclusions require disposable isolated PostgreSQL QA. SQLite alone does not establish PostgreSQL behavior. Never substitute Neon or production.
