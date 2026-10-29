# Durable ordinary Box creation intents

## Why registration is separate

A receipt written only when a Box commits cannot distinguish a request that was
never sent from a delayed creation. UUID-only inspection must never cancel or
unlock that absence. The homepage instead registers an immutable intention
before execution and retains enough evidence to repeat registration safely.
No researcher-facing request or cancellation controls are added.

A dedicated intention model was unnecessary: `AutomaticBoxCreationRequest` now
supports protected pending and terminal states while retaining legacy receipts.
A single atomic register-and-execute endpoint would not independently preserve
registration when execution rolls back. Expiry-based release and cancellation of
stored UUID candidates remain unsafe and are not used.

## Authenticated API

All three endpoints use authenticated session/CSRF protection, require an explicit
`X-Organization-Id`, and revalidate active institution, active actor and current
Admin/technician authority under organization -> membership -> actor row locks.
No intention, token hash or raw token is returned. No token is written to audits.

### Register: POST /api/boxes/automatic/intents/

Body: the existing `BoxCreatePayload` plus `resume_token` (an independently generated
UUID). It binds institution + actor + request_id + normalized immutable parameters
and SHA-256 of the recovery token. The token hash is globally unique: a token
cannot be rebound to another UUID, actor or institution, including racing inserts.
A binding conflict returns generic 409 without revealing the other scope.

Normalization is shared with ordinary creation: integer strain/zone, ISO date,
two-decimal volume (zero distinct from null), stripped note and existing defaults.
Same binding and normalized payload returns the existing receipt without writes;
any differing payload or token conflicts even after completion or rejection.
Registration reserves no identifier or namespace and creates no Box/location/audit.

With valid request/token UUIDs, syntactically invalid parameters are durably
rejected, with the original input retained under `intent.invalid_parameters` and
field errors in `failure`. They can never execute or be corrected in place. Invalid
request/token UUIDs cannot identify an intention and return ordinary 400 without
creating a receipt. Authorization failures never register or reject an intention.

### Execute: POST /api/boxes/automatic/intents/execute/

Body accepts only `request_id` and `resume_token`. No replacement parameters are
accepted. Missing receipts or wrong proofs return 409 without any write.

A pending receipt executes only its stored normalized parameters. Current
strain/zone/identity checks, shared namespace allocation, Box, initial BoxLocation,
creation audit and receipt-to-Box link commit atomically. Committed receipts return
the current authorized Box without revalidating obsolete strain/zone eligibility.
A Box reassigned outside the institution is never disclosed.

Business ValidationError runs inside an execution savepoint: all partial creation
writes roll back before a durable rejection is saved in the outer transaction.
Unexpected/database errors roll execution back entirely, leaving an independently
committed registration pending and safely executable again. Permission failures do
not terminalize the receipt. There is no durable executing state to become stuck:
concurrent operations serialize on the existing institution lock.

### Cancel: POST /api/boxes/automatic/intents/cancel/

Body accepts only the matching key and token. This endpoint is not called by the
homepage UI. It can terminalize an existing pending registered intention, retaining
its original intent and proof hash; it never creates an absent-key tombstone.
A wrong token, including A's token paired with independent B's UUID, cannot mutate B.
A committed Box always wins and is returned, never cancelled or deleted.

### Outcomes (HTTP 200)

- `{"status":"pending"}`: registered, no execution yet.
- `{"status":"created","box":<BoxDetailSerializer>}`: backend-confirmed Box.
- `{"status":"rejected","errors":<field errors>}`: durable terminal refusal.
- `{"status":"cancelled"}`: existing durable terminal cancellation.

Registration can return any existing outcome. Execute cannot return pending on
success. Rejected/cancelled intentions cannot later execute. The frontend unlocks
only after a confirmed terminal response, not after an arbitrary 4xx or lookup miss.

## Lifecycle and races

`absent -> pending -> created | rejected | cancelled`

Invalid parameters with valid identity/proof: `absent -> rejected`.
Terminal states are never reset and their immutable parameters are never replaced.

- Registration never arrives: the intact client repeats the same key/proof/payload,
  registers it and executes. It never needs to cancel an unknown request.
- Registration commits but response is lost: registration repeats without mutation.
- Execution commits but response is lost: registration returns the committed Box;
  no second execution/allocation is needed.
- Execution still running: registration/execution/cancellation waits for the same
  institution lock, then observes the committed result or rolled-back pending state.
- Cancellation wins: delayed execution observes the terminal state and creates no Box.
- Execution wins: cancellation returns the existing Box without mutation.
- Registration/cancellation rolls back: no partial state survives; retries remain safe.
- Independent requests have independent keys/proofs; their Strains may differ. They
  never share cancellation authority and retain the existing shared allocator rules.

## Browser persistence and recovery

Before any network mutation, the client persists a raw request UUID and a version-2
JSON envelope containing scope (actor/institution storage key), UUID, independent
recovery token, full unchanged payload and SHA-256 integrity digest. The digest
covers scope, UUID, token and every creation parameter. It detects accidental local
corruption; it is not server authorization or a signature. Backend proof/intent
matching remains authoritative. Storage failure sends nothing. Submit is reserved
synchronously while asynchronous integrity/persistence runs, preventing double-clicks.

After reload, reopening or connectivity restoration, a complete scoped envelope
with valid integrity resumes registration and execution with the same key/proof and
parameters. A duplicated browser tab resumes that same intention, not a new one.
Retries are bounded at 1, 2, 4, 8, 15 and 30 seconds, then wait for connectivity,
reopening or the existing generic connection retry action. No time-based unlock or
unbounded polling is introduced. Institution-generation guards are checked between
registration/execution and before cache publication.

UUID-only records, old envelopes without a proof, damaged payloads/integrity, or
divergent valid UUID candidates never register, execute or cancel automatically.
They use read-only GET inspection. `unknown` and `pending` are non-terminal. Each
committed Box remains visible/openable even if another candidate fails inspection;
creating another stays disabled until all candidates are terminal. Two independently
committed candidates are both displayed. Lost identities/storage never silently
become a new Box request.

## Compatibility and migration

Migration `0017_automatic_box_intent_lifecycle` adds nullable `resume_token_hash`
(unique) and `failure`, and replaces the coherent-state constraint. Existing legacy
rows retain null new fields and remain valid, with no identifier or history rewrite.
Protected pending rows require an intent/proof and no failure; committed rows have
an intent/proof and no failure; rejected/cancelled rows have no Box and retain intent.
Legacy committed/cancelled coherence is unchanged.

Existing direct POST creation and UUID-only POST resolution remain compatible for
legacy unprotected receipts. They cannot execute or cancel protected pending or
rejected/cancelled receipts (409). UUID-only resolution can read a protected committed
Box without mutation. GET inspection is unchanged except it now reports `pending`;
rejected/cancelled states are terminal `cancelled` for UUID-only inspection and no
immutable parameters/proof are disclosed.

Deploy the schema migration with the backend before serving the new frontend.
There is deliberately no unsafe fallback to direct creation if the new endpoints
are unavailable. Do not blindly reverse the migration once protected pending or
terminal rows exist: the old constraint cannot represent them. No existing QA or
production database was migrated during implementation.

## Irrecoverable information loss

Complete new envelopes recover ordinary absent receipts. Lost/corrupted proof or
payload, conflicting stored identities, and pre-protocol legacy unknown requests
remain genuinely ambiguous: UUID-only inspection cannot reconstruct the intention
or authorize execution/cancellation. The UI fails safely, retains evidence and
shows minimal unavailability instead of claiming resolution or issuing a new UUID.
Permission revocation also remains authoritative; recovery cannot bypass it.

## Verification gates

Focused service/API tests in `test_automatic_box_intents.py` exercise actual database
receipts, missing registration, lost responses, normalization, conflicting bindings,
independent requests, wrong proofs, permission changes and phase-by-phase rollback.
`test_automatic_box_intent_concurrency.py` uses independent connections and observed
PostgreSQL lock waits for registration/execution/cancellation races, allocator safety,
permission revocation, rollback and delayed independent B during recovery of A.
SQLite skips these races and is not evidence that PostgreSQL locking passed.

Frontend lifecycle tests run the real App callback and panel through controlled
transport, including reload in either phase, duplicated tabs, lost responses,
durable refusal, integrity damage and institution replacement between phases.
PostgreSQL concurrency validation on a verified disposable instance and desktop/
laboratory-tablet browser QA remain integration gates when unavailable locally.
