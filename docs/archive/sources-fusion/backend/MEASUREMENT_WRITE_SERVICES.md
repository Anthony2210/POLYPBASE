# Shared biological measurement writes

`apps.measurements.services` provides two ordinary-write operations:

```python
measurement = create_biological_measurement(
    box_id=box_id,
    organization_ids=server_resolved_organization_ids,
    user=actor,
    data={"measured_on": measured_on, "polyp_count": 0, "ephyrae_count": 0},
)

measurement = correct_biological_measurement(
    box_id=box_id,
    measurement_id=measurement_id,
    organization_ids=server_resolved_organization_ids,
    user=actor,
    data={"polyp_count": 0, "salinity_psu": None},
)
```

These are not import applicators, bulk corrections, or an alternative permission
capability. They implement the existing interactive entry/correction rules.

## Boundary and authorization

The caller supplies an authenticated actor and the institution scope resolved by
the backend. HTTP authentication and active-institution selection remain with the
API; never pass institution IDs directly from untrusted input. The services
independently require an active writing membership for creation and apply the
existing administrator / technician correction rules. Superuser status alone
does not grant either measurement-write capability.

`data` accepts the ordinary field mapping. It may also be a zero-argument callable
returning that mapping, evaluated only after the initial scope and permission
checks. The API uses this to preserve permission/not-found precedence over body
parsing failures. A non-HTTP caller normally supplies a mapping.

Both creation and correction services keep the supplied institution scope
strict for every lookup. An empty scope cannot inspect Boxes or memberships,
parse input, or write data, even for a superuser.

Only the interactive POST adapter preserves the old read-only lookup fallback
when no institution is active. It reuses the shared permission and validation
helpers to retain legacy error precedence, then rejects without invoking a write
operation. This HTTP compatibility behavior is not a service capability.

Services raise `MeasurementNotFound`, `MeasurementWriteForbidden`,
`MeasurementWriteInvalid`, or `MeasurementWeekConflict` rather than returning an
HTTP response. The API maps these to its existing 404/403/400/409 shapes and uses
the unchanged output serializer for 201/200 responses. Unexpected database or
audit errors propagate after transaction rollback.

## Validation and scientific integrity

`apps.measurements.serializers.BiologicalMeasurementCreateSerializer` is the
single existing live-entry validation contract. Its old cultures import path is
retained as an alias. Required fields, model defaults, allowed values, decimal
precision, unknown-field handling and partial-update semantics are unchanged.

- Zero remains a measured value.
- Nullable salinity remains null when absent and may be corrected to null.
- Explicit `strobila_count=None` remains invalid for ordinary writes.
- An omitted strobila count keeps the existing live-entry creation default of
  zero; an omitted correction field preserves the existing value, including a
  historical null. This is not a rule for interpreting missing file cells.
- Creation never overwrites an occupied ISO week, even at the same date.
- Corrections may change date/week, but cannot enter another measurement's week.
- Inactive boxes reject creation after the weekly-conflict check. Authorized
  corrections to their existing historical measurements remain allowed.

A future historical applicator will need an explicitly reviewed validation and
attribution contract; do not bypass validation or silently reuse live defaults
for missing historical values. Existing historical and normalized importers have
not been switched to these services.

## Transactions, source order and audit

Both operations own an atomic write boundary and lock Box before measurement
rows. Creation rechecks its membership under that lock. Weekly-conflict detection,
measurement saving, scientific revision changes and audit insertion share the
transaction. Existing database uniqueness remains the final integrity defense.

The unchanged model `save()` protocol increments Box revision for every accepted
save, assigns source sequence only on creation, and preserves it on correction.
Corrections retain measurement ID and `created_at`. Even an empty correction
still saves, changes attribution, increments revision and appends an UPDATE with
an empty scientific diff, matching the existing endpoint.

Creation records ENTRY with `measurement_id` and `valeurs`. Correction records
UPDATE with `before`, `after`, `valeurs` and `modifications`; audit values and diff
keys are unchanged. The actor is both the measurement's current `user` and the
audit author. Prior audit events remain immutable. No original-observer / uploader
split is introduced here.

An audit failure rolls back the measurement, revision and any inserted audit.
No alerts are generated or synchronized. Accepted subculture snapshots,
allocations, lineage, locations and initialization sources are not rewritten.
An old correction cannot become a newer source merely by incrementing revision
or changing its biological date.

The audit-helper aliases in `apps.cultures.api_views` remain available to the
unchanged historical 2026 importer. Fault-injection tests for interactive writes
now patch the helpers in `apps.measurements.services`, where they execute.

## Future guarded corrections

The marked insertion point in `correct_biological_measurement` is after the
locked Box/measurement lookup, authorization, validation and weekly-conflict
check, but before the before-snapshot, field assignment, save or audit. Future
expected-value / reviewed-plan checks must compare against the freshly locked
row there and reject without mutation. No guard parameter, stale-plan behavior,
plan persistence or receipt is implemented in this foundation.

Any future guarded capability must explicitly decide its permissions, historical
null validation and observer/uploader/corrector attribution instead of widening
ordinary PATCH rules implicitly. Membership rows are not newly locked, and no
new disabled-account or institution policy is introduced by this extraction.

## Regression coverage

- `apps.measurements.test_write_services`: direct operations, zeros/nulls,
  validation, role/window restrictions, institution and box scope, date changes,
  weekly conflicts, inactive status reread, attribution and noop semantics,
  revision/order, exact audit values and rollback after audit insertion,
  subculture compatibility, and HTTP error precedence/shapes.
- `apps.measurements.tests` and `test_nullable_strobila`: existing API contracts.
- `apps.cultures.test_biological_measurement_concurrency`: PostgreSQL creation
  races plus direct concurrent partial corrections with contiguous audit
  snapshots and preserved source sequence. Accepted Low limitation: this test
  demonstrates serialization, not independent proof of Box-first lock order.
  The service's Box-before-measurement lookup order remains unchanged; validating
  lock order dynamically would require additional PostgreSQL instrumentation.
- `apps.cultures.test_quantitative_subculture_concurrency`: ordinary writes racing
  with subculture, stale subculture intentions and historical source retention.

Use `config.test_settings` for all local checks. PostgreSQL locking claims require
its disposable loopback QA opt-in, never a shared database.
