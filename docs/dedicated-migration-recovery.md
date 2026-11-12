# Dedicated migration control and release recovery

This is an operator procedure, **not authorization to execute it**. No production
operation was performed while implementing it. Obtain approval immediately before
each production mutation. Never use a production database for these tests.

## Two independently reviewed commits

- `CONTROL`: full Git SHA containing **all** new control scripts, backup helper,
  tests and documentation, integrated and independently reviewed. Untracked files
  cannot be delivered by the exporter. Nothing in this working tree is a release.
- `TARGET`: exact clean application checkout whose migrations are approved.
  For the initial partial release, resolve the full SHA for `8504f52` and use it as
  TARGET; CONTROL will be a different, later commit. Do not advance the production
  checkout just to obtain the runner. A later ordinary migration uses the already
  prepared target release as TARGET.

A Git hash identifies reviewed bytes; it does not prove who reviewed them. The
operator must establish CONTROL through the authenticated repository/review channel.
An archive and the manifest shipped beside it do not authenticate themselves.
Never obtain the expected manifest hash from the receiving machine.

## 1. Build the control bundle on the trusted workstation

After integration/review, use a clean checkout of CONTROL. Inspect `git status`,
`git diff` and the independent review first. Substitute approved values below;
angle-bracket values are deliberately not executable defaults.

```powershell
$control = Read-Host 'Full independently reviewed CONTROL SHA'
if ($control -cnotmatch '^[0-9a-f]{40}$') { throw 'Invalid CONTROL' }
if ((git rev-parse HEAD).Trim() -cne $control) { throw 'Wrong checkout' }
if (git status --porcelain --untracked-files=all) { throw 'Dirty checkout' }
python deploy/scripts/export_migration_control.py $control migration-control --repository .
if ($LASTEXITCODE -ne 0) { throw 'Export failed' }
(Get-FileHash migration-control/SHA256SUMS -Algorithm SHA256).Hash.ToLowerInvariant()
```

Keep the printed `manifest_sha256` in the independently approved operator record,
alongside CONTROL, TARGET and reviewer identity. The exporter reads exact blobs
from CONTROL, not the worktree/index, and refuses a missing/untracked script.
Transfer the directory using the organization's authenticated SSH/SFTP connection
with the host key pinned to the existing deployment trust configuration. Do not
accept a new host key opportunistically. Example with OpenSSH, **only if its
known_hosts entry was independently checked against the existing pinned key**:

```powershell
scp -o StrictHostKeyChecking=yes -r migration-control aquariumparis@polypbase.org:/home/aquariumparis/
if ($LASTEXITCODE -ne 0) { throw 'Transfer failed' }
```

The transfer is preparation, not permission to migrate. Never use a web-writable
bundle for privileged execution. A root operator installs a new copy into a fresh
root-owned directory outside the app, then verifies the independently recorded
manifest hash and every file **after installation**, before using any script:

```bash
# Run as the separately authorized root operator. Stop if this destination exists.
install -d -o root -g root -m 0755 /opt/polypbase-controls
mkdir -m 0755 /opt/polypbase-controls/<CONTROL>
cp -R /home/aquariumparis/migration-control/. /opt/polypbase-controls/<CONTROL>/
chown -R root:root /opt/polypbase-controls/<CONTROL>
find /opt/polypbase-controls/<CONTROL> -type d -exec chmod 0755 {} +
find /opt/polypbase-controls/<CONTROL> -type f -exec chmod 0644 {} +
cd /opt/polypbase-controls/<CONTROL>
printf '%s  SHA256SUMS\n' '<independently-recorded-manifest-sha256>' | sha256sum --check --strict - &&
sha256sum --check --strict SHA256SUMS
```

Stop on any failure, unexpected entry, link, writable ancestor, default/extended ACL
or mismatched CONTROL_COMMIT. Use only the named verified files; never execute
extra transferred files. Recheck the pinned manifest/files before each use. This
delivery neither modifies application source nor changes its HEAD.

## 2. Install the separate deployment backup broker

Normal deployment must not give the web OS account credentials for private data.
The root-installed broker runs under **OS `polypbase_backup`**, authenticates as
**database `box1902_backup`**, and returns only a verified archive path. The web
identity cannot read its credentials or archives, select another database, choose
code paths or pass environment overrides. It may request another backup of the
currently root-approved target; this is not DDL or private-read authority.

The full installation contract, JSON schema, paths and sudo rule are in the
verified `deploy/scripts/trusted_deployment_backup.py` docstring. Execute those
installation commands only after separately approving OS package/account/config
changes. Use the verified bundle as their source, never the app checkout. In short:

1. Provision isolated system account/group `polypbase_backup`, no login, no shared
   UID/group, no supplementary groups or sudo rights. Install the reviewed broker
   root:root 0755 at `/usr/local/libexec/polypbase-deployment-backup`.
2. Install the bundle's `database_backup.py` and `backup_database.py` root:root
   0644 under `/usr/local/lib/polypbase-deployment-backup`. Use system Python with
   independently approved system Django/psycopg dependencies, never the web venv.
3. Create root-owned `/etc/polypbase/deployment-backup.json`, group
   `polypbase_backup`, mode 0640, with `approved_commit=TARGET`, the independently
   checked SHA-256 of each installed file and the explicit database configuration.
   Do not copy `.env`. Pin the backup helper from CONTROL, including when TARGET
   is 8504f52. Ancestors must be root-owned and not group/other writable.
4. Archive directory `/srv/polypbase/deployment-backups` is backup-owned 0700;
   ancestors are root-owned. Never make archives web-readable for verification.
5. DBA authorizes the existing provisioned read-only `box1902_backup` login through
   peer **only from OS `polypbase_backup`**, using the explicit ident mapping in
   the broker contract, or a separately protected password in its private JSON.
   No trust route, role membership or peer mapping from OS/runtime `polypbase`.
   Provisioning already grants the required reads; do not broaden runtime grants.
6. Install/validate the exact `NOSETENV` sudo rule from the broker contract. No
   general interpreter, shell, editor or environment permission is allowed.
7. Test the installed broker as the deployment OS user with TARGET. Require exactly
   `BACKUP_OK /srv/polypbase/deployment-backups/pre-<target8>-<timestamp>.dump`.
   A root/backup operator independently checks hash/TOC and archives the evidence.

Every release needs an explicit root repin of TARGET and reviewed helper hashes.
The normal deployment fails **before Git fast-forward** if the broker is absent,
unapproved, altered, cannot authenticate, cannot read any schema/data, or fails
archive completeness/readability checks. There is no runtime-backup fallback.
Nightly backups must also move to this isolated authority; the old web-identity
systemd invocation intentionally fails once private data is protected. See
[backup completeness and restore evidence](database-backup-completeness.md).

## 3. Initial 0018 only: role provisioning

Preserve the existing verified pre-provisioning backup. Separately review the
SQL emitted by the verified `box_1902_privileges.py provision` and obtain DBA
approval. Follow its existing strict provisioning procedure; this document does
not authorize its execution. A pre-provisioning backup uses the then-owner's
existing authority; broker authentication must be prepared before resuming normal
deployment after provisioning. Do not reprovision a partial topology.

After provisioning, independently verify runtime ownership/ACL restrictions and
actual authenticated migrator login. Do not run Django migrations as a superuser,
fake migrations, edit `django_migrations`, weaken guards or give runtime DDL.
Provisioning is not repeated for later migrations.

## 4. Capture and independently review the dedicated plan

Use the dedicated nonroot OS account/group **`polypbase_migrator`**, never the
web OS identity, without shared UID, primary or supplementary web groups. Give
it access to the existing deployment lock and a private migration review/backup
directory through narrowly reviewed ACLs, not membership in a web group.

**Do not use the live checkout or its `.venv`.** Before introducing credentials,
a root operator installs an independent authenticated Git clone of TARGET under
`/opt/polypbase-migrations/<TARGET>`, on branch main, with all tracked bytes matching
TARGET. Clone from the authenticated repository with the existing repository trust
configuration, not from web-owned working files. Install frozen dependencies from
TARGET's reviewed lockfile using a trusted installer into that snapshot's `.venv`;
all files, ignored bytecode/site hooks, executable targets and ancestors must be
root-owned and not group/other writable. Do not reuse a web-owned uv cache, editable
external package, Python startup path, `.env` or symlink into the live checkout.
Dependency installation is a separately approved operation, not part of migration
execution. A fresh independent virtualenv is required, not `chown` of an untrusted
existing environment.

Example root preparation, after authentication of the repository URL and TARGET:

```bash
install -d -o root -g root -m 0755 /opt/polypbase-migrations
# Use the independently authenticated repository URL, never a web-owned clone.
git -c core.hooksPath=/dev/null clone --no-checkout <authenticated-repository-url> /opt/polypbase-migrations/<TARGET>
git -C /opt/polypbase-migrations/<TARGET> -c core.hooksPath=/dev/null checkout -B main <TARGET>
# From this snapshot, use an independently trusted uv installation/cache:
cd /opt/polypbase-migrations/<TARGET>
uv sync --frozen --no-cache --no-editable
```

Independently inspect source, lockfile, installed dependency provenance and root
ownership/ACLs before allowing the migration account to execute them. The runner's
system-Python preflight checks the complete snapshot/control trees **before**
starting snapshot Python; ignored `.venv`/`.pth`/bytecode are not hidden by Git
status. Ownership checks do not prove package provenance: independent installation
and review remain essential. Do not relax the preflight to run on web-owned paths.

Supply explicit POSTGRES database/host/port and independently protected migrator
secret only to this secure execution environment. The snapshot has no copied app
`.env`; inspect the configured endpoint read-only first. Missing runtime settings
must be supplied from the operator's protected configuration, not guessed.

```bash
export POSTGRES_USER=box1902_migrator
export PGOPTIONS='-c role=box1902_owner'
export POLYPBASE_MIGRATION_CONTROL_DIR=/opt/polypbase-controls/<CONTROL>
export POLYPBASE_MIGRATION_APP_DIR=/opt/polypbase-migrations/<TARGET>
/bin/bash /opt/polypbase-controls/<CONTROL>/deploy/scripts/run_dedicated_migrations.sh \
  --plan <TARGET> /srv/polypbase/migration-reviews/<unique-plan>.json
```

Set POSTGRES_HOST explicitly to the approved TCP/TLS endpoint; the script refuses
peer/socket migrator authentication. Require a genuine migrator login, current
owner role and `session_replication_role=origin` on the same physical execution
connection. Do not replace this with admin impersonation. Planning never applies
migrations and refuses to overwrite an earlier plan.

The independent human reviewer inspects the whole JSON plan, corresponding Git
migration sources/custom operations and their preconditions, lock/downtime needs,
reversibility and backup/restore boundaries. Initial 0018 is reviewed separately
from the scientific correction: this procedure never authorizes that correction.
The reviewer records TARGET and the SHA-256 of the **exact JSON bytes**. The
operator explicitly decides whether to approve them. Hash checks are integrity
checks, not automated proof of human approval or safety.

No pending migrations means no execution/approval is applicable. Stop on an
unexpectedly empty/missing 0018 plan and verify the database and history read-only.
For future releases, every pending migration requires this same independent review,
even CreateModel or an innocuous custom description. No pattern-based exemption.

## 5. Authorized execution and failure/retry

After explicit approval, supply the separate read-only backup login to the trusted
migration environment (`POLYPBASE_BACKUP_USER=box1902_backup` and its independently
protected credential if needed). It must not reuse the migrator's role/options,
password file or routing. Do not expose any credential in shell history, command
arguments, web environment or repository. Reverify bundle pins, then:

```bash
/bin/bash /opt/polypbase-controls/<CONTROL>/deploy/scripts/run_dedicated_migrations.sh \
  --apply <TARGET> /srv/polypbase/migration-reviews/<unique-plan>.json \
  <TARGET>:<independently-approved-json-sha256>
```

Both modes hold the deployment lock. Apply verifies a **new complete backup**,
rebuilds the plan, compares exact bytes with `cmp`, rechecks SHA-256 and clean exact
Git identity immediately before execution, and checks the same physical database
connection's identity. No implicit approval or silent application. Wrong/missing
approval, changed code/plan/history, conflicting graph or missing privilege stops.
Require `MIGRATION_BACKUP_OK`, `MIGRATION_REVIEW_APPROVED` and `MIGRATION_COMPLETE`;
then independently attest privileges and migration history read-only.

On failure stop; preserve the plan, backup identifier, commit and protected logs.
Do not automatically restore or reverse anything. Diagnose whether Django applied
none, some or all migrations. A changed pending set needs a new plan and human
approval; an already completed plan rejects stale approval. Resume only with new
explicit authorization. The protocol is not a transaction spanning all migrations.
Non-atomic/data migrations require their own failure recovery review.

## 6. Normal deployment continuation and future migrations

Run the existing `deploy/deploy_vm.ps1` for the reviewed integrated release. Its
backup broker must be pinned to that exact release first. Do **not** pass the old
plan approval to deployment: its plan is now empty and its text format differs.
It takes another verified complete backup, preserves its usual fast-forward,
locked dependency/build/static/publication/service/health interlocks, and runs only
`migrate --check`. Pending migrations stop before frontend publication/restart.

For a future target with pending migrations: stop at that boundary, keep the clean
prepared target checkout, repeat steps 1–2 and 4–5 for the newly reviewed controls
and target, then rerun normal deployment. Never restore automatic runtime migrations.
A new backup is required on every retry. No production checkout advancement is
needed to deliver new controls, including recovery of the 8504f52 partial release.

## Explicit stop conditions and validation boundary

Stop on unavailable approved source, any hash/identity/ACL mismatch, insufficient
backup access, unknown RLS/foreign/large-object behavior, unexpected pending set,
concurrent operation, missing lock access, stale approval or any failed check.
Do not infer scientific correctness from successful DDL or backup restoration.

Windows validation covers real disposable PG18 logins, 0018, future migration,
private-data backup/restore and local guard tests. Real Debian sudo/UID/ACL/peer
configuration, root-owned client/broker installation, systemd, TLS transport and
lock contention are **NOT RUN** here. Before production approval, independently
exercise the complete installed operator workflow on isolated Debian PostgreSQL 18.
This document is not a claim that the Linux end-to-end workflow passed.
