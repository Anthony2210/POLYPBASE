"""Read-only diagnostic of weekly movement uniqueness and movement provenance.

Organization-wide companion of ``location_history_diagnostics``. It performs
ORM reads only, inside a PostgreSQL READ ONLY snapshot when it owns the
transaction, and never classifies, links, merges or deletes anything. It
reports facts that an operator must review before any manual decision:

- Paris ISO weeks holding more than one ordinary-movement candidate (ordinary
  rows, plus unclassified rows whose provenance is unknown);
- unclassified movements, whose provenance cannot be established;
- movement/period mismatches, for linked rows and for exact-instant matches;
- weekly keys that differ from the value derived from ``moved_at``;
- several open periods for one box;
- current zone pointers inconsistent with open periods or status;
- relations to zones of another organization (identifiers only).
"""

from collections import defaultdict

from .location_history_diagnostics import _read_only_snapshot
from .models import Box, BoxLocation, BoxMovement, ThermalZone

SCHEMA_VERSION = 1

WEEK_CANDIDATE_KINDS = (BoxMovement.Kind.ORDINARY, BoxMovement.Kind.UNCLASSIFIED)


def _timestamp(value):
    return value.isoformat() if value is not None else None


def _is_open(row):
    return row["ends_at"] is None and not row["end_date_unknown"]


def diagnose_box_movement_weeks(*, organization):
    """Return a JSON-serializable report for every box of one Organization."""
    with _read_only_snapshot() as database_read_only:
        boxes = list(
            Box.objects.filter(organization_id=organization.pk)
            .order_by("global_code", "id")
            .values("id", "global_code", "status", "thermal_zone_id")
        )
        box_ids = [box["id"] for box in boxes]
        locations = list(
            BoxLocation.objects.filter(box_id__in=box_ids)
            .order_by("box_id", "starts_at", "id")
            .values("id", "box_id", "thermal_zone_id", "starts_at", "ends_at", "end_date_unknown")
        )
        movements = list(
            BoxMovement.objects.filter(box_id__in=box_ids)
            .order_by("box_id", "moved_at", "id")
            .values(
                "id", "box_id", "kind", "moved_at", "iso_week_start", "from_thermal_zone_id",
                "to_thermal_zone_id", "origin_location_id", "destination_location_id",
            )
        )
        zone_ids = (
            {box["thermal_zone_id"] for box in boxes}
            | {row["thermal_zone_id"] for row in locations}
            | {row["from_thermal_zone_id"] for row in movements}
            | {row["to_thermal_zone_id"] for row in movements}
        ) - {None}
        zone_organizations = dict(
            ThermalZone.objects.filter(pk__in=zone_ids).values_list("id", "organization_id")
        )

    locations_by_box = defaultdict(list)
    for row in locations:
        locations_by_box[row["box_id"]].append(row)
    locations_by_id = {row["id"]: row for row in locations}
    movements_by_box = defaultdict(list)
    for row in movements:
        movements_by_box[row["box_id"]].append(row)

    findings = defaultdict(list)
    kind_counts = defaultdict(int)
    for row in movements:
        kind_counts[row["kind"]] += 1

    def zone_issue(zone_id):
        if zone_id is None:
            return None
        if zone_id not in zone_organizations:
            return "missing"
        if zone_organizations[zone_id] != organization.pk:
            return "outside_organization"
        return None

    for box in boxes:
        box_locations = locations_by_box[box["id"]]
        box_movements = movements_by_box[box["id"]]
        base = {"box_id": box["id"], "global_code": box["global_code"]}

        # Weekly candidates, by Paris ISO week derived from moved_at.
        weeks = defaultdict(list)
        for row in box_movements:
            if row["kind"] in WEEK_CANDIDATE_KINDS:
                weeks[BoxMovement.week_start_for(row["moved_at"])].append(row)
        for week_start, rows in sorted(weeks.items()):
            if len(rows) > 1:
                iso_year, iso_week, _ = week_start.isocalendar()
                findings["weekly_candidates"].append({
                    **base,
                    "iso_week_start": week_start.isoformat(),
                    "iso_year": iso_year,
                    "iso_week": iso_week,
                    "movements": [
                        {"id": row["id"], "kind": row["kind"], "moved_at": _timestamp(row["moved_at"])}
                        for row in rows
                    ],
                    "ordinary_count": sum(row["kind"] == BoxMovement.Kind.ORDINARY for row in rows),
                })

        for row in box_movements:
            ref = {**base, "movement_id": row["id"], "kind": row["kind"]}
            if row["kind"] == BoxMovement.Kind.UNCLASSIFIED:
                findings["unclassified_movements"].append({**ref, "moved_at": _timestamp(row["moved_at"])})

            expected_week = (
                BoxMovement.week_start_for(row["moved_at"])
                if row["kind"] == BoxMovement.Kind.ORDINARY else None
            )
            if row["iso_week_start"] != expected_week:
                findings["week_key_mismatches"].append({
                    **ref,
                    "stored": row["iso_week_start"].isoformat() if row["iso_week_start"] else None,
                    "derived": expected_week.isoformat() if expected_week else None,
                })

            for side in ("origin_location_id", "destination_location_id"):
                location_id = row[side]
                if location_id is None:
                    continue
                location = locations_by_id.get(location_id)
                if location is None or location["box_id"] != row["box_id"]:
                    findings["movement_period_mismatches"].append(
                        {**ref, "link": side, "issue": "linked_period_of_another_box", "location_id": location_id}
                    )
                    continue
                if side == "destination_location_id":
                    matches = (
                        location["starts_at"] == row["moved_at"]
                        and location["thermal_zone_id"] == row["to_thermal_zone_id"]
                    )
                else:
                    matches = (
                        location["ends_at"] == row["moved_at"]
                        and not location["end_date_unknown"]
                        and location["thermal_zone_id"] == row["from_thermal_zone_id"]
                    )
                if not matches:
                    findings["movement_period_mismatches"].append(
                        {**ref, "link": side, "issue": "linked_period_differs", "location_id": location_id}
                    )

            if row["destination_location_id"] is None:
                # Exact rule of the zone history API: same box, instant and zone.
                starts = [
                    loc["id"] for loc in box_locations
                    if loc["starts_at"] == row["moved_at"] and loc["thermal_zone_id"] == row["to_thermal_zone_id"]
                ]
                ends = [
                    loc["id"] for loc in box_locations
                    if row["from_thermal_zone_id"] is not None
                    and loc["ends_at"] == row["moved_at"] and loc["thermal_zone_id"] == row["from_thermal_zone_id"]
                ]
                if len(starts) != 1 or (row["from_thermal_zone_id"] is not None and len(ends) != 1):
                    findings["movement_period_mismatches"].append({
                        **ref,
                        "link": None,
                        "issue": "unlinked_without_unique_exact_match",
                        "matching_period_start_ids": starts,
                        "matching_period_end_ids": ends,
                    })

            for side in ("from_thermal_zone_id", "to_thermal_zone_id"):
                issue = zone_issue(row[side])
                if issue:
                    findings["foreign_zone_relations"].append(
                        {**ref, "relation": f"BoxMovement.{side}", "zone_id": row[side], "issue": issue}
                    )

        open_rows = [row for row in box_locations if _is_open(row)]
        if len(open_rows) > 1:
            findings["multiple_open_periods"].append({**base, "location_ids": [row["id"] for row in open_rows]})

        pointer = box["thermal_zone_id"]
        open_zone_ids = [row["thermal_zone_id"] for row in open_rows]
        pointer_issue = None
        if pointer is None and open_rows:
            pointer_issue = "no_pointer_with_open_period"
        elif pointer is not None and not open_rows:
            pointer_issue = "pointer_without_open_period"
        elif pointer is not None and pointer not in open_zone_ids:
            pointer_issue = "pointer_differs_from_open_periods"
        elif box["status"] == Box.Status.INACTIVE and pointer is not None:
            pointer_issue = "inactive_with_pointer"
        if pointer_issue:
            findings["current_pointer_inconsistencies"].append({
                **base, "status": box["status"], "issue": pointer_issue,
                "thermal_zone_id": pointer if zone_issue(pointer) is None else None,
                "open_location_ids": [row["id"] for row in open_rows],
            })

        issue = zone_issue(pointer)
        if issue:
            findings["foreign_zone_relations"].append(
                {**base, "relation": "Box.thermal_zone", "zone_id": pointer, "issue": issue}
            )
        for row in box_locations:
            issue = zone_issue(row["thermal_zone_id"])
            if issue:
                findings["foreign_zone_relations"].append({
                    **base, "relation": "BoxLocation.thermal_zone", "location_id": row["id"],
                    "zone_id": row["thermal_zone_id"], "issue": issue,
                })

    sections = (
        "weekly_candidates",
        "unclassified_movements",
        "movement_period_mismatches",
        "week_key_mismatches",
        "multiple_open_periods",
        "current_pointer_inconsistencies",
        "foreign_zone_relations",
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "read_only": True,
        "database_read_only_transaction": database_read_only,
        "week_time_zone": "Europe/Paris",
        "organization": {"id": organization.pk, "name": organization.name},
        "box_count": len(boxes),
        "movement_count": len(movements),
        "movement_kind_counts": dict(sorted(kind_counts.items())),
        "summary": {section: len(findings[section]) for section in sections},
        **{section: findings[section] for section in sections},
    }
