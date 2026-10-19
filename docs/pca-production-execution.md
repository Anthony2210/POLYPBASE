# Explicit PCA production execution

This is an operator runbook, **not authorization to run any command**. No PCA
correction runs during deployment, migration, planning, backup, or temperature
import. Deployment of reviewed code and authorization of a production correction
are separate decisions. Never execute uncommitted code on the VM.

## Boundaries

- Existing `unify_pca_identity` and its public service functions remain strictly
  limited to the independently verified disposable PostgreSQL QA harness.
- The separate `pca_production` command requires an explicit action and
  `--production`. There is no automatic transition from plan to apply.
- The production path uses `config.settings`, PostgreSQL, `DEBUG=False`, one
  database alias, no routers, and the existing application credentials. It runs
  **locally on the Debian server**, against its local PostgreSQL endpoint, without
  tunnels, remote databases, Neon, service files, or inherited `PG*` variables.
  The existing QA backend can exercise this path only after its own independent
  target/role/system-identifier checks. It cannot authorize a production database.
- All actions require explicit organization **1** and an active user with an
  active administrator membership in that organization. Apply rechecks and locks
  those rights through the reviewed merge service.
- No new production grants are needed or made. A privileged or mismatched login
  is a refusal, not a reason to grant privileges or run as `postgres`.

## Independent target attestation

Production accepts only **`/etc/polypbase/pca-production-target.json`**, pinned in
code outside the repository. There is no CLI or environment override of this trust
source. A separate privileged operator must independently verify the physical
server, local PostgreSQL endpoint, database and application login, then create the
root-owned attestation using the procedure below. Do not promote an application-
or deployment-runtime-generated file to trusted status without that independent
verification. A hash, a nonempty reference or runtime ownership is not evidence
of an independent source.

`attestation_ref` has been removed. The exact `pca_target_v2` artifact contains
these fields, with no defaults. Version 1 attestations and their prior plan hashes
are no longer accepted; generate and review a new read-only plan:

| Field | Source / type |
| --- | --- |
| `schema` | Literal `pca_target_v2` |
| `machine_id` | Independently checked `/etc/machine-id`; 32 lowercase hex characters |
| `configured_host` | Existing application DB host, numeric loopback or `localhost` |
| `configured_port` | Existing application DB port, string |
| `database` | Independently verified application database name |
| `role` | Independently verified application login, not a privileged maintenance user |
| `database_oid` | Actual database OID, integer |
| `role_oid` | Actual login OID, integer |
| `server_address` | Actual canonical numeric loopback server address |
| `server_port` | Actual server port, integer; must match configured port |
| `postmaster_started_at` | Actual server start instant, exact UTC format from SQL below |
| `server_version_num` | Actual server version number, integer |

From the separately established connection to the exact application endpoint and
login, these ordinary metadata reads provide the live SQL fields without
`pg_control_system()` or additional grants:

```sql
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SELECT jsonb_build_object(
    'database', current_database(),
    'database_oid', d.oid::bigint,
    'role', session_user::text,
    'role_oid', r.oid::bigint,
    'server_address', host(inet_server_addr()),
    'server_port', inet_server_port(),
    'postmaster_started_at', to_char(
        pg_postmaster_start_time() AT TIME ZONE 'UTC',
        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'server_version_num', current_setting('server_version_num')::integer
)
FROM pg_database d, pg_roles r
WHERE d.datname = current_database()
  AND r.rolname = current_user
  AND current_user = session_user;
COMMIT;
```

Combine these fields with the separately verified machine/configuration fields
and review the resulting artifact. Do not include a password, connection URL,
`.env`, production source files or production data in the repository.

Each command compares the configured parameters, actual libpq address/port/name/
login, SQL database/login OIDs, server endpoint, startup instant and version,
and the local machine ID against that artifact. It also requires identical
session/current roles, public schema only, origin replication mode and a primary
server. Superuser, CREATEDB, CREATEROLE, REPLICATION, BYPASSRLS, or membership of
privileged `pg_*` roles are refused (`pg_database_owner`, the ordinary implicit
ownership role, is excluded from that rejection).

This identifies the **exact local server/database boot instance**, not a permanent
cluster identifier. A restart, role/database recreation, version change or target
change invalidates the attestation and the reviewed plan. Reattest independently
and obtain new review; never edit identity values just to bypass a refusal.

### OS authentication and protected artifact paths

The target source is authenticated by the Linux OS ownership boundary, not by
its contents or hash. The attestation must be a root-owned regular file with one
link, no group/world write permission, and no symlink at any path component.
Every ancestor through `/`, including `/etc` and `/etc/polypbase`, must be a
root-owned directory without group/world write permission. The runtime may read
this source but may neither edit it nor replace its directory entry. Every target
check reloads this source and requires the supplied target to match it exactly,
including checks inside the locked application transaction.

The runtime must have equal nonzero real/effective/saved/filesystem UIDs, zero
inheritable/permitted/effective/ambient Linux capabilities, and `NoNewPrivs=1`.
The initial user-namespace UID mapping is required: an unprivileged user namespace
must not manufacture an apparently root-owned source. These values are read
fresh from procfs, not environment variables. Production is Linux-only. Invoke
the existing command through Debian's `setpriv --no-new-privs --` as shown below;
this drops escalation ability and does not grant a new privilege.

For plan files, backup manifests and archives, all ancestors must be owned by
root or the current runtime UID and must not be group/world writable. The final
artifact directory must be **exactly mode `0700`**. File inputs must be regular,
single-link, root/runtime-owned files without group/world write permission.
Creation is exclusive mode `0600`; missing parents are not created by the
production orchestration. Symlinks, `..`, noncanonical or relative paths are
refused rather than resolved. Descriptor-relative `O_NOFOLLOW` traversal retains
all ancestor descriptors and rechecks their identities. File reads check both the
open file and its directory entry for mutation/replacement. Backup creation and
archive validation retain the original directory bindings across external tools,
so a replaced ancestor is refused even if the archive inode is preserved.

This protects against other OS identities and observed replacement during access;
it is not a defense against hostile root, modified deployed code, or a fully
compromised runtime UID after a completed check. The service account must not
have root-equivalent sudo, mount or other delegated administration authority.
Use the trusted local Linux/procfs/filesystem architecture, not a runtime-controlled
container/user namespace or filesystem that fabricates ownership. Hashes still
provide plan/archive integrity, not independent human review authentication.

### Privileged operator setup — separate authorized operation

**These commands are a future operator procedure, not commands run by this task.**
No real production identity has been supplied, created or verified here.

1. A separately authorized privileged operator uses trusted server inventory and
   an independently established read-only SQL session to collect the exact fields
   above. Confirm the application login, configured host/port and machine ID
   independently; do not copy `.env`, credentials or connection URLs into a file.
   Construct the complete version 2 JSON in a root-private staging file using a
   root-owned editor/session, for example `/root/pca-production-target.reviewed.json`.
   Set its owner to root, mode `0600`, and verify its contents against the independent
   observations. Keep the operator's verification record separately; it is not an
   `attestation_ref` bypass in the JSON.
2. Audit `/`, `/etc`, the intended `/etc/polypbase` directory and existing artifact
   ancestors with `namei -l` and `stat`. Refuse symlinks, unexpected owners, writable
   ancestors or delegated permissions. Independently confirm that runtime user
   `polypbase` cannot become root or write/replace this protected tree. Do not use
   the runtime account to create or install the attestation.
3. In that independently authorized root session, install a **new** attestation.
   The shell must stop on a failed check. These commands intentionally refuse an
   existing destination rather than silently replacing an active attestation:

   ```sh
   set -eu
   umask 077
   test ! -L /etc/polypbase
   install -d -o root -g root -m 0755 /etc/polypbase
   test ! -e /etc/polypbase/pca-production-target.json
   test ! -L /etc/polypbase/pca-production-target.json
   python3 -m json.tool /root/pca-production-target.reviewed.json >/dev/null
   install -T -o root -g root -m 0644 \
     /root/pca-production-target.reviewed.json \
     /etc/polypbase/pca-production-target.json
   namei -l /etc/polypbase/pca-production-target.json
   stat -c '%u:%g %a %h %n' /etc/polypbase/pca-production-target.json
   ```

   The final file should report owner `0:0`, mode `644`, link count `1`.
   The independent operator must also confirm all ancestors have the protected
   root ownership/modes; the application enforces these checks again. Existing
   attestation rotation after a server restart requires a separately authorized
   root operation, new independent verification, and a new reviewed plan. Never
   rotate it merely to make a failed live check pass.
4. Separately prepare private runtime artifact directories, using the existing
   service identity and audited ancestor paths. For the deployment defaults:

   ```sh
   install -d -o polypbase -g polypbase -m 0700 \
     /srv/polypbase/deployments/pca /srv/polypbase/backups/pca
   namei -l /srv/polypbase/deployments/pca
   namei -l /srv/polypbase/backups/pca
   ```

   Audit existing ancestors before creating these directories; do not follow
   pre-existing symlinks or loosen unrelated permissions. The dedicated backup
   subdirectory avoids changing normal deployment backup permissions or retention.
5. Leave the privileged session. Run plan/backup/apply as the unprivileged service
   user with `setpriv --no-new-privs --`, never as root. A missing or invalid pinned
   attestation fails closed before database access. Keep independent review and
   explicit apply authorization as separate requirements.

## Operator commands

The following are **templates for a separately authorized future production
operation**. They were not executed against production. From the deployed
application's `backend` directory, replace `ACTOR_PK`, `REVIEWED_SHA256` and
`INDEPENDENT_REVIEW_REFERENCE` with independently approved values. The application,
backup and deployment paths below follow the existing deployment defaults; use
the actual approved artifact paths if those defaults were overridden. The target
path itself is pinned and cannot be overridden. Complete the separate privileged
setup above first; no real attestation is assumed to exist.

### 1. Actual read-only plan

```sh
setpriv --no-new-privs -- ../.venv/bin/python manage.py pca_production plan --production \
  --organization-id 1 --actor-id ACTOR_PK \
  --target /etc/polypbase/pca-production-target.json \
  --plan-file /srv/polypbase/deployments/pca/plan.json
```

The data snapshot is built in a real PostgreSQL `REPEATABLE READ, READ ONLY`
transaction. No audit, receipt, namespace counter or scientific row is written.
The private output file contains the full exact baseline, before/after column
changes, dependency inventory, blockers, target identity and expected outcome:

- retain canonical Box **2135** and retire Box **2141**;
- **94 canonical measurements** (92 moved + two unchanged);
- **18 evidence-retained duplicates**, for 112 retained measurement rows total.

The command prints a deterministic SHA-256 `plan_hash` over the canonical JSON
of the entire production artifact, including target identity. This is **not** the
SHA-256 of the pretty-printed file. A blocked plan is still emitted for inspection,
but the command exits unsuccessfully and backup/apply refuse it. Files are never
overwritten: use new explicit filenames for a new plan.

Review this actual plan independently, including preservation of recorded dates,
zones, source evidence, authors, zero/NULL values and institution isolation.
Record the exact approval hash and an independent review reference. A planning
request is not apply authorization.

### 2. Fresh, target-bound backup

```sh
setpriv --no-new-privs -- ../.venv/bin/python manage.py pca_production backup --production \
  --organization-id 1 --actor-id ACTOR_PK \
  --target /etc/polypbase/pca-production-target.json \
  --plan-file /srv/polypbase/deployments/pca/plan.json \
  --expected-plan-hash REVIEWED_SHA256 \
  --backup-dir /srv/polypbase/backups/pca \
  --backup-evidence /srv/polypbase/deployments/pca/backup.json
```

Backup creation rechecks the target, actor and exact live plan inside a new
read-only repeatable-read transaction. It exports that snapshot and keeps the
transaction open while `pg_dump --snapshot` creates the custom-format archive.
The dump uses the same database/login, with `PGHOSTADDR` pinned to the verified
numeric endpoint while retaining the configured host for TLS verification.
Inherited libpq overrides are scrubbed. Any command failure stops the workflow;
failed archive creation removes its incomplete file.

`config.database_backup` is shared with the existing
`deploy/scripts/backup_database.py`; normal deployment backup output and retention
remain compatible. The PCA action adds a private evidence manifest containing:

- verified target identity and exact approved production plan hash;
- archive absolute path, size and streaming SHA-256;
- UTC verification timestamp and manifest integrity hash;
- `verification: archive_readability_only`, `restoration_tested: false`.

Both creation and apply check a nonempty regular nonsymlink archive, its size and
SHA-256, and successful `/usr/bin/pg_restore --list`. Apply requires evidence no
more than **one hour old**, not future-dated, matching the exact target and plan.
An arbitrary backup reference string, missing file, corrupt/expired archive or
mismatched manifest never qualifies. Commands are bounded to 600 seconds.

**Archive verification is not a tested restoration.** No database is restored by
this workflow. A restoration rehearsal requires a separately authorized isolated
restoration procedure; do not mark this manifest as restoration-tested.

### 3. Separately authorize and apply

Obtain explicit authorization immediately before this production-changing step.
The authorization must name organization 1, canonical/donor PKs, the exact
production plan hash, validated fresh backup and independent review reference.
The reference must point to a real independent review of this exact artifact;
a nonempty CLI value is not a substitute for the human review process.

```sh
setpriv --no-new-privs -- ../.venv/bin/python manage.py pca_production apply --production \
  --organization-id 1 --actor-id ACTOR_PK \
  --target /etc/polypbase/pca-production-target.json \
  --plan-file /srv/polypbase/deployments/pca/plan.json \
  --expected-plan-hash REVIEWED_SHA256 \
  --backup-evidence /srv/polypbase/deployments/pca/backup.json \
  --review-ref INDEPENDENT_REVIEW_REFERENCE \
  --authorization 'APPLY_PCA_ORGANIZATION_1:REVIEWED_SHA256'
```

Apply rejects options belonging to another action, missing authorization/evidence,
blocked plans, changed targets, unsafe roles and unauthorized actors. It calls the
same reviewed locked merge implementation as QA. The live plan must exactly match
the approved baseline under its existing ordered locks before namespace
registration or scientific/audit writes. Target and backup validity are checked
again after lock waits. Lock/statement timeouts are 10/30 seconds.

All deltas and audit entries, including the final receipt, commit atomically or
roll back together. The receipt retains the original reviewed merge plan and
records the production approval hash, target, backup evidence, authorization,
review reference and actor. A matching retry replays the integrity-checked receipt
rather than merging again, even after later canonical edits. Production retries
still require the same valid target and fresh archive evidence; if those have
expired, inspect the existing audit receipt read-only rather than weakening gates
or attempting a new merge. Conflicting receipts are refused.

The merge does not fabricate movements, historical zones or observation dates,
rewrite original source evidence, normalize scientific zero to NULL, or reassign
historical authors. Existing temperature/dependency evidence blocks the operation
until separately reviewed; this command never starts a temperature import.

On any failure, stop and investigate. A rollback/restore is never automatic and
requires separate explicit authorization. After success, retain the private plan,
backup evidence and receipt, and perform the separately approved read-only
verification of the 94/18 outcome and affected application views.

## Focused validation

For this security correction, run only the affected production workflow module
through the established disposable PostgreSQL harness, never application settings:

```sh
python scripts/run_pca_qa.py --run apps.cultures.test_pca_production
```

The new database fixtures explicitly verify the existing disposable QA gate.
They never substitute SQLite or a shared database for PostgreSQL proof. Windows
QA may set `POLYPBASE_PCA_QA_PG_BIN` solely to map the fixed production pg tools to
installed test binaries; production code does not consult that variable.
Connection-free security tests model Linux ownership, procfs, descriptor-relative
opens, symlinks and replacement races without writing a real root-owned attestation:

```sh
# From backend with project dependencies installed
python -B -m unittest config.test_pca_protected_artifacts -v
```

The unaffected archive-helper and existing merge suites remain available but need
not be rerun for this attestation-only correction.

The original pre-security-review baseline used separately provisioned, fresh PostgreSQL
18.6 clusters inside the worktree's ignored `.local-docs` directory, bound only to
fresh loopback ports, with restricted synthetic owner/runtime logins, the existing
QA backend and exclusive lease. Results across bounded batches: **14 production
refusal/workflow tests, 42 existing PCA guard/merge/concurrency tests, and 19
archive helper tests passed; 0 failures, 0 skipped**. The archive tests in the
production workflow used real `pg_dump`/`pg_restore`. An earlier combined batch
hit its timeout; the final three batches completed independently. No production,
VM, Neon or shared database was contacted; no actual PCA correction or temperature
import was performed. No production restoration has been tested by these results.

After the attestation security correction, only affected tests were run:
**21 production refusal/workflow tests passed** on a new disposable PostgreSQL
18.6 cluster, and **55 protected-artifact security tests passed**, with zero
failures or skips. These include self-declared-source refusal, trusted-source
acceptance, unsafe owners/ancestors, symlinks, hard links, namespace/capability
refusal, directory replacement preserving the archive inode, and live target
mismatch. The old 42-test merge suite and 19-test archive suite were not rerun.

The Linux filesystem tests use explicitly simulated syscalls on Windows, not a
claim of real Linux kernel validation. A local WSL availability check timed out;
no native Linux smoke-test result is claimed. The documented privileged setup
and a native Linux acceptance check remain required before a future production
operation. No production attestation has been installed or validated by this task.
The disposable cluster was stopped and removed.
