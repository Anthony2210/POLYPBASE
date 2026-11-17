# Deployment backup completeness after Box 1902 provisioning

## Authority and operational handoff

The existing `box_1902_sql.provision_sql()` already creates `box1902_backup`:
no superuser, ownership, role memberships, write privileges or trigger bypass.
It grants public schema/table/sequence read access, including owner defaults.
Migration `measurements.0018_box_1902_preservation` grants private schema usage
and SELECT on all three provenance tables, including owner defaults. Do not
add these private grants to `polypbase` or use the migrator as a backup login.

`deploy/scripts/backup_database.py` retains its existing CLI and `BACKUP_OK`
output, but always calls `create_complete_archive`. The normal deployment invokes the independently installed
`trusted_deployment_backup.py` broker under a separate OS identity; it does not
import application settings or pass private credentials through the web process.
Direct script calls, including the old nightly systemd call, fail closed if their
login cannot read the whole database. Without an override they still use the configured
runtime identity: after provisioning/0018 this is intentionally insufficient.
There is no fallback to a public-only archive or to a more privileged login.

For the separately authorized backup execution environment, select
`POLYPBASE_BACKUP_USER=box1902_backup`. `POLYPBASE_BACKUP_PASSWORD` is optional
for an independently configured passwordless authentication path; otherwise it
must contain the dedicated credential. The runtime password and password file
are never reused for the override. Host, port, database and SSL mode remain the
application target; a backup override cannot select another database.

**Authentication/delivery remains operator work, not performed by this change.**
Supply backup credentials only to an isolated trusted backup/deploy process,
not the web service, its shared `.env`, a command-line argument, or the repo.
A peer mapping available to the web service's OS identity would also allow it
to log in as the backup role: it is not an independent authentication boundary.
Review OS identity, credential storage, HBA/ident rules and orchestration before
production use. No role grants or authentication configuration are applied by
the backup script. The broker's guarded installation and the full recovery workflow
are documented in [dedicated migration recovery](dedicated-migration-recovery.md).
Normal deployment deliberately fails until that installation is independently
provisioned and its exact target and helper hashes approved.

## What is verified

Before any dump or retention, the script requires safe versioned Debian PG18
clients. It rejects ambient `PG*` connection overrides, connects directly with
explicit routing, and uses a REPEATABLE READ READ ONLY transaction with an
exported snapshot. It inventories every non-system schema, table, partitioned
table, sequence and materialized view, not only Django models or `public`.
Missing schema usage, table/sequence SELECT, or active RLS causes a refusal.

The unfiltered custom-format `pg_dump` imports that same snapshot and preserves
ownership and ACL metadata. `create_complete_archive` calls `create_archive` with
`preserve_authority=True`, omitting `--no-owner` and `--no-acl`. The historical
`create_archive(..., preserve_authority=False)` default and PCA callers keep their
existing flags and behavior. After the existing SHA-256, size and `pg_restore --list` checks, the new verifier requires
all expected TABLE DATA, SEQUENCE SET and populated MATERIALIZED VIEW DATA
entries. Table data is matched by source OID. PG18 uses synthetic OID zero for
sequence state entries, so those entries use exact schema/name/owner identity.
For sequences/materialized views, identifiers outside ASCII SQL identifier
syntax are rejected rather than parsed ambiguously. A missing TOC entry deletes
the newly created archive. Failures never print `BACKUP_OK` or run retention.

Limitations deliberately fail closed:

- Foreign table data and large objects require a separately reviewed verifier;
  they currently stop deployment backup rather than being silently omitted.
- Extension-owned data tables omitted by normal pg_dump will fail TOC coverage.
- Names ambiguous in the textual sequence/materialized-view TOC are refused.
- Sequence values have PostgreSQL's normal non-MVCC semantics, not a transactional
  snapshot guarantee. Quiesce writers when a stricter cutover boundary is needed.
- DDL must remain quiescent during deployment backup. Catalog snapshot/TOC checks
  are not a DDL coordination mechanism.

TOC coverage and readability are not proof of every data block's restorability.
`BACKUP_OK` does not claim a per-backup restore test. Complete archives now retain
the ownership and ACL information required for authority-aware restoration, but
they do not contain cluster-global role definitions or authentication secrets.
Historical PCA archive/evidence contracts remain readability-only and unchanged;
they are not a substitute for the deployment completeness gate.

## Restore authority preconditions

A complete archive must be restored **without** `pg_restore --no-owner` or
`--no-acl`. Require `--exit-on-error`; missing roles or failed ownership/ACL commands
must stop recovery, not be worked around by granting private access to runtime.
Only restore a trusted archive using an independently authorized recovery identity.
The backup login remains read-only and is not the restore/migration identity.

Before restoring to another cluster, separately recreate and verify every
referenced owner, grantor and grantee, including `box1902_owner`, `polypbase`,
`box1902_migrator`, `box1902_approver`, `box1902_executor` and `box1902_backup`.
Required role attributes, memberships (including SET-only migrator membership),
authentication, HBA/ident rules and credentials are cluster configuration, not
part of a single-database `pg_dump`. Do not blindly replay first-install
provisioning against an existing restored database or grant owner/private rights
to runtime. Role reconstruction requires its own reviewed recovery procedure.

The isolated proof uses `pg_restore --create --exit-on-error --dbname postgres`
with all authority metadata enabled. `--create` restores database ownership and
ACL as well as schema/object metadata; restoring into an arbitrary precreated
`--dbname` alone does not establish those database-level properties. The fixture
temporarily renames its disposable source to free the original archived database
name, restores it, and renames the restored copy for comparison. This is test
lifecycle management, not an authorization or production recovery command.

The successful target is a fresh database in the same freshly initialized
cluster, so the independently provisioned source roles already exist. Catalog
comparison and contract attestation verify database/schema/relation/function
owners, schema/table/sequence/function/column ACLs, default ACLs, SECURITY DEFINER
flags and function configuration. Runtime denial checks and backup read-only
checks run again against the restored database. A second fresh cluster without
source roles verifies that the same real restore stops on the missing owner.
No successful cross-cluster role/authentication reconstruction is claimed.

## Isolated evidence, Windows PostgreSQL 18.6

From the repository root:

```text
.venv/Scripts/python.exe -m unittest discover -s backend/config -p test_database_backup.py -t backend -v
.venv/Scripts/python.exe deploy/tests/test_backup_postgres18.py --bin-dir "C:/Program Files/PostgreSQL/18/bin"
```

The integration command accepts binaries only, never an endpoint. It initializes
a fresh temporary UTF-8 cluster, binds a random loopback port excluding known
shared ports, disables dotenv and inherited database/backup overrides, verifies
server version and the newly created data directory, and stops/removes the cluster
on exit. It runs the real pre-0018 Django migrations, actual role provisioning,
then actual 0018 through the runner's embedded execution logic as the genuine
SCRAM-authenticated SET-only migrator and attests the topology. It also exercises
a real subsequent installed-app migration. Only the runner's checkout guard is
replaced in that fixture; shell/OS delivery is not proven by this test.

Verified with populated synthetic private provenance and zero/null sentinels:

- normal unchanged backup invocation and explicit runtime invocation refuse;
- dedicated read-only login succeeds using real pg_dump/pg_restore and TOC checks;
- removing SELECT on one private table refuses and preserves old archives;
- an RLS table refuses even when its SELECT policy allows every row;
- real `--create` restore with ownership and ACLs enabled preserves all 133
  tables/sequences (including the subsequent migration fixture), 18 function
  definitions and 20 trigger definitions, all three private rows and zero/NULL;
- normalized catalog owners and ACLs match, including column/default ACLs,
  SECURITY DEFINER flags and function configuration; restored topology attests;
- restored runtime cannot assume owner, disable triggers, create public tables,
  read or modify private provenance; restored backup login can read provenance
  but cannot update public data; no runtime grants are added by the test;
- restoring into a second independently initialized PG18 cluster without the
  source roles fails on missing `box1902_owner` with `--exit-on-error`.

The local proof substitutes only the Debian root-owned client resolver with
explicit version-checked local binaries. PostgreSQL commands, connections,
provisioning, migration, snapshot, inventory and restore are not mocked.
Debian root ownership enforcement is unit tested; real Unix peer/HBA/ident,
systemd, the runner's OS/snapshot preflight and production delivery integration
are **NOT RUN** on Windows. The latest embedded runner Python is loaded verbatim
and its SHA-256 is printed by each run. Shell/preflight execution is not implied
by passing its SQL identity, planning and migration execution checks.
No VM, Neon, production, copied production data or shared database is used.
