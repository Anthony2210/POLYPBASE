# Measurements and scientific integrity

## Exact-date CSV import

The import service in `measurements/import_apply.py` reuses the box lock,
`save()` for allocating sequences, and the weekly constraint, without applying
interactive-entry defaults. Migration `0010_measurement_import_receipt` allows
NULL for `culture_status`, `needs_attention`, and `notes` to preserve unknowns
in imported data. It does not rewrite existing rows. Routine entry keeps its
defaults and still rejects explicit NULL. The source, canonical states, plan
1.3, result, and audits are kept in an immutable private receipt in the same
transaction. `user` is the import actor; the original observer remains
NULL/not_recorded. No iso_week storage or conversion of precision is enabled.
Current contract: [`../measurement-csv-diagnostics.md`](imports-exports-transfers.md).
The current plan adds `duplicate_of_id` and `environment_source_box_id` to the record baselines, in addition to the precision and provenance fields introduced in version 1.2. The planner remains read-only; the separate import service is limited to exact dates. References below to a “future service that applies plans” and versions 1.1/1.2 describe earlier stages, not an absence of this restricted acceptance today.

## Zero invariant

`0` is a real scientific value. No record means no row; a missing optional value stays `null`. No serializer, filter, chart, import, export, or frontend fallback may replace `0` with a blank or turn a blank into `0`.

`BiologicalMeasurement.strobila_count` accepts `NULL`, meaning “not measured” for historical records whose source did not measure strobilae (2026 import). `0` remains a real value, and existing zeros are never rewritten. Routine entry is unchanged (default `0`; the API and form reject `null`). The dashboard total is `null` if there are records but no known values.

A `0/0` record therefore exists and is distinct from no record. Test both cases explicitly, including updates to zero and aggregation.

## Biological records

`BiologicalMeasurement` associates a box and a date with counts of polyps, ephyrae, and strobilae, optional salinity, a culture status, an attention flag, notes, and an author.

The database guarantees at most one **canonical record** per box and ISO week. The `unique_biological_measurement_per_box_week` constraint on `(box, week_start)` applies only to rows where `duplicate_of IS NULL` (migration `0014_measurement_canonical_evidence`). Duplicate evidence linked by `duplicate_of` is kept read-only and is not a second canonical record. The default, base, and reverse managers include it. Scientific readers must select `.canonical()`, including through `box.biological_measurements.canonical()`, before calculating, selecting the latest record, or exporting. This filtering must not hide evidence in audits or reviewed operations.

The interactive POST follows these rules:

1. Open a transaction and lock the box.
2. Create the canonical record and return `201` if the week's canonical slot is free.
3. Return `409 measurement_week_conflict` without mutation if a canonical record already exists in the week, including on the same date or for `0/0`.
4. Write the audit in the same transaction, without generating or synchronizing alerts.

Two concurrent creations are thus serialized under the same rule as two sequential operations: one final canonical record and a conflict for the second. The conditional database constraint on `(box, week_start)` remains the final protection against two canonical records. Keeping duplicate evidence does not weaken it. `save()` computes the key from the date. There is not yet a dedicated Monday/date CHECK for bulk or SQL paths that bypass it. These paths do not guarantee permission, auditing, or scientific consistency of the key. Correct an existing record only through the designated PATCH.

An inactive box rejects new records. An authorized user can correct an existing historical record. Check status under lock at write time, not just in the UI.

The `check_biological_measurement_duplicates` command runs a read-only diagnostic before a constraint is applied to an existing database. It groups by box and ISO week and lists the institution, box, ISO key/week, count, and affected record IDs and dates. It never chooses which scientific data to keep.

## Record observer and origin

The backend foundation distinguishes scientific attribution from the last actor to write the record:

- `user` keeps its historical contract: the creation actor, then the actor of the last ordinary correction. `user_identity` is unchanged; ENTRY/UPDATE audits identify their respective actors.
- `observer_status` is `known`, `not_recorded`, or `legacy_unestablished`. `original_observer` is an optional account reference, separate from `user`.
- `origin` is `direct_entry`, `file_import`, `box_creation`, or `legacy_unclassified`. `box_creation` (migration `0019_box_creation_initial_origin`, after `0018_box_1902_preservation`) explicitly identifies the initial polyp count entered when an ordinary box is created: observer `known`, precision `exact_date`, at most one per box (partial constraint), and never assigned to an imported or existing record. The UI shows “Départ” in “Voir détails” and “Nombre initial de polypes observé par” in the popover for this record only. `import_format` is blank outside file imports; only `csv` is currently defined. `imported_by` is an optional account reference, never an implicit observer.

The ordinary POST captures the authenticated account once as the known observer and sets the origin to `direct_entry`. PATCH preserves these fields, including for a legacy record or a deleted observer account. Provenance fields are not on the ordinary write allowlist. Client values are ignored, just as for the older non-editable identity fields. This foundation adds no observer picker, attribution endpoint, or service to apply imports.

Migration `0008_observer_origin` only adds fields and constraints. All existing records remain `legacy_unestablished` / `legacy_unclassified`, with null accounts and a blank format. The migration never copies `user`, rereads a source, or calls the current `save()`. Historical CSV/2026, v1 transfer, seed, and admin paths keep these defaults for new rows. Those paths do not automatically prove origin or observer. The fields are not editable in generated forms or admin.

New or replaced account references are checked against membership in the box's institution in `clean()` and `save()`. The ordinary service still requires an authorized active role. An unchanged historical reference does not block a correction if the membership has since disappeared. Technical writes through `QuerySet.update()`, `bulk_create()`, or SQL bypass this Python relationship check. Any future import service must therefore enforce institution scope and permissions explicitly.

### Read contract and identity retention

The existing serializer adds `observer_status`, `origin`, `import_format`, `original_observer_identity`, `original_observer_display`, `imported_by_identity`, and `imported_by_display`. Identities come from live accounts. Display reuses first name + uppercase last name, then falls back to email, never the technical username. No additional account ID or personal snapshot is exposed or stored.

Deleting an account sets its references to NULL without changing attribution status, origin, or scientific revision. `known` with a NULL reference means attribution was established but the account is no longer available, **not** that an external name was stored. Do not implicitly reclassify it as `not_recorded` or legacy. This foundation retains no name or email after deletion.

For the future frontend, `not_recorded` corresponds to “Observateur non renseigné” / “Observer not recorded”. The “Importé par” line is reserved for `origin=file_import` and uses the format actually stored. It is absent for `direct_entry`. Do not infer CSV origin from an old date or missing account. How to display `legacy_unestablished` and `known` without an account remains open; no frontend presentation is changed here.

Storing external observer names and snapshots of deleted accounts awaits an explicit product policy for personal data retention. No fake accounts are created. A service to apply imports and its reliable source metadata remain outside this foundation's scope. The existing planner keeps its versioned contract: its old `MeasurementBaseline.observer_id` still means `measurement.user_id`, not the new scientific observer. Changing that contract is a separate task.

## Time precision foundation (week-only storage not enabled)

`temporal_precision` is a non-editable backend field exposed read-only by the record serializer:

- `legacy_unverified`: a date is stored, but this foundation did not establish it as the actual observation date. This status proves neither an incorrect date nor week-only precision. It includes existing rows, even `direct_entry` rows, and technical creations or imports outside the ordinary service.
- `exact_date`: the ordinary creation service received an explicit, validated observation date. This is an entry contract, not independent verification that the date is scientifically accurate.

The additive migration `0009_temporal_precision`, after `0008_observer_origin`, sets the default `legacy_unverified` without inferring anything from weekday, origin, author, or observer. It uses no `RunPython` or calls to the current `save()`. Dates, ISO keys, IDs, counts, zeros, NULLs, sequences, timestamps, box revisions, audits, and transplant snapshots are preserved. Even a synthetic 2026 Monday is neither certified nor converted.

The ordinary POST still requires `measured_on` and sets `exact_date` on the server. The precision field is not on the write allowlist; client values are ignored as with provenance fields. A partial or empty PATCH, or one that explicitly corrects the date, preserves existing precision, observer, and origin. **Open product question:** is an explicit correction of a legacy date enough to establish an exact observation, and with what evidence and permissions? No implicit change in precision or conversion endpoint is added.

`measured_on` remains NOT NULL. This foundation does not change `week_start`, the ISO calculation in `save()`, scientific ordering, resolver priority, or audits. The current `(box, week_start)` uniqueness rule applies only to canonical records, as described above. The `measurement_temporal_precision_supported` constraint allows only the two values above, even outside Python validation: `iso_week` is rejected even with a fabricated Monday. This phase preserves how legacy dates are interpreted at runtime and does not resolve their uncertainty retroactively.

### First read changes for time precision compatibility

The shared `cultures.BiologicalMeasurementSerializer` now exposes `week_start` read-only, in the same `YYYY-MM-DD` ISO date format as `measured_on`. This Monday is solely an **ISO week key**, never a substitute observation day. `measured_on`, `temporal_precision`, scientific values, and existing identities and provenance stay unchanged. List, detail, record history, inventory, and dashboard responses that use this serializer get the additional field without extra related-object loading.

The serializer can already represent an unsaved object with `measured_on=None`: a record remains an object with an ID, values, and week key, distinct from `latest_measurement=null` (no record). This does **not** change input validation: POST and PATCH still reject an explicit null date. The `iso_week` test fixture is never saved.

Per-box reads (list/detail/inventory prefetches, record GETs, serializer fallbacks, and the latest recorded biological salinity) sort by `-week_start`, then `-created_at`, then `-pk`. For readers filtered with `.canonical()`, canonical `(box, week_start)` uniqueness and current key calculation guarantee the same latest record for current valid data, including at ISO year boundaries. Technical sort keys do not establish an observation chronology. `current_polyp_state` sources and their priorities, and snapshots, are unchanged.

The inventory's “sans relevé” filter, shared with bulk selection, uses a box-correlated `Exists` rather than checking if the latest record date is null. Zero is still present; an unknown strobila count stays `null`. Age and historical creation rules still use existing dates, with no new threshold or substitute Monday.

**Consumers that still need changes before any week-only storage:**

- `cultures/biological_timeline.py` still calls `measured_on.isoformat()` and sorts records and transplants together by effective date. Substituting Monday would impose an undecided within-week priority; this read path is intentionally unchanged.
- The dashboard sorts latest records **across multiple boxes** by date. Weekly uniqueness per box does not allow this sort to be replaced without changing the current within-week order.
- `cultures/polyp_state.py`, scientific resolution and transplant snapshots, Actions/audit, history/overview, forms/admin, and write services still require actual dates.
- Inventory age and the first record date for the historical cohort need an explicit rule for unknown dates. No age or biological creation date is inferred from `week_start`.
- Planner 1.1 keeps `MeasurementBaseline.measured_on` required and keeps its current fingerprint. The parser, classifications, and readiness are unchanged.
- Frontend types and consumers still expect non-null dates (history, inventory, entry, sorting, charts, labels). Exports and transfers remain to be examined separately.

These changes do not alter the schema, constraints, `save()`, or writes: `iso_week` still **cannot be stored**.

### Foundation for comparing scientific time intervals (not enabled)

`measurements/scientific_time.py` provides `ObservationTime`, an immutable, pure representation of precision, `measured_on`, and the `week_start` key, with ISO year/week. `from_measurement()` reads only these scalar fields, with no query or mutation when they are already loaded. Week-only objects used in tests are never saved. Inconsistent combinations are rejected without inferring precision.

`compare_operation_day()` expresses **the observation relative to the confirmed operation's local day** (not the reverse):

- Exact or legacy date: `before`, `after`, or `same_day`. The last preserves the existing date-based eligibility rule `measured_on >= operation_day` without proving within-day order.
- Week only: the possible interval runs from Monday through Sunday inclusive. `before` if its Sunday precedes the operation; `after` if its Monday follows the operation; otherwise `unresolved`, including at both ends.
- Unconfirmed occurrence: pass `None`; the result is `unresolved`. A legacy event date does not, by itself, confirm that the event occurred.

These bounds describe uncertainty, never an actual observation date. Counts (including zero), technical sequences, `created_at`, and import timestamps do not resolve an overlap. The comparison does not select a source: a chronologically later record must still meet the existing consumption and sequence rules. Corrections, immutable sequences, and snapshots stay unchanged.

**What blocks resolver integration:** `current_polyp_state` currently exposes either a count usable for a transplant or `None` when no eligible absolute source exists. It cannot distinguish a present observation whose chronology is unresolved. Filtering that observation out could wrongly make an older result authoritative. Returning its count would let it be spent; returning `None` alone would conflate ambiguity and absence. The transplant service, aggregates, and API and frontend consumers must adopt an explicit status before integration. No biological fallback is introduced here. The SQL resolver and its bounded prefetches stay unchanged.

Before allowing NULL dates, adapt filtering, evidence selection, and fallback together. The `measured_on >= operation_day` filter would exclude NULLs, and the current descending sort puts them first on PostgreSQL but last on SQLite. Explicit NULL placement is not a decision about scientific priority. A prefetch limited to one source must not discard evidence of ambiguity.

**Requirement for the future reverse guard:** if a week-only record already exists when a transplant is requested, reuse the interval comparison under the box lock, capturing the occurrence and local day once before any write or code reservation. The import path must check the same compatibility under that lock, not just in the planner. No new rejection is enabled. The product must decide how to handle an overlapping operation and its resolution workflow. An overlapping import already requires an exact date and cannot be forced.

Distinguish occurrence from quantitative source. A partial parent transplant has a documented occurrence but no absolute transition. A zero child allocation is known; a NULL allocation is not. A legacy event with no confirmed occurrence gives no certain operation date. No conflict in the planner's current filters therefore does not certify that all incomplete histories are safe. How to handle these histories conservatively before activation, and the contract for unresolved states and aggregates, still need decisions, without reconstructing history.

### Internal discovery of evidence for an observation (not integrated)

`cultures/scientific_evidence.py` provides `discover_observation_evidence(box=..., organization_id=..., observation=ObservationTime(...), source_sequence=None)`. The institution is the active context authorized by the calling backend. The function rechecks that the stored Box belongs to it and filters child relationships on both institutions. This is not a user permissions API.

The immutable result contains **factual summaries**, not an exhaustive set of events or a new stock state:

- Confirmed complete or partial parent operations, grouped by the time relationship of the observation to the operation.
- Known child initialization, including zero, or unknown allocation (`NULL`), from the explicit allocation relationship, never from similar codes.
- Legacy parent or child operations without a confirmed occurrence: unknown operation day and `unresolved` relationship, without using their `event_date`.
- Consumption barriers only for complete operations and known initializations that are confirmed. Each summary exposes their count, maximum barrier sequence, and number that consumed the supplied sequence (`source_sequence <= barrier_sequence`). The last stays `None` if the observation sequence is unknown. Zero only means none of these barriers consumed it, not that the source is eligible.

SQL partitions events using the `ObservationTime` bounds; returned relationships use `compare_operation_day()`. The exposed day bounds are for operations in the group, not a reconstructed observation day. Any week/operation overlap, including Monday and Sunday, remains chronologically unresolved regardless of consumption facts. A consumed ambiguity stays visible without blocking or choosing a newer authoritative source.

Discovery makes three queries: scope check, SQL parent aggregate, and one-to-one child allocation. It covers every event for the Box in at most seven parent groups and one child summary, without an arbitrary cutoff or loading records. Result volume and query count are bounded. Work for the aggregate remains proportional to that Box's history. Exhaustive test references are limited to small, isolated histories.

No calls are added to the resolver, serializers, totals, writes, or imports. This read alone provides no lock or concurrent snapshot guarantee. Future integration needs its own transactional contract and decisions on open resolution questions. Weekly constraints, corrections, sequences, snapshots, and the inability to store `iso_week` all remain unchanged.

### Internal resolution of a week-only proposal (not integrated)

`cultures/scientific_resolution.py` adds `resolve_week_observation_state(box=..., organization_id=..., measurement=...)`. It handles **one unsaved `iso_week` proposal** in a free week, compared with already stored dated sources. It reuses factual discovery and the existing date-based resolver. It does not reconstruct history or retroactively certify legacy occurrences. The supplied institution must still have been authorized by the server caller. No public consumer, aggregate, import, or write service calls it.

- An absolute count of zero is known. A proposed `NULL` count is invalid, not zero. Unknown child allocations do not become initializations.
- A consumed proposal never becomes eligible again. A later complete transition establishes an absolute after value; an observation entirely before the latest barrier does not replace it.
- An observation entirely after the latest barrier also needs a known, higher source sequence. An unknown sequence stays unknown, with `unresolved` and the internal reason `unknown_source_sequence`. No biological order is inferred from the sequence.
- A still-relevant overlap, including on Monday or Sunday, remains `unresolved`. Partial operations, unknown allocations, and legacy occurrences are not quantitative barriers. Overlaps and unknown occurrences in these cases are handled conservatively. This does not define a policy for accepting or rejecting writes.
- An eligible stored record from a strictly later week wins over the older proposal, even if it is ambiguous: the proposal cannot outrank that record under the existing biological priority rules. An older count is never selected by ignoring a still-relevant, unresolved newer proposal.

The internal contract remains `known / no_eligible_source / unresolved`. The public interface still rejects any unresolved state. The selected proposal has internal provenance `week_observation` with an ISO key, without inventing a day, ID, or timestamp. An occupied week is rejected as incompatible input to this hypothetical comparison, with no new write guard or replacement or correction rule.

Discovery takes three queries, the week check takes one, and the date-based fallback takes at most three: **at most seven queries**, regardless of the number of operations, or four if the fallback was already prefetched. SQL summaries stay bounded without truncation. The SQL cost of aggregation still depends on the Box's history. Public prefetches do not change. Objects and prefetched data must be fresh. The read guarantees neither a lock nor a concurrent snapshot. Do not call it in a loop over a list of boxes.

**Intentional limit:** no batch resolution of synthetic proposals and no general reconstruction of mixed history. Product policy for overlaps involving partial or unknown operations or legacy data, their resolution, and reverse guards still needs approval before integration. No schema, snapshot, audit, permission, API, or import service is changed. `iso_week` still cannot be stored.

### Exact requirements for enabling week-only storage in the future

A future `iso_week` row must have `measured_on=NULL` and an explicit ISO week key (`week_start`, with Monday used only as a key, never as an observation date). The display might read “Semaine 40 · 2026”, without inventing a day. This representation **cannot be stored** in this phase.

Before enabling it:

1. Change `measured_on` nullability, the precision choices, and the precision constraint. Enforce allowed exact/legacy/week combinations and validate ISO keys. Decouple `save()`/`week_start_for()` from a required date while preserving weekly uniqueness. Define precision transitions and their audit without inference or automatic historical conversion.
2. Integrate interval comparison after deciding the unresolved-state contract and the reverse guard. Implement scientific resolution for observations known only by week, particularly before or after a transplant within the same week. Adapt `cultures/polyp_state.py`, its queries and prefetches, and transplant calculations and snapshots without imposing arbitrary priority.
3. Complete the first read changes described above. Corrections, history/timeline, cross-box sorting, inventory freshness and historical creation, Actions/audit, and forms/admin still use a required date. The additive contract and latest-record ordering per box do not suffice to enable storage.
4. Adapt frontend types and consumers (entry, dates, sorting, charts, inventory, labels) and exports (periods, historical location, daily temperature). Define rules for weeks that overlap a period boundary or a movement rather than choosing Monday.
5. Version the planner baselines and fingerprints if needed. The baseline at this stage requires a date and does not include precision. Define legacy/exact/week comparisons and an authorized, institution-scoped, locked, atomic, audited service to apply imports that preserves missing/NULL/0 and Phase 4A provenance. Review historical imports and transfers separately without reinterpreting their existing conventions.
6. Validate migrations, constraints, transactions, and concurrency on isolated disposable PostgreSQL, then validate consumers and time boundaries. Remove the `week_precision_persistence_required` planner block only when everything is ready.

The foundation and first read changes do not meet these remaining requirements. No changes to the UI, charts, exports, or historical imports are enabled.

## Current state and quantitative transplants

`latest_measurement` remains the latest ordinary record and retains its freshness and ISO week rules. `current_polyp_state` is separate: the server resolves an absolute observation, the absolute result of a parent transplant, or a child's absolute initial allocation. It never subtracts all historical allocations from the latest record.

A quantitative transplant creates no `BiologicalMeasurement` and no fictitious ephyra or strobila count. It can coexist with an ordinary record in the same week for both parent and children. Ordinary writes and transplants share the box lock and a source sequence. Opaque revisions invalidate stale intents. Correcting an old record does not rewrite the snapshot or allocations of an accepted transplant. Typed history distinguishes editable records from non-editable transplant events.

A partial transplant (at least one child allocation `null` or omitted) keeps the known before value but has `null` for total and after. Its revision invalidates intents without a scientific transition. All resolver paths, including record-priority subqueries and prefetches, ignore these events as parent sources and unknown allocations as child initializations. History keeps the partial event without a quantitative after point. A child with an unknown allocation gets no invented initialization entry. Zero remains a known initialization. Weekly and correction rules stay unchanged.

See [`boxes-lifecycle-locations.md`](boxes-lifecycle.md) and [`../../backend/QUANTITATIVE_SUBCULTURE.md`](annexes/quantitative-subculture.md) for transplant and resolver details. The proposed order of remaining stages (explicit contract, guard, consumers, schema, planner, then services that apply plans) and review evidence are tracked in [`../../POLYPBASE_CURRENT_STATE.md`](../archive/etat-historique-2026-10-14_2026-11-16.md), without enabling any undecided business rule.

## Temperatures

`TemperatureMeasurement` stores an individual timestamped measurement from a `Probe`. It is unique by probe and timestamp. `DailyTemperature` stores a daily aggregate for a zone and is unique by `(thermal_zone, date)`.

Manual entry adds a value to the daily aggregate. It does not create an individual row per entry. The API locks the `ThermalZone`, an existing synchronization point even before the first aggregate, then locks the `DailyTemperature` row if it exists. Two concurrent first entries or two concurrent updates are therefore serialized.

Mean, minimum, maximum, and count must represent all accepted entries. The aggregate and audit are in the same transaction. An audit error rolls back the operation. Do not change precision rules or turn entries into individual history without a business decision.

The models accept several probe types. Their presence does not prove that automatic ingestion via a connected service works; check the routes and services. See the [iMinilide contract](#iminilide-probe-collection-october-29-2026-decision) below.

### Four-hour thermal monitoring (decision `thermal-control-4h`, November 17, 2026)

The zone detail page replaces the numeric summary with the existing `ProbeSeriesChart` engine, with a fixed window and three metrics: mean, difference from setpoint, and minimum / maximum. The historical `Mesures des sondes` chart and its controls remain unchanged. This decision replaces the zone detail presentation described below, not the `thermal-control` contract used elsewhere.

- `GET /api/thermal-zones/<id>/thermal-monitoring/` sets inclusive bounds `[timezone.now() − 4 h, timezone.now()]` on the server. The zone and its probes are filtered by the active institution. All probe types and inactive probe history are allowed, as with historical retention. One read of `TemperatureMeasurement` supplies both curves and metrics. No daily aggregates or condensed buckets are read, and there are no writes, migrations, or reconstruction. Future observations and those before the start are excluded. Crossing midnight does not change the calculation.
- For each contributing probe i, `m_i = sum of observations from i in the window / number of those observations`; the `Mean = sum(m_i) / number of contributing probes` metric applies the existing equal-weight rule for probes to the whole period. A probe that reports more often does not get more weight. One probe is enough; probes with no observations do not contribute. Irregular sampling can still bias a probe's own mean. This is neither a time-weighted mean nor an estimate of unobserved intervals.
- The difference is the same unrounded mean minus the setpoint, with its sign. Without a setpoint, it is `null`. The extremes are the actual minimum and maximum observations across all probes in the same window. Calculations use Decimal, are rounded to two decimal places for the API (`ROUND_HALF_UP`), then displayed to one decimal place using the existing format. Independent display rounding can therefore produce a different result from subtracting the displayed values. Zero and negative values are preserved. Missing means `null`, never zero.
- The mean curve keeps the historical convention, distinct from the period metric: for each UTC minute, average the per-probe means with equal weight, and only if at least two probes contribute in that minute. Individual curves keep their actual timestamps. There is no artificial synchronization or interpolation; the engine still breaks curves at gaps greater than three sampling intervals. Hiding a curve in the legend does not recalculate the server mean or metrics.
- The frontend reads only this `no-store` endpoint and does not reuse the historical chart cache. One request is in flight at a time. It polls 30 seconds after completion, pauses when the page is hidden, and reads again on return or focus. A new read clears the previous snapshot. Loading and error states do not show stale metrics. The exact bounds and browser time zone are in the chart's accessible name. The visible period appears only once, in the heading.
- With no raw observations in the window, including for a manual-only zone, the empty state reports that fact and metrics remain absent. Condensed data alone is not presented as individual observations. An older measurement still within the four hours remains at its actual time and is never extended to now. Late arrivals are selected by `measured_at`, not by import time.

Regression coverage: `backend/apps/measurements/test_thermal_monitoring.py`, `frontend/scripts/test-thermal-monitoring-refresh.mjs`, `test-thermal-control-rendering.mjs`, and `test-zone-detail.mjs`. No browser QA is claimed; it is left to the user before independent review.

### Recent thermal control (explicit decision `thermal-last-reading`, November 4, 2026)

Recent thermal control is separate from daily statistics. It uses the zone's active probes belonging to the same institution, regardless of probe type, without requiring configured iMinilide collection.

- For each probe, select the latest reading by the actual measurement time: `TemperatureMeasurement.measured_at` for raw data, `ProbeTemperatureBucket.last_measured_at` for condensed buckets. Neither import order nor a bucket's theoretical start or end is a measurement time. A latest reading in the future is excluded without falling back to an earlier one.
- The server evaluates freshness. Age must be between zero and `3 × Probe.sampling_interval_seconds`, inclusive. The interval is stored per probe, defaults to 60 seconds, and accepts only 60, 600, or 900 seconds. Configure it with `backend/apps/measurements/management/commands/set_probe_sampling_interval.py`: it requires an active Django superuser, explicit institution and zone, the exact ID and code of one probe, and a reason. It simulates by default and writes with `--apply`. Locks are taken in institution → zone → probe order. The change and before/after audit share a transaction, with no audit if nothing changes. Procedure: [`../sondes/probe_sampling_interval.md`](#per-probe-sampling-interval). No product privilege, dedicated API, or admin control is added. A changed interval takes effect at the next evaluation; this field does not reconfigure cloud collection.
- Each included probe contributes exactly one latest known temperature, with equal weight in the arithmetic mean. There is no weighting by number of measurements or averaging of bucket means. `0` and negative temperatures remain measurements. An invalid interval, no reading, or a latest reading that is future, stale, or unknown does not contribute. If no probe is included, the temperature is `null`, never zero.
- Condensation now keeps `last_temperature_c` with its exact `last_measured_at`. During a merge, only a later time replaces the pair. Incompatible values at the same time make the value unknown. The same caution applies to conflicts between raw data and buckets or between buckets at the latest time. A historical `null` stays unknown. Do not reconstruct it from a mean, minimum, maximum, or older reading.
- `GET /api/thermal-zones/<id>/thermal-control/` returns an uncached summary authorized in the active institution. `ThermalZoneSerializer.thermal_control` exposes the same contract, including in the zone list. It includes `evaluated_at`, `temperature_c`, the number of included probes, their earliest and latest measurement times (`oldest_measured_at`, `newest_measured_at`), the earliest expiry (`expires_at`), and exclusion counts. This is a read-only calculation and does not rewrite aggregates.
- The frontend keeps only one request in flight. It schedules the next poll 30 seconds after the request finishes, never while waiting for it. Focus, visibility restored, and expiry triggers share an in-flight request without cancelling it. Only a change of zone or institution context, or unmounting the component, cancels the request and invalidates the snapshot. A snapshot already expired on arrival is discarded. Repeated expired responses trigger retries after 1, 2, 4, 8, then at most 15 seconds. A valid response or an error resets this progression. After an error, the normal 30-second delay applies. The displayed mean is conservatively cleared at the earliest expiry plus 1 ms, and a refresh is requested unless one is already in flight. The frontend does not recalculate a partial mean from remaining probes. Request round-trip time is deducted from the validity period; the local clock cannot extend server freshness. A failure removes the snapshot and displays an error, without falling back to the list summary, an old reading, or a daily mean.

The daily mean and its difference from setpoint (`mean − setpoint`) remain unchanged. Their date is displayed separately from the range of times measured by probes included in the recent mean. Do not attach a recent measurement timestamp to daily statistics or show a daily aggregate as a current temperature. The FR/EN keys `temperatureRecentMean`, `temperatureNoRecent`, `temperatureRecentPeriod`, `temperatureRecentError`, `temperatureDailyPeriod`, `temperatureDailyMean`, and `temperatureDailyGap` distinguish these time references (application register, batch `B13-2026-11-04`). No historical Japanese is changed.

Targeted regression coverage: `backend/apps/measurements/test_thermal_control.py`, `backend/apps/measurements/test_probe_persistence.py`, and `frontend/scripts/test-thermal-control-rendering.mjs`.

### iMinilide probe collection (October 29, 2026 decision)

Only iMinilide ingestion is implemented. `collect_iminilide` collects from the iminilide.com cloud for a `Probe` of type `iminilide` with `external_serial` and `external_channel` set (service `probe_collection.py`, client `iminilide.py`). Operating procedure: [`../sondes/collecte_iminilide.md`](../operations/deployment-operations.md#exploitation-des-sondes-iminilide).

- Each minute reading is a `TemperatureMeasurement` (`source="iminilide"`). Uniqueness by probe and timestamp prevents duplicates. A missing value is skipped and never converted to `0`; `0` remains a measurement. A value outside the manufacturer's range (-100 to 250 °C) is skipped and counted.
- Cloud timestamps are local times without offsets. Response order disambiguates the repeated hour when clocks go back. Ambiguous timestamps without an observable jump backward are skipped and counted, never guessed.
- `DailyTemperature.computed_from_probes = True` marks a day recalculated from all measurements from the zone's collected probes (G and D together). Collection never rewrites a day with a manual or historical value (`False`). Manual entry on a probe-derived day sets it back to `False` and freezes it. Recalculation locks the zone and then the day, in the same order as manual entry.
- After `PROBE_READING_RETENTION_DAYS` (14 days), minute readings are condensed into `ProbeTemperatureBucket` (min, mean, max, count, and exact sum per 15 minutes), then deleted. Condensation is intentional and irreversible. Daily aggregates account for buckets. The required `last_measured_at` field preserves the exact latest measurement time, including during merges. A bucket's theoretical start or end never replaces it. `last_temperature_c` keeps the temperature at that instant if known. Historical buckets stay `null` without reconstruction (see the recent thermal control contract above).
- A zone with an active collected probe blocks manual entry only when a latest reading exists and its age is at most `PROBE_MANUAL_FALLBACK_HOURS` (6 hours by default). With no stored reading or after the threshold is strictly exceeded, authorized users can use manual fallback. After checking permissions, the API rechecks this under the zone lock and returns `409` when manual entry is unavailable. `ThermalZoneSerializer.probe_monitoring` exposes this state.
- `GET /api/thermal-zones/<id>/probe-readings/?start=&end=` returns series per probe in the active institution: minute resolution up to 2 days, 15-minute resolution up to 31 days, and hourly beyond that (366 days maximum), with `sampling_interval_seconds` per probe. The read takes no lock. Both storage tiers are read in a single SQL statement and thus a single snapshot, so no reading is duplicated or missed during condensation. The calculation of historical bounds (`first_reading_at`, `last_reading_at`) was removed from this response. `group_sources=1` keeps the same resolution but merges each condensed group into one source (exact sum, count, min, max, and latest time). No individual raw reading is sent at resolutions coarser than one minute. On PostgreSQL, the same SQL statement groups the minute readings (`GROUP BY` on the epoch-aligned UTC period, exact `SUM`/`COUNT`/`MIN`/`MAX` in `numeric`, and `MAX(measured_at)`), then merges them with whole buckets as before. The response matches the Python reference (`test_probe_aggregation.py`), with one row per period and probe rather than per reading. Display resolution is the least common multiple of the requested resolution and the resolutions of stored buckets read. A bucket aligned to the epoch at its own resolution therefore fits wholly within one period. It is never split or assigned based only on its start period. There is no second read, so concurrent condensation cannot mix two snapshots. SQLite and ungrouped responses keep the Python path. SQLite does not validate this PostgreSQL variant.
- The location detail page shows a recent 6-hour trend and a combined chart defaulting to 24 hours directly, without a modal (`ZoneProbeCharts.tsx`, `ProbeSeriesChart.tsx`, `utils/probeSeries.ts`). Each probe keeps its own measurement times; the cross-probe mean is described below, with no interpolation or time synchronization. A minute-resolution response plots actual measurements and breaks the curve at gaps greater than 3 × the probe interval. This is an unapproved display assumption, separate from latest-reading freshness. The old chart convention broke the curve at gaps greater than one resolution interval. The 6-hour trend window is also a display choice still to be confirmed. On opening, the 24-hour response also feeds the trend and probe status; each then queries the server separately. A condensed response plots buckets (mean, min–max band), never invented times. The trend and difference from setpoint stay neutral. No thermal tolerance is defined (`ThermalAnomaly.max_deviation_c` describes a recorded anomaly, not a threshold). All curves are solid (8 colors, then a hollow latest-reading marker for probes 9 and 10). The setpoint is a solid line labeled above the plot. A segmented control selects the period in one click (24 h, 7 jours, 30 jours, dates précises). The exact-dates choice opens a small anchored popover (`useAnchoredPopover`), with focus on the panel rather than an input. The exact bounds and browser time zone remain in the control's accessible name and `title`. No text about time zones, condensed or partial buckets, or a partial preview appears below the chart (November 11, 2026 decision). The hover tooltip stays simple: a short time line, then each curve's name and value (bucket mean or reading), and for a bucket, the min – max range. It no longer shows the difference from setpoint (the setpoint stays labeled above the plot). Screen reader announcements keep the full detail. Thermal control no longer displays “Aucune mesure récente” or the silent-probe badge and description (November 10, 2026 decision). No recent mean, the time and age of the last reading, the chart's empty state, and loading errors remain distinct. The server still decides whether manual fallback is available.
- **Cross-probe mean (November 10, 2026 product decision).** The server returns `probe_average`, one mean per period, with equal weight for each contributing probe. For each period P and probe i, `mean_i(P) = sum of valid observations from i in P / their count` (a stored bucket contributes its exact sum and count, never its rounded mean). `zone_mean(P) = mean of mean_i(P)` over contributing probes. A point is shown only when at least two distinct probes contribute. `0` and negative values are measurements; missing data is never zero and nothing is interpolated. Periods use the same resolution and bounds as the series. A partial period includes only readings within the request, and a stored bucket remains whole. The frontend plots a thick, solid mean curve in a color made by blending probe colors in OKLab (darkened for readability, `averageSeriesColor`), keeps individual probe curves and the setpoint, and never recalculates the mean. Since November 11, 2026 (product decision), neither the legend nor the visible tooltip shows the contributing probe count or average period bounds. Only the screen reader announcement keeps them. Contract and tests: `test_probe_average.py`, `test_probe_snapshot.py`.
- Automatic collection does not create an `AuditLog` entry per reading. Linking a probe (`link_iminilide_probe`) is audited.

#### Historical assignment and replacement

A `Probe` with at least one `TemperatureMeasurement` or `ProbeTemperatureBucket`
can no longer change thermal zones. `clean()`, `save()`, and the linking command
reject the move. Code, placement, and active status remain editable. A probe
without history can still change zones. Do not bypass these checks with bulk or
SQL writes.

An external channel is unique only for its active probe. Several inactive
versions can coexist. The code is unique per institution across all probes,
active or not. The database constraint prevents reactivating an old version while
another is active on the channel.

`link_iminilide_probe --replace` deactivates the old active probe and creates a
new probe with a new unique code in the same institution. Historical minute
readings, buckets, and aggregates stay in the old zone, without copying or
reassignment. The old probe's `UPDATE` audit (`replacement_probe_id`) and the
new probe's `CREATION` audit (`replaced_probe_id`) share the replacement
transaction. An audit failure or code collision rolls back everything.

With no active probe, `--replace` uses the latest inactive one as the predecessor.
Normal mode does not reactivate an inactive probe with history in order to move
it; it creates a new probe. The command cannot reuse a channel from another
institution, even if inactive, or a zone from another institution.

#### Per-channel cutoff when resuming collection

`collection_start()` uses the latest time across the channel's minute readings
and bucket `last_measured_at` values, including from older probes, within the
cloud catch-up window. The lookback needed when clocks go back never permits
historical data to be imported into another probe. `store_readings()` strictly
excludes any time at or before the historical maximum of the **other** probes on
the channel, regardless of the requested window or what the cloud returns.
Duplicates for the current probe keep their usual handling, including repair of
an incomplete daily aggregate.

The cutoff remains exact after condensation, multiple replacements, or a
deactivation followed by manual creation on the same channel. The minute after
the last reading can arrive in the new zone even before the end of the old
bucket. A latest temperature of `0` is real and establishes the same cutoff.
Other channels do not contribute to this maximum.

Regression coverage for the model, command, audits, rollback, strict resumption,
and condensation is in `backend/apps/measurements/test_probe_replacement.py`.
PostgreSQL concurrency tests remain in their dedicated module.

## Factual presentation and removal of alerts

Alerts are no longer active. Counts, temperatures, aggregates, and setpoint differences remain measured or calculated facts, without inferred operational or biological warnings. A drop in polyps, a zero value, missing salinity, or a temperature difference does not trigger an alert. A setpoint is not an alert threshold.

Dropping alerts does not remove entry validation, permissions, or auditing of records and measurements. `AuditLog` and Actions are preserved. Historical `Alert` storage remains dormant, including its migrations and data, pending a separate, explicitly authorized, non-destructive decision. See [`product-architecture.md`](../archive/sources-fusion/context/product-architecture.md).

## Observations and notes

`Observation` is a dated, typed model with notes and an author. It can be managed in Django admin and is used in some demo data and tests, but no dedicated DRF route is currently declared. The notes on a biological record are not `Observation` objects.

Do not display or filter a “latest observation” on the assumption that the API supplies it. Check the actual workflow first. In particular, the Inventory must not use this model as an indicator without a dedicated feature.

## Transactional integrity

For any record or measurement write, examine these together:

- Target object and date.
- Permission and the institution of the box, zone, or probe.
- Scientific validation.
- Uniqueness rules and database constraints.
- Locked objects and lock order.
- Permitted side effects, without generating or synchronizing alerts.
- `AuditLog` and the API response.
- Rollback on failure.

A constraint protects the final state but does not by itself provide a consistent API response. A lock does not replace a constraint when the invariant must hold outside the endpoint. Reproduce concurrency findings on isolated QA PostgreSQL under the relevant engine.

## Tests to preserve

- Positive values, `0`, `0/0`, absence, and updates to zero.
- Creation followed by correction on the same day.
- Direct constraint enforcement outside the API.
- Concurrent operations on PostgreSQL.
- Rejection of objects from another institution.
- Box status allowing or blocking a write.
- Rollback if writing the audit fails.
- No alert generation, synchronization, or resolution during record creation and correction or temperature entry.
- Preservation of historical `Alert` data and `AuditLog` entries without treating them as active alerts.
- Accuracy of temperature mean, min, max, and count.

## Entry points

- Models: `backend/apps/measurements/models.py`.
- Endpoints and serializers used by boxes: `backend/apps/cultures/api_views.py`, `backend/apps/cultures/serializers.py`.
- Concurrency tests: `test_*_concurrency.py` modules in `backend/apps/cultures/`.
- Display and entry: box detail and chart components in `frontend/src/components/`.
- Import/export contract: [`imports-exports.md`](imports-exports-transfers.md).

## Write services

Shared services validate the actor, box, and institution, lock the box before the record, and write the audit in the same transaction. A correction does not replace the observer with the person making the correction or turn legacy precision into a certified date. A POST for an occupied week rejects the operation rather than correcting implicitly. Do not apply this contract to historical importers.

## Per-probe sampling interval

`sampling_interval_seconds` is the reference sampling interval for each probe. It is separate from the timer frequency, condensed bucket width, and chart window. It helps interpret spacing in the series without creating points. See [iMinilide operations](../operations/deployment-operations.md#exploitation-des-sondes-iminilide) for configuration.

The dedicated command accepts only 60, 600, or 900 seconds. It requires an active Django superuser, consistent institution, zone, and probe IDs, the exact probe code, and a reason. Without `--apply` it simulates. With authorization and `--apply`, it changes only the interval and audits the change atomically. An identical value causes no writes. It does not change the hardware sampling interval, existing readings, or active status of the probe.
