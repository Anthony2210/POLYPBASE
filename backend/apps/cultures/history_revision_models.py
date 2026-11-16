"""Inert history storage, not an authorization or publication service.

A publisher must scope queries, authorize, lock the stream and atomically insert
and advance its head. Operations are ordered JSON, not executed here. SQL guards
protect structural integrity, not JSON semantics or the provenance of SQL writers.
The sole revision update exception is author FK nulling for SET_NULL lifecycle;
this never changes the immutable author identifier or label.
"""

import hashlib
import json
import re
from datetime import datetime
from uuid import UUID, uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models, router
from django.db.models import Q

from .effective_history import TemporalPosition, TemporalPrecision
from .history_preview_validation import (
    MAX_JUSTIFICATION_LENGTH,
    MAX_JUSTIFICATIONS,
    MAX_OPERATIONS,
    PreviewInputError,
    _temporal,
)


SHA256 = re.compile(r"[0-9a-f]{64}\Z")
FORBIDDEN_KEYS = re.compile(r"password|secret|token|credential", re.I)
RAW_KEYS = {"raw", "raw_data", "raw_payload", "raw_snapshot", "thermal_payload", "thermal_snapshot", "payload", "blob"}

AWARE_DATETIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])\Z")
EVENT_KINDS = {"movement", "deactivation", "reactivation", "location_assignment", "reconstructed_movement", "initial_historical_location", "unclassified_movement", "period_boundary"}
TEMPORAL_KEYS = {"precision", "instant", "iso_year", "iso_week", "not_before", "before", "ordering_anchor"}
SNAPSHOT_KEYS = {"event_key", "kind", "temporal", "origin_zone_id", "destination_zone_id", "source_fingerprint", "sources"}
METADATA_KEYS = {"producer", "producer_version", "composition_version", "source_version", "compatibility_version", "source_kind"}


def validate_json(value, *, container, allowed_keys=None):
    """Bound conservative JSON; no arbitrary nested payloads or credential keys."""
    if not isinstance(value, container):
        raise ValidationError("Invalid history JSON container.")

    def walk(item, depth=0):
        if depth > 6:
            raise ValidationError("History JSON is too deeply nested.")
        if isinstance(item, dict):
            if len(item) > 64:
                raise ValidationError("Too many history JSON keys.")
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > 80 or FORBIDDEN_KEYS.search(key) or key.lower() in RAW_KEYS:
                    raise ValidationError("Unsupported history JSON key.")
                walk(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 256:
                raise ValidationError("Too many history JSON entries.")
            for child in item:
                walk(child, depth + 1)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValidationError("History values must be JSON primitives.")
        elif isinstance(item, str) and len(item) > MAX_JUSTIFICATION_LENGTH:
            raise ValidationError("History string is too long.")

    walk(value)
    if allowed_keys is not None:
        if set(value) - allowed_keys or any(isinstance(v, (dict, list)) for v in value.values()):
            raise ValidationError("Unsupported snapshot or metadata field.")
    try:
        encoded = json.dumps(value, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValidationError("Invalid JSON value.") from exc
    if len(encoded.encode("utf-8")) > 262144:
        raise ValidationError("History JSON exceeds the storage limit.")


def validate_digest(value):
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ValidationError("A lowercase SHA256 fingerprint is required.")


def require_shape(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - allowed or not set(required) <= set(value):
        raise ValidationError("Unsupported history object shape.")


def require_text(value, limit=200):
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise ValidationError("A bounded nonblank history string is required.")


def require_positive_id(value):
    if type(value) is not int or value < 1:
        raise ValidationError("A positive integer identifier is required.")


def require_uuid(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("A canonical server UUID is required.") from exc


def validate_event_key(value):
    require_text(value)
    # Persisted source keys remain unchanged. Durable reconstructed/inserted
    # keys carry backend identities, never browser draft labels.
    if value.startswith(("anchor:", "draft:")):
        require_uuid(value.split(":", 1)[1])


def validate_temporal(value):
    """Parse strict stored JSON and share Phase 3 temporal semantics."""
    require_shape(value, TEMPORAL_KEYS, {"precision"})
    parsed = dict(value)
    try:
        if type(value["precision"]) is not str:
            raise ValueError
        parsed["precision"] = TemporalPrecision(value["precision"])
        for key in ("instant", "not_before", "before", "ordering_anchor"):
            item = value.get(key)
            if item is not None:
                if type(item) is not str or not AWARE_DATETIME.fullmatch(item):
                    raise ValueError
                parsed[key] = datetime.fromisoformat(item)
        _temporal(TemporalPosition(**parsed), None)
    except (PreviewInputError, ValueError, TypeError, OverflowError) as exc:
        raise ValidationError("Invalid history temporal position.") from exc


def validate_event_fields(value):
    if "kind" in value and (type(value["kind"]) is not str or value["kind"] not in EVENT_KINDS):
        raise ValidationError("Unsupported event kind.")
    if "temporal" in value:
        validate_temporal(value["temporal"])
    for name in ("origin_zone_id", "destination_zone_id"):
        if value.get(name) is not None:
            require_positive_id(value[name])


def validate_snapshot(value):
    require_shape(value, SNAPSHOT_KEYS, {"event_key", "kind", "temporal"})
    validate_event_key(value["event_key"])
    validate_event_fields(value)
    if value.get("source_fingerprint") is not None:
        validate_digest(value["source_fingerprint"])
    sources = value.get("sources", [])
    if not isinstance(sources, list) or len(sources) > 64:
        raise ValidationError("A bounded list of source references is required.")
    for source in sources:
        require_shape(source, {"source", "source_id", "role", "basis", "exclusive"}, {"source", "source_id", "role"})
        for name in ("source", "role"):
            require_text(source[name])
        if type(source["source_id"]) is int:
            require_positive_id(source["source_id"])
        else:
            require_text(source["source_id"])
        if "basis" in source:
            require_text(source["basis"])
        if "exclusive" in source and type(source["exclusive"]) is not bool:
            raise ValidationError("Source exclusivity must be a boolean.")


def validate_operations(value):
    """Phase 4 JSON syntax with durable IDs; execution belongs to the decoder.

    The publisher must bind browser drafts to server UUIDs before calling this
    storage layer. UUID syntax alone cannot prove who generated an identifier.
    Omitted fields and explicit null zone changes remain distinct.
    """
    validate_json(value, container=list)
    if len(value) > MAX_OPERATIONS:
        raise ValidationError("At most 100 operations may be stored per revision.")
    fields = {"kind", "temporal", "origin_zone_id", "destination_zone_id"}
    shapes = {
        "modify_event": (fields | {"target"}, {"target"}),
        "adjust_event_kind": ({"target", "kind"}, {"target", "kind"}),
        "adjust_event_temporal": ({"target", "temporal"}, {"target", "temporal"}),
        "adjust_event_zones": ({"target", "origin_zone_id", "destination_zone_id"}, {"target"}),
        "insert_event": (fields | {"draft_id"}, {"draft_id", "kind", "temporal"}),
    }
    for operation in value:
        if not isinstance(operation, dict) or type(operation.get("type")) is not str or operation["type"] not in shapes:
            raise ValidationError("Unsupported stored operation type.")
        allowed, required = shapes[operation["type"]]
        require_shape(operation, allowed | {"type"}, required | {"type"})
        validate_event_fields(operation)
        if operation["type"] == "insert_event":
            require_uuid(operation["draft_id"])
        else:
            target = operation["target"]
            require_shape(target, {"key", "expected_evidence_fingerprint"}, {"key"})
            validate_event_key(target["key"])
            if target.get("expected_evidence_fingerprint") is not None:
                validate_digest(target["expected_evidence_fingerprint"])


def validate_justifications(value, operations):
    validate_json(value, container=list)
    if len(value) > MAX_JUSTIFICATIONS:
        raise ValidationError("Too many operation justifications.")
    justified = set()
    for item in value:
        require_shape(item, {"operation_index", "reason"}, {"operation_index", "reason"})
        index = item["operation_index"]
        if type(index) is not int or not 0 <= index < len(operations) or index in justified:
            raise ValidationError("Justification indices must be unique and reference an operation.")
        require_text(item["reason"], limit=MAX_JUSTIFICATION_LENGTH)
        justified.add(index)
    for index, operation in enumerate(operations):
        if operation["type"] == "insert_event" and index not in justified:
            raise ValidationError("An insertion requires an operation justification.")


class ImmutableHistoryQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("History records are immutable.")

    def delete(self):
        raise ValidationError("History records cannot be deleted.")

    def bulk_create(self, *args, **kwargs):
        raise ValidationError("History records require individual validated saves.")


class ImmutableHistoryModel(models.Model):
    objects = ImmutableHistoryQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("History records are immutable.")
        self.full_clean()
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("History records cannot be deleted.")


class BoxHistoryStream(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="box_history_streams")
    box = models.OneToOneField("cultures.Box", null=True, blank=True, on_delete=models.SET_NULL, related_name="history_stream")
    original_box_id = models.PositiveBigIntegerField(editable=False)
    head = models.ForeignKey("cultures.BoxHistoryRevision", null=True, blank=True, on_delete=models.PROTECT, related_name="headed_streams")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "original_box_id"], name="history_stream_original_unique"),
        ]

    def clean(self):
        super().clean()
        db = self._state.db or router.db_for_write(type(self), instance=self)
        if self.box_id:
            box = self._meta.get_field("box").remote_field.model.objects.using(db).filter(pk=self.box_id).first()
            if box is None or box.organization_id != self.organization_id or box.pk != self.original_box_id:
                raise ValidationError("History stream scope does not match its box.")
        old = type(self).objects.using(db).filter(pk=self.pk).first()
        if old is None and self.box_id is None:
            raise ValidationError("A new history stream requires a live box.")
        if old and (old.organization_id != self.organization_id or old.original_box_id != self.original_box_id or (self.box_id != old.box_id and self.box_id is not None)):
            raise ValidationError("History stream scope is immutable; its box can only detach.")
        if self.head_id:
            head = BoxHistoryRevision.objects.using(db).get(pk=self.head_id)
            if head.stream_id != self.pk:
                raise ValidationError("Head must belong to this stream.")
            if not old or old.head_id != self.head_id:

                if head.predecessor_id != (old.head_id if old else None):
                    raise ValidationError("Head must advance by exactly one revision.")
        elif old and old.head_id:
            raise ValidationError("History head cannot be cleared.")

    def save(self, *args, **kwargs):
        if self._state.adding and self.box_id and not self.original_box_id:
            self.original_box_id = self.box_id
        self.full_clean()
        return super().save(*args, **kwargs)


class BoxHistoryEventIdentity(ImmutableHistoryModel):
    class Kind(models.TextChoices):
        PERSISTED = "persisted"
        DERIVED = "derived"
        INSERTED = "inserted"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    stream = models.ForeignKey(BoxHistoryStream, on_delete=models.PROTECT, related_name="event_identities")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    source_key = models.CharField(max_length=200, blank=True, default="")
    locator = models.JSONField(default=dict, blank=True)
    derived_source_key = models.CharField(max_length=64, blank=True, default="", editable=False)
    source_fingerprint = models.CharField(max_length=64, blank=True, default="")
    source_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["stream", "source_key"], condition=~Q(source_key=""), name="history_identity_source_unique"),
            models.UniqueConstraint(fields=["stream", "derived_source_key"], condition=~Q(derived_source_key=""), name="history_identity_derived_unique"),
            models.CheckConstraint(condition=(Q(kind="persisted", derived_source_key="") & ~Q(source_key="") & Q(source_fingerprint="", locator={})) | (Q(kind="derived", source_key="") & ~Q(derived_source_key="") & ~Q(source_fingerprint="") & ~Q(locator={})) | Q(kind="inserted", source_key="", derived_source_key="", source_fingerprint="", locator={}), name="history_identity_shape"),
        ]

    def clean(self):
        super().clean()
        # Preserve original locator names (including source table names) rather
        # than reducing them to a lossy domain whitelist.
        validate_json(self.locator, container=dict)
        for value in self.locator.values():
            if type(value) is int:
                require_positive_id(value)
            else:
                require_text(value)
        validate_json(self.source_snapshot, container=dict)
        if self.source_snapshot:
            validate_snapshot(self.source_snapshot)
        if self.kind == self.Kind.PERSISTED and not self.source_key.strip():
            raise ValidationError("Persisted identities require a nonblank source key.")
        if self.kind == self.Kind.DERIVED:
            validate_digest(self.source_fingerprint)
            # Freeze this canonical encoding: sort all object keys, compact JSON,
            # ASCII escapes, UTF-8, and no NaN. SQL guards validate the key's
            # shape/uniqueness, not its derivation; raw writers must compute it.
            canonical = json.dumps(
                {"locator": self.locator, "source_fingerprint": self.source_fingerprint},
                sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
            )
            self.derived_source_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class BoxHistoryRevision(ImmutableHistoryModel):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    stream = models.ForeignKey(BoxHistoryStream, on_delete=models.PROTECT, related_name="revisions")
    sequence = models.PositiveBigIntegerField()
    predecessor = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="successors")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="box_history_revisions")
    author_identifier = models.PositiveBigIntegerField(editable=False)
    author_label = models.CharField(max_length=300, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=1000)
    operations = models.JSONField(default=list, blank=True)
    justifications = models.JSONField(default=list, blank=True)
    review_values = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    schema_version = models.PositiveSmallIntegerField(default=1)
    source_fingerprint = models.CharField(max_length=64)
    idempotency_key = models.UUIDField(default=uuid4, editable=False)
    request_fingerprint = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["stream", "sequence"], name="history_revision_sequence_unique"),
            models.UniqueConstraint(fields=["stream", "idempotency_key"], name="history_revision_request_unique"),
            models.UniqueConstraint(fields=["predecessor"], condition=Q(predecessor__isnull=False), name="history_revision_no_branch"),
            models.CheckConstraint(condition=Q(sequence__gte=1, schema_version__gte=1), name="history_revision_positive"),
            models.CheckConstraint(condition=Q(sequence=1, predecessor__isnull=True) | Q(sequence__gt=1, predecessor__isnull=False), name="history_revision_root_shape"),
            models.CheckConstraint(condition=~Q(reason=""), name="history_revision_reason_required"),
        ]

    def clean(self):
        super().clean()
        if not self.reason.strip():
            raise ValidationError("A revision reason is required.")
        if self.predecessor_id:
            previous = type(self).objects.using(self._state.db or router.db_for_write(type(self), instance=self)).get(pk=self.predecessor_id)
            if previous.stream_id != self.stream_id or previous.sequence != self.sequence - 1:
                raise ValidationError("Predecessor must be the preceding revision in the same stream.")
        validate_digest(self.source_fingerprint)
        validate_digest(self.request_fingerprint)
        validate_operations(self.operations)
        validate_justifications(self.justifications, self.operations)
        validate_json(self.review_values, container=dict)
        require_shape(self.review_values, {"events"})
        events = self.review_values.get("events", [])
        if not isinstance(events, list) or len(events) > 100:
            raise ValidationError("A review may contain at most 100 event snapshots.")
        for event in events:
            validate_snapshot(event)
        validate_json(self.metadata, container=dict, allowed_keys=METADATA_KEYS)

    def save(self, *args, **kwargs):
        if self._state.adding:
            db = kwargs.get("using") or router.db_for_write(type(self), instance=self)
            user = get_user_model().objects.using(db).filter(pk=self.author_id, is_active=True).first()
            if user is None or not user.is_authenticated:
                raise ValidationError("An authenticated stored active author is required.")
            self.author_identifier = user.pk
            self.author_label = user.get_full_name().strip() or user.get_username()
        return super().save(*args, **kwargs)
