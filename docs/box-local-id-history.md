# Box Local ID history — local implementation

## Scope and identity contract

This registry is an additive foundation, not itself a recoding service. Its
migration does not rewrite existing Box codes, PKs, Global IDs, scientific
snapshots, measurements, lineage or audits. A separate inactive internal
[Species code-change service](species-code-change-apply.md) now uses it
transactionally. There is no public mutation endpoint or frontend activation.
The Species preview remains advisory and read-only, with
`mutation_available=false`.

- `Box.global_code` remains the operational Local ID (`AAA-BBB-X.YYY`), despite
  its legacy technical name, and remains globally unique in the database.
- `Box.global_identity` remains the permanent physical UUID. A shared UUID is
  not permission to share or reassign Local IDs across institutions.
- `Box.previous_global_code` remains intact. It is also used by historical
  merge/temperature consumers; it is not repurposed as a generic rename slot.
- `Box.merged_into` is explicit identity evidence. Only same-institution paths
  reaching a terminal Box resolve; cycles and foreign paths fail closed.

## Exact role of `Box.local_code`

`local_code` is a separate, nonunique, optional legacy/source label, not the
operational Local ID. The normalized CSV importer stores `boite.csv.code_local`
there. The 2026 workbook pipeline preserves its original label there while
normalizing the Box number in `global_code`. Subculture can accept a local label;
automatic creation and Transfer v1 leave it empty. Lists, inventory, lineage and
manual lookup can display/search it. It is not a globally reserved identity and
must not be promoted into an alias or recoded by inference.

The Species planner keeps its `requires_review` dependency and now fingerprints
its exact value. No policy for changing this field has been invented.

## Registry and registration

`cultures.0023_box_local_id_alias` adds `BoxLocalIdAlias` after
`cultures.0022_box_global_identity`, which follows main's
`0021_merge_dna_and_history`. Existing main migration identities are unchanged.

- globally unique exact `code` (up to 100 characters);
- permanent `box` FK with `PROTECT`, retaining the actual source, even if merged;
- immutable `created_at`;
- institution derived from the source Box, not a second independently editable FK.

`register_box_local_id_alias(box=..., code=..., organization_id=...)` is an
internal explicit registration primitive. It does not establish historical
truth, authorize an actor, rename a Box, infer continuity or write an audit.
A business service must supply confirmed evidence, actor authorization
and a suitable audit in its transaction. The internal AAA-change service confirms
the persisted current code before preserving it as an alias. There is no public alias-write endpoint.
Registration is idempotent only for the exact source/code pair; it never moves an
existing alias to another source, even within an equivalent merge chain.

SQL triggers on PostgreSQL and SQLite cover raw/bulk claims, alias update/delete,
source-organization changes and merge changes invalidating registered history.
Claims against current, previous and registered identifiers are compatible only
when they explicitly resolve to the same terminal in the same institution.
Existing unchanged legacy conflicts remain inspectable; new ambiguous claims are
rejected. The unique constraint on current `global_code` is retained.

Namespace allocation considers current, previous and registered codes, including
numeric prefixes of retired legacy names, so it does not recycle old suffixes.

### Transactions and lock order

PostgreSQL uses one transaction advisory lock for the globally enforced Local ID
namespace. Admission triggers acquire it at statement level, before tuple locks.
ORM Box save, registration and namespace allocation use the same coordination.
Lifecycle, automatic creation, batch qualification, measurement writes/import
application and PCA integration acquire it before their existing row locks.
This deliberately serializes these write transactions across institutions; it is
a correctness tradeoff, not a claim of throughput neutrality.

An outer transaction that explicitly locks Box/namespace/related authority rows
before calling these primitives must acquire `lock_local_id_registry()` first.
Future mutation work must audit its entire lock order, not rely on preview.
`REPEATABLE READ` identity writes are refused; `SERIALIZABLE` callers must handle
serialization retries. No retries or recoding workflow are introduced here.

## Existing history and migration safety

Backfill reads only persisted `previous_global_code`, never inferred prefixes,
workbook correspondences, shared UUIDs or similar suffixes. It verifies all
current/previous claimants have one same-institution terminal before adding a
row. If several confirmed sources share that terminal, it retains a deterministic
source carrying that previous value (prefer the terminal if eligible).

Ambiguous values, invalid labels and unresolved/cyclic/foreign merge paths are
left unchanged and skipped with a count-only warning. Deployment review must
inspect those conflicts separately; this task did not inspect any real database.
No CLA/SME associations are manufactured. Existing CLA/CLC reservations remain;
SME/SSP observational matches remain separate physical histories. PCA's current
canonical code equaling a retired donor's previous code remains supported.

Rollback refuses if any alias is not reconstructible from current/previous fields
and explicit same-institution merges. On PostgreSQL, preflight takes
`ACCESS EXCLUSIVE NOWAIT` locks on Box then alias tables before inspecting state,
held through the atomic downgrade. A busy database or lossy downgrade fails
closed. Do not delete the registry to force rollback: review/export and plan a
lossless downgrade separately. Maintenance is required for deployment/downgrade;
no production migration was run here.

## Resolution, search and consumers

`identity_resolution.py` batches exact current/previous/registered claims globally,
then authorizes every source and merge hop through the caller's institution
queryset. Unknown, conflicting, cyclic and foreign identifiers yield the same
404. An inaccessible conflicting claim is not ignored to manufacture uniqueness.

- `GET /api/boxes/by-code/<code>/` returns canonical Box detail using the explicit
  active institution. The path converter supports confirmed legacy encoded `/`.
- `/boxes/<code>` uses that exact endpoint, not paginated fuzzy search, and
  replaces a resolved historical URL with the current canonical Local ID.
- List and inventory search find registered and merged-source identifiers and
  return each operational Box once. Existing legacy `local_code` search remains.
- Numeric detail/scan and `/bac/<id>/` preserve PK identity and resolve explicit
  same-institution merges. Scan audit behavior is retained.
- UUID API and frontend behavior remain unchanged; UUID is never an authorization.
- Audit Box navigation resolves stored `object_id` through the shared resolver.
  Original audit codes, metadata and scientific records are not rewritten.

### Imports and scientific consumers are deliberately separate

The generic measurement import planner still matches exact current `global_code`
in the active institution and excludes merged records. Normalized CSV import
retains its current-code identity and source `local_code` interpretation. The
sealed 2026 importer retains its reviewed explicit corrections/reservations;
Transfer v1 retains its source receipt and strain matching. Database reservation
now prevents these writers from creating an ambiguous alias collision, but no
historical CSV is silently rematched to an operational Box.

Historical temperature/measurement evidence and audit measurement lookup are not
reinterpreted via operational aliases. An operational navigation redirect is not
a decision about which historical scientific row a CSV observation describes.
Supporting historical aliases for scientific imports requires a separate reviewed
contract, including source evidence, merge/measurement provenance and collisions.
Exports and immutable lineage/transfer snapshots retain their existing semantics.

## Species code-change planner

Regression fixtures cover `current_code_unusable`, `local_identity_mismatch`, and
`assignment_used_by_unplanned_strain`, with state snapshots and SQL no-write
assertions. Registry rows are included in no-write snapshots.

The planner detects exact and namespace alias reservations globally, including
strains with no Boxes. Foreign collisions disclose scope, not owner/record
identities. It no longer blocks merely because `previous_global_code` is filled.
It still blocks reviewed protected identities and unresolved historical claims
(`local_id_history_unresolved`), including incoming merge sources.

Fingerprint inputs now include relevant aliases, source/terminal merge state,
current/previous claims, local assignment references, `InstitutionSpecies.revision`
and exact `Box.local_code`. Source namespace existence, stored high-water and
effective allocation floors are also fingerprinted for transactional application.
Alias timestamps are deliberately excluded: the
immutable code/source pair determines identity, not insertion time. Audit/tag
counts and unrelated scientific activity remain excluded; preview is not a
scientific-write authorization. Status is reported but excluded from this identity
fingerprint; a future mutator must separately revalidate any lifecycle policy.
The fingerprint is advisory and is not a substitute for transactional collision
checks and locks immediately before any future write.

## Validation and independent review

Use only `config.test_settings`, `POSTGRES_DB=''`, and
`POLYPBASE_TEST_POSTGRES=0` for isolated SQLite. Set `DJANGO_DEBUG=1` for standalone
check/migration-drift commands if no development environment is configured.

Focused modules: `apps.cultures.test_box_local_id_aliases`,
`apps.cultures.test_box_alias_resolution`,
`apps.taxonomy.test_species_code_change_preview`,
`apps.cultures.test_box_global_identity`, `apps.audit.tests`, plus lifecycle,
automatic creation, measurement-write/import, PCA and historical correction
regressions. Frontend identity/history/QR harnesses exercise actual App effects;
TypeScript and the production build are also checked.

PostgreSQL-only tests cover competing raw claims, registry/save/allocation lock
order and rollback write exclusion. They remain unexecuted locally: Docker did
not respond and no safely available disposable PostgreSQL was established. SQLite
cannot certify PostgreSQL SQL, snapshots, advisory locks or interleavings. Real
browser QA was not performed. Independent review and disposable PostgreSQL
validation remain required before release; no mutation readiness is asserted.

### Local execution results

- Identity registry/resolution, Species preview, Global ID, audit and inventory:
  116 tests run, 112 passed, 4 PostgreSQL-only skips.
- PCA and historical reservation/guard modules: 75 run, 40 passed, 35 skips.
- Six targeted CLA/SME apply, read-only preview and idempotent-rerun regressions:
  all passed.
- Delegated caller-integration suite (creation/intents, lifecycle, PCA,
  measurement services/evidence/import application): 212 run, 150 passed,
  62 skips. These groups overlap; do not sum them as unique coverage.
- Frontend Local ID/UUID routes, history and transfer QR harnesses: 69 passed.
- Django system checks, migration drift, TypeScript, CSS checks, Vite production
  build and `git diff --check`: passed (Git reports LF/CRLF notices only).
- The combined 175-test historical reconciliation run exceeded its 240-second
  bound with no failure displayed. It is not a passing full-suite result; the
  smaller PCA/reservation group and six critical CLA/SME cases passed afterward.
- Standalone checks initially required `DJANGO_DEBUG=1` because the local shell
  had no development key; they passed with the explicit isolated settings.

No shared database, Neon, production, commit, push, stash or deployment was used.
