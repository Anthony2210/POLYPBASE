# Boxes, lifecycle, and locations

## Identifiers: Local ID and Global ID

Despite its technical name, `Box.global_code` is the institution's **Local ID** (the `AAA-BBB-X.YYY` code). It is neither renamed nor changed. The **Global ID** is a permanent UUID that identifies the same physical Box across institutions: the `BoxGlobalIdentity` registry (unique `global_id`) and the nullable `Box.global_identity` foreign key (`global_id` column). Several institutional records can reference the same identity. The database allows at most one live record (`merged_into IS NULL`) per institution and identity.

- Allocation: only `create_automatic_box` and `create_subculture` allocate a Global ID. Each child receives a new identity. Reassigning Species/Strain does not change the Global ID. Historical imports, Transfer v1, and other technical creation paths leave it `NULL`.
- Permanence: once assigned, the Global ID cannot be changed or cleared (`Box.save()` + SQL triggers); the registry is immutable.
- `NULL` means “not initialized,” never “a different physical Box.” Migration `cultures.0022_box_global_identity` initializes only Boxes whose creation by the application is proven (automatic creation receipt or quantitative transplant allocation), excluding merges and Transfer v1 imports. Historical Boxes remain `NULL` until their physical continuity is established (e.g. SME → SSP candidates, merges, transfers).
- API: `global_id` is read-only in list and detail responses. `GET /api/boxes/global/<uuid>/` resolves only within the active institution (same `404` for unknown, uninitialized, or out of scope). Frontend: `/boxes/global/<uuid>` resolves and then replaces the URL with `/boxes/<ID local>`. The `/boxes/<code>` URLs and `/bac/<id>/` QR links are unchanged.
- Transfers v1/v2: out of scope; no identity propagation is implemented.
- Local ID history: `BoxLocalIdAlias` stores multiple confirmed former codes without renaming Boxes or replacing `previous_global_code`. Exact lookup, search, and audit links follow explicit merges within the active institution; conflicts fail closed. `local_code` remains a non-unique legacy/source label, not the operational Local ID. Contract, migration `0023_box_local_id_alias`, protected rollback, and validation limits: [`../box-local-id-history.md`](../archive/sources-fusion/box-local-id-history.md).

## Boxes and statuses

`Box` is the main culture unit. Its `global_code` is unique in the database. Django enforces the rules below, mainly through the services in `backend/apps/cultures/services.py`. Views and the frontend must not reimplement them.

| Status | Meaning and current transitions |
|---|---|
| `pending_review` | A box from historical data, labeled “À vérifier” (“Pending”). A review can set it to `active` or `inactive`; the normal workflow does not return it to this state. |
| `active` | A box being tracked. Normal manual creation requires an active zone in the same institution. |
| `inactive` | Tracking has stopped. No new records are accepted; an authorized user can still correct an existing historical record. |

Do not infer status from location alone or from the latest record date. Inventory suggestions are guidance, never automatic transitions.

## Deactivation, status review, and reactivation

Deactivating an active box requires the reason specified by the model. It closes current location periods, clears the `Box.thermal_zone` pointer, and preserves history. When reviewing a historical box and setting it to `inactive`, the reviewer can explicitly indicate that the reason and end date were not recorded, without inventing a comment, reason, or date.

Reactivating an inactive box requires an explicitly selected active zone in the same institution. This opens a new location period; the old location is never restored automatically.

Historical status review from `pending_review -> active` exceptionally allows a box with no location. This exception does not apply to normal manual creation or reactivation. Such a box must remain identifiable as active with no location until one is assigned explicitly.

## Current and historical locations

- `Box.thermal_zone` points to the current location.
- `BoxLocation` stores each location period. A historical end may be unknown.
- `BoxMovement` represents a recorded event, its kind and provenance, and its author. An unestablished legacy kind does not prove that an ordinary move occurred.
- An inactive box has no current location but may have a last historical location that can be displayed.
- Moving, deactivating, or reactivating a box must never overwrite earlier periods.

The current location and the last historical location are distinct. Interfaces and exports must use the one that answers the question and must not present history as current state.

## Concurrent moves

Moving a box is transactional. The service locks the box and its open periods, then updates the current pointer, period, movement, and audit consistently.

The intent contract uses `expected_thermal_zone_id`:

- Missing field: invalid request, because the client did not provide the state on which its intent is based.
- `null`: an explicit assertion that the box has no current location.
- An ID: the zone the client believes is the current location.

If the state changed between display and confirmation, the API returns `409` with code `box_location_changed`. The frontend must not report success; it reloads the box and zones. This mechanism complements locking and does not replace permission or institution checks.

## Movement kinds and the weekly limit

`BoxMovement.kind` distinguishes `ordinary` (a move between two zones), `initial_location` (the first location for an active box with no location, including via `move/` from `null`), `qualification`, `reactivation`, and `unclassified`. `unclassified` means “provenance not established.” It applies to all rows before migration `0013` and is the default for technical creation without an explicit kind. An ORM or SQL write can supply a kind, but is not by itself evidence of provenance or an authorized workflow. No kind is inferred after the fact.

At most one `ordinary` movement is allowed per box and Europe/Paris ISO week (Monday–Sunday). `iso_week_start` is derived from `moved_at` in `save()`. Since `0014`, a database `CHECK` enforces that it matches the Monday of `moved_at` in Europe/Paris. A partial unique constraint protects the final state and the box lock serializes writes. A second ordinary movement returns `409 box_movement_week_conflict` without mutation (`conflicting_movement_id`, `iso_week_start`, `iso_year`, `iso_week`); `box_location_changed` still takes precedence. Manual creation, transplants, deactivation, and lifecycle movement kinds do not use this slot. Neither do `unclassified` movements.

`QuerySet.update()` and `bulk_update()` through the normal manager reject `moved_at`, `kind`, and `iso_week_start`. `bulk_create()` does not compute the key: the caller must supply a consistent key for `ordinary`, or the database rejects the write. The PostgreSQL `CHECK` and uniqueness constraint also cover upserts, fixtures (`save_base(raw=True)`), the base manager, and direct SQL writes. These paths bypass the service and do not guarantee permissions, period links, or audits. They do not replace the service. `ignore_conflicts=True` can skip an invalid row on SQLite; on PostgreSQL it does not hide a `CHECK` failure. `0014` repairs no data and fails if an inconsistent ordinary row already exists. `ParisMovementWeekStart` is retained for migration serialization.

New movements reference the periods they wrote (`origin_location`, `destination_location`). An ambiguous origin stays `NULL`. Historical links are never reconstructed.

## Correcting a movement

`PATCH /api/boxes/<box_id>/movements/<id>/` (service `movement_corrections.correct_box_movement`) requires `expected_revision` and at least one of `notes`, `moved_at`, or `to_thermal_zone_id`. Active Admins and Technicians in the institution may correct movements, without a time limit or original-author restriction. The original ID, kind, and author are retained. Every correction increments `revision` and adds an `AuditLog` (`event: box_movement_correction`) with before and after values, affected periods, and any change to the current zone. No movement is created, deleted, or merged.

- Notes: always correctable, including for `unclassified`. Only a proven copy in the destination period is updated to match.
- Date: only for `ordinary` movements with consistent links, and only within the movement's original Europe/Paris ISO week (Monday 00:00 to Sunday 23:59:59, `400 movement_date_outside_week`; never another week, even if free). It must be strictly between the start of the origin period and the end of the destination period, never in the future, with no other movement or overlapping period. `iso_week_start` remains consistent.
- Destination: only for the terminal movement (open and current destination period, active box, no later movement). It updates the period and the current pointer. Allowed for `ordinary`, `initial_location`, `qualification`, `reactivation`, and `unclassified` with a recorded, consistent destination-period link. A legacy movement without a link (`has_destination_link` false) is limited to notes. No link is reconstructed.
- The API exposes `has_origin_link` / `has_destination_link` (booleans, not IDs) so the UI does not offer fields it knows will be rejected.

`409` rejections: `box_movement_changed`, `box_movement_week_conflict`, `movement_provenance_unresolved`, `movement_date_not_correctable`, `movement_period_link_missing`, `movement_period_mismatch`, `movement_period_overlap`, `movement_chronology_conflict`, `movement_not_terminal`. `400` rejections: validation, `movement_same_zone`, `movement_date_in_future`, `movement_correction_no_change`. The generic Django admin is read-only for `BoxMovement` and `BoxLocation`, and `Box.thermal_zone` cannot be edited there for an existing box.

## Historical movements at Aquarium de Paris

This product decision applies only to institution 1 and observations strictly before 2026-10-05: the temperature in historical tracking identifies the location, and a change between two consecutive reliable observations establishes a movement. `historical_location_transitions.py` makes this a read-only projection, with no `BoxMovement`, `BoxLocation`, receipt, audit, or timestamp. The zone is resolved only if exactly one `ThermalZone` in the institution has that setpoint; otherwise only the temperature category is retained. A transition without an existing representation adds an exit and an entry sharing the same `transition_id` to location history, outside the weekly bars. Both sides are sorted by the destination observation's week, a positioning convention never shown as a date. A zone query reconstructs only Boxes whose sequence includes that setpoint and another value. A matching movement, receipt, or pair of periods prevents double counting; any contradiction is retained in the backend. The UI shows new history rows only for established, unblocked transitions with both locations resolved. Temperature changes without a matching location, insufficient evidence, planner candidates, and technical period boundaries add neither a row in Box > Mouvements nor a generic uncertainty banner. Recorded movements and already accepted reconstructions are kept with their existing actions. Unresolved source evidence does not hide them. Location lists, totals, and links continue to count only the sides added by the read model, without inventing an exact date.

## History display and inline correction

The chart distinguishes `movement` (persisted `BoxMovement`) from `location_change` (a boundary inferred from `BoxLocation`). Location history shows period entries and exits without claiming that each one was a physical move. The “Dernier déplacement” banner in `BoxPage` selects the latest persisted event by date, then ID, displays origin → destination using full names, and turns into an inline editor. It does not open a modal. The save action is hidden if the normalized values have not changed; cancellation is available. A revision conflict requires an explicit reload and review. The backend remains authoritative: not all period links are exposed to the UI.

The editor uses Paris time and rejects a changed time that is ambiguous or nonexistent during a DST transition. An unchanged value retains the original instant. UTC chart details and filters based on browser-local days follow a separate contract; there is no decision to unify all time zones. The historical UTC/Paris marker issue was put on hold. It does not lead to any reclassification or historical correction.

## Transplants and lineages

`SubcultureEvent` describes a transplant from a parent box. `BoxLineage` keeps the parent-child relationship, including relationships created by this workflow. Lineage history must survive status and location changes.

New transplants are entered at the actual time of the operation, with a server timestamp. Each child allocation is either an explicit integer (including zero), `null`, or omitted. If all are known, the before/allocated/after snapshot is exact. Otherwise the total allocated and the after value are `null`: no quantitative change is applied to the parent, and its previous authoritative state remains current. The parent must always have a known count. The sum of known allocations cannot exceed it, even for a partial entry. Children with known allocations get their individual initial state; no scientific initial state is invented for the others. The service locks the parent, checks and increments its revision even for a partial entry (invalidating the intent, not making a state transition), and stores the before snapshot, ordered immutable allocations, boxes, locations, lineages, and audit in one transaction. Unknown is never zero. Older events stay unchanged; no history is reconstructed.

The server assigns child codes: `BoxCodeNamespace` reserves consecutive suffixes above the known maximum for the actual prefix, without filling gaps. Other ordinary code writes use the same lock through `Box.save()`. This counter is separate from the strain counter.

A transplant creates no artificial biological record and uses no weekly slot. Later corrections remain allowed without recalculating snapshots. If the parent reaches zero, only then does the UI offer optional deactivation, using the existing permissions and lifecycle service in a separate transaction.

Each child takes the parent's [planned weekday](#planned-weekday) by default. Sending `copy_planned_weekday: false` for a child leaves its day empty. The parent is never changed, and no weekday audit is written for the children.

The detailed contract, inspected consumers, and concurrency strategy are in [`../../backend/QUANTITATIVE_SUBCULTURE.md`](annexes/quantitative-subculture.md).

Do not infer parentage from similar codes.

### Transfers between institutions

See [identities and proposals in Transfer v2](imports-exports-transfers.md#whole-box-donation-proposals).

## Rules for changes

- Validate institution, permission, status, and zone on the server.
- Reuse the lifecycle and movement services.
- Preserve location periods, movements, authors, and audits.
- Do not implicitly restore an old zone.
- Test allowed and rejected transitions, objects from two institutions, and failures midway through an operation.
- For a concurrent change, verify behavior on isolated QA PostgreSQL. SQLite is not enough to draw conclusions about locks.

## Automatic creation of an ordinary box

`POST /api/boxes/automatic/` retains the Admin/Technician permissions and requires an active institution, an eligible strain, an active zone in the same institution, and an entry date. The client supplies no code or number: `allocate_box_codes` reserves a code in the existing namespace. Allocation, the active box, its initial period and record, audits, and the idempotency receipt are atomic. `initial_polyp_count` (required integer ≥ 0; blank is never 0) creates a single record with `origin=box_creation`, dated `entered_on`, with ephyra count `0` (product decision), strobila count and salinity `NULL`, and the authenticated actor as the known observer. The operation also requires permission to enter records (active Admin/Technician), so a superuser without that role is rejected (`403`, no writes). A partial unique constraint allows at most one `box_creation` record per box. Replaying a key does not create a second record. No movement, parentage, or biological identity is invented, and no historical record is reclassified. Users never choose the box number or full code: the old manual-code `POST /api/boxes/` was removed (`405` for all accounts) and `/api/boxes/` is read-only. Import, transfer, normalization, and transplant workflows remain separate and unchanged.

The code follows `AAA-BBB-X.YYY`. A new allocation requires `Strain.code` in `AAA-BBB-X` format and no contradiction with the recorded strain number, origin code, institutional species code, or local identity (`box_identity.py`). Otherwise the request returns `400` without writing. This checks consistency of the stored code, not biological identity. A local assignment from another institution is never evidence, even with the same code; it makes the code unusable. The prefix remains `Strain.code`, shared with transplant creation. No historical code is rewritten. `GET /api/boxes/automatic/strains/` exposes eligible strains and the components read from their code, read-only, never the next number. The selector shows only usable identities.

The form durably registers an intent through `/api/boxes/automatic/intents/`, then executes it through `/intents/execute/`. The UUID, actor, institution, immutable normalized parameters, and independent recovery secret are bound together. The secret hash is globally unique. Identical registration is idempotent; a different intent is rejected. Execution uses only the stored parameters and revalidates permissions and eligibility under the existing locks. Allocation, box, location, audit, and result are atomic. A rejection under the business rules is durable; an unexpected failure leaves the intent resumable. An intact v2 envelope resumes the same registration even if the first never arrived. Bare or mismatched UUIDs stay in GET-only inspection, with no mutation or unlocking on `unknown`/`pending`. Historical resolution cannot cancel a protected receipt. Migration `0017` introduces intent storage. UI workflow limits are described in the [frontend reference](frontend.md#ordinary-box-creation).

## DNA validation of box identity — V1

DNA validation is integrated in the code (PR #19, then added to box creation). DNA evidence is an asserted fact about the identity of **this box**, independent of its status (`active`, `inactive`, `pending_review`). It is not inferred from the species or strain and is not passed on to children or the lineage. The “Nouvelle boîte” form accepts optional evidence (“Preuve ADN (facultatif),” to the right of the initial polyp count). `dna_evidence_url` then becomes part of the creation intent and is validated by the same URL rule. `add_dna_validation_to_new_box` writes the row and its `box_dna_validation_added` audit in the same transaction as the box, under the same Admin/Technician authority. Without evidence, the key is absent and the intent keeps its previous shape. This has no effect on records, alerts, exports, QR codes, inventory, or charts.

- `BoxDnaValidation` (additive migration `cultures.0019_box_dna_validation`) stores the URL, authors, dates, revision, and revocation. A partial unique constraint allows at most one active row per box. Removing evidence revokes the row; adding evidence later creates another row. ORM deletion methods are blocked and foreign keys protect the authors and box. This does not protect against privileged technical SQL.
- A URL is required: leading and trailing whitespace is trimmed, at most 2,000 characters, HTTP/HTTPS only. The server stores the URL without ever fetching it. The link is user-provided evidence, not automatic certification of its contents.
- The detail response exposes `dna_validation: null | {id, evidence_url, created_by, created_at, revision}` (`created_by` is a human-readable identity label) and the server capability `can_edit_dna_validation`. Writes require active Admin/Technician membership in the active institution, including for superusers. A read-only user gets `403`; an authorized actor cannot find a box outside the active institution (`404`).
- `POST /api/boxes/<id>/dna-validation/` creates evidence (`201`); an active duplicate returns `409 dna_validation_exists`. `PATCH` on the same path requires the URL and `expected_revision`. Removal uses `POST /api/boxes/<id>/dna-validation/revoke/` with `expected_revision` (`204`). Absent or replaced state, or a stale revision: `409 dna_validation_changed`. An identical edit after normalization: `400`. Revisions increase across all rows for a box, including revocations, so a stale intent never targets replacement evidence.
- `dna_validation.mutate_dna_validation` locks the institution with `FOR NO KEY UPDATE`, then the box, revalidates permission, and writes the data and audit in one transaction. The institution lock coordinates membership changes without blocking foreign-key checks for audits of a concurrent move. An audit failure rolls back the mutation. Events: `box_dna_validation_added`, `box_dna_validation_updated`, `box_dna_validation_revoked`, with before and after snapshots and attribution to the institution and actor.
- `DnaValidationBadge` is to the right of the code in `BoxPage`. It shows a shield and helix with a gray `?` when there is no evidence, and a green check when evidence is active. Without evidence, a read-only user sees only an accessible indicator, with no prompt. Active evidence opens a popover readable by everyone and styled like existing popovers: a titled header with no chip or redundant status label, a block with muted labels and values (“Preuve,” “Ajoutée par,” “Date”), then a divider and the removal action. There is no close button: Escape, clicking outside, or clicking the icon again closes it and returns focus. The pencil next to the link opens the edit modal. Authorized actors remove evidence through `ConfirmActionModal`. The link displays its domain with an external-link icon and its full URL in `title`, and opens with `target="_blank" rel="noopener noreferrer"`. On small phones, the code stays on one line and the badge moves below it rather than breaking the code.
- The add/edit modal reuses the shared classes `box-dialog-backdrop`, `box-dialog`, `box-dialog-heading`, `box-dialog-form`, `box-dialog-body`, and `box-dialog-actions`. The cross, “Annuler,” and Escape close it when no request is in progress. There is no URL error on open or while typing. It appears when a changed invalid field loses focus, or on invalid submission (returning focus to the field). Typing again clears the error. If `relatedTarget` is the submit button, blur leaves validation and field focus to submit directly, without a `focusRequest` effect or delay. Previously, inserting the error on blur moved “Enregistrer” between `mousedown` and `mouseup` and prevented submission. Current frontend/browser results supplied by the user are recorded in the QA README.
- `App.tsx` still coordinates mutations through the existing API client. The badge is not updated optimistically: reload after success. A `409` blocks further attempts until an explicit reload, without replaying the write. `AuditTimeline` shows business summaries and before → after links in Actions and in Administration history.

Historical results and limits: [`../qa/box-dna-validation/README.md`](../archive/rapports/qa-box-dna-validation.md). Screenshots and `report.json` remain outside the repository. The 382 historical checks use fixtures of the real `BoxPage` with a mocked API, not end-to-end Django QA.

## Planned weekday

`Box.planned_weekday` is the day of the week planned for the box's weekly record, as an ISO number: `1` = Monday through `7` = Sunday. It is empty (`null`) by default, which means no day is planned. No migration assigns days. A database check accepts only `null` or `1` to `7`.

- Reading: the box list, box detail, and Overview payloads expose `planned_weekday` read-only to every member of the institution.
- Writing: `PATCH /api/boxes/<box_id>/planned-weekday/` with `{"planned_weekday": 1..7 | null}`. Only an active Admin of the box's institution may set or clear it, with the same checks as the other admin box actions. A Technician or a read-only user gets `403`; a box of another institution or a merged box gets `404`. Other values, strings, and booleans get `400`. The rule does not depend on the box status: active, inactive, and pending boxes can be edited.
- Audit: each real change writes an `AuditLog` (`event: box_planned_weekday_updated`) whose `modifications.planned_weekday` stores the before and after values. It appears in the box history like other box field changes. Saving the same value writes nothing. The Django admin shows the field read-only.
- Transplants: see [Transplants and lineages](#transplants-and-lineages).
- Rollback: reversing migration `cultures.0026_box_planned_weekday` is refused while any box has a planned weekday. The error gives the number of boxes. The audit log records explicit edits only: a value a child inherited on transplant has no audit event of its own, so the log cannot rebuild every assignment. Export the current assignments before clearing them for a rollback. Like `0022` and `0023`, it is a protected migration in `config/rollback_preflight.py`: a rollback plan that also unapplies other migrations is refused even when every value is empty, so `0026` must be rolled back on its own first.

## Entry points

- Models: `backend/apps/cultures/models.py`.
- Services: `backend/apps/cultures/services.py`.
- API and serializers: `backend/apps/cultures/api_views.py`, `backend/apps/cultures/serializers.py`.
- Move UI: `frontend/src/components/MoveBoxModal.tsx`; inline correction: `LatestMovementBanner.tsx`, coordinated in `App.tsx`.
- Read-only location history diagnostic: `diagnose_box_location_history` (`backend/apps/cultures/location_history_diagnostics.py`). It reports facts without repairing data or inferring movements.
- Per-institution read-only diagnostic for weekly candidates, unestablished provenance, movement-to-period links, multiple open periods, pointers, and out-of-institution zones: `diagnose_box_movement_weeks` (`backend/apps/cultures/movement_week_diagnostics.py`).
- Movement correction: `backend/apps/cultures/movement_corrections.py`.
- [Inventory and status review](#inventory-and-status-review).
- Records accepted by status: [`measurements-integrity.md`](measurements-temperatures-probes.md).
- [Imports, exports, and transfers](imports-exports-transfers.md).

## Inventory and status review

The administrative inventory is scoped to the active institution. The paginated list supports search and filters without inferring status from a missing record. A box awaiting review is not ready for normal tracking: the explicit action records the required fields and preserves historical facts. Bulk actions check every box and its eligibility on the server. Selection does not grant permission. A partial load is neither an empty list nor a complete result. The tracking preview loads on demand.

For ordinary creation, Django chooses the global code in a transaction; React neither predicts nor reserves the number. Strain identity and the institution's AAA are used in the current workflow without rewriting historical identifiers. The direct compatibility route and the intent workflow are not interchangeable protocols. Do not bypass a pending intent by creating a second box.
