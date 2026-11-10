# Box 1902 — executable PostgreSQL authority proof

> **Current implementation: READY_FOR_INDEPENDENT_REVIEW.** The actual v2
> correction is now integrated and locally tested: **28/28** real-role PostgreSQL
> correction tests, **25/25** original authority proofs replayed, and **48/48**
> safety/execution-boundary regressions. No final test was skipped. See the
> [implementation handoff](#actual-v2-workflow-and-local-validation).
> Production remains untouched and its current runtime-owned topology is unsafe.
> The proof-only limitations below describe the earlier stage, not an assertion
> that the v2 correction is still disconnected or blocked unconditionally.

## Verdict and scope

**AUTHORITY_BOUNDARY_PROVEN**, on a fresh disposable **PostgreSQL 18.6** cluster.
This verdict concerns only the minimal SQL authority boundary. The full Box 1902
correction remains **BLOCKED**. Migration 0018, the application safety stop, the
existing correction workflow, and CCA/EOB infrastructure are unchanged.

No production/Neon access, production provisioning, privilege changes, deployment,
commit, push or merge. Nothing in this proof authorizes a production operation.

## Files added

- `backend/apps/cultures/test_box_1902_authority_postgres.py`: 25 executable
  real-role tests; local provisioning and catalog assertions confined to the
  leased cluster.
- `scripts/box_1902_authority_fixture.sql`: minimal synthetic tables and writer,
  explicitly test-only, not a migration or production provisioner.
- `docs/box-1902-postgresql-authority-proof.md`: this report.

The existing `scripts/test_historical_temperature_production.py` is reused
unchanged. It creates the fresh cluster, strips inherited database configuration,
disables dotenv, pins loopback/port, runs the tests and stops/removes its cluster.
The new class requests no Django test database; all authority assertions execute
through genuine psycopg connections with distinct `session_user` identities.
There are no mocked permission decisions or SQLite authority assertions.

The tests require the harness lease, check its temporary-directory prefix,
endpoint/database/user, PostgreSQL major version and actual `data_directory`
before provisioning. Only the disposable cluster's own `subculture_qa`
administrator performs privileged setup and fault injection.

Two separate databases are created. Every test reconstructs both fixtures:

- `box1902_owner_proof`: database, schemas, tables and functions owned by
  `polypbase`, with CREATE public and no explicit runtime memberships;
- `box1902_hardened_proof`: initially the same ownership, followed by explicit
  local transfers and grants described below.

Database objects/data are isolated between these environments, while roles are
cluster-wide as PostgreSQL requires. The databases are dropped at class cleanup.
Schemas, rather than whole databases, are reset between tests to avoid repeated
Windows checkpoint/file-removal costs. No deployed migration is executed.

## Exact mechanism demonstrated

1. Independent `box1902_approver` inserts a single fixed-scope approval, including
   operation, organization 1, Box 1902, old/new codes and Strain IDs, executor,
   synthetic review reference and expiry. Its INSERT grant excludes consumption.
   It cannot update/delete/reissue approval or invoke the writer.
2. `box1902_executor` alone can call `box1902_private.apply(integer)`. The function
   also requires that exact **session_user**, independently of its EXECUTE ACL.
3. The writer runs as the protected NOLOGIN owner, locks approval, checks expiry
   and READ COMMITTED, takes fixture maintenance locks, rechecks expiry, and
   checks exact source Box/target taxonomy. The minimal target fixture uses
   Strain 285, institution 1, `CLC-JKA-1`, Species 164, `Chrysaora lactea`.
4. It inserts a private context bound to approval, full before/after Box images,
   transaction ID (`xid8`), backend PID and session identity. It changes only the
   Box's code and Strain.
5. The Box trigger is **SECURITY DEFINER**, owned by the protected owner, with
   `search_path=pg_catalog, pg_temp` and qualified relation references. Runtime
   has neither schema access to its private context nor direct EXECUTE on the
   trigger function; PostgreSQL still invokes the bound trigger successfully.
6. The trigger checks the independently stored approval and exact context. It
   acknowledges an accepted exception by marking the context `validated` and
   skips ordinary historical invalidation only for that exact transition.
   Otherwise the original guard behavior remains in force.
7. The writer requires both the exact updated Box and the trigger's protected
   acknowledgement. An exception that was not accepted cannot be reported as a
   successful correction. It writes a technical outcome, consumes approval and
   deletes context in the same transaction.
8. Replay is rejected. Two concurrent real executor connections yield exactly
   one application and one refusal. Failure in audit, consumption or the outer
   transaction restores Box, statuses, approval, context and outcome together.

The ordinary Box guard body comes from migration 0014, with relation names
qualified, SECURITY DEFINER/fixed search_path added and one private exception
predicate inserted. It is not taken from the insecure 0018 prototype. No global
source-label alias or historical evidence rewrite is introduced.

### Expiry race found and closed inside this proof

Expiry checked only before maintenance locks is insufficient: expiry while
waiting could cause the trigger to use ordinary invalidation, while the writer
reports success. The fixture now rechecks after locks **and** requires the
trigger acknowledgement after UPDATE. Regression tests actually hold a conflicting
lock until expiry, verify refusal and an unchanged snapshot, and independently
remove the acknowledgement to prove that the writer rolls back instead of
accepting a Box-only postcondition.

## Role, ownership and ACL topology

All six fixture roles are NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOINHERIT,
NOREPLICATION and NOBYPASSRLS. All are login roles except the protected owner.

| Role | Authority in the hardened database |
|---|---|
| `polypbase` | CONNECT, USAGE public, SELECT on public fixture tables, UPDATE on explicit ordinary Box columns. No database ownership/CREATE/TEMP, schema CREATE, protected ownership, private data access or exceptional EXECUTE. |
| `box1902_owner` | NOLOGIN; owns hardened database, public/private schemas, protected tables, indexes and functions. Trusted owner, outside the hostile-runtime boundary. |
| `box1902_approver` | CONNECT, private schema USAGE, column INSERT on approval excluding `consumed_at`. No approval UPDATE/DELETE/TRUNCATE, context access or writer EXECUTE. |
| `box1902_executor` | CONNECT, private schema USAGE, EXECUTE only on the fixed writer. No direct Box, approval, context or outcome DML. |
| `box1902_migrator` | CONNECT and the sole explicit membership: owner with ADMIN FALSE, INHERIT FALSE, SET TRUE. Must explicitly SET ROLE owner for trusted DDL. |
| `box1902_other` | CONNECT and private schema USAGE, deliberately without writer EXECUTE, to test the function ACL independently of schema access. |

PUBLIC has no database CONNECT/CREATE/TEMP, protected schema privileges or private
table privileges. Function ACLs are exactly owner/executor EXECUTE for `apply`,
and owner-only EXECUTE for `box_guard`. Default owner function privileges revoke
PUBLIC EXECUTE for future DDL. Runtime table ACL refusals are also tested after a
trusted test-only schema-USAGE grant, so schema denial does not mask table access.

Catalog assertions cover `pg_roles`, `pg_database`, `pg_namespace`, `pg_class`,
`pg_auth_members` including ADMIN/INHERIT/SET options, effective
`pg_has_role(... MEMBER/USAGE/SET)`, column/table/schema/database privilege
functions, `pg_proc` owner/SECURITY DEFINER/proconfig/expanded ACL, and the actual
trigger binding and enabled state. Actual runtime `SET ROLE`, session
authorization, self-grants, elevated role changes, function replacement, trigger
disable/drop and `session_replication_role` changes are refused.

The owner-based environment demonstrates the opposite: despite PUBLIC revokes,
`polypbase` can forge approval/context, replace the trigger function and disable
the trigger. **The current production-like ownership topology is not secure
against its runtime.** The tests do not reinterpret this as protection.

## Executed results

From repository root:

```sh
.venv/Scripts/python.exe scripts/test_historical_temperature_production.py --run --pg-bin 'C:/Program Files/PostgreSQL/18/bin' apps.cultures.test_box_1902_authority_postgres
```

Final complete run: **25 run, 25 passed, 0 failed, 0 errors, 0 skipped**, in
**133.962 seconds**, PostgreSQL **18.6**. The cluster was stopped and removed.
The runner's “Skipping setup of unused database(s): default” means no Django
migration database was requested; it does not mean that any SQL test was skipped.

| Coverage group | Passed test methods |
|---|---:|
| Starting runtime-owner topology and successful owner bypasses | 3 |
| Hardened catalogs, genuine identity/escalation, DDL denial and future migrations | 4 |
| Runtime/approver/executor ACL separation, unauthorized/absent/expired approval | 4 |
| Exact synthetic correction, 41 confirmed statuses, values/evidence and other Boxes | 1 |
| Replay/reissue and concurrent single-use execution | 2 |
| Ordinary guard, invoker failure/definer solution, caller GUCs and context binding | 4 |
| Audit failure, consumption failure and outer transaction rollback | 3 |
| Expiry under lock, missing trigger acknowledgement, isolation/overlong expiry | 3 |
| One-off writer retirement | 1 |
| **Total** | **25** |

Full snapshots include all minimal tables. The positive test verifies unchanged
41 historical rows, biological zeros versus NULL, source evidence and witness
Boxes in both institutions, except the intended Box identity and protected
approval/outcome records. These are synthetic fixtures, not production evidence.

Earlier attempts are not silently counted as successful:

- Initial 21-method suite: **20 passed, 1 assertion failure, 0 errors, 0 skips**.
  The catalog assertion mistakenly included PostgreSQL's three built-in
  `pg_monitor` memberships. It now checks every edge touching a fixture role,
  while effective membership/SET tests remain exact.
- Targeted follow-up: **4 run, 4 passed, 0 failed/errors/skipped**, 38.490 s,
  covering catalogs, expiry under lock, mismatched contexts and acknowledgement.
- An intermediate 25-method launch timed out at the external 300 s bound.
  The captured log contains **19 `ok`, 5 `ERROR` markers and one method without
  a result**, not a completed suite. Its server log shows repeated 10 s
  `DROP DATABASE` statement timeouts and subsequent database-already-exists
  setup errors. Resetting schemas and using a separate bounded lifecycle timeout
  resolved that harness problem. The stopped cluster was explicitly checked
  and removed; logs were retained locally under `.local-docs/`.
- Existing safety-stop regressions, separately on isolated SQLite:
  **8 run, 8 passed, 0 failed/errors/skipped**, 0.977 s. These confirm the stop
  remains in place, not the SQL boundary.
- Python compilation and `git diff --check`: passed.

## One-off operator capability versus permanent exposure

The executable evidence supports a **temporary, independently controlled
executor capability**, not permanent runtime access. A retirement test revokes
EXECUTE, verifies effective denial on an already-open executor session, disables
new logins, drops the writer and verifies that ordinary historical invalidation
still works. The consumed approval and outcome are retained. Revocation is
checked explicitly, so consumed-approval refusal cannot masquerade as ACL denial.

This reduces persistent exposed capabilities while retaining PostgreSQL-enforced
approval, fixed scope and atomicity. It is preferable for this single exceptional
operation to granting the application permanent exceptional EXECUTE. It is **not**
permission for an operator to bypass Django's future reviewed workflow with an
ad hoc UPDATE or superuser correction. A complete workflow and durable scientific
correction record remain necessary before implementation or execution.

## Minimal future privilege transition — not executed in production

The demonstrated dependency is removal of runtime ownership and DDL authority,
not creation of another runtime-owned table:

1. Separate trusted NOLOGIN ownership from runtime for the database, protected
   schemas, Box/history/evidence/audit objects and all enforcing functions.
   Revoke runtime/PUBLIC CREATE and escalation paths. Real application inventories,
   extension objects and existing grants must be reviewed; the minimal fixture is
   not a production inventory.
2. Use an independently controlled migration/DDL login. Explicit SET ROLE owner
   supports future Django table/function migrations; the real test alters a
   protected table and creates a default-private function in that role, while
   the same login before SET ROLE is refused. Runtime has no membership path.
   DDL authority is trusted and can alter enforcement: it must not share runtime
   credentials or be active in the application process.
3. Regrant only ordinary application DML, with protected invoker/definer paths
   reviewed for compatibility. Do not grant private-context writes as a workaround.
4. Keep approval issuance independent and exception EXECUTE exclusive to a
   short-lived dedicated executor. Retire it after the separately reviewed
   operation without deleting provenance or breaking ordinary guards.

These are requirements established by the local proof, not a production
provisioning script or an authorization. Production transition, backup,
maintenance and approvals require a separate operator review and explicit
permission. No CCA/EOB role or infrastructure is created by this proof.

## Remaining limitations

- Minimal SQL fixtures, not a migrated complete Django schema. No claim that all
  real application grants, validators or concurrency consumers are integrated.
- Approval contains only fixed technical scope and a synthetic review reference;
  **no complete manifest, authenticated production plan or scientific reconciliation**.
- No production snapshot is taken, historical association is reinterpreted or
  laboratory attestation invented. Full correction and preservation provenance
  remain blocked until those later tasks are implemented and independently reviewed.
- The ordinary runtime may still change Box identity with ordinary invalidation;
  what it cannot do is invoke or fabricate the **preserving exception**. This
  distinction is deliberate and tested.
- The cluster uses loopback trust authentication inherited from the existing
  disposable harness. The proof covers SQL authorization under genuine roles,
  not production login authentication, OS isolation, root-controlled artifacts,
  backup, secret custody or Linux peer sockets.
- Cluster administrator, protected owner, approval authority and trusted DDL
  operator are trust roots, not adversaries defeated by these ACLs. A hostile
  superuser/DDL owner can always alter PostgreSQL enforcement.
- No production-ready provisioning/attestation command or full workflow is
  delivered. Existing migration/application safety interlocks are unchanged.

Stop here: the requested technical authority boundary is demonstrated. Await
independent review before any next implementation or production procedure.

## Operator contract added after the proof — not execution authorization

The report above records the earlier synthetic proof, not validation of the new
v2 implementation. The SQL-only implementation now lives in
`backend/apps/measurements/box_1902_sql.py`; migration 0018 installs it only after
independent provisioning. Application/workflow integration and real-role testing
of this implementation are separate requirements. No production SQL was run.

### Emit-only tooling and independent identities

`deploy/scripts/box_1902_privileges.py` imports no settings, credentials or database
driver and only writes SQL to stdout:

- `provision`: first installation only, reviewed by the separately authorized
  cluster administrator. **All four technical roles must be absent**: owner,
  approver, executor and migrator. Any preexisting role is rejected before role,
  membership, ownership or ACL changes, even with matching attributes or an
  expected marker. A completed provisioning cannot be repeated; recovery of
  precreated roles requires a separate reviewed operator procedure, not automatic
  adoption or cleanup. The existing `polypbase` runtime is accepted only after
  checking its unprivileged login attributes, absence of membership edges, actual
  database/public-object ownership and absence of trigger-bypass privileges.
  Unexpected memberships are refused, never silently revoked. Strict CREATE ROLE
  statements also fail on concurrent name collisions instead of adopting them.
  Creates fresh technical roles without passwords, installs only the
  migrator-to-owner SET-only membership, transfers **all**
  public relations/sequences/routines and database/public-schema ownership to the
  NOLOGIN owner, removes public/untrusted DDL and public write grants, preserves
  ordinary runtime DML, and grants the executor public SELECT only. Existing
  PUBLIC routine execution is preserved for runtime, not for the executor.
  This is deliberately broad trusted DDL, not an application migration. Review
  extensions, external roles, existing grants and future migration compatibility.
  Reprovisioning an installed or retired capability is refused.
  Focused collision validation: **11/11 tests passed, 0 failed/errors/skipped**, on
  disposable PostgreSQL 18.6 (55.424 s), module
  `apps.cultures.test_box_1902_provisioning_postgres`. Rejected attempts preserve
  role attributes/settings/comments, memberships, ownership, grants/column ACLs,
  default/parameter ACLs and synthetic data. Runtime escalation remains denied.
- `approve --plan PATH --manifest PATH --actor-id ID --expires-at ISO_TIMESTAMP`:
  review and execute through the **independent approver login**, never runtime or
  executor. The plan file must contain the exact canonical JSON bytes (no trailing
  newline): `json.dumps(plan, sort_keys=True, separators=(',', ':'),
  ensure_ascii=False, allow_nan=False)`. Hash is SHA-256 of those UTF-8 bytes.
  The manifest is the separately reviewed result of the SQL manifest function,
  not an ORM reconstruction. The plan must embed that exact object under
  `manifest`; SQL checks equality with both the approval manifest and the fresh
  database manifest. The separately approved `actor_id` must match the apply
  argument and pass active administrator validation under maintenance locks.
  Expiry must include a timezone and SQL permits at most one hour. Only column
  INSERT is granted; consumption is not approver-owned.
- `retire`: review as separately authorized cluster administrator. Drops only
  lock/apply, disables executor logins and retains approval, acknowledged context,
  correction, audits, manifest/evidence helpers and the original trigger bindings.
  Existing executor sessions also lose the writer because its functions are gone.

Install 0018 through a genuine `box1902_migrator` session explicitly executing
`SET ROLE box1902_owner`. The migration changes no roles or memberships and fails
closed on missing/unsafe ownership or privileges. Reversal always raises
`IrreversibleError`; retirement is not reversal.

### Exact SQL/API surface

Operation: `cla_box_1902_preservation_20261109_v2`.

| Function | Result and access |
|---|---|
| `public.box_1902_manifest_v2()` | `jsonb`; read-only definer, runtime/approver/executor |
| `public.box_1902_lock_v2(actor_id bigint)` | `void`; executor session only; READ COMMITTED; all public/private tables EXCLUSIVE NOWAIT; active organization-1 admin validation |
| `public.box_1902_apply_v2(actor_id bigint, plan_hash text)` | `void`; executor session only; calls lock again; exact approval/manifest/scientific preconditions, SQL namespace registration, atomic Box update, receipt and audit |
| `public.box_1902_evidence_valid_v2(evidence_id bigint)` | `boolean`; PUBLIC executable narrow exact-row predicate, no private record disclosure |

The executor workflow must use one explicit transaction: lock, compute/check the
reviewed ORM plan, apply, verify and commit. Do not call lock in autocommit and
assume its locks survive. A failed lock or apply requires rollback, never fallback
to the old invalidating correction. Runtime has no exceptional EXECUTE grant.

Private durable tables are `box1902_private.approval_v2`, `context_v2` and
`correction_v2`. Approval id is permanently 1; no reissue or replay is allowed.
Context acknowledges once and is retained, not deleted. Correction retains full
before/after Box images, the original full manifest, approved plan text/hash,
actor, executor, consumption timestamp and approval reference. Audit inserts
both the Box event and `cla_box_1902_correction` receipt with object id `1902`.
Original historical evidence and existing audits are never rewritten.

Exports used by the dedicated workflow: `SQL`, `GUARD_SQL`, `GUARD_NAMES`,
`EXPECTED_DEFINITIONS` (schema-qualified name to exact `pg_proc.prosrc`),
`FUNCTION_SIGNATURES`, `canonical_plan(plan)`, `plan_digest(plan_text)`,
`assert_topology(connection, installation=False)`,
`attest(connection, allow_retired=True)`, `provision_sql()`,
`approval_sql(plan_text, actor_id, manifest, expires_at)` and `retirement_sql()`.
Connection helpers expect a Django PostgreSQL connection, read catalogs only and
raise `ContractError` on drift. `attest` returns `{'operation': OPERATION,
'retired': bool}`; a partial retirement is rejected. These checks do not replace
independent scientific plan review or deployment approval.

## Actual v2 workflow and local validation

**READY_FOR_INDEPENDENT_REVIEW — not authorization to deploy or correct production.**
Only organization 1, physical Box 1902, source 213 / Species 131 is accepted;
target is fixed to Strain 285 / organization 1 / `CLC-JKA-1` / Species 164 /
`Chrysaora lactea`. Shared source Strain 213 remains eligible without changing its
ownership. Box PK, local code `CLA-JKA-1.09` and inactive status do not change.

### End-to-end path

`correct_cla_box_1902` invokes `cla_box_1902.build_plan()` or `apply_plan()`.
The operation is now **`cla_box_1902_preservation_20261109_v2`**; the old v1
operation is rejected, not mapped to this operation.

Planning uses the existing target-bound read-only session. The complete reviewed
plan includes the canonical SQL manifest, schema/privilege attestation, exact
source/target references, all institution Boxes, dependency closure, namespaces
and prior institution audits. It reports preservations, not pending-review
invalidations. The SQL manifest includes full ordered rows for every direct
biology row, all linked readings/evidence, source/target Strains and Species and
the Box. Scientific text is retained verbatim; numeric zero and null stay distinct.

Application must run as the dedicated executor, not the normal runtime:

1. `identity_execution.session()` checks the root-authenticated target and
   authorization, independently restored fresh backup, held archive/socket
   guards, PostgreSQL identity, operation, scope and reviewed plan hash.
2. The live capability and privilege topology are attested. The SQL maintenance
   barrier locks every public/private table NOWAIT and verifies an active user
   with active institution-1 Admin membership. Django rechecks that permission.
3. The plan is rebuilt under those locks and its full fingerprint must match the
   reviewed hash. Foreign dependency conflicts and changed references still block.
4. The service invokes **`public.box_1902_apply_v2(actor_id, plan_hash)`** on that
   same connection and transaction. No ORM Box update or caller-written context
   remains. SQL checks the independently issued approval and exact fresh manifest.
5. SQL registers both namespace counters without lowering them, performs the
   acknowledged exception, verifies scientific equality, consumes approval and
   writes immutable correction provenance and both audit events.
6. Django compares all institution Boxes and dependency rows with the exact
   expected state and checks its receipt and preservation of prior audit rows.
   Any Python postcondition failure rolls back the already executed SQL writer.
   Existing root/backup/target checks run again before the owned transaction commits.

The independent approval cannot be inferred from root approval or minted by the
runtime/executor. Both authorization boundaries are required for the actual
command. Direct SQL tests exercise the independent SQL boundary separately; they
are not an alternative production entrypoint. The real approval emitter and its
SQL guard reject the proof's former minimal documents: a complete READY v2
workflow plan, its exact scope, attested execution binding and embedded manifest
are mandatory. A direct-SQL negative test exercises this refusal under the actual
approver login, not only in Python.

### Fresh artifact preparation for separate operator review

No production commands were executed. The following is the contract for the
future independently reviewed operator workflow, not permission to execute it:

- Independently verify the exact production endpoint, cluster/database identity,
  OS peer identity and maintenance/backup boundaries **before** any privilege
  transition. Provision SQL acts on `current_database()`; never execute its output
  against an inferred/default connection. Review every generated ownership/grant
  change and existing extension/external-role interaction before approval.
- Provisioning is outside Django migrations. Its emit-only CLI performs no
  connections. Roles have no password or authentication setup in its output;
  independent operator authentication and credential custody remain to arrange.
- Run migrations only through the independently controlled migrator explicitly
  assuming the NOLOGIN owner. Ordinary runtime migrations must not be used to
  regain ownership. Future migrations need the same trusted DDL boundary.
- After installation, use the dedicated executor's explicitly attested endpoint
  to obtain **a fresh full plan**, not the historical production observations.
  The command prints only a redacted summary. Prepare protected plan and manifest
  files through `cla_box_1902.build_plan()` and
  `box_1902_sql.canonical_plan(plan)` / `canonical_plan(plan['manifest'])`, keeping
  exact UTF-8 bytes and no trailing newline. Do not log scientific contents.
- Independently review those files, identity, actor and complete preservation
  scope. Arrange and independently verify the fresh root authorization and backup
  first, bound to the same v2 operation, executor target, actor and plan hash.
  Only after that review may the independent authority issue the live SQL approval
  through `box1902_approver` using the emitter. Do not activate the capability
  while root authorization or backup validation is still pending.
- Invoke the **existing dedicated command**, in that operator-controlled executor
  context, with organization 1, Box 1902, source Strain 213 / Species 131, target
  285, active Admin actor and the exact hash. Do not use a direct SQL UPDATE or
  call the SQL writer as an alternative to the protected workflow.
- Verify full receipts and preservation, then obtain separate authorization to
  retire the transient writer. Keep the approval, acknowledged context, correction
  record, exact original manifest and audit history. Root authorization v1 cannot
  be reused. Do not reverse/fake 0018 or delete its migration record.

Topology checks additionally reject runtime-accessible unreviewed SECURITY
DEFINER routines and untrusted SET/ALTER SYSTEM parameter ACLs capable of setting
`session_replication_role`. They are not satisfied merely by nonsuperuser flags.

### Historical scientific validity

The protected correction stores original full evidence, reading and biological
rows together with the exact identity transition, approval, actor, reviewed plan
bytes/hash, actual executor, scientific-decision reason and consumption timestamp.
The reason attributes the explicit product decision to Anthony's orally reported
laboratory confirmation; it does not invent written laboratory attestation.

The source-label exception is the exact protected record, not a global alias:
SQL and Python permit the original CLA label only for its registered persisted
row, unchanged original science and the actual corrected Box/taxonomy. Python
also compares every candidate evidence field with the stored row, so modifying
an in-memory object while retaining its PK cannot borrow the exception. New or
unrelated evidence cannot use it. Later ordinary scientific or identity changes
retain their original invalidation behavior; no reconfirmation is performed.

Retirement removes only lock/apply and executor login capability. The immutable
record and narrow evidence validator remain usable after retirement. Migration
reversal always refuses rather than erasing the explanation for confirmations.
Migrations 0012 and 0014 are unchanged.

### Final executed checks

PostgreSQL commands, from the repository root, each allocate a fresh cluster:

```sh
.venv/Scripts/python.exe scripts/test_historical_temperature_production.py --run --pg-bin 'C:/Program Files/PostgreSQL/18/bin' apps.cultures.test_box_1902_correction_v2_postgres
.venv/Scripts/python.exe scripts/test_historical_temperature_production.py --run --pg-bin 'C:/Program Files/PostgreSQL/18/bin' apps.cultures.test_box_1902_authority_postgres
```

| Final check | Passed | Failed | Errors | Skipped | Test time |
|---|---:|---:|---:|---:|---|
| Actual correction, migrated populated schema, distinct real roles, PostgreSQL 18.6 | 28 | 0 | 0 | 0 | 333.565 s |
| Original authority-boundary proof replayed, PostgreSQL 18.6 | 25 | 0 | 0 | 0 | 101.972 s |
| Updated safety stop plus root/backup/execution regressions, isolated SQLite | 48 | 0 | 0 | 0 | 9.477 s |

The SQLite invocation used explicit `POLYPBASE_TEST_POSTGRES=0`,
`PYTHON_DOTENV_DISABLED=1`, `DJANGO_DEBUG=1`, `--settings=config.test_settings` and
only `apps.cultures.test_box_1902_safety_stop apps.cultures.test_identity_execution`.
These 48 tests are not PostgreSQL authority proofs. All leased clusters were
stopped and removed. Django preflight check and migration-drift check passed
with unconditional SQLite `config.preflight_settings`; no model drift detected.
Python compilation and final whitespace checks also passed.

The 28 real integration tests include the actual command; full populated-history
installation/refusal under unsafe ownership; all 41 confirmed statuses and original
science/evidence; other institutions and transitive-history witnesses; modified,
omitted, reordered, substituted or extra manifest data; wrong actor/membership/
identity; stale biology; SQL ACL and role escalation; replay/concurrency; SQL
faults and outer/Python-postcondition rollback; exact immutable history; retirement;
and strict historical/CCA attestation. CCA helper absence is deliberately rejected
**after** the new historical guards pass; the test does not install CCA roles or
claim a complete CCA correction execution. The CCA drift test rejects altered
protected guard configuration. The old v1 invalidation suites are not evidence
for this version and were not used as acceptance gates.

Earlier attempts are distinct from the final results:

- Initial installation preparations failed before executing test methods on
  catalog evaluation of sequence privileges and private constraint attestation;
  these SQL defects were fixed, not bypassed.
- Preparatory 120 s and 180 s launches timed out without a final report and are
  not counted as passing tests.
- First completed three-label attempt: 1 pass / 2 errors (cloned database default
  TEMP ACL; QA directory metadata access). The fixture now reapplies CONNECT-only
  database ACL and provides a zero-argument, fixed, test-only catalog reader;
  no production or role-membership exception was added.
- Next three-label attempt: 2 pass / 1 failure (Django raw JSONB text was not
  decoded before manifest comparison). Fixed before fingerprinting.
- Actual-command rerun: 1/1 passed; then an earlier full integration run passed
  25/25 and 27/27. The final 28-test run also rejects proof-only approvals; its
  three-label prerequisite run passed 3/3. Parameter-ACL and unexpected-definer
  rejection remain covered in that final complete run.
- One 46-test SQLite launch timed out during setup; its completed retry reported
  2 failures and 4 errors including subtests, due to mock connection USER values
  still describing a production role. Test fixtures were updated for the explicit
  QA role, and v1-rejection/QA-reader regressions added. Final 48/48 passed.

No production privilege is changed or claimed safe. Independent review, actual
production inventory/privilege transition, authentication, fresh backup/restore,
maintenance, fresh complete plan/manifest and both approvals remain prerequisites
for any production action. No commit, push, merge or deployment was performed.
