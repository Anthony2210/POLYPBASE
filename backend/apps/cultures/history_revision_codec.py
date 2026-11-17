"""Pure bridge between typed historical decisions and version-1 storage JSON.

Callers fetch and scope model fields explicitly. This module does not authorize,
query, publish, assign identities, or copy raw evidence. UUID syntax cannot prove
that an insertion identity was assigned by the backend; callers must ensure it.
Revision reason, author, metadata and storage fingerprints are separate concerns.
"""

import json
import re
from dataclasses import fields
from datetime import datetime
from typing import Any, NoReturn
from uuid import UUID

from .effective_history import (
    UNSET, AdjustEventKind, AdjustEventTemporal, AdjustEventZones, EventKind,
    EventTarget, InsertEvent, ModifyEvent, SourceRef, TemporalPosition,
    TemporalPrecision,
)
from .history_preview_validation import (
    OperationJustification, PreviewInputError, _temporal, validate_draft,
    validate_scope_ids,
)
from .history_revision_composition import HistoricalRevision, ReviewedEvent


_OPERATION_TYPES = {
    "modify_event": ModifyEvent,
    "adjust_event_kind": AdjustEventKind,
    "adjust_event_temporal": AdjustEventTemporal,
    "adjust_event_zones": AdjustEventZones,
    "insert_event": InsertEvent,
}
_REQUIRED = {
    "modify_event": {"target"},
    "adjust_event_kind": {"target", "kind"},
    "adjust_event_temporal": {"target", "temporal"},
    "adjust_event_zones": {"target"},
    "insert_event": {"draft_id", "kind", "temporal"},
}
_DATETIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TIMES = {"instant", "not_before", "before", "ordering_anchor"}


def _reject() -> NoReturn:
    raise PreviewInputError("invalid_stored_revision")


def _shape(value, allowed, required=()):
    if type(value) is not dict or set(value) - set(allowed) or not set(required) <= set(value):
        _reject()


def _text(value, limit=200):
    if type(value) is not str or not value.strip() or len(value) > limit:
        _reject()
    return value


def _uuid(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            _reject()
    except (ValueError, TypeError, AttributeError):
        raise PreviewInputError("invalid_durable_identity") from None


def _key(value):
    _text(value)
    if value.startswith(("anchor:", "draft:")):
        _uuid(value.split(":", 1)[1])
    return value


def _digest(value):
    if value is not None and (type(value) is not str or not _DIGEST.fullmatch(value)):
        _reject()
    return value


def _zone(value):
    if value is not None and (type(value) is not int or value < 1):
        _reject()
    return value


def _json(value, container):
    # Same conservative per-field bounds as durable storage, without model access.
    if type(value) is not container:
        _reject()

    def walk(item, depth=0):
        if depth > 6:
            _reject()
        if type(item) is dict:
            if len(item) > 64 or any(type(key) is not str or len(key) > 80 for key in item):
                _reject()
            for child in item.values():
                walk(child, depth + 1)
        elif type(item) is list:
            if len(item) > 256:
                _reject()
            for child in item:
                walk(child, depth + 1)
        elif item is not None and type(item) not in (str, int, float, bool):
            _reject()
        elif type(item) is str and len(item) > 4000:
            _reject()

    walk(value)
    try:
        if len(json.dumps(value, allow_nan=False).encode("utf-8")) > 262144:
            _reject()
    except (ValueError, TypeError, OverflowError):
        raise PreviewInputError("invalid_stored_revision") from None


def _decode_temporal(value):
    _shape(value, {field.name for field in fields(TemporalPosition)}, {"precision"})
    kwargs = dict(value)
    try:
        if type(kwargs["precision"]) is not str:
            _reject()
        kwargs["precision"] = TemporalPrecision(kwargs["precision"])
        for name in _TIMES & kwargs.keys():
            raw = kwargs[name]
            if raw is not None:
                if type(raw) is not str or not _DATETIME.fullmatch(raw):
                    _reject()
                kwargs[name] = datetime.fromisoformat(raw)
        temporal = TemporalPosition(**kwargs)
    except (ValueError, TypeError, OverflowError):
        raise PreviewInputError("invalid_temporal") from None
    _temporal(temporal, None)
    return temporal


def _encode_temporal(value):
    _temporal(value, None)
    return {
        field.name: (item.isoformat() if field.name in _TIMES else item)
        for field in fields(TemporalPosition)
        if (item := getattr(value, field.name)) is not None
    } | {"precision": value.precision.value}


def _decode_operation(value):
    if type(value) is not dict or type(value.get("type")) is not str:
        _reject()
    tag = value["type"]
    cls = _OPERATION_TYPES.get(tag)
    if cls is None:
        _reject()
    _shape(value, {field.name for field in fields(cls)} | {"type"}, _REQUIRED[tag] | {"type"})
    kwargs = {name: item for name, item in value.items() if name != "type"}
    if cls is InsertEvent:
        _uuid(kwargs["draft_id"])
    else:
        target = kwargs["target"]
        _shape(target, {"key", "expected_evidence_fingerprint"}, {"key"})
        kwargs["target"] = EventTarget(_key(target["key"]), _digest(target.get("expected_evidence_fingerprint")))
    if "kind" in kwargs:
        try:
            if type(kwargs["kind"]) is not str:
                _reject()
            kwargs["kind"] = EventKind(kwargs["kind"])
        except ValueError:
            raise PreviewInputError("invalid_kind") from None
    if "temporal" in kwargs:
        kwargs["temporal"] = _decode_temporal(kwargs["temporal"])
    return cls(**kwargs)


def _decode_review(value):
    _shape(value, {field.name for field in fields(ReviewedEvent)}, {"event_key", "kind", "temporal"})
    sources = value.get("sources", [])
    if type(sources) is not list or len(sources) > 64:
        _reject()
    refs = []
    for ref in sources:
        _shape(ref, {field.name for field in fields(SourceRef)}, {"source", "source_id", "role"})
        for name in ("source", "role", "basis"):
            if name in ref:
                _text(ref[name])
        if type(ref["source_id"]) is int:
            _zone(ref["source_id"])
        else:
            _text(ref["source_id"])
        if "exclusive" in ref and type(ref["exclusive"]) is not bool:
            _reject()
        refs.append(SourceRef(**ref))
    try:
        if type(value["kind"]) is not str:
            _reject()
        kind = EventKind(value["kind"])
    except ValueError:
        raise PreviewInputError("invalid_kind") from None
    return ReviewedEvent(
        _key(value["event_key"]), kind, _decode_temporal(value["temporal"]),
        _zone(value.get("origin_zone_id")), _zone(value.get("destination_zone_id")),
        _digest(value.get("source_fingerprint")), tuple(refs),
    )


def decode_revision(*, revision_id, organization_id, box_id, sequence, predecessor_id,
                    operations, review_values, justifications) -> HistoricalRevision:
    """Decode explicitly supplied storage fields, never a model instance.

    Accept canonical UUID strings (callers stringify fetched UUID fields). Review
    compatibility, chain replay and evidence resolution belong to composition.
    """
    _uuid(revision_id)
    if predecessor_id is not None:
        _uuid(predecessor_id)
    validate_scope_ids(organization_id, box_id)
    if (type(sequence) is not int or sequence < 1
            or (sequence == 1) != (predecessor_id is None) or revision_id == predecessor_id):
        _reject()
    _json(operations, list)
    _json(review_values, dict)
    _json(justifications, list)
    _shape(review_values, {"events"}, {"events"})
    events = review_values["events"]
    if type(events) is not list or len(events) > 100:
        _reject()
    decoded_operations = tuple(_decode_operation(item) for item in operations)
    reasons = []
    for item in justifications:
        _shape(item, {"operation_index", "reason"}, {"operation_index", "reason"})
        reasons.append(OperationJustification(**item))
    validate_draft(decoded_operations, justifications=tuple(reasons),
                   organization_id=organization_id, box_id=box_id)
    reviewed = tuple(_decode_review(item) for item in events)
    targets = {item.target.key for item in decoded_operations if not isinstance(item, InsertEvent)}
    if len({item.event_key for item in reviewed}) != len(reviewed):
        raise PreviewInputError("invalid_reviewed_events")
    if {item.event_key for item in reviewed} != targets:
        raise PreviewInputError("review_target_mismatch")
    return HistoricalRevision(revision_id, organization_id, box_id, sequence,
                              predecessor_id, decoded_operations, reviewed, tuple(reasons))


def encode_revision(revision: HistoricalRevision) -> dict:
    """Return only operations, review_values and justifications as fresh JSON.

    UNSET is omitted; explicit null is retained. No browser-to-durable identity
    binding happens here. Minimal proof references are not raw source snapshots.
    """
    if type(revision) is not HistoricalRevision:
        raise PreviewInputError("invalid_revision")
    validate_draft(revision.operations, justifications=revision.justifications,
                   organization_id=revision.organization_id, box_id=revision.box_id)
    operations = []
    for operation in revision.operations:
        encoded: dict[str, Any] = {"type": next(tag for tag, cls in _OPERATION_TYPES.items() if type(operation) is cls)}
        for field in fields(operation):
            value = getattr(operation, field.name)
            if value is UNSET:
                continue
            if field.name == "target":
                value = {"key": value.key, "expected_evidence_fingerprint": value.expected_evidence_fingerprint}
            elif field.name == "kind":
                value = value.value
            elif field.name == "temporal":
                value = _encode_temporal(value)
            encoded[field.name] = value
        operations.append(encoded)
    events = []
    for item in revision.reviewed_events:
        if type(item) is not ReviewedEvent or type(item.kind) is not EventKind:
            raise PreviewInputError("invalid_reviewed_events")
        if any(type(ref) is not SourceRef for ref in item.sources):
            raise PreviewInputError("invalid_reviewed_events")
        events.append({
            "event_key": item.event_key, "kind": item.kind.value,
            "temporal": _encode_temporal(item.temporal),
            "origin_zone_id": item.origin_zone_id, "destination_zone_id": item.destination_zone_id,
            "source_fingerprint": item.source_fingerprint,
            "sources": [{field.name: getattr(ref, field.name) for field in fields(SourceRef)} for ref in item.sources],
        })
    result = {
        "operations": operations, "review_values": {"events": events},
        "justifications": [{"operation_index": item.operation_index, "reason": item.reason}
                           for item in revision.justifications],
    }
    # One strict storage boundary in both directions, including reviewed values.
    decode_revision(revision_id=revision.revision_id, organization_id=revision.organization_id,
                    box_id=revision.box_id, sequence=revision.sequence,
                    predecessor_id=revision.predecessor_id, **result)
    return result
