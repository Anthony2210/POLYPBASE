"""Strict HTTP boundary for the read-only Phase 3 history preview.

After ``is_valid(raise_exception=True)``, request ``validated_data`` contains
aware ``start``/``end`` datetimes and lists of typed ``operations`` and
``justifications``. Pass these as keyword arguments to ``preview_box_history``
with actor, organization_id and box_id obtained by the view, never the payload.
The view/service must call Phase 3 ``validate_draft`` with that trusted scope;
this module does not authorize targets, zones or reconstruction provenance.
"""

import re
from collections.abc import Mapping
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from enum import Enum

from rest_framework import serializers

from . import effective_history as history
from . import effective_temperature as temperature
from . import history_preview as preview
from .history_preview_validation import (
    MAX_DRAFT_ID_LENGTH,
    MAX_JUSTIFICATION_LENGTH,
    MAX_JUSTIFICATIONS,
    MAX_KEY_LENGTH,
    MAX_OPERATIONS,
    OperationJustification,
    PreviewInputError,
    validate_window,
)


class _StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({"non_field_errors": ["invalid_object"]}, code="invalid_object")
        unknown = set(data) - set(self.fields)
        if unknown:
            # Do not echo arbitrary caller-controlled field names or values.
            raise serializers.ValidationError({"non_field_errors": ["unknown_fields"]}, code="unknown_fields")
        return super().to_internal_value(data)


class _StrictTextField(serializers.CharField):
    def __init__(self, **kwargs):
        super().__init__(trim_whitespace=False, **kwargs)

    def to_internal_value(self, data):
        if type(data) is not str:
            self.fail("invalid")
        value = super().to_internal_value(data)
        if not value.strip():
            self.fail("blank")
        return value


class _StrictIntegerField(serializers.IntegerField):
    def to_internal_value(self, data):
        if type(data) is not int:
            self.fail("invalid")
        return super().to_internal_value(data)


class _EnumField(serializers.ChoiceField):
    def __init__(self, enum_type, **kwargs):
        self.enum_type = enum_type
        super().__init__(choices=[item.value for item in enum_type], **kwargs)

    def to_internal_value(self, data):
        if type(data) is not str:
            self.fail("invalid_choice", input="invalid")
        if data not in self.choice_strings_to_values:
            self.fail("invalid_choice", input="invalid")
        return self.enum_type(data)


class _AwareISODateTimeField(serializers.Field):
    # Explicit offsets keep repeated DST wall times distinct. Do not let DRF
    # localize naive input or convert offsets to the active Django timezone.
    _pattern = re.compile(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}"
        r"(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])\Z"
    )
    default_error_messages = {"invalid": "invalid_aware_iso8601_datetime"}

    def to_internal_value(self, data):
        if type(data) is not str or not self._pattern.fullmatch(data):
            self.fail("invalid")
        try:
            value = datetime.fromisoformat(data)
            if value.utcoffset() is None:
                self.fail("invalid")
            value.astimezone(UTC)  # Refuse values whose UTC instant overflows.
        except (ValueError, OverflowError):
            self.fail("invalid")
        return value

    def to_representation(self, value):
        return value.isoformat()


class EventTargetSerializer(_StrictSerializer):
    key = _StrictTextField(max_length=MAX_KEY_LENGTH)
    expected_evidence_fingerprint = _StrictTextField(
        required=False, allow_null=True, min_length=64, max_length=64,
    )

    def validate_expected_evidence_fingerprint(self, value):
        if value is not None and not re.fullmatch(r"[0-9a-f]{64}", value):
            raise serializers.ValidationError("invalid_fingerprint", code="invalid_fingerprint")
        return value

    def to_internal_value(self, data):
        return history.EventTarget(**super().to_internal_value(data))


class TemporalPositionSerializer(_StrictSerializer):
    precision = _EnumField(history.TemporalPrecision)
    instant = _AwareISODateTimeField(required=False, allow_null=True)
    iso_year = _StrictIntegerField(required=False, allow_null=True)
    iso_week = _StrictIntegerField(required=False, allow_null=True)
    not_before = _AwareISODateTimeField(required=False, allow_null=True)
    before = _AwareISODateTimeField(required=False, allow_null=True)
    ordering_anchor = _AwareISODateTimeField(required=False, allow_null=True)

    def to_internal_value(self, data):
        values = super().to_internal_value(data)
        try:
            return history.TemporalPosition(**values)
        except (TypeError, ValueError, OverflowError):
            raise serializers.ValidationError("invalid_temporal", code="invalid_temporal") from None


class _TargetedOperationSerializer(_StrictSerializer):
    target = EventTargetSerializer()


class ModifyEventSerializer(_TargetedOperationSerializer):
    kind = _EnumField(history.EventKind, required=False)
    temporal = TemporalPositionSerializer(required=False)
    origin_zone_id = _StrictIntegerField(required=False, allow_null=True, min_value=1)
    destination_zone_id = _StrictIntegerField(required=False, allow_null=True, min_value=1)


class AdjustEventKindSerializer(_TargetedOperationSerializer):
    kind = _EnumField(history.EventKind)


class AdjustEventTemporalSerializer(_TargetedOperationSerializer):
    temporal = TemporalPositionSerializer()


class AdjustEventZonesSerializer(_TargetedOperationSerializer):
    origin_zone_id = _StrictIntegerField(required=False, allow_null=True, min_value=1)
    destination_zone_id = _StrictIntegerField(required=False, allow_null=True, min_value=1)


class InsertEventSerializer(_StrictSerializer):
    draft_id = _StrictTextField(max_length=MAX_DRAFT_ID_LENGTH)
    kind = _EnumField(history.EventKind)
    temporal = TemporalPositionSerializer()
    origin_zone_id = _StrictIntegerField(required=False, allow_null=True, min_value=1)
    destination_zone_id = _StrictIntegerField(required=False, allow_null=True, min_value=1)


_OPERATION_TYPES = {
    "modify_event": (ModifyEventSerializer, history.ModifyEvent),
    "adjust_event_kind": (AdjustEventKindSerializer, history.AdjustEventKind),
    "adjust_event_temporal": (AdjustEventTemporalSerializer, history.AdjustEventTemporal),
    "adjust_event_zones": (AdjustEventZonesSerializer, history.AdjustEventZones),
    "insert_event": (InsertEventSerializer, history.InsertEvent),
}


class HistoryPreviewOperationSerializer(serializers.Serializer):
    """Dispatch the required ``type`` discriminant to an exact operation shape."""

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError("invalid_operation", code="invalid_operation")
        kind = data.get("type")
        if type(kind) is not str or kind not in _OPERATION_TYPES:
            raise serializers.ValidationError({"type": "invalid_operation_type"})
        serializer_type, operation_type = _OPERATION_TYPES[kind]
        values = serializer_type(context=self.context).run_validation(
            {key: value for key, value in data.items() if key != "type"},
        )
        # Dataclass defaults preserve omitted zone changes as UNSET, whereas
        # explicit JSON null clears the zone. InsertEvent defaults are None.
        return operation_type(**values)


class OperationJustificationSerializer(_StrictSerializer):
    operation_index = _StrictIntegerField(min_value=0)
    reason = _StrictTextField(max_length=MAX_JUSTIFICATION_LENGTH)

    def to_internal_value(self, data):
        return OperationJustification(**super().to_internal_value(data))


class HistoryPreviewRequestSerializer(_StrictSerializer):
    """Parse typed service kwargs, without accepting client scope or anchors.

    Phase 3 validation in the view/service still checks temporal semantics,
    justification references/duplicates, required insertion reasons and scope.
    Window validation here checks elapsed UTC time but retains supplied offsets.
    """

    start = _AwareISODateTimeField()
    end = _AwareISODateTimeField()
    operations = HistoryPreviewOperationSerializer(many=True, required=False, default=list, max_length=MAX_OPERATIONS)
    justifications = OperationJustificationSerializer(
        many=True, required=False, default=list, max_length=MAX_JUSTIFICATIONS,
    )

    def validate(self, attrs):
        try:
            validate_window(attrs["start"], attrs["end"])
            # Loaders select local calendar days and their exclusive next
            # midnight. Reject unrepresentable boundaries, not scientific ages.
            paris = ZoneInfo("Europe/Paris")
            for value in (attrs["start"], attrs["end"]):
                local_day = value.astimezone(paris).date()
                for day in (local_day, local_day + timedelta(days=1)):
                    datetime.combine(day, time.min, tzinfo=paris).astimezone(UTC)
        except PreviewInputError as error:
            raise serializers.ValidationError(error.code, code=error.code) from None
        except (ValueError, OverflowError):
            raise serializers.ValidationError("invalid_window", code="invalid_window") from None
        return attrs


# Exact types AND explicit field names: adding a dataclass field cannot silently
# extend the HTTP contract. SourceHistory, ThermalSourceHistory and sessions are
# deliberately absent. Trusted anchor resolutions are output provenance only.
_RESULT_FIELDS = {
    preview.HistoryPreview: (
        "organization_id", "box_id", "before", "after", "event_changes", "interval_changes",
        "side_changes", "thermal_changes", "stay_changes", "gap_changes", "conflict_changes",
        "affected_event_keys", "justifications", "mode",
    ),
    preview.PreviewState: ("history", "thermal", "safety"),
    preview.PreviewSafety: (
        "has_conflicts", "has_impossible_conflicts", "has_provisional_occupancy", "has_temporal_uncertainty",
        "has_unassigned_thermal_evidence", "has_source_findings", "publication_evaluated",
    ),
    preview.EventChange: ("event_key", "before", "after", "changed_fields", "before_position", "after_position"),
    preview.IntervalChange: ("started_by", "before", "after"),
    preview.SideChange: ("event_key", "side", "before", "after"),
    preview.ThermalChange: ("source_key", "before", "after", "before_assigned", "after_assigned"),
    preview.StayChange: ("started_by", "before", "after"),
    preview.GapChange: ("before", "after"),
    preview.ConflictChange: ("evolution", "before", "after"),
    OperationJustification: ("operation_index", "reason"),
    history.HistoryProjection: (
        "organization_id", "box_id", "events", "unplaced_events", "intervals", "sides", "conflicts",
        "rejected_operations", "anchor_resolutions", "findings",
    ),
    history.EventIdentity: ("key", "stability", "evidence_fingerprint", "locator", "derived_key"),
    history.TemporalPosition: (
        "precision", "instant", "iso_year", "iso_week", "not_before", "before", "ordering_anchor",
    ),
    history.SourceRef: ("source", "source_id", "role", "basis", "exclusive"),
    history.HistoryEvent: (
        "identity", "kind", "origin", "temporal", "origin_zone_id", "destination_zone_id", "sources",
        "provenance", "established", "details",
    ),
    history.EffectiveEvent: (
        "identity", "kind", "origin", "temporal", "origin_zone_id", "destination_zone_id", "status",
        "established", "original", "applied_operations", "ordering_ambiguous_with",
    ),
    history.OccupancyInterval: (
        "state", "zone_id", "start", "started_by", "end", "ended_by", "boundary_uncertain", "provisional_reasons",
    ),
    history.LocationSide: (
        "zone_id", "side", "event_key", "kind", "temporal", "related_zone_id", "zone_basis", "provisional_reasons",
    ),
    history.Conflict: ("code", "certainty", "event_keys", "uncertainty_reasons", "details"),
    history.RejectedOperation: ("index", "code", "details"),
    history.SourceFinding: ("code", "details"),
    history.AnchorResolution: ("anchor", "status", "event_key"),
    history.ReconstructionAnchor: ("anchor_id", "organization_id", "box_id", "evidence_fingerprint", "locator"),
    temperature.ThermalPreview: (
        "history", "start", "end", "stays", "segments", "unassigned", "gaps", "findings", "continuity",
    ),
    temperature.ThermalEvidence: (
        "key", "organization_id", "zone_id", "source_kind", "temporal", "temperature_c", "minimum_c",
        "maximum_c", "temperature_sum_c", "measurement_count", "probe_id", "box_id", "last_measured_at",
        "last_temperature_c", "daily_computed_from_probes", "association_basis", "sources", "established",
        "uncertainty_reasons",
    ),
    temperature.ThermalAttribution: (
        "occupancy_index", "zone_id", "extent", "overlap_start", "overlap_end", "established",
        "provisional_reasons", "uncertainty_reasons",
    ),
    temperature.ThermalSegment: ("evidence", "attributions", "overlapping_source_keys", "uncertainty_reasons"),
    temperature.ThermalStay: (
        "occupancy_index", "occupancy", "possible_start", "possible_end", "definite_start", "definite_end",
        "source_keys", "missing_reasons",
    ),
    temperature.ThermalGap: (
        "start", "end", "reason", "occupancy_index", "provisional_reasons", "established", "uncertainty_reasons",
    ),
}


def _public_result(value):
    if type(value) is history.SourceRef and value.source == "temperature_measurement":
        result = {name: _public_result(getattr(value, name)) for name in _RESULT_FIELDS[history.SourceRef]}
        # The stored provider label is free text, not a reviewed provenance
        # category. References retain the evidence identity without publishing
        # arbitrary imported text or credentials embedded in a source label.
        withheld = value.basis not in ("", "iminilide")
        result["basis"] = None if withheld else value.basis
        result["basis_withheld"] = withheld
        return result
    if type(value) is history.SourceFinding:
        withheld = sorted(set(value.details) & {"source_values"})
        return {
            "code": value.code,
            "details": _public_result({key: item for key, item in value.details.items() if key not in withheld}),
            "withheld_detail_keys": withheld,
        }
    field_names = _RESULT_FIELDS.get(type(value))
    if field_names is not None:
        return {name: _public_result(getattr(value, name)) for name in field_names}
    if isinstance(value, Enum):
        return _public_result(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if value is None or type(value) in (str, bool, int, float):
        return value
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("Preview result mappings require string keys.")
        return {key: _public_result(value[key]) for key in sorted(value)}
    if type(value) in (tuple, list):
        return [_public_result(item) for item in value]
    if type(value) in (set, frozenset):
        return sorted(_public_result(item) for item in value)
    raise TypeError("Unreviewed history preview result type.")


class HistoryPreviewResponseSerializer(serializers.BaseSerializer):
    """Render only actual public results, never prepared sessions or snapshots.

    Provenance is retained from the scoped adapters: machine labels, source
    references, evidence identities and diagnostic details, not raw audit rows,
    actors or source snapshots. No introspective dataclass serialization is used.
    """

    def to_representation(self, instance):
        if type(instance) is not preview.HistoryPreview:
            raise TypeError("Expected a HistoryPreview result.")
        return _public_result(instance)
