# Historical biological-measurement CSV import — exact dates

## Implementation and delivery evidence

The restricted acceptance API and the `MeasurementImportEntry` /
`MeasurementImportPanel` components are implemented. The components support
selection of bounded UTF-8 CSV, header inspection, explicit column mapping,
diagnostics, import confirmation and a durable receipt outside Administration,
but the entry is **not mounted in the active React UI**. Backend availability is
not proof of an accessible laboratory screen. No partial acceptance, correction,
Box creation or identity guessing is available.

The original delivery baseline was `ce70fc1`, including diagnostics commit
`2b4382a`; its uncommitted and pending-review status was historical. The applicator
is now committed in `main` (`de75a3b`, retained by `37587da`). This local Git evidence
establishes neither production deployment nor execution, independent acceptance
or scientific validation. The test results below describe the original delivery,
not checks rerun during this documentation update.

## Scientific contract

- Only unblocked `CREATE`, `ALREADY_MATCHES` and `IGNORED` rows are accepted.
- CREATE requires a genuine exact date; no synthetic Monday is persisted.
- A real zero (including 0/0) occupies its Box/ISO-week slot.
- Every blocking row rejects the whole batch. Source duplicates block even when identical.
- ALREADY_MATCHES and IGNORED never modify scientific data or provenance.
- Week-only CREATE and named observer references remain blocked.
- No mixed-source resolution, new subculture guard, precision conversion,
  correction, legacy importer change or iso_week persistence is activated.

Imported measurements explicitly establish `exact_date`, `file_import`, `csv`,
`imported_by=actor`, `observer_status=not_recorded` and no original observer.
The legacy `user` field remains the writing actor, not an observer attribution.

Nullable optional counts/salinity remain unknown rather than zero. The additive
migration also permits NULL culture status, attention and notes, so missing or
blank imported metadata does not acquire interactive defaults. Exact canonical
`absent`, `blank`, `null`, `value` states remain in immutable acceptance evidence.
Ordinary entry keeps its existing defaults and rejects explicit null for its
previously nonnullable input fields. Existing rows are not rewritten.

## Private source and durable receipt

`MeasurementImportReceipt` stores original bytes in a database BinaryField,
their SHA-256, the complete canonical preparation (schema/version, mapping,
delimiter, source coordinates and interpretation), the reviewed versioned plan,
organization, importing account ID, acceptance time, result snapshots,
measurement IDs and mandatory audit IDs. This includes blank-only/header-only
and already-matching accepted batches creating zero measurements.

Source, receipt, measurements, sequence increments and audits use **one database
transaction**. No filesystem/object-storage operation, staging file, on-commit
source write, public media URL or cross-resource atomicity claim is involved.
A failure to persist the bytes or receipt rolls back all scientific writes and
audits. Inspection and preview retain no source or receipt.

Receipts reject update/delete through the model/queryset and SQLite/PostgreSQL
triggers. There is no automatic deletion, expiration or retention schedule.
Database backups must therefore include this table; source bytes and canonical
snapshots can include personal data from ignored columns and original cells.
This is private application storage, not encryption against database operators.

## API

Session authentication, CSRF for POST, explicit `X-Organization-Id`, fresh active
Admin/technician membership and active institution/account are mandatory.
Superuser status without product membership does not bypass authorization.
Authorization precedes multipart parsing. Responses are `Cache-Control: no-store`.

- `POST /api/measurement-import/inspect/`: ephemeral header inspection.
- `POST /api/measurement-import/preview/`: ephemeral diagnostics with availability.
- `POST /api/measurement-import/apply/`: source and mapping resubmission plus
  `expected_preparation_fingerprint` and `expected_plan_fingerprint`.
- `GET /api/measurement-import/receipts/<uuid>/`: authorized receipt JSON,
  including counts, source identity, mapping, actor/institution and result snapshots.
- `GET /api/measurement-import/receipts/<uuid>/source/`: authorized private
  original bytes, forced attachment, octet-stream and nosniff.

Uploads use the existing bounded in-memory handler: 5 MiB source, 10,000 data
records, 64 columns and 16,384 decoded characters per field, with bounded
multipart metadata. Apply requires confirmed=true, complete explicit mapping
and two lowercase SHA-256 fingerprints. No client plan is accepted as authority.

An accepted response is `{receipt, replayed}`: 201 for new acceptance, 200 for
replay. Stale source/interpretation/plan or unsupported batches return 409;
malformed inputs return 400, oversized inputs 413, unauthorized access 403,
foreign receipt IDs 404, database acceptance failures 503. Errors expose stable
codes, not source contents or database exception messages.

## Locks, freshness and replay

The server reparses actual bytes, recomputes their interpretation and locks all
resolved Boxes in PK order. It reloads authority, rebuilds the complete plan and
compares its fingerprint before any scientific write. The current plan has
`schema_version: "1.3"` and includes Box identity/status/revision, canonical
occupancy, all measurement payload fields, date, precision, sequence, latest
writer, observer status, original observer, origin, import format and importer,
plus `duplicate_of_id` and `environment_source_box_id`. The provenance/precision
fields were introduced in 1.2; 1.3 retains the duplicate/environment-source
baseline explicitly. Preserved duplicate evidence is not a second canonical
observation and is never silently rewritten by acceptance.
Ordinary creation/correction uses the same Box-lock protocol; uniqueness of
canonical measurements (`duplicate_of IS NULL`) per Box/week remains the final
database constraint. Existing subculture snapshots, consumption,
movements and audits are not rewritten. New measurements allocate normal source
sequences through the existing model save behavior.

Uniqueness is organization + exact source SHA-256 + preparation fingerprint.
Same accepted interpretation returns its original receipt without reapplying
data or producing new audits, including after later correction/deletion. A second
authorized actor can replay a previously reviewed identical interpretation; the
receipt retains its original accepting actor. Original reviewed guards or a fresh
matching preview identify a replay. Changed source/interpretation requires its
own valid preview and is a distinct acceptance, not an implicit replay.

The frontend prevents repeated submission and discards stale responses after
source/mapping/delimiter/institution change or closure. A lost response may be
retried safely. Cancellation cannot undo an already committed server transaction.
Success exposes receipt/source downloads with fresh organization authorization.
All user-facing copy uses the existing French/English catalogs.

## Validation evidence

Ordinary tests use SQLite in-memory with `POSTGRES_DB=''`,
`POLYPBASE_TEST_POSTGRES=0`, `DJANGO_DEBUG=1` and `config.test_settings`.
PostgreSQL tests opt in to the fixed disposable loopback port 55439 only, using
an empty locally created `postgres:16-alpine` container, never the shared local
container on port 55432, Neon or production.

- Complete backend suite: 1,603 discovered, passed with 74 skips (SQLite).
- Focused measurement/migration regressions: 327 discovered, passed with 9 skips.
- PostgreSQL acceptance/API/concurrency: 58 passed, including 9 real locking races.
- Frontend targeted API/measurement/history/timeline/CSV regressions: 122 passed.
- Django check, migration drift, TypeScript, CSS and Vite production build passed.
- Complete frontend suite: 1,782 passed, zero failures. Its initial run found a
  static allowlist test that did not yet admit the CSV field-label metadata
  module; that metadata-only allowance was updated without weakening
  null-versus-zero assertions.
- Final measurements-only SQLite run: 290 discovered, passed with 9 skips.
- `git diff --check` passed. The disposable PostgreSQL container was removed.

The PostgreSQL races verify import/import (same and differing source, multiple
actors, disjoint and reversed multi-Box order), import/manual creation in both
orders and concurrent no-op receipt uniqueness. They do not certify all possible
production workloads. No live-browser QA or production migration was performed.
Wait for an independent read-only review before release decisions.
