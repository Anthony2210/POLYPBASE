# Automatic ordinary Box creation

## Legacy direct API contract

`POST /api/boxes/automatic/` uses the existing authenticated session and CSRF
protection. `X-Organization-Id` is required; it must identify an active institution
accessible to the actor. No institution is silently selected for this endpoint.

```json
{
  "strain": 123,
  "thermal_zone": 45,
  "entered_on": "2026-10-01",
  "volume_liters": "1.50",
  "notes": "Optional note",
  "request_id": "655cda25-058b-46b5-a71f-1962edb30132"
}
```

- Required: `strain` and `thermal_zone` (positive integer PKs), `entered_on`
  (non-null ISO date), `request_id` (non-null UUID).
- Optional: `volume_liters` (decimal, at most 5 digits / 2 decimal places, default
  null), `notes` (string, default empty, surrounding whitespace stripped).
  Zero volume remains distinct from null; volume validation matches legacy POST.
  Volume is API-only for now: the ordinary `CreateBoxPanel` always sends
  `volume_liters: null` and does not ask for a volume. Adding it to the UI needs
  an explicit product decision.
- Unknown fields are rejected with field-level `400` errors. In particular,
  `global_code`, `local_code`, `box_number`, `organization`, `origin`, `status`,
  and any lineage/measurement fields are not accepted.
- `201 Created`: new committed Box; `200 OK`: identical retry, same Box ID.
  Both return the existing **BoxDetailSerializer** representation, not an envelope
  or code preview. It includes `id`, committed `global_code`, `box_number`, empty
  `local_code`, nested strain/zone, and the other existing detail fields. Retries
  return the current authorized Box representation, not a frozen response snapshot.
- `409 Conflict`: same institution + actor + request_id with different normalized
  intent. Body: `{"code": "box_creation_request_conflict", "detail": "..."}`.
- `409 Conflict`: a cancelled key returns
  `{"code": "box_creation_request_cancelled", "detail": "..."}` and never creates.
  After request UUID validation and current authority checks, this takes precedence
  over intent validation, including invalid/missing strain or zone in a late request.
- `400`: malformed input or currently ineligible strain/zone, including on retry.
  `403`: missing/unauthorized institution or insufficient current authority
  (unauthenticated responses follow existing authentication configuration).

## Durable homepage lifecycle

The homepage now uses separate durable registration and execution, with an
immutable normalized intent and an independent recovery token. A complete scoped
envelope can resume even when registration never reached Django. Bare or divergent
stored UUIDs remain inspection-only and never acquire cancellation authority.

See [Durable ordinary Box creation intents](AUTOMATIC_BOX_INTENT_LIFECYCLE.md) for
the API contracts, states, locking, rollback, migration, compatibility and QA gates.

The direct creation contract above remains available to legacy clients. Legacy
POST resolution accepts only a request UUID and can durably cancel an absent
legacy key; it must never probe corrupted storage. Protected pending or terminal
non-created intents refuse UUID-only mutation (409). GET resolution remains
read-only, returning unknown, pending, cancelled, or the authorized committed Box.
An unknown or pending read never permits draft replacement.

## AAA-BBB-X consistency and strain options

A Box identifier has the approved structure `AAA-BBB-X.YYY` (species code,
biological origin code, strain number, Box number). The namespace used by ordinary
creation, subculture and Transfer v1 remains the established `Strain.code`; this
workflow never rebuilds or rewrites it (`apps/cultures/box_identity.py`).

A new allocation is accepted only when `Strain.code` fully matches
`^[A-Z]{3}-[A-Z]{3}-[0-9]+$` (the historical importer's strict shape) and no
recorded reference field contradicts it: `Strain.number` (X), `Strain.origin_code`
(BBB), the active institution's `OrganizationSpeciesCode` (AAA) and the strain's
`LocalStrainIdentity` species/provenance assignments. Missing records are not
contradictions. "usable" therefore means *saved code well formed and not
contradicted*, not independent biological corroboration.

Local code assignments are evidence only for the institution that owns them. A
`LocalStrainIdentity` is treated as a contradiction (`local_identity_mismatch`)
when the strain is not owned by the active institution, when either assignment
belongs to another institution (even with the same literal code), or when the
species assignment is for another species. Its provenance name is then never
returned. Records are not modified.

Otherwise creation answers `400` on `strain` (codes
`strain_identity_<reason>`), before any namespace, Box, audit or receipt write. A
replay of an already committed receipt is not re-checked: it allocates nothing.

`GET /api/boxes/automatic/strains/` (same authority and explicit institution as
creation, read-only, `no-cache`) lists `eligible_strains` with species, recorded
strain name, `code`, active Box count in the institution and `identity`:
`{"status": "usable", "prefix", "species_code", "origin_code", "strain_number",
"recorded_provenance_name"}` or `{"status": "unusable", "reason"}`. The components
are read from the saved code. `recorded_provenance_name` is optional reference
metadata: only the name of a `BiologicalProvenance` recorded through the active
institution's own assignment; a provider, place or acquisition event is never
presented as BBB. The next Box number is never returned or reserved.

`seed_demo_data` adds one synthetic Paris strain, `AAU-DMO-1` (number 1, origin
code `DMO`, no `Origin`, no local identity, notes stating it has no verified
provenance, localized name "Souche de démonstration fictive" / "Fictitious
demonstration strain"), so a freshly seeded demo can exercise this workflow. The
picker uses the compact localized Strain N label, recorded code and active Box
count; the synthetic name and notes remain saved but are not repeated there.

Legacy demo codes
(`1-ATL`, `3-MED`, `2-PAC`, `1-LAB`) stay unchanged and unusable. Existing demo
databases are not modified; re-run the command only on a fresh disposable one.


## Authority, normalization and persistence

Active admins and laboratory technicians can create; viewers cannot. The existing
technical superuser break-glass is retained, but does not bypass inactive account,
inactive institution, strain eligibility, or zone validation.

Both services reload and lock current institution, membership and actor before
looking up any receipt. Creation rejects a cancelled receipt immediately; otherwise
it locks and revalidates strain and zone before creation or replay. Resolution of a
created receipt checks current Box ownership without requiring strain/zone eligibility.
Owned strains and unowned legacy strains already used in the active institution use
`eligible_strains`, unchanged.
Zones must be active and owned by that institution. An existing receipt is never a
permission grant. A receipt whose Box no longer belongs to the institution is not
replayed.

Normalized intent contains strain PK, zone PK, ISO entered date, fixed two-place
volume or null, and trimmed notes. Omitted volume equals explicit null; omitted
notes equals blank notes. Decimal spelling and signed numeric zero are normalized;
zero does not equal null. UUID spelling is normalized by the UUID field. The key is
scoped by institution and actor, not by globally unique UUID alone.

`AutomaticBoxCreationRequest` stores the normalized intent and resulting Box in the
same transaction as code allocation, Box, initial BoxLocation and required CREATION
audit. Failure at any stage rolls everything back, including the namespace high
water. No pending receipt survives a rollback. Receipt uniqueness is enforced in
the database. Receipt references use PROTECT: deleting its Box, actor or institution
must not silently remove the durable retry history. There is no automatic expiry.
Resolution is the only public cancellation operation: it can insert an absent-key
tombstone, but cannot cancel a committed creation or delete/replace a receipt.

Receipts have explicit `cancelled` state and nullable Box/intent fields. The database
constraint `automatic_box_request_coherent_state` permits exactly:

- `cancelled=False`, non-null Box and intent (committed creation);
- `cancelled=True`, null Box and intent (permanent cancellation).

Migration `cultures.0016_automaticboxcreationrequest` creates this final model
directly: nullable Box/intent, `cancelled`, the unique receipt key, the coherent
state constraint and PROTECT on actor, institution and Box. Earlier unreleased
drafts 0016/0017 were consolidated before any commit or deployment; a local
database that applied those drafts must be migrated back to `cultures 0015` with
the draft files before using the consolidated migration (never `--fake`).

## Lock ordering and compatibility

Order: Organization -> OrganizationMembership -> User -> Strain -> ThermalZone ->
BoxCodeNamespace. The institution lock serializes competing ordinary automatic
creations and resolutions, including first use of a retry key; uniqueness is an
additional safeguard. Resolution uses only the organization -> membership -> user
prefix of this order and never takes strain, zone or namespace locks.
This intentionally serializes this workflow within one institution rather than
introducing a second reservation protocol.

Upstream locks use PostgreSQL `NO KEY UPDATE`: competing state mutations are
excluded while FK key-share checks from subculture inserts remain compatible.
The service never locks an existing Box after locking the namespace. Subculture
retains its existing parent -> sorted zones -> namespace order. Allocation reuses
`allocate_box_codes(strain.code, 1)` and Box.save registration, including global
namespace scope, high-water reconciliation and no gap filling. Different strains
with the same code still share the existing namespace, regardless of institution.

Product rule: users never choose Box serial numbers or full Box identifiers for
ordinary creation; Polypbase generates them server-side. The former manual
`POST /api/boxes/` is therefore removed: `/api/boxes/` is list-only and answers
`405 Method Not Allowed` to every authenticated account, superusers included,
without writing a Box, location, audit or namespace value. Existing Boxes and
their historical identifiers are unchanged.

Importers (`import_bdd_csv`, historical import), `normalize_box_codes`, Transfer
v1/v2 imports and subculture keep their own services and are unaffected. The
only remaining interactive path that accepts an explicit identifier is the Django
admin `BoxAdmin`, reserved to staff accounts with model permissions for technical
maintenance; ordinary institution roles have no access to it. Ordinary creation
has no parent, lineage, biological measurement, polyp initialization, movement event
or invented biological identity. Entered date does not backdate the initial location;
its timestamp remains the current server time, as for legacy ordinary creation.

## Validation

All backend commands must disable dotenv loading and use `config.test_settings`:

```sh
PYTHON_DOTENV_DISABLED=1 POSTGRES_DB='' DJANGO_DEBUG=1 uv run python manage.py test apps.cultures.test_automatic_box_creation apps.cultures.test_automatic_box_resolution apps.cultures.test_automatic_box_identity apps.cultures.test_seed_demo_box_creation apps.cultures.test_automatic_box_concurrency --settings=config.test_settings --noinput
```

SQLite exercises validation, API shape, durable replay, permissions, organization
isolation and injected rollback failures, but skips PostgreSQL lock tests.

`test_automatic_box_concurrency` uses independent connections and verifies actual
blocking via `pg_blocking_pids`. It covers same-key first use/replay/conflict,
different keys, rollback, ordinary vs subculture in both directions (different
zones, shared namespace), and committed permission/institution/actor/zone/strain
changes while creation or replay waits. Resolution races cover create-first,
cancel-first, concurrent resolves, both rollback directions and current permission
changes while resolution waits. `test_automatic_box_resolution` additionally checks
API shape, recovery despite now-invalid strain/zone, scoped tombstones, coherent
receipt states, protected references, rollback and translations.

PostgreSQL requires a **disposable empty** instance matching the explicit opt-in in
`config.test_settings`: loopback port 55439, database/user `subculture_qa`, test
database `test_subculture_qa`. Never point these tests at the default database,
Neon, production or a shared QA database. Only after provisioning that disposable
instance, add `POLYPBASE_TEST_POSTGRES=1` to the command above. The existing
`apps.cultures.test_quantitative_subculture_concurrency` suite should run alongside
it. SQLite results do not establish PostgreSQL concurrency correctness.

Status (2026-10-26): the SQLite suites pass; the 23 PostgreSQL concurrency tests
have **not yet been executed**, because no disposable instance matching the opt-in
was available. That run remains a release gate.
