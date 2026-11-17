# Historical preview HTTP API

This API exposes the reviewed Phase 3 service as a **read-only simulation**. It does not approve, save, publish or apply corrections. The existing history display, inline movement correction and external sharing contracts are unchanged.

## Route and authorization

Named route: `api_box_history_preview`.

- `GET /api/boxes/<box_id>/history/preview/?start=<aware ISO8601>&end=<aware ISO8601>`: initial effective history, with identical before/after states.
- `POST /api/boxes/<box_id>/history/preview/`: simulate one complete draft batch; JSON only, no query parameters.
- `HEAD` follows GET; `OPTIONS` provides standard DRF metadata. PUT, PATCH and DELETE are not supported.

An authenticated, active user must have an active **Admin membership** in the explicitly selected, active institution (`X-Organization-Id`). Superuser status alone grants nothing. The HTTP layer checks membership before the handler; Phase 3 reloads the user and checks authorization again inside its source snapshot. Missing, merged and foreign Boxes return the same 404. Targets and zones are resolved only against scoped, freshly loaded evidence.

Normal Django session authentication and CSRF protections apply to POST. Responses, including view errors, carry `Cache-Control: private, no-store` and vary on `Cookie` and `X-Organization-Id`. Nothing caches or serializes a prepared session as an authorization/freshness token.

## Request

```json
{
  "start": "2026-02-09T00:00:00+01:00",
  "end": "2026-02-16T00:00:00+01:00",
  "operations": [
    {
      "type": "adjust_event_zones",
      "target": {"key": "movement:42"},
      "destination_zone_id": 7
    }
  ],
  "justifications": [
    {"operation_index": 0, "reason": "Reason supported by laboratory evidence"}
  ]
}
```

`start` and `end` are required, explicit-offset ISO8601 timestamps. The thermal window is half-open `[start, end)`; the chronology still includes the all-time evidence required by Phase 3. Dates without an offset, invalid calendar dates, reversed/empty windows and unrepresentable local-calendar boundaries are refused. Explicit offsets designate instants, not assertions that a wall time exists in Europe/Paris. Repeated DST wall times with different offsets remain distinct.

`operations` and `justifications` default to empty lists. All object levels reject unknown fields. Integer IDs do not accept strings or booleans. Client-supplied actor, institution, Box identity, anchors, source snapshots and sessions are not accepted.

Supported operations map directly to the reviewed typed operations:

| `type` | Fields besides `type` |
| --- | --- |
| `modify_event` | `target`; optional `kind`, `temporal`, `origin_zone_id`, `destination_zone_id` |
| `adjust_event_kind` | `target`, `kind` |
| `adjust_event_temporal` | `target`, `temporal` |
| `adjust_event_zones` | `target`; optional `origin_zone_id`, `destination_zone_id` |
| `insert_event` | `draft_id`, `kind`, `temporal`; optional `origin_zone_id`, `destination_zone_id` |

A target contains `key` and optional `expected_evidence_fingerprint`. Use the returned identity and fingerprint for derived events; these are evidence checks, not authorization or snapshot capabilities. Persisted keys use their typed identity; derived identities retain their explicitly reported stability.

Omitted change fields remain `UNSET`; an explicit zone `null` means clearing that zone, subject to the engine's shape validation. No-op operations, unsupported draft kinds and invalid references are rejected by Phase 3, not repaired by HTTP code.

A temporal object has required `precision` and the actual Phase 3 fields: `instant`, `iso_year`, `iso_week`, `not_before`, `before`, `ordering_anchor`. Fields not applicable to that precision must be absent/null as required by Phase 3. Supported precisions are `exact`, `recorded_boundary`, `iso_week`, `observation_interval`, `unknown`. ISO-week bounds must match the Europe/Paris week. An ordering anchor is only a sequence convention, never a fabricated occurrence time.

Justifications contain `operation_index` and `reason`. Every insertion requires its indexed justification. Duplicate/out-of-range justifications are refused. A justification is provenance, not approval.

## Response

The response serializes the actual `HistoryPreview` result through explicit reviewed type/field allowlists:

- `mode: "simulation"`, `organization_id`, `box_id`;
- `before` / `after`, each with `history`, `thermal`, `safety`;
- event, interval, entry/exit side, thermal attribution, stay and missing-data gap changes;
- `conflict_changes`, `affected_event_keys`, `justifications`.

Events retain `identity`, kind, origin, temporal precision/bounds, origin/destination zones, original source event, effective status and applied operation indexes. Histories include placed/unplaced events, location intervals, entry/exit sides, conflicts, rejections, anchor resolutions and source findings. Thermal output includes evidence identities and references, measured/setpoint source categories, original numeric values, complete/partial/possible attributions, whole-source statistics, overlap warnings, provisional/uncertainty reasons, unassigned evidence and missing-data gaps. No interpolation or continuous curve is inferred.

Decimals are JSON strings, preserving their exact values. Zero, negative numbers and null remain distinct. Datetimes use ISO8601 with offsets; uncertain temporal representations retain a null `instant`. Evidence references are scoped identifiers, not raw audit records, external channel credentials, files or source snapshots.

Two intentional provenance protections are explicit in the response:

- Raw probe provider labels are free text in storage. The existing known `iminilide` label and empty labels are retained; other labels return `basis: null`, `basis_withheld: true`. Source type, row ID, probe ID and scientific source category remain available.
- Diagnostic raw source-cell strings (`source_values`) are not exposed. Findings list `withheld_detail_keys` while retaining reading/evidence IDs, scientific values and reasons. This is provenance redaction, not history/result truncation.

Conflict evolution names are the Phase 3 names, without invented severity or approval semantics: `introduced`, `aggravated`, `changed`, `preexisting_affected`, `preexisting_independent`, `not_reported_after`. The latter means no longer reported by the simulated engine, **not** a certified historical resolution. `safety.publication_evaluated` remains false.

## Errors and resource protection

- 400 `invalid_preview_input`: strict serializer errors in `errors`, without reflecting arbitrary unknown keys or submitted values.
- 400 `invalid_json`: malformed JSON, without raw parser details.
- 400 Phase 3 machine code: `rejected_operations` with only `index` and `code`, plus `operation_index` when available. A rejected batch has **no partial before/after simulation**.
- 403: missing/unauthorized institution, inactive actor/membership, non-Admin, or normal CSRF failure.
- 404 `box_not_found`: missing, merged or foreign Box, indistinguishably.
- 413 `request_too_large`: Django's configured body-size budget exceeded.
- 415: unsupported content type; 405: unsupported method.

The bounded JSON parser uses Django's `DATA_UPLOAD_MAX_MEMORY_SIZE` check even when session CSRF causes parsing before the handler. It does not replace existing infrastructure limits. The checked-in Nginx body limit and Gunicorn timeout remain unchanged; no global DRF throttles are configured, and this API does not add a new rate-limit policy or disable inherited DRF protections.

All reviewed Phase 3 budgets remain authoritative: 366 elapsed days, 100 operations, 100 justifications and their existing key/text bounds; 5,000 history rows, 10,000 reference rows, 50,000 thermal evidence rows; event comparison, attribution and overlap-expansion budgets. Limit failures return explicit service codes, never truncated results. These budgets are not a hard SQL runtime or serialized-byte-size guarantee.

## Read-only guarantees and validation

One service call prepares one immutable original source snapshot and compares the draft against that same snapshot. No projection, conflict matching, source deduplication, lifecycle interpretation or thermal attribution is rebuilt in the HTTP layer.

The language middleware uses its existing read-only diagnostic pattern for this exact route, avoiding preference creation and session-language writes. Its ordinary response language cookie remains unchanged. HTTP tests capture SELECT-only SQL and compare all managed model rows across successful and failing requests, including a real session/CSRF path. No migration or confirmation endpoint is added.

Local validation used explicitly isolated SQLite only:

- 286 tests: 33 HTTP tests plus all seven Phase 1–3 modules.
- 444 regression tests: history audit lookup, historical transitions/movements, Box and zone temperature/history APIs, inline correction, organization scoping, general cultures/admin APIs and account permissions.
- Django system check, migration drift check and whitespace checks.

No explicitly authorized disposable PostgreSQL QA environment was established. No PostgreSQL, Neon, production or shared database was accessed, and no database was automatically created/destroyed outside Django's in-memory SQLite test lifecycle. PostgreSQL snapshot, concurrent membership/source changes, read-only transaction enforcement and real query plans remain unvalidated. The foundation promises PostgreSQL `REPEATABLE READ, READ ONLY` only when its wrapper owns the outermost transaction; inherited transactions and SQLite do not gain that guarantee.

Before persistence, independent product/security decisions are still required for correction storage, durable reconstruction identities, freshness/concurrency checks, approval/publication policy, audit semantics and atomic application to historical versus operational state. This API decides none of them.
