# CCA-EOB TARGET cb07a2c — isolated installation QA

## Status: READY FOR NARROW DELTA REVIEW; corrected Linux rerun pending

- TARGET: `cb07a2cea43e4af0bc9a8a61ed33abaa23eef1cc`
  (`fix: prepare targeted species corrections`).
- Previous validated TARGET: `65f30e4e39fd9bb56b118d65dd21eb9a246de891`.
- Reported LIVE reference, never built or contacted:
  `4866520eae2b30f676ab96365fd806486948e832`.
- Dedicated branch: `test/cca-eob-target-linux-cb07a2c`.

This is QA-only infrastructure, not an active production manifest or approval for
bootstrap, ACL changes, scientific operations, migrations or release. No production
host, Neon, network service or database was accessed during this local adaptation.
No commit, push, merge or deployment was performed.

## Lineage and scope

The eight files were copied from raw Git blobs of the previous QA commit
`63af90af1e223a587abce511c3cc0e0edc172e6e`, branch
`test/cca-eob-target-linux-65f30e4`, then adapted in place, not redesigned.
Previous successful QA supplied for reference:
[Actions run 41650958815](https://github.com/Anthony2210/POLYPBASE/actions/runs/41650958815).
New main CI supplied for reference:
[Actions run 41659099707](https://github.com/Anthony2210/POLYPBASE/actions/runs/41659099707).
These runs were not fetched or rerun locally. Neither is an acceptance result for
this TARGET-specific harness.

Only `.github/workflows/cca-eob-linux-qa.yml` and these seven companion files are
allowed to differ from TARGET. Application code, tests, lockfiles, migrations,
permissions policy and deployment procedure are untouched.

## Exact previous-TARGET delta

`prepare.target_delta()` checks the **entire** Git delta, not merely selected
runtime paths, and requires previous TARGET ancestry:

| Status | Path |
| --- | --- |
| Added | `backend/apps/cultures/historical_2026/variant_species_correction.py` |
| Added | `backend/apps/cultures/management/commands/correct_variant_species.py` |
| Modified | `backend/apps/cultures/test_historical_2026_import.py` |
| Added | `backend/apps/cultures/test_variant_species_correction.py` |
| Added | `backend/apps/cultures/test_variant_species_correction_postgres.py` |

Exactly two additional eligible tracked backend Python sources: **247 → 249**.
Eligibility follows the authentic tool: `.py` under `backend`, excluding any path
component beginning with `test`. No eligible source is removed. Runtime tools,
settings, migrations, resource catalogs and dependency files are unchanged from
`65f30e4`. Any additional change, including a test/resource/lock change, refuses
preparation. The complete delta and counts enter `source-integrity.json`.

Both new sources must be included as `tracked_code`, but neither is expected in
CCA-EOB startup. The authentic Linux trace is checked for both paths in `modules`,
`opened` and `mapped`; any observation is a STOP. `startup-delta.json` records this
check and is mandatory evidence. Local negative tests exercise all six source/event
combinations; they do **not** establish the actual Linux startup closure.

## Environment-specific counts and failure evidence

The operator-provided evidence from
[failed run 41666652749](https://github.com/Anthony2210/POLYPBASE/actions/runs/41666652749)
reports that the QA protocol and all **79 core tests passed on Linux with zero
skips**. The isolated installation then failed at the harness assertion:
`Expected 955 candidate-equivalent entries; observed 971`.
This diagnosis is supplied evidence, not a fresh local Linux execution.

The harness incorrectly applied the Debian 13 production inventory total to the
Debian 12 Bookworm QA environment. These environments must remain distinct:

| Environment | Inventory | TARGET tracked_code | Manifest total |
| --- | ---: | ---: | ---: |
| Production Debian 13, conditional on unchanged LIVE inventory | 706 | 249 | **955** |
| Isolated Debian 12 QA, observed in run 41666652749 | 722 | 249 | **971** |

The previous successful QA had **969 entries with 247 tracked sources**. The new
QA adds exactly two tracked sources, giving **971**, not the production count.
Production **955 remains unchanged**, conditional on an independently verified,
unchanged Debian 13 inventory of **706**. `source-integrity.json` labels these as
`expected_production_candidate_entries` and
`conditional_on_unchanged_debian13_inventory_entries`; they are not QA acceptance
counts and are not proof of production closure equivalence.

The isolated installation proof and host acceptance now require **722 inventory
entries and 971 manifest entries**, with exact per-origin counts:

| Origin | QA inventory | QA manifest |
| --- | ---: | ---: |
| dependency | 465 | 465 |
| interpreter | 19 | 19 |
| stdlib | 237 | 237 |
| resource | 1 | 1 |
| tracked_code | absent | 249 |

`summary.json` records actual inventory/manifest totals and per-origin counts;
`expected_manifest_files` is **971 for this QA environment only**. Unknown origins,
missing entries, changed counts or reclassification with an unchanged total fail
closed. These checks supplement, never replace, exact source hashes, inventory
entry equality and independent resource closure checks. The corrected full Linux
installation proof still requires a successful rerun.

## Canonical identities

Recalculated from authenticated raw Git blobs, not Windows CRLF working-tree bytes:

| Item | SHA-256 |
| --- | --- |
| Canonical TARGET tar, 845 files (`prepare.target_archive()`) | `ba28e8bfd21fac2974f944cb1c9410a4428e4f120c32a34e62d41568c5f30cdd` |
| `backend/cca_eob_runtime.py` (unchanged) | `0d125888059a97a7e8641f041ee10422aebec91258e43306ebd135b5f2daea42` |
| `deploy/scripts/cca_eob_runtime_inventory.py` (unchanged) | `bf9f59d689036b38d47f5688a32d8faa903efab37b139f3c92c76ba6f1f517e4` |
| `deploy/scripts/cca_eob_runtime_permissions.py` (unchanged) | `8ce7fcb443978a8b32ddfdff4fa8b383f262ce2d89b41f2971c7dc2b86cd30c4` |
| `pyproject.toml` (unchanged) | `86036e55971c5acf56678bb135b0063bd2aff9b2059db8cd68632dd94f66bd80` |
| `uv.lock` (unchanged) | `ab2fac481c9ad93e16ab075ab7a072c437e3d46260de0fa60737dec90b48d154` |
| LIVE `backend/locale/fr/LC_MESSAGES/django.mo` | `096e634c898ca0b72b4ab7199f31e79d67ed006ee243a4d0024d98a240184b82` |
| TARGET `backend/locale/fr/LC_MESSAGES/django.mo` | `f640685fc0185867c28752a39572c9a38ae66d218bd6312221b3bbcc1500bba9` |
| Procedure bundle `cca-eob-reviewed.tar` (LF) | `548c79a505d995164387ecda976dd9efa90de9cf407d0f9f88019f0e18d7d93b` |
| Procedure bundle `cca-eob-live-backend.tar` (LF, unchanged) | `75f398cd7cdfe09030570d80383c143bc91374cf1e5b86ed9d851efa8c27e9d9` |

Procedure bundles use `git -c core.autocrlf=false -c tar.umask=0002 archive`.
Tests require their file members to equal the raw Git blobs. Default Windows
archive conversion is not accepted. Bundle digests also depend on Git's tar writer;
the later Linux run must confirm them independently.

## Preserved resource checks

- Authentic TARGET `build` hashes each inventoried resource from its own Git-tracked
  `--root`, not by copying the inventory hash. Host tests use real LIVE and TARGET
  blobs and separate real Git roots for bridge and candidate.
- The procedure's unmodified `CCA_EOB_CANDIDATE_DATA_CHECK` proves the real `en` and
  `fr` catalog differences, and refuses a legacy candidate carrying LIVE hashes or
  a TARGET-rooted bridge. Untracked resources, real leaf/ancestor/in-root symlinks,
  Git mode `120000` and leaf/regular-file race swaps remain refusal cases.
- Installation proof independently compares external entries exactly between
  inventory and manifest, and checks identical resource path/access/origin sets.
  Each resource hash must equal verified TARGET blob bytes, be present in pinned
  LIVE backend tree `1d84d130f185c8eaabb1329b7a4bbb0146a569a7`, and any change must
  occur in the independently derived LIVE→TARGET Git diff.
- The **only reviewed changed observed resource** remains
  `backend/locale/fr/LC_MESSAGES/django.mo`. An observed `en` change, new resource or
  different closure is a STOP requiring review, not automatic scope expansion.
  Both settings modules must appear in the loaded-module trace.
- `resource-closure.json` retains per-resource TARGET/LIVE hashes, loaded backend
  modules and counts by origin. This QA builds TARGET only: its inventory and
  manifest are candidate-equivalent, not a new LIVE installation or bridge proof.

## Mandatory suites: actual TARGET discovery

AST counts and unittest discovery agree, without executing test bodies:

| Suite | Previous TARGET | TARGET |
| --- | ---: | ---: |
| `deploy/scripts/test_cca_eob_runtime_*.py` | 59 | 59 |
| `deploy/tests/test_preflight_settings.py` | 6 | 6 |
| `deploy/tests/test_deploy_release.py::MigrationGuardTests` | 14 | 14 |
| Linux core total, strict zero skips | 79 | 79 |
| Host QA protocol tests | 56 | 63 |

The original three added harness tests cover the exact delta, unexpected delta
refusal and startup-source rejection. Four count-regression tests additionally
cover the observed QA distribution, production/QA count confusion in proof and
host acceptance, same-total origin drift and unknown origins. No species-correction
Django/PostgreSQL tests are added to this harness. In particular, the new PostgreSQL 18 tests are a **separate
release-quality gate**: not run here, not reported as passing or skipped-success.

## Unchanged isolation and evidence guarantees

- Source authentication rejects non-QA deltas and byte drift. Build receives a
  deterministic raw-blob tar and a pack containing only TARGET objects, never a
  local `.env`, production credentials, database or local venv.
- Fresh Linux CPython 3.13 installation uses the same digest-pinned Bookworm image,
  uv 0.13.6 and `uv sync --locked --no-install-project --no-default-groups`.
  Network is used only during image preparation for system/locked dependencies.
  Only the real regular venv lock is hardened with `chmod 0644`; no hook is fabricated.
- Execution has no network, credential inputs, sockets or host mounts:
  `--network=none`, `--read-only`, uid/gid `10001`, `--cap-drop=ALL`,
  `no-new-privileges`, `/tmp` tmpfs only; configuration inspected before start.
- Source-only startup remains `-I -S -B -X pycache_prefix=/dev/null`, contract
  `cca_eob_source_only_v1`. A pre-site audit hook refuses socket/DNS, SQLite and
  subprocess activity. Private denial probes are synthetic root-owned 0600 files.
- Strict zero-skips enforcement remains mandatory for all Linux host/core suites.
  A local Windows skip is a limitation, never Linux acceptance.
- Evidence uses one bounded streamed tar frame, regular allowlisted members only,
  no overwrites or links, then artifact hash verification. Source integrity,
  installation receipt, test summary, trace/inventory/report, independent source
  hashes, locked-environment check, resource closure and startup delta are required.
  Missing evidence fails even after process exit zero. Evidence remains QA-only.

## Local validation: Windows, CPython 3.13.3

For this count-only correction:

- `python -B .github/workflows/cca_eob_linux_qa/test_qa.py`:
  **63 discovered; 56 passed; 7 Linux-only skipped; 0 failures/errors**.
  Includes canonical TARGET/tool hashes, real-Git resource build tests, exact
  delta and actual core-suite discovery. No database access.
- In-memory compilation of harness Python files and `git diff --check`: passed.
- No Linux installation or core-suite execution was repeated for this correction.
  A successful full corrected workflow is still required.

Prior adaptation's local results (not rerun for this count-only correction):

- `python -B -m unittest discover -s deploy/scripts -p 'test_cca_eob_runtime_*.py' -v`:
  **59 discovered; 55 passed; 4 skipped; 0 failures/errors**. Missing platform/venv
  prerequisites: real resource symlink, staged bootstrap, full launcher and project venv.
- `python -B -m unittest deploy.tests.test_deploy_release.MigrationGuardTests -v`:
  **14 discovered; 14 passed; 0 skips/failures/errors**; real shell guards only,
  never deployment or a database operation.
- Preflight: **6 discovered only**, not executed locally because the retained suite
  exercises synthetic in-memory SQLite. This task permits no local database access.
- Direct `prepare.py` correctly refuses the Windows CRLF checkout. A disposable
  local clone with TARGET index, `* -text` attributes and raw TARGET blob files
  passed the unmodified preparation path: **845 authenticated non-QA files, eight
  QA files**, exact delta, canonical archive and TARGET-only object pack generated.
  The temporary copy/build context was removed. No actual image installation ran.
- Whitespace/compile checks are local checks, not substitutes for Linux execution.

## Reproduction on GitHub Linux

Run from the workflow's fresh canonical checkout (`* -text`, `checkout-index` into
a new path), never by canonicalizing a worktree containing user work:

```sh
python3 -B .github/workflows/cca_eob_linux_qa/test_qa.py
python3 .github/workflows/cca_eob_linux_qa/prepare.py --output /tmp/cca-eob-target-context
docker build --platform linux/amd64 --no-cache --progress plain --file /tmp/cca-eob-target-context/Dockerfile --tag cca-eob-target:cb07a2c /tmp/cca-eob-target-context
python3 .github/workflows/cca_eob_linux_qa/run_container.py --image cca-eob-target:cb07a2c --output /tmp/cca-eob-execution-evidence
```

## Remaining gates; no deployment approval

1. Independent review of these eight files and their source/closure expectations.
2. A corrected GitHub Actions rerun on the dedicated branch: all 63 host and
   79 core tests with **zero skips**, fresh locked install, authentic trace with
   neither new source imported/read, 249 tracked sources, 722 QA inventory entries
   and 971 QA manifest entries with the exact per-origin counts above, plus complete
   hash-verified target-specific installation/closure evidence.
3. Separate successful PostgreSQL 18 species-correction release-quality tests.
4. Procedure-required review of TARGET evidence against the actual Debian 13 LIVE
   inventory, including dependencies, stdlib and native libraries. Bookworm QA is
   not a production interpreter/native-library hash source. No lazy apply/restore
   closure, `pg_restore`, scientific mutation, production ACL or active policy was
   exercised here.
5. Explicit separate authorization for each future production phase and release.
   Existing deployment orchestration and guards must not be bypassed.

Stop here for independent review. No commit or push is part of this task.
