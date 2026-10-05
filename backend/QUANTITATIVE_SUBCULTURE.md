# Quantitative subculture backend contract

## Frontend integration contract

`Box` list/detail, Inventory rows, and Overview rows expose:

```ts
type CurrentPolypState = {
  polyp_count: number | null;
  revision: string; // opaque: currently box:<id>:<monotone write revision>
  source: null | {
    kind: 'measurement' | 'subculture' | 'subculture_initialization';
    id: number;
    timestamp: string;
    measured_on?: string; // measurement sources only
  };
};
```

Use `current_polyp_state.polyp_count` for current stock. Do not replace or
reinterpret `latest_measurement`: it remains the latest real weekly measurement.
`null` is unknown; `0` is a known scientific state. Do not parse revision tokens.

`POST /api/boxes/<id>/subcultures/` requires the active organization context and:

```json
{
  "expected_current_state_revision": "box:42:7",
  "reason": "",
  "notes": "",
  "children": [
    {
      "thermal_zone_id": 12,
      "allocated_polyps": 0,
      "local_code": "",
      "copy_origin": true,
      "copy_volume_liters": true,
      "notes": ""
    }
  ]
}
```

- One to twenty children, in allocation order. `allocated_polyps` is optional:
  omitted or JSON null means unknown; a known value must be strictly a JSON
  integer in `[0, 2147483647]`. Booleans, floats (including `1.0`), strings and
  empty strings are rejected. All children must have an active zone in the parent institution.
- `reason`, `notes`, and the child options apart from zone/allocation are optional.
- Parent must be active, have an eligible Strain, and have a known current count.
  The sum of known child allocations must not exceed that count, even for a
  partial operation. An unknown parent cannot create even an all-unknown or zero allocation operation.
- `event_date`, `occurred_at`, child `global_code`, `box_number`, and
  `initial_polyp_count` are rejected, not silently accepted. The server records
  the actual occurrence and assigns the next consecutive canonical codes.
- `copy_volume_liters` defaults to true, as declared by the existing option.
- Success: `201`, existing event response keys plus `occurred_at`,
  `parent_polyp_count_before`, `allocated_polyp_count`,
  `parent_polyp_count_after`, `parent_state_snapshot`, `allocations`.
  `children` contains Box list payloads including their current states, and no
  synthetic latest measurement. Allocation rows contain `id`, zero-based
  `position`, `child_box_id`, `child_global_code`, `allocated_polyps` (integer or null).
  When every allocation is known (including zero), before/total/after are exact
  and the parent transitions to before minus total. With any unknown allocation,
  `allocated_polyp_count` and `parent_polyp_count_after` are null, not the known
  subtotal or zero. The known parent-before snapshot is retained. Each known child
  initializes independently; an unknown child has no scientific initial source.
  No redundant completeness flag is returned: aggregate nulls express no known
  parent quantitative effect. Audit metadata preserves the same nulls and ordered allocations.
- Stale intent: `409`, `code: subculture_current_state_changed`, `detail`, and
  fresh `current_polyp_state`. Reload the parent before retrying, do not retry
  the old intent automatically. Other business errors are `400`, with a code
  (`subculture_parent_count_unknown`, `subculture_parent_ineligible`,
  `subculture_allocation_exceeds_parent`, `subculture_invalid_zone`, etc.).
  Field validation errors use DRF's existing field-error shape. Permissions
  remain `403`; an inaccessible parent remains `404`.
- After success, refresh the parent/list/zone state. Measurement POST/PATCH
  response contracts and editing routes are unchanged.

### Advisory child code preview

`GET /api/boxes/<id>/subcultures/code-preview/?count=2` requires authentication,
the active organization context, and the same lab-write permission as creation.
`count` is required and must be an integer from 1 to 20. Success is `200`:

```json
{
  "reserved": false,
  "children": [
    {"position": 0, "global_code": "AQT-QA-1.002", "box_number": "002"},
    {"position": 1, "global_code": "AQT-QA-1.003", "box_number": "003"}
  ]
}
```

Responses use `Cache-Control: no-store` (with Django's additional cache-prevention
directives). Invalid count/code length returns `400`; permission and inaccessible
parent errors follow the existing API conventions. The server derives the namespace
from the authorized parent's Strain, never from a client prefix or visible Box list.
It reads the global namespace high-water and existing leading numeric suffixes,
including legacy decorations, without creating/updating a counter or taking a row
lock. It returns no foreign Box details. Repeated previews do not consume codes.
The view/helper issue only reads; existing application language/session middleware
may still initialize account preferences and save the session, as on other GETs.
No Box, scientific state, business audit, or namespace counter is mutated.

Candidates are advisory, not reserved or guaranteed: concurrent creation, import,
or rename can change them, even without changing the parent's scientific revision.
The preview does not validate biological eligibility, quantities, or child zones.
POST remains authoritative, allocates under its existing transaction/locks, and
continues rejecting client-supplied codes. No preview token is required on POST.

### `BoxDetail.biological_timeline`

Newest effective day/source sequence first. Every entry has:

```ts
type BiologicalTimelineEntry = {
  kind: 'measurement' | 'subculture' | 'subculture_initialization';
  id: number;
  identity: string; // measurement:<id>, subculture:<id>, subculture_initialization:<id>
  author: { id: number | null; username: string | null };
  timestamp: string | null; // actual subculture occurrence; legacy unknown stays null
  effective_date: string;
  state_sequence: number | null;
  polyp_count_before: number | null;
  polyp_count_after: number | null;
  allocated_polyps: number | null;
  allocations: Array<{
    id: number; position: number; child_box_id: number;
    child_global_code: string; allocated_polyps: number | null;
  }>;
  children: Array<{ id: number; global_code: string }>;
  can_edit: boolean;
};
```

- `measurement` additionally has `measurement` containing the unchanged
  BiologicalMeasurement payload, including ephyrae/strobila/salinity and editing
  restrictions. Only this kind can be edited via a measurement route.
- `subculture` additionally has `parent_state_snapshot`, `reason`, `notes`.
  A known `polyp_count_after` is an absolute resulting state, never a decrement
  applied dynamically to a subsequently corrected measurement. Partial events
  remain visible as immutable history with null aggregate/after; they provide no
  quantitative graph point. Never plot a null as zero or reuse the before snapshot
  as an after value.
- `subculture_initialization` additionally has `event_id` and
  `parent: {id, global_code}`. It is not a measurement, has no invented ephyrae,
  strobila or salinity, and is not editable. Only known child allocations (including
  zero) create this entry; unknown allocations remain event evidence, not an invented
  initialization entry.
- Legacy events remain `subculture` entries with unknown timestamp/counts and
  unchanged lineage. Existing legacy synthetic measurements are not deleted,
  reclassified, corrected, or given new provenance.
- Use `identity`, not numeric `id`, for UI identity; tables can share numeric IDs.

## Scientific state and concurrency

`polyp_state.py` is the common absolute-state resolver. Only complete parent
operations and known child initializations are quantitative candidates, in both
plain and prefetched paths and measurement eligibility subqueries. Partial
operations still advance the Box revision to invalidate stale intentions, but
never consume/supersede a measurement, prior complete transition or known
initialization. Revision invalidation is not a scientific transition.

The latest complete operation by
per-Box serialized sequence supersedes every source that existed when it
committed, including any future-dated measurement. Only a measurement created
after that operation, with `measured_on` on/after the operation day, can supersede
it. Among eligible measurements, biological effective day, source sequence and
legacy creation timestamp/ID determine priority. Same-clock-tick operations do
not depend on wall-clock precision or unrelated cross-table PK values.

Measurement `save()` uses the same Box row lock as interactive POST/PATCH and
subculture, advances the Box revision, and assigns a sequence to new rows.
Correction retains the original source sequence, while invalidating prepared
intents. Thus correcting a pre-operation measurement does not rebase an existing
parent-after snapshot, even when its measured date is corrected forward. A newly
created eligible measurement is a new absolute source. Existing measurement date
validation is unchanged (including pre-existing future-date behavior); a consumed
future-dated source cannot revive parent stock or permit another spend.

Subculture validates parent/organization/actor/eligibility under the parent lock,
locks distinct child zones in ascending ID order, then the Box namespace. Event,
revision, code reservation, children, allocation rows, locations, lineage and
mandatory audit share one transaction. Failure rolls everything back, including
first-use counter creation and reservations. No alert effects are introduced.

Quantitative events and allocation rows reject ORM updates/deletion and expose
no editing API. PostgreSQL also guards direct SQL updates/deletion. SQLite
provides SQL update guards but relies on ORM deletion protection: Django's SQLite
flush uses DELETE rather than PostgreSQL TRUNCATE. The nullable author FK can be
cleared when an account is deleted; the original subculture author name survives.
Referenced Boxes and events remain protected. Snapshots retain source identity,
original polyp count, source timestamp/date and the observed revision.

### Optional-allocation migration

`cultures.0012_optional_subculture_allocations` makes child allocations nullable
and widens the balance constraint with a timestamped partial branch requiring a
known before, nonnull snapshot and sequence, and null aggregate/after. The legacy
all-null branch and complete exact-balance branch remain unchanged. There is no
data migration or fabricated backfill. Existing immutability guards are removed
and reinstalled around schema operations so SQLite table rebuilds preserve them;
PostgreSQL retains the same protection semantics. Reversing after partial data
exists is not supported without an explicit data decision: the old schema cannot
represent unknown allocations and must not coerce them to zero.

## Codes and inspected writers

`BoxCodeNamespace` is independent of Strain number counters. Its globally unique
namespace is the Strain code prefix, not organization or Strain PK. Allocation
reconciles every existing code's leading numeric suffix in that namespace,
including noncanonical padding/legacy trailing decorations, then reserves exactly
N suffixes above the high-water mark. Minimum padding is three digits; 999 ->
1000. Holes and removed codes do not get reused after their high water is known.
No legacy codes are changed and no historical deleted suffixes are invented.

Unique get-or-create plus a row lock safely handles namespace first use. Namespace
reconciliation never locks existing Boxes, avoiding namespace -> parent lock
inversion. Existing Box creation and renaming through `save()` cooperates, including
manual creation, Django admin, `import_bdd_csv`, `normalize_box_codes`, demo seed
and Transfer v1. Existing Box rename obtains its Box lock before its namespace.
Transfer v1 automatic codes now use the allocator, and explicit-code suggestions
do not consume a suffix. Strain/local X allocation remains untouched.

There are no production Box bulk-create/code-queryset-update consumers in the
inspected backend. Test fixtures do use these bypasses: the next allocator call
reconciles them. New bulk/SQL writers must cooperate with namespace and Box state
locks; reconciliation alone cannot guarantee correctness for uncooperative
concurrent SQL. Arbitrary SQL/ORM bulk measurement writes likewise bypass
`save()`'s revision protocol and are not supported scientific writing paths.

## Current-state consumer audit

| Consumer | Result |
|---|---|
| Box list/detail/scan-related Box serialization | Adds `current_polyp_state`; latest measurement unchanged |
| Administration Inventory | Adds current state; freshness, age, qualification and first measurement dates remain measurement-only |
| Overview active boxes | Adds current state; history, tracked-in-app eligibility and earliest biological date remain measurement-only |
| Thermal zone list/detail | Adds `current_polyp_totals: {polyp_count, unknown_box_count}` for active Boxes in that same institution |
| Dashboard | Adds `current_polyps` and `current_polyps_unknown_box_count` for active Boxes; existing `measured_*` historical totals retained |
| Weekly export eligibility, preview and CSV | Deliberately unchanged: actual weekly measurements only, original counts preserved, initialization-only children excluded |
| Transfer v1 preparation/v2 packages | Declared/planned transfer quantities remain declared quantities, not repurposed as current stock |
| Actions/business audit serialization | Adds quantitative before/allocated/after/occurrence/allocations; legacy initial-count presentation remains compatible |

List and Inventory use bounded latest-source prefetches, not full histories or a
new per-row current-state query. Counts aggregated across Boxes carry an explicit
unknown-Box count rather than falsely claiming unknown states are measured zero.

## Validation

### Optional-allocation validation (2026-10-05)

This follow-up is backend-only; the PostgreSQL results below belong to the prior
complete-allocation implementation and do not validate optional allocations.
The follow-up is ready for isolated PostgreSQL validation, not production rollout.

Every command below used the same explicit environment from `backend/`:

```sh
POSTGRES_DB='' POLYPBASE_TEST_POSTGRES=0 UV_OFFLINE=1 DJANGO_SECRET_KEY=isolated-test-key-not-for-production uv run python manage.py test apps.cultures.test_quantitative_subculture apps.cultures.test_quantitative_subculture_migrations apps.cultures.test_quantitative_subculture_concurrency --settings=config.test_settings --noinput
POSTGRES_DB='' POLYPBASE_TEST_POSTGRES=0 UV_OFFLINE=1 DJANGO_SECRET_KEY=isolated-test-key-not-for-production uv run python manage.py test --settings=config.test_settings --noinput
POSTGRES_DB='' POLYPBASE_TEST_POSTGRES=0 UV_OFFLINE=1 DJANGO_SECRET_KEY=isolated-test-key-not-for-production uv run python manage.py check --settings=config.test_settings
POSTGRES_DB='' POLYPBASE_TEST_POSTGRES=0 UV_OFFLINE=1 DJANGO_SECRET_KEY=isolated-test-key-not-for-production uv run python manage.py makemigrations --check --dry-run --settings=config.test_settings
git --no-pager diff --check
```

- Final focused run: 62 tests, 49 passed, 13 PostgreSQL-only skips (20.612 s).
- Broad isolated run: 892 tests, 840 passed, 52 skips (123.750 s). After this
  run, only test coverage was extended; the final focused run includes that extension.
- Configuration, migration drift and whitespace checks passed.
- The first migration-generation attempt stopped before DB access because DEBUG
  was off and no secret key was configured. Subsequent commands explicitly used
  the public test-only key shown above; no real secret was read or added.
- Migration coverage preserves legacy and complete evidence without backfill,
  accepts nullable partial evidence, rejects mixed invalid aggregates, and checks
  direct SQL immutability after SQLite schema rebuilds.
- No frontend, audit presentation adapter, Transfer, dependency, commit, production,
  shared database or Neon changes/access in this follow-up. PostgreSQL lock/race
  behavior and guard behavior still require isolated PostgreSQL QA.

### Prior complete-allocation validation

All Django check/migration/test commands run with `POSTGRES_DB=''`, `DJANGO_DEBUG=1`, and
`--settings=config.test_settings`. Default DB is isolated in-memory SQLite.
PostgreSQL opt-in `POLYPBASE_TEST_POSTGRES=1` fixes loopback `127.0.0.1:55439`,
QA role/database `subculture_qa`, and test database `test_subculture_qa`; it never
inherits shared DB credentials/hosts. Used a newly created PostgreSQL 16 Alpine
container with tmpfs storage, no copied data and no passwords/secrets. Existing
`polypbase-postgres-local` was inspected for availability only, never queried.

Intermediate runs:
- Quantitative science/API tests: 23/23 SQLite and 23/23 PostgreSQL passed.
- PostgreSQL targeted concurrency: 17/17 passed (12 new quantitative races,
  3 existing measurement races, 2 existing Transfer v1 races).
- Full backend SQLite: 854 tests, 803 passed, 51 skipped, no failures/errors.
- Initial migration generation lacked development DEBUG configuration and failed
  before connecting; rerun with safe explicit development/test settings passed.
- Existing subculture tests initially failed because their legacy request contract
  was intentionally removed; updated to explicit quantitative allocations and
  server codes. No unrelated tests were changed.

Final validation:
- Full SQLite suite before the final future-date hardening: 868 tests, 816 passed,
  52 skipped, no failures/errors (172 seconds).
- Full PostgreSQL suite before final hardening: 868 tests; 8 failures and 36 errors, all in unchanged
  portable-lineage/Transfer v2 lineage TestCase tests. Their existing PostgreSQL
  service deliberately rejects caller-owned transactions with `Portable lineage
  requires its own transaction`. Two representative failures were reproduced with
  the new Box/measurement save paths replaced in memory by their baseline behavior;
  no repository files were reverted or changed. These unrelated tests were not fixed.
- Expanded affected PostgreSQL scope before final future-date hardening: 263/263
  passed, including science, migration, codes, actual concurrency, weekly exports,
  audit, lifecycle, Inventory, Overview and organization scoping (146 seconds).
- Django `check`, migration drift (`makemigrations --check --dry-run`) and
  `git diff --check` passed. French/English `.mo` catalogs compiled with ephemeral
  `polib==1.2.0`; no dependency added to the project or production configuration.
- Added final regressions for future-dated consumed sources, corrections moving
  dates forward, eligible new measurements after a consumed future source, and
  inactive/missing service actors. Also verifies numeric trailing code decorations
  register the actual Strain namespace rather than acquiring another counter.
- Final full SQLite suite: **873 tests, 821 passed, 52 skipped, no failures/errors**
  (179 seconds). Skips include the PostgreSQL-only lock/concurrency tests and the
  production SQL DELETE guard test; these run in the PostgreSQL validation.
- Final expanded affected PostgreSQL scope: **268/268 passed, no skips**
  (214 seconds). Includes all 54 new quantitative tests (41 science/API/consumer
  tests, 12 actual concurrency races, 1 populated migration preservation test).
- Final Django configuration check, migration drift check and whole-worktree
  whitespace check passed again after the final code changes.
- Frontend checks and visual QA were not run by this backend-only task.
- Docker's first tmpfs invocation failed from Git Bash path conversion without
  creating a container; rerunning with `MSYS_NO_PATHCONV=1` succeeded. The empty QA
  container was removed after validation and recreated empty for final hardening
  reruns. It has now been removed with its ephemeral storage. Only the pre-existing
  shared local container remains, untouched. No shared database was modified.

### Final executed commands

From `backend/`, default isolated SQLite:

```sh
POSTGRES_DB='' DJANGO_DEBUG=1 uv run python manage.py test --settings=config.test_settings --noinput
POSTGRES_DB='' DJANGO_DEBUG=1 uv run python manage.py check --settings=config.test_settings
POSTGRES_DB='' DJANGO_DEBUG=1 uv run python manage.py makemigrations --check --dry-run --settings=config.test_settings
git --no-pager diff --check
```

The PostgreSQL full-suite invocation used the same test command with
`POLYPBASE_TEST_POSTGRES=1` added. The final passing expanded invocation used:

```sh
POSTGRES_DB='' DJANGO_DEBUG=1 POLYPBASE_TEST_POSTGRES=1 uv run python manage.py test \
  apps.cultures.test_quantitative_subculture \
  apps.cultures.test_quantitative_subculture_concurrency \
  apps.cultures.test_quantitative_subculture_migrations \
  apps.cultures.test_biological_measurement_concurrency \
  apps.cultures.test_transfer_v1_concurrency \
  apps.cultures.tests apps.cultures.test_box_strain_scoping \
  apps.cultures.test_box_inventory_api apps.cultures.test_box_detail_organization_scoping \
  apps.cultures.test_overview_history apps.cultures.test_box_lifecycle \
  apps.cultures.test_box_move_concurrency apps.cultures.test_transfer_strain_ownership \
  apps.audit apps.exports apps.measurements --settings=config.test_settings --noinput
```

PostgreSQL commands require a newly authorized disposable empty QA instance on
the fixed loopback port; none is left running by this task. Do not point them at
the shared local copy. SQLite needs no container. No production migrations were
executed; migrations were applied only inside disposable test databases.

No frontend files edited by this backend task; concurrent frontend work preserved.
No commit, push, stash, deploy, shared DB, Neon, or production access.
