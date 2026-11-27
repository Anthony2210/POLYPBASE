# Deploying Polypbase on the VM

This guide describes the version-controlled deployment procedure. The VM inventory
is a historical reference supplemented by dated observations, not a real-time
report. Never add secrets or private credentials to the repository.

## VM inventory and version-controlled units

| Item | Status |
|---|---|
| System | Debian GNU/Linux 13, `aquariumparis01` |
| Repository | `/srv/polypbase/app`, branch `main` |
| Service account | `polypbase` |
| Backend | Django served by Gunicorn on `127.0.0.1:8000` |
| Frontend | Vite build served by Nginx |
| Nginx | Active; public ports 80 and 443 allowed by UFW |
| Production PostgreSQL | Version 18, cluster `18/main`, local port 5433 |
| Active database | `polypbase`, local role `polypbase`, `peer` authentication |
| Old local cluster | PostgreSQL 17, empty, still present on port 5432 |
| PostgreSQL clients | `/usr/bin/pg_dump`/`pg_restore` = Debian `pg_wrapper`; without `--cluster`, `/usr/bin/pg_restore --version` selects 17.10. Version 18 clients: `/usr/lib/postgresql/18/bin/` (required by the version-controlled backup procedure) |
| Domain | `polypbase.org` and `www.polypbase.org` point to the VM |
| HTTPS | Active on both names, with HTTP redirected to HTTPS |
| Deployment | Automated and controlled by `deploy/deploy_vm.ps1` |
| iMinilide collection | Version-controlled `polypbase-iminilide.timer` and `.service`: cloud collection every 10 minutes. Installation and active status have not been verified here; see [iMinilide collection](#exploitation-des-sondes-iminilide). |

To check whether the services in the inventory are active and enabled, use the
following read-only checks:

```bash
systemctl is-active polypbase nginx postgresql
systemctl is-enabled polypbase nginx postgresql
```

The frontend responds over HTTPS on the public domain. Gunicorn and both
PostgreSQL clusters listen only on the local interface.

## Cleaned historical database

A full Neon backup was created with PostgreSQL 18, checked with
`pg_restore --list`, and then restored to an isolated database. Cleanup was
applied only to that copy, never to Neon.

Verified final state:

- 1 organization: Aquarium de Paris
- 2 accounts retained: `admin` and `antho_ca`
- 6 thermal zones
- 555 historical boxes
- 1,391 historical location periods
- 36,779 historical biological readings
- no test data from after the July 3, 2026 import
- no test movements, subcultures, transfers, temperature readings, or alerts
- one audit entry recording the restore and the source backup fingerprint

The scripts have safeguards: cleanup refuses to run if the database name or
expected counts do not match the audited snapshot.

```bash
cd /srv/polypbase/app/backend

../.venv/bin/python ../deploy/scripts/clean_staging_database.py
../.venv/bin/python ../deploy/scripts/verify_staging_database.py \
  --expected-database polypbase
```

The first command is a dry run. Use `--apply` only on `polypbase_staging` and
only after taking a new backup.

## Retained backups

Backups are stored in `/srv/polypbase/backups`, with access restricted to the
`polypbase` account:

- original Neon backup
- copy of the staging database before cleanup
- cleaned backup used to create the production database
- historical daily production backups from the old backup service

The deployment backup broker's private archives are separate:
`/var/lib/polypbase/deployment-backups` must be under a root:root ancestor with
mode 0755, and the directory itself must be owned by
`polypbase_backup:polypbase_backup` with mode 0700. Never make these archives
readable by `polypbase`. The broker prunes its own archives after each verified
backup; see [deployment backup retention](#deployment-backup-retention).

The `polypbase-backup.timer` timer runs the isolated broker each night at about
2:30 a.m., with up to 15 minutes of randomized delay. The service passes no
commit, so the broker writes `nightly-<YYYYMMDDTHHMMSSZ>.dump` in the private
directory. Release backups keep the `pre-<8-char-sha>-<YYYYMMDDTHHMMSSZ>.dump`
name. Both timestamps are UTC. See the [one-time cutover](#one-time-root-cutover-for-backup-approval).

The operational script `deploy/scripts/backup_database.py` now requires
`/usr/lib/postgresql/18/bin/pg_dump` and `pg_restore`, with no fallback to `pg_wrapper`.
Both clients and their parent directories must be owned by root, must not be
symlinks, and must not be writable by the group or others. The binaries must be
executable and report PostgreSQL 18. Any rejection stops the backup before the
archive is created. Confirm the presence and permissions of both clients on the
VM before the cutover. The old PCA campaign helpers are no longer part of this
procedure; see the [archived PCA folder](../archive/2026-10-pca/).

```bash
systemctl status polypbase-backup.timer --no-pager
systemctl list-timers polypbase-backup.timer --no-pager
journalctl -u polypbase-backup.service -n 50 --no-pager
```

With explicit operator approval, run a manual backup through the same isolated
service with `sudo systemctl start polypbase-backup.service` and check the journal
for the new `BACKUP_OK` line.

Arrange for an off-VM copy as well: a backup on the same disk does not protect
against complete machine failure.

## Django configuration

The file `/srv/polypbase/app/backend/.env` is owned by `polypbase` and has mode
`600`. The previous Neon configuration is kept in a protected copy on the
server to allow a controlled rollback.

The local database uses the following settings, with no network password:

```dotenv
POSTGRES_DB=polypbase
POSTGRES_USER=polypbase
POSTGRES_PASSWORD=
POSTGRES_HOST=/var/run/postgresql
POSTGRES_PORT=5433
POSTGRES_SSLMODE=disable
```

This authentication works because Django and PostgreSQL use the same local
account, `polypbase`. PostgreSQL is not accessible from the internet.

The expected public settings are:

```dotenv
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=polypbase.org,www.polypbase.org,<IP_VM>,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=https://polypbase.org,https://www.polypbase.org
PUBLIC_BASE_URL=https://polypbase.org
DJANGO_SECURE_SSL_REDIRECT=1
DJANGO_SECURE_HSTS_SECONDS=86400
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_DELIVERY_ENABLED=1
DEFAULT_FROM_EMAIL=Polypbase <noreply@polypbase.org>
EMAIL_HOST=mail.gandi.net
EMAIL_PORT=587
EMAIL_HOST_USER=noreply@polypbase.org
EMAIL_HOST_PASSWORD=<mot_de_passe_de_la_boite>
EMAIL_USE_TLS=1
EMAIL_USE_SSL=0
EMAIL_TIMEOUT=10
```

**Unresolved HSTS discrepancy (2026-11-04).** The 2026-11-03 CCA-EOB closeout
reports `HSTS_SECONDS=0` / `security.W004` in production, although both the
expected value above and the code default (`backend/config/settings.py`) are
`86400`. The `deploy/nginx/polypbase.conf` template also contains
`add_header Strict-Transport-Security "max-age=86400" always;`. The Django
warning describes the Django setting: it does not establish that the public
HTTPS response lacks the HSTS header, because Nginx may add it. The active HTTPS
configuration and the headers actually served were not rechecked here; the
template does not prove their current state. The cause and decision are still
undetermined: no configuration change is decided here. `check --deploy`
reports it as a warning without stopping the release.

## Automated deployment

Run the normal deployment from PowerShell at the root of a local repository
copy aligned with `origin/main`:

```powershell
.\deploy\deploy_vm.ps1
```

The command performs the following steps itself:

1. checks the `main` branch, Git status, and pushed commit;
2. runs Django checks, checks for missing migrations, runs backend tests, and builds the frontend;
3. verifies the VM's public SSH fingerprint;
4. transfers the version-controlled Linux runner and verifies its SHA-256 digest;
5. acquires a lock to prevent simultaneous deployments;
6. obtains a PostgreSQL backup verified by the trusted broker before the fast-forward;
7. updates Git strictly by fast-forwarding to the requested commit;
8. installs locked dependencies and builds the frontend in a separate staging directory;
9. runs Django checks, generates a text migration plan, and runs a read-only schema readiness check; collects static files only if no migrations are pending;
10. publishes the frontend, restarts Gunicorn, and validates Nginx;
11. checks health, React routes, HTTPS, and the commit actually being served.

### Deployment backup broker

The runner invokes the broker installed by root at
`/usr/local/libexec/polypbase-deployment-backup` via `sudo -n -u polypbase_backup`,
passing only the full target SHA as a filename label. The deployment runner
still checks the target against `origin/main`; the broker does not look up or
approve commits. It checks the SHA-256 of the two installed helpers against the
root-owned configuration on every run. Root only needs to repin the bundle when
its reviewed bytes change, independently of application releases. The broker
uses system Python and the dedicated peer-authenticated PostgreSQL login
`box1902_backup`, never the web checkout, its virtual environment, or its `.env`.
See the [broker installation contract](../../deploy/scripts/trusted_deployment_backup.py).

Before fast-forwarding, the release requires `BACKUP_OK` output with the expected
private path for that commit. The broker creates and checks the archive with the
PostgreSQL 18 clients; this attestation does not replace a restore test. Do not
give the web account access to the archive or secrets, or additional rights to
repeat the verification. The broker does not determine whether the schema is
ready and does not run migrations.

The script stops at the first failure. It never restores the database or
reverses a migration automatically. The remote log and previous frontend build
are retained under `/srv/polypbase`; the release backup stays in the broker's
private directory. Keep these for diagnosis or an explicitly approved rollback.

To run only the local checks without contacting the VM:

```powershell
.\deploy\deploy_vm.ps1 -PreflightOnly
```

### Deployment backup retention

The broker prunes `/var/lib/polypbase/deployment-backups` itself, as
`polypbase_backup`, and only after a backup was written and verified. A failed
backup deletes nothing. Each run prunes only the category it just wrote:

- nightly: keep the 14 most recent, plus the most recent nightly of each ISO
  week (UTC) for the last 8 weeks, meaning the current week and the 7 before it;
- pre-deploy: keep the 10 most recent.

Everything else in that category is deleted. Times come from the UTC timestamp in
the name, never from the modification time. The broker considers only regular
files owned by `polypbase_backup` whose names exactly match
`nightly-<YYYYMMDDTHHMMSSZ>.dump` or `pre-<8 lowercase hex>-<YYYYMMDDTHHMMSSZ>.dump`.
Older name formats, links, directories, and files of other owners are never
touched. If the backup just written is not the newest of its category (a clock
anomaly or a concurrent backup), it deletes nothing.

A pruning failure prints `RETENTION_ERROR` and keeps the backup just written.
Broker exit codes:

| Code | Meaning |
| --- | --- |
| `0` | Backup verified; retention done, or failed during a pre-deploy run. |
| `1` | `BACKUP_ERROR`: no verified backup; nothing was deleted. |
| `3` | Nightly run only: backup verified and kept, retention failed. |

A pre-deploy retention failure never fails the backup or the deployment. A
nightly one fails `polypbase-backup.service` with status 3, so `OnFailure=`
alerting can report it; check the `RETENTION` lines in the journal.

Preview the plan without deleting anything. Root runs it as the backup account;
the broker refuses root itself. It needs no database access and writes no backup.

```bash
runuser -u polypbase_backup -- /usr/local/libexec/polypbase-deployment-backup --retention-dry-run
```

The output has one `RETENTION keep` or `RETENTION would-delete` line per managed
file, each with its reason, then a `RETENTION_DRY_RUN kept=<n> would_delete=<n>`
summary. Unmanaged entries are only counted.

Every real run logs each kept and deleted file with its reason, then
`RETENTION_OK kept=<n> deleted=<n>`, on stderr. The release runner's stdout
contract (`BACKUP_OK <path>`) is unchanged. Nightly decisions are in the journal,
and pre-deploy decisions are in that release's log under `/srv/polypbase/deployments`:

```bash
journalctl -u polypbase-backup.service --since -30d -o cat --no-pager | grep -E '^RETENTION'
grep -h '^RETENTION' /srv/polypbase/deployments/deploy-*.log
```

The broker is installed by root, so retention reaches the VM only when root
installs it from a reviewed, root-controlled snapshot. On a VM that has not had
the [cutover](#one-time-root-cutover-for-backup-approval), it ships through
cutover step 2. For a later broker update after the cutover (explicit production
approval required), reuse the step 1 definitions and
`stop_timer_and_wait_for_backup`, save the current broker, install the new one,
preview, then restart the timer:

```bash
stop_timer_and_wait_for_backup || exit 1
saved=$(mktemp -d)
cp -p -- "$broker" "$saved/"
install -o root -g root -m 0755 deploy/scripts/trusted_deployment_backup.py "$broker"
runuser -u polypbase_backup -- "$broker" --retention-dry-run
systemctl start "$timer"
```

Review the plan before restarting the timer; to roll back, reinstall
`$saved/polypbase-deployment-backup` the same way. The broker is not part of the
SHA-256-pinned helper bundle, so the JSON stays unchanged. The systemd unit is
unchanged: `ProtectSystem=full` leaves `/var/lib` writable, and the account
already owns the archive directory.

### One-time root cutover for backup approval

Run only after explicit production approval, as root in Bash from an independently
reviewed, root-controlled snapshot (not `/srv/polypbase/app`). Pause deployments
until verification succeeds. Run the commands in order in the same Bash session
and stop on failure or unexpected output. Leave the pinned helper files, database
settings, peer mapping, sudo rule, archive directory, and timer schedule unchanged.

1. Confirm the base unit is a regular file at the path below and only the two
   named drop-ins apply. Review the drop-ins privately for any extra hardening
   or site-specific settings. If present, stop and reconcile them before removal.
   Confirm the timer is active and enabled. Privately confirm that the JSON's
   `approved_commit` is the exact legacy approval below, without exporting or
   copying the JSON. The check below also requires the live drop-in's `ExecStart`
   to use that SHA. Stop if either check fails.

   ```bash
   unit=polypbase-backup.service
   timer=polypbase-backup.timer
   fragment=/etc/systemd/system/$unit
   dropins=$fragment.d
   broker=/usr/local/libexec/polypbase-deployment-backup
   config=/etc/polypbase/deployment-backup.json
   legacy_approval=68ca288ef63a37340f3380e67835711be3ab885e

   check_legacy_exec_start() {
     local expected="^[[:blank:]]*ExecStart[[:blank:]]*=[[:blank:]]*$broker[[:blank:]]+$legacy_approval[[:blank:]]*$"
     if ! grep -Eq -- "$expected" "$1"; then
       printf 'Aborting: %s has no broker ExecStart matching legacy approval %s.\n' \
         "$1" "$legacy_approval" >&2
       return 1
     fi
   }

   stop_timer_and_wait_for_backup() {
     systemctl stop "$timer" || return 1
     local deadline=$((SECONDS + 1800)) state
     while (( SECONDS < deadline )); do
       state=$(timeout 5s systemctl is-active "$unit") || true
       case "$state" in
         inactive|failed) return 0 ;;
         active|activating|deactivating|reloading|refreshing) sleep 5 ;;
         *) printf 'Aborting: cannot determine backup service state (%s).\n' "$state" >&2
            return 1 ;;
       esac
     done
     printf 'Aborting: %s did not stop within 30 minutes.\n' "$unit" >&2
     return 1
   }

   systemctl show "$unit" -p FragmentPath -p DropInPaths -p User -p Group -p ExecStart
   systemctl is-active "$timer"
   systemctl is-enabled "$timer"
   check_legacy_exec_start "$dropins/20-current-release.conf" || exit 1
   visudo -cf /etc/sudoers.d/polypbase-deployment-backup
   sudo -l -U polypbase
   stat -c '%a %U:%G %n' "$broker" "$config" /var/lib/polypbase/deployment-backups
   ```

2. Stop the timer and wait up to 30 minutes for any in-flight backup to finish.
   A failed wait aborts the step before any file changes. Keep the path of the
   root-only rollback directory. Save only the broker and unit files, not
   the JSON or anything containing credentials. Install the new broker first:
   the old one still requires `approved_commit` and a SHA argument. This broker
   applies [retention](#deployment-backup-retention) after every backup from
   step 4 on. Review its dry-run plan, which deletes nothing, before going on.

   ```bash
   stop_timer_and_wait_for_backup || exit 1
   saved=$(mktemp -d)
   printf 'Rollback directory: %s\n' "$saved"
   cp -p -- "$broker" "$fragment" "$dropins/10-isolated-broker.conf" \
     "$dropins/20-current-release.conf" "$saved/"
   install -o root -g root -m 0755 deploy/scripts/trusted_deployment_backup.py "$broker"
   runuser -u polypbase_backup -- "$broker" --retention-dry-run
   ```

3. Remove just `approved_commit` from the JSON with a root editor that leaves
   no backup or swap copies. Keep the SHA-256 manifest and database settings
   intact. The new broker accepts either config, so this cleanup can also wait
   until after the unit switch. Install the versioned base unit and remove both
   overrides; otherwise the old SHA argument can still take precedence.

   ```bash
   stop_timer_and_wait_for_backup || exit 1
   editor "$config"
   stat -c '%a %U:%G %n' "$config"
   install -o root -g root -m 0644 deploy/systemd/polypbase-backup.service "$fragment"
   rm -- "$dropins/10-isolated-broker.conf" "$dropins/20-current-release.conf"
   systemd-analyze verify "$fragment"
   systemctl daemon-reload
   systemctl show "$unit" -p FragmentPath -p DropInPaths -p User -p Group -p ExecStart -p EnvironmentFiles
   ```

   Expect JSON permissions `640 root:polypbase_backup` and archive directory
   permissions `700 polypbase_backup:polypbase_backup`. Confirm the service uses
   the dedicated user and group, with no drop-ins or environment file and a
   single `ExecStart` pointing to the broker without arguments.

4. Start the service once and require a fresh `BACKUP_OK`. Paste only the path
   from that line when prompted. Confirm the file is nonempty, mode `600`, and
   owned by `polypbase_backup`. The web account must not be able to read the
   archive or the config. Then check the deploy-time sudo call with a reviewed
   full SHA (a second backup, not a deployment). Start the timer again only
   after both checks succeed. If `systemctl start` fails and
   `systemctl show "$unit" -p ExecMainStatus` reports `3`, the backup was kept
   but retention failed: stop and review the `RETENTION` journal lines.

   ```bash
   started=$(date '+%Y-%m-%d %H:%M:%S')
   systemctl start "$unit"
   journalctl -u "$unit" --since "$started" -o cat --no-pager | \
     grep -Ex 'BACKUP_OK /var/lib/polypbase/deployment-backups/nightly-[0-9]{8}T[0-9]{6}Z\.dump'
   read -r -p 'Nightly archive path: ' archive
   stat -c '%a %U:%G %s %n' "$archive"
   sudo -u polypbase -- test ! -r "$archive"
   sudo -u polypbase -- test ! -r "$config"

   read -r -p 'Reviewed full SHA: ' target
   [[ "$target" =~ ^[0-9a-f]{40}$ ]]
   result=$(sudo -u polypbase -- /usr/bin/sudo -n -u polypbase_backup "$broker" "$target")
   printf '%s\n' "$result" | grep -Ex \
     "BACKUP_OK /var/lib/polypbase/deployment-backups/pre-${target:0:8}-[0-9]{8}T[0-9]{6}Z\.dump"

   systemctl start "$timer"
   systemctl list-timers "$timer" --no-pager
   ```

#### Roll back the backup configuration

If the cutover fails, leave deployments paused and report the failing step.
Roll back only with explicit approval. If an abort closed the Bash session,
re-enter the variable and function definitions from step 1 (not the checks) and
set `saved` to the root-only directory printed in step 2. The commands stop the
timer, wait up to 30 minutes for any in-flight backup,
then check the saved `20-current-release.conf`. A failed wait or a mismatched
`ExecStart` aborts rollback before any file changes. In the editor, restore
`approved_commit` to `68ca288ef63a37340f3380e67835711be3ab885e` without changing
any other JSON fields. This rolls back only backup configuration, not a release
or database.

```bash
stop_timer_and_wait_for_backup || exit 1
check_legacy_exec_start "$saved/20-current-release.conf" || exit 1
editor "$config"
stat -c '%a %U:%G %n' "$config"
install -o root -g root -m 0755 "$saved/polypbase-deployment-backup" "$broker"
install -o root -g root -m 0644 "$saved/$unit" "$fragment"
install -o root -g root -m 0644 "$saved/10-isolated-broker.conf" "$dropins/10-isolated-broker.conf"
install -o root -g root -m 0644 "$saved/20-current-release.conf" "$dropins/20-current-release.conf"
systemctl daemon-reload
systemctl show "$unit" -p DropInPaths -p User -p Group -p ExecStart
started=$(date '+%Y-%m-%d %H:%M:%S')
systemctl start "$unit"
journalctl -u "$unit" --since "$started" -o cat --no-pager | grep -Ex \
  "BACKUP_OK /var/lib/polypbase/deployment-backups/pre-${legacy_approval:0:8}-[0-9]{8}T[0-9]{6}Z\.dump"
systemctl start "$timer"
systemctl list-timers "$timer" --no-pager
```

The old broker again requires a matching approval key for releases. Never
make an archive web-readable or fall back to backing up with the web role.

#### Security impact

Before the cutover, a compromised web identity could use the existing narrow
sudo rule to request backups only with the approved SHA. Afterward it can
request backups with any valid SHA or no label. Labels do not prove that a
release or the timer initiated a backup. Repeated requests can consume database,
CPU, or disk capacity; the old check did not prevent repeat requests for an
approved SHA. This change adds no throttling. With retention, repeated requests
can also push older archives of one category out of the recent keep counts.
Weekly nightly archives from earlier weeks survive, because new requests only
add archives to the current week. The web identity can also run the read-only
dry run, which reveals archive names but no content.

The web identity still cannot select the database, credentials, helpers, or
output directory. The sudo rule, pinned helper checks, isolated OS identity,
scrubbed environment, and peer-authenticated PostgreSQL role remain in place.
The web identity receives only an attested path, not access to the private
archive or the root-owned configuration.

### Fast resume after verified GitHub CI (`-UseVerifiedGitHubCi`)

This option must be selected explicitly and is never enabled by default. It
avoids rerunning local Django tests and the local frontend build when official
GitHub CI has already validated the exact commit. It requires `gh` to be
installed and authenticated (`gh auth login`); the script neither stores nor
accepts tokens. If verification is unavailable, ambiguous, incomplete, or fails,
the script stops before accessing the VM. There is no fallback.

Requirements: exactly one `push` run on `main` of the `.github/workflows/ci.yml`
workflow for the exact SHA, from the `origin` repository, completed with
`success`, with exactly one `Frontend checks` job and one `Backend checks` job
completed with `success`. The script still checks the branch, Git status,
alignment with `origin/main`, `git diff --check`, `manage.py check`, and migration
drift, and runs all VM-side steps (dependencies, frontend build, Django checks,
backup, read-only migration plan check, publication, restart, and health checks).

```powershell
.\deploy\deploy_vm.ps1 -UseVerifiedGitHubCi
```

`-UseVerifiedGitHubCi -PreflightOnly` verifies CI without modifying the VM.

Supply the SSH key path with `-SshKeyPath` or, if that parameter is missing or
empty, with `POLYPBASE_SSH_KEY`. Without either setting,
`deploy/deploy_vm.ps1` stops with an error: it does not fall back to a personal
path. The key and its contents are never copied into the repository.

### Schema readiness and the dedicated migrator

Normal deployment is now **strictly read-only with respect to migrations**:
the `database migration readiness` step runs `migrate --check --noinput` and
rejects any pending migration, regardless of its contents. It never applies a
migration, and the release script no longer accepts migration approval
parameters.

For any dedicated migration run, use `deploy/scripts/run_dedicated_migrations.sh`
with the isolated `polypbase_migrator` OS account, using an authenticated control
bundle and a target snapshot installed by root outside the web checkout. The
migrator verifies the exact JSON plan, `COMMIT:SHA256` approval, and a new
verified backup before applying migrations. For 0018 and any future migration,
follow the [authenticated verification and recovery procedure](#reprise-par-migrateur-dédié).
No operation, even a custom or apparently additive one, receives implicit
approval. The dedicated approval plan is a deterministic JSON file; the
text-based deployment `migrate-plan.txt` is not interchangeable with it.
A deployment with no pending migrations continues normally. A pending migration
blocks the deployment before publication, restart, or health checks, and must
then go through the dedicated runner after a new human review.

The runner retains `migrate-plan.txt` in the release directory for diagnosis.
Its contents may describe any Django operation: no keyword or text summary
changes the decision to block. Normal deployment never produces
`MIGRATION_REVIEW_APPROVED` and never runs `migrate`.

When code and schema are in an intermediate state, do not treat a failed
diagnostic using a field that has not yet been migrated as evidence of a data
anomaly. For the release above, the Phase 3B diagnostic using
`Organization.portable_id` cannot be used before `organizations.0002`; the
taxonomy migration retains its own duplicate precheck on historical models.
The dedicated migration procedure does not bypass that precheck.

### Box 1902 PostgreSQL setup and resuming a prepared release

Installing `measurements.0018_box_1902_preservation` is separate from application
deployment. The `polypbase` account must never run a migration and receives no
protected owner privileges. The SQL from
`deploy/scripts/box_1902_privileges.py provision` is only emitted for review:
running it requires separate authorization from the cluster admin and a new
verified backup. It rejects any collision, reuse, or partial reprovisioning.
After running it, verify the roles, database, owners, ACLs, and absence of
CREATE/TEMP for `polypbase` in read-only mode. Do not continue if any check fails.

The migration connection must then be authenticated independently of
`polypbase` (password or certificate managed outside the repository, never a
secret in the command or Git). The actual PostgreSQL role must be
`box1902_migrator`, with only its SET-only membership in `box1902_owner`.
`PGOPTIONS` explicitly requests `SET ROLE box1902_owner`; the runner verifies
`session_user=box1902_migrator`, `current_user=box1902_owner`,
`session_replication_role=origin`, and the recording of 0018 before and after
migration. The current local peer authentication for `polypbase` does not meet
this contract and must not be bypassed with a superuser, `SET SESSION AUTHORIZATION`,
or a permissive change.

**Do not run a runner from checkout `8504f52`**, where it does not exist, or
transfer an untracked file. Export the controls from a full Git commit that has
already been integrated and independently reviewed, then verify their
fingerprints according to the [recovery procedure](#reprise-par-migrateur-dédié).
That procedure leaves the application checkout intact and distinguishes the
control commit from the commit to migrate. The migration and backup OS
identities and their secrets are separate from the web service; the old
`sudo -u polypbase` example does not provide this isolation and must not be used.

The required result is `MIGRATION_COMPLETE`. Any other output, identity
mismatch, plan difference, or error stops the procedure. This script does not
provision roles, perform restores, or modify Git. An error leaves the database
and release in an intermediate state for read-only diagnosis; no migration is
reversed automatically.

After independently checking `django_migrations`, resume deployment for the same
commit. Deployment now checks the plan with `migrate --check` without running
DDL as `polypbase`, then publishes the frontend, restarts the service, and runs
health checks. If the check still finds a pending migration, stop and go back to
the dedicated runner; never grant an additional privilege to the runtime role.
Future migrations follow the same separation and require a new human review of
the exact commit and exact JSON plan, even for a simple addition. A partially
prepared release can resume only if the checkout is clean, the target commit is
exact, the previous backup and log are retained, and the plan and identity
checks pass.

Isolated local tests, with no VM or database:

```powershell
python -m unittest discover -s deploy/tests -v
```

These tests exercise the actual Bash functions and checks without launching the
runner, and the PowerShell blocks with a mock transport. Bash is required; on
Windows, PowerShell 5.1 and PowerShell 7 are checked if available. Missing
interpreters are reported as skipped tests.

## Stopping and resuming after a failure

The official procedure remains `deploy/deploy_vm.ps1` and its version-controlled
runner, `deploy/scripts/deploy_release.sh`, described above. Do not reconstruct a
manual sequence of pull, build, migration, and restart: it would bypass the
commit, backup, migration plan, and health safeguards.

At the first failure, stop and retain the log, the commit reached, the migration
plan, and backup references without exposing secrets. Diagnose the failed step
and intermediate state in read-only mode before proposing to resume. Resuming
through the guarded procedure requires new explicit authorization; do not relax
any protection to make it pass. This document provides no alternative
emergency procedure. A restore, rollback, or intervention outside deployment
requires its own reviewed procedure and explicit approval. Never edit code
directly in the production checkout.

After Certbot is enabled, do not directly overwrite the active Nginx
configuration with the repository's HTTP template. Carry the new directives
into the generated HTTPS block, then validate with `nginx -t` before reloading.

## DNS status

Status verified on August 24, 2026:

- `polypbase.org` has an A record pointing to `217.71.122.88`;
- `www.polypbase.org` works over HTTPS and reaches the same application.

The expected configuration remains:

| Name | Type | Value | Recommended TTL |
|---|---|---|---|
| `@` | `A` | `217.71.122.88` | 300 |
| `www` | `CNAME` | `polypbase.org.` | 300 |

Then check propagation:

```bash
getent ahostsv4 polypbase.org
getent ahostsv4 www.polypbase.org
```

Both names must continue to resolve to the VM's IPv4 address.

## HTTPS

The certificate is active and HTTP redirects to HTTPS. The remaining
operational checks are:

```bash
systemctl status certbot.timer --no-pager
certbot certificates
certbot renew --dry-run
```

Recheck sign-in, APIs, QR codes, and scanning after any significant change to
Nginx or the HTTPS settings.

## Remaining open items

1. configure an SMTP server for invitations and forgotten passwords;
2. regularly copy backups to storage outside the VM;
3. incorporate domain corrections from the anomaly files later, once Anaïs and Étienne have responded.

The uncorrected anomaly files do not block going live: the production database
preserves the historical import exactly as performed. Future corrections must
be handled as a separate domain operation, with documentation and a backup.

## Final checks

After DNS and HTTPS are enabled:

- `https://polypbase.org` opens the frontend without a warning
- HTTP redirects to HTTPS
- an internal frontend route reloads without a 404 error
- sign-in and sign-out work
- the API and administration interface are served through Nginx
- the administration interface's static files are present
- a QR code opens the correct record on a phone
- the scanner receives camera permission
- an existing reading from the authorized organization is viewed in read-only
  mode, checking values (including zero and missing values), the date, and the
  author, without creating, changing, or deleting scientific data
- an automatic backup is created and a test restore is validated only in a
  separate, isolated, and explicitly authorized environment

The production smoke test uses only existing data that the operator is authorized
to view. Any exceptional write to scientific data is outside both this smoke
test and the deployment approval: it requires a separate decision, explicit
authorization immediately before the operation, and a reviewed procedure covering
scope, audit, verification, and what happens to the data afterward. No test
write followed by deletion is authorized here.

### Completeness and restore

The release check uses the version-controlled PG18 clients, an inventory of objects outside system schemas, and a custom-format dump from the same exported snapshot. Missing privileges, active RLS, an unsupported object, or incomplete table-of-contents coverage cause the backup to fail. Owners and ACLs are preserved. `BACKUP_OK` is not a restore test and does not guarantee a transactional snapshot of sequence values.

A restore is a separate operation, using an authorized identity distinct from the backup account. Verify or recreate roles, memberships, and authentication rules beforehand: they are not part of a database dump. Preserve owners and ACLs (no `--no-owner` or `--no-acl`), stop at the first error (`--exit-on-error`), and verify both the expected privileges and the operations denied to the runtime role. See the [detailed contract and limits of the PG18 evidence](../archive/sources-fusion/database-backup-completeness.md).

<a name="reprise-par-migrateur-dédié"></a>

### Recovery with the dedicated migrator

1. Identify the full target commit and the commit containing the reviewed controls; verify their fingerprints. Prepare a control bundle and target snapshot installed by root outside the web checkout. The broker's identity remains separate from the migrator's.
2. Capture the dedicated JSON plan and establish it for the exact identity, database, and snapshot. Have the plan and `COMMIT:SHA256` approval reviewed before any execution; an approximate plan or a text release plan is not equivalent.
3. Run only after authorization and verification of a new backup. Stop if the plan differs, a check is invalid, or `MIGRATION_COMPLETE` is absent. Keep the logs and do not weaken protections to resume.
4. After independently checking the schema, resume a normal release for the same commit. It only checks that the plan is empty. The [original runbook](../archive/sources-fusion/dedicated-migration-recovery.md) details the evidence and commands for Box 1902. Adapt the targets to the plan actually approved, without replaying the historical provisioning.

<a name="exploitation-des-sondes-iminilide"></a>

## iMinilide probe operations

Collection uses the version-controlled units `deploy/systemd/polypbase-iminilide.{service,timer}`. Their presence does not prove installation or connectivity. Commissioning, linking channels, replacement, and enabling the timer require separate authorization; check schema readiness first.

- Set credentials only in the dedicated service environment (`/etc/polypbase/iminilide.env`, root access), never in the repository, chat, or the application's environment file. Do not overwrite an already provisioned file.
- `link_iminilide_probe --list` identifies the zones of the explicitly selected institution. Linking uses the approved zone, serial number, and channel. Do not move a probe that already has raw or condensed points to another zone: use replacement, which preserves the historical assignment. If needed, set the reference sampling interval with `set_probe_sampling_interval`.
- Review the log of the first `collect_iminilide` run, channel counters, and failures before enabling the timer. The timer is configured to run every ten minutes; that is not the sampling interval. QA checks use mock responses without querying the cloud. Collection is disabled in demo mode.

| Setting | Default or usage |
|---|---|
| `IMINILIDE_BASE_URL` | `https://iminilide.com`; send credentials only to an approved HTTPS destination |
| `IMINILIDE_EMAIL`, `IMINILIDE_PASSWORD` | Required for collection; not set by default |
| `IMINILIDE_TIMEOUT_SECONDS`, `IMINILIDE_TIME_ZONE` | 20 s, `Europe/Paris` |
| `IMINILIDE_MAX_LOOKBACK_DAYS` | 10 days |
| `PROBE_READING_RETENTION_DAYS`, `PROBE_BUCKET_MINUTES`, `PROBE_MANUAL_FALLBACK_HOURS` | 14 days, 15 minutes, 6 hours |

See the [channel collection, assignment, and recovery contract](../reference/measurements-temperatures-probes.md#iminilide-probe-collection-october-29-2026-decision).
