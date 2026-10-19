"""Pure, read-only association planning for extracted historical temperatures.

``plan(candidates, *, organization_id, baseline=None)`` accepts JSON data only.
IDs are positive integers or nonempty strings. A baseline is an explicit snapshot,
not a database connection or an authorization grant. Invalid baseline data blocks
all proposals, including invalid records unrelated to the supplied candidates.

Association statuses are BLOCKED, CONFLICT, BASELINE_UNAVAILABLE,
BASELINE_INVALID, MISSING_BOX, MISSING_WEEK, CREATE_READING, ADD_EVIDENCE and
ALREADY_PRESENT. Issues are machine-readable codes, not user-facing copy.
Proposals never create or correct biology, boxes, daily readings or probe data.
An applicator must independently authorize and revalidate the complete snapshot.

Evidence identity is (source_sha256, sheet, polyp_cell, ephyrae_cell), matching
the storage uniqueness contract. Repeated identical occurrences are reported
but stored once. Different metadata for one occurrence is always a conflict;
distinct occurrences may support one reading only if all biological counts and
temperatures agree. Decimal spellings remain verbatim in each evidence.
"""

import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date

from .temperature_series import agreed_temperature, iso_week_bounds, temperature_decimal
from .historical_temperature_identity import temperature_identity, temperature_source_matches

_BOX = re.compile(r"([A-Z]{3}-[A-Z]{3}-[0-9]+)\.([0-9]+)")
_CELL = re.compile(r"[A-Z]+[1-9][0-9]*")
_EVIDENCE_FIELDS = {
    "source_file", "source_sha256", "sheet", "temperature_cell",
    "temperature_merged_range", "source_value", "source_box", "polyp_cell",
    "polyp_value", "ephyrae_cell", "ephyrae_value", "week_cell", "week_value",
    "source_iso_year", "source_iso_week",
}
_OCCURRENCE_FIELDS = ("source_sha256", "sheet", "polyp_cell", "ephyrae_cell")
_CANDIDATE_FIELDS = {
    "candidate_id", "iso_year", "iso_week", "source_box", "canonical_box",
    "temperature_c", "biological_state", "polyps", "ephyrae", "evidence",
}
_BASELINE_FIELDS = {"organization_id", "boxes", "biological_measurements", "historical_temperatures"}


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _id(value):
    return (type(value) is int and value > 0) or (type(value) is str and bool(value.strip()))


def _count(value):
    return type(value) is int and value >= 0


def _canonical_box(value):
    if not isinstance(value, str):
        return None
    match = _BOX.fullmatch(value)
    if not match:
        return None
    # Avoid integer conversion of unbounded input while accepting leading zeros.
    number = match[2].lstrip("0") or "0"
    if len(number) > 3:
        return None
    return f"{match[1]}.{number.zfill(3)}"


def _week(year, week):
    try:
        return iso_week_bounds(year, week)[0].isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _temperature(value):
    try:
        return temperature_decimal(value)
    except ValueError:
        return None


def _occurrence(evidence):
    return tuple(evidence[field] for field in _OCCURRENCE_FIELDS)


def _evidence_issues(evidence, *, box, year, week, temperature, polyps, ephyrae):
    if not isinstance(evidence, dict) or set(evidence) != _EVIDENCE_FIELDS:
        return {"INVALID_EVIDENCE_SCHEMA"}
    issues = set()
    text_fields = _EVIDENCE_FIELDS - {"source_iso_year", "source_iso_week"}
    if any(not isinstance(evidence[field], str) for field in text_fields):
        return {"INVALID_EVIDENCE_SCHEMA"}
    if any(not evidence[field].strip() for field in text_fields - {"temperature_merged_range"}):
        issues.add("INCOMPLETE_EVIDENCE")
    if not re.fullmatch(r"[0-9a-f]{64}", evidence["source_sha256"]):
        issues.add("INVALID_EVIDENCE_SHA256")
    if not re.fullmatch(r"C[1-9][0-9]*", evidence["temperature_cell"]):
        issues.add("INVALID_EVIDENCE_CELL")
    matches = [re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", evidence[field])
               for field in ("polyp_cell", "ephyrae_cell", "week_cell")]
    coordinates = [match.groups() for match in matches if match is not None]
    if len(coordinates) != 3:
        issues.add("INVALID_EVIDENCE_CELL")
    elif (len({column for column, _row in coordinates}) != 1
          or int(coordinates[1][1]) != int(coordinates[0][1]) + 1):
        issues.add("EVIDENCE_COORDINATE_MISMATCH")
    literal_week = _temperature(evidence["week_value"])
    if literal_week is None or literal_week != week:
        issues.add("EVIDENCE_LITERAL_WEEK_MISMATCH")
    merged = evidence["temperature_merged_range"]
    if merged:
        match = re.fullmatch(r"(C[1-9][0-9]*):C([1-9][0-9]*)", merged)
        if (not match or match[1] != evidence["temperature_cell"]
                or (len(match[2]), match[2]) <= (len(match[1][1:]), match[1][1:])):
            issues.add("INVALID_EVIDENCE_MERGE")
    if not issues & {"INVALID_EVIDENCE_CELL", "INVALID_EVIDENCE_MERGE", "EVIDENCE_COORDINATE_MISMATCH"}:
        start = int(evidence["temperature_cell"][1:])
        polyp_row, ephyrae_row = int(coordinates[0][1]), int(coordinates[1][1])
        if merged:
            end = int(merged.split(":")[1][1:])
            covered = start <= polyp_row and ephyrae_row <= end
        else:
            covered = polyp_row == start
        if not covered:
            issues.add("EVIDENCE_OUTSIDE_TEMPERATURE_BLOCK")
    if (not _week(evidence["source_iso_year"], evidence["source_iso_week"])
            or (evidence["source_iso_year"], evidence["source_iso_week"]) != (year, week)):
        issues.add("EVIDENCE_WEEK_MISMATCH")
    if box is None or not temperature_source_matches(_canonical_box(evidence["source_box"]), box):
        issues.add("EVIDENCE_BOX_MISMATCH")
    source_temperature = _temperature(evidence["source_value"])
    if source_temperature is None or source_temperature != _temperature(temperature):
        issues.add("EVIDENCE_TEMPERATURE_MISMATCH")
    for field, expected in (("polyp_value", polyps), ("ephyrae_value", ephyrae)):
        value = _temperature(evidence[field])
        if not _count(expected) or value is None or value != expected:
            issues.add("EVIDENCE_BIOLOGY_MISMATCH")
    return issues


def _validate_baseline(baseline, organization_id):
    """Validate the whole graph; never filter away foreign or orphan records."""
    errors = []
    boxes, measurements, readings = {}, {}, {}
    by_week, by_code, by_measurement, occurrences = {}, {}, {}, {}

    def error(code, path):
        errors.append({"code": code, "path": path})

    if not isinstance(baseline, dict) or set(baseline) != _BASELINE_FIELDS:
        error("INVALID_BASELINE_SCHEMA", "baseline")
        return errors, by_code, by_week, by_measurement, occurrences
    if not _id(baseline["organization_id"]) or baseline["organization_id"] != organization_id:
        error("ORGANIZATION_MISMATCH", "baseline.organization_id")
    schemas = {
        "boxes": {"id", "organization_id", "global_code"},
        "biological_measurements": {"id", "box_id", "week_start", "polyp_count", "ephyrae_count"},
        "historical_temperatures": {"id", "biological_measurement_id", "iso_year", "iso_week", "temperature_c", "evidences"},
    }
    tables = {"boxes": boxes, "biological_measurements": measurements, "historical_temperatures": readings}
    for name, fields in schemas.items():
        rows = baseline[name]
        if not isinstance(rows, list):
            error("INVALID_BASELINE_SCHEMA", name)
            continue
        # Paths are ID-based so reordering a valid or invalid snapshot is stable.
        for row in sorted(rows, key=_json):
            path = f"{name}:{_json(row.get('id'))}" if isinstance(row, dict) else name
            optional = ({"merged_into_id", "previous_global_code"} if name == "boxes"
                        else {"duplicate_of_id"} if name == "biological_measurements" else set())
            if (not isinstance(row, dict) or not fields <= set(row)
                    or set(row) - fields - optional or not _id(row.get("id"))):
                error("INVALID_BASELINE_SCHEMA", path)
                continue
            if row["id"] in tables[name]:
                error("DUPLICATE_ID", path)
                continue
            tables[name][row["id"]] = row

    for box_id, box in boxes.items():
        path = f"boxes:{_json(box_id)}"
        if not _id(box["organization_id"]) or box["organization_id"] != organization_id:
            error("ORGANIZATION_MISMATCH", path)
        code = box["global_code"]
        merged_into = box.get("merged_into_id")
        if merged_into is not None:
            target = boxes.get(merged_into) if _id(merged_into) else None
            previous = box.get("previous_global_code")
            if (target is None or target is box or target.get("merged_into_id") is not None
                    or target["organization_id"] != organization_id
                    or not isinstance(previous, str) or _canonical_box(previous) != previous
                    or code != f"MERGED:{box_id}:{previous}"):
                error("INVALID_RETIRED_BOX_IDENTITY", path)
            continue
        if not isinstance(code, str) or _canonical_box(code) != code:
            error("INVALID_BOX_CODE", path)
        elif code in by_code:
            error("DUPLICATE_BOX_CODE", path)
        else:
            by_code[code] = box

    for measurement_id, measurement in measurements.items():
        path = f"biological_measurements:{_json(measurement_id)}"
        box_id = measurement["box_id"]
        if not _id(box_id) or box_id not in boxes:
            error("ORPHAN_BIOLOGICAL_MEASUREMENT", path)
            continue
        value = measurement["week_start"]
        try:
            day = date.fromisoformat(value) if isinstance(value, str) else None
            valid = day is not None and day.isoformat() == value and day.weekday() == 0
        except ValueError:
            valid = False
        if not valid:
            error("INVALID_MONDAY_WEEK_KEY", path)
        else:
            duplicate_id = measurement.get("duplicate_of_id")
            if duplicate_id is not None:
                target = measurements.get(duplicate_id) if _id(duplicate_id) else None
                if (target is None or target is measurement or target.get("duplicate_of_id") is not None
                        or target["box_id"] != box_id or target["week_start"] != value):
                    error("INVALID_BIOLOGICAL_DUPLICATE", path)
            elif boxes[box_id].get("merged_into_id") is not None:
                error("BIOLOGY_ON_RETIRED_BOX", path)
            else:
                key = (box_id, value)
                if key in by_week:
                    error("DUPLICATE_BIOLOGICAL_WEEK", path)
                by_week[key] = measurement
        if not _count(measurement["polyp_count"]) or not _count(measurement["ephyrae_count"]):
            error("INVALID_BIOLOGICAL_COUNTS", path)

    for reading_id, reading in readings.items():
        path = f"historical_temperatures:{_json(reading_id)}"
        measurement_id = reading["biological_measurement_id"]
        if not _id(measurement_id) or measurement_id not in measurements:
            error("ORPHAN_HISTORICAL_TEMPERATURE", path)
            continue
        measurement = measurements[measurement_id]
        if measurement.get("duplicate_of_id") is not None:
            error("TEMPERATURE_ON_DUPLICATE_BIOLOGY", path)
        if measurement_id in by_measurement:
            error("DUPLICATE_HISTORICAL_TEMPERATURE", path)
        by_measurement[measurement_id] = reading
        week_start = _week(reading["iso_year"], reading["iso_week"])
        if week_start is None or week_start != measurement["week_start"]:
            error("HISTORICAL_WEEK_MISMATCH", path)
        if _temperature(reading["temperature_c"]) is None:
            error("INVALID_TEMPERATURE", path)
        evidences = reading["evidences"]
        if not isinstance(evidences, list) or not evidences:
            error("MISSING_HISTORICAL_EVIDENCE", path)
            continue
        box = boxes.get(measurement["box_id"]) if _id(measurement["box_id"]) else None
        for evidence in sorted(evidences, key=_json):
            issues = _evidence_issues(
                evidence, box=box["global_code"] if box else None,
                year=reading["iso_year"], week=reading["iso_week"],
                temperature=reading["temperature_c"], polyps=measurement["polyp_count"],
                ephyrae=measurement["ephyrae_count"],
            )
            for issue in sorted(issues):
                error(issue, path)
            if issues:
                continue
            identity = _occurrence(evidence)
            if identity in occurrences:
                error("DUPLICATE_BASELINE_EVIDENCE", path)
            occurrences[identity] = (measurement_id, evidence)
    return sorted(errors, key=_json), by_code, by_week, by_measurement, occurrences


def _source_groups(candidates, source_blockers):
    groups = defaultdict(list)
    id_rows, occurrence_rows = defaultdict(list), defaultdict(list)
    for candidate in candidates:
        issues = set()
        if not isinstance(candidate, dict):
            candidate = {"invalid_candidate": candidate}
        if set(candidate) != _CANDIDATE_FIELDS:
            issues.add("INVALID_CANDIDATE_SCHEMA")
        candidate_id = candidate.get("candidate_id")
        if isinstance(candidate_id, str):
            issues.update(source_blockers.get(candidate_id, []))
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            issues.add("INVALID_CANDIDATE_ID")
        canonical = _canonical_box(candidate.get("source_box"))
        if canonical is None or candidate.get("canonical_box") != canonical:
            issues.add("INVALID_BOX_IDENTITY")
        year, week = candidate.get("iso_year"), candidate.get("iso_week")
        week_start = _week(year, week)
        if week_start is None:
            issues.add("INVALID_ISO_WEEK")
        if _temperature(candidate.get("temperature_c")) is None:
            issues.add("INVALID_TEMPERATURE")
        if candidate.get("biological_state") != "complete":
            issues.add("INCOMPLETE_BIOLOGICAL_SOURCE")
        if not _count(candidate.get("polyps")) or not _count(candidate.get("ephyrae")):
            issues.add("INVALID_BIOLOGICAL_COUNTS")
        evidence = candidate.get("evidence")
        evidence_issues = _evidence_issues(
            evidence, box=canonical, year=year, week=week,
            temperature=candidate.get("temperature_c"), polyps=candidate.get("polyps"),
            ephyrae=candidate.get("ephyrae"),
        )
        issues.update(evidence_issues)
        if isinstance(evidence, dict) and evidence.get("source_box") != candidate.get("source_box"):
            issues.add("EVIDENCE_BOX_MISMATCH")
        item = {"candidate": candidate, "issues": issues}
        # Malformed identities must not accidentally merge with unrelated rows.
        key = (temperature_identity(canonical), year, week) if canonical and week_start else (None, _json(candidate))
        groups[key].append(item)
        if isinstance(candidate_id, str):
            id_rows[candidate_id].append(item)
        if (isinstance(evidence, dict)
                and all(isinstance(evidence.get(field), str) and evidence[field]
                        for field in _OCCURRENCE_FIELDS)
                and re.fullmatch(r"[0-9a-f]{64}", evidence["source_sha256"])
                and _CELL.fullmatch(evidence["polyp_cell"])
                and _CELL.fullmatch(evidence["ephyrae_cell"])):
            occurrence_rows[_occurrence(evidence)].append(item)

    for rows in id_rows.values():
        if len(rows) > 1 and len({_json(row["candidate"]) for row in rows}) > 1:
            for row in rows:
                row["issues"].add("CANDIDATE_ID_COLLISION")
    for rows in occurrence_rows.values():
        if len(rows) > 1 and len({_json(row["candidate"]["evidence"]) for row in rows}) > 1:
            for row in rows:
                row["issues"].add("SOURCE_OCCURRENCE_CONFLICT")
    return groups


def plan(candidates, *, organization_id, baseline=None, source_blockers=None):
    """Return deterministic JSON proposals without importing Django or doing IO.

    Non-list candidates raise TypeError; non-JSON data or an invalid target ID
    raise ValueError. Invalid JSON baseline content is instead returned as
    BASELINE_INVALID with diagnostics and no proposals. Source errors block the
    affected association, not unrelated valid associations. No baseline means
    source-only analysis, never an assumption that the institution is empty.
    """
    source_blockers = {} if source_blockers is None else source_blockers
    if (not isinstance(source_blockers, dict)
            or any(not isinstance(key, str) or not isinstance(values, list)
                   or not values or any(not isinstance(value, str) or not value for value in values)
                   for key, values in source_blockers.items())):
        raise ValueError("source_blockers must map candidate IDs to nonempty lists of issue codes.")
    if not _id(organization_id):
        raise ValueError("organization_id must be a positive integer or nonempty string.")
    if not isinstance(candidates, list):
        raise TypeError("candidates must be a JSON list.")
    try:
        _json(candidates)
        _json(baseline)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Candidates and baseline must contain finite JSON data only.") from exc
    candidates, baseline = deepcopy(candidates), deepcopy(baseline)
    errors, boxes, measurements, readings, baseline_occurrences = [], {}, {}, {}, {}
    baseline_status = "BASELINE_UNAVAILABLE"
    if baseline is not None:
        errors, boxes, measurements, readings, baseline_occurrences = _validate_baseline(baseline, organization_id)
        baseline_status = "BASELINE_INVALID" if errors else "VALID"

    associations, changes = [], []
    for items in _source_groups(candidates, source_blockers).values():
        rows = ([items[0]["candidate"]] if len(items) == 1
                else sorted((item["candidate"] for item in items), key=_json))
        first = rows[0]
        issues = set().union(*(item["issues"] for item in items))
        values = [row.get("temperature_c") for row in rows]
        parsed = {_temperature(value) for value in values} - {None}
        if len(parsed) > 1:
            issues.add("SOURCE_TEMPERATURE_CONFLICT")
        counts = {(row.get("polyps"), row.get("ephyrae")) for row in rows
                  if _count(row.get("polyps")) and _count(row.get("ephyrae"))}
        if len(counts) > 1:
            issues.add("SOURCE_BIOLOGY_CONFLICT")
        if len(rows) == 1:
            evidences = [first["evidence"]] if "evidence" in first else []
        else:
            evidence_map = {_json(row["evidence"]): row["evidence"] for row in rows if "evidence" in row}
            evidences = [evidence_map[key] for key in sorted(evidence_map)]
        occurrence_counts = Counter(
            _occurrence(row["evidence"]) for row in rows
            if isinstance(row.get("evidence"), dict)
            and all(isinstance(row["evidence"].get(field), str) for field in _OCCURRENCE_FIELDS)
        )
        association = {
            "candidate_ids": sorted({row["candidate_id"] for row in rows if isinstance(row.get("candidate_id"), str)}),
            "canonical_box": temperature_identity(_canonical_box(first.get("source_box"))),
            "iso_year": first.get("iso_year"), "iso_week": first.get("iso_week"),
            "source_occurrence_count": len(rows),
            "duplicate_source_occurrences": sum(count - 1 for count in occurrence_counts.values()),
            "temperature_values": sorted({value for value in values if isinstance(value, str)}),
            "evidences": evidences,
        }
        conflict_codes = {"SOURCE_TEMPERATURE_CONFLICT", "SOURCE_BIOLOGY_CONFLICT",
                          "SOURCE_OCCURRENCE_CONFLICT", "CANDIDATE_ID_COLLISION"}
        if issues:
            status = "CONFLICT" if issues & conflict_codes else "BLOCKED"
        elif baseline_status != "VALID":
            status = baseline_status
        else:
            box = boxes.get(association["canonical_box"])
            measurement = measurements.get((box["id"], _week(first["iso_year"], first["iso_week"]))) if box else None
            if box is None:
                status = "MISSING_BOX"
                issues.add("MISSING_BOX")
            elif measurement is None:
                status = "MISSING_WEEK"
                issues.add("MISSING_BIOLOGICAL_WEEK")
            else:
                association["box_id"] = box["id"]
                association["biological_measurement_id"] = measurement["id"]
                association["baseline_counts"] = {"polyps": measurement["polyp_count"], "ephyrae": measurement["ephyrae_count"]}
                if counts != {(measurement["polyp_count"], measurement["ephyrae_count"])}:
                    issues.add("BIOLOGICAL_COUNT_MISMATCH")
                reading = readings.get(measurement["id"])
                temperature = agreed_temperature(values)
                if reading:
                    association["historical_temperature_id"] = reading["id"]
                    # New provenance never rewrites the accepted representation.
                    if _temperature(reading["temperature_c"]) != _temperature(temperature):
                        issues.add("EXISTING_TEMPERATURE_CONFLICT")
                    temperature = reading["temperature_c"]
                new_evidences = []
                for evidence in evidences:
                    present = baseline_occurrences.get(_occurrence(evidence))
                    if present:
                        if present != (measurement["id"], evidence):
                            issues.add("EXISTING_EVIDENCE_CONFLICT")
                    else:
                        new_evidences.append(evidence)
                if issues:
                    status = "CONFLICT"
                elif reading and not new_evidences:
                    status = "ALREADY_PRESENT"
                else:
                    status = "ADD_EVIDENCE" if reading else "CREATE_READING"
                    association["temperature_c"] = temperature
                    changes.append({"operation": status, "payload": {
                        "biological_measurement_id": measurement["id"],
                        "iso_year": first["iso_year"], "iso_week": first["iso_week"],
                        "temperature_c": temperature, "evidences": new_evidences,
                    }})
        if baseline_status != "VALID":
            issues.add(baseline_status)
        association.update(status=status, issues=sorted(issues))
        associations.append(association)

    associations.sort(key=lambda row: (
        row["canonical_box"] or "",
        row["iso_year"] if type(row["iso_year"]) is int else -1,
        row["iso_week"] if type(row["iso_week"]) is int else -1,
        # Valid canonical Box/week keys are unique; only unresolved keys need a tie-break.
        _json(row) if row["canonical_box"] is None or not _week(row["iso_year"], row["iso_week"]) else "",
    ))
    changes.sort(key=_json)
    statuses = Counter(row["status"] for row in associations)
    return {
        "organization_id": organization_id, "baseline_status": baseline_status,
        "baseline_errors": errors, "associations": associations, "proposed_changes": changes,
        "summary": {
            "candidate_count": len(candidates), "association_count": len(associations),
            "status_counts": dict(sorted(statuses.items())), "proposed_change_count": len(changes),
            "duplicate_source_occurrences": sum(row["duplicate_source_occurrences"] for row in associations),
            "ready": baseline_status == "VALID" and all(status in {"CREATE_READING", "ADD_EVIDENCE", "ALREADY_PRESENT"} for status in statuses),
        },
    }
