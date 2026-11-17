"""Pure structural validation for in-memory history previews.

Limits bound technical work, not publication policy. The Phase 1 engine remains
responsible for target resolution, zone membership, allowed draft kinds and the
final business shape. This module neither parses payloads nor persists data.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NoReturn
from zoneinfo import ZoneInfo

from .effective_history import (
    UNSET,
    AdjustEventKind,
    AdjustEventTemporal,
    AdjustEventZones,
    EventKind,
    EventTarget,
    InsertEvent,
    ModifyEvent,
    ReconstructionAnchor,
    TemporalPosition,
    TemporalPrecision,
    anchor_key,
    draft_key,
)

MAX_OPERATIONS = 100
MAX_ANCHORS = 500
MAX_JUSTIFICATIONS = 100
MAX_KEY_LENGTH = 256
MAX_DRAFT_ID_LENGTH = MAX_KEY_LENGTH - len(draft_key(""))
MAX_ANCHOR_ID_LENGTH = MAX_KEY_LENGTH - len(anchor_key(""))
MAX_JUSTIFICATION_LENGTH = 4000
MAX_LOCATOR_ITEMS = 100
MAX_WINDOW_DAYS = 366

_PARIS = ZoneInfo("Europe/Paris")
_MACHINE_CODE = re.compile(r"[a-z][a-z0-9_]{0,127}\Z")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
_OPERATION_TYPES = (ModifyEvent, AdjustEventKind, AdjustEventTemporal, AdjustEventZones, InsertEvent)


class PreviewInputError(ValueError):
    """A safe machine code, optionally accompanied by engine rejections."""

    code: str
    operation_index: int | None
    rejections: tuple

    def __init__(self, code, *, operation_index=None, rejections=()):
        # Never interpolate caller data, reasons or engine details into a message.
        self.code = code if type(code) is str and _MACHINE_CODE.fullmatch(code) else "invalid_preview_input"
        self.operation_index = operation_index
        self.rejections = tuple(rejections)
        super().__init__(self.code)


@dataclass(frozen=True)
class OperationJustification:
    operation_index: int
    reason: str


def _positive_int(value):
    return type(value) is int and value > 0


def _bounded_text(value, limit=MAX_KEY_LENGTH):
    return type(value) is str and 0 < len(value) <= limit and bool(value.strip())


def _utc(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("invalid_datetime")
    # Conversion also refuses datetimes whose UTC representation would overflow.
    instant = value.astimezone(UTC)
    local = instant.astimezone(value.tzinfo)
    # A gap time must not silently become a different local time. Compare wall
    # time and offset, not fold: both real folds and fixed offsets remain valid.
    if local.replace(tzinfo=None) != value.replace(tzinfo=None) or local.utcoffset() != value.utcoffset():
        raise ValueError("invalid_datetime")
    return instant


def validate_window(start, end):
    """Return a nonempty UTC window of at most 366 elapsed days."""
    try:
        start, end = _utc(start), _utc(end)
    except (TypeError, ValueError, OverflowError):
        raise PreviewInputError("invalid_window") from None
    if start >= end:
        raise PreviewInputError("invalid_window")
    if end - start > timedelta(days=MAX_WINDOW_DAYS):
        raise PreviewInputError("window_too_large")
    return start, end


def validate_scope_ids(organization_id, box_id):
    """Validate typed identifiers, without asserting existence or permission."""
    if not _positive_int(organization_id) or not _positive_int(box_id):
        raise PreviewInputError("invalid_scope")


def _sequence(value, name, limit):
    if type(value) not in (list, tuple):
        raise PreviewInputError(f"invalid_{name}")
    if len(value) > limit:
        raise PreviewInputError(f"too_many_{name}")
    return tuple(value)


def _fingerprint(value, *, operation_index=None):
    # Production evidence identities are SHA-256 hexdigests from Phase 1.
    if type(value) is not str or not _FINGERPRINT.fullmatch(value):
        raise PreviewInputError("invalid_fingerprint", operation_index=operation_index)


def _target(value, index):
    if type(value) is not EventTarget or not _bounded_text(value.key):
        raise PreviewInputError("invalid_target", operation_index=index)
    if value.expected_evidence_fingerprint is not None:
        _fingerprint(value.expected_evidence_fingerprint, operation_index=index)


def _temporal(value, index):
    def reject() -> NoReturn:
        raise PreviewInputError("invalid_temporal", operation_index=index)

    if type(value) is not TemporalPosition or type(value.precision) is not TemporalPrecision:
        reject()
    try:
        times = {
            name: _utc(getattr(value, name)) if getattr(value, name) is not None else None
            for name in ("instant", "not_before", "before", "ordering_anchor")
        }
    except (TypeError, ValueError, OverflowError):
        reject()
    pointlike = value.precision in (TemporalPrecision.EXACT, TemporalPrecision.RECORDED_BOUNDARY)
    if pointlike != (times["instant"] is not None):
        reject()
    if pointlike and any(times[name] is not None for name in ("not_before", "before", "ordering_anchor")):
        reject()
    lower, upper = times["not_before"], times["before"]
    if lower is not None and upper is not None and lower >= upper:
        reject()
    if (value.precision in (TemporalPrecision.ISO_WEEK, TemporalPrecision.OBSERVATION_INTERVAL)
            and (lower is None or upper is None)):
        reject()
    if value.precision is TemporalPrecision.ISO_WEEK:
        if type(value.iso_year) is not int or type(value.iso_week) is not int:
            reject()
        try:
            expected = TemporalPosition.week(value.iso_year, value.iso_week, _PARIS)
            expected_bounds = _utc(expected.not_before), _utc(expected.before)
        except (TypeError, ValueError, OverflowError):
            reject()
        if (lower, upper) != expected_bounds:
            reject()
    elif value.iso_year is not None or value.iso_week is not None:
        reject()
    # Interval ordering anchors are sequence conventions, not occurrence dates;
    # source reconstructions may legitimately place them outside the bounds.


def _zone(value, index, *, allow_unset):
    if value is None or (allow_unset and value is UNSET) or _positive_int(value):
        return
    raise PreviewInputError("invalid_zone_id", operation_index=index)


def _operation(operation, index):
    operation_type = type(operation)
    if operation_type not in _OPERATION_TYPES:
        raise PreviewInputError("invalid_operation", operation_index=index)
    if operation_type is InsertEvent:
        if not _bounded_text(operation.draft_id, MAX_DRAFT_ID_LENGTH):
            raise PreviewInputError("invalid_draft_id", operation_index=index)
    else:
        _target(operation.target, index)
    if (operation_type in (ModifyEvent, AdjustEventKind, InsertEvent)
            and not (operation_type is ModifyEvent and operation.kind is UNSET)
            and type(operation.kind) is not EventKind):
        raise PreviewInputError("invalid_kind", operation_index=index)
    if (operation_type in (ModifyEvent, AdjustEventTemporal, InsertEvent)
            and not (operation_type is ModifyEvent and operation.temporal is UNSET)):
        _temporal(operation.temporal, index)
    if operation_type in (ModifyEvent, AdjustEventZones, InsertEvent):
        for value in (operation.origin_zone_id, operation.destination_zone_id):
            _zone(value, index, allow_unset=operation_type is not InsertEvent)


def _locator(value):
    if type(value) is not tuple or len(value) > MAX_LOCATOR_ITEMS:
        raise PreviewInputError("invalid_locator")
    keys = set()
    for item in value:
        if type(item) is not tuple or len(item) != 2:
            raise PreviewInputError("invalid_locator")
        key, reference = item
        if not _bounded_text(key) or key in keys:
            raise PreviewInputError("invalid_locator")
        if not (_positive_int(reference) or _bounded_text(reference)):
            raise PreviewInputError("invalid_locator")
        keys.add(key)


def validate_draft(operations=(), anchors=(), justifications=(), *, organization_id, box_id):
    """Validate typed draft inputs and return immutable outer sequences.

    Objects and reason text are preserved, including explicit None versus UNSET.
    Insertion reasons express provenance only; they are not an approval workflow.
    """
    validate_scope_ids(organization_id, box_id)
    operations = _sequence(operations, "operations", MAX_OPERATIONS)
    anchors = _sequence(anchors, "anchors", MAX_ANCHORS)
    justifications = _sequence(justifications, "justifications", MAX_JUSTIFICATIONS)

    for index, operation in enumerate(operations):
        _operation(operation, index)

    anchor_ids = set()
    for anchor in anchors:
        if type(anchor) is not ReconstructionAnchor or not _bounded_text(anchor.anchor_id, MAX_ANCHOR_ID_LENGTH):
            raise PreviewInputError("invalid_anchor")
        if not _positive_int(anchor.organization_id) or not _positive_int(anchor.box_id):
            raise PreviewInputError("invalid_anchor")
        if (anchor.organization_id, anchor.box_id) != (organization_id, box_id):
            raise PreviewInputError("anchor_scope_mismatch")
        _fingerprint(anchor.evidence_fingerprint)
        _locator(anchor.locator)
        if anchor.anchor_id in anchor_ids:
            raise PreviewInputError("duplicate_anchor_id")
        anchor_ids.add(anchor.anchor_id)

    justified = set()
    for justification in justifications:
        if type(justification) is not OperationJustification:
            raise PreviewInputError("invalid_justification")
        index = justification.operation_index
        if type(index) is not int or not 0 <= index < len(operations):
            raise PreviewInputError("invalid_justification")
        if not _bounded_text(justification.reason, MAX_JUSTIFICATION_LENGTH):
            raise PreviewInputError("invalid_justification", operation_index=index)
        if index in justified:
            raise PreviewInputError("duplicate_justification", operation_index=index)
        justified.add(index)
    for index, operation in enumerate(operations):
        if type(operation) is InsertEvent and index not in justified:
            raise PreviewInputError("missing_insertion_justification", operation_index=index)

    return operations, anchors, justifications
