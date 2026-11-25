# Historical movements: approved adjacent-week protocol

This annex describes the current read-only planner and history projection. The
former reconciliation command and acceptance service have been removed; there
is no write path here for historical reconstruction. Prior procedures and dated
validation results are retained in the [pre-removal annex](https://github.com/Anthony2210/POLYPBASE/blob/9e00e2c6e0eb581334f9238ce041a7d3d2fafb19/docs/reference/annexes/historical-movements.md).

## Approved evidence and week attribution

The product decision establishes persisted `BoxLocation` period identities as
zone evidence when the known week-only import convention and full-week coverage
are satisfied. Biology does not need a separate, nonexistent source-zone field.
This replaces both earlier policies: assigning the destination boundary week,
and universally blocking because independent source-zone provenance is absent.

For origin observation week **W**, followed by destination observation week
**W+1**, the reconstructed movement is attributed to **W**. For example:

```text
Origin period A: starts 2025-12-15, ends at the 2025-12-29 week boundary
Origin canonical measurement: 2025-12-22 -> 2025-W52
Destination period B: starts 2025-12-29 and covers 2026-W01 completely
Destination canonical measurement: 2025-12-29 -> 2026-W01
Reconstructed movement: 2025-W52, A -> B, occurred_at = null
```

The stored period boundary remains raw source evidence. It is not a certified
physical occurrence timestamp. An existing exact `BoxMovement` at that boundary
retains its actual timestamp and its derived **2026-W01** key. Nothing is shifted
to the preceding week in `BoxMovement`, and no period link is forged. The separate
week-only reconstruction owns **2025-W52** under the approved attribution rule.
`accepted_at` is the later review/recording time, not either occurrence time.

Evidence provenance/basis is:

`persisted_location_periods_and_canonical_measurement_weeks`

Evidence records `policy`, `source_location_provenance`, both period IDs,
canonical measurement IDs/weeks, `attributed_week_start`,
`destination_week_start`, `stored_period_boundary`, represented/conflicting
movement IDs and lifecycle audit IDs. `source_location_provenance` is
`established_by_period_identity` when source checks succeed. The flags
`physical_occurrence_proven`, `exact_time_proven` and `period_pairing_reliable`
remain false: weekly reconstruction does not certify an exact physical instant
or justify suppressing period evidence in the UI.

## Deterministic read-only planner

`plan_historical_movements(organization_id=..., box_ids=[...])` requires an exact,
nonempty selection entirely within one institution, excluding merged identities.
It grants no user permission itself. It reads only: no locks, saves, period links,
audits, import calls or corrections. Outermost PostgreSQL reads use the existing
repeatable-read read-only helper; nested calls retain their caller's isolation.

An eligible transition requires:

- different local zones, same-box periods, no malformed or overlapping periods;
- contiguous periods with a known origin end equal to the destination start;
- Paris ISO-Monday midnight boundary/start values consistent with the known
  weekly import convention;
- the origin covering the whole W week, and the destination covering the whole
  W+1 week; a known destination end must be week-aligned and at least the next
  Monday (equality is sufficient); unknown ends remain blocked;
- exactly one canonical measurement in each adjacent week, with a consistent
  stored ISO key and unambiguous environment attribution to this same box
  (`environment_source_box` absent or this box, never a different box);
- no future boundary, lifecycle ambiguity, unrelated movement or competing
  reconstruction in the relevant weeks.

Zero counts are real evidence. NULL strobilae stay NULL. Duplicate evidence is
not a second canonical observation. Missing adjacent weeks are not interpolated
from an earlier/later observation. Temperatures, target-temperature equality,
names, usernames and missing movements never establish zone identity.

The plan returns `candidates` and separate diagnostic `assessments`:

| Classification | Reconciliation status | Meaning |
| --- | --- | --- |
| `eligible` | `review_required` | Complete evidence for review; no current acceptance workflow |
| `accepted` | `accepted` | Matching immutable row and one matching acceptance audit |
| `already_represented` | `already_represented` | One corresponding existing movement; never insert another |
| `ambiguous` | `blocked` | Inconsistent, competing or incomplete period/environment evidence |
| `insufficient_evidence` | `blocked` | Missing adjacent canonical observation or location history |
| `initial_assignment` | `not_applicable` | First observed period, not a between-zone movement |

Initial assignments and missing-observation/history findings live in
`assessments`, not phantom movement candidates. Blocked transition candidates
remain inspectable as diagnostics. Multiple transitions attributed to
one box/week are blocked even when one is an insufficient-evidence assessment.

### Existing movements and conflicts

Correspondence is checked using recorded period links and/or the exact same-box
source/destination zones at the stored boundary. Supplied links must not point to
other periods, and linked timestamps/zones must agree. One consistent counterpart
is `already_represented`, not a generic conflict. This applies even to an
unclassified row: its presence prevents duplication but does not reclassify it,
certify its provenance or create missing links.

Several counterparts, contradictory links, or unrelated records in **either W
or W+1** block reconstruction. A boundary movement belongs to W+1 under the exact
movement invariant, while this historical protocol attributes the transition to
W; matching by week alone would therefore miss duplicates. Lifecycle audits in
either week also block. Existing reconstruction rows competing by attributed
week **or by the same period pair** block duplicates, including old receipts
that used the incorrect destination week. Such old rows are preserved, not
silently migrated, rewritten or reaccepted.

### Fingerprints and existing receipts

Policy `last_origin_measurement_week_full_periods_v3` participates in the preview
fingerprint and candidate key, invalidating previous policies' previews. The hash
covers the explicit selection, full box/period/movement/measurement facts,
scoped zone attributes and scoped numeric-box audits. Changes without a Box save
still stale the preview. Acceptance audits are excluded from the source hash;
existing receipt rows and their audits are checked separately for exact evidence,
attribution, references and uniqueness.

## Preserved storage

Migration `0018` only adds `HistoricalBoxMovement` and its constraints. There is
no data migration. It stores ISO year/week, zone/period references, immutable
evidence, candidate key, preview fingerprint, reviewer, acceptance time and note.
It has **no occurrence timestamp**. Unique candidate and box/week constraints,
year/week ranges and distinct zones are database enforced. Model validation checks
real ISO week validity, same-institution zones and matching same-box periods.
Protected references preserve evidence. The model/default queryset and generic
admin reject edits/deletions. Raw SQL/base-manager bypasses are not an authorized
business workflow and do not provide application-level cross-table guarantees.

`BoxMovement` and its exact Paris-week check, ordinary uniqueness, correction
service and endpoints are unchanged. Historical reconstructions use a separate
ledger and do not create or reserve an ordinary exact movement slot. The planner
checks both observation weeks for existing records; the projection re-evaluates
source changes without rewriting either ledger.

## Authorized GET and frontend contract

`GET /api/boxes/<id>/history/` requires authentication and an explicit authorized
`X-Organization-Id`. Wrong authorized scope returns 404; unauthorized context
returns 403. Canonical box IDs are required; merged aliases are not combined.
Existing detail, movement PATCH, zone history, import/export and graph endpoints
remain intact. GET only reads and never masks, removes or audits an event.

Response: `{events: HistoryEvent[], candidates: Candidate[], assessments: Assessment[]}`.
The additive `assessments` array is diagnostic only; it is not a timeline event
source. Every HistoryEvent retains the existing top-level contract:

```text
id: string
kind: ordinary | historical_reconstruction | deactivation | reactivation |
      initial_location | qualification | unclassified | location_evidence
temporal_precision: exact | iso_week | unresolved
occurred_at: string | null
iso_year: number | null
iso_week: number | null
from_thermal_zone: {id: number, name: string} | null
to_thermal_zone: {id: number, name: string} | null
actor: {first_name: string, last_name: string, email: string} | null
provenance: string
evidence: object
reconciliation_status: string
recorded_movement_id: number | null
can_correct: boolean
notes: string
```

Stable ID namespaces are `movement:`, `historical:`, `lifecycle:` and
`location:<period-id>:start|end`. Sources remain separate. Ordering is deterministic,
not a certified exact chronology for unresolved or week-only observations.

- Valid accepted reconstructions project as `historical_reconstruction`,
  `temporal_precision: "iso_week"`, attributed origin year/week,
  `occurred_at: null`, `actor: null`, the approved provenance above and
  `reconciliation_status: "accepted"`. The reviewer and `accepted_at` live in
  evidence. `can_correct` and `period_pairing_reliable` remain false.
- Existing receipts are not blanket unresolved. Only a receipt whose evidence,
  source, policy, references, week or acceptance audit no longer validates is
  unresolved. Its stored year/week remain in evidence, not certified top-level
  fields. Old invalid policies are not auto-upgraded.
- Persisted exact movements retain kind, actor, timestamp, revision and correction
  ID. `can_correct` permits entry to the existing PATCH workflow, not every field.
- Period pairing uses validated recorded links, not equal timestamps alone.
  Planner correspondence to an unlinked boundary never changes that rule.
- Lifecycle events require numeric same-box institution-scoped audit association,
  including after code renames. Explicit movement IDs, matching kinds/authors and
  validated period closures establish pairing. New qualification audits include
  their created movement ID. Missing links remain unresolved. `recorded_at` is
  an audit recording time, never substituted for an occurrence time.
- Foreign zone attributes/foreign-membership identities are omitted. Arbitrary
  audit metadata is not returned.

### Inactive-gap evidence (preserved)

The inactive interval lies between the previous active period's end and the
explicit reactivation period's start. **A short active BoxLocation is not that
gap and must not be masked based on its length or boundary-pairing flags.**

Lifecycle evidence defaults to `inactive_pairing_reliable: false` and
`inactive_interval: null`. A proven pair receives the same interval on both
lifecycle events:

```text
inactive_interval: {
  deactivation_event_id: string, reactivation_event_id: string,
  previous_location_id: number, next_location_id: number,
  reactivation_movement_id: number,
  starts_at: string, ends_at: string, duration_seconds: number,
  measurement_overlap_possible: boolean, masking_eligible: boolean
}
```

Proof requires consecutive ordered UPDATE lifecycle audits, explicit matching
status/zone snapshots, one unique closure matching `changed_at`, one unique
explicit reactivation movement and consistent local period links/chronology.
Reactivation must start unlocated and close no unknown-ended legacy periods.
Duplicate endpoint claims, intervening movements, periods, reconstructions or
audits block pairing. Only the reactivation's matching own movement audit is
allowed between its two lifecycle audits. Numeric-box and current/previous-code
scoped audits are inspected conservatively as barriers.

A reliable pair is maskable only if the positive elapsed duration is **under
24 hours** and no observation could overlap. This includes same-box records,
same-institution observations using this box as environment source, duplicates
and scientific zero. Exact-date observations cover their entire Paris day;
legacy-unverified records cover their entire ISO week. Unknown precision blocks
masking. No midnight sample time is invented. The frontend may add stricter
checks and must preserve both surrounding active periods. No backend data is
hidden or deleted by this evidence projection.

## Frontend integration

The movement subtab loads this projection through the organization-scoped API
client, including refresh, cancellation, retry and stale-context protection.
Exact and week-only events are grouped chronologically by ISO week without
inventing a week-only occurrence instant. Candidates needing review and blocked
assessments are explicitly diagnostic, with any proposed week marked unconfirmed.
Accepted and already-represented candidates do not duplicate persisted events.
A unique explicitly paired lifecycle audit is folded into its movement's evidence
for display; the movement's correction capability remains available. Coincident
but unrelated records are never collapsed.

Tracking and export previews share `projectBoxHistory`. Ordinary markers are
black; accepted historical events are black dashed week intervals, not Monday
occurrence lines. Deactivation is red, reactivation green. Initial location,
qualification, unclassified events and raw period evidence are not ordinary
movement markers. Source records remain visible in the history. Only reciprocal,
validated backend inactive-gap evidence can suppress lifecycle chart markers;
active location bands remain intact.

### Audit lookup scaling — corrected and measured on synthetic SQLite only

Previously the planner queried numeric JSON identities, and the projection read
those audits again using a numeric-or-code predicate. Existing audit indexes
supported organization/date or action/date, not the complete identity predicate.

`audit.box_lookup.box_audits` now uses independently organization/object-type
scoped numeric and code PK branches joined by distinct UNION, followed by one
model/user fetch. The projection shares its loaded numeric subset with the private
planner inside the same read snapshot. Public planning still loads
its own evidence; callers cannot inject evidence through the public planner.
Database-provided membership annotations preserve typed JSON equality, including
numeric values whose decoded Python floats lose precision. Only concrete audit
fields participate in snapshots, so the annotation does not change fingerprints.

Migration `audit.0004_auditlog_audit_box_numeric_idx_and_more` depends only on audit
`0003` and adds two indexes:

- `audit_box_numeric_idx`: organization, object type, typed JSON `box_id`.
- `audit_box_code_idx`: organization, object type, object ID.

The fixed-path `AuditBoxId` expression preserves PostgreSQL JSON `->` semantics;
its SQLite path is literal and matches the expression index. It never casts to
text or equates string IDs to numeric IDs. Migration `0004` has no data operation
and does not modify the existing cultures migration `0018`.

Current and previous box codes still supply conservative legacy ambiguity
barriers. Conflicting numeric identities are filtered as before; code-only rows
are not promoted into proven lifecycle events. UNION removes duplicate retrieval,
not historical records. The joined user fetch avoids per-audit user queries.

The synthetic query-plan regression uses 4,004 audits, including 4,000 unrelated
rows. Old/new results match: one numeric row, four fetched identity/code rows and
three retained projection rows. Before indexing, the tested SQLite plans searched
by organization only. Afterward, the regression checks all numeric-index keys,
indexed organization searches in both UNION branches, and outer primary-key
lookup, with no full audit-table or branch scans. The migrated definitions of both
composite indexes are checked independently of the planner's choice: SQLite may
use either the code index or an organization index for the code branch. A separate
regression confirms one audit query for the combined projection and unchanged
planner results and fingerprints. Indexed organization access does not guarantee
code-branch selectivity or performance; no latency claim is made.
PostgreSQL SQL and index DDL compile without connecting, but PostgreSQL plans,
latency and write/index-maintenance costs remain unverified. No verified disposable
PostgreSQL environment was used.

## Local and PostgreSQL validation

Use the [isolated QA settings](../../operations/development-qa.md) for local
`apps.cultures.test_historical_movements` tests. The `backend-postgres` job in the
[CI workflow](../../../.github/workflows/ci.yml)
checks the migration graph, reconstruction constraints and audit indexes, plus
read-only planning, API scoping and audit lookup on a disposable PostgreSQL
instance. This tests synthetic data, not production conditions or a write path.
