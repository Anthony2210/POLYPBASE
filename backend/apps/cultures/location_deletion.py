"""Permanent deletion of laboratory locations that were never used.

Product rule (2026-11-05): a location may be deleted only when it has never
been used and nothing references it. Zero active boxes is not proof of
non-use, so eligibility is established from every stored trace:

* every reverse database relation of ThermalZone (boxes, location periods,
  movements, measurements, probes, tags, anomalies, alerts), read from the
  model registry so a future relation is checked without code changes;
* JSON documents that can name a location (audit metadata, box-creation
  intents, exports, imports, transfers, subculture and lineage snapshots);
* audit entries, except the location's own configuration history (its
  creation and edits), which is not a use and stays after the deletion.

The deletion locks the location row, re-checks everything under that lock,
writes a DELETION audit entry and deletes only the location row, inside one
transaction. Anything else that would be deleted or nulled aborts it.
"""

import json

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connections, router, transaction
from django.db.models import Q, TextField
from django.db.models.deletion import Collector, ProtectedError, RestrictedError
from django.db.models.functions import Cast

from apps.accounts.models import OrganizationMembership
from apps.accounts.permissions import user_can_administer_organization
from apps.audit.models import AuditLog
from apps.exports.models import DataExport, ExcelImportRow
from apps.measurements.models import HistoricalTemperatureImportReceipt, MeasurementImportReceipt
from apps.organizations.models import Organization

from .models import (
    AutomaticBoxCreationRequest,
    Box,
    BoxInventoryInitialization,
    BoxTransferImport,
    HistoricalBoxMovement,
    SubcultureEvent,
    ThermalZone,
    TransferItem,
)

REASON_OCCUPIED = "occupied"
REASON_HISTORY = "history"

DELETION_EVENT = "thermal_zone_deletion"
DELETION_REASON = "never_used"

# Values written by _thermal_zone_audit_values: the configuration of a location.
CONFIGURATION_AUDIT_FIELDS = frozenset(
    {"nom", "type", "temperature_consigne", "capacite", "salinite_psu", "active"}
)
_CONFIGURATION_METADATA_KEYS = frozenset({"thermal_zone_id", "valeurs", "modifications"})

# Keys that contain "zone" without naming a location.
_NON_LOCATION_ZONE_KEYS = frozenset(
    {"zone_type", "week_time_zone", "time_zone", "include_other_zones",
     "has_current_thermal_zone", "thermal_zones_unchanged"}
)

# Measurement rows hang off a probe; any probe of the location already blocks.
JSON_EXEMPTIONS = {
    "measurements.TemperatureMeasurement": "raw probe payload; its probe relation is checked",
    "sharing.BoxReaderShare": "scientific snapshot selector names only; the catalogue has no location",
}


class ThermalZoneInUse(Exception):
    def __init__(self, reasons):
        super().__init__(", ".join(reasons))
        self.reasons = reasons


class ThermalZoneChanged(Exception):
    """The location changed while it was being deleted; nothing was deleted."""


class ThermalZoneDeletionForbidden(Exception):
    """The actor does not administer the institution at deletion time."""


def _lock_admin_authority(organization_id, actor):
    """Lock organization -> membership -> user, then revalidate administration.

    Same order and NO KEY UPDATE mode as box creation and intent registration,
    so member changes (which lock the organization first) are serialized with
    the deletion and the decision reads the current, locked state.
    """
    organization = Organization.objects.select_for_update(no_key=True).filter(pk=organization_id).first()
    if organization is None:
        raise ThermalZoneDeletionForbidden()
    list(OrganizationMembership.objects.select_for_update(no_key=True).filter(
        organization=organization, user_id=actor.pk,
    ))
    user = get_user_model().objects.select_for_update(no_key=True).filter(pk=actor.pk).first()
    if user is None or not user.is_active or not user_can_administer_organization(user, organization):
        raise ThermalZoneDeletionForbidden()
    return user


def _json_sources(zone):
    """(queryset, JSON fields) that may name a location of this institution."""
    organization_id = zone.organization_id
    return [
        (AuditLog.objects.filter(Q(organization_id=organization_id) | Q(organization__isnull=True)),
         ("metadata", "original_values")),
        (AutomaticBoxCreationRequest.objects.filter(organization_id=organization_id), ("intent", "failure")),
        (DataExport.objects.filter(organization_id=organization_id), ("filters",)),
        (HistoricalBoxMovement.objects.filter(box__organization_id=organization_id), ("evidence",)),
        (ExcelImportRow.objects.filter(excel_import__organization_id=organization_id), ("raw_data", "errors")),
        (MeasurementImportReceipt.objects.filter(organization_id=organization_id),
         ("preparation", "accepted_plan", "result")),
        (HistoricalTemperatureImportReceipt.objects.filter(organization_id=organization_id),
         ("accepted_plan", "results")),
        (BoxTransferImport.objects.filter(destination_organization_id=organization_id), ("source_data",)),
        (BoxInventoryInitialization.objects.filter(organization_id=organization_id), ("previous_status_counts",)),
        (SubcultureEvent.objects.filter(parent_box__organization_id=organization_id), ("parent_state_snapshot",)),
        (TransferItem.objects.filter(source_box__organization_id=organization_id), ("lineage_snapshot",)),
    ]


def scanned_json_models():
    """Labels of the models whose JSON documents are scanned (checked by tests)."""
    sources = _json_sources(ThermalZone(organization_id=0))
    return {queryset.model._meta.label for queryset, _ in sources}


def _is_location_key(key):
    name = str(key).lower()
    return name not in _NON_LOCATION_ZONE_KEYS and ("zone" in name or name.startswith("emplacement"))


def _references(value, zone_id, names, *, under_location_key=False):
    """True when a location key (at any depth) holds this location's id or a name it had."""
    if isinstance(value, dict):
        return any(
            _references(item, zone_id, names, under_location_key=under_location_key or _is_location_key(key))
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_references(item, zone_id, names, under_location_key=under_location_key) for item in value)
    if not under_location_key:
        return False
    # bool is an int subclass: True must never match location 1.
    if type(value) is int:
        return value == zone_id
    if isinstance(value, str):
        text = value.strip()
        return text == str(zone_id) or text in names
    return False


def _is_own_configuration_entry(object_type, action, metadata, zone_id):
    if object_type != "thermal_zone" or action not in (AuditLog.Action.CREATION, AuditLog.Action.UPDATE):
        return False
    if not isinstance(metadata, dict) or type(metadata.get("thermal_zone_id")) is not int:
        return False
    if metadata["thermal_zone_id"] != zone_id or not set(metadata) <= _CONFIGURATION_METADATA_KEYS:
        return False
    for key in ("valeurs", "modifications"):
        part = metadata.get(key, {})
        if not isinstance(part, dict) or not set(part) <= CONFIGURATION_AUDIT_FIELDS:
            return False
    return True


def _audit_zone_id(metadata):
    value = metadata.get("thermal_zone_id") if isinstance(metadata, dict) else None
    return value if type(value) is int else None


def _known_names(zone):
    """Every name the location carried, read from its own configuration history."""
    names = {zone.name}
    entries = AuditLog.objects.filter(organization_id=zone.organization_id, object_type="thermal_zone")
    for object_type, action, object_id, metadata in entries.values_list(
        "object_type", "action", "object_id", "metadata",
    ).iterator():
        if not _is_own_configuration_entry(object_type, action, metadata, zone.id):
            continue
        names.add(object_id)
        name = metadata.get("valeurs", {}).get("nom")
        if isinstance(name, str):
            names.add(name)
        change = metadata.get("modifications", {}).get("nom")
        if isinstance(change, dict):
            names.update(value for value in change.values() if isinstance(value, str))
    return {name.strip() for name in names if isinstance(name, str) and name.strip()}


def _text_prefilter(fields, needles):
    """Rows whose JSON text contains a needle: a superset of real references."""
    condition = Q()
    annotations = {}
    for field in fields:
        alias = f"_{field}_text"
        annotations[alias] = Cast(field, TextField())
        for needle in needles:
            condition |= Q(**{f"{alias}__contains": needle})
    return annotations, condition


def _has_json_reference(zone, names):
    needles = {str(zone.id)}
    for name in names:
        needles.add(name)
        needles.add(json.dumps(name)[1:-1])  # SQLite keeps the escaped form
    for queryset, fields in _json_sources(zone):
        annotations, condition = _text_prefilter(fields, needles)
        is_audit = queryset.model is AuditLog
        if is_audit:
            # Legacy audit entries may identify the location only by its name.
            condition |= Q(object_type="thermal_zone", object_id__in=names)
        columns = ("object_type", "action", "object_id", *fields) if is_audit else fields
        rows = queryset.annotate(**annotations).filter(condition).values_list(*columns)
        for row in rows.iterator():
            if is_audit:
                object_type, action, object_id, *documents = row
                metadata = documents[0]
                if _is_own_configuration_entry(object_type, action, metadata, zone.id):
                    continue
                entry_zone_id = _audit_zone_id(metadata)
                if object_type == "thermal_zone" and (
                    entry_zone_id == zone.id or (entry_zone_id is None and object_id in names)
                ):
                    return True
            else:
                documents = row
            if any(_references(document, zone.id, names) for document in documents):
                return True
    return False


def thermal_zone_usage_reasons(zone):
    """Why the location cannot be deleted; an empty list means it was never used."""
    reasons = []
    if zone.boxes.exists():
        reasons.append(REASON_OCCUPIED)
    # Every other reverse relation, read from the registry: periods, movements,
    # measurements, probes, tags, anomalies, alerts and any future relation.
    other_relations = [
        relation for relation in ThermalZone._meta.related_objects
        if not (relation.related_model is Box and relation.field.name == "thermal_zone")
    ]
    has_related_rows = any(
        relation.related_model._base_manager.filter(**{relation.field.name: zone}).exists()
        for relation in other_relations
    )
    if has_related_rows or _has_json_reference(zone, _known_names(zone)):
        reasons.append(REASON_HISTORY)
    return reasons


def _has_rows(objects):
    return objects.exists() if hasattr(objects, "exists") else bool(len(objects))


def _collector_touches_other_rows(collector):
    updates = (objects for batches in collector.field_updates.values() for objects in batches)
    return any(_has_rows(objects) for objects in updates) or any(
        _has_rows(queryset) for queryset in collector.fast_deletes
    )


def delete_unused_thermal_zone(zone_id, *, organization_id, actor, audit_values):
    """Delete a never-used location of `organization_id`, audited, or raise.

    `audit_values(zone)` gives the configuration recorded as the before-state,
    the same values as the creation and edit audit entries.
    Raises ThermalZoneDeletionForbidden, ThermalZone.DoesNotExist,
    ThermalZoneInUse or ThermalZoneChanged. Administration is revalidated under
    lock inside the transaction, whatever the caller checked before.

    Lock order: organization -> membership -> user -> location, as in box
    creation and intent registration. The location is locked FOR UPDATE, which
    conflicts with the NO KEY UPDATE lock an intent takes before naming it.
    """
    using = router.db_for_write(ThermalZone)
    connection = connections[using]
    try:
        with transaction.atomic(using=using):
            if connection.vendor == "postgresql":
                # Report a reference committed concurrently at the DELETE itself.
                with connection.cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            actor = _lock_admin_authority(organization_id, actor)
            # The row lock serializes with edits, with intents naming the location
            # and with any new foreign key reference, which must read this row.
            zone = ThermalZone.objects.select_for_update().get(pk=zone_id, organization_id=organization_id)
            reasons = thermal_zone_usage_reasons(zone)
            if reasons:
                raise ThermalZoneInUse(reasons)

            before = audit_values(zone)
            AuditLog.objects.create(
                organization_id=zone.organization_id,
                user=actor,
                action=AuditLog.Action.DELETION,
                object_type="thermal_zone",
                object_id=zone.name,
                description=f"Thermal zone deleted: {zone.name}",
                metadata={
                    "thermal_zone_id": zone.id,
                    "event": DELETION_EVENT,
                    "reason": DELETION_REASON,
                    "valeurs": before,
                },
            )

            collector = Collector(using=using, origin=zone)
            try:
                collector.collect([zone])
            except (ProtectedError, RestrictedError) as error:
                raise ThermalZoneInUse([REASON_HISTORY]) from error
            # Nothing may be nulled or deleted besides the location row itself.
            # Django schedules SET_NULL and fast deletes as lazy querysets, so
            # their content is checked, not their presence.
            if _collector_touches_other_rows(collector):
                raise ThermalZoneInUse([REASON_HISTORY])
            if {model: len(objects) for model, objects in collector.data.items()} != {ThermalZone: 1}:
                raise ThermalZoneInUse([REASON_HISTORY])
            _, deleted = collector.delete()
            if {label: count for label, count in deleted.items() if count} != {ThermalZone._meta.label: 1}:
                raise ThermalZoneChanged()
            return before
    except IntegrityError as error:
        raise ThermalZoneChanged() from error


__all__ = [
    "DELETION_EVENT",
    "JSON_EXEMPTIONS",
    "REASON_HISTORY",
    "REASON_OCCUPIED",
    "ThermalZoneChanged",
    "ThermalZoneDeletionForbidden",
    "ThermalZoneInUse",
    "delete_unused_thermal_zone",
    "scanned_json_models",
    "thermal_zone_usage_reasons",
]
