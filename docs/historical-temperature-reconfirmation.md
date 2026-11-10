# Exceptional historical temperature reconfirmation — organization 1

## Status and decision

Local implementation only. Independent review and a separately authorized
production operation are required. This document is not deployment or database
access authorization. No production count has been verified locally; **1,175 is
an earlier observation, not an approved expected count**.

The owner authorizes bulk reconfirmation without reviewing every reading. This
is not authority to change source evidence, temperature values, biological
counts, dates, identities, or original import receipts.

## Root cause and existing contract

`measurements.0012_historical_temperature_guards` installs
`historical_temperature_box_guard`: changes to organization, global code, strain
or box number invalidate confirmed associations. Migration 0014 also protects
canonical identity/merged records. Even an identity correction that leaves the
retained evidence compatible with the resulting identity invalidates the status.

The immutable-reading trigger permits only `confirmed -> pending_review`, with
all other fields unchanged. ORM save/update/bulk-update are also blocked. Import
replay refuses pending associations; reverting a correction does not reconfirm.
There was no supported reconfirmation workflow.

A genuinely unconditional bulk confirmation conflicts with this scientific
contract. The new command **does not implement it**. In particular, a retained
source label incompatible with the current Box remains blocking. The only
non-identical supported label mapping remains the existing explicit
`PCA-CHO-1.005 -> PCA-JKA-1.005` identity decision, with padding normalization.
No new identity aliases or biological exceptions are inferred.

## Implementation

- `backend/apps/measurements/historical_temperature_reconfirm.py`: maintenance
  service, hardcoded organization 1, PostgreSQL only, standalone transaction.
- `backend/apps/measurements/management/commands/reconfirm_historical_temperatures.py`:
  dry-run-by-default command and JSON reports; anomalies exit unsuccessfully.
- `backend/apps/measurements/test_historical_temperature_reconfirm.py`: synthetic
  PostgreSQL trigger/transaction/concurrency/command tests and SQLite refusal.

No migration, model, import, Box service, frontend or production grants change.

### Eligibility and approval

The scope is **every pending association currently belonging to organization 1**,
not a guessed list of 1,175 IDs. All must be eligible; there is no partial apply.
Every retained evidence is validated, not just one matching source:

- Canonical biological measurement and Box, matching ISO week and observed date.
- Finite exact temperature and unchanged source temperature, including real zero.
- Matching current/source polyp and ephyrae counts, including zero.
- Existing supported source identity, source year/week and literal week value.
- Source digest format, nonblank source file/sheet, valid coordinates/merge block.
- At least one retained source evidence per reading.

Validation uses a hypothetical confirmed in-memory object, without saving or
fabricating evidence. Existing retained evidence is trusted as provenance; the
command does not reopen workbooks or independently reauthenticate owner approval.
The recorded operator attests that authorization exists.

Apply requires an active Django superuser operator ID, nonblank reason, exact
owner-attestation text, positive expected count and exact dry-run fingerprint.
The approval fingerprint covers only the scope defined below, not every locked
or preserved table. Same-count changes within that snapshot are rejected.
No-op/stale replay is rejected, not counted as a second success. Reuse after a
later invalidation would require a new explicit approval/count/fingerprint; this
is maintenance tooling, not a product workflow.

### Exact approval scope and receipt immutability

| Guarantee | Exact scope |
| --- | --- |
| Fingerprinted | Version `1`, organization ID `1`, original immutable-reading function definition, and every current `pending_review` reading linked through biology to a Box in organization 1. For each target: all concrete model fields of the reading, linked biology and linked Box, plus all concrete fields of every retained evidence. Readings and evidence are ordered by primary key; JSON keys are sorted. Even linked Box/biology metadata is included. |
| Not fingerprinted | Import receipts (including their archive bytes, plan, results, hashes and timestamps), users, organization records, audits, unrelated Boxes/biology/evidence, and non-target readings. A shared target Box is included even if it also has non-target readings. Trigger configuration is checked separately, not hashed. |
| Reverified at apply | The snapshot and all eligibility checks are recomputed under exclusive locks. The active superuser, reason, exact attestation, positive expected count, zero blockers, expected fingerprint, origin replication mode and original immutable-reading guard configuration are checked. The original function is restored and verified before commit. |
| Preserved after execution | Every pre-existing receipt, archive, evidence, temperature value, biological record, Box and audit; every non-target status. Only approved target statuses change, and one new audit is appended. Preservation is broader than the approval fingerprint. |

**Receipts are deliberately excluded, not silently omitted.**
`HistoricalTemperatureImportReceipt` has an organization FK, but no FK to a
reading. Supported importers record reading/biology IDs in the `results` JSON
when creating the receipt in the import transaction; those are historical import
results, not a relational or exclusive membership constraint. Reconfirmation
neither replays an import nor uses receipt contents to decide eligibility. Its
scientific evidence is the retained `HistoricalTemperatureEvidence.reading` FK
and the current linked biology/Box. It does not verify receipt presence, receipt
JSON membership, or archive-to-evidence correspondence.

Existing receipts cannot be changed through the supported write contract:

- `models.py` rejects receipt save-after-insert, delete, queryset update/delete,
  bulk update and bulk create.
- Migration 0013 installs `historical_receipt_immutable` before any SQL UPDATE or
  DELETE, and `historical_receipt_no_truncate` before TRUNCATE. Both call
  `historical_temperature_immutable()` from migration 0012, which always raises
  SQLSTATE `23514` (`Historical temperature provenance is append-only`).
- The temporary reading-status exception does not replace that function or either
  receipt trigger. PostgreSQL tests attempt changes to every receipt field,
  deletion and truncation before, during and after reconfirmation; all must fail.

Appending a new receipt remains possible. It does not alter an existing receipt,
create an enforced reading relationship or invalidate an unchanged reading
approval, even if its JSON mentions a target ID. A regression test covers new
org-1 and foreign-org receipts between dry-run and apply, unchanged fingerprint,
success with the original approval and preservation of all receipts. Adding a
pending reading or changing its linked data still changes the approval snapshot.

This is the smallest correction to the Medium review finding: clarify the
fingerprint/preservation distinction and prove enforced immutability, rather
than introduce receipt hashing, archive loading, or an invented reading join.
No fingerprint format or runtime authorization/update behavior is changed.
Receipt immutability assumes the normal migrated schema and enabled guards. The
command does not attest receipt-trigger definitions; it attests the reading guard.
Privileged trigger disabling, schema tampering or replica-mode writes outside
this workflow are not supported receipt mutations. Including receipt hashes
alone would not establish an authentic receipt-to-reading relationship.

### Transaction and temporary exception

Both modes lock the organization, auth user, Box, biology, historical reading,
evidence, import receipt and audit tables, in that order. Dry-run takes SHARE
locks (no data/schema writes); apply takes ACCESS EXCLUSIVE locks before reading.
This deliberately serializes writers and blocks unseen inserts. Apply also
blocks reads across organizations while the maintenance transaction runs.

The immutable function body, security mode, search path, unconditional/non-column-
limited trigger configuration and origin replication mode must match the expected
protection. Unexpected drift fails closed.

Apply needs existing schema-owner maintenance privileges. Inside its transaction
only, it replaces the immutable function with an exception restricted to the
validated numeric IDs, org 1, `pending_review -> confirmed`, and no other field
change. It does not disable triggers or set a bypass GUC. It restores and verifies
the exact original function definition before appending one AuditLog containing
operator ID/name, reason, attestation, IDs, counts and snapshot fingerprint.
Status update, restoration and audit commit together. Error, crash or lost
connection before commit rolls all of them back, including transactional DDL.
Other sessions cannot observe a committed permissive function.

Lock acquisition timeout is 10 seconds; statement timeout is 120 seconds **per
statement**, not a transaction-wide limit. No retry or partial continuation is
performed. Concurrent apply with the same approval has at most one success;
subsequent biology corrections still invalidate associations normally.

## Operator syntax (not executed here)

Run from `backend/`, using the separately verified production settings/environment
and existing deployment procedure. Do not put credentials on the command line.
The values in angle brackets below are syntax placeholders, not observed counts
or authorization. Do not paste them without substitution.

Dry-run is automatic when `--apply` is absent:

```sh
uv run python manage.py reconfirm_historical_temperatures --organization-id 1
```

The JSON contains `pending_count`, `eligible_count`, `blockers` with reading IDs
and errors, eligible `reading_ids`, and `fingerprint`. Zero pending is a read-only
success but cannot be applied. Any blocker refuses the whole batch.

After separate approval of the exact report:

```sh
uv run python manage.py reconfirm_historical_temperatures \
  --organization-id 1 --apply \
  --actor-id <active-superuser-id> \
  --reason '<owner decision reference, operator context and justification>' \
  --owner-attestation 'I attest owner approval for exceptional batch reconfirmation of evidence-matching historical temperatures in organization 1 without individual review' \
  --expected-count <exact-pending-and-eligible-count> \
  --expected-fingerprint <dry-run-fingerprint>
```

Success returns `applied_count` and `audit_id` in addition to the approved report.
A rejected attempt raises a command error and does not leave an application audit
or partial changes. Preserve its operational log separately.

## Future production runbook — separate authorization required

1. Complete independent code/test review. Deploy this reviewed revision only via
   the repository's deployment procedure after explicit deployment approval.
   No database migration is needed for this workflow.
2. Obtain and retain the owner's exceptional decision reference. Identify the
   real operator's active superuser account. Separately approve the maintenance
   window and database operation; code delivery is not execution authorization.
3. Prepare and verify the normal production backup/recovery process. Use existing
   owner maintenance access; do not grant DDL to the application/import role or
   add permanent UPDATE/trigger-bypass privileges. The restricted historical
   import role is intentionally unsuitable for this operation.
4. Quiesce application/collectors and exclude concurrent schema maintenance.
   Table locks protect normal writers but cannot be treated as protection against
   another privileged administrator replacing functions concurrently.
5. Record pre-operation scoped counts by status, the report and original
   immutable-reading function/trigger definition. Separately retain protected-state
   verification artifacts from the approved backup/verification process, including
   exact evidence, biology, Boxes and receipts (all fields and archive bytes).
   These before/after artifacts are **not** the command's approval fingerprint;
   the command does not generate receipt hashes or export archives. Verify that
   the normal receipt/provenance guards are enabled and unchanged if schema
   integrity is in doubt; receipt guard definitions are not attested by this
   command. Record foreign-org status counts as a boundary check. Keep artifacts
   in approved restricted operational storage, not Git or chat.
6. Run dry-run. Check the **actual** count, every blocking anomaly and the scope.
   If any evidence or identity mismatch exists, stop the entire operation. Do not
   weaken the validator, modify evidence, or select only the convenient rows.
   A different identity interpretation needs a new explicit scientific decision.
7. Have the authorized operator approve the exact fingerprint/count and reason.
   Run the explicit apply once. If stale, timed out, or failed, do not reuse stale
   approval blindly: rerun dry-run and obtain a fresh approval where needed.
8. Verify the returned audit ID exists in organization 1 with the exact reason,
   operator, attestation, approved IDs/fingerprint and applied count. Confirm all
   approved IDs are confirmed, the pending count is zero in the quiescent org-1
   scope, and confirmed count rose by exactly the approved count. Foreign-org
   rows and previously confirmed rows must be unchanged.
9. Compare the separately retained protected-state artifacts from step 5:
   temperatures, evidence, biology, Boxes and original import receipts must be
   identical. Only approved association statuses and the single appended audit
   may differ. Do not compare the post-apply approval fingerprint with the
   pre-apply fingerprint: confirmation removes the targets from its pending-only
   scope. Compare the original reading function/trigger definitions byte-for-byte
   with preflight. A new dry-run rechecks that guard and should report zero pending
   rows; it is not a receipt-integrity or archive verification command.
10. Verify the existing historical temperature projection/API for representative
    approved Boxes under normal organization permissions. Reconfirmed historical
    values become visible again; live probe and daily-temperature behavior is
    unchanged. Resume services and retain the report/audit verification evidence.
    Consider retiring this one-off command in a separately reviewed cleanup.

### Recovery

Before commit, failures roll back status, audit and temporary DDL automatically.
If command output is lost, inspect the audit and target IDs before retrying:
connection loss during COMMIT can make the client outcome ambiguous. A stale
retry is intentionally refused after a completed commit.

After a successful commit, do not delete evidence/audits or restore the complete
database over subsequent scientific work. If the decision must be reversed,
request separately reviewed, audited `confirmed -> pending_review` maintenance
for the recorded exact IDs. Existing scientific guards already permit only that
status-only invalidation; no automatic post-commit undo command is supplied.

## Operational readiness for a real dry-run

No additional implementation is required to enumerate the organization-1 pending
associations blocked by the **existing evidence/identity contract**. The command
inspects every pending target and collects errors for each incompatible reading;
a mixed eligible/ineligible batch is wholly refused. No receipt-to-reading join
is needed to make that diagnostic complete within this scope.

A separately authorized real dry-run is still necessary to establish the actual
count and blockers. **Do not infer that all pending rows, or 1,175 rows, are
eligible.** If a Box correction leaves source labels incompatible beyond the
already supported PCA alias, those rows will be blocked. Supporting a different
identity interpretation would need a new explicit scientific decision and a
separate reviewed change, not weaker validation here.

Independent re-review, authorized code delivery, verified settings/maintenance
access, a quiet operational window, intact schema and retained owner approval
remain prerequisites. Schema/preflight failure or a lock/query timeout prevents
a complete diagnostic and must be resolved before calling a report complete.
The command does not diagnose archive authenticity, receipt membership, or
corruption introduced by privileged bypasses. No production readiness verdict or
benchmark on the actual target volume is implied by the local tests.

## Local validation

Synthetic PostgreSQL 18 only, new temporary cluster allocated by the existing
launcher; no existing database, production data or Neon is used:

```sh
uv run python scripts/test_historical_temperature_production.py --run \
  --pg-bin "C:/Program Files/PostgreSQL/18/bin" \
  apps.measurements.test_historical_temperature_reconfirm
```

Tests cover dry-run/no mutation, explicit authorization, exact count/fingerprint,
all-evidence/zero/identity/week validation, organization isolation, preserved
original records/receipts/audits, command behavior, stale replay, concurrency,
raw SQL triggers during the temporary exception and after restoration, drift,
replica-mode refusal, and rollback on audit or restoration failure.
SQLite tests prove refusal and existing history regressions, not SQL guarantees.

Initial implementation results (before the receipt-scope correction):

- PostgreSQL 18.6 fresh-cluster launcher, reconfirmation plus existing production
  workflow modules: **85 discovered, 83 passed, 2 expected skips** (SQLite-only
  refusal and opt-in large import scale test), 377.959 seconds. Cluster stopped
  and removed. The reconfirmation module accounts for 27 tests, with its one
  SQLite-only test skipped on PostgreSQL.
- Isolated SQLite, reconfirmation, corrections, storage, apply and Box historical
  API modules: **97 discovered, 72 passed, 25 PostgreSQL-only skips**, 10.057 seconds.
- Django `check` and `makemigrations --check --dry-run` using
  `config.preflight_settings`, dotenv disabled, PostgreSQL opt-in neutralized and
  `DJANGO_DEBUG=1`: passed, no migration drift. The first preflight attempt
  omitted the local debug flag and correctly refused to load without a secret;
  no secret was read or supplied to fix that configuration issue.
- Initial test iterations exposed fixture/expectation errors, corrected before
  the final pass. One earlier combined run exceeded its 240-second tool timeout;
  its stopped synthetic cluster was verified and its temporary directory removed.
- Read-only safety review findings (trigger drift and temporary-exception test
  timing) were fixed and re-reviewed with no remaining implementation blocker.
  This does not replace the requested independent approval before production.

No benchmark at 1,175 pending readings or production count verification was run.

### Targeted receipt-scope correction and fresh validation

The subsequent independent review gave **NO GO for commit** on the Medium
fingerprint/receipt-contract finding. The resolution above intentionally leaves
receipt data out of the fingerprint, documents why it cannot affect eligibility
under the supported immutable contract, and distinguishes preservation from
approval coverage. Runtime validation, locks, SQL allowlist, audit and restoration
are unchanged; the service change is an explanatory docstring only. No migration
or command syntax change is needed.

New PostgreSQL tests:

- Reject changes to all receipt fields, DELETE and TRUNCATE before apply,
  immediately after temporary reading-function installation, and after commit.
- Append org-1 and foreign-org receipts between dry-run and apply, including JSON
  references to a target; verify identical report/fingerprint, original approval
  acceptance, unchanged receipts and foreign reading status.
- Assert the exact hashed snapshot contains every concrete field of only the
  pending reading, linked biology/Box and evidence, plus version/org/function.
- Refuse the old approval after a linked Box metadata change, even with unchanged
  eligibility/counts. The existing same-count biology-change test also remains.

Fresh PostgreSQL validation was independently executed by a separate agent using
the fresh-cluster launcher command above: **31 discovered, 30 passed, 1 expected
SQLite-only skip**, PostgreSQL 18.6, **105.817 seconds**, no failures/errors.
The synthetic cluster was stopped and removed; no existing database was changed.
The full reconfirmation suite reran authorization/count checks, incompatible
sources, zero, organization isolation, temporary allowlist, guard restoration,
audit/restoration rollback and concurrent writer/apply tests, not just the four
new tests. A preceding local test iteration exposed a stale fixture Box revision
in the new exact-snapshot assertion; refreshing fixture objects fixed it before
this independent pass.

Targeted SQLite regressions were rerun from `backend/`:

```sh
PYTHON_DOTENV_DISABLED=1 POLYPBASE_TEST_POSTGRES=0 DJANGO_DEBUG=1 uv run python manage.py test \
  apps.measurements.test_historical_temperature_reconfirm \
  apps.measurements.test_historical_temperature_corrections \
  apps.measurements.test_historical_temperature_storage \
  apps.measurements.test_historical_temperature_apply \
  apps.cultures.test_box_historical_temperature_api \
  --settings=config.test_settings --noinput
```

Result: **101 discovered, 72 passed, 29 PostgreSQL-only skips**, **7.222 seconds**,
no failures/errors. SQLite does not establish trigger or concurrency guarantees.

Django checks were rerun from `backend/` with unconditional in-memory preflight
settings and no dotenv database selection:

```sh
PYTHON_DOTENV_DISABLED=1 POLYPBASE_TEST_POSTGRES=0 DJANGO_DEBUG=1 uv run python manage.py check --settings=config.preflight_settings
PYTHON_DOTENV_DISABLED=1 POLYPBASE_TEST_POSTGRES=0 DJANGO_DEBUG=1 uv run python manage.py makemigrations --check --dry-run --settings=config.preflight_settings
```

Both passed: no system-check issues and no migration drift. The separate agent's
narrow read-only re-review found no blocker in the receipt-scope resolution and
confirmed the documented limitations; this is **ready for targeted independent
re-review**, not production or commit authorization. No real diagnostic, commit,
push, merge or deployment was performed.
