# Transactional institutional AAA change — internal backend only

## Activation boundary

`apps.taxonomy.species_code_change_apply.apply_species_code_change` is an internal
service, not a public mutation API. No URL, serializer action, confirmation UI or
label-print operation invokes it. Preview remains read-only and returns
`advisory_only=true`, `mutation_available=false` and `revalidation_required=true`.
Existing HTTP code-edit paths remain immutable. No real database was recoded in
this implementation task; changes are exercised only through isolated fixtures.

This implementation is ready for independent review, not release-ready.
PostgreSQL concurrency, triggers and rollback guarantees remain unexecuted.

## Internal interface

```python
receipt, created = apply_species_code_change(
    user=user,
    organization=organization,
    profile_pk=profile_pk,
    proposed_code="AUR",
    expected_plan_fingerprint=preview["plan_fingerprint"],
    request_id=request_uuid,
)
```

`profile_pk` identifies the institutional Species profile, not an unscoped client
Species/Box ownership claim. The service reloads the institution, membership and
User, checks active status and existing institutional admin authorization, and
filters the profile by that institution. The existing explicit superuser
break-glass convention is preserved. Cached User flags never authorize either a
new change or a successful-request replay.

The caller must enter without pre-acquired identity row locks. PostgreSQL accepts
only `READ COMMITTED`; `REPEATABLE READ` and `SERIALIZABLE` are rejected before
state reads. A snapshot predating the table fences could otherwise miss a raw
identity insert. SQLite supports isolated functional tests, not concurrency proof.

## Transaction and lock architecture

One atomic transaction covers all writes. Order:

1. Check supported isolation; acquire the existing global Local ID advisory lock.
2. Lock Organization, the actor's membership, then a freshly loaded User.
3. For a new request, take identity-table fences in sorted table-name order,
   all `NOWAIT`: `SHARE ROW EXCLUSIVE` on profile, AAA/BBB assignments, Strain,
   LocalStrainIdentity and Box; `EXCLUSIVE` on BoxCodeNamespace.
4. Lock the profile/assignment, all affected Strains, local identities and Boxes
   by PK. PostgreSQL row-lock acquisition uses `NOWAIT` as well.
5. Recompute the read-only planner, compare its fingerprint, reject all blockers,
   run stricter mutation support checks, and verify complete planner/locked sets.
6. Only then materialize/lock old and new namespaces in sorted order and mutate.

The registry protocol covers Box/alias/namespace writers but not every historical
raw taxonomy writer. Table fences therefore prevent uncoordinated DML and
phantoms during revalidation. The stronger namespace fence also excludes an
existing `SELECT FOR UPDATE` holder, avoiding a row-lock/table-fence cycle.
Existing holders cause a safe busy rejection rather than waiting in reverse order.
No trigger or constraint is disabled by the service.

These fences temporarily delay identity writes in other institutions, although
no other institution's data is modified. This is an explicit conservative
correctness tradeoff for this inactive service, not a throughput-neutral design.
A future API must handle busy/conflict responses and transaction boundaries.

## Records updated and safe write ordering

- Reconcile the old namespace floors and carry them into the target namespaces;
  keep all old namespaces reserved, including gaps in allocated numbering.
- Register each affected operational Box's exact former current Local ID before
  any rename. If a donor already owns an alias resolving to that same canonical
  Box, validate and retain that association instead of moving the alias.
- Update `OrganizationSpeciesCode.code` in place, preserving its PK.
- Recode every applicable exclusively owned Strain, changing only AAA.
- Recode the required operational Boxes with `Box.save(update_fields=["global_code"])`,
  allowing the normal registry and SQL protections to execute.
- Increment `InstitutionSpecies.revision`.
- Write the batch audit and durable success receipt before commit.

`LocalStrainIdentity` has assignment FKs, not a literal AAA column. Its existing
record and assignment FK remain intact and now reference the updated assignment.
No identity record is manufactured to claim an unowned Strain.

Box PKs, Global ID UUIDs, `previous_global_code`, `local_code`, merge relations,
measurement state revisions, statuses, locations, tags, lineage, scientific
measurements, zero values and historical snapshots are not rewritten. Merged
Boxes retain their codes and resolve through explicit same-institution paths,
including valid noncanonical historical donors and incoming donors of another
Species. Strain/Box suffixes BBB, X and YYY are preserved exactly.

## Planner and fingerprint compatibility

The planner is not converted into a mutation or authorization mechanism. Its
identity digest additionally includes each source namespace's existence, stored
high-water mark and effective reconciled floor, using the allocator's actual
read-only calculation across current, previous and registered codes. This also
captures a floor changed by an alias on an unplanned or other-institution Box.
No namespace is created or reconciled while building a preview.

The existing alias, merge, profile revision, local-code and local-assignment
fingerprint inputs remain. Audit/tag counts and scientific activity remain
excluded. Apply independently checks current ownership and supported identities,
not just fingerprint equality. Old fingerprints may be stale after this change.

## Unsupported cases and errors

The service rejects all planner blockers, plus noncanonical live identifiers,
inconsistent local references and Strains not exclusively institution-owned.
It never claims shared, foreign-owned or unowned Strains, resolves historical
ambiguity, bypasses reviewed identity guards or guesses how to recode source
labels. Noncanonical merged historical labels are retained, not recoded.

Existing domain errors are reused:

- `PermissionDenied`: authentication/fresh authority failure.
- `InstitutionSpeciesNotFound`: profile not in the authorized institution.
- `InstitutionSpeciesInvalid.errors`: invalid profile ID, AAA, fingerprint or UUID.
- `InstitutionSpeciesConflict.code` and `.details`: stable conflict/blocker codes.

Conflict codes include `species_code_change_plan_changed`,
`species_code_change_blocked` (`details.blockers`),
`species_code_change_plan_incomplete`, `species_code_change_request_conflict`,
`species_code_change_validation_conflict`, `species_code_change_database_conflict`,
`species_code_change_busy`, `species_code_change_backend_unsupported` and
`species_code_change_isolation_unsupported`. Unexpected database faults propagate
for diagnosis; known conflicts do not return raw constraint/foreign-record text.

## Audit, atomicity and retry safety

One UPDATE audit on the institutional Species records the actor, organization,
batch UUID, request UUID, assignment/profile/Species IDs, confirmed fingerprint,
old/new AAA and revision, complete Strain and Box mappings and retained merged IDs.
Old audits and literal historical identifiers remain unchanged.

`SpeciesCodeChangeRequest` stores the successful normalized intent and result.
The unique key is `(organization, actor, request_id)`, matching the existing
institutional Strain-creation pattern. A retry freshly authorizes, compares the
entire normalized intent (profile, proposed AAA, expected fingerprint), then
returns the original receipt with `created=False`, even after subsequent recodes.
Different intent with the same key conflicts. A new key with an old fingerprint
is stale, not another recode.

Failures roll back namespaces, aliases, assignment, Strains, Boxes, revision,
audit and receipt together. Failed requests leave no success marker and can be
retried using the original intent when the database state still matches.
There is no automatic retry loop for lock/serialization/deadlock errors.

## Migration and rollback

Additive migration `taxonomy.0014_speciescodechangerequest` creates the receipt
model only. It does not initialize Global IDs or modify existing biological data.
Reverse migration refuses if any success receipt exists: dropping receipts while
leaving recoded identities would break retry safety. PostgreSQL requires an atomic
READ COMMITTED rollback and acquires `ACCESS EXCLUSIVE NOWAIT` on the receipt
table before checking emptiness, retained through DropModel. A concurrent writer
therefore prevents the downgrade or has its whole mutation rejected/rolled back.
Reversing this schema migration is never an undo of a Species code change.

## Local validation

Only isolated `config.test_settings` databases were used, with `POSTGRES_DB=''`
and `POLYPBASE_TEST_POSTGRES=0`.

- Mutation module: 30 tests run, 21 passed, 9 PostgreSQL-only skips.
- Combined mutation/preview, institutional Strain creation and Species API,
  alias registry/resolution, Global ID and audit regressions: 191 tests run,
  166 passed, 25 skipped. These overlap the mutation module; do not sum them.
- Django system check and `makemigrations --check --dry-run`: passed.
- Whitespace checks cover both tracked diffs and new files.

Functional tests cover full-model preservation snapshots, measured zero values,
old/new/multiple-alias resolution, UUID/PK/QR preservation, foreign isolation,
stale/colliding plans, unsupported identities, successful and conflicting replays,
multiple recodes, merged donors, allocation floors, namespace/audit/receipt and
intermediate-write failure rollback, preview read-only behavior and absence of
an exposed mutation route. An actual SQLite migration reversal succeeds while
empty and refuses after a successful operation without losing receipt/history.

PostgreSQL-only tests cover duplicate submissions, raw insert visibility between
authorization and fencing, writer/namespace contention, fenced writes, unsupported
isolation and concurrent receipt rollback. They were not run: Docker did not
respond and no verified disposable instance was available. Outstanding 0019
PostgreSQL alias/trigger/rollback tests remain unverified as well. SQLite cannot
certify these guarantees. No frontend activation or browser QA was needed for
this backend-only change; the full repository QA aggregator was not run.

## Requirements before user-facing activation

1. Independent review of this service, migration, tests and the extended fingerprint.
2. Execute mutation and outstanding 0019 concurrency/trigger/rollback suites on a
   verified disposable PostgreSQL instance; investigate every failure before release.
3. Review table-fence contention and agree on busy/retry UX and transaction ownership.
4. Design an explicitly authorized API, confirmation flow, request UUID lifecycle,
   stale-plan refresh and safe successful-replay presentation. Keep preview advisory.
5. Complete code-based consumer decisions: historical scientific CSV matching,
   Transfer v1 Strain matching, exports, receipts and printed-label implications.
   This service does not silently change those contracts or create reprint jobs.
6. Review existing historical blockers on an explicitly authorized deployment
   preflight. No inference or automatic reconciliation is part of activation.
7. Perform integration/browser QA and the separate authorized deployment workflow.

No commit, push, stash, deployment, shared database, Neon or production access.
