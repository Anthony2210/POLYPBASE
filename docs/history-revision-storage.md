# Inactive historical revision storage

## Scope

This foundation adds storage and pure composition only. It adds no publisher,
HTTP route, admin writer, consumer integration, conflict policy, override,
operational reconciliation or automatic audit write. Existing Phase 1–4 engines,
preview routes, inline movement correction and operational occupancy are unchanged.
A stored row is not permission to publish or proof of scientific establishment.

Implementation:

- `backend/apps/cultures/history_revision_models.py` (registered by `models.py`);
- `history_revision_composition.py` (pure typed composition);
- `history_revision_codec.py` (pure version-1 JSON bridge);
- migrations `0019_history_revision_storage` and `0020_history_revision_guards`.

## Storage objects

### BoxHistoryStream

One stream per institution/original Box identity, with a UUID and nullable current
Box reference. Creation requires a live, correctly scoped Box. Institution and
original numeric Box identity cannot change. The current Box reference may only
detach to null, preserving the existing ability to delete a Box without cascading
away correction history. The original identity remains reserved after detachment.
The institution is protected while retained history exists.

The nullable head references a revision of the same stream. Head advancement is
explicit and one step at a time: no skipping, clearing or rollback. Preinserted
successors do not strand the head; they remain reachable sequentially. Creating a
revision does not advance the head or change any operational model.

A future scoped reader must fetch the complete prefix selected by the head, not
implicitly treat every stored successor as published. A future publisher must
lock/recheck the head and append/advance atomically.

### BoxHistoryRevision

Immutable UUID record with:

- stream, positive sequence and same-stream adjacent predecessor;
- fresh stored active author reference and server-captured identifier/name;
- server creation timestamp and nonblank reason;
- ordered typed-operation JSON and indexed justifications;
- reviewed effective event values, original-source content fingerprints and
  minimal evidence references;
- schema version and bounded provenance/version metadata;
- source/request fingerprints and idempotency UUID.

The model requires a real active stored author, but is not a request authentication
or Admin authorization boundary. SQL cannot attest who submitted a row. A future
publisher must enforce fresh active institutional Admin membership and never
trust client author, scope, fingerprints or identity allocation.

Sequence and idempotency key are unique within a stream. A predecessor can have
only one successor. No existing historical rows are backfilled or rewritten.

To respect user deletion, the author FK uses SET_NULL. Its transition from a
non-null value to null is the sole allowed attribution-reference update; the
numeric author identifier, captured label and all revision content stay immutable.
The SQL exception allows such detachment without proving a user deletion caused
it. It cannot replace the immutable author identity or attach another account.

### BoxHistoryEventIdentity

Server UUID registry, immutable after insertion:

- `persisted`: original typed source key, unique within its stream;
- `derived`: original locator and evidence fingerprint, plus server-computed
  `derived_source_key`, unique within its stream;
- `inserted`: a server identity independent of a browser draft label.

The derived key is SHA-256 over compact sorted-key ASCII-escaped JSON containing
`locator` and `source_fingerprint`. The original locator/fingerprint are retained.
Different locators may share an evidence fingerprint. Reordered object keys do
not create duplicate registrations. SQL validates the digest's shape and unique
value, not its correspondence to locator/content; validated model saves compute
that correspondence. Any future permitted raw writer must use the same encoding.

An optional minimal source snapshot supports review-time provenance. The registry
is not a source resolver or automatic rematching mechanism. The future publisher
must resolve operation targets against this scoped registry and the actual source.
JSON references intentionally have no polymorphic FK; dangling/mismatched targets
are rejected by pure composition, not certified by storage constraints.

## Composition contract

`compose_revisions(source, revisions, candidate=..., anchors=...,
thermal_source=...)` accepts trusted typed, scoped inputs and performs no ORM work.
The codec receives explicit scalar/JSON fields, never model instances or querysets.
It preserves omitted UNSET fields versus explicit null and creates fresh JSON.

The result separates:

1. the unchanged original source and its projected history;
2. effective history after the complete ordered stored revision prefix;
3. an optional next candidate's effective history;
4. the complete revision/decision references;
5. optional thermal attribution over the unchanged thermal source snapshot.

R1 modifies source interpretation. R2 is checked against R1's effective values.
R3 can restore earlier values, but R1/R2 remain in the chain and the restored event
remains a provisional corrected assertion. No historic decision is erased.

Each revision is checked independently with the Phase 3 request budgets. The
lifetime sum of old operations is not a Phase 3 request and is never truncated to
100. Existing event-comparison, attribution and overlap work budgets remain
explicit. Composition reuses the reviewed projection and provisional-propagation
functions; it does not rebuild conflict, lifecycle or temperature interpretation.

Every modified existing event requires its reviewed effective values and original
source content fingerprint. Incompatible reviews reject the entire composition.
Derived anchors must resolve exactly and unambiguously. A changed temporary
derived key alone is excluded from the source content fingerprint, but changing
actual content, locator, evidence fingerprint or provenance remains detectable.

Insertions require canonical backend UUIDs and indexed justifications. The pure
layer checks UUID syntax; it cannot prove the caller allocated that UUID. A future
publisher must bind browser draft IDs to fresh scoped registry identities before
encoding. Engine keys `anchor:<UUID>` and `draft:<UUID>` are backend replay keys,
not authorization capabilities or a claim that an inserted event is established.

Original evidence references and original HistoryEvent objects survive successive
replay. Effective events' `applied_operations` remain local to replay steps; the
full cross-revision attribution is `RevisionComposition.decisions`, pairing a
revision UUID, local operation index and event key.

Semantic conflicts are returned, not accepted or rejected as publication policy.
The result is explicitly `mode="composition_only"` and
`publication_evaluated=False`. No automatic source rebase, anchor rematch or
operational current-state synchronization occurs.

## Scientific and payload boundaries

Exact and uncertain temporal representations retain their precision/bounds and
explicit offsets; no occurrence date is fabricated. Shared Phase 3 temporal
validation also checks canonical Paris ISO-week boundaries and invalid DST times.

Thermal evidence stays external to revision storage. Composition reattributes the
same evidence items without inventing temperatures or replacing observations.
Zero, negative and null values and measured/setpoint source categories stay distinct.
No raw probe datasets, source files or audit payloads are copied into revisions.

Version-1 storage JSON uses typed operation/review/reference shapes. Metadata is an
explicit small allowlist. Credential/raw-payload keys, unsupported structures,
nonfinite JSON numbers and excessive payloads are rejected by model validation.
Limits are technical, not publication rules: 100 operations/reviews/justifications
per revision, existing Phase 3 reason limits, and 256 KiB per JSON field. An
oversized record is rejected, never shortened. These storage byte bounds can
reject combinations whose individual fields fit the preview budgets; they do not
create a lifetime revision-count limit. Free-text reasons still require normal
secret-handling discipline; no text validator can reliably detect every secret.

## Integrity layers

| Layer | Enforced here |
| --- | --- |
| DB constraints | Stream/source identity uniqueness, revision sequence/idempotency uniqueness, single successor, root/sequence and identity shapes, FKs |
| SQLite/PostgreSQL trigger definitions | Stream scope at creation, immutable scope/reference detachment, one-step same-stream head changes, predecessor adjacency, active stored author/identifier at insertion, revision/identity UPDATE and DELETE guards, selected JSON containers/counts/digest formats |
| Model validation | Fresh captured author, complete typed JSON syntax, canonical derived key computation, bounded locators/snapshots, indexed reasons, shared temporal validation |
| Pure codec/composition | Typed round trips, complete chain, source-review compatibility, anchor resolution, operation validity, scientific projection and provisional status |
| Future publisher — not implemented | Request authorization, full dependency freshness/fences, row locking, registry resolution, idempotent transaction semantics, semantic acceptance policy, mandatory audit and atomic head advancement |

Raw SQL can bypass application JSON meaning checks and provenance computation.
Schema owners can disable/drop triggers. PostgreSQL TRUNCATE is deliberately not
blocked by triggers, to avoid breaking existing Django test/maintenance flush
workflows; production database privileges must separately prevent unauthorized
TRUNCATE. No general tamper-proof or production concurrency guarantee is claimed.
The new guards do not mutate existing tables or enroll their writers in a new
freshness protocol. Existing sources can still change via their existing workflows;
a future publisher must detect/recheck those changes.

## Validation and remaining work

On explicitly isolated in-memory SQLite:

- 94 new tests: 33 storage, 47 pure composition and 14 codec/integration tests;
- 382 tests including all new tests and Phase 1–4 regressions;
- 466 further regressions: histories, historical transitions/movements, Box/zone
  temperatures, inline corrections, cultures/admin APIs, accounts and language;
- Django system check and migration-drift check passed;
- guard removal/reinstallation and SQLite SQL-bypass rejection are covered;
- PostgreSQL SQL generation is covered without opening a connection.

PostgreSQL trigger execution, FK/deletion behavior under concurrency, isolation,
locking and migration rehearsal remain outstanding. No disposable PostgreSQL
was explicitly authorized or accessed. No production, Neon or shared DB access.

Before an active publisher: approve the semantic conflict/current-occupancy/source
change policies; establish complete source and permission fences; define an atomic
append/head/audit protocol and retries; validate on authorized disposable
PostgreSQL; then expose a separately reviewed HTTP contract. Existing previews
remain read-only and do not read this overlay. Consumer/journal integration and
future FR/EN interface messages are later work.
