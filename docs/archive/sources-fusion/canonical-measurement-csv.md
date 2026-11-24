# Canonical biological measurement preparation — Phase 2

## Boundary and reuse

`backend/apps/measurements/canonical.py` is a pure, transport-independent validator.
`csv_import.py` is its bounded UTF-8 CSV adapter. Neither imports Django, performs
I/O, resolves identities, queries a database, nor writes anything. Existing CSV
importers, exports, live serializers and measurement write services are unchanged.
No dependency, model, migration, endpoint, plan, receipt or UI is introduced.

The normalized `import_bdd_csv` command is not this contract: its rounding,
Monday conversion and upsert semantics are not reused. The historical 2026 code
provides a precedent for source coordinates, explicit decisions, versioning and
stable hashes, but its dataset rules and Monday-as-measurement-date convention
are deliberately not copied. Current exports are wide weekly reports (including
repeated headers), not round-trip entry files for this narrow row adapter.

## Canonical contract v1.0

Schema: `polypbase.biological-measurement-entry`, version `1.0`.
Frozen dataclasses use tuples for stable, immutable records. A `CanonicalRow`
contains `time`, ordered `values`, and `provenance`; `to_dict()` and
`canonical_json()` provide deterministic serialization. Counts are integers;
salinity is a fixed two-decimal string, not a float. No actor is encoded.

Canonical source fields:

| Field | Entry rule |
|---|---|
| `box_reference` | Required nonempty opaque source reference; no identity guessing or normalization beyond trimming |
| `measured_on` | Exact ISO `YYYY-MM-DD`, or use year/week instead |
| `iso_year`, `iso_week` | Complete valid ISO year/week pair when supplied |
| `polyp_count`, `ephyrae_count` | Required for any nonblank measurement; explicit integers, no live-entry default zero |
| `strobila_count` | Optional, nullable, including ordinary file imports |
| `salinity_psu` | Optional, nullable, signed decimal with at most 2 fractional places and magnitude at most 999.99 |
| `culture_status` | Optional; exact domain codes `good`, `medium`, `bad`, `dead`, `not_specified` |
| `needs_attention` | Optional; exact `true` or `false` |
| `notes` | Optional free text, preserved verbatim when nonblank |
| `observer_reference` | Optional nullable opaque source reference, never resolved or invented here |

Counts use the portable Django `PositiveIntegerField` range `0..2147483647`.
Fractions (including `-0.1`), negative counts, exponent notation, grouping,
NaN/infinity and formulas are rejected rather than rounded or coerced. Decimal
notation is ASCII with a dot; comma/grouping/exponent conventions are not guessed.
The current salinity model/serializer have no nonnegative validator: this phase
therefore does **not** invent one. A biological restriction on negative salinity
would require an explicit product decision and coordinated domain validation.

`FieldValue.state` distinguishes:

- `absent`: field not supplied/mapped; not a zero, null or correction instruction;
- `null`: mapped blank nullable field (strobila, salinity, observer);
- `blank`: mapped blank nonnullable optional field; no implicit model default;
- `value`: an explicitly supplied valid value, including `0` and `false`.

Required blanks are invalid. Literal `null`, `None`, `NA` etc. are not null
sentinels. CSV has no native null type: a blank nullable cell is intentionally
interpreted as null under this versioned entry contract, with the original cell
retained. These states are **not instructions to modify existing data**.

A row with all measurement payload fields blank is intentionally ignored and
counted: counts, salinity, culture status, attention and notes form that payload.
Box/date/week/observer references may be prefilled; they alone are not a
measurement. Explicitly ignored columns do not contribute to payload presence.
A nonblank payload with either required count absent is invalid. A truly empty
physical line is also counted; a quoted empty one-column record must still obey
header width. Structure errors are checked before blank classification.

## Dates and weeks

`MeasurementTime` contains `precision`, `iso_year`, `iso_week`, and `measured_on`.
An exact date gives `precision="exact_date"` and its actual ISO year/week (for
example `2021-01-01` belongs to `2020-W53`). A week-only source gives
`precision="iso_week"`, `measured_on=None`. ISO weeks are validated using the
standard calendar; invalid week 53 is rejected, never clamped. No synthetic
Monday is stored. If both exact date and year/week are supplied, they must agree.
A partial supplied week pair is invalid even alongside an exact date. Date
formats, timezone timestamps and regional slash conventions are not guessed.

## CSV mapping and resource contract

```python
from apps.measurements.csv_import import read_csv

result = read_csv(
    b"Box,Date,Polyps,Ephyrae,Strobila\nB-001,2021-01-01,0,0,\n",
    source_id="synthetic.csv",
    mapping={
        "Box": "box_reference", "Date": "measured_on",
        "Polyps": "polyp_count", "Ephyrae": "ephyrae_count",
        "Strobila": "strobila_count",
    },
    confirmed=True,
    delimiter=",",
)
```

Every exact decoded header must have an explicit mapping to one canonical field
or `None` (deliberately ignored). No auto aliases, header translation, fuzzy
matching or dialect sniffing. French/English labels work through explicit maps.
Confirmation is mandatory; a future caller must obtain it through its reviewed
workflow, not silently set it. Persistent profiles are out of scope.
Duplicate/empty headers and names ambiguous after trim/case-fold are rejected.
Two columns cannot map to the same field. Required identity/count columns and
at least one complete time representation must be mapped.

Input is caller-provided bytes: UTF-8, with optional BOM. Explicit delimiters are
comma, semicolon or tab, double-quote quoting with doubled quote escapes. CR, LF,
CRLF and quoted embedded newlines are supported. Bare quotes in unquoted fields,
characters after a closing quote, unterminated quotes, NUL and invalid UTF-8 are
blocking errors. A structure failure stops parsing because reliable recovery
coordinates cannot be guaranteed. Ordinary width/value errors remain visible
and subsequent records are still inspected.

Default `CsvLimits`: **5 MiB bytes**, **10,000 data records** (including blank and
invalid), **64 columns**, **16,384 decoded characters per field**. Headers and
mapping configuration are also bounded; source identity is at most 512 chars.
Callers may lower limits. The core validator always caps cells at 16,384 chars.
The lexer stops accumulating oversized fields/columns; it does not modify a
process-global CSV setting. The whole bounded byte string, decoded text and
structured results are kept in memory; this is not a streaming upload API.
At a row limit, at most one additional diagnostic record is retained.
The adapter cannot undo allocation of bytes by its caller: future upload code
must enforce its own read-size limit before constructing the input.

Formula-looking text remains inert data. Numeric expressions beginning with
`=`, `+` or `@` get `numeric_formula`; other nonnumeric expressions are also
invalid. Nothing is evaluated. Future export/UI adapters must independently
escape formula-like text and render all raw source content safely.

## Provenance and results

`CsvResult.records` retains source order. `valid_rows`, `blank_rows`,
`blank_count`, and `invalid_rows` expose classifications. Each record includes:
source identity, one-based record number (header is record 1), physical line
start/end, exact decoded headers/cells, and the header-ordered mapping. CSV
syntax is decoded (outer quotes/BOM removed); raw bytes are identified by SHA-256.
Oversized/malformed records retain only bounded partial cells, not a complete
source row. File/configuration issues may precede any usable row coordinates.

`Issue` contains a stable machine code, canonical field and one-based source
column when applicable. It never interpolates source values or parser exceptions.
A future UI must translate these codes through existing FR/EN i18n and safely
render source values. File-level `issues` and `complete=False` block planning;
`ready_for_planning` requires a complete file without file or invalid-row issues.
Previously valid rows in a blocked result are diagnostics, not an applicable
partial batch. A valid empty/blank-only file has no measurement rows.

The result includes schema/version, source SHA-256, source identity, delimiter,
confirmed mapping, limits and classifications. `canonical_json()` and
`fingerprint` (SHA-256) cover this interpretation deterministically, independent
of caller mapping insertion order. Rejected oversized/configuration input is not
hashed. These are preparation fingerprints, not persisted plans or acceptance
receipts; no database matching outcome is generated.

## Read-only planning consumer

The institution-scoped consumer is documented in
[`measurement-import-planner.md`](measurement-import-planner.md). It classifies
these preparation results without changing the pure Phase 2 contract, existing
importers, schema or write services. It preserves source states and date precision,
blocks repeated slots and unresolved problems, and marks week-only CREATE
candidates as blocked pending persistence support. It introduces no apply path.

## Future dependencies and next phase

A future XLSX adapter can construct source-string mappings and `Provenance`,
then call `validate_row()` without CSV or Django. It must explicitly handle
native typed cells, dates, formulas and conventions; this phase does not provide
an XLSX reader or invent a null sentinel.

`BiologicalMeasurement.measured_on` is currently nonnullable and its write
services implement interactive actor attribution/live defaults. They cannot
persist week-only precision faithfully. Before application, design reviewed
schema/service support for source date precision and unknown observer vs importing
actor. Do not satisfy the current schema with a fake Monday or overwrite an
existing exact date when matching a week-only source.

The read-only planner now resolves exact Box codes under server-authorized
institution scope and compares applicable supplied values against the weekly
slot. Its null/absent/blank comparison and metadata rules are explicit in the
planner contract above. Named-observer resolution remains explicitly unsupported
and blocking pending an approved reference contract. Future phases still require
immutable reviewed durable plans and deterministic state guards, then separate
restricted creation/correction applicators, all-or-nothing transactions and
durable receipts. No unresolved decision blocks preparation or diagnostic-only
planning; persistence and observer dependencies must be resolved before apply.

## Local validation

Pure tests require no Django settings or database:

```text
POSTGRES_DB='' POLYPBASE_TEST_POSTGRES=0 uv run python -m unittest apps.measurements.test_canonical_csv -v
```

From `backend/`, compatibility tests and Django/migration checks use only
`--settings=config.test_settings` and force the PostgreSQL opt-in off.
For `check` and `makemigrations` without local development configuration, also
set `DJANGO_DEBUG=1` to use the existing development-only key fallback.
No production/shared database, dataset or deployment workflow is used.
