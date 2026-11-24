# Movement / thermal register integration — 2026-11-09

## Scope and authoritative sources

Local working-tree resolution only. No staging, commit, cherry-pick continuation,
abort, skip, push, merge, production operation or database access is authorized.
Japanese and frontend script files belong to the other integration agent and are
not edited here. Existing committed evidence, including
`fr-en-sync-recovery.md`, is not rewritten.

All source data was extracted with `git show <commit>:docs/translation-review/application/register.csv`,
not reconstructed from the conflicted working tree. Three new complete snapshots
retain the original bytes, including both independent uses of B20/T1011–T1036:

| Snapshot suffix | Full source commit | Events |
|---|---|---:|
| `base` | `eea90d12ab6ece48a5f75635a4b58912befa86bf` | 1010 |
| `movements` | `5b1299b02b5a84be8a6008cb9d9233bc9c7aed5e` | 1036 |
| `thermal` | `ee492bd4d591b787d9147e913531a89e015ed9dc` | 1036 |

Snapshot names are `integration-2026-11-09-<suffix>.csv` in this directory.
Both complete branch registers have the complete base blob as their exact byte
prefix. The integrated `register.csv` has the complete movements blob as its
exact byte prefix: no historical field, quoting, ordering or LF byte was changed.
Windows checkout CRLF conversion is normalized to LF only for portable byte-hash
verification in tests; the local reconstruction used original Git LF bytes.

## Mapping and append-only convention

The README's laboratory integration precedent renumbers only `entry_id` and
`batch` when independent incoming events collide. Apply that same convention:

- Retain movements B20 (6 events) and B21 (20 events), T1011–T1036, unchanged.
- Append all 26 thermal source events, in source order, as B22-2026-11-09.
- Change each thermal source ID Tn to T(n + 26), exactly.
- Preserve all other 12 fields exactly: surface, key, language, source file,
  initial/final/sheet values, decision reference, status, reason, baseline commit
  and workbook hash. In particular, the baseline remains `eea90d12...`, not HEAD.
- No new wording approval, Japanese translation or scientific rule is implied.
- The result is exactly 1062 events in 22 contiguous chronological batches.

All rows in the following table move from source B20-2026-11-09 to integrated
B22-2026-11-09. This table is a mapping, not a replacement for original evidence.

| Original ID | Integrated ID | Key | Language |
|---|---|---|---|
| T1011 | T1037 | temperatureTrendTitle | FR |
| T1012 | T1038 | temperatureTrendTitle | EN |
| T1013 | T1039 | probeIntervalMean | FR |
| T1014 | T1040 | probeIntervalMean | EN |
| T1015 | T1041 | probeCombinedChartKeyboard | FR |
| T1016 | T1042 | probeCombinedChartKeyboard | EN |
| T1017 | T1043 | probeRangeEdit | FR |
| T1018 | T1044 | probeRangeEdit | EN |
| T1019 | T1045 | probeRangeStart | FR |
| T1020 | T1046 | probeRangeStart | EN |
| T1021 | T1047 | probeRangeEnd | FR |
| T1022 | T1048 | probeRangeEnd | EN |
| T1023 | T1049 | probeRangeApply | FR |
| T1024 | T1050 | probeRangeApply | EN |
| T1025 | T1051 | probeRangeReset | FR |
| T1026 | T1052 | probeRangeReset | EN |
| T1027 | T1053 | probeRangeInvalid | FR |
| T1028 | T1054 | probeRangeInvalid | EN |
| T1029 | T1055 | probeCondensedNotice | FR |
| T1030 | T1056 | probeCondensedNotice | EN |
| T1031 | T1057 | probeLegendProbe | FR |
| T1032 | T1058 | probeLegendProbe | EN |
| T1033 | T1059 | probeLatestInterval | FR |
| T1034 | T1060 | probeLatestInterval | EN |
| T1035 | T1061 | probeSeriesToggle | FR |
| T1036 | T1062 | probeSeriesToggle | EN |

## Recalculated canonical digests

`rows_digest` in `test_check_register.py` serializes every field in `reg.COLUMNS`
order as a list of lists, using `json.dumps(..., ensure_ascii=False)` with its
standard separators, then hashes UTF-8 with SHA-256. These are not CSV byte hashes.
They were recalculated from the actual Git source objects before reconstruction,
and all three requested expected hashes matched:

| Event group | Canonical SHA-256 |
|---|---|
| Movements source T1011–T1036, original B20/B21 | `092d2c92d7dbdb16b5f4eccb9687636eca4b567d5f64dd4d57d92c53f1032de1` |
| Thermal source T1011–T1036, original B20 | `05c86c2949ba886fc8c5529163725bb6be21750bf53209c3c10093009cde2eb6` |
| Thermal projection T1037–T1062, integrated B22 | `ee979768b84b2dd0900773f26ecb25095dd916be7983c61d78f3f440658ea53e` |

Complete histories are independently frozen as well:

| Complete register | Canonical SHA-256 |
|---|---|
| Base, 1010 events | `1c2035ad35072d2df221a4543494701ea82acc7a4645a07d50648cf764c7798d` |
| Movements HEAD, 1036 events | `e5e5f9f6eacaafd47b083dc743e22a8e572121010f9cf123d88a4d71981f1df5` |
| Thermal source, 1036 events | `5088da5700fe1b4727898b03ed3b07f2cd13d21dfa8cbdfbc29f2547beafd0c7` |
| Integrated, 1062 events | `cf1b7f575ce11e801e78ce81385fb133b3ee07e04bfca63ffc4eff4d77b9308c` |

Original Git LF bytes have these independently calculated SHA-256 digests:

| Register | Bytes | Byte SHA-256 |
|---|---:|---|
| Base snapshot | 542934 | `88b17986a4bc5d69ea3c23c3d5dc9b3b5162dd9c40c0b94979d2dd05e7319eda` |
| Movements snapshot | 557714 | `19ccea2e745b600b75d0121819dbab11e9b4a7323881c775876ed8312337fc48` |
| Thermal snapshot | 555664 | `309be58d9c02c101a7db17723cbe1a7f7f18daa131bbd96bd64204d375b822d6` |
| Integrated register | 570444 | `3502cbbdca1eed175c5763a89765fed48a167ee2796bb7e2d84a6175a414a550` |

The movements and thermal original register Git blob IDs are respectively
`a6b60e0c0407a284a5ef20e8dd6e968054da06bb` and
`447b0546c8e40993ded6cda42431d4d6636279b8`.
Snapshots are sufficient for regression tests without requiring non-shallow Git
history. They remain independently verifiable against the full commit/path above.

## Regression coverage and semantic conflict

Eight new integration tests check complete original byte/field histories, common
base, original tail digests, all-field source-to-projection mapping, exact count,
unique consecutive IDs, unique identity per batch, ordering, chronology and
current FR/EN source consistency. Mutations cover every field of every movement
and thermal addition, a changed field in every older event, deletions, inserted
or duplicated rows, reordered rows, reused batches and incorrect dates. Coordinated
catalog/register changes cannot bypass the independent historical fingerprints.
Later corrections remain possible only through new appended events.

The existing B13 reconstruction test had a semantic cherry-pick conflict despite
merging textually: the thermal patch removed B20 additions, while HEAD already
removed movement B20/B21 additions later. The first removal now targets thermal
B22 only; the later removal still targets movement B20/B21. Each independent set
is removed exactly once, preserving the existing reconstructed baseline hash and
all B13/laboratory/movement-correction freezes.

## Validation

- `uv run --frozen python -B docs/translation-review/application/check_register.py`:
  PASS, 1062 events in 22 batches; 253 ALREADY_MATCHING, 198 APPLIED,
  128 DEFERRED, 45 PRESERVED and 438 REMOVED.
- Initial concurrent `uv run --frozen python -B -m unittest discover -s docs/translation-review/application -p 'test_*.py'`
  exceeded its 120-second limit after partial progress. This run overlapped
  first-time local environment setup; the cause of the timeout was not isolated.
  No success is inferred from this partial run.
- `python -B -m unittest discover -s docs/translation-review/application -p 'test_*.py' -v`:
  PASS, 79 tests, 78.104 seconds, Python 3.13.3.
- Repeated the exact `uv run --frozen` application test command after setup:
  PASS, 79 tests, 79.164 seconds, Python 3.14.5.
- `git diff --check -- docs/translation-review/application/`: PASS.
- Final independent comparison against all three actual Git blobs: PASS,
  byte-for-byte snapshots, exact HEAD byte prefix, exact ID/batch-only appended
  thermal bytes and all 26 documented mapping rows.
- During the register-only phase, repository-wide `git diff --check` still found
  conflict markers in the Japanese/frontend files. Their separate resolution is
  now complete: final working-tree and cached checks pass (only Git's LF/CRLF
  conversion warning remains).

See `integration-2026-11-09-validation.md` for the combined frontend/backend
validation, independently reproduced prior failures and remaining limitations.
The index remains Anthony's responsibility; resolving worktree content does not
mark an unmerged index entry resolved.
