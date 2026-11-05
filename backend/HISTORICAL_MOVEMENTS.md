# Historical movements: approved adjacent-week protocol

Backend-only additive implementation. No real correction has been executed.
The command and acceptance service remain restricted to in-memory SQLite under
`config.test_settings`; this document is not a production execution procedure.

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
| `eligible` | `review_required` | Complete evidence; requires explicit acceptance |
| `accepted` | `accepted` | Matching immutable row and one matching acceptance audit |
| `already_represented` | `already_represented` | One corresponding existing movement; never insert another |
| `ambiguous` | `blocked` | Inconsistent, competing or incomplete period/environment evidence |
| `insufficient_evidence` | `blocked` | Missing adjacent canonical observation or location history |
| `initial_assignment` | `not_applicable` | First observed period, not a between-zone movement |

Initial assignments and missing-observation/history findings live in
`assessments`, not phantom movement candidates. Blocked transition candidates
remain inspectable, but cannot be accepted. Multiple transitions attributed to
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

### Fingerprints and acceptance

Policy `last_origin_measurement_week_full_periods_v3` participates in the preview
fingerprint and candidate key, invalidating previous policies' previews. The hash
covers the explicit selection, full box/period/movement/measurement facts,
scoped zone attributes and scoped numeric-box audits. Changes without a Box save
still stale the preview. Acceptance audits are excluded from the source hash so
an unchanged replay remains idempotent; receipt rows and their audits are checked
separately for exact evidence, attribution, references and uniqueness.

`accept_historical_movements(...)` rechecks an active institution Admin (existing
superuser break-glass retained), exact candidate IDs, fingerprint and a nonempty
review note. Lock order is organization, membership, user, selected boxes,
periods, movements, measurements, scoped zones and historical rows. It rebuilds
the real plan under those locks. Only `review_required` or valid `accepted`
candidates can proceed; blocked findings and `already_represented` cannot.

New week-only rows and individual institution-scoped audits share one transaction.
An audit failure rolls back the entire batch. An unchanged accepted replay returns
existing IDs with no second audit. A partial/contradictory receipt, missing audit,
changed preview or changed source evidence fails closed. No operational box
field, period, scientific value, existing movement or old audit is changed.

## Storage and local command safety

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
ledger and do not create or reserve an ordinary exact movement slot. Acceptance
checks both observation weeks for existing records; later changes to that source
are re-evaluated by the projection rather than silently reconciling two ledgers.

Command: `reconcile_historical_movements`

- Required: `--organization-id`, repeated `--box-id`, `--actor-id`.
- Default: authorized read-only JSON preview.
- Accept: `--accept`, repeated `--candidate-id`, `--expected-fingerprint`, `--notes`.
- No whole-institution default, implicit acceptance, force or guard bypass.

The command refuses before querying outside `config.test_settings` with SQLite
in memory. The public acceptance service has the same guard. Synthetic command
tests use `call_command` in the test process. A standalone invocation starts
with an empty in-memory database and is not a route to real application data.

PostgreSQL concurrency is **not validated or enabled**. Enabling a real-database
workflow requires separate approval, environment attestation, backup/restore
proof, a writer/concurrency review, PostgreSQL tests and an operational runbook.

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

## Synthetic validation

Use `POSTGRES_DB=''`, `POLYPBASE_TEST_POSTGRES=0`, an ephemeral Django secret and
`--settings=config.test_settings`. Never use the default database for validation.

```text
uv run python manage.py test apps.cultures.test_historical_movements --settings=config.test_settings --noinput
uv run python manage.py check --settings=config.test_settings
uv run python manage.py makemigrations --check --dry-run --settings=config.test_settings
```

The feature tests use the **actual planner for every positive acceptance**, command
replay and projection, with no eligibility/planner mocks. Only audit-failure
injection and controlled lifecycle clocks are mocked. Tests cover ISO rollover
(2025-W52, not destination 2026-W01), full/partial weeks, initial/insufficient
assessments, canonical/duplicate/zero evidence, environment attribution, recorded
linked/unlinked correspondence without forged links, ambiguous representations,
conflicts in W and W+1, immutable/old receipts, real idempotent acceptance, batch
audit rollback, stale fingerprints, scopes/permissions, week-only projection and
conservative inactive-gap masking evidence including DST elapsed time.

Validation after the approved protocol correction on 2026-11-04:

- **68 feature tests passed**, using actual planner-to-acceptance paths.
- **300 tests executed: 299 passed, one skipped** in the expanded selection:
  historical movements, movement corrections, weekly integrity/limits, lifecycle,
  location diagnostics, detail organization scoping, zone history, normalized CSV
  and historical-2026 imports, box temperature history and CCA-EOB environment
  consumers. The skip is the existing lifecycle `SELECT FOR UPDATE OF` test,
  which SQLite cannot support.
- Django `check`, migration drift (`makemigrations --check --dry-run`) and backend
  `git diff --check` passed. This policy correction needs no additional schema
  migration.

PostgreSQL concurrency, browser behavior and real source-data suitability are not
established by these synthetic tests. No real correction, production/Neon access,
commit, push or deployment was performed.

## Frontend integration and final local checks

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

FR/EN copy is registered. The 44 added keys use English fallback in Japanese;
no existing Japanese translation or locked review input was changed.

Final checks on 2026-11-04:

- Frontend full suite: `npm run test:all`: **2408 passed, zero failed**.
  This ran before the final unique lifecycle-pair presentation fix and exact
  black-stroke adjustment; their focused rerun below passed afterward.
- Final focused frontend rerun: `node --test scripts/test-box-history.mjs
  scripts/test-box-insights.mjs scripts/test-chart-readability.mjs`:
  **92 passed, zero failed**.
- Final `npm run build`: passed (CSS architecture, TypeScript and Vite).
- Final backend focused regression selection: **259 executed, 258 passed,
  one SQLite feature skip**, covering reconstruction, lifecycle, corrections,
  weekly integrity, location diagnostics, organization scope, zone history,
  normalized CSV and historical-2026 imports.
- Full backend suite: attempted with `config.test_settings`; stopped at the
  600-second timeout with no final result. It is **not** counted as passed.
- Final Django check: no issues. Migration drift: no changes detected.
- Japanese checks: **22 Node tests and 19 Python tests passed**. Translation
  register checks and **62 register tests passed**.
- Whitespace checks passed, with Git LF/CRLF advisories only.
- No browser QA: no browser automation package was available in this worktree.
  SVG hit testing, keyboard/touch interactions and desktop/tablet rendering
  remain independent-review items.

## Future separately authorized preview procedure

There is intentionally no real-data acceptance path in this local implementation.
Do not remove the command/service environment guard for an operational trial.

For a future explicitly authorized **read-only** review of an installed schema:

1. Review migration `0018` and this policy independently, and deploy only under
   the separate approved release procedure. No migration is executed by preview.
2. Establish the authorized institution and explicit canonical box-ID selection.
   Do not use an unrestricted whole-institution scan or a shared test database.
3. In an authenticated application session, request
   `GET /api/boxes/<box-id>/history/` through the normal client with
   `X-Organization-Id: <authorized-organization-id>`. It returns preserved events,
   candidates, reasons and assessments, and performs no reconstruction or audit.
4. For a scoped fingerprinted plan in a separately reviewed read-only backend
   environment, call `plan_historical_movements(organization_id=<id>,
   box_ids=[<explicit IDs>])` only after caller authorization. Retain its version,
   fingerprint, evidence, classifications and exact selection for review. This
   helper grants no permission itself and has no writes.
5. Compare source periods and observations independently. Missing weeks, gaps,
   overlaps, unknown ends, conflicting events and incomplete provenance remain
   unresolved; do not edit originals to force eligibility.
6. Stop after preview. Applying institutional reconstructions requires new
   explicit authorization, an environment-attested operational acceptance path,
   PostgreSQL concurrency tests, backup/restore evidence and a reviewed runbook.
   The shipped management command and acceptance service refuse that operation.

For the currently authorized synthetic local preview/acceptance exercise, run
`apps.cultures.test_historical_movements` under `config.test_settings`; command
preview and exact candidate/fingerprint acceptance are executed with `call_command`
inside the same synthetic in-memory test process. A standalone command does not
share that test database.

No further biological product decision is required for these deliberately narrow
eligible cases. Independent review must assess the source-period completeness
policy, ledger coexistence, PostgreSQL locking, read-projection cost and the
operational guard before any future institutional application.

## Independent-review corrections (2026-11-04)

### Duplicate reactivation markers — corrected

The original chart counted valid audits independently, while the timeline checked
unique pairs. Two audit claims for one movement therefore suppressed the movement
and produced two green audit markers. `uniqueLifecyclePairs` now counts every
explicit claim before validating a unique one-to-one association. The timeline,
chart and inactive-gap masking share that rule. A valid pair retains the movement
identity and correction capability, with the complete audit nested as evidence.
Ambiguous, missing or contradictory claims stay separate unresolved history rows;
their complete original event, including its claimed occurrence, remains in
`evidence.source_event`. They do not acquire dated chart markers. The recorded
reactivation remains green, not an ordinary black movement. No source object or
persisted audit is changed.

### Audit lookup scaling — corrected and measured on synthetic SQLite only

Previously the planner queried numeric JSON identities, and the projection read
those audits again using a numeric-or-code predicate. Existing audit indexes
supported organization/date or action/date, not the complete identity predicate.

`audit.box_lookup.box_audits` now uses independently organization/object-type
scoped numeric and code PK branches joined by distinct UNION, followed by one
model/user fetch. The projection shares its loaded numeric subset with the private
planner inside the same read snapshot. Public planning and acceptance still load
their own evidence; callers cannot inject evidence through the public planner.
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

### PostgreSQL acceptance lock order — deferred, still a mandatory activation blocker

The acceptance path currently locks organization -> membership -> reviewing user
with `FOR UPDATE`, then selected boxes -> periods -> movements -> measurements ->
zones -> historical rows. Ordinary movement writers lock the box first, then
create movements and mandatory audits referencing the actor/organization.

A possible PostgreSQL cycle is:

1. Acceptance holds the reviewer/organization row and waits for a box.
2. An ordinary writer holds that box and, on movement/audit foreign-key checking
   (possibly at commit for deferred constraints), needs `KEY SHARE` on the actor
   or organization row held `FOR UPDATE` by acceptance.
3. Each transaction waits for the other. Changing the reviewer does not eliminate
   the organization-FK variant.

No lock-order change is made in this correction. Before operational activation,
the fix must remove this opposing lock dependency while retaining permission
revalidation and membership serialization. One concrete option requiring review
is FK-compatible `FOR NO KEY UPDATE` for existing actor/organization keys that the
operation never changes; another is a shared global ordering across acceptance,
box writers and membership administration. Neither option is validated here and
neither may be enabled merely by removing the environment guard.

Required disposable-PostgreSQL tests must force both interleavings with separate
connections/barriers and bounded lock/statement timeouts: acceptance vs ordinary
movement with the same and different actor; acceptance vs correction,
deactivation/reactivation and measurement writes; overlapping acceptance batches
in opposite input order; and concurrent membership revocation/user deactivation.
Assert no deadlock, no stale permission acceptance, no partial reconstruction or
audit, idempotent duplicate behavior and unchanged scientific/location data. Check
foreign-key locks at commit as well as explicit row-lock acquisition.

The active GET remains read-only and takes no acceptance row locks. The public
acceptance service and command still reject every environment except SQLite in
memory under `config.test_settings`, before querying. Added tests exercise settings,
file-backed SQLite and PostgreSQL guard refusals without opening those databases.
SQLite write contention is not evidence of PostgreSQL lock safety.

### Correction validation

- Frontend history/insights/readability/biological-timeline selection: **126 passed**.
- `npm run typecheck`, `npm run check:css` and final `npm run build`: passed.
- Backend feature/audit-lookup/audit-app selection after numeric-membership review:
  **101 passed**.
- Final broader backend selection, including lifecycle and movement correction:
  **159 executed, 158 passed, one existing SQLite-unsupported lock-feature skip**.
- Django check and migration drift checks: passed. Whitespace checks: passed.
- One earlier combined frontend command timed out after its tests/typecheck/CSS
  stages; the focused tests and production build were rerun separately and passed.
- No browser QA or PostgreSQL execution was performed for these corrections.

These findings are ready for targeted independent re-review. This is not an
institutional acceptance or deployment verdict. All pre-existing uncommitted
implementation remains intact.

## Dedicated GitHub Actions PostgreSQL validation

`.github/workflows/historical-movements-postgres.yml` runs on pushes to
`feat/historical-movements`. It does not depend on a workflow already being on
main, a pull request or a local Docker installation. No run has been executed or
reported successful as part of this local workflow implementation.

### CI boundary

- GitHub-hosted `ubuntu-latest`, 20-minute job timeout, `contents: read` only,
  checkout without persistent credentials, actions pinned to commit SHAs.
- PostgreSQL `18`, matching the major used by the existing PCA Unix QA. The image
  tracks PostgreSQL 18 patch releases; the actual server version is recorded.
- Fresh service database/role `historical_movements_ci`, mapped to loopback
  client endpoint `127.0.0.1:55439`; Django tests create
  `test_historical_movements_ci`. PostgreSQL data lives in a tmpfs mount under
  `/var/lib/postgresql`. No host/persistent volume is configured.
- Trust authentication is deliberately confined to this disposable hosted runner;
  there are no institutional credentials, repository secrets or reusable password.
  This is not a pattern for a shared runner or production database.
- `config.historical_movements_ci_settings` requires the dedicated GitHub/Linux
  gates, refuses backend/.env and external libpq/database URL configuration, and
  uses a fixed PostgreSQL engine/endpoint/database/role. There is no SQLite fallback.
- The read-only preflight checks configuration and PostgreSQL 18 identity, then
  refuses unexpected schemas, public relations, routines/types or extensions.
  It never flushes an unexpected database. A failed preflight prevents migrations.
- The historical acceptance service and command are unchanged and remain disabled
  on PostgreSQL. No positive acceptance test or direct historical-row insertion is
  used to evade that guard. The known deadlock is not exercised, fixed or enabled.

### Checks and evidence

The job installs application dependencies from `uv.lock` using Python 3.12 and
`uv sync --frozen`. The independent offline workflow/settings tests use pinned
PyYAML as a transient test-tool dependency; it is not added to production packages.

After preflight, CI runs Django checks/drift detection, applies the entire current
migration graph, captures SQL for cultures `0018` and audit `0004`, and verifies
applied migrations, exact historical columns, CHECK/unique/FK constraints and the
two valid/ready B-tree audit indexes in PostgreSQL catalogs. Schema checks also
require the historical reconstruction table to remain empty.

The selected Django modules are:

- `apps.cultures.test_historical_movements_postgres_ci`: seven PostgreSQL-only
  tests for deterministic W/W+1 planning, read-only API, permissions/institution
  isolation, real PostgreSQL guard refusal, schema and lookup evidence.
- `apps.cultures.test_history_audit_lookup`: existing identity/equivalence,
  snapshot reuse and query-count regressions. Its SQLite plan test is explicitly
  skipped on PostgreSQL; it is not used as PostgreSQL performance evidence.
- `apps.cultures.test_box_detail_organization_scoping`: detail authorization.

The full SQLite-oriented reconstruction test module is deliberately not selected:
its positive acceptance cases are incompatible with the PostgreSQL refusal guard.
Its synthetic setup helper is reused without discovering or running those tests.
No unrelated full backend suite or frontend build is added to this job.

The lookup test generates 1,000 / 10,000 / 50,000 unrelated synthetic audits,
split equally across the scoped and another institution. At each volume it checks
numeric, legacy-current, legacy-previous and combined retrieval against the old
query formulation. It captures 12 observations with unforced
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`, actual index choices, row counts,
planning/execution times, basic ORM timing and index sizes. It verifies equal
results, typed identities, no duplicated evidence, one query and no user N+1.

Plans are deliberately not forced and no latency threshold is asserted. An
optimizer-chosen sequential scan is reported for review, not hidden or made into
a correctness failure. Measurements are single warm executions on a shared CI
runner inside a test transaction, not production benchmarks. The old and new
formulations both use the migrated indexes. Index storage is reported; this job
does not benchmark concurrent write overhead or production index-build locks.

Every run attaches `historical-movements-postgres-<run-id>-<attempt>` for seven
days, including the tested SHA, migration plan/logs/SQL, schema findings, test log
and `historical-movements-postgres-lookup.json`. Failure logs are retained where
available. Artifacts and the job summary alone do not imply a successful job;
inspect all step conclusions and the test summary.

### Local review and first GitHub run

Local checks do not start Docker or connect to PostgreSQL:

```text
uv run --frozen --with pyyaml==6.0.3 python -B -m unittest discover -s scripts -p test_historical_movements_ci.py -v
```

Use `config.test_settings` with `POSTGRES_DB=''` and
`POLYPBASE_TEST_POSTGRES=0` for local Django import/regression checks. The seven
new PostgreSQL tests skip locally; a skip is not a PostgreSQL pass. Do not spoof
the GitHub gates or redirect these settings to an existing database.

After independent review, Anthony can run the following from the repository root.
These commands are instructions only; the writer did not execute them:

```text
git status --short
git diff --check
git add .github/workflows/historical-movements-postgres.yml backend/config/historical_movements_ci_settings.py backend/apps/cultures/test_historical_movements_postgres_ci.py scripts/validate_historical_movements_ci.py scripts/test_historical_movements_ci.py backend/HISTORICAL_MOVEMENTS.md
git diff --cached --stat
git commit -m "Add isolated PostgreSQL history CI"
git push origin feat/historical-movements
```

In GitHub, open Actions -> Historical movements PostgreSQL -> the run for the
pushed SHA. Verify the preflight, graph/catalog checks and tests, then download the
artifact to inspect all twelve lookup observations, actual index choices and
limitations. If Actions requires approval/enabling in the repository, resolve that
in GitHub; no deployment environment or production secret should be configured.

Local validation of this workflow addition: all eight offline settings/workflow
unit tests passed with pinned PyYAML; 82 Django tests completed under isolated
SQLite settings (75 passed, seven new PostgreSQL-only tests skipped). Django
system checks, migration drift and whitespace checks passed. YAML was parsed and
its trigger, service, permissions, steps and pinned actions were asserted;
`actionlint` was not available. An initial invocation with system Python failed
because that interpreter lacked Django; the documented `uv run` invocation passed.
No Docker command, PostgreSQL connection or GitHub run was executed locally.
Actual service startup, migrations/catalog SQL and PostgreSQL test/EXPLAIN results
remain unverified until the first successful Actions run.

A green run is PostgreSQL integration evidence for this commit and synthetic
workload only. It is neither production deployment approval nor authorization to
activate historical acceptance. The documented concurrency blocker and separate
institutional authorization remain mandatory.
