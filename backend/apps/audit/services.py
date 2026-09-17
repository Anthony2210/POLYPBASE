"""Shared querying and presentation helpers for audited business actions."""

import re

from django.db.models import (
    Case,
    CharField,
    Count,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils.dateparse import parse_date

from .models import AuditLog


IMPACTFUL_AUDIT_ACTIONS = (
    AuditLog.Action.CREATION,
    AuditLog.Action.UPDATE,
    AuditLog.Action.ARCHIVE,
    AuditLog.Action.ENTRY,
    AuditLog.Action.SUBCULTURE,
    AuditLog.Action.TRANSFER,
    AuditLog.Action.IMPORT,
    AuditLog.Action.EXPORT,
)

AUDIT_FAMILIES = (
    "measurements",
    "transfers",
    "subcultures",
    "boxes",
    "exports",
    "environment",
    "accounts",
    "references",
)

_MEASUREMENT_DESCRIPTION_PREFIX = "Biological measurement for "
_VALID_YEAR_REGEX = r"([1-9][0-9]{3}|0[1-9][0-9]{2}|00[1-9][0-9]|000[1-9])"
_COMMON_MONTH_DAY_REGEX = (
    r"((01|03|05|07|08|10|12)-(0[1-9]|[12][0-9]|3[01])"
    r"|(04|06|09|11)-(0[1-9]|[12][0-9]|30)"
    r"|02-(0[1-9]|1[0-9]|2[0-8]))"
)
_LEAP_YEAR_REGEX = (
    r"([0-9]{2}(0[48]|[2468][048]|[13579][26])"
    r"|(0[48]|[2468][048]|[13579][26])00)"
)
_VALID_DATE_REGEX = (
    rf"(({_VALID_YEAR_REGEX})-{_COMMON_MONTH_DAY_REGEX}|({_LEAP_YEAR_REGEX})-02-29)"
)
_MEASUREMENT_DESCRIPTION_REGEX = (
    rf"\A{_MEASUREMENT_DESCRIPTION_PREFIX}{_VALID_DATE_REGEX}\Z"
)
_ENVIRONMENT_OBJECT_TYPES = {"thermal_zone", "probe", "alert"}
_REFERENCE_OBJECT_TYPES = {"species", "strain", "organization"}
_BOX_OBJECT_TYPES = {"box", "box_inventory_initialization"}

# Object types whose stored object id is a database primary key rather than a
# business-readable label, so it must never reach a user-facing payload.
_PRIMARY_KEY_RESOURCE_OBJECT_TYPES = {"alert", "species", "strain"}

# Pre-migration box statuses that no longer exist in the canonical lifecycle.
# They are inactive-like and must be presented as the canonical inactive value.
_LEGACY_BOX_STATUS_TOKENS = {"archived", "lost", "stopped"}

_MEASUREMENT_FIELDS = {
    "date",
    "polypes",
    "ephyrules",
    "strobiles",
    "salinite_psu",
    "statut_culture",
    "a_verifier",
    "note",
}
_BOX_FIELDS = {
    "code_global",
    "numero_boite",
    "souche",
    "espece",
    "emplacement",
    "date_entree",
    "volume_litres",
    "statut",
    "raison_arret",
    "note",
}
_ENVIRONMENT_FIELDS = {
    "nom",
    "type",
    "temperature_consigne",
    "capacite",
    "salinite_psu",
    "active",
    "date",
    "temperature_c",
    "code",
    "emplacement",
    "position",
}
_ACCOUNT_FIELDS = {
    "nom",
    "email",
    "role",
    "acces_actif",
    "is_responsable",
    "actif",
    "responsable",
}
_REFERENCE_FIELDS = {"nom", "ville", "pays", "email_contact", "notes"}
_EXPORT_FILTER_FIELDS = {"date_from", "date_to", "include_other_zones"}

_PERSONAL_DETAIL_FIELDS_BY_OBJECT_TYPE = {
    "box": _MEASUREMENT_FIELDS
    | _BOX_FIELDS
    | {"ancienne_zone", "nouvelle_zone", "date_deplacement"},
    "probe": _ENVIRONMENT_FIELDS,
    "thermal_zone": _ENVIRONMENT_FIELDS,
}


def impactful_audit_logs(*, organization_id, actor=None):
    """Return supported business actions within exactly one organization."""
    queryset = AuditLog.objects.filter(
        organization_id=organization_id,
        action__in=IMPACTFUL_AUDIT_ACTIONS,
    )
    if actor is not None:
        queryset = queryset.filter(user=actor)
    return annotate_business_family(queryset)


def annotate_business_family(queryset):
    """Annotate every impactful row with one approved product family.

    Current writers identify measurements with ``measurement_id``. Older
    measurement rows can lack it, so the exact, writer-owned English prefix is
    retained as a narrow compatibility discriminator. No translated UI text or
    arbitrary description parsing is used. Unknown box events fall back to
    ``boxes``; other unknown impactful rows fall back to ``references`` so they
    remain visible instead of disappearing.
    """
    return queryset.annotate(
        business_family=Case(
            When(action=AuditLog.Action.EXPORT, then=Value("exports")),
            When(action=AuditLog.Action.SUBCULTURE, then=Value("subcultures")),
            When(action=AuditLog.Action.TRANSFER, then=Value("transfers")),
            When(
                action=AuditLog.Action.IMPORT,
                object_type="box",
                then=Value("transfers"),
            ),
            When(
                action=AuditLog.Action.ENTRY,
                object_type="box",
                then=Value("measurements"),
            ),
            When(
                action=AuditLog.Action.UPDATE,
                object_type="box",
                metadata__has_key="measurement_id",
                then=Value("measurements"),
            ),
            When(
                action=AuditLog.Action.UPDATE,
                object_type="box",
                description__regex=_MEASUREMENT_DESCRIPTION_REGEX,
                then=Value("measurements"),
            ),
            When(object_type__in=_ENVIRONMENT_OBJECT_TYPES, then=Value("environment")),
            When(object_type="account", then=Value("accounts")),
            When(object_type__in=_REFERENCE_OBJECT_TYPES, then=Value("references")),
            When(object_type__in=_BOX_OBJECT_TYPES, then=Value("boxes")),
            default=Value("references"),
            output_field=CharField(),
        )
    )


def classify_audit_log(log):
    """Return the same product family used by the queryset annotation."""
    annotated_family = getattr(log, "business_family", None)
    if annotated_family in AUDIT_FAMILIES:
        return annotated_family

    metadata = _metadata(log)
    if log.action == AuditLog.Action.EXPORT:
        return "exports"
    if log.action == AuditLog.Action.SUBCULTURE:
        return "subcultures"
    if log.action == AuditLog.Action.TRANSFER:
        return "transfers"
    if log.action == AuditLog.Action.IMPORT and log.object_type == "box":
        return "transfers"
    if log.object_type == "box" and (
        log.action == AuditLog.Action.ENTRY
        or (
            log.action == AuditLog.Action.UPDATE
            and (
                "measurement_id" in metadata
                or _legacy_measurement_date(log.description) is not None
            )
        )
    ):
        return "measurements"
    if log.object_type in _ENVIRONMENT_OBJECT_TYPES:
        return "environment"
    if log.object_type == "account":
        return "accounts"
    if log.object_type in _REFERENCE_OBJECT_TYPES:
        return "references"
    if log.object_type in _BOX_OBJECT_TYPES:
        return "boxes"
    return "references"


def legacy_measurement_lookup_key(log):
    """Return the exact box/date key for a supported legacy measurement row.

    Legacy rows predate ``measurement_id`` but were written with the stable
    ``Biological measurement for YYYY-MM-DD`` backend description. Requiring
    both the centralized measurement classification and the complete historical
    format prevents unrelated dated box events from being enriched.
    """
    metadata = _metadata(log)
    if "measurement_id" in metadata or classify_audit_log(log) != "measurements":
        return None
    if not log.object_id:
        return None

    measured_on = _legacy_measurement_date(log.description)
    if measured_on is None:
        return None
    return log.object_id, measured_on


def _legacy_measurement_date(description):
    description = description or ""
    if re.fullmatch(_MEASUREMENT_DESCRIPTION_REGEX, description) is None:
        return None
    date_text = description.removeprefix(_MEASUREMENT_DESCRIPTION_PREFIX)
    measured_on = parse_date(date_text)
    if measured_on is None or measured_on.isoformat() != date_text:
        return None
    return measured_on


def parse_audit_pagination(query_params, *, default_limit=40, max_limit=100):
    """Parse the existing limit/offset contract with bounded safe defaults."""
    try:
        limit = int(query_params.get("limit", default_limit))
    except (TypeError, ValueError):
        limit = default_limit
    limit = max(1, min(limit, max_limit))

    try:
        offset = int(query_params.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    offset = max(0, offset)
    return limit, offset


def paginate_audit_logs(queryset, *, limit, offset, legacy_effective_order=False):
    """Return one stable offset page and whether another row exists."""
    if legacy_effective_order:
        queryset = queryset.annotate(effective_at=Coalesce("edited_at", "created_at"))
        ordering = ("-effective_at", "-id")
    else:
        ordering = ("-created_at", "-id")

    logs = list(queryset.order_by(*ordering)[offset : offset + limit + 1])
    has_more = len(logs) > limit
    return logs[:limit], has_more


def resolve_audit_box_references(logs, *, organization_id):
    """Resolve current box targets inside exactly one active organization."""
    from apps.cultures.models import Box

    global_codes = {
        log.object_id
        for log in logs
        if log.object_type == "box" and isinstance(log.object_id, str) and log.object_id
    }
    if not global_codes:
        return {}

    boxes = Box.objects.filter(
        organization_id=organization_id,
        global_code__in=global_codes,
    ).select_related("strain__species")
    return {box.global_code: box for box in boxes}


def resolve_audit_subculture_children(logs, *, organization_id):
    """Resolve current child box codes from stored ids inside one institution."""
    from apps.cultures.models import Box

    child_ids_by_log_id = {}
    all_child_ids = set()
    for log in logs:
        if log.action != AuditLog.Action.SUBCULTURE:
            continue
        raw_ids = _metadata(log).get("child_box_ids")
        if not isinstance(raw_ids, list):
            continue
        child_ids = [
            value if type(value) is int and value > 0 else None
            for value in raw_ids
        ]
        child_ids_by_log_id[log.id] = child_ids
        all_child_ids.update(child_id for child_id in child_ids if child_id is not None)

    boxes_by_id = {
        box.id: box
        for box in Box.objects.filter(
            id__in=all_child_ids,
            organization_id=organization_id,
        ).only("id", "global_code")
    }
    return {
        log_id: [boxes_by_id.get(child_id) if child_id is not None else None for child_id in child_ids]
        for log_id, child_ids in child_ids_by_log_id.items()
    }


def resolve_audit_measurements(logs, *, organization_id):
    """Resolve explicit measurement identities without crossing organizations."""
    from apps.measurements.models import BiologicalMeasurement

    measurement_ids = {
        metadata["measurement_id"]
        for log in logs
        if type((metadata := _metadata(log)).get("measurement_id")) is int
        and metadata["measurement_id"] > 0
    }
    if not measurement_ids:
        return {}

    measurements = BiologicalMeasurement.objects.filter(
        id__in=measurement_ids,
        box__organization_id=organization_id,
    ).select_related("box")
    return {measurement.id: measurement for measurement in measurements}


def related_measurement_action_counts(
    logs,
    *,
    organization_id,
    valid_measurement_ids,
):
    """Count other structured measurement events for each eligible page row.

    The supplied valid ids must already have been resolved against the active
    institution. One grouped query then covers every explicit measurement id on
    the page; legacy box/date/text references are intentionally excluded.
    """
    measurement_ids = {
        metadata["measurement_id"]
        for log in logs
        if log.action in {AuditLog.Action.ENTRY, AuditLog.Action.UPDATE}
        and classify_audit_log(log) == "measurements"
        and type((metadata := _metadata(log)).get("measurement_id")) is int
        and metadata["measurement_id"] > 0
        and metadata["measurement_id"] in valid_measurement_ids
    }
    if not measurement_ids:
        return {}

    grouped_counts = (
        annotate_business_family(
            AuditLog.objects.filter(
                organization_id=organization_id,
                action__in=(AuditLog.Action.ENTRY, AuditLog.Action.UPDATE),
                metadata__measurement_id__in=measurement_ids,
            )
        )
        .filter(business_family="measurements")
        .values("metadata__measurement_id")
        .annotate(count=Count("id"))
    )
    counts_by_measurement_id = {
        row["metadata__measurement_id"]: max(row["count"] - 1, 0)
        for row in grouped_counts
        if type(row["metadata__measurement_id"]) is int
        and row["metadata__measurement_id"] in measurement_ids
    }
    return {
        log.id: counts_by_measurement_id.get(metadata["measurement_id"], 0)
        for log in logs
        if log.action in {AuditLog.Action.ENTRY, AuditLog.Action.UPDATE}
        and classify_audit_log(log) == "measurements"
        and type((metadata := _metadata(log)).get("measurement_id")) is int
        and metadata["measurement_id"] in measurement_ids
    }


def resolve_legacy_audit_measurements(logs, *, organization_id):
    """Resolve all supported legacy box/date references in one scoped query."""
    from apps.measurements.models import BiologicalMeasurement

    lookup_keys = {
        lookup_key
        for log in logs
        if (lookup_key := legacy_measurement_lookup_key(log)) is not None
    }
    if not lookup_keys:
        return {}

    global_codes = {global_code for global_code, _measured_on in lookup_keys}
    measured_dates = {measured_on for _global_code, measured_on in lookup_keys}
    measurements = BiologicalMeasurement.objects.filter(
        box__global_code__in=global_codes,
        box__organization_id=organization_id,
        measured_on__in=measured_dates,
    ).select_related("box")
    return {
        lookup_key: measurement
        for measurement in measurements
        if (lookup_key := (measurement.box.global_code, measurement.measured_on))
        in lookup_keys
    }


def readable_account_label(user):
    """Return a human-readable account label, never an internal username."""
    if user is None:
        return None

    full_name = " ".join(
        part for part in [user.first_name, user.last_name] if part
    ).strip()
    if full_name:
        return full_name

    email = (user.email or "").strip()
    return email or None


def serialize_personal_audit_log(
    log,
    *,
    box=None,
    measurement=None,
    subculture_children=None,
):
    """Serialize one action without raw or administration-only metadata."""
    return {
        "id": log.id,
        "created_at": log.created_at,
        "action": log.action,
        "action_label": log.get_action_display(),
        "family": classify_audit_log(log),
        "resource": {
            "type": log.object_type,
            "identifier": _personal_resource_identifier(log),
            "label": _personal_resource_label(log),
        },
        "description": log.description,
        "details": _personal_audit_details(log),
        "business_details": serialize_business_details(
            log,
            measurement=measurement,
            subculture_children=subculture_children,
        ),
        "box_reference": serialize_box_reference(box),
        "context": serialize_audit_context(
            log,
            measurement=measurement,
            subculture_children=subculture_children,
        ),
    }


def serialize_business_details(log, *, measurement=None, subculture_children=None):
    """Normalize known business metadata into a stable discriminated contract."""
    metadata = _metadata(log)
    family = classify_audit_log(log)

    if family == "measurements":
        values = _allowlisted_values(metadata.get("valeurs"), _MEASUREMENT_FIELDS)
        if not values and measurement is not None:
            values = _measurement_values(measurement)
        return _compact_details(
            "measurement",
            values=values,
            changes=_allowlisted_changes(metadata.get("modifications"), _MEASUREMENT_FIELDS),
        )

    if family == "subcultures":
        children = _normalized_subculture_children(
            metadata,
            resolved_children=subculture_children,
        )
        return _compact_details(
            "subculture",
            parent_global_code=_safe_string(log.object_id),
            child_global_codes=[child["global_code"] for child in children],
            initial_polyp_counts={
                child["global_code"]: child["initial_polyp_count"]
                for child in children
                if child["initial_polyp_count"] is not None
            },
        )

    if family == "transfers" and log.action == AuditLog.Action.TRANSFER:
        return _compact_details(
            "transfer_out",
            destination_organization=_safe_string(metadata.get("to_organization")),
            date=_safe_string(metadata.get("date")),
            polyp_count=_safe_number(metadata.get("polypes")),
            note=_safe_string(metadata.get("note"), allow_empty=True),
        )

    if family == "transfers" and log.action == AuditLog.Action.IMPORT:
        return _compact_details(
            "transfer_import",
            source_global_code=_safe_string(metadata.get("source_global_code")),
            source_organization=_safe_string(metadata.get("source_organization")),
        )

    if family == "exports":
        filters = {
            key: value
            for key in _EXPORT_FILTER_FIELDS
            if (value := metadata.get(key)) is not None and _is_safe_scalar(value)
        }
        return _compact_details(
            "export",
            box_count=_safe_number(metadata.get("box_count")),
            measurement_count=_safe_number(metadata.get("measurement_count")),
            week_count=_safe_number(metadata.get("week_count")),
            filters=filters,
        )

    if family == "boxes" and _is_box_movement(metadata):
        return _compact_details(
            "box_movement",
            from_zone=_safe_string(metadata.get("from_thermal_zone_name"), allow_empty=True),
            to_zone=_safe_string(metadata.get("to_thermal_zone_name")),
            moved_at=_safe_string(metadata.get("moved_at")),
            note=_safe_string(metadata.get("note"), allow_empty=True),
        )

    if family == "boxes" and isinstance(metadata.get("transition"), str):
        after_candidate = metadata.get("after")
        after = after_candidate if isinstance(after_candidate, dict) else {}
        transition = metadata["transition"].split("->", 1)
        return _compact_details(
            "box_status",
            transition={"from": transition[0], "to": transition[1]} if len(transition) == 2 else None,
            stop_reason=_safe_string(after.get("stop_reason"), allow_empty=True),
            stop_reason_missing_from_history=(
                after.get("stop_reason_missing_from_history")
                if isinstance(after.get("stop_reason_missing_from_history"), bool)
                else None
            ),
            deactivated_on=_safe_string(after.get("deactivated_on"), allow_empty=True),
        )

    if family == "boxes":
        legacy_status = _legacy_box_status_details(metadata)
        if legacy_status is not None:
            return legacy_status

    if family == "boxes" and log.object_type == "box_inventory_initialization":
        return _compact_details(
            "box_inventory_initialization",
            box_count=_safe_number(metadata.get("box_count")),
            target_status=_safe_string(metadata.get("target_status")),
        )

    field_allowlist = {
        "boxes": _BOX_FIELDS,
        "environment": _ENVIRONMENT_FIELDS,
        "accounts": _ACCOUNT_FIELDS,
        "references": _REFERENCE_FIELDS,
    }.get(family, set())
    values = _allowlisted_values(metadata.get("valeurs"), field_allowlist)
    changes = _allowlisted_changes(metadata.get("modifications"), field_allowlist)
    if family == "boxes":
        # Legacy rows can store pre-migration status tokens. They are mapped to
        # the canonical display value so no raw English token reaches the UI.
        values = _normalized_box_status_values(values)
        changes = _normalized_box_status_changes(changes)
    return _compact_details(family.rstrip("s"), values=values, changes=changes)


def serialize_box_reference(box):
    """Return only data needed by the authorized box tracking preview."""
    if box is None:
        return None
    return {
        "id": box.id,
        "global_code": box.global_code,
        "species_scientific_name": box.strain.species.scientific_name,
    }


def serialize_audit_context(log, *, measurement=None, subculture_children=None):
    """Expose only stable relationships explicitly stored by current writers."""
    metadata = _metadata(log)
    context = {}

    if (
        measurement is not None
        and metadata.get("measurement_id") == measurement.id
        and classify_audit_log(log) == "measurements"
    ):
        context["measurement"] = {"id": measurement.id}

    if log.action == AuditLog.Action.SUBCULTURE:
        context["subculture"] = {
            "parent_global_code": log.object_id,
            "children": _normalized_subculture_children(
                metadata,
                resolved_children=subculture_children,
            ),
        }

    if log.action == AuditLog.Action.TRANSFER:
        context["transfer"] = {
            "source_organization": log.organization.name if log.organization else None,
            "destination_organization": _safe_string(metadata.get("to_organization")),
            "source_global_code": log.object_id or None,
        }
    elif log.action == AuditLog.Action.IMPORT and log.object_type == "box":
        context["transfer"] = {
            "source_organization": _safe_string(metadata.get("source_organization")),
            "destination_organization": log.organization.name if log.organization else None,
            "source_global_code": _safe_string(metadata.get("source_global_code")),
        }

    if _is_box_movement(metadata):
        context["movement"] = {
            "from_zone": _safe_string(metadata.get("from_thermal_zone_name"), allow_empty=True),
            "to_zone": _safe_string(metadata.get("to_thermal_zone_name")),
        }

    return context


def _normalized_subculture_children(metadata, *, resolved_children=None):
    stored_codes = _safe_string_list(metadata.get("child_global_codes"))
    counts = metadata.get("initial_polyp_counts")

    if resolved_children is None:
        indexed_current_codes = list(enumerate(stored_codes))
    else:
        indexed_current_codes = [
            (index, box.global_code if box is not None else stored_codes[index])
            for index, box in enumerate(resolved_children)
            if box is not None or index < len(stored_codes)
        ]

    children = []
    for index, current_code in indexed_current_codes:
        stored_code = stored_codes[index] if index < len(stored_codes) else current_code
        count = None
        if isinstance(counts, dict):
            candidate = counts.get(current_code, counts.get(stored_code))
            if _is_safe_number(candidate):
                count = candidate
        children.append(
            {
                "global_code": current_code,
                "initial_polyp_count": count,
            }
        )
    return children


def _legacy_box_status_details(metadata):
    modifications = metadata.get("modifications")
    if not isinstance(modifications, dict):
        return None
    status_change = modifications.get("statut")
    if not isinstance(status_change, dict):
        return None

    before = _normalized_legacy_box_status(status_change.get("avant"))
    after = _normalized_legacy_box_status(status_change.get("apres"))
    if before is None or after is None or before == after:
        return None

    values = metadata.get("valeurs")
    values = values if isinstance(values, dict) else {}
    stop_reason = _safe_string(values.get("raison_arret"), allow_empty=True)
    reason_change = modifications.get("raison_arret")
    if isinstance(reason_change, dict):
        stop_reason = _safe_string(reason_change.get("apres"), allow_empty=True)

    return _compact_details(
        "box_status",
        transition={"from": before, "to": after},
        stop_reason=stop_reason,
    )


def _normalized_legacy_box_status(value):
    status = _safe_string(value)
    if status in _LEGACY_BOX_STATUS_TOKENS:
        return "inactive"
    return status


def _normalized_box_status_values(values):
    """Map a confirmed legacy status token to the canonical display value."""
    if "statut" not in values:
        return values
    return {**values, "statut": _normalized_legacy_status_value(values["statut"])}


def _normalized_box_status_changes(changes):
    """Map confirmed legacy status tokens inside a stored change record."""
    change = changes.get("statut")
    if not isinstance(change, dict):
        return changes
    return {
        **changes,
        "statut": {
            side: _normalized_legacy_status_value(value)
            for side, value in change.items()
        },
    }


def _normalized_legacy_status_value(value):
    if isinstance(value, str) and value in _LEGACY_BOX_STATUS_TOKENS:
        return "inactive"
    return value


def _personal_resource_identifier(log):
    """Hide opaque technical identifiers from the personal history."""
    if log.object_type == "account":
        return _personal_resource_label(log)
    # Alerts, species and strains store a database primary key in object_id, so
    # the personal payload never exposes it. The readable summary stays.
    if log.object_type in _PRIMARY_KEY_RESOURCE_OBJECT_TYPES:
        return None
    return log.object_id


def _personal_resource_label(log):
    """Give account resources a readable target instead of an internal username."""
    if log.object_type in _PRIMARY_KEY_RESOURCE_OBJECT_TYPES:
        return None
    if log.object_type != "account":
        return log.object_id

    metadata = _metadata(log)
    values = metadata.get("valeurs")
    if not isinstance(values, dict):
        return None

    name = values.get("nom")
    if isinstance(name, str) and name.strip() and not name.strip().startswith("internal_"):
        return name.strip()

    email = values.get("email")
    if isinstance(email, str) and email.strip():
        return email.strip()
    return None


def _personal_audit_details(log):
    metadata = _metadata(log)
    allowed_fields = _PERSONAL_DETAIL_FIELDS_BY_OBJECT_TYPE.get(log.object_type, set())
    details = {}

    values = _allowlisted_values(metadata.get("valeurs"), allowed_fields)
    if values:
        details["values"] = values

    changes = _allowlisted_changes(metadata.get("modifications"), allowed_fields)
    if changes:
        details["changes"] = changes

    if log.object_type == "box":
        # Same legacy status normalization as the business details contract.
        if "values" in details:
            details["values"] = _normalized_box_status_values(details["values"])
        if "changes" in details:
            details["changes"] = _normalized_box_status_changes(details["changes"])

    return details


def _measurement_values(measurement):
    return {
        "date": measurement.measured_on.isoformat(),
        "polypes": measurement.polyp_count,
        "ephyrules": measurement.ephyrae_count,
        "strobiles": measurement.strobila_count,
        "salinite_psu": str(measurement.salinity_psu) if measurement.salinity_psu is not None else None,
        "statut_culture": measurement.culture_status,
        "a_verifier": measurement.needs_attention,
        "note": measurement.notes,
    }


def _compact_details(detail_type, **values):
    details = {"type": detail_type}
    details.update(
        {
            key: value
            for key, value in values.items()
            if value is not None and value != {} and value != []
        }
    )
    return details


def _metadata(log):
    return log.metadata if isinstance(log.metadata, dict) else {}


def _is_box_movement(metadata):
    return (
        "movement_id" in metadata
        and "to_thermal_zone_name" in metadata
        and isinstance(metadata.get("to_thermal_zone_name"), str)
    )


def _allowlisted_values(candidate, allowed_fields):
    if not isinstance(candidate, dict):
        return {}
    return {
        key: value
        for key, value in candidate.items()
        if key in allowed_fields and _is_safe_scalar(value)
    }


def _allowlisted_changes(candidate, allowed_fields):
    if not isinstance(candidate, dict):
        return {}

    changes = {}
    for key, value in candidate.items():
        if key not in allowed_fields or not isinstance(value, dict):
            continue
        before = value.get("avant")
        after = value.get("apres")
        if _is_safe_scalar(before) and _is_safe_scalar(after):
            changes[key] = {"before": before, "after": after}
    return changes


def _safe_string_list(value):
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _safe_string(value, *, allow_empty=False):
    if not isinstance(value, str):
        return None
    if value or allow_empty:
        return value
    return None


def _safe_number(value):
    return value if _is_safe_number(value) else None


def _is_safe_number(value):
    return type(value) in {int, float}


def _is_safe_scalar(value):
    return value is None or isinstance(value, (bool, int, float, str))
