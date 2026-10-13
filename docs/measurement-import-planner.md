# Institution-scoped measurement import planning

## Boundary and API

`backend/apps/measurements/import_planner.py` exposes the internal Python service:

```python
from apps.measurements.import_planner import build_measurement_import_plan

plan = build_measurement_import_plan(
    preparation,  # CsvResult returned by the Phase 2 read_csv adapter
    organization=selected_organization,
    actor=authenticated_user,
)
summary = plan.summary
```

This is an ephemeral read-only preview, not an endpoint, durable plan, apply
operation, correction service, audit event or acceptance receipt. It consumes the
trusted Phase 2 preparation object, not arbitrary client-created dataclasses.
The canonical parser and all existing importers remain unchanged. Invalid or
incomplete preparations still produce useful diagnostic rows, never a partially
applicable batch. Unsupported preparation schema/version blocks the plan and
prevents identity resolution of its valid rows.

The service reloads the actor and institution from the database. Both must be
active, with an active Admin or Lab Technician membership in exactly the selected
institution. It reuses `get_active_measurement_role`, the read-only helper used by
ordinary measurement writes. Viewers, anonymous users, outsiders and inactive
memberships are refused before Box lookup. A superuser alone does not substitute
for a product membership, consistent with the measurement write services.
No write service is called.

## Identity and classification

`box_reference` means the existing exact `Box.global_code`. Only Phase 2 trimming
is used: no local-code, primary-key, number, case-folded, fuzzy, alias, historical
rename or taxonomy-derived fallback. The globally unique code is looked up with an
institution predicate. No foreign Box is queried or distinguished from a missing
Box. Both yield `UNKNOWN_BOX`, `unknown_box`, and no resolved identity or baseline.
Even membership in another institution does not broaden the selected scope.

Every retained preparation record has one immutable `PlannedRow` in source order:

| Outcome | Meaning |
|---|---|
| `IGNORED` | Completely blank measurement payload, as classified by Phase 2 |
| `INVALID` | Phase 2 row validation/structure issues retained |
| `UNKNOWN_BOX` | Required reference cannot be resolved in the selected institution |
| `CREATE` | No existing biological measurement in the Box/week slot; candidate only |
| `ALREADY_MATCHES` | Supplied applicable payload agrees with the existing slot |
| `CONFLICT` | Supplied payload/date disagrees, source repeats the resolved slot, or week-only chronology is unresolved |
| `BLOCKED` | Otherwise matching row has an unsupported observer reference, or unsupported preparation version |

`CREATE` may have blocking issues. Inactive Boxes cannot receive a new ordinary
measurement; existing historical matches on an inactive Box are still recognized.
The planner never creates Boxes, references, locations, lifecycle events or data.

The slot is the real `(box_id, week_start)` model uniqueness key. The ISO Monday
is calculated **only as a lookup key**, not as `measured_on`. Measurement reads
also carry the institution predicate. No date or precision is rewritten.

## Supplied-value comparison

The comparison covers the canonical measurement payload: polyp/ephyrae/strobila
counts, salinity, culture status, attention and notes. Metadata in the payload is
compared when explicitly supplied, never silently changed or discarded.

- `value`: compare the supplied value exactly, including integer `0`, boolean
  `false`, and verbatim nonblank notes.
- `null`: compare explicit unknown nullable strobila/salinity against database
  `NULL`. It does not match scientific zero or any known value.
- `absent`: no supplied comparison or implicit default.
- `blank`: a nonnullable optional field has no supplied value; no comparison,
  clearing or model-default inference. Its state remains available in the plan.

Salinity baselines use fixed two-decimal strings; comparisons use exact decimal
numeric equality, so signed `-0.00` equals `0.00` without losing the source sign
in diagnostics. Null still never equals zero. All canonical states and raw
cells remain in `row.source`, including invalid-row coordinates and issues.
Conflicts identify the canonical field and mapped source column; baseline values
remain available to explain the disagreement without interpolated messages.
Issue codes are internal machine codes. Any future UI must translate them with
existing FR/EN i18n and safely render source content.

An exact-date source must have the same exact date as the existing measurement.
Different known dates in one week are `exact_date_conflict`, even when values
match. A week-only source can be `ALREADY_MATCHES` against an existing exact-date
measurement in the same ISO week if applicable supplied values match. The plan
retains the source's `iso_week` precision and `measured_on=None` alongside the
existing exact date. Matching never means moving, overwriting or redating it.

`observer_reference` is provenance, not a scientific count or importing actor.
Absent/null observer references remain absent/unknown and do not claim to replace
an existing observer. A named observer reference is blocked with
`observer_resolution_unsupported`: no approved lookup namespace/profile exists
in the canonical contract. No user is guessed from a username, email or importer.
The importing actor ID is a separate plan field, and the existing observer ID is
a separate baseline field. The future apply service must preserve this separation.

## Week-only chronological conflicts (plan version 1.1)

An unsatisfied valid `iso_week` row is a `CONFLICT` with the blocking machine code
`week_subculture_chronology_conflict` when a relevant quantitative subculture's
`event_date` lies inside its actual ISO week, Monday through Sunday inclusive.
The order of the observation and operation is unknown. Boundaries are lookup
keys only; the source `measured_on` remains `None`. No precedence is inferred
from import time, occurrence timestamps, source sequences or week boundaries.

Chronological relevance is distinct from eligibility as an absolute quantitative
state source in `cultures/polyp_state.py`:

- **Parent:** `SubcultureEvent.parent_box` with non-null `occurred_at` proves a
  recorded quantitative operation occurred, including a partial operation whose
  `allocated_polyp_count` and `parent_polyp_count_after` are `NULL`. Its actual
  `event_date` is relevant to week-only chronology even though its incomplete
  result cannot supersede an absolute parent state. All qualifying same-week
  events are retained, not just the latest. Scientific state resolution is unchanged.
- **Child:** `SubcultureAllocation.child_box`, known `allocated_polyps` (including
  zero), with a non-null event occurrence. A known child of a partial event is
  relevant even though that event has no quantitative parent result.
- A Box can have both its incoming initialization and its outgoing operations.
  Sibling or ancestor events do not propagate to unrelated Boxes. `BoxLineage`
  alone is not a quantitative source and codes never imply parentage.

The model's `subculture_absolute_polyp_balance` constraint permits a known
occurrence with partial results; the service records `occurred_at` and `event_date`
for both complete and partial operations. A `NULL` result is quantitative
uncertainty, not evidence that no operation happened. The model has no
pending/completed/cancelled status to filter; no such status is invented. The
service records `event_date` as the local calendar day of `occurred_at`; the
resolver uses `event_date` as its effective operation day.

Legacy events with `occurred_at=NULL` retain a parent link and stored `event_date`,
but the model explicitly denotes their occurrence history as unknown; the date
field also has a default and alone does not establish confirmed occurrence or
quantitative effects. They remain excluded, without reinterpreting their history.
Whether and under what evidence they should block chronology is a separate product
question. Unknown child allocations likewise remain excluded: their chronological
meaning beyond known quantitative initialization needs an explicit product decision.
A lineage alone does not resolve either question. This correction does not invent
those rules or repair historical dates.

Evidence is immutable and stored once per affected Box/week in
`plan.chronology_conflicts`, referenced by `row.chronology_conflict_id`:

```json
{
  "id": 1,
  "box_id": 42,
  "iso_year": 2026,
  "iso_week": 40,
  "week_start": "2026-09-28",
  "week_end": "2026-10-04",
  "operations": [
    {"event_id": 7, "event_date": "2026-10-01", "role": "parent", "allocation_id": null}
  ]
}
```

The row already includes source coordinates, resolved Box code/ID, declared ISO
week, baseline and blocking issues. Evidence groups are sorted by Box ID/week;
operations by effective date, event ID, role and allocation ID. Repeated source
rows share the evidence group rather than multiplying its full operation list.
Existing validation, duplicate, occupancy, observer and lifecycle blockers are
retained. Duplicate occupied rows also retain supplied-value/date disagreements.
A free week with a chronology conflict retains its separate persistence blocker.

An occupied matching week remains `ALREADY_MATCHES` without chronology evidence:
it recognizes existing data, not a new observation, date-precision upgrade or
origin change. An occupied nonmatching week stays a conflict and can carry both
value disagreement and chronology evidence. Nothing overwrites the occupied slot.

Resolution requires correcting the source with an **actual exact measurement
date**, then preparing and building a fresh plan. The canonical parser rejects
`date_week_mismatch` if that date is outside the declared ISO week. Exact-date
rows do not receive this week-only ambiguity blocker, even on the operation day;
existing exact-date comparisons and scientific same-day semantics are unchanged.
A calendar date alone does not establish intra-day ordering. There is no force
acceptance for unknown dates and no new scientific precedence rule.

Any unresolved row blocks readiness for the **whole file**, including otherwise
valid exact-date rows. No applicator, partial import, frontend warning or buttons
are implemented. A future UI can translate this evidence as “Conflit chronologique
détecté” / “Chronological conflict detected” and require cancellation or an exact
date; this is not an existing UI.

The additive JSON contract uses plan schema version **1.1**, so even unaffected
plans receive new fingerprints. The canonical entry/CSV schemas stay at 1.0;
measurement baselines, observer attribution and source precision are unchanged.
All evidence and row references participate in stable canonical JSON/SHA-256.

## Repeated source slots

All valid rows sharing a Box/week slot are blocked; none is silently deduplicated,
combined, chosen as a winner or considered satisfied by an existing measurement.

- `contradictory_source_slot`: overlapping supplied payload value/null fields disagree,
  or known exact dates disagree.
- `duplicate_source_slot`: repeated slot without such disagreement, including
  identical rows and complementary supplied fields/precision.

Repeated unresolved references also report their source duplicates but retain
`UNKNOWN_BOX` whether a foreign Box exists or not. Invalid rows retain their
original errors rather than acquiring invented resolved identities.

Each row references `duplicate_group_id`. The plan's `duplicate_groups` contains
each group's complete coordinates once, in source order. This bounds construction
and JSON diagnostics linearly instead of replicating a large group's coordinates
on every member. There is no automatic merge of complementary rows.

## Summary and readiness

`plan.summary` includes:

- `total_source_rows`: retained preparation records, including invalid and ignored;
- `ignored_rows`, `create_candidates`, `already_matching`, `conflicts`,
  `unknown_boxes`;
- `invalid_or_blocked`: INVALID/BLOCKED outcomes plus CREATE candidates with issues;
- `blocked_rows`: every row with blocking issues or a blocking outcome;
- `ready`: the complete preparation has no file-level or row-level blockers.

The blocked-candidate count intentionally overlaps `create_candidates`: a
week-only new measurement remains a recognizable CREATE candidate, but it cannot
be executed. Outcome counts are not a disjoint sum with `invalid_or_blocked`.
File-level blockers may exist with zero blocked rows. A valid blank-only/empty
batch is a ready no-op, not an invented measurement. For interrupted/limited
preparations, `total_source_rows` counts only retained records; `source_complete`
and the file issues explicitly prevent claiming a complete source census.

Any unknown required reference, scientific/date conflict, duplicate slot, invalid
row, unsupported operation, incomplete source, or file error makes readiness
false. Recognized rows in that plan are diagnostics only. There is no partial
application path. Readiness means **no unresolved planner blocker**, not that an
apply implementation exists or that a preview is concurrency-safe.

## Week-only persistence and guarded application dependencies

The current model has a required `measured_on` and the temporal-precision
foundation, but does not allow persisted `iso_week` precision.
A week-only CREATE therefore carries `week_precision_persistence_required` and
blocks readiness. Its source date stays `None`; no synthetic Monday is fabricated.
No database schema or migration changes are included here.

Before application, implement reviewed schema and service support for week-only
precision. The existing interactive service must not be reused blindly: it applies
live defaults, rejects nullable strobila and attributes the observer to the actor.
An import-specific service must define preservation of unknown/absent optional
values and observer provenance, not fill missing strobila with scientific zero.
Named-observer resolution also needs an approved explicit institution-scoped
reference contract. These dependencies do not prevent a safe diagnostic planner.

The frozen plan carries preparation fingerprint, selected institution and actor,
resolved Box ID/code/status/revision, weekly key, and the existing measurement
ID, full payload, exact date, creation time, observer and source sequence. Stable
canonical JSON and SHA-256 cover this preview, with no wall-clock timestamp.
An absent baseline records an empty slot at preview time only.

A future guarded applicator must reload permissions and source interpretation,
lock Boxes in a consistent order, recheck identity, institution, status, revisions,
weekly occupancy and all relevant measurement fields under locks, then apply the
whole accepted batch atomically with audit/receipt guarantees. Permission changes,
new measurements, corrections, code/status changes, deletion or lifecycle changes
can invalidate this preview. Its multiple reads do not provide a transactional
snapshot or protection from concurrent mutation. A hash is not a lock, permission,
signature, durable acceptance or complete guard by itself.

## Query bounds and local validation

For R distinct references and S distinct resolved Box/week slots, planning uses
three authorization SELECTs plus `ceil(R/200)` Box SELECTs and `ceil(S/200)`
measurement SELECTs (zero batches for empty sets). For C distinct unsatisfied
week-only slots, add `2 * ceil(C/200)` SELECTs: one for parent events and one for
child allocations per batch. Exact-date, unknown, invalid, unsupported and already
matching nonduplicate rows need no chronology lookup. Both parent and child
queries use exact Box/week range pairs, with institution predicates on the parent
and (for allocations) child. No foreign operation evidence is exposed, including
malformed cross-institution allocations. Selected scalar fields avoid lazy reads.
Measurement batch predicates select exact groups of Box IDs by week, not an
unbounded Cartesian product of all Boxes and weeks. Evidence construction is
linear in returned operations plus rows, with deterministic sorting, and duplicate
rows share evidence. There are no per-row reads, lazy related-object reads, writes,
locks, audit calls or revision increments. Maximum CSV size/rows remain bounded
by Phase 2. Duplicate diagnostics are linear in retained rows.

The focused synthetic tests use `config.test_settings` and reject every non-SELECT
SQL statement during previews and serialization. They also compare all application
model tables before and after planning, including audit, revisions and history.
Tests cover permissions, foreign indistinguishability, all classifications,
scientific null/zero states, dates/precision, duplicate groups, source failures,
immutable baselines, determinism, and query bounds across the 200-slot boundary.

Run from `backend/`, using the existing isolated SQLite configuration:

```sh
POSTGRES_DB='' POLYPBASE_TEST_POSTGRES=0 DJANGO_DEBUG=1 uv run python manage.py test apps.measurements.test_import_planner apps.measurements.test_import_chronology apps.measurements.test_canonical_csv apps.measurements.test_write_services apps.measurements.test_nullable_strobila --settings=config.test_settings
POSTGRES_DB='' POLYPBASE_TEST_POSTGRES=0 DJANGO_DEBUG=1 uv run python manage.py check --settings=config.test_settings
POSTGRES_DB='' POLYPBASE_TEST_POSTGRES=0 DJANGO_DEBUG=1 uv run python manage.py makemigrations --check --dry-run --settings=config.test_settings
```

No production/shared database or dataset is required. No PostgreSQL concurrency
claim is made by these read-only tests.
