# Imports and exports

## Scientific import: foundations and exact-date acceptance

At the historical milestone on 2026-10-14, canonical/CSV preparation (`measurements/canonical.py`, `csv_import.py`, contract 1.0) and the read-only Python planner (`import_planner.py`, plan 1.1) had been integrated without a service to apply plans. The current planner remains read-only, at version **1.3**. A separate service applies the restricted exact-date plans described below; it does not provide general bulk correction or onboarding. The actor's active Admin or Technician role and the selected institution are validated. Boxes are resolved by exact code within that institution.

- Statuses: `IGNORED`, `INVALID`, `UNKNOWN_BOX`, `CREATE`, `ALREADY_MATCHES`, `CONFLICT`, `BLOCKED`. A row with no scientific values is ignored and counted. If a payload is present, both required counts must be valid. Optional fields preserve absent, blank, NULL, and zero according to their contracts.
- Any duplicate box/week in the source blocks the import, even if identical. `ALREADY_MATCHES` does not rewrite values, dates, precision, or provenance. A plan or hash is not a reservation, permission to apply, or a transactional snapshot.
- A relevant transplant in the same week as a week-only record produces `week_subculture_chronology_conflict`, with evidence deduplicated by box/week. A partial parent transplant with a known occurrence is still relevant; a zero child allocation is known. Unknown legacy occurrences or allocations are not certified as safe.
- Product decision for the future service that applies plans: all or nothing, with no unresolved errors. If there is a conflict, enter the actual observation date within the week or cancel. Do not force the import or force it to keep week-only precision. Any resolution regenerates preparation and the plan.

The approved design uses separate services for routine entry, bulk correction (expected old value + audit), and onboarding, after shared preparation and review. Lab entry and import stay outside Administration; bulk correction and onboarding remain administrative. The exact-date service adds private storage of the original source in the database, a durable plan, idempotency and receipts, and revalidation under locks. Accepted sources, including batches that create nothing, are retained without expiry or automatic deletion. Inspection and preview retain no source.

The apply API and the `MeasurementImportEntry` / `MeasurementImportPanel` components implement explicit confirmation and atomic application of valid plans with exact-date CREATE, ALREADY_MATCHES, or IGNORED rows. These components are not mounted in the active React UI; their presence does not prove that users can access the workflow. The server parses the source and mapping again, checks fingerprints and permissions, locks the boxes, and revalidates the full **1.3** plan. Its baselines include `duplicate_of_id` and `environment_source_box_id`, in addition to the precision and provenance fields introduced in version 1.2. Only canonical records count toward occupancy of a scientific slot; duplicate evidence is not removed. `application_available` reflects whether the plan is ready, never a reservation. Authorized endpoints can retrieve the receipt and download the private source. Week-only CREATE rows and named observers remain blocked. Scope, storage, limits, and validation evidence: [`../measurement-csv-diagnostics.md`](../archive/sources-fusion/measurement-csv-diagnostics.md).

Detailed contracts: [`../canonical-measurement-csv.md`](../archive/sources-fusion/canonical-measurement-csv.md), [`../measurement-import-planner.md`](../archive/sources-fusion/measurement-import-planner.md). Do not extend these guarantees to the historical importers below.

## Normalized historical import

The `import_bdd_csv` command imports normalized CSVs from `data/tables/`. It runs in a transaction and offers `--dry-run`, which rolls back writes. Its `get_or_create` and `update_or_create` operations make reruns idempotent under the current keys. This legacy path can update a record's counts on the same conventional Monday. That is not the interactive POST contract and does not authorize overwrites by a future general-purpose import service. The conversion of `numero_souche_local` still uses `or None` after parsing and can lose X=0. This is confirmed technical debt and is not fixed here.

Any reset is strictly limited to the boxes in the explicitly targeted institution and their affected relations. An institutional import must never modify or delete another institution's data, including through a cascade.

The input file is a normalized product, not raw historical data. The confirmed contract for the biological columns in `saisir_releve.csv` is:

- `0` and `"0"` are valid values;
- blank, missing, non-numeric, negative, `NaN`, or infinite values violate the contract;
- the error reports at least the row and field;
- the entire import stops and rolls back;
- raw data processing and anomaly tracking happen in the upstream normalization pipeline.

Do not apply this contract to another import without inspecting it. Never silently replace an invalid value with zero.

`initialize_box_inventory` is a separate command for reviewing the status of a historical batch. It requires an institution, defaults to simulation, checks the expected count and fingerprint, and then sets a marker to prevent implicit reinitialization. It is not a general rule for future imports.

## Archived one-off campaigns

The 2026 workbook import (manifest generation, import, targeted cleanup, and related deactivations) and the dedicated correction tools for CLA/SME, Box 1902, PCA, species, and historical temperatures are no longer available procedures. The dedicated CCA-EOB commands had already been removed. The normalized imports and exact-date import service above are separate from these campaigns.

To trace an operation, see the [archived records](../archive/README.md) or the [reference before removal](https://github.com/Anthony2210/POLYPBASE/blob/9e00e2c6e0eb581334f9238ce041a7d3d2fafb19/docs/reference/imports-exports-transfers.md#import-historique-2026-manifeste-revu). Dated procedures and validation results do not prove that an operation ran or establish a database's current state. Do not replay old plans.

## Time precision: current storage limit

The `0009_temporal_precision` foundation preserves all existing dates and ISO keys. Conventional Mondays remain `legacy_unverified`, whether they come from the old 2026 campaign or a normalized CSV import that is still available, including new records created through that importer. No date is automatically certified or converted. The Phase 4A observer and origin fields remain unchanged.

Canonical preparation can represent `iso_week` without `measured_on`, but the Phase 3 planner remains read-only. It can classify an unoccupied week as `CREATE` with the blocking issue `week_precision_persistence_required` (`ready=False`). The new field does not enable week-only storage: `measured_on` is still NOT NULL, and the precision constraint rejects `iso_week`. This foundation did not enable any new write contract. The current 1.3 plan and its baselines are described at the top of this document.

The first backend read changes now expose `week_start` (the ISO Monday date, used only as a key) separately from `measured_on`, prepare per-box latest-record ordering, and distinguish the presence of a record from the availability of its exact day in the inventory filter. At that milestone, the changes did not enable any week-only writes or modify the planner's 1.1 baseline or fingerprint, classification, or readiness. Version 1.1 is a historical reference, not the current version. The mixed timeline, ordering across boxes, freshness rules, frontend, and exports still need changes before activation.

Schema requirements, scientific resolution, import and correction contracts, and consumers are detailed in [`measurements-integrity.md`](measurements-temperatures-probes.md#exact-requirements-for-enabling-week-only-storage-in-the-future). A future ISO Monday key must never be shown as an actual observation day.

## Diagnostics before applying constraints

Before applying a uniqueness constraint to a database with historical data, run the read-only diagnostic command for that domain. For biological records, `check_biological_measurement_duplicates` groups by box and ISO week and reports the institution, box, ISO key/week, count, record IDs, and dates.

If a duplicate exists, do not apply the affected migration or automatically choose a record to keep. Resolving scientific data requires an explicit decision. General rules for pre-migration diagnostics are documented in [`development-deployment.md`](../archive/sources-fusion/context/development-deployment.md).

## Current exports

Endpoints under `/api/exports/` are scoped to the active institution and check permissions on the server. The current downloadable export is a weekly CSV: one row per ISO week, with polyp, ephyra, and temperature columns for each box.

Key contracts:

- Species, strain, zone, box, and period filters can be combined.
- A box with no record in the selected period and scope is excluded from the CSV and charts.
- A cell with no record stays blank; a recorded zero remains `0`.
- The zone filter uses the historical location on the date of the record.
- Temperature uses the daily aggregate when available, otherwise the zone setpoint. Do not present this fallback as an observed temperature.
- In the preview, `measurement_count=0` distinguishes aggregate zeros for a week with no record from a real zero record. A child's quantitative initialization alone is not an exportable record.
- `include_other_zones` also includes records made in other zones for boxes that contributed records in the selected zone.
- Selection, generation, and audit remain tied to the active institution.

The `DataExport` model defines several formats. A model choice does not prove that a format has an endpoint or a UI workflow. Announce support only after checking current consumers.

## Transfers between institutions

The v1 CSV workflow was removed; compatibility routes return `410`. The [old specification](../archive/sources-fusion/transferts_csv.md) describes the historical format, not an available workflow.

### Transfer v2 — foundations integrated through Phase 5

Phase 2B was integrated into `main` at commit `20adf0a` (`feat: add transfer v2 protocol foundation`). `TransferEnvelope` / `TransferItem` are stored with stable UUIDs, the discriminator `polypbase.transfer`, and exact version `2.0`. The internal service builds snapshots on the backend from an allowlist. The strict parser is transport-independent and makes no database lookups or writes. Each source strain must have a `GlobalStrainIdentity`. The package, items, and required audit entry are created in one transaction. The scope remains an internal Python service.

Since that delivery, the following have also been integrated: read-only destination resolver (3A), identity diagnostic (3B), canonical Organization + GlobalStrainIdentity cardinality (3C), internal X allocator (4), source portable lineage projection, and opt-in protocol 2.1 snapshots (5). The default remains 2.0. Identities and lineages grant no access to another institution's operational data. The resolver's observations reserve nothing.

Phase 6 delivers a global identity and a donation proposal, without destination acceptance or changes to source stock. See [donation proposals](#whole-box-donation-proposals).

### Core scientific exchange contract (internal foundation, no workflow)

`measurements/scientific_snapshot.py` defines the pure `polypbase.scientific-snapshot` **1.0** contract (no Django): a server-side selector catalog, explicit validated selection, assembly limited to disclosed selectors, strict validation, and deterministic JSON. Field states: `value` (including `0`), `null`, `blank`, `absent`. Undisclosed information is omitted from the payload; `observations: []` means no observations. Time carries its actual precision (`exact_date`, `legacy_unverified`, `iso_week`). A week-only observation never emits a day or a Monday date. A later version adds keys without changing the meaning of existing ones. Unknown versions are rejected.

`cultures/scientific_projection.py` builds a read-only projection of a Box from the explicitly supplied source institution. It uses the existing read rule (active membership or technical superuser), treats out-of-institution, merged, and nonexistent boxes identically, and reads only canonical records and disclosed columns, with a constant query count. Scope (institution, merge status, existence) is always determined from the `cultures_box` row alone. Strain/Species/GlobalStrainIdentity and the strain eligibility check are read only if an `identity.*` selector is disclosed. Validation returns diagnostics, not exceptions, when JSON lists or objects appear where scalars are expected. No account, note, other Box, lineage, temperature, or row ID is read or emitted. A missing `GlobalStrainIdentity` stays `null` and is never created. No transactional snapshot is guaranteed. Authorization for [external sharing](organizations-permissions-sharing.md#external-read-only-sharing) is handled separately from this foundation.

## Tests to target

- Real zero versus a missing or invalid cell.
- Full rollback of an import that fails.
- Dry run with no persistence.
- Strict institution scope, using two institutions.
- Idempotency under the designated keys.
- Empty export versus a `0` value.
- Combined filters and historical location.
- `include_other_zones` enabled and disabled.
- Permissions and `AuditLog`.

Entry points: `backend/apps/cultures/management/commands/`, `backend/apps/exports/`, and `frontend/src/components/ExportsView.tsx`.

## Whole-box donation proposals

**Identity.** `GlobalBoxIdentity` represents the same biological box across local representations. `BoxOwnershipEpisode` links a distinct representation to each episode, with at most one current owner. Do not change `Box.organization` to simulate a transition. A transplant is a different box, not a new ownership episode.

**Proposal.** In Administration > Envois et réceptions, a source Admin proposes donating an entire active box to another eligible institution. The preflight writes nothing. Sending requires confirmation of the history (`history_verified: true`, `history_sha256`) and a `request_id` for identical replay. The server revalidates identity, eligibility, and current history. The details shown to the recipient contain no records, source accounts, history fingerprint, or local box ID.

**Limit.** The destination cannot accept the proposal or create a local representation. No ownership, stock, record, or movement is transferred. The source box remains operational. The source can cancel only through the API, with `expected_revision`. See the [routes](api.md) and the [separate sharing contract](organizations-permissions-sharing.md#external-read-only-sharing).
