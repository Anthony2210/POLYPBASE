# CCA-EOB TARGET 65f30e4 — isolated installation QA

## Status: READY FOR INDEPENDENT REVIEW; Linux execution pending

TARGET: `65f30e4e39fd9bb56b118d65dd21eb9a246de891`
(`fix: derive CCA-EOB resource hashes from target checkout`).
Reported live commit (LIVE reference only, never built): `4866520eae2b30f676ab96365fd806486948e832`.
Reference infrastructure: `13a83602ab67f3ac9092ce7cd7207e0dbe5efe50`.
Dedicated branch: `test/cca-eob-target-linux-65f30e4`.

This is temporary QA infrastructure. It is not an active production manifest and
does not approve bootstrap, ACLs, scientific operations, migrations or a release.
No production host, Neon or shared database was contacted.

## Lineage

The harness is the previously successful QA for TARGET `5461e7a`
(branch `test/cca-eob-target-linux-5461e7a`, QA commit
`6d5b567d8d3a5a39609d751b193615260d978ba4`,
[Actions run 41616241870](https://github.com/Anthony2210/POLYPBASE/actions/runs/41616241870)).
Its eight files were restored byte-for-byte from that commit's Git blobs, then
adapted in place. Isolation, evidence transport, Dockerfile, uv lock handling,
reviewed Bookworm system-data scope and container flags are unchanged apart from
the TARGET identity. No historical run is an acceptance result for this TARGET.

## What changed in TARGET and how the QA follows it

TARGET changes only `deploy/scripts/cca_eob_runtime_permissions.py`, its two
test files and the runtime-manifest procedure. `build` now hashes each inventoried
`resource` from the Git-tracked bytes of `--root` (bridge: LIVE `APP`; candidate:
TARGET clone) instead of copying the inventory hash. The defect it fixes: a
candidate carried the LIVE hash of `backend/locale/fr/LC_MESSAGES/django.mo`
although TARGET contains different bytes.

QA adaptations (this branch only):

1. **Identities.** TARGET SHA, canonical archive, permission-tool pin (the
   previous `831d9ef8…` is asserted revoked in the procedure), counts.
2. **Pinned LIVE reference.** `prepare.py` authenticates LIVE commit `4866520e`,
   its backend tree `1d84d130f185c8eaabb1329b7a4bbb0146a569a7` and its ancestry
   to TARGET, then records SHA-256 of LIVE backend non-Python **Git blobs** and
   the LIVE→TARGET changed backend resources in `source-integrity.json`.
   Nothing from LIVE is installed, executed or added to the image tree.
3. **Container proof (`prove_installation.py`).** The former single check
   "non-`tracked_code` manifest entries == inventory" is replaced by strict,
   independent checks (`verify_resource_closure`):
   - `dependency`/`interpreter`/`stdlib` entries identical in inventory and manifest;
   - resource path/access/origin set identical; nothing added or removed;
   - every resource is a tracked regular TARGET blob, and its inventory and
     manifest hashes equal the independently verified TARGET bytes;
   - every observed resource exists in LIVE (a new resource = STOP);
   - the observed changed resources must be **exactly**
     `backend/locale/fr/LC_MESSAGES/django.mo` and listed in the LIVE→TARGET diff.
     Any other changed resource (for example `en/django.mo` if it appears) = STOP
     for a separate review decision, never an automatic acceptance;
   - `backend/config/settings.py` and `backend/config/cca_eob_production_settings.py`
     must appear in the loaded modules.
   The result is preserved as `installation/resource-closure.json` (per resource:
   path, access, TARGET hash, LIVE hash, changed; loaded backend modules; counts by
   origin). `run_container.py` requires this artifact, `resource_closure=passed`
   and `changed_resources == [fr/django.mo]`.
4. **Host protocol tests (`test_qa.py`)**, run on the GitHub Linux runner before
   the image build, with a strict runner that refuses any skip on Linux:
   - authentic TARGET `build` with **real LIVE and TARGET backend blobs** in real
     temporary Git roots: bridge fr catalog = LIVE `096e634c…`, candidate =
     TARGET `f640685f…`; external entries copied exactly in order; resource shape
     unchanged; tracked sources from the respective root;
   - the procedure's unmodified `CCA_EOB_CANDIDATE_DATA_CHECK` script, run on
     those real builds, prints exactly the `en` and `fr` `CHANGED_RESOURCE` lines,
     and refuses both the legacy candidate (LIVE hash) and a TARGET-rooted bridge;
   - untracked resource refused; **Linux-only, mandatory:** real leaf symlink to
     outside LIVE bytes, real ancestor-directory symlink, in-root symlink to another
     tracked catalog, Git `120000` entry, `O_NOFOLLOW` refusal (`ELOOP`) when the
     leaf becomes a symlink after all path checks, identity refusal of the same
     swap without `O_NOFOLLOW`, and a regular-file swap before `open`;
   - fail-closed unit tests of `verify_resource_closure` and the LIVE record;
   - LF procedure bundles (`git -c core.autocrlf=false archive`) whose members
     equal the raw Git blobs.

The container also runs the authentic TARGET suites; TARGET's own real-symlink
build test skips only where symlinks cannot be created and is therefore mandatory
under the container's zero-skip rule.

## Canonical identities

Recalculated from raw Git blobs, never from Windows CRLF working-tree bytes:

| Item | SHA-256 |
| --- | --- |
| Canonical TARGET tar, 841 tracked files (`prepare.target_archive()`) | `f799cf96f4dfc1a49a3eb54a9aa76fbd756f10672bc413c6c62fda470dcae215` |
| `backend/cca_eob_runtime.py` | `0d125888059a97a7e8641f041ee10422aebec91258e43306ebd135b5f2daea42` |
| `deploy/scripts/cca_eob_runtime_inventory.py` | `bf9f59d689036b38d47f5688a32d8faa903efab37b139f3c92c76ba6f1f517e4` |
| `deploy/scripts/cca_eob_runtime_permissions.py` | `8ce7fcb443978a8b32ddfdff4fa8b383f262ce2d89b41f2971c7dc2b86cd30c4` |
| `pyproject.toml` (unchanged) | `86036e55971c5acf56678bb135b0063bd2aff9b2059db8cd68632dd94f66bd80` |
| `uv.lock` (unchanged) | `ab2fac481c9ad93e16ab075ab7a072c437e3d46260de0fa60737dec90b48d154` |
| LIVE `backend/locale/fr/LC_MESSAGES/django.mo` | `096e634c898ca0b72b4ab7199f31e79d67ed006ee243a4d0024d98a240184b82` |
| TARGET `backend/locale/fr/LC_MESSAGES/django.mo` | `f640685fc0185867c28752a39572c9a38ae66d218bd6312221b3bbcc1500bba9` |
| Procedure bundle `cca-eob-reviewed.tar` (LF) | `8b8208983d7078f2b98d144d1174a2d8e717406c92d3226c2a1b617b4d226793` |
| Procedure bundle `cca-eob-live-backend.tar` (LF) | `75f398cd7cdfe09030570d80383c143bc91374cf1e5b86ed9d851efa8c27e9d9` |

The previous canonical tar `bc6cc0ba…` is reproduced by the same method for
`5461e7a`. A default Windows `git archive` (system `core.autocrlf=true`) yields
`35bec017…` and `5227d2c9…` for the two procedure bundles: CRLF, **not valid**.
The bundle digests depend on `git archive`'s tar writer; the Linux run confirms
them on another Git version.

## Mandatory suites (counts from real TARGET discovery)

| Suite | Previous | TARGET |
| --- | ---: | ---: |
| `deploy/scripts/test_cca_eob_runtime_*.py` | 54 | 59 |
| `deploy/tests/test_preflight_settings.py` | 6 | 6 |
| `deploy/tests/test_deploy_release.py::MigrationGuardTests` | 14 | 14 |
| Total Linux core, zero skips | 74 | 79 |

The +5 are the new resource-build/data-check tests of the permission tool.
`test_qa.py` reconciles counts by static AST count and unittest discovery.

## Unchanged isolation and evidence guarantees

- `prepare.py` refuses any delta from TARGET outside the exact eight-file
  allowlist and verifies every other tracked file against its TARGET blob,
  rejecting CRLF drift; the image receives a raw-blob tar and a pack holding only
  TARGET objects. No `.env`, credentials, database or local venv is copied.
- Fresh complete locked installation: digest-pinned `python:3.13-slim-bookworm`,
  uv 0.13.6, `uv sync --locked --no-install-project --no-default-groups`, then
  only `chmod 0644 /app/.venv/.lock`; read-only `uv sync --locked --check`.
- Execution: `--network=none`, `--read-only`, uid/gid `10001` `polypbase_cca_eob`,
  `--cap-drop=ALL`, `no-new-privileges`, no mounts/env/sockets/credentials, only
  `/tmp` tmpfs; inspected before start. Source-only `-I -S -B -X pycache_prefix=/dev/null`
  startup contract `cca_eob_source_only_v1`, pre-site audit hook refusing
  socket/DNS/SQLite/subprocess, synthetic root-owned 0600 probes.
- Evidence streamed as one bounded tar frame, unpacked only as allowlisted regular
  files, artifact hashes rechecked; missing evidence fails even if the process
  reports success.

## Limits

- Debian 12 Bookworm QA, not the Debian 13 production runtime; no `interpreter`,
  `stdlib` or native-library hash for production comes from this run.
- The QA installs TARGET only: inventory and manifest are TARGET-rooted
  (candidate-equivalent). The bridge is exercised with real LIVE blobs in host
  tests, not on a LIVE installation. The procedure's comparison of the TARGET
  closure with the **live** Debian 13 inventory remains a reviewer step: compare
  `resource-closure.json` and the inventory with the live evidence.
- No plan/apply, DB, `pg_restore`, production ACL, protected manifest or deploy.

## Local validation (Windows, CPython 3.13.3)

- `python -B .github/workflows/cca_eob_linux_qa/test_qa.py`: 56 discovered,
  49 passed, 7 Linux-only skipped (refused on Linux), 0 failures.
- TARGET `test_cca_eob_runtime_*.py`: 59 run, 4 Windows-only skips (real symlink,
  staged bootstrap, full launcher, project venv), 0 failures.
- `prepare.py` refuses the Windows checkout (CRLF), as designed; the Docker
  build and container run require GitHub Linux.

## Reproduction on GitHub Linux

```sh
python3 -B .github/workflows/cca_eob_linux_qa/test_qa.py
python3 .github/workflows/cca_eob_linux_qa/prepare.py --output /tmp/cca-eob-target-context
docker build --platform linux/amd64 --no-cache --progress plain --file /tmp/cca-eob-target-context/Dockerfile --tag cca-eob-target:65f30e4 /tmp/cca-eob-target-context
python3 .github/workflows/cca_eob_linux_qa/run_container.py --image cca-eob-target:65f30e4 --output /tmp/cca-eob-execution-evidence
```

Run only from the workflow's fresh canonical checkout (`* -text`,
`checkout-index` into a new path); never over a worktree with user work.
