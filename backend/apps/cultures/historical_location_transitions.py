"""Read-only projection of historical location changes for one institution.

Aquarium de Paris confirmed that, before 2026-10-05, the temperature of its
historical tracking identifies the location environment of a Box. A change
between successive reliable observations therefore establishes a location
change. The rule is institution-specific and never applies to live data.

An observation pair bounds the change in time: no occurrence instant is derived.
Nothing here writes: movements, periods, receipts and audits stay untouched.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from django.db.models import Count, Q

from apps.measurements.historical_temperature_identity import temperature_source_matches
from apps.measurements.historical_temperature_source import SOURCES, canonical_box
from apps.measurements.models import HistoricalTemperatureEvidence, HistoricalTemperatureReading
from apps.measurements.temperature_series import temperature_decimal

from .historical_movements import fingerprint
from .models import MOVEMENT_WEEK_TIME_ZONE, Box, BoxLocation, BoxMovement, HistoricalBoxMovement, ThermalZone

RULE_ORGANIZATION_ID = 1
HISTORICAL_CUTOFF = date(2026, 10, 5)
POLICY = "aquarium_paris_weekly_environment_change_v1"
PROVENANCE = "historical_tracking_environment_observations"

ESTABLISHED = "established"
ENVIRONMENT_ONLY = "environment_only"
ALREADY_REPRESENTED = "already_represented"
REVIEW = "review"

_EVIDENCE_FIELDS = (
    "source_file", "source_sha256", "sheet", "temperature_cell", "temperature_merged_range", "source_value",
    "source_box", "polyp_cell", "polyp_value", "ephyrae_cell", "ephyrae_value", "week_cell", "week_value",
    "source_iso_year", "source_iso_week",
)


def rule_applies(organization_id):
    return organization_id == RULE_ORGANIZATION_ID


def _week_monday(year, week):
    try:
        return date.fromisocalendar(year, week, 1)
    except (TypeError, ValueError):
        return None


def _temperature(value):
    try:
        return temperature_decimal(value)
    except ValueError:
        return None


def _monday_instant(day):
    return datetime.combine(day, time.min, tzinfo=MOVEMENT_WEEK_TIME_ZONE)


def _observation_blockers(reading, evidences, global_code):
    blockers = set()
    if reading["association_status"] != HistoricalTemperatureReading.AssociationStatus.CONFIRMED:
        blockers.add("observation_association_not_confirmed")
    if reading["biological_measurement__duplicate_of_id"] is not None:
        blockers.add("observation_measurement_not_canonical")
    if reading["biological_measurement__environment_source_box_id"] not in (None, reading["biological_measurement__box_id"]):
        blockers.add("observation_environment_from_other_box")
    monday = _week_monday(reading["iso_year"], reading["iso_week"])
    measured_on = reading["biological_measurement__measured_on"]
    if (monday is None or reading["biological_measurement__week_start"] != monday
            or measured_on - timedelta(days=measured_on.weekday()) != monday):
        blockers.add("observation_week_inconsistent")
    temperature = _temperature(reading["temperature_c"])
    if temperature is None:
        blockers.add("observation_temperature_invalid")
    if not evidences:
        blockers.add("source_box_identity_unavailable")
    for evidence in evidences:
        known = SOURCES.get(evidence["source_file"])
        if known is None or known[1] != evidence["source_sha256"]:
            blockers.add("source_not_validated")
        source_code = canonical_box(evidence["source_box"])
        if source_code is None or not temperature_source_matches(source_code, global_code):
            blockers.add("source_box_identity_mismatch")
        if temperature is None or _temperature(evidence["source_value"]) != temperature:
            blockers.add("observation_source_value_conflict")
        if (evidence["source_iso_year"], evidence["source_iso_week"]) != (reading["iso_year"], reading["iso_week"]):
            blockers.add("observation_week_inconsistent")
    return blockers


def _before_cutoff(reading):
    monday = _week_monday(reading["iso_year"], reading["iso_week"])
    return (monday is not None and monday < HISTORICAL_CUTOFF
            and reading["biological_measurement__week_start"] < HISTORICAL_CUTOFF
            and reading["biological_measurement__measured_on"] < HISTORICAL_CUTOFF)


def _observations(readings, evidences_by_reading, global_code):
    observations = []
    for reading in readings:
        evidences = evidences_by_reading.get(reading["id"], [])
        observations.append({
            "reading": reading, "evidences": evidences, "temperature": _temperature(reading["temperature_c"]),
            "monday": _week_monday(reading["iso_year"], reading["iso_week"]),
            "blockers": _observation_blockers(reading, evidences, global_code),
        })
    observations.sort(key=lambda item: (item["monday"], item["reading"]["id"]))
    by_week = defaultdict(list)
    for observation in observations:
        by_week[observation["monday"]].append(observation)
    for same_week in by_week.values():
        if len({item["temperature"] for item in same_week}) > 1:
            for item in same_week:
                item["blockers"].add("observations_conflict_in_week")
    return observations


def _observation_pairs(observations):
    """Yield successive comparable observations whose environment differs."""
    previous, skipped = None, set()
    for observation in observations:
        if observation["temperature"] is None:
            skipped |= observation["blockers"]
            continue
        if previous is not None and previous["temperature"] != observation["temperature"]:
            yield previous, observation, skipped
        previous, skipped = observation, set()


def _observation_payload(observation):
    reading = observation["reading"]
    return {
        "reading_id": reading["id"], "biological_measurement_id": reading["biological_measurement_id"],
        "iso_year": reading["iso_year"], "iso_week": reading["iso_week"], "week_start": observation["monday"].isoformat(),
        "environment_temperature_c": reading["temperature_c"], "association_status": reading["association_status"],
        "sources": [{field: evidence[field] for field in _EVIDENCE_FIELDS} for evidence in observation["evidences"]],
    }


def _zone_for(temperature, zones_by_temperature):
    """Resolve a setpoint only when exactly one institutional location has it."""
    matches = zones_by_temperature.get(temperature, [])
    if len(matches) == 1:
        return matches[0], None
    return None, "location_mapping_ambiguous" if matches else "location_mapping_unavailable"


def _classify(*, origin_zone, destination_zone, start, end, movements, receipts, periods):
    """Return (blockers, representation) for one transition between two known zones."""
    inside = [movement for movement in movements if start <= movement["moved_at"] < end]
    origin_day, last_day = start.date(), end.date() - timedelta(days=1)
    nearby = [receipt for receipt in receipts
              if (monday := _week_monday(receipt["iso_year"], receipt["iso_week"])) is None
              or origin_day <= monday <= last_day]
    pair = (origin_zone["id"], destination_zone["id"])
    moves = [row for row in inside if (row["from_thermal_zone_id"], row["to_thermal_zone_id"]) == pair]
    accepted = [row for row in nearby if (row["from_thermal_zone_id"], row["to_thermal_zone_id"]) == pair]
    if len(moves) + len(accepted) > 1:
        return ["multiple_recorded_candidates"], None
    if len(inside) != len(moves) or len(nearby) != len(accepted):
        return ["conflicting_recorded_movement"], None
    if moves:
        return [], {"kind": "box_movement", "event_id": f"movement:{moves[0]['id']}", "movement_id": moves[0]["id"]}
    if accepted:
        return [], {"kind": "historical_box_movement", "event_id": f"historical:{accepted[0]['id']}",
                    "historical_movement_id": accepted[0]["id"]}
    touching = [row for row in periods if row["starts_at"] < end and (row["ends_at"] is None or row["ends_at"] > start)]
    if not touching:
        return [], None
    links = [(left, right) for left in touching for right in touching
             if left["thermal_zone_id"] == pair[0] and right["thermal_zone_id"] == pair[1]
             and not left["end_date_unknown"] and left["ends_at"] == right["starts_at"] and start <= right["starts_at"] < end]
    if len(links) != 1 or len(touching) != 2:
        return ["location_period_conflict"], None
    return [], {"kind": "location_periods", "origin_location_id": links[0][0]["id"],
                "destination_location_id": links[0][1]["id"]}


def _transition(*, box, origin, destination, skipped, zones_by_temperature, movements, receipts, periods):
    start = _monday_instant(origin["monday"])
    end = _monday_instant(destination["monday"] + timedelta(days=7))
    origin_zone, origin_gap = _zone_for(origin["temperature"], zones_by_temperature)
    destination_zone, destination_gap = _zone_for(destination["temperature"], zones_by_temperature)
    blockers = set(origin["blockers"]) | set(destination["blockers"]) | skipped
    mapping_gaps = {gap for gap in (origin_gap, destination_gap) if gap}
    representation = None
    if blockers:
        status = REVIEW
    elif mapping_gaps:
        # Without proven locations a nearby record cannot be compared with this change.
        unsettled = (any(start <= row["moved_at"] < end for row in movements)
                     or any(row["starts_at"] < end and (row["ends_at"] is None or row["ends_at"] > start) for row in periods))
        status = REVIEW if unsettled else ENVIRONMENT_ONLY
        if unsettled:
            blockers.add("recorded_location_data_not_comparable")
    else:
        found, representation = _classify(origin_zone=origin_zone, destination_zone=destination_zone, start=start, end=end,
                                          movements=movements, receipts=receipts, periods=periods)
        blockers.update(found)
        status = REVIEW if found else ALREADY_REPRESENTED if representation and representation["kind"] != "location_periods" else ESTABLISHED
    blockers |= mapping_gaps
    resolved = origin_zone is not None and destination_zone is not None
    return {
        "id": "historical-transition:" + fingerprint([POLICY, box["id"], origin["reading"]["id"], destination["reading"]["id"]])[:32],
        "policy": POLICY, "provenance": PROVENANCE, "status": status, "box_id": box["id"], "box_code": box["global_code"],
        "box_status": box["status"],
        "source_box_labels": sorted({row["source_box"] for row in origin["evidences"] + destination["evidences"]}),
        "from_environment_c": origin["reading"]["temperature_c"], "to_environment_c": destination["reading"]["temperature_c"],
        "from_thermal_zone": origin_zone if resolved else None, "to_thermal_zone": destination_zone if resolved else None,
        "origin_observation": _observation_payload(origin), "destination_observation": _observation_payload(destination),
        "interval": {"starts_on": origin["monday"].isoformat(), "ends_before": (destination["monday"] + timedelta(days=7)).isoformat(),
                     "observation_week_gap": (destination["monday"] - origin["monday"]).days // 7},
        "physical_occurrence_known": False, "representation": representation, "blockers": sorted(blockers),
        # Stored periods are already listed by location; only unrepresented changes add a side.
        "adds_location_history": status == ESTABLISHED and representation is None,
    }


def project_historical_transitions(*, organization_id, box_ids=None):
    """Return every observed change for the institution, or nothing outside its rule."""
    if not rule_applies(organization_id):
        return []
    scope = {"box__organization_id": organization_id, "box__merged_into__isnull": True}
    boxes = Box.objects.filter(organization_id=organization_id, merged_into__isnull=True)
    if box_ids is not None:
        boxes = boxes.filter(pk__in=box_ids)
        scope["box_id__in"] = list(box_ids)
    measurement_scope = {f"biological_measurement__{key}": value for key, value in scope.items()}
    readings_by_box = defaultdict(list)
    for reading in HistoricalTemperatureReading.objects.filter(**measurement_scope).values(
        "id", "biological_measurement_id", "association_status", "iso_year", "iso_week", "temperature_c",
        "biological_measurement__box_id", "biological_measurement__week_start", "biological_measurement__measured_on",
        "biological_measurement__duplicate_of_id", "biological_measurement__environment_source_box_id",
    ):
        if _before_cutoff(reading):
            readings_by_box[reading["biological_measurement__box_id"]].append(reading)
    if not readings_by_box:
        return []
    evidences_by_reading = defaultdict(list)
    for evidence in HistoricalTemperatureEvidence.objects.filter(
        **{f"reading__{key}": value for key, value in measurement_scope.items()},
    ).order_by("pk").values("reading_id", *_EVIDENCE_FIELDS):
        evidences_by_reading[evidence["reading_id"]].append(evidence)
    zones_by_temperature = defaultdict(list)
    for zone in ThermalZone.objects.filter(organization_id=organization_id, target_temperature_c__isnull=False).order_by("pk"):
        zones_by_temperature[zone.target_temperature_c].append({"id": zone.pk, "name": zone.name})
    grouped = {name: defaultdict(list) for name in ("movements", "receipts", "periods")}
    for row in BoxMovement.objects.filter(**scope).values("id", "box_id", "from_thermal_zone_id", "to_thermal_zone_id", "moved_at"):
        grouped["movements"][row["box_id"]].append(row)
    for row in HistoricalBoxMovement.objects.filter(**scope).values(
        "id", "box_id", "from_thermal_zone_id", "to_thermal_zone_id", "iso_year", "iso_week",
    ):
        grouped["receipts"][row["box_id"]].append(row)
    for row in BoxLocation.objects.filter(**scope).values("id", "box_id", "thermal_zone_id", "starts_at", "ends_at", "end_date_unknown"):
        grouped["periods"][row["box_id"]].append(row)
    transitions = []
    for box in boxes.order_by("pk").values("id", "global_code", "status"):
        observations = _observations(readings_by_box.get(box["id"], []), evidences_by_reading, box["global_code"])
        for origin, destination, skipped in _observation_pairs(observations):
            transitions.append(_transition(
                box=box, origin=origin, destination=destination, skipped=skipped,
                zones_by_temperature=zones_by_temperature, movements=grouped["movements"][box["id"]],
                receipts=grouped["receipts"][box["id"]], periods=grouped["periods"][box["id"]],
            ))
    return transitions


def _zone_candidate_box_ids(zone):
    """Return Boxes whose sequence can hold a change into or out of this setpoint.

    A side of this zone needs a pre-cutoff observation equal to the setpoint and
    another one that differs. Every association status is kept: eligibility stays
    with the full projection. Stored temperatures are exact text, so equivalent
    lexemes ("15", "15.0", "1.5e1") are resolved from the distinct stored values.
    """
    target = zone.target_temperature_c
    if target is None or ThermalZone.objects.filter(
        organization_id=zone.organization_id, target_temperature_c=target,
    ).count() != 1:
        return []
    # These predicates are implied by _before_cutoff; the projection rechecks them.
    readings = HistoricalTemperatureReading.objects.filter(
        biological_measurement__box__organization_id=zone.organization_id,
        biological_measurement__box__merged_into__isnull=True,
        biological_measurement__week_start__lt=HISTORICAL_CUTOFF,
        biological_measurement__measured_on__lt=HISTORICAL_CUTOFF,
    ).order_by()
    equivalent = [value for value in readings.values_list("temperature_c", flat=True).distinct()
                  if _temperature(value) == target]
    if not equivalent:
        return []
    own = Q(temperature_c__in=equivalent)
    return list(readings.values("biological_measurement__box_id").annotate(
        own_count=Count("pk", filter=own), other_count=Count("pk", filter=~own),
    ).filter(own_count__gt=0, other_count__gt=0).values_list("biological_measurement__box_id", flat=True))


def zone_history_rows(*, zone, direction=None):
    """Return each location's side of the changes that add to its history."""
    if not rule_applies(zone.organization_id):
        return []
    box_ids = _zone_candidate_box_ids(zone)
    if not box_ids:
        return []
    return zone_sides_from_transitions(
        project_historical_transitions(organization_id=zone.organization_id, box_ids=box_ids), zone, direction,
    )


def zone_sides_from_transitions(transitions, zone, direction=None):
    rows = []
    for transition in transitions:
        if not transition["adds_location_history"]:
            continue
        origin, destination = transition["from_thermal_zone"], transition["to_thermal_zone"]
        sides = [("departure", origin, destination), ("arrival", destination, origin)]
        for event_type, own, related in sides:
            if own["id"] != zone.pk or direction not in (None, event_type):
                continue
            rows.append({
                "id": None, "event_type": event_type, "occurred_at": None, "box_id": transition["box_id"],
                "box_code": transition["box_code"], "box_status": transition["box_status"],
                "related_zone_id": related["id"], "related_zone_name": related["name"],
                "temporal_precision": "observation_interval", "transition_id": transition["id"],
                "historical_interval": {
                    "from_iso_year": transition["origin_observation"]["iso_year"],
                    "from_iso_week": transition["origin_observation"]["iso_week"],
                    "to_iso_year": transition["destination_observation"]["iso_year"],
                    "to_iso_week": transition["destination_observation"]["iso_week"],
                },
                "provenance": {"policy": transition["policy"], "source": transition["provenance"],
                               "source_box_labels": transition["source_box_labels"],
                               "origin_observation": transition["origin_observation"],
                               "destination_observation": transition["destination_observation"]},
                "_sort_week": transition["destination_observation"]["week_start"],
            })
    return rows


def merge_zone_history(operational, historical):
    """Order dated rows and interval rows together.

    Both sides of a transition sort on the destination observation week, so they
    keep one deterministic position. It is never displayed as an occurrence date.
    """
    def key(row):
        if row.get("transition_id"):
            return (_monday_instant(date.fromisoformat(row["_sort_week"])), 0, row["transition_id"])
        return (row["occurred_at"], row["id"], "")
    return sorted([*operational, *historical], key=key, reverse=True)
