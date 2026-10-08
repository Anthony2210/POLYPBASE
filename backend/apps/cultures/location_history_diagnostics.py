"""Read-only diagnostic of the persisted location history of explicit Boxes.

The diagnostic is an operator observation tool, never a repair mechanism. It
performs ORM reads only: no save, update, delete, bulk operation, row lock,
importer call or AuditLog write. Nothing is inferred from temperatures, weekly
source observations or adjacent zones.

Three kinds of evidence are kept apart in the report:

1. persisted BoxMovement rows;
2. BoxLocation period boundaries (derived from period rows, never movements);
3. correlations between them, limited to the exact rule already used by the
   zone history API: same Box, same instant and same zone.

Boxes are resolved by exact ``global_code`` inside one explicit Organization.
Related zones are resolved separately so that a zone outside that Organization
is reported as such without loading or exposing its attributes.
"""

from contextlib import contextmanager
from datetime import timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.db import connection, transaction

from apps.accounts.identity import readable_user_identity_label
from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog

from .models import Box, BoxLocation, BoxMovement, ThermalZone


SCHEMA_VERSION = 1

OPEN = "OPEN"
CLOSED = "CLOSED"
CLOSED_END_UNKNOWN = "CLOSED_END_UNKNOWN"

IN_ORGANIZATION = "IN_ORGANIZATION"
OUTSIDE_ORGANIZATION = "OUTSIDE_ORGANIZATION"
MISSING = "MISSING"
MALFORMED = "MALFORMED"
UNRESOLVED_SCOPE = "UNRESOLVED_SCOPE"

# Known end of an open period: it covers every later instant.
_OPEN_END = object()

# Metadata keys written by the lifecycle and movement services.
LOCATION_AUDIT_KEYS = (
    "movement_id",
    "closed_location_ids",
    "legacy_closed_location_ids",
    "from_thermal_zone_id",
    "to_thermal_zone_id",
)

GENERAL_UNDETERMINED_FACTS = (
    "Physical moves that were not recorded as BoxMovement rows cannot be excluded: "
    "the absence of a BoxMovement does not prove the absence of a physical move.",
    "BoxLocation rows store neither an author nor a write timestamp: who recorded "
    "each period, and when, cannot be determined from these rows.",
    "Zone name, activity and target temperature are current values; historical "
    "zone configuration is not versioned.",
    "Audit association uses this Box's current global_code within this "
    "Organization; entries recorded under another code or without an "
    "Organization are not found.",
)


@contextmanager
def _read_only_snapshot(conn=connection):
    """Add a PostgreSQL read-only snapshot when this call owns the transaction.

    The core guarantee is that the diagnostic only issues ORM reads. On
    PostgreSQL, an outermost call additionally runs every read in one
    REPEATABLE READ, READ ONLY transaction: an accidental write fails in the
    database and all sections describe the same snapshot. Other engines, and
    calls nested in an existing transaction, run the same reads without it.
    """
    if conn.vendor != "postgresql" or conn.in_atomic_block:
        yield False
        return
    with transaction.atomic(using=conn.alias):
        with conn.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        yield True


def diagnose_box_location_history(*, organization, global_codes):
    """Return a JSON-serializable report for exact global codes in one Organization."""
    requested = sorted(set(global_codes))
    with _read_only_snapshot() as database_read_only:
        boxes = list(
            Box.objects.filter(organization_id=organization.pk, global_code__in=requested)
            .order_by("global_code", "id")
            .values(
                "id", "global_code", "local_code", "box_number", "status",
                "organization_id", "thermal_zone_id", "entered_on", "created_on",
                "deactivated_on",
            )
        )
        box_ids = [box["id"] for box in boxes]
        locations = list(
            BoxLocation.objects.filter(box_id__in=box_ids)
            .order_by("box_id", "starts_at", "id")
            .values("id", "box_id", "thermal_zone_id", "starts_at", "ends_at", "end_date_unknown", "notes")
        )
        movements = list(
            BoxMovement.objects.filter(box_id__in=box_ids)
            .order_by("box_id", "moved_at", "id")
            .values("id", "box_id", "from_thermal_zone_id", "to_thermal_zone_id", "moved_at", "user_id", "notes")
        )
        audits = _location_audits(organization, boxes)
        zones = _zone_lookup(organization, boxes, locations, movements, audits)
        users = _user_lookup(organization, movements, audits)

    resolved_codes = {box["global_code"] for box in boxes}
    return {
        "schema_version": SCHEMA_VERSION,
        "read_only": True,
        "database_read_only_transaction": database_read_only,
        "organization": {
            "id": organization.pk,
            "name": organization.name,
            "is_active": organization.is_active,
        },
        "requested_global_codes": requested,
        "unresolved_global_codes": [code for code in requested if code not in resolved_codes],
        "boxes": [
            _box_report(
                box,
                organization=organization,
                locations=[row for row in locations if row["box_id"] == box["id"]],
                movements=[row for row in movements if row["box_id"] == box["id"]],
                audits=audits[box["id"]],
                zones=zones,
                users=users,
                database_read_only=database_read_only,
            )
            for box in boxes
        ],
    }


def _location_audits(organization, boxes):
    """Collect audit rows unambiguously tied to each Box, in this Organization only."""
    by_code = {box["global_code"]: box for box in boxes}
    by_id = {box["id"]: box for box in boxes}
    result = {box["id"]: {"relevant": [], "other_counts": {}, "excluded_box_id_mismatch": 0} for box in boxes}
    if not boxes:
        return result

    fields = ("id", "object_id", "action", "user_id", "created_at", "description", "metadata")
    direct = AuditLog.objects.filter(
        organization_id=organization.pk, object_type="box", object_id__in=list(by_code),
    ).order_by("created_at", "id").values(*fields)
    for audit in direct:
        box = by_code[audit["object_id"]]
        metadata = audit["metadata"] if isinstance(audit["metadata"], dict) else {}
        entry = result[box["id"]]
        if _is_identifier(metadata.get("box_id")) and metadata["box_id"] != box["id"]:
            entry["excluded_box_id_mismatch"] += 1
        elif _is_location_audit(audit["action"], metadata):
            entry["relevant"].append({
                **audit, "metadata": metadata, "association": "object_id",
                "malformed_metadata_fields": _audit_metadata_issues(audit["metadata"]),
            })
        else:
            entry["other_counts"][audit["action"]] = entry["other_counts"].get(audit["action"], 0) + 1

    # A subculture child's initial period is audited on its parent Box.
    subcultures = AuditLog.objects.filter(
        organization_id=organization.pk, object_type="box", action=AuditLog.Action.SUBCULTURE,
    ).order_by("created_at", "id").values(*fields)
    candidates = []
    for audit in subcultures:
        metadata = audit["metadata"] if isinstance(audit["metadata"], dict) else {}
        child_ids = metadata.get("child_box_ids")
        if not isinstance(child_ids, list):
            continue
        for child_id in dict.fromkeys(value for value in child_ids if _is_identifier(value)):
            if child_id in by_id:
                candidates.append((child_id, {
                    **audit, "metadata": metadata, "association": "subculture_child_box_ids",
                    "malformed_metadata_fields": _audit_metadata_issues(audit["metadata"]),
                }))
    local_parent_codes = set(Box.objects.filter(
        organization_id=organization.pk,
        global_code__in={audit["object_id"] for _, audit in candidates},
    ).values_list("global_code", flat=True))
    for child_id, audit in candidates:
        audit["parent_scope_validated"] = audit["object_id"] in local_parent_codes
        result[child_id]["relevant"].append(audit)
    for entry in result.values():
        entry["relevant"].sort(key=lambda audit: (audit["created_at"], audit["id"]))
    return result


def _is_location_audit(action, metadata):
    if action == AuditLog.Action.CREATION:
        return True
    if any(key in metadata for key in LOCATION_AUDIT_KEYS):
        return True
    return any(
        isinstance(metadata.get(side), dict) and "thermal_zone_id" in metadata[side]
        for side in ("before", "after")
    )


def _is_identifier(value):
    # Persisted primary keys are positive BigAutoField integers; JSON bool is not an ID.
    return type(value) is int and 0 < value <= 9223372036854775807


def _audit_zone_values(metadata):
    values = {
        key: metadata[key]
        for key in ("from_thermal_zone_id", "to_thermal_zone_id") if key in metadata
    }
    for side in ("before", "after"):
        if isinstance(metadata.get(side), dict) and "thermal_zone_id" in metadata[side]:
            values[f"{side}.thermal_zone_id"] = metadata[side]["thermal_zone_id"]
    return values


def _audit_zone_ids(metadata):
    return [value for value in _audit_zone_values(metadata).values() if _is_identifier(value)]


def _audit_metadata_issues(metadata):
    """Keep field paths, never arbitrary malformed JSON values, as evidence."""
    if not isinstance(metadata, dict):
        return ["metadata"]
    issues = []
    for key in ("box_id", "movement_id"):
        if key in metadata and not _is_identifier(metadata[key]):
            if key == "box_id" or metadata[key] is not None:
                issues.append(key)
    for key, value in _audit_zone_values(metadata).items():
        if value is not None and not _is_identifier(value):
            issues.append(key)
    for key in ("before", "after"):
        if key in metadata and not isinstance(metadata[key], dict):
            issues.append(key)
    for key in ("closed_location_ids", "legacy_closed_location_ids", "child_box_ids"):
        if key not in metadata:
            continue
        values = metadata[key]
        if not isinstance(values, list):
            issues.append(key)
        else:
            issues.extend(f"{key}[{index}]" for index, value in enumerate(values) if not _is_identifier(value))
    for key in ("moved_at", "occurred_at", "transition"):
        if key in metadata and not isinstance(metadata[key], str):
            issues.append(key)
    return sorted(issues)


def _audit_zone_reference(value, zone):
    if value is None:
        return None
    if not _is_identifier(value):
        return {"reference_status": MALFORMED}
    return zone(value)


def _zone_lookup(organization, boxes, locations, movements, audits):
    zone_ids = {box["thermal_zone_id"] for box in boxes}
    zone_ids.update(row["thermal_zone_id"] for row in locations)
    for row in movements:
        zone_ids.update((row["from_thermal_zone_id"], row["to_thermal_zone_id"]))
    for entry in audits.values():
        for audit in entry["relevant"]:
            zone_ids.update(_audit_zone_ids(audit["metadata"]))
    zone_ids.discard(None)

    owners = dict(ThermalZone.objects.filter(pk__in=zone_ids).values_list("id", "organization_id"))
    # Only zones of the requested Organization are read beyond their owner.
    details = {
        zone["id"]: zone
        for zone in ThermalZone.objects.filter(pk__in=zone_ids, organization_id=organization.pk)
        .values("id", "name", "is_active", "target_temperature_c")
    }
    lookup = {}
    for zone_id in zone_ids:
        if zone_id not in owners:
            lookup[zone_id] = {"reference_status": MISSING, "id": zone_id}
        elif owners[zone_id] != organization.pk:
            lookup[zone_id] = {"reference_status": OUTSIDE_ORGANIZATION}
        else:
            zone = details[zone_id]
            target = zone["target_temperature_c"]
            lookup[zone_id] = {
                "reference_status": IN_ORGANIZATION,
                "id": zone_id,
                "name": zone["name"],
                "is_active": zone["is_active"],
                "current_target_temperature_c": None if target is None else str(target),
            }
    return lookup


def _user_lookup(organization, movements, audits):
    user_ids = {row["user_id"] for row in movements}
    for entry in audits.values():
        user_ids.update(audit["user_id"] for audit in entry["relevant"])
    user_ids.discard(None)
    # Inactive/ended memberships remain evidence of an institutional relationship.
    related_ids = OrganizationMembership.objects.filter(
        organization_id=organization.pk, user_id__in=user_ids,
    ).values_list("user_id", flat=True)
    users = get_user_model().objects.filter(pk__in=related_ids).only("id", "first_name", "last_name", "email")
    return {user.pk: {"id": user.pk, "label": readable_user_identity_label(user)} for user in users}


def _actor(user_id, users):
    if user_id is None:
        return None
    # Do not distinguish a foreign live account from an unresolvable reference.
    return users.get(user_id, {"reference_status": UNRESOLVED_SCOPE})


def _timestamp(value):
    return None if value is None else value.astimezone(dt_timezone.utc).isoformat()


def _date(value):
    return None if value is None else value.isoformat()


def _period_state(row):
    if row["end_date_unknown"]:
        return CLOSED_END_UNKNOWN
    if row["ends_at"] is None:
        return OPEN
    return CLOSED


def _zone_label(ref):
    if ref is None:
        return "none"
    if ref["reference_status"] == OUTSIDE_ORGANIZATION:
        return "zone outside the requested organization (withheld)"
    if ref["reference_status"] == MISSING:
        return f"missing zone #{ref['id']}"
    return f"zone #{ref['id']} \"{ref['name']}\""


def _box_report(box, *, organization, locations, movements, audits, zones, users, database_read_only):
    zone = zones.get
    inconsistencies = []
    observations = []
    undetermined = list(GENERAL_UNDETERMINED_FACTS)
    if not database_read_only:
        undetermined.append(
            "The sections were read by separate queries without a database snapshot; "
            "a concurrent write during the run would not be detected."
        )

    def finding(target, code, detail, **refs):
        target.append({"code": code, "detail": detail, **refs})

    def check_zone(zone_id, relation, row_id):
        ref = zone(zone_id) if zone_id is not None else None
        if ref is None or ref["reference_status"] == IN_ORGANIZATION:
            return
        if ref["reference_status"] == OUTSIDE_ORGANIZATION:
            finding(
                inconsistencies, "CROSS_ORGANIZATION_RELATION",
                f"{relation} #{row_id} references a zone outside the requested organization.",
                relation=relation, row_id=row_id,
            )
        else:
            finding(
                inconsistencies, "MISSING_REFERENCED_ZONE",
                f"{relation} #{row_id} references zone #{zone_id}, which does not exist.",
                relation=relation, row_id=row_id, zone_id=zone_id,
            )

    check_zone(box["thermal_zone_id"], "Box.thermal_zone", box["id"])

    # Periods, ordered by persisted start then id; repeated stays stay separate.
    periods = []
    for row in locations:
        state = _period_state(row)
        duration = row["ends_at"] - row["starts_at"] if state == CLOSED else None
        periods.append({
            "id": row["id"],
            "thermal_zone": zone(row["thermal_zone_id"]),
            "starts_at": _timestamp(row["starts_at"]),
            "ends_at": _timestamp(row["ends_at"]),
            "end_date_unknown": row["end_date_unknown"],
            "state": state,
            "duration_seconds": None if duration is None else int(duration.total_seconds()),
            "notes": row["notes"] if zone(row["thermal_zone_id"])["reference_status"] == IN_ORGANIZATION else None,
        })
        check_zone(row["thermal_zone_id"], "BoxLocation", row["id"])
        if duration is not None and duration.total_seconds() <= 0:
            finding(
                inconsistencies, "NON_POSITIVE_DURATION",
                f"BoxLocation #{row['id']} ends at or before its start "
                f"(model validation requires the end to be after the start).",
                period_ids=[row["id"]],
            )

    open_rows = [row for row in locations if _period_state(row) == OPEN]
    if len(open_rows) > 1:
        finding(
            inconsistencies, "MULTIPLE_OPEN_PERIODS",
            f"{len(open_rows)} open BoxLocation periods: "
            + ", ".join(f"#{row['id']}" for row in open_rows) + ".",
            period_ids=[row["id"] for row in open_rows],
        )

    _check_overlaps(locations, inconsistencies, undetermined, finding)
    _check_coverage(locations, observations, undetermined, finding)
    _check_current_pointer(box, open_rows, zones, inconsistencies, finding)

    # Persisted movements and their exact correlation with period boundaries.
    movement_ids = {row["id"] for row in movements}
    period_ids = {row["id"] for row in locations}
    audit_by_movement = {}
    for audit in audits["relevant"]:
        if audit.get("parent_scope_validated") is False:
            continue
        movement_id = audit["metadata"].get("movement_id")
        if _is_identifier(movement_id):
            audit_by_movement.setdefault(movement_id, []).append(audit["id"])

    persisted_movements = []
    previous = None
    for row in movements:
        starts = [
            loc["id"] for loc in locations
            if loc["starts_at"] == row["moved_at"] and loc["thermal_zone_id"] == row["to_thermal_zone_id"]
        ]
        ends = [
            loc["id"] for loc in locations
            if row["from_thermal_zone_id"] is not None and loc["ends_at"] == row["moved_at"]
            and loc["thermal_zone_id"] == row["from_thermal_zone_id"]
        ]
        persisted_movements.append({
            "id": row["id"],
            "moved_at": _timestamp(row["moved_at"]),
            "from_thermal_zone": zone(row["from_thermal_zone_id"]) if row["from_thermal_zone_id"] is not None else None,
            "to_thermal_zone": zone(row["to_thermal_zone_id"]),
            "recorded_actor": _actor(row["user_id"], users),
            "notes": row["notes"] if all(
                zone(zone_id)["reference_status"] == IN_ORGANIZATION
                for zone_id in (row["from_thermal_zone_id"], row["to_thermal_zone_id"]) if zone_id is not None
            ) else None,
            "coinciding_period_start_ids": starts,
            "coinciding_period_end_ids": ends,
            "linked_audit_ids": audit_by_movement.get(row["id"], []),
        })
        check_zone(row["from_thermal_zone_id"], "BoxMovement.from_thermal_zone", row["id"])
        check_zone(row["to_thermal_zone_id"], "BoxMovement.to_thermal_zone", row["id"])
        if row["from_thermal_zone_id"] is not None and row["from_thermal_zone_id"] == row["to_thermal_zone_id"]:
            finding(
                inconsistencies, "MOVEMENT_SAME_ZONE",
                f"BoxMovement #{row['id']} has identical origin and destination zones "
                f"(the movement service rejects this).",
                movement_ids=[row["id"]],
            )
        if not starts:
            finding(
                observations, "MOVEMENT_WITHOUT_MATCHING_PERIOD_START",
                f"No BoxLocation of this Box starts at {_timestamp(row['moved_at'])} in the "
                f"destination zone of BoxMovement #{row['id']}; an exact correspondence is not "
                f"established, and these rows do not establish which write path produced them.",
                movement_ids=[row["id"]],
            )
        if row["from_thermal_zone_id"] is not None and not ends:
            finding(
                observations, "MOVEMENT_WITHOUT_MATCHING_PERIOD_END",
                f"No BoxLocation of this Box in the origin zone of BoxMovement #{row['id']} ends at "
                f"{_timestamp(row['moved_at'])}; an exact correspondence is not established, "
                f"and these rows do not establish which write path produced them.",
                movement_ids=[row["id"]],
            )
        if row["from_thermal_zone_id"] is None:
            undetermined.append(
                f"BoxMovement #{row['id']} records no origin zone: the Box had no current zone "
                f"at that time, or the origin zone was later deleted; these cannot be distinguished."
            )
        if row["user_id"] is None:
            undetermined.append(
                f"BoxMovement #{row['id']} records no actor: none was recorded, or the account "
                f"was later deleted; these cannot be distinguished."
            )
        if row["id"] not in audit_by_movement:
            finding(
                observations, "MOVEMENT_WITHOUT_LINKED_AUDIT",
                f"No location-related audit entry of this Box references BoxMovement #{row['id']}.",
                movement_ids=[row["id"]],
            )
        if (
            previous is not None and row["from_thermal_zone_id"] is not None
            and row["from_thermal_zone_id"] != previous["to_thermal_zone_id"]
        ):
            finding(
                observations, "MOVEMENT_CHAIN_DISCONTINUITY",
                f"BoxMovement #{row['id']} starts from a different zone than the destination of "
                f"the previous BoxMovement #{previous['id']}; no persisted movement records that "
                f"change.",
                movement_ids=[previous["id"], row["id"]],
            )
        previous = row

    boundaries = _period_boundaries(locations, movements, zones)
    unmatched_starts = [b["period_id"] for b in boundaries if b["kind"] == "START" and not b["coinciding_movement_ids"]]
    unmatched_ends = [b["period_id"] for b in boundaries if b["kind"] == "END" and not b["coinciding_movement_ids"]]
    if unmatched_starts or unmatched_ends:
        parts = []
        if unmatched_starts:
            parts.append("starts of " + ", ".join(f"#{period_id}" for period_id in unmatched_starts))
        if unmatched_ends:
            parts.append("ends of " + ", ".join(f"#{period_id}" for period_id in unmatched_ends))
        undetermined.append(
            "Period boundaries without a coinciding persisted BoxMovement (" + "; ".join(parts)
            + "): whether they correspond to physical moves, or to creation, import, subculture "
            "or lifecycle changes, cannot be determined from these rows."
        )

    # Audit text can describe a foreign zone through a linked local row even when
    # the audit does not repeat its zone ID in metadata.
    unscoped_movement_ids = {
        row["id"] for row in movements
        if any(
            zone(zone_id)["reference_status"] != IN_ORGANIZATION
            for zone_id in (row["from_thermal_zone_id"], row["to_thermal_zone_id"]) if zone_id is not None
        )
    }
    unscoped_period_ids = {
        row["id"] for row in locations
        if zone(row["thermal_zone_id"])["reference_status"] != IN_ORGANIZATION
    }
    audit_evidence = []
    for audit in audits["relevant"]:
        metadata = audit["metadata"]
        referenced_zones = [zone(zone_id) for zone_id in _audit_zone_ids(metadata)]
        unresolved_zone = any(ref["reference_status"] != IN_ORGANIZATION for ref in referenced_zones)
        malformed = audit["malformed_metadata_fields"]
        parent_unresolved = audit.get("parent_scope_validated") is False
        unlinked_reference = (
            _is_identifier(metadata.get("movement_id"))
            and (
                metadata["movement_id"] not in movement_ids
                or metadata["movement_id"] in unscoped_movement_ids
            )
        ) or any(
            _is_identifier(value) and (value not in period_ids or value in unscoped_period_ids)
            for key in ("closed_location_ids", "legacy_closed_location_ids")
            for value in (metadata.get(key) if isinstance(metadata.get(key), list) else [])
        )
        withheld = unresolved_zone or bool(malformed) or parent_unresolved or unlinked_reference
        evidence = {
            "id": audit["id"],
            "created_at": _timestamp(audit["created_at"]),
            "action": audit["action"],
            "association": audit["association"],
            "recorded_actor": _actor(audit["user_id"], users),
            "description": None if withheld else audit["description"],
            "description_withheld": withheld,
            "malformed_metadata_fields": malformed,
        }
        if malformed:
            finding(
                observations, "MALFORMED_AUDIT_METADATA",
                f"AuditLog #{audit['id']} contains malformed metadata at: {', '.join(malformed)}.",
                audit_ids=[audit["id"]], fields=malformed,
            )
        if audit["association"] == "subculture_child_box_ids":
            evidence["parent_scope_validated"] = audit["parent_scope_validated"]
            if parent_unresolved:
                finding(
                    observations, "SUBCULTURE_PARENT_SCOPE_UNRESOLVED",
                    f"AuditLog #{audit['id']} is candidate subculture evidence for this Box, "
                    f"but its parent scope could not be validated; parent details are withheld.",
                    audit_ids=[audit["id"]],
                )
            elif not withheld and isinstance(metadata.get("occurred_at"), str):
                evidence["occurred_at"] = metadata["occurred_at"]
        else:
            _audit_location_details(audit, evidence, zone, movement_ids, period_ids, inconsistencies, finding)
        audit_evidence.append(evidence)

    return {
        "current_state": {
            "id": box["id"],
            "global_code": box["global_code"],
            "local_code": box["local_code"],
            "box_number": box["box_number"],
            "status": box["status"],
            "organization": {"id": organization.pk, "name": organization.name},
            "thermal_zone": zone(box["thermal_zone_id"]) if box["thermal_zone_id"] is not None else None,
            "entered_on": _date(box["entered_on"]),
            "created_on": _date(box["created_on"]),
            "deactivated_on": _date(box["deactivated_on"]),
            "open_period_ids": [row["id"] for row in open_rows],
        },
        "location_periods": periods,
        "period_boundaries": boundaries,
        "persisted_movements": persisted_movements,
        "audit_evidence": audit_evidence,
        "other_audit_counts": dict(sorted(audits["other_counts"].items())),
        "audits_excluded_box_id_mismatch": audits["excluded_box_id_mismatch"],
        "inconsistencies": inconsistencies,
        "observations": observations,
        "undetermined": undetermined,
    }


def _check_overlaps(locations, inconsistencies, undetermined, finding):
    """Report overlaps that are established; unknown ends stay undetermined."""
    undetermined_pairs = {}
    for index, first in enumerate(locations):
        for second in locations[index + 1:]:
            # ``locations`` is ordered by start, so ``first`` never starts later.
            first_state, second_state = _period_state(first), _period_state(second)
            if any(
                state == CLOSED and row["ends_at"] <= row["starts_at"]
                for state, row in ((first_state, first), (second_state, second))
            ):
                continue
            if first["starts_at"] == second["starts_at"]:
                overlap = True
            elif first_state == CLOSED_END_UNKNOWN:
                undetermined_pairs.setdefault(first["id"], []).append(second["id"])
                continue
            else:
                overlap = first_state == OPEN or second["starts_at"] < first["ends_at"]
            if overlap:
                finding(
                    inconsistencies, "OVERLAPPING_PERIODS",
                    f"BoxLocation #{first['id']} and #{second['id']} cover a common instant "
                    f"starting at {_timestamp(second['starts_at'])}.",
                    period_ids=[first["id"], second["id"]],
                )
    for period_id, later_ids in undetermined_pairs.items():
        undetermined.append(
            f"BoxLocation #{period_id} has an unknown end: its actual end, and whether it "
            f"overlapped later periods (" + ", ".join(f"#{later}" for later in later_ids)
            + "), cannot be determined."
        )
    for row in locations:
        if _period_state(row) == CLOSED_END_UNKNOWN and row["id"] not in undetermined_pairs:
            undetermined.append(f"BoxLocation #{row['id']} has an unknown end; its actual end cannot be determined.")


def _check_coverage(locations, observations, undetermined, finding):
    """Report intervals not covered by any period's known extent, without filling them."""
    frontier = None
    frontier_period_id = None
    unknown_end_ids = []
    for row in locations:
        if frontier is not None and frontier is not _OPEN_END and row["starts_at"] > frontier:
            detail = (
                f"No persisted period with a known extent covers {_timestamp(frontier)} to "
                f"{_timestamp(row['starts_at'])} (between BoxLocation #{frontier_period_id} and "
                f"#{row['id']}); this is a gap in the recorded history, not a stay."
            )
            if unknown_end_ids:
                detail += " Earlier periods with an unknown end: " + ", ".join(
                    f"#{period_id}" for period_id in unknown_end_ids
                ) + "."
            finding(
                observations, "GAP_IN_KNOWN_COVERAGE", detail,
                period_ids=[frontier_period_id, row["id"]],
                gap_seconds=int((row["starts_at"] - frontier).total_seconds()),
            )
            undetermined.append(
                f"The Box location from {_timestamp(frontier)} to {_timestamp(row['starts_at'])} "
                f"is not recorded."
            )
        state = _period_state(row)
        if state == OPEN:
            known_end = _OPEN_END
        elif state == CLOSED:
            known_end = row["ends_at"]
        else:
            # Only the start instant of an unknown-end period is known to be covered.
            known_end = row["starts_at"]
            unknown_end_ids.append(row["id"])
        if frontier is None or known_end is _OPEN_END or (
            frontier is not _OPEN_END and known_end > frontier
        ):
            frontier = known_end
            frontier_period_id = row["id"]



def _check_current_pointer(box, open_rows, zones, inconsistencies, finding):
    pointer = box["thermal_zone_id"]
    open_zone_ids = [row["thermal_zone_id"] for row in open_rows]
    pointer_label = _zone_label(zones.get(pointer)) if pointer is not None else "none"
    detail = None
    if pointer is None and open_rows:
        detail = "Box.thermal_zone is empty but open periods exist: " + ", ".join(
            f"#{row['id']} ({_zone_label(zones.get(row['thermal_zone_id']))})" for row in open_rows
        ) + "."
    elif pointer is not None and not open_rows:
        detail = f"Box.thermal_zone points to {pointer_label} but no period is open."
    elif pointer is not None and pointer not in open_zone_ids:
        detail = f"Box.thermal_zone points to {pointer_label}, which differs from every open period: " + ", ".join(
            f"#{row['id']} ({_zone_label(zones.get(row['thermal_zone_id']))})" for row in open_rows
        ) + "."
    if detail is not None:
        finding(
            inconsistencies, "CURRENT_POINTER_MISMATCH", detail,
            period_ids=[row["id"] for row in open_rows],
        )
    if box["status"] == Box.Status.INACTIVE and (pointer is not None or open_rows):
        finding(
            inconsistencies, "INACTIVE_WITH_CURRENT_LOCATION",
            "The Box is inactive but still has a current zone pointer or an open period "
            "(deactivation closes current periods and releases the pointer).",
            period_ids=[row["id"] for row in open_rows],
        )


def _period_boundaries(locations, movements, zones):
    """Derive period start/end instants; a boundary is never itself a movement."""
    boundaries = []
    for row in locations:
        boundaries.append({
            "at": row["starts_at"],
            "kind": "START",
            "period_id": row["id"],
            "thermal_zone": zones.get(row["thermal_zone_id"]),
            "coinciding_movement_ids": [
                movement["id"] for movement in movements
                if movement["moved_at"] == row["starts_at"]
                and movement["to_thermal_zone_id"] == row["thermal_zone_id"]
            ],
        })
        if row["ends_at"] is not None:
            boundaries.append({
                "at": row["ends_at"],
                "kind": "END",
                "period_id": row["id"],
                "thermal_zone": zones.get(row["thermal_zone_id"]),
                "coinciding_movement_ids": [
                    movement["id"] for movement in movements
                    if movement["moved_at"] == row["ends_at"]
                    and movement["from_thermal_zone_id"] == row["thermal_zone_id"]
                ],
            })
    # At one instant, a period end reads before the next period start.
    boundaries.sort(key=lambda item: (item["at"], item["kind"] == "START", item["period_id"]))
    for item in boundaries:
        item["at"] = _timestamp(item["at"])
    return boundaries


def _audit_location_details(audit, evidence, zone, movement_ids, period_ids, inconsistencies, finding):
    metadata = audit["metadata"]
    movement_id = metadata.get("movement_id")
    if _is_identifier(movement_id):
        evidence["movement_id"] = movement_id
        evidence["movement_is_persisted_for_box"] = movement_id in movement_ids
        if movement_id not in movement_ids:
            finding(
                inconsistencies, "AUDITED_MOVEMENT_NOT_FOUND",
                f"AuditLog #{audit['id']} references BoxMovement #{movement_id}, which is not a "
                f"persisted movement of this Box.",
                audit_ids=[audit["id"]],
            )
    for key in ("closed_location_ids", "legacy_closed_location_ids"):
        ids = metadata.get(key)
        if not isinstance(ids, list):
            continue
        evidence[key] = [value for value in ids if _is_identifier(value)]
        missing = [location_id for location_id in evidence[key] if location_id not in period_ids]
        if missing:
            finding(
                inconsistencies, "AUDITED_LOCATION_NOT_FOUND",
                f"AuditLog #{audit['id']} lists BoxLocation "
                + ", ".join(f"#{location_id}" for location_id in missing)
                + " in " + key + ", not persisted for this Box.",
                audit_ids=[audit["id"]],
            )
    for key in ("from_thermal_zone_id", "to_thermal_zone_id"):
        if key in metadata:
            value = metadata[key]
            evidence[key.removesuffix("_id")] = _audit_zone_reference(value, zone)
    if not evidence["description_withheld"]:
        if isinstance(metadata.get("moved_at"), str):
            evidence["recorded_moved_at"] = metadata["moved_at"]
        if isinstance(metadata.get("transition"), str):
            evidence["transition"] = metadata["transition"]
    for side in ("before", "after"):
        values = metadata.get(side)
        if isinstance(values, dict) and "thermal_zone_id" in values:
            value = values["thermal_zone_id"]
            evidence[f"{side}_thermal_zone"] = _audit_zone_reference(value, zone)
