# Box list query performance — local evidence

## Scope and initial state

- Worktree: `perf-box-list-query/POLYPBASE`; branch `perf/box-list-query`.
- Initial HEAD: `178ce33`. Git working tree was clean; no existing diff.
- No commit, push, merge, deployment, production/VM/Neon/shared DB access.
- PostgreSQL 17.11 in a disposable Docker container using the local Docker
  Desktop Linux engine, a tmpfs data directory and loopback-only port 55439.
  Django created/destroyed `test_subculture_qa`; default database settings were
  never used for database operations. No imported or production data.

## Confirmed root cause

The expensive query was the current-state measurement prefetch, not pagination
or a classic Box-list N+1. It filtered measurement rows against a correlated
latest-eligible-measurement-ID subquery. That inner query repeated operation
sequence/day lookups; the outer candidate queryset repeated them again.
Long histories increased repeated work and planner cost drastically.

For the initial first page, the dominant SQL took **4,523.6 ms**. Its
`EXPLAIN (ANALYZE, BUFFERS)` reported:

- Estimated cost: **5,368,911.89**.
- Execution: **4,300.793 ms**; planning: **8.379 ms**.
- JIT: **312 functions**, **4,165.557 ms** total, including **2,278.458 ms**
  optimization and **1,858.475 ms** emission.
- Existing `box_id` index scan; 50 selected state measurements, 5,150 rows
  removed by the filter; 33,345 shared buffer hits, no reads or temp spill.
- Latest ordinary measurement prefetch was also correlated, but much smaller:
  **52.9 ms** on that first request. Main Box query: **12.2 ms**.

JIT was the dominant measured cost, triggered by the inflated query shape.
Disabling JIT globally or adding an index would mask rather than remove this
shape. Neither was done.

## Implementation

1. Sliced Django `Prefetch` querysets select one eligible measurement and one
   complete parent event **per Box** using `ROW_NUMBER() OVER (PARTITION BY ...)`.
   Eligibility filtering is inside the window query, before ranking.
2. The selected complete operation is joined by a single latest-event PK lookup.
   Its sequence and date are reused. Child initialization uses one-to-one joins
   and CASE expressions with the unchanged known-value and organization checks.
3. Internal state expressions use `alias()`, not selected annotations. The
   resolver still returns the same source, timestamp, measured date and revision.
4. Ordinary latest measurement is independently selected by its original
   `-measured_on, -created_at` ordering. Its sliced prefetch uses an internal
   `list_latest_measurements` attribute because caching a sliced queryset on the
   related manager raises Django's `Cannot filter a query once a slice has been
   taken` error. Serializer fallback behavior remains available for detail and
   other existing consumers.
5. A present salinity annotation is authoritative even when null, preventing
   fallback queries after the prefetch attribute change. Selection of the most
   recent non-null salinity remains unchanged; scientific zero is preserved.

No schema change, index migration, backfill, denormalized count, dependency,
frontend change, cache, API field or pagination change. The query rewrite works
on PostgreSQL and SQLite with the repository's Django 5.2/window support.

## Methodology and artifacts

Synthetic performance shape only:

- 650 Boxes (600 active), 7,000 BiologicalMeasurement rows.
- 50 Boxes with 100 weekly measurements; 200 with 4; 400 with 3.
- 400 SubcultureEvents: 300 complete and 100 partial; 100 allocations with known,
  zero and unknown values. Located/unlocated and inactive Boxes included.
- Fixed clock, fixed measurement creation timestamps, deterministic IDs,
  authors, revisions and strings; identical fixtures before/after.
- Data bulk-created solely for performance shape, not to establish business
  semantics. Separate equivalence fixtures use normal scientific services and
  deliberately documented historical/anomalous cases.
- `ANALYZE` after loading; default local PostgreSQL JIT configuration retained.
- Three requests per endpoint; median end-to-end Django test-client wall time,
  including response serialization/rendering, excluding seed/migration time.
  Forced authenticated user: real authentication/network latency not included.
- SQL count and high-resolution execute-wrapper timings; `EXPLAIN (ANALYZE,
  BUFFERS, FORMAT JSON)` of the three slowest first-request SELECTs outside the
  endpoint timer. SQL/plan and warm-cache capture overhead are included in the
  same methodology on both paths. These are not cold-storage measurements.

Generated artifacts in the worktree's `.qa-box-list/` directory:

- `initial-before.json`: actual unmodified implementation, captured before coding.
- `before.json`: reproducible frozen-oracle replay after implementation, including
  every executed SQL statement and plans for the three slowest statements.
- `after.json`: final optimized replay, same SQL/plan capture and complete output.

Artifacts contain only synthetic QA records. All ten complete response snapshots
in the final replay were equal before/after; payload lengths were also identical.
The benchmark retains full snapshots, not just selected scientific fields.

## Results

Milliseconds; medians of three samples. Initial and frozen replay baselines are
shown separately to expose local host variability rather than cherry-pick it.

| Endpoint | Initial before | Frozen before replay | Final after | Queries before/after | Bytes before/after |
|---|---:|---:|---:|---:|---:|
| Boxes offset 0 | 5,701.6 | 3,415.8 | 439.3 | 8 / 8 | 134,611 |
| Boxes offset 100 | 828.0 | 505.8 | 273.6 | 8 / 8 | 136,485 |
| Boxes offset 200 | 651.4 | 478.8 | 372.2 | 8 / 8 | 136,496 |
| Boxes offset 300 | 640.8 | 459.0 | 474.0 | 8 / 8 | 136,493 |
| Boxes offset 400 | 654.1 | 502.3 | 400.2 | 8 / 8 | 136,496 |
| Boxes offset 500 | 699.4 | 564.3 | 490.9 | 8 / 8 | 136,496 |
| Boxes offset 600 | 538.0 | 393.8 | 543.4 | 8 / 8 | 68,276 |
| Single 100-week-history Box | 405.7 | 279.9 | 928.3 | 8 / 8 | 1,276 |
| Dashboard | 5,313.0 | 3,199.6 | 2,374.6 | 22 / 22 | 4,188 |
| Overview, months=3 | 6,990.7 | 4,117.3 | 2,351.5 | 11 / 11 | 531,534 |

Each full list page returns 100 Boxes; final page returns 50. Filtered long-history
request returns 1; Overview returns 600 active Boxes. Dashboard is not a Box list.

- First page: **92.3% faster** than the actual pre-edit baseline, **87.1% faster**
  than the frozen replay. Final full-page medians: **274–491 ms**.
- Sum of seven page medians: **9,713.3 → 2,993.6 ms**, about **69.2% lower**
  (52.6% lower versus the frozen replay). This is a sum, not a separately timed
  network/frontend navigation or seven-request workload.
- SQL query count is unchanged. Performance comes from query shape, not reducing
  response fields or changing pagination.
- Final first-page slowest SQL statements: **56.6, 22.4, 10.1 ms**, versus
  **4,523.6, 52.9, 12.2 ms** initially. Total executed SQL in the final first
  captured request: **107.8 ms**.

Final dominant current-state plan:

- First page estimated cost: **2,881.28**; execution **102.180 ms** in the separate
  EXPLAIN run; 21,791 shared hits; **no JIT**, disk reads or temp spill.
- Active-collection state query cost: **3,557.54**; **no JIT** for Dashboard or
  Overview. Separate final EXPLAIN executions: **286.918 / 318.325 ms**.
- Dominant full-history latest-ID subquery removed. One small latest-operation
  lookup remains; existing parent FK and child one-to-one indexes are reused.
- The remaining ordinary-measurement, salinity and location queries are not
  dominant. No index addition was justified by these plans.

### Variability and secondary endpoints

Earlier serial optimized runs measured first page **286.9 ms**, the single
long-history Box **92.6 ms**, Dashboard **273.2 ms**, Overview **1,429.3 ms**.
Other runs had similar large improvements in the dominant SQL. The final replay
became noticeably slower across otherwise trivial queries; even a trivial
Dashboard lookup took 246.8 ms while its separate EXPLAIN took 0.076 ms.
This is evidence of significant local scheduling/round-trip noise, not a reliable
production latency forecast. It also explains the isolated long-history and
last-page wall-time regressions in the final table; they are not hidden.

Dashboard and Overview benefit automatically from the shared state query. No
endpoint-specific redesign was made. Overview still serializes 600 Boxes and
recent history (~532 KB), so its full response remains above one second here.
Dashboard retains its existing 22 queries, including latest-entry editability
lookups; these were not changed. No further unrelated endpoint optimization.

## Semantic equivalence coverage

Twelve focused tests compare the complete nested Box-list payload against frozen
original query builders, with fresh contexts and querysets. Coverage includes:

- Ordinary latest measurement versus distinct current state; all API fields,
  authors, corrections/editability across roles, locations and response envelopes.
- Polyp/ephyrae/salinity zero; strobila null versus zero; unknown and no source;
  inactive historical measurements; no current location and historical locations.
- Complete/partial parent operations, zero/known/unknown child allocations,
  supersession, consumed future-dated source, newly eligible lower-date source,
  old correction sequence preservation, backdated measurements and legacy events.
- Child initialization followed by its own complete/partial parent operations.
- Historical measurement sequences NULL/0 and event sequence ties with PK priority.
- Foreign Box exclusion and deliberately anomalous foreign initialization ignored.
- Page-local prefetch bounds, unchanged ordering/pagination, fixed query count for
  measured Boxes with histories, and zero queries during cached-role serialization.

Additional edge-case tests compare ordered old/new measurement candidate IDs and
fresh-instance fallback state against prefetched state with explicit expectations.
Same-date ordinary measurement ties are impossible under the current per-Box ISO
week uniqueness constraint; tests enforce that constraint and unchanged ordering,
plus same-clock transition and event-PK ties rather than invalid measurement data.
Scientific behavior is independently exercised by the existing quantitative,
measurement, nullable-strobila, lifecycle, scoping and concurrency tests.

## Validation

- Before optimization: original nine new oracle tests passed on isolated SQLite.
- Initial targeted SQLite run: 101 tests, one skip. Added edge coverage:
  all 12 focused SQLite tests passed. Final expanded SQLite run: **231 tests,
  OK with 17 skips** (including PostgreSQL-only concurrency coverage).
- **228 PostgreSQL tests passed**, covering query equivalence, quantitative state,
  overview, API permissions/pagination, inventory, organization scoping, lifecycle,
  measurements, nullable strobila and both biological/subculture concurrency modules.
- After adding three edge tests: **all 12 focused PostgreSQL tests passed**.
- Initial benchmark, frozen before replay and final after replay passed; all ten
  full final response snapshots equal.
- `Django check`: no issues. `makemigrations --check --dry-run`: no changes.
  Initial standalone checks needed an explicit local-only DJANGO_SECRET_KEY
  because DEBUG was off; rerun with a disposable non-secret QA value succeeded.
- `git diff --check`: passed; new-file trailing-whitespace checks also passed.
  No frontend typecheck: API contract/types unchanged.
- The dedicated QA container was stopped and automatically removed; tmpfs and
  test databases discarded. Synthetic JSON benchmark artifacts retained locally.
- Read-only independent code review: no blocking findings; suggested edge coverage
  was subsequently added. This is not a substitute for external review.

## Reproduce

From the worktree root, first verify `docker context inspect` resolves to the local
engine. Do not use a remote Docker engine, shared container or occupied QA port.
Start an empty disposable PostgreSQL 17 container (MSYS_NO_PATHCONV avoids Git
Bash path conversion on Windows):

```sh
MSYS_NO_PATHCONV=1 docker run --detach --rm --name polypbase-box-list-perf-qa --mount type=tmpfs,destination=/var/lib/postgresql/data --publish 127.0.0.1:55439:5432 --env POSTGRES_DB=subculture_qa --env POSTGRES_USER=subculture_qa --env POSTGRES_HOST_AUTH_METHOD=trust postgres:17
```

From `backend/`, run sequentially, with no concurrent test workload:

```sh
POLYPBASE_TEST_POSTGRES=1 POLYPBASE_BOX_BENCHMARK=before uv run python manage.py test apps.cultures.test_box_list_benchmark --settings=config.test_settings --noinput
POLYPBASE_TEST_POSTGRES=1 POLYPBASE_BOX_BENCHMARK=after uv run python manage.py test apps.cultures.test_box_list_benchmark --settings=config.test_settings --noinput
POLYPBASE_TEST_POSTGRES=1 uv run python manage.py test apps.cultures.test_box_list_query --settings=config.test_settings --noinput
```

The `before` mode patches only the two endpoint query-builder references with the
frozen original test implementations; it does not revert production code. Both
modes generate the same disposable dataset; `after` asserts complete snapshot
identity. Reports are overwritten on rerun. Stop only this dedicated container
when finished; its `--rm` and tmpfs discard the instance and data:

```sh
docker stop polypbase-box-list-perf-qa
```

## Residual risks and verdict

- Measurements are representative synthetic scale, not a copy of production.
  Distribution, hardware, planner statistics, PostgreSQL configuration and real
  authentication/network overhead may differ.
- Three samples per endpoint and strong local host variability limit precise
  latency predictions. The SQL-plan improvement and exact payload equivalence
  are stronger evidence than a single wall-time number.
- The small correlated latest-operation lookup still scales with candidate rows;
  it is no longer the repeated full-history nested selection or a JIT trigger at
  this tested scale. Reassess only if event/history volumes grow materially.
- Shared consumers inherit the query rewrite, so independent review should include
  their state semantics. No scientific state is cached between requests.

**READY FOR INDEPENDENT REVIEW**
