# Isolated PCA and historical-temperature QA

The QA tooling integrates the committed historical-temperature implementation from
`b379662718b0f89a2aa337bcd05a1706b6eabfef` and the PCA test-label interface from
`c25ff19653380893780d0d25d92638ced4f23c8f`. No original local database, role, system
identifier or installation path is a configuration default.

## Boundary and provisioning contract

Provisioning belongs to the operator/parent workflow, not these scripts. Use a new,
empty, disposable PostgreSQL **18** cluster containing synthetic data only. Never
point these tools at a shared copy, production, Neon or a production-derived dump.
No database provisioning or connection is needed to run the connection-free checks.

Before running a database suite, the operator must independently verify the cluster
control system identifier and supply it explicitly. Both restricted QA logins need
EXECUTE on `pg_catalog.pg_control_system()` in the disposable database: the backend
compares its result on every connection. Missing permission or a mismatch aborts;
there is no attestation-only fallback. Do not grant superuser, cluster-wide reading
roles, CREATEDB, CREATEROLE, REPLICATION or BYPASSRLS to satisfy this requirement.

The owner must own the disposable database and be able to migrate its public
schema. The runtime login must be distinct, have no owner membership/SET privilege,
no schema/database CREATE privilege and no inherited privileged cluster role.
Provision those restrictions before running the harness. The harness grants only
local table/sequence/function privileges needed by synthetic proofs.

Every target must use a canonical numeric loopback address, not a hostname,
Unix socket, URI, host list or DNS name. Port **55438 is forbidden**. Ports have no
default. Database and role names are lowercase plain PostgreSQL identifiers; the
database cannot be a maintenance/template database. Passwords must be nonempty and
are entered through hidden interactive prompts, never source files or command-line
arguments. Do not run under a debugger or terminal transcript.

## Required QA process environment

All six identity values are mandatory. This document intentionally supplies no
machine-specific values.

| Variable | Meaning |
| --- | --- |
| `POLYPBASE_QA_HOST` | Explicit numeric loopback server address |
| `POLYPBASE_QA_PORT` | Explicit disposable server port |
| `POLYPBASE_QA_DATABASE` | Dedicated empty disposable database |
| `POLYPBASE_QA_SYSTEM_IDENTIFIER` | Independently verified cluster control identifier |
| `POLYPBASE_QA_OWNER` | Restricted migration/fixture owner login |
| `POLYPBASE_QA_RUNTIME` | Separate restricted runtime login |
| `POLYPBASE_TEST_POSTGRES` | Must be `1` |
| `POLYPBASE_HISTORICAL_POSTGRES_QA` | Must be `1` |

The launcher requires `--run` and an interactive terminal. It clears inherited
`PG*`/`POSTGRES_*` settings, disables dotenv (including older python-dotenv versions),
forces the dedicated Django settings and supplies explicit libpq parameters with
the password file disabled. It never falls back to application settings. The custom
backend rejects target/credential/option drift, maintenance connections, database
creation/deletion/cloning, extra aliases and parallel test databases.

The harness checks database identity, PostgreSQL major, real session/current roles,
role privileges, origin replication mode, public schema, read-committed isolation,
empty starting data and an exclusive advisory lease. Migration rehearsal and
synthetic cleanup require that lease. Live workers prevent destructive cleanup.
The real UI importer stays disabled. The advisory key is a fixed harness constant,
not a machine port or personal identifier; do not run an older harness concurrently.

## Commands for the parent/operator

From the repository root, after separately provisioning and exporting the complete
configuration above, using a Python interpreter with the project's dependencies:

```sh
# Full migration round-trip, historical PostgreSQL proofs and representative load
python scripts/validate_historical_temperature_postgres.py --run

# Historical tests only: intentionally exits 1 / CHANGES REQUIRED without load proof
python scripts/validate_historical_temperature_postgres.py --run --tests-only

# Synthetic load only, on an already migrated, empty disposable database
python scripts/validate_historical_temperature_postgres.py --run --load-only

# Focused PCA checks and tests through the same restricted backend and lease
python scripts/run_pca_qa.py --run
python scripts/run_pca_qa.py --run measurements.test_canonical_evidence
```

PCA preserves the original three default labels (canonical measurement evidence,
canonical exports and PCA identity unification), optional short labels, Django
checks and migration drift checks. Unlike its committed source, it never invokes
initdb/pg_ctl/createdb or creates/deletes temporary clusters. It uses the active
Python interpreter and a 900-second subprocess deadline. A focused pass exits 0 but
is explicitly **not** a full historical validation. A timeout exits 124; inspect
sessions and leftover synthetic rows before reusing the disposable database.

The historical runner also supports bounded synthetic reading count/duration,
optional memory tracing and guarded synthetic-leftover cleanup. `--labels` requires
`--tests-only`; it cannot generate a full-validation verdict. Do not bypass failed
identity, privilege, migration or schema checks to make a suite pass.

Reports are local to `.local-docs/historical-temperature-pgqa-validation.json` and
may contain runtime target/role metadata, never passwords. A PCA run replaces this
same report. Keep it private and untracked; do not copy it into committed docs.

Connection-free checks (no provisioning, sockets or database connections):

```sh
python -B -m unittest discover -s backend/config/temperature_pgqa_backend -t backend -p test_runtime_config.py
python -B scripts/run_pca_qa.py --help
python -B scripts/validate_historical_temperature_postgres.py --help
git diff --check
```

`config.historical_temperature_test_settings` is a separate SQLite-only,
in-memory integration configuration with application migrations disabled. It blocks
the legacy PostgreSQL opt-in before importing base test settings and disables
dotenv. It does not validate PostgreSQL SQL, constraints, locks or concurrency.

## Staged UI configuration is separate and not QA authorization

`config.historical_temperature_ui_local_settings` requires these identity variables:

- `POLYPBASE_UI_TEMP_DB_HOST`
- `POLYPBASE_UI_TEMP_DB_PORT`
- `POLYPBASE_UI_TEMP_DB_DATABASE`
- `POLYPBASE_UI_TEMP_DB_SYSTEM_IDENTIFIER`
- `POLYPBASE_UI_TEMP_DB_OWNER`
- `POLYPBASE_UI_TEMP_DB_RUNTIME`
- `POLYPBASE_UI_TEMP_DB_USER` (must match one of the two explicit roles)

It also requires `POLYPBASE_UI_TEMP_STAGE` (A/B/C/D). The separate existing switches
`POLYPBASE_UI_TEMP_ALLOW_MIGRATE` and `POLYPBASE_UI_TEMP_ENABLE_APPLY` remain limited
to stages B and C respectively. Authentication may use
`POLYPBASE_UI_TEMP_DB_PASSWORD` or the operator's private `PGPASSFILE`; other libpq
environment overrides are rejected. Inherited application/PostgreSQL test settings
are rejected. This configuration does not run or authorize any staged operation.

Application-side staged services now validate the explicit target and compare its
control system identifier with `pg_catalog.pg_control_system()` before continuing.
Both staged logins need narrowly scoped EXECUTE permission on that function. The
runtime role must be the configured runtime login; the migration role must match
the configured owner. Existing privilege, backup, stage and approval gates remain.
No UI stage or scientific import was authorized or executed during integration.
The old approved snapshot and plan fingerprints are not silently regenerated:
identity-unification changes require a newly reviewed plan before real imports.

## Integrated migration contract

`measurements.0011_historical_temperature_storage` follows `0010`; `0012` and
`0013` follow in sequence. `0014_measurement_canonical_evidence` depends on `0013`
and `cultures.0015_box_merged_into_box_previous_global_code_and_more`. The latter
depends on main's `cultures.0014_box_movement_week_matches_moved_at`, organizations
`0002` and taxonomy `0009`. There is one measurement leaf and no data-correction
operation. Migration `0014` extends the historical guards for canonical readings,
the explicit CHO-to-JKA product identity, duplicate exclusion and retired boxes;
reversal restores the old functions before removing referenced fields. The QA
cleanup reinstalls these extensions, not only the original historical guards.
The existing `unify_pca_identity` command and its public QA service accept only the
independently verified disposable QA harness; no legacy local database target
remains an alternative. The separate, explicitly authorized production workflow
is documented in [pca-production-execution.md](pca-production-execution.md); it is
never invoked by this harness, a deployment, or a migration.

## Integrated PostgreSQL validation — 2026-10-19

Validated on freshly initialized, independently identified native PostgreSQL 18.6
clusters containing synthetic data only. Before schema writes, the local workflow
compared `pg_controldata` with `pg_control_system()`, checked the bootstrap process,
loopback port and exact fresh data directory, verified restricted owner/runtime
attributes and privileges, and refused access to other databases. Port 55438 and
all pre-existing clusters were excluded. Passwords were generated in memory; no
credentials were recorded in tracked files. All clusters created by the workflow
were stopped, without deleting or resetting any pre-existing QA instance.

- Actual graph: one measurement leaf at `0014`, with the dependencies above.
- Forward migrations, empty reverse through measurements `0010`, and reapply: passed.
- Empty reverse of cultures `0015` and dependent measurements `0014`, followed by
  reapply: passed; removed fields and migration records checked explicitly.
- Populated historical/receipt reversal refuses atomically without schema/data loss.
- Historical PostgreSQL suite: **54 passed, 0 failed, 0 skipped** (all 52 original
  proofs plus two integrated CHO/JKA/canonical SQL-trigger regressions).
- Targeted PCA, canonical evidence, exports, historical API, integration and movement
  correction/concurrency coverage: **144 distinct tests passed, 0 remaining
  failures, 0 skipped**. Completed across focused batches, excluding already passed
  methods after fixture corrections.
- Fail-closed preconnection/identity/lifecycle negatives: **11 passed**; connection-free
  runtime target configuration regressions: **5 passed**.
- Django database checks and migration drift checks passed on the fresh clusters.

Two test-only corrections were necessary: the dependency-inventory fixture now
supplies the source evidence required by the deferred nonempty trigger, and
`DatabaseGuardTests` explicitly disables the real PostgreSQL harness except in the
method specifically testing that harness. No scientific guard or business rule was
weakened. Initial Windows process-output capture and global-timeout interruptions
were resolved in the ignored local validation workflow; the final historical run
completed normally. Private per-run reports and progress logs stay under ignored
`.local-docs/`; do not commit them or copy their runtime identities into this guide.

This is schema, integrity and concurrency validation, not authorization for any
scientific import or deployment. No representative 40,291-reading load, frontend
suite/build or unrelated full backend suite was run in this step.
