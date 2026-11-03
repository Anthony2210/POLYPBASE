# CCA-EOB TARGET 5461e7a — isolated installation QA

## Status: BLOCKED for installation acceptance

TARGET: `5461e7a5c07cf8893c2db78953316df78c9243a8`.
Reference infrastructure: `13a83602ab67f3ac9092ce7cd7207e0dbe5efe50`.
Dedicated branch: `test/cca-eob-target-linux-5461e7a`.

This is temporary QA infrastructure, not an active production manifest or an
approval of bootstrap, ACLs, scientific operations, or release. Production is
reported to remain at `4866520`; this task did not inspect or contact it. Main CI
and historical 62/62 at another commit do not prove this installation.

**Previous pyvenv.cfg blocker: resolved in the corrected TARGET collector at the
source/synthetic regression level; complete Linux trace/collection is still pending.**
The expected `APP/.venv/pyvenv.cfg` is classified as interpreter startup, read-only,
whether observed in `opened` or not. The offline QA verifies identical file
inventories, the exact config hash and preservation of the original trace and
its canonical hash. The 25 authentic collector tests cover forged/noncanonical
prefixes, symlinked ancestors, mapped config aliases, private paths and bytecode.

The QA does **not** remove the startup event, exclude the config, change the
collector or fabricate successful inventory/closure evidence. The full proof
requires real site initialization to observe `/app/.venv/pyvenv.cfg` and retain
its exact hash with `origin=interpreter`, `access=r`. It must fail on any refused
collection or incomplete evidence, even if all **74** mandatory core tests pass.
Linux execution may reveal additional blockers. The old target is superseded;
no historical pass is an acceptance result for this TARGET.

## Targeted QA image correction: `.venv/.lock`

Baseline QA HEAD: `0d0428132f09b86159baaa252b3b6b91100c0e7d`.
Existing [Actions run 41608492102](https://github.com/Anthony2210/POLYPBASE/actions/runs/41608492102)
failed during the installation proof. The public summary confirms the failed run
at that QA commit and its evidence artifact; detailed logs require sign-in and
were not independently retrieved here. The operator supplied these CI results:

- Linux CPython 3.13 locked installation and container isolation checks completed.
- **54 runtime + 6 preflight + 14 migration guard = 74 tests passed, zero skips.**
- `installation/failure.txt` reports `Not root controlled: /app/.venv/.lock` from
  the unchanged `controlled()` check, before the complete trace/inventory proof.

That failure proves the lock failed `uid == 0 and (mode & 0022) == 0`. It does
**not** distinguish non-root ownership from group/world-write bits, or prove the
observed UID/GID/mode; the supplied error contains no stat metadata.

### Source-supported explanation, not a fabricated CI stat

The pinned uv `0.13.6` tag resolves to upstream commit
`329bf9448afd9f9088d14a4d57fbc61eb454f117`.
[`Interpreter::lock`](https://github.com/astral-sh/uv/blob/329bf9448afd9f9088d14a4d57fbc61eb454f117/crates/uv-python-interpreter/src/interpreter.rs)
selects `sys_prefix/.lock` for a virtual environment during synchronization.
[`LockedFile::create`](https://github.com/astral-sh/uv/blob/329bf9448afd9f9088d14a4d57fbc61eb454f117/crates/uv-fs/src/locked_file.rs)
sets newly created Unix locks to `DESIRED_MODE = 0o666` using `set_permissions`
**after creation to override the umask**, then persists the file. It does not
change ownership or remove the lock on unlock. Reopening an existing lock can
preserve its prior permissions.

This Dockerfile creates a fresh venv and synchronizes it as root, before the
`USER 10001:10001` switch. A root-owned `0666` environment lock is consequently
the source-supported expected explanation: its write bits violate the proof.
It is not an observed stat from the failed CI image. Changing umask alone would
not address uv's explicit permission reset. This lock is installation metadata,
not the tracked dependency file `/app/uv.lock`.

### Minimal correction and preserved refusal

Immediately after dependency installation, still inside the disposable image's
root build stage, the Dockerfile:

1. Requires the exact `/app/.venv/.lock` to exist as a regular non-symlink file.
2. Logs UID, GID and mode with `stat` before correction.
3. Applies **only** `chmod 0644 /app/.venv/.lock`.
4. Logs the same metadata afterward, retained in the existing build log artifact.

No ownership is changed: unexpected non-root ownership must still fail the proof.
There is no recursive permission adjustment, lock deletion, exemption, runtime
repair or grant of runtime write access. `prove_installation.py`, including
`controlled()` and its traversal of **all** venv paths/ancestors, is unchanged.
The proof, source provenance, trace, inventory, closure, RECORD, manifest checks,
private probes and evidence protocol remain mandatory. The 74 tests and their
zero-skip requirement are unchanged.

Focused offline tests model Linux metadata on the Windows host. They verify
root-owned `0644` acceptance and continued refusal of wrong ownership, `0666`
and other unsafe modes even with effective writes denied by a read-only mount,
effective runtime write access despite safe mode bits, symlinks and unsafe
ancestors. A Dockerfile regression checks the precise path and ordering after
sync/before non-root execution. These tests are not a real Linux chmod/ACL proof.

**READY for targeted correction review; complete installation acceptance remains
BLOCKED pending a new actual CI execution of this correction.** Further proof
stages may expose additional failures and must not be bypassed.

## Files and lineage

All changes are restricted to these exact QA paths (no directory-wide exception):

- `.github/workflows/cca-eob-linux-qa.yml`
- `.github/workflows/cca_eob_linux_qa/Dockerfile`
- `.github/workflows/cca_eob_linux_qa/run_tests.py`
- `.github/workflows/cca_eob_linux_qa/prove_installation.py`
- `.github/workflows/cca_eob_linux_qa/prepare.py`
- `.github/workflows/cca_eob_linux_qa/run_container.py`
- `.github/workflows/cca_eob_linux_qa/test_qa.py`
- `.github/workflows/cca_eob_linux_qa/README.md`

The eight prepared QA files are preserved and adapted in place. Their original
workflow/Dockerfile lineage remains the reference above. The operator already
fast-forwarded this worktree and renamed its branch; this task performs neither
operation. Runtime counts increase from 44 to 54, with 6 preflight and 14 Bash
MigrationGuard cases retained. Application sources, dependency declarations and
lockfile, business logic, migrations, existing documentation and main workflows
are unmodified by this QA realignment.

## Safe reproduction on an independent Linux Docker host

No push/commit is performed here. The baseline has the failed CI result recorded
above; this local correction has no CI result until an authorized operator
commits, pushes and executes it. The workflow is restricted to the named QA
branch and uses pinned checkout/upload actions with read-only contents permission
and no persisted Git credentials.

Run from a fresh dedicated checkout containing exact Git blob bytes, with fresh
output paths outside the checkout. `prepare.py` intentionally refuses Windows
checkout conversion. Even Linux normally checks `*.ps1` out as CRLF because of
TARGET's `.gitattributes`. The workflow materializes canonical bytes only in its
fresh disposable CI checkout: `.git/info/attributes` is set to `* -text`, then
`git -c core.autocrlf=false checkout-index --all --prefix=/tmp/cca-eob-canonical/`
exports tracked HEAD into a new empty location. The CI-only `.git` metadata is
copied there, and integrity checks/build preparation run from that canonical
checkout. No tracked policy or source blob is changed; original files are not
overwritten. Do not repurpose this materialization over an existing worktree with
user work. It was **not** run in this local Windows worktree.

```sh
python3 -B -m unittest discover -s .github/workflows/cca_eob_linux_qa -p test_qa.py
python3 .github/workflows/cca_eob_linux_qa/prepare.py --output /tmp/cca-eob-target-context
# Preparation only: downloads public base image, OS packages, uv and locked wheels.
docker build --platform linux/amd64 --no-cache --progress plain --file /tmp/cca-eob-target-context/Dockerfile --tag cca-eob-target:5461e7a /tmp/cca-eob-target-context
# Isolated execution: no install or implicit download is allowed.
python3 .github/workflows/cca_eob_linux_qa/run_container.py --image cca-eob-target:5461e7a --output /tmp/cca-eob-execution-evidence
```

Do not start Docker, a VM, WSL, SSH, PostgreSQL or any production operation just
to turn an unavailable local environment into an acceptance claim. Cleanup of
these disposable QA images/artifacts is a local operator action; no broad Docker
prune or Git cleanup is provided. The helper removes only the container it creates.

## Integrity and provenance

1. `prepare.py` checks HEAD-to-TARGET, working-tree and untracked deltas against
   the exact eight-file allowlist, then hashes the actual bytes of **every**
   non-QA tracked file against its TARGET Git blob. CRLF drift is rejected, not
   normalized. Git diff checks also cover tracked mode changes on Linux.
2. The build receives a canonical TARGET tar made from raw `git cat-file --batch`
   blobs, not the QA worktree. Each blob's Git identity is verified before it is
   added. Plain `git archive` can apply `eol=crlf` attributes even on Linux, so it
   is not used for this all-path byte-exact build. Ignored `.env`,
   credentials, database files and local venvs are never copied. The export uses
   canonical non-group-writable modes. The Git pack contains only the TARGET
   commit and its recursive trees/blobs, not historical commits or local refs.
3. Root-owned `/app` is the exact TARGET source tree plus an image-local Git
   index, a fresh `.venv` and one clearly synthetic private probe. QA executables
   live outside it under `/qa`. The proof rechecks every TARGET blob and mode,
   every tracked runtime source SHA-256 and absence of untracked backend Python.
4. Base CPython Linux amd64 image digest and uv 0.13.6 match the reference.
   `uv sync --locked --no-install-project --no-default-groups` installs the full
   selected production Python runtime, checking the lock and wheel hashes; no
   dependency file is changed. The deployment uses `uv sync --frozen` and has no
   default groups in TARGET. The optional `analysis` group is **not** production
   sync scope and is not installed. No selected runtime dependency is omitted.
5. Build logs, image identity/inspect, independent source hashes, lock hash,
   actual successful install argv/receipt, installed versions, `uv pip check`,
   and read-only offline `uv sync --locked --check` are retained. The last check
   rejects incomplete selected closure; subset lock membership alone is not
   accepted as full installation proof. The receipt is a build attestation, not
   an independent second build. No runtime installation/repair occurs.

The Python image is digest pinned; public apt repositories and the bootstrap uv
wheel are version-selected but not independently digest-pinned here. Logs and
installed inventory allow review, but bit-reproducible OS package builds are not
claimed. Lockfile hashes authenticate application wheel downloads; RECORD checks
add installed-runtime file evidence, not independent trust in a malicious image.

### Canonical TARGET identities

These SHA-256 values were recalculated from raw TARGET Git blobs, not Windows
checkout bytes. The collector hash matches the updated runtime-manifest procedure.
`test_qa.py` checks all five source identities and the procedure's three tool pins.
The deterministic tar identity is also enforced by `prepare.target_archive()`.

| Source | SHA-256 |
| --- | --- |
| `backend/cca_eob_runtime.py` | `0d125888059a97a7e8641f041ee10422aebec91258e43306ebd135b5f2daea42` |
| `deploy/scripts/cca_eob_runtime_inventory.py` | `bf9f59d689036b38d47f5688a32d8faa903efab37b139f3c92c76ba6f1f517e4` |
| `deploy/scripts/cca_eob_runtime_permissions.py` | `831d9ef85b2e4a26266a0ba07bc556e0428cf393e85f17f0968af3dbbd637ac5` |
| `pyproject.toml` | `86036e55971c5acf56678bb135b0063bd2aff9b2059db8cd68632dd94f66bd80` |
| `uv.lock` | `ab2fac481c9ad93e16ab075ab7a072c437e3d46260de0fa60737dec90b48d154` |
| Canonical tar, 841 tracked files | `bc6cc0bad2e7b63c70bf448d23c393701a270d81d98674a28fc21e6beb37fffc` |

## Isolation and acceptance

`run_container.py` creates a disposable Linux amd64 container by immutable image
ID, explicitly inspects its security configuration before starting it, and uses:

- `--network=none`; only loopback may exist, no DNS/network test against a host;
- UID/GID `10001:10001`, named `polypbase_cca_eob`, no unexpected groups;
- `--read-only`, `--cap-drop=ALL`, `no-new-privileges:true`, no privileged mode;
- no host bind mounts, credentials, device grants, environment forwarding or DB
  socket; bounded memory/CPU/PIDs; only `/tmp` is writable tmpfs;
- root-owned sources, venv, interpreter and native targets, no runtime writes;
- synthetic root-owned mode0600 `/app/backend/.env` and `/private/probe`, both
  actually inaccessible to the runtime, without any real secret or content read.

`/tmp` is executable only because real venv/subprocess fixtures require it;
`nosuid,nodev` and dropped capabilities remain. This is a test sandbox, not an
adversarial-code sandbox or production ACL topology reproduction.

Core suites use actual discovered tests and fail on count drift, failures,
errors, or **any skip**:

| Suite | TARGET expected |
| --- | ---: |
| `deploy/scripts/test_cca_eob_runtime_*.py` | 54 |
| `deploy/tests/test_preflight_settings.py` | 6 |
| `deploy/tests/test_deploy_release.py::MigrationGuardTests` | 14 |
| Total mandatory Linux core | 74 |

The preflight suite deliberately exercises only synthetic in-memory SQLite
fixtures; the separate real CCA trace forbids SQLite and all database connection
attempts and requires Django's PostgreSQL connection to remain unopened.
No PowerShell-only tests are substituted for mandatory Linux coverage. Counts
are reconciled by static inspection plus unittest discovery in `test_qa.py`;
discovery is not Linux test execution. Real Django import, hook/site behavior,
source-only startup restrictions, permissions, preflight settings and deployment
migration guards remain covered by the unmodified TARGET tests.

After the core tests, a separate proof drives the authentic bootstrap with
`-I -S -B -X pycache_prefix=/dev/null`, real `site.main()`, the unmodified collector
source on stdin, minimal empty runtime environment, and a pre-site QA audit hook
that refuses socket/DNS/SQLite/subprocess attempts. The authentic trace imports
Django and the PostgreSQL backend, checks `connection.connection is None`, and
never executes plan/apply or a database command. The proof requires trace v2,
NoNewPrivs, private denials, directory listing evidence, no readable bytecode,
no subprocesses, RECORD provenance and exact independently hashed source closure.

It then requires authentic inventory/build, exact dependency equality between
inventory and a **QA-only** manifest, and the authentic content/ACL check API.
There are no automatic exclusions, blanket package additions or retries with
weaker startup flags. Unclassified resources/aliases stop acceptance; explicit
future scope decisions require review. The QA-authored manifest is not installed
or represented as a root-protected active policy. Production protected-policy
and live ACL gates are deliberately not executed.

Evidence is streamed as a bounded base64 tar data frame **before container exit**
(tmpfs is ephemeral). The host accepts only regular allowlisted data paths,
rejects duplicate/unsafe members, validates required summaries and artifact
hashes, and fails on missing evidence even if the process reports success.
Failure artifacts and full collector stderr/report are preserved where produced.
GitHub artifacts are QA-only, retained for 14 days; no production data enters them.

## Full-installation coverage and limits

The proposed proof covers a fresh installation of TARGET's **selected runtime**,
real interpreter/site initialization and observed CCA-EOB startup closure. It does
not claim that every optional analysis package is a runtime requirement, execute
scientific apply/restore branches, or prove a PostgreSQL transaction. Lazy
resources/native dependencies and `pg_restore` remain explicit review topics in
the authentic inventory report and production procedure. Unit tests alone never
unlock acceptance. Currently **no successful inventory/closure proof is claimed**.

### Debian 13 production differences

- Reference image is Debian **12 Bookworm**, not Debian 13 Trixie. Kernel comes
  from the Docker Linux host (GitHub Ubuntu host or Docker Desktop VM), not the
  production VM. glibc, system libraries, OpenSSL, filesystem and security policy
  can differ. Installed native mappings are inventoried, not extrapolated to live.
- Official `/usr/local` CPython 3.13 and a fresh uv 0.13.6 venv are not an
  authenticated copy of production's exact interpreter/venv or patch level.
- Production's real `_virtualenv.pth` / `_virtualenv.py` pair is reported in the
  task context; no production inspection was performed. This fresh QA venv uses
  uv 0.13.6 and deliberately requires the accepted **hook-free** layout. The proof
  will check and record actual absence of both files; it does not fabricate,
  delete, copy or substitute production startup hooks. TARGET tests support both
  real layouts, but a hook-free QA installation cannot establish provenance or
  effective behavior of production's genuine pair. The older documentation's
  mandatory-pair wording is not a requirement to synthesize hooks and is not
  permission to remove them from production. Fresh independent review of live
  hook source/provenance remains required before imports, including non-RECORD
  files. All installed QA runtime `.pth` files remain subject to review.
- UID/GID, `/app`, root-owned read-only files and synthetic probes differ from
  `/srv/polypbase/app`, deployment/runtime split, named ACLs, private ancestors,
  systemd/service policy and existing caches. Effective access is tested here;
  no claim about live permissions is possible.
- No PostgreSQL server, socket, credentials, schema, PG18 client/server behavior,
  existing migration state or production manifest is exercised. Previously
  inspected PG18 prerequisites are context, not evidence from this run.

## Local validation record

This correction was validated on Windows with CPython 3.13.3; no dependency
installation or service startup was attempted. `docker version` finds the Windows
client but fails to connect to `dockerDesktopLinuxEngine` (named pipe absent).
The corrected image could not be built or executed locally.

- `python -B -m unittest discover -s .github/workflows/cca_eob_linux_qa -p test_qa.py -v`:
  **30/30 passed, zero skips**, including the seven new lock security/build-order
  tests, all existing evidence-protocol checks, canonical TARGET hashes and actual
  discovery of **54 + 6 + 14 = 74** mandatory core cases.
- AST syntax passed for all five QA Python files. `git diff --check` passed.
- The source identity test still verifies the canonical export of **841** TARGET
  blobs and the unchanged pinned tar/tool/dependency hashes above. No application
  source or tracked dependency content was changed. The strict Windows checkout
  audit is not replaced by this canonical-blob check and is not claimed to pass.
- Only `Dockerfile`, `test_qa.py` and this README are changed by the correction.
  The production/application paths and the entire proof/runner protocol remain
  unchanged from baseline QA HEAD `0d0428132f09b86159baaa252b3b6b91100c0e7d`.

The earlier CI run's **74/74 Linux core result** is operator-supplied evidence for
that baseline, not a rerun of the corrected image. Its complete installation
proof failed at the lock check. No successful corrected Linux proof, trace v2,
inventory/permission closure, RECORD or manifest validation is claimed here.

**READY for targeted review; BLOCKED for full installation acceptance until the
corrected image is actually executed and its complete evidence independently
reviewed.** QA cannot establish production hook provenance, live ACL compliance,
protected-policy compliance, lazy apply/restore closure or database behavior.
No active production manifest, bootstrap, ACL or release approval is produced.
