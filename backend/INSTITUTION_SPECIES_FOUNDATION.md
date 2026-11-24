# Institutional Species foundation and backend services

## Scope

`InstitutionSpecies` stores institution-owned information over an existing shared
`Species` reference. The model adds no Django admin registration, signal-based
conversion or backfill. Backend registration/editing services and Admin API
routes exist (see below); no frontend uses them and the current Administration
interface is not approved by this work.

Fields are limited to organization, shared Species, local scientific name,
nullable local AphiaID and revision (starting at 1). There are no required common
names, translations or copied taxonomic metadata. An AphiaID association is not
by itself a statement of current WoRMS verification or biological equivalence.

Database constraints enforce one row per institution/reference, normalized-name
uniqueness per institution using the existing `ScientificNameKey`, nonempty
normalized names, positive non-null AphiaIDs and positive revisions. The same
name or AphiaID can occur in different institutions. Organization and Species
foreign keys are required and protected. Shared Species uniqueness is unchanged.

The database uses the repository's existing shared-role tenancy architecture,
not row-level security. Constraints do not authorize reads. The inactive
`institution_species_for_admin(request)` helper requires an explicit authorized
active institution and active Admin, and returns only its rows. A shared Species
ID is never permission to read another institution's information.

`InstitutionSpecies.objects.compare_and_swap(...)` is a low-level conditional
UPDATE scoped by organization, PK and expected revision; it changes only local
name/AphiaID and advances revision atomically. It returns 0 for stale or foreign
rows. It is **not** an authorized business service: a future caller must perform
permissions, final name/AphiaID validation and transactional audit. Ordinary ORM
`save`/`update` is not automatically converted into an audited or revision-safe
business operation; the authorized writer is `institution_species_services`.

## Migration and rollback

`taxonomy.0012_institution_species_foundation` creates only the new table and its
constraints. It depends on 0011 and Organization portable identity 0002. It does
not modify 0010/0011, rewrite shared Species, change Strain foreign keys, create
codes or backfill local information.

An empty-table rollback removes the new table without changing historical
records. A populated rollback deliberately raises `IrreversibleError` before
removing anything. Preserve local data and prepare an explicit reviewed rollback
plan rather than dropping populated information. No automatic reverse copy into
shared Species is allowed.

## Read-only preparation

`prepare_institution_species_manifest(organization=...)` is a trusted internal
maintenance function, not an HTTP endpoint or an apply command. It requires an
existing explicit institution. Do not run it on shared or production databases
as part of QA.

It reports:

- Species referenced by this institution's explicit code assignments;
- institution-owned Strains and existing local information;
- unowned Strains used by this institution's Boxes, including whether another
  institution also uses them, without declaring ownership;
- relevant Species without institution codes;
- conflicting owned prefixes, assignment/prefix disagreements, local identity
  relationship inconsistencies and local Boxes using foreign-owned Strains;
  that foreign usage is a conflict, never an ownership assignment;
- prospective normalized-name collisions between existing local names and
  proposed initial names from the shared reference.

A candidate is evidence to review, not an assignment to apply. Every candidate
requires review. Shared records with no evidence in the selected institution
are deliberately not adopted. Unowned Strains with no Box use in that institution
are outside its scoped inventory, not inferred to belong to it. Matching names,
AphiaIDs, codes or Global IDs do not establish institutional ownership.

The manifest reads no foreign institutional Species information and performs no
INSERT/UPDATE/DELETE or network call. It is advisory across multiple reads, not
an immutable apply authorization. A future approved applicator must revalidate
the evidence and collisions in its own transaction. No such applicator exists.

## Institutional services and API

`institution_species_services.py` holds the authorized business writers;
`institution_species_api.py` exposes them. Every route requires explicit
`X-Organization-Id` and active Admin authority on it.

| Route | Behavior |
|---|---|
| `GET /api/admin/institution-species/` | Own profiles (`id`, shared `species_id`, local name/AphiaID, own code, revision, strain count) plus `historical_species` used here without a profile, labeled `requires_historical_adoption` |
| `POST /api/admin/institution-species/` | `{scientific_name, worms_aphia_id?}` → 201 profile with its definitive generated code |
| `POST /api/admin/institution-species/resolve/` | Retired: `410 institution_species_resolution_retired` after authorization; no profile, AAA assignment or audit is written |
| `GET /api/admin/institution-species/<id>/` | Own profile and its eligible Strains (`is_owned` distinguishes unowned legacy Strains; `recorded_provenance_name` is the active institution's verified recorded biological provenance of a box-usable Strain, otherwise `null`) |
| `PATCH /api/admin/institution-species/<id>/` | `{expected_revision, scientific_name, worms_aphia_id}` (all required; `null` removes the reference) |
| `GET /api/admin/institution-species/<id>/strains/` | Eligible Strains only |
| `POST /api/admin/institution-species/<id>/strains/` | `{request_id, collection_location: <id>}` or `{request_id, new_collection_location: {name, code}}` → 201 `{strain, strains}`; the server allocates X and builds `AAA-BBB-X` (see below). `request_id` is a required client idempotency key |
| `GET /api/admin/institution-species/<id>/code-change-preview/?proposed_code=AAA` | Read-only impact plan of a Species code change (see "Species code change preview"); writes and reserves nothing |
| `GET /api/admin/collection-locations/` | The active institution's coded collection locations `{id, code, name}` (BBB), never another institution's |
| `GET /api/admin/institution-species/historical/<species_id>/` | Read-only view of a reference used here without a profile (shared name, own code, eligible Strains); also returns the profile id when one exists; 404 for unused or foreign references |

Registration: WoRMS verification (if an AphiaID is sent) runs before any
transaction; then allocation gate → Organization lock. The normalized name is
the shared reference key: an existing reference is reused only when this
institution has neither a profile nor history (code, owned Strain, own Box) and
its AphiaID does not contradict the submitted one (`shared_reference_conflict`
otherwise). History returns `historical_adoption_required` with no write. A new
shared reference stores the name only; the AphiaID belongs to the profile.
Profile, code (allocator unchanged) and audits are one transaction.

Edit: stale revisions → 409 `stale_revision`; a kept or new AphiaID is
re-verified against the final name (mismatch → 400, never substitution);
WoRMS unavailable → 503, while `worms_aphia_id: null` needs no network. The
conditional update uses `compare_and_swap` under the Organization/profile lock;
audit stores before/after values and revisions. Shared Species, codes, Strain
and Box identifiers are never modified.

## Legacy mutation paths

| Path | Current state |
|---|---|
| `POST /api/admin/species-catalog/species/`, `.../species/<id>/code/` | Closed: 410 `superseded_by_institution_species` (not used by the Species workspace) |
| `PATCH /api/taxonomy/species-codes/<id>/` | Changing an assigned code → 409 `species_code_immutable`; same value is a no-op |
| `PATCH /api/taxonomy/species/<id>/` | 409 when another institution uses the reference; name/AphiaID frozen once any profile exists; single-institution legacy edits otherwise unchanged. The guard runs after the allocation gate and a `FOR NO KEY UPDATE` Species row lock, to coordinate with registration, code assignment (gate) and the internal legacy Transfer v1 service (Species row lock) |
| Django admin `SpeciesAdmin` + translation inline | Superuser-only writes; existing name/AphiaID read-only |
| `POST /api/taxonomy/strains/` | Closed: 410 `superseded_by_institution_species_strains` (manual code/number no longer accepted). The legacy References UI no longer offers Strain creation (its Strains tab points to Species and strains) and the unmounted `QuickStrainCreator` was removed |
| `PATCH /api/taxonomy/strains/<id>/` | Owned Strains only (foreign or unowned legacy → 404). Any requested `code`, `number` or `origin_code` that differs from the stored value after normalization (`0` ≠ `null`; unreadable value = change) → 409 `strain_identity_immutable` with `fields`, before any write or audit. Same values, notes, translations and the existing species rule are unchanged. The legacy edit form shows identity read-only and never sends it. Superuser Django admin `StrainAdmin` is not restricted |
| `POST /api/taxonomy/species/`, `POST /api/taxonomy/species-codes/` | **Still open — activation blocker.** No mounted UI consumer: the References tab (`TaxonomyAdminSection`) is in `DISABLED_ADMIN_SECTIONS` on HEAD. Manual first code assignment remains possible through the API |
| `GET /api/admin/species-catalog/`, `GET /api/taxonomy/references/` | Still shared-name views — activation blocker for the naming switch; no longer read by the Species workspace |
| WoRMS search `local_species` | Institution-scoped: own profiles (local name/AphiaID) and historical references used here; never another institution's information |
| `import_bdd_csv`, `seed_demo_data`, historical importers | Technical writers of shared references; the public Transfer v1 prepare/import routes now return `410` |

Existing codes remain unchanged. Historical references lacking an institution
code still require an approved, reviewed adoption process; nothing assigns one.

## Consumers for the later naming switch

No consumer is switched in this phase:

- Box/Strain summary serializers still read shared Species names.
- Ordinary Box creation options, search, overview and inventory still use shared
  names. Their creation/numbering/idempotence behavior must remain unchanged.
- Export options currently key Species by the shared ID; multi-institution
  presentation will need the institution context as well.
- Audit previews currently resolve shared names; historical audit entries must
  not be rewritten. Future local corrections need explicit before/after values.
- Historical Transfer v1 uses shared names; its public workflow is retired.
  Do not silently inject local names into historical payloads.
- Transfer v2 snapshots contain shared names and its resolver checks exact name
  agreement after UUID resolution. Preserve payloads, snapshots and acceptance
  semantics; any future protocol extension is a separate reviewed change.

`Strain.species`, AAA/BBB assignments, LocalStrainIdentity, GlobalStrainIdentity,
X counters, Box codes/namespaces, subculture, lineage and transfer relationships
remain untouched.

## Validation gates

Use `config.test_settings`, `POSTGRES_DB=''` and `POLYPBASE_TEST_POSTGRES=0` for
SQLite tests. Focused module: `apps.taxonomy.test_institution_species`.

Actual concurrent uniqueness and revision tests require a newly initialized,
verified disposable PostgreSQL cluster, never an unknown listener or port 5432.
Use `scripts/test_historical_temperature_production.py --run --pg-bin <local PostgreSQL 18 bin>`
with explicit test labels. PostgreSQL fixture cleanup requires the launcher's
live in-memory lease, matching server identity and the expected test database.
A declared directory or environment flag alone grants no cleanup authority.
The runner bypasses ordinary triggers only inside its fixture-cleanup transaction
and checks that the normal replication role is restored. Historical-temperature
and identity guards remain active during test assertions. Other isolated runners
retain their own cleanup infrastructure; they do not gain this bypass.

Automatic `migrate` refuses any plan with multiple backwards migrations that
crosses a protected identity, alias, measurement-origin or receipt migration,
even if its tables are empty. Preflight table locks do not span Django's
per-migration commits, and advisory locks do not cover arbitrary receipt writers.
A downgrade of that scope needs a separately reviewed offline preservation
procedure; splitting commands is not an equivalent atomic rollback guarantee.
Single-migration reversals still run their existing guards. Forward migrations
are unchanged; the official deployment runner already permits forward plans only.
Direct `MigrationExecutor` calls do not emit `pre_migrate` and must not be used
to bypass this command-level restriction in operational rollback procedures.

Independent review should cover additive migration/rollback, normalized SQL
constraints, conditional revision UPDATE, authorization/scoping, read-only
manifest evidence, compatibility and the still-open activation gates above.

## Mounted desktop UI

`AdminView` mounts `SpeciesStrainsAdminSection` for Species. It uses institutional
routes, WoRMS search and the complete Box collection (`GET /api/boxes/`) to group
Boxes by Strain. It never calls the retired resolution POST. Historical entries
without a profile are read-only. A profiled Species can be edited without an AAA
assignment, but creating a Strain requires a persisted usable AAA and server
readiness. There is no Strain edit or code-change preview screen. Country,
acquisition, donor, GPS and optional name belong to Strain creation; the initial
polyp count and optional DNA evidence belong to Box creation. See
[`frontend-ux.md`](../docs/context/frontend-ux.md).

## Collection-location visibility (M1)

`BiologicalProvenance` stays a shared table with only `id` and `name`; no ownership
column is added and no existing row is merged or deleted. Visibility is enforced by
institution-scoped association, never by a frontend filter:

- `GET /api/taxonomy/biological-provenances/` returns only provenances that the
  active institution has coded (`OrganizationProvenanceCode.organization`). An
  unassigned legacy row and another institution's private location are never listed.
- `GET /api/taxonomy/provenance-codes/` preserves the active institution's existing
  BBB assignments, including verified historical associations.
- Both legacy POSTs (`/api/taxonomy/biological-provenances/` and
  `/api/taxonomy/provenance-codes/`) are retired (405 Method Not Allowed). Clients
  cannot create an unassigned shared row or claim a historical row by supplying its
  id. Own, foreign, unassigned and nonexistent ids receive the same method refusal,
  without resolving or disclosing any location name. Restricting code creation to
  already-associated rows would leave only duplicate conflicts, so neither POST is
  retained as a nonfunctional write API.
- `GET /api/admin/collection-locations/` and the Strain-creation conflict messages
  already name only the active institution's own locations.

Two institutions may register the same geographic name independently: each gets its
own `BiologicalProvenance` row and its own BBB. BBB stays institution-scoped and a
location keeps the same BBB across Species. Unassigned legacy rows are preserved but
never attributed automatically or claimable through these APIs; a future ownership
or historical-adoption workflow requires a separate decision. No migration or data
rewrite is part of this correction.

Repository consumer inspection found only tests calling these legacy POSTs. The
active `SpeciesStrainsAdminSection` uses `GET /api/admin/collection-locations/` and
`POST /api/admin/institution-species/<id>/strains/`, whose service creates a new
location and BBB atomically with the Strain, identity, audits and M2 receipt. It
never adopts an unassigned row. Retiring the POSTs removes standalone location/BBB
creation and known-id coding of unassigned historical rows; no approved adoption
consumer was found, and no replacement workflow is introduced. External/manual API
consumers cannot be ruled out by repository inspection alone.

## Strain creation (`institution_strains.py`)

Approved rule: Strain code `AAA-BBB-X`, prefix of Box Local IDs `AAA-BBB-X.YYY`.
AAA is the institution's `OrganizationSpeciesCode`. BBB codes the geographical
location where the original polyps were collected: it is the institution's
`OrganizationProvenanceCode` of a `BiologicalProvenance` (existing constraints:
one code per institution and location, one location per institution and code;
the same location keeps its BBB for every Species). BBB never identifies the
current institution, a supplier or a current Box location. `Origin` (acquisition
events) is not used. X is never entered by an administrator.

One transaction, after explicit Admin authorization and an Organization row lock:
the profile must belong to the institution; AAA must exist and match `[A-Z]{3}`;
the location is selected among the institution's own codes (a foreign or unknown
id is `not_found`) or created (name required, ≤150; code exactly three letters
after trimming/uppercasing; a code or name already recorded here is a 409 naming
that location). X comes from `allocate_next_local_strain_number` (Organization +
Species + location). Values already carried by any Strain code (numerically,
e.g. `-01`) or Box Local ID with the same `AAA-BBB` prefix, in any Species or
institution, are skipped and never reused; gaps are allowed. The candidate's Box
namespace row is locked (`locked_namespace`) and the code rechecked, so
institutions sharing an `AAA-BBB` prefix cannot create the same code. Strain
(`number`, `origin_code` = BBB, `origin` NULL), `LocalStrainIdentity` with the
provenance assignment, and audits (location and BBB like the existing APIs, then
`Strain created: <code>` with structured metadata) commit together. No existing
Strain or Box code is rewritten; names, notes and translations are not collected.

### Idempotency contract (M2)

`request_id` is a required UUID identifying one deliberate creation intent. It is
recorded with the normalized intent (profile, and either the selected location id or
the cleaned new name/code) in `InstitutionStrainCreationRequest`, scoped to
`(organization, actor, request_id)` by a unique constraint. The receipt is written in
the same transaction as the location, Strain, local identity and audit, so a rollback
leaves no false success record.

- A new key creates one Strain and returns 201.
- Replaying the same key with the same intent returns the recorded Strain and X with
  200; no location, Strain, identity, counter or audit is written again.
- Reusing a key with a materially different intent (another profile, location or new
  location) is a 409 `strain_creation_request_conflict`.
- Keys are scoped to the institution and the actor: the same key in another
  institution is a different scope, never a replay, and never retrieves another
  institution's records.
- The Organization row lock serializes concurrent identical submissions, so exactly
  one Strain is created. Existing Strain code uniqueness and namespace locking are
  unchanged.

## Species code change preview (`species_code_change.py`)

Planning only. `plan_species_code_change(user, organization, profile_pk,
proposed_code)` and its GET route compute the impact of changing the
institution's AAA. An internal transactional service now exists, but **no public
mutation endpoint or frontend activation exists** (see below). Preview writes nothing; nothing is reserved
or locked: Strain codes, Box Local IDs (`Box.global_code`), Global IDs, aliases,
namespaces, counters and audits are untouched. `PATCH
/api/taxonomy/species-codes/<id>/` still answers `species_code_immutable`.

Input: active Admin of the explicit institution; own profile (else 404);
`proposed_code` trimmed, uppercased, `[A-Z]{3}` (else 400). Every other unsafe
condition is returned as a blocker in a 200 response.

Result (`polypbase.species_code_change.preview.v1`): `current_code`,
`proposed_code`, `feasible` (no blocker), `blockers` (`code`, `category`, ids),
`warnings`, every `strains` and `boxes` row of the institution for that Species
(all statuses, merged records included, never paginated) with current/proposed
identifier, `action` (`rename`, `unchanged`, `blocked`) and `reasons`, `summary`,
`dependencies` (counts), `code_based_lookups`, `plan_fingerprint`. Always
`advisory_only`, `revalidation_required`, `mutation_available: false`.

Strain ownership: `institution_owned` (renamable), `shared` (owned here, used by
another institution's Boxes), `legacy_unowned`, `foreign_owned`. The last three
are blockers; nothing is claimed, adopted or rewritten. Only a canonical
`AAA-BBB-X` code carrying the current AAA gets a proposed code; a Box gets one
only when its Local ID starts with its Strain's code.

| Category | Blockers |
|---|---|
| `validation` | `species_code_missing`, `current_code_unusable`, `code_unchanged` |
| `collision` | `code_assigned_in_institution`, `proposed_code_used_by_other_species` (this institution), `strain_code_collision`, `box_namespace_reserved`, `box_namespace_in_use`, `box_local_id_collision`, `box_local_id_historically_reserved` (`previous_global_code` and confirmed alias registry), `*_collision_in_batch` |
| `shared_data` | `strain_used_by_another_institution`, `strain_without_owner`, `strain_owned_by_another_institution` |
| `historical_identity` | `local_id_history_unresolved` (conflicting or unresolved current/previous/registered claims), `reviewed_identity_protected` (`Box._reviewed_identity_codes`) |
| `consistency` | `strain_prefix_mismatch`, `box_local_id_strain_mismatch`, `local_identity_mismatch`, `assignment_used_by_unplanned_strain` |

Collisions outside the institution carry `scope: "outside_institution"` and no
record id or owner. Non-blocking warnings: `missing_global_id` (reported, never
created), `noncanonical_strain_unchanged`, `box_without_current_code_unchanged`,
`current_code_on_other_species_boxes`, `proposed_code_used_outside_institution`
(no approved rule for manual cross-institution AAA reuse). Merged records are
kept as historical aliases (`merged_alias_retained`).

`plan_fingerprint` is a SHA-256 of the identity state (codes, ownership,
actions, alias sources and merge targets, Global IDs, local assignments,
`Box.local_code`, profile revision, source namespace existence/stored/effective
allocation floors and blockers), not of audit/tag counts or Box status.
A future mutation must recompute the plan in its own transaction and under its
own locks, and compare it; a preview never authorizes a write.

Tests: `apps.taxonomy.test_species_code_change_preview`. Preview has no mutation
consumer. Additive historical Local ID registry, migration `cultures.0019`,
resolution compatibility and limitations: [`../docs/box-local-id-history.md`](../docs/box-local-id-history.md).
A previous Local ID no longer blocks by itself; all retained claims must remain
unambiguous. `Box.local_code` is fingerprinted for review, never recoded here.

## Internal Species code-change application

`species_code_change_apply.apply_species_code_change` transactionally applies a
revalidated supported plan and returns a durable `SpeciesCodeChangeRequest`
receipt. It has no public endpoint or frontend caller. Preview still reports
`mutation_available=false`. Migration `taxonomy.0014` adds receipts only; rollback
refuses receipt loss. PostgreSQL locking/concurrency validation remains outstanding.

Interface, lock architecture, supported cases, preservation guarantees, retry
semantics, tests and activation gates:
[`../docs/species-code-change-apply.md`](../docs/species-code-change-apply.md).

## Country, acquisition and name of a new Strain

Approved rule: within one institution a country has exactly one BBB and a BBB
exactly one country. `OrganizationProvenanceCode.country_code` (ISO 3166-1
alpha-2, `taxonomy.0015`, nullable, never inferred or backfilled) plus the
existing constraints enforce it in the database: one code per (institution,
location), one location per (institution, code), and now one location per
(institution, country) with a format CHECK. Each institution keeps its own
correspondence.

`POST /api/admin/institution-species/<id>/strains/` now takes
`{request_id, country, country_name?, bbb_code?, link_collection_location?,
acquisition_type?, donor_institution?, latitude?, longitude?, name?, name_language?}`.
The former `collection_location` / `new_collection_location` fields are refused
(`not_allowed`): a new Strain always names its country.

- A country already coded here reuses its BBB; another BBB or link → 409
  `country_already_coded`.
- A new country needs `bbb_code` (existing manual allocation, uniqueness and
  conflicts unchanged) or an explicit `link_collection_location` to a coded
  location of this institution that has no country yet. Linking only fills NULL
  (audited UPDATE with before/after); no Strain, Box or code is rewritten. A
  location with another country → 409 `collection_location_country_conflict`.
- The Organization row lock serializes first use of a country; the partial unique
  constraint is the final defence.
- `acquisition_type`: `donation`, `field_collection`,
  `artificial_fertilization_wild`, `artificial_fertilization_captive` (two new
  `Origin.SourceType` values). `donor_institution` is free text, accepted only for a
  donation, never a POLYPBASE institution. GPS (both or none, ±90/±180, ≤6
  decimals, 0 is real) belongs to the Strain's own `Origin`, never to its country.
  No `Origin` row is created when none of these is provided.
- `name` is optional; when given it is stored once as a `StrainTranslation` in
  `name_language` (an interface language) of the new Strain only. No fallback name
  is persisted: the UI shows "Souche X" from the server number. Existing
  translations of other Strains are untouched.
- The idempotency intent now records all these normalized fields.

`GET /api/admin/collection-locations/` adds `country` per location and the
accepted `countries`; Strain payloads add `recorded_country_code`.
