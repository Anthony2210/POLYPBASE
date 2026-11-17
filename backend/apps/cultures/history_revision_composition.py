"""Pure, inactive composition of durable historical decisions.

No ORM, publication policy or authorization lives here. Each revision is replayed
against the preceding effective values, never concatenated into a lifetime draft.
The original source and its per-event fingerprints remain separate. A restored
value is still an attributed, provisional assertion, not newly established proof.
"""

from dataclasses import dataclass, replace
from hashlib import sha256
from uuid import UUID

from .effective_history import (
    AnchorStatus, EffectiveStatus, EventKind, HistoryEvent, IdentityStability, InsertEvent,
    ReconstructionAnchor, SourceHistory, SourceRef, TemporalPosition, HistoryProjection, _mark_provisional,
)
from .effective_temperature import ThermalPreview, project_temperature
from .history_preview import _check_attribution_budget, _check_event_budget, _check_work_budget, _project, _signature
from .history_preview_validation import PreviewInputError, validate_draft, validate_scope_ids


@dataclass(frozen=True)
class ReviewedEvent:
    event_key: str
    kind: EventKind
    temporal: TemporalPosition
    origin_zone_id: int | None
    destination_zone_id: int | None
    source_fingerprint: str | None
    sources: tuple[SourceRef, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "sources", tuple(self.sources))


@dataclass(frozen=True)
class HistoricalRevision:
    revision_id: str
    organization_id: int
    box_id: int
    sequence: int
    predecessor_id: str | None
    operations: tuple
    reviewed_events: tuple = ()
    justifications: tuple = ()

    def __post_init__(self):
        for name in ("operations", "reviewed_events", "justifications"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True)
class DecisionReference:
    revision_id: str
    operation_index: int
    event_key: str


@dataclass(frozen=True)
class RevisionComposition:
    source: SourceHistory
    source_history: HistoryProjection
    published_history: HistoryProjection
    candidate_history: HistoryProjection | None
    revisions: tuple
    candidate_revision: HistoricalRevision | None
    decisions: tuple[DecisionReference, ...]
    published_thermal: ThermalPreview | None = None
    candidate_thermal: ThermalPreview | None = None
    mode: str = "composition_only"
    publication_evaluated: bool = False


def _uuid(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise PreviewInputError("invalid_durable_identity") from None


def source_event_fingerprint(event):
    """Review-time source content identity, not an authorization/freshness token."""
    if type(event) is not HistoryEvent:
        raise PreviewInputError("invalid_source_event")
    if event.identity.stability in (IdentityStability.DERIVED, IdentityStability.ANCHORED):
        # Derived keys are projection-local locators, not source content. A
        # durable anchor remains compatible when only that temporary key changes.
        identity = replace(event.identity, key="derived-evidence", derived_key=None,
                           stability=IdentityStability.DERIVED)
        event = replace(event, identity=identity)
    return sha256(_signature(event).encode("utf-8")).hexdigest()


def review_event(event):
    """Capture current effective values separately from untouched source values."""
    return ReviewedEvent(
        event.key, event.kind, event.temporal, event.origin_zone_id,
        event.destination_zone_id,
        source_event_fingerprint(event.original) if event.original is not None else None,
        event.sources,
    )


def _events(history):
    return {item.key: item for item in (*history.events, *history.unplaced_events)}


def _fold(source, history):
    # These are private replay surrogates, not new recorded scientific evidence.
    # Original evidence is restored on the returned EffectiveEvents below.
    events = tuple(HistoryEvent(
        item.identity, item.kind, item.origin, item.temporal,
        item.origin_zone_id, item.destination_zone_id,
        sources=item.sources, provenance=item.provenance, established=item.established,
    ) for item in (*history.events, *history.unplaced_events))
    return replace(source, events=events)


def _step(source, previous, revision):
    operations, _, _ = validate_draft(
        revision.operations, justifications=revision.justifications,
        organization_id=source.organization_id, box_id=source.box_id,
    )
    current = _events(previous)
    reviewed = {}
    for item in revision.reviewed_events:
        if type(item) is not ReviewedEvent or item.event_key in reviewed:
            raise PreviewInputError("invalid_reviewed_events")
        reviewed[item.event_key] = item
    targets = {item.target.key for item in operations if not isinstance(item, InsertEvent)}
    if set(reviewed) != targets:
        raise PreviewInputError("review_target_mismatch")
    for key, expected in reviewed.items():
        actual = current.get(key)
        if actual is None:
            raise PreviewInputError("revision_target_missing")
        if _signature(review_event(actual)) != _signature(expected):
            raise PreviewInputError("revision_review_incompatible")
    for operation in operations:
        if isinstance(operation, InsertEvent):
            # Only a backend-assigned UUID belongs in a stored insertion. A
            # future publisher must bind browser IDs before constructing this.
            _uuid(operation.draft_id)
    _check_event_budget(len(current) + sum(isinstance(item, InsertEvent) for item in operations))
    projected = _project(_fold(source, previous), operations=operations)
    if projected.rejected_operations:
        raise PreviewInputError("revision_operations_rejected", rejections=projected.rejected_operations)
    restored = {}
    for item in (*projected.events, *projected.unplaced_events):
        prior = current.get(item.key)
        if prior is not None:
            item = replace(
                item, original=prior.original,
                status=prior.status if item.status == EffectiveStatus.ORIGINAL else (
                    EffectiveStatus.INSERTED if prior.status == EffectiveStatus.INSERTED else item.status
                ),
            )
        restored[item.key] = item
    placed = tuple(restored[item.key] for item in projected.events)
    unplaced = tuple(restored[item.key] for item in projected.unplaced_events)
    # Reuse the reviewed propagation rule after restoring historical statuses;
    # otherwise an old assertion would lose its provisional dependency marker.
    intervals, sides = _mark_provisional(placed, unplaced, projected.intervals, projected.sides, projected.conflicts)
    return replace(projected, events=placed, unplaced_events=unplaced,
                   intervals=tuple(intervals), sides=tuple(sides),
                   anchor_resolutions=previous.anchor_resolutions)


def compose_revisions(source, revisions=(), *, candidate=None, anchors=(), thermal_source=None):
    """Replay a complete ordered chain and an optional, separate next candidate.

    A missing/incompatible anchor or review rejects composition explicitly. No
    partial effective chain is returned. Semantic conflicts remain diagnostics;
    this function never decides whether they would permit publication.

    Per-request limits apply independently to each revision, not their lifetime
    sum. Event/comparison/thermal work budgets remain enforced without truncation.
    No new revisions, UUIDs, source rows, timestamps or measurements are created.
    """
    if type(source) is not SourceHistory:
        raise PreviewInputError("invalid_revision_source")
    validate_scope_ids(source.organization_id, source.box_id)
    revisions = tuple(revisions)
    anchors = tuple(anchors)
    seen_anchors = set()
    for anchor in anchors:
        if type(anchor) is not ReconstructionAnchor:
            raise PreviewInputError("invalid_anchor")
        _uuid(anchor.anchor_id)
        validate_draft(anchors=(anchor,), organization_id=source.organization_id, box_id=source.box_id)
        if anchor.anchor_id in seen_anchors:
            raise PreviewInputError("duplicate_anchor_id")
        seen_anchors.add(anchor.anchor_id)
    _check_event_budget(len(source.events))
    if thermal_source is not None:
        if ((thermal_source.organization_id, thermal_source.box_id) != (source.organization_id, source.box_id)
                or thermal_source.zone_ids != source.zone_ids):
            raise PreviewInputError("source_scope_mismatch")
        _check_work_budget(source, thermal_source)
    original = _project(source)
    published = _project(source, anchors=anchors) if anchors else original
    if any(item.status != AnchorStatus.MATCHED for item in published.anchor_resolutions):
        raise PreviewInputError("source_anchor_incompatible")
    predecessor, seen, decisions = None, set(), []
    proposed = None
    chain = (*revisions, candidate) if candidate is not None else revisions
    for sequence, revision in enumerate(chain, 1):
        if type(revision) is not HistoricalRevision:
            raise PreviewInputError("invalid_revision")
        _uuid(revision.revision_id)
        if revision.predecessor_id is not None:
            _uuid(revision.predecessor_id)
        if ((revision.organization_id, revision.box_id) != (source.organization_id, source.box_id)):
            raise PreviewInputError("revision_scope_mismatch")
        if (type(revision.sequence) is not int or revision.sequence != sequence
                or revision.predecessor_id != predecessor or revision.revision_id in seen):
            raise PreviewInputError("invalid_revision_chain")
        next_history = _step(source, published, revision)
        for index, operation in enumerate(revision.operations):
            key = f"draft:{operation.draft_id}" if isinstance(operation, InsertEvent) else operation.target.key
            decisions.append(DecisionReference(revision.revision_id, index, key))
        if candidate is not None and sequence == len(chain):
            proposed = next_history
        else:
            published = next_history
        seen.add(revision.revision_id)
        predecessor = revision.revision_id
    published_thermal = candidate_thermal = None
    if thermal_source is not None:
        _check_attribution_budget(published, thermal_source)
        published_thermal = project_temperature(published, thermal_source)
        if proposed is not None:
            _check_attribution_budget(proposed, thermal_source)
            candidate_thermal = project_temperature(proposed, thermal_source)
    return RevisionComposition(source, original, published, proposed, revisions, candidate,
                               tuple(decisions), published_thermal, candidate_thermal)
