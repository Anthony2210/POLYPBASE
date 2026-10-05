"""Turn the authoritative workbook into a reviewed manifest.

Pure Python: no database, no third-party package. The only inputs are the
workbook fingerprint, the cells, and the explicit decisions in decisions.py.
Anything unexpected raises ``SourceError`` instead of being repaired silently.
"""

import re
from datetime import date

from . import decisions
from .workbook import column_letter, read_workbook

STRICT_IDENTIFIER = re.compile(r"^([A-Z]{3}-[A-Z]{3}-[0-9]+)\.([0-9]+)$")
OPERATION_CREATE = "CREATE_OR_MATCH"
OPERATION_CORRECTION = "EXPLICIT_CORRECTION"


class SourceError(Exception):
    """The workbook violates an extraction expectation."""


def canonical_box_code(identifier):
    """Return (global_code, strain_code, box_number) for a strict identifier.

    The global code zero-pads the box number to three digits, exactly like the
    codes already stored; the source text is kept as the local code.
    """
    match = STRICT_IDENTIFIER.match(identifier)
    if match is None:
        raise SourceError(f"Unexpected box identifier: {identifier!r}.")
    strain_code, number = match.groups()
    if int(number) > 999:
        raise SourceError(f"Box number out of range: {identifier!r}.")
    return f"{strain_code}.{int(number):03d}", strain_code, number


def _count(value, cell, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SourceError(f"{label} {cell} is not a non-negative integer: {value!r}.")
    return value


def _week_columns(sheet):
    """Map column -> (iso_week, raw header, reviewed correction or None)."""
    weeks = {}
    seen = set()
    for column in range(decisions.FIRST_WEEK_COLUMN, sheet.max_column + 1):
        raw = sheet.value(decisions.HEADER_ROW, column)
        reviewed = decisions.REVIEWED_WEEK_HEADERS.get((sheet.name, column))
        if reviewed is not None:
            if raw != reviewed["raw"]:
                raise SourceError(
                    f"Reviewed header {sheet.name}!{column_letter(column)}2 changed: {raw!r}."
                )
            week, correction = reviewed["iso_week"], "REVIEWED_WEEK_HEADER"
        elif raw is None:
            continue
        elif isinstance(raw, int) and not isinstance(raw, bool) and 1 <= raw <= 53:
            week, correction = raw, None
        else:
            raise SourceError(
                f"Unexpected week header {sheet.name}!{column_letter(column)}2: {raw!r}."
            )
        if week in seen:
            raise SourceError(f"Duplicate week {week} header in sheet {sheet.name}.")
        seen.add(week)
        weeks[column] = (week, raw, correction)
    return weeks


def _check_sheet_layout(sheet):
    for column, expected in decisions.EXPECTED_HEADER_LABELS.items():
        if sheet.value(decisions.HEADER_ROW, column) != expected:
            raise SourceError(f"Unexpected header layout in sheet {sheet.name}.")
    if sheet.value(decisions.YEAR_ROW, decisions.RECAP_COLUMN) != decisions.RECAP_YEAR:
        raise SourceError(f"Recap column is not {decisions.RECAP_YEAR} in {sheet.name}.")
    if sheet.value(decisions.HEADER_ROW, decisions.RECAP_COLUMN) is not None:
        raise SourceError(f"Recap column carries a week header in {sheet.name}.")


def _orientation_for(sheet_name, row):
    for item in decisions.ORIENTATION_CORRECTIONS:
        if item["sheet"] == sheet_name and row in (item["polyp_row"], item["ephyrae_row"]):
            return item
    return None


def _blocks(sheet):
    """Yield one dict per polyp/ephyrae row pair with its resolved context."""
    species = None
    box = None
    row = 3
    while row <= sheet.max_row:
        label = sheet.value(row, 4)
        species_cell = sheet.value(row, 1)
        box_cell = sheet.value(row, 2)
        if isinstance(species_cell, str) and species_cell.strip():
            species = species_cell
            box = box_cell
        elif box_cell is not None:
            box = box_cell
        if label is None:
            if any(sheet.value(row, c) is not None for c in range(3, sheet.max_column + 1)):
                raise SourceError(f"Values without a label at {sheet.name}!{row}.")
            row += 1
            continue
        orientation = _orientation_for(sheet.name, row)
        if orientation is not None:
            expected = (decisions.EPHYRAE_LABEL, decisions.POLYP_LABEL)
            first, second = orientation["polyp_row"], orientation["ephyrae_row"]
            if row != first:
                raise SourceError(f"Orientation block misaligned at {sheet.name}!{row}.")
        else:
            expected = (decisions.POLYP_LABEL, decisions.EPHYRAE_LABEL)
            first, second = row, row + 1
        if (sheet.value(first, 4), sheet.value(second, 4)) != expected:
            raise SourceError(f"Unexpected row labels at {sheet.name}!{row}.")
        if sheet.value(second, 1) is not None or sheet.value(second, 2) is not None:
            raise SourceError(f"Context cell on a second block row at {sheet.name}!{second}.")
        if box is None:
            raise SourceError(f"Block without a box at {sheet.name}!{row}.")
        yield {
            "polyp_row": first,
            "ephyrae_row": second,
            "species_label": species,
            "source_box": box,
            "orientation": orientation,
            "temperature": sheet.value(first, 3),
        }
        row += 2


def _operational_species(label, code):
    name = decisions.SPECIES_LABEL_OVERRIDES.get(label, label)
    name = name.strip()
    if not name or "\n" in name:
        raise SourceError(f"Unusable species label {label!r} for {code}.")
    return name


def build_manifest(workbook_path):
    workbook = read_workbook(workbook_path)
    if workbook.sha256 != decisions.EXPECTED_WORKBOOK_SHA256:
        raise SourceError(
            "Unexpected workbook SHA-256 "
            f"{workbook.sha256}; expected {decisions.EXPECTED_WORKBOOK_SHA256}."
        )

    observations = []
    excluded = []
    boxes = {}
    skipped_sheets = []
    ignored_merged = 0
    used_aliases = set()
    used_exclusions = set()
    used_orientations = set()
    used_week_overrides = set()
    corrections_by_key = {
        (item["box"], item["iso_year"], item["iso_week"]): item
        for item in decisions.MEASUREMENT_CORRECTIONS
    }

    for sheet in workbook.sheets:
        if sheet.state != "visible":
            if sheet.cells:
                raise SourceError(f"Hidden sheet {sheet.name!r} unexpectedly holds data.")
            skipped_sheets.append({"sheet": sheet.name, "reason": "HIDDEN_EMPTY_SHEET"})
            continue
        _check_sheet_layout(sheet)
        for (hidden_row, hidden_column), hidden in sheet.ignored_merged_values.items():
            if hidden_column >= decisions.RECAP_COLUMN and hidden_row > decisions.YEAR_ROW:
                raise SourceError(
                    f"Hidden value {hidden!r} in a merged measurement cell at "
                    f"{sheet.name}!{column_letter(hidden_column)}{hidden_row}."
                )
            ignored_merged += 1
        weeks = _week_columns(sheet)
        for cell_row, cell_column in sheet.cells:
            if (
                cell_row > decisions.HEADER_ROW
                and cell_column >= decisions.FIRST_WEEK_COLUMN
                and cell_column not in weeks
            ):
                raise SourceError(
                    f"Data outside a week column at {sheet.name}!"
                    f"{column_letter(cell_column)}{cell_row}."
                )

        for block in _blocks(sheet):
            raw_identifier = block["source_box"]
            if not isinstance(raw_identifier, str):
                raise SourceError(f"Non-text box identifier at {sheet.name}!{block['polyp_row']}.")
            source_box = raw_identifier
            alias_reason = None
            if raw_identifier in decisions.IDENTIFIER_ALIASES:
                source_box = decisions.IDENTIFIER_ALIASES[raw_identifier]
                alias_reason = "ANTHONY_EXACT_IDENTIFIER_ALIAS"
                used_aliases.add(raw_identifier)
            code, strain_code, box_number = canonical_box_code(source_box)
            species_name = _operational_species(block["species_label"], code)

            known = boxes.get(code)
            if known is None:
                boxes[code] = {
                    "code": code,
                    "local_code": source_box,
                    "box_number": box_number,
                    "strain_code": strain_code,
                    "strain_number": int(strain_code.rsplit("-", 1)[1]),
                    "origin_code": strain_code.split("-")[1],
                    "species_aaa": strain_code.split("-")[0],
                    "species_label_source": block["species_label"],
                    "species_name": species_name,
                    "sheet": sheet.name,
                    "first_source_row": block["polyp_row"],
                    "source_identifier": raw_identifier,
                    "identifier_correction": alias_reason,
                }
            elif (known["sheet"], known["species_name"]) != (sheet.name, species_name):
                raise SourceError(f"Box {code} appears with conflicting species or sheet.")

            excluded_rule = next(
                (
                    rule
                    for rule in decisions.EXCLUDED_BLOCKS
                    if (rule["sheet"], rule["source_box"], rule["polyp_row"], rule["ephyrae_row"])
                    == (sheet.name, raw_identifier, block["polyp_row"], block["ephyrae_row"])
                ),
                None,
            )
            if excluded_rule is not None and block["temperature"] != excluded_rule["block_temperature_c"]:
                raise SourceError(f"Excluded block temperature changed at {sheet.name}!{block['polyp_row']}.")
            if block["orientation"] is not None:
                used_orientations.add((block["polyp_row"], block["ephyrae_row"]))

            for column in [decisions.RECAP_COLUMN] + sorted(weeks):
                polyp_value = sheet.value(block["polyp_row"], column)
                ephyrae_value = sheet.value(block["ephyrae_row"], column)
                if polyp_value is None and ephyrae_value is None:
                    continue
                polyp_cell = f"{column_letter(column)}{block['polyp_row']}"
                ephyrae_cell = f"{column_letter(column)}{block['ephyrae_row']}"
                base = {
                    "sheet": sheet.name,
                    "source_box": raw_identifier,
                    "box": code,
                    "polyp_cell": polyp_cell,
                    "polyp_value": polyp_value,
                    "ephyrae_cell": ephyrae_cell,
                    "ephyrae_value": ephyrae_value,
                    "block_temperature_c": block["temperature"],
                }
                if column == decisions.RECAP_COLUMN:
                    excluded.append({**base, "reason": "RECAP_2025_COLUMN_E"})
                    continue
                week, header, week_correction = weeks[column]
                if excluded_rule is not None:
                    used_exclusions.add(excluded_rule["polyp_row"])
                    excluded.append({**base, "iso_week": week, "reason": excluded_rule["reason"]})
                    continue
                if polyp_value is None or ephyrae_value is None:
                    raise SourceError(
                        f"Partial observation at {sheet.name}!{polyp_cell}/{ephyrae_cell}."
                    )
                polyps = _count(polyp_value, polyp_cell, "Polyp cell")
                ephyrae = _count(ephyrae_value, ephyrae_cell, "Ephyra cell")
                applied = []
                if alias_reason:
                    applied.append({"code": alias_reason, "from": raw_identifier, "to": source_box})
                if week_correction:
                    used_week_overrides.add((sheet.name, column))
                    applied.append(
                        {"code": week_correction, "raw_header": header, "iso_week": week}
                    )
                legacy_reading = None
                if block["orientation"] is not None:
                    # The label-literal reading of the same two cells: the only
                    # old stored value the guarded TTH correction may replace.
                    legacy_reading = {"polyps": ephyrae, "ephyrae": polyps}
                    applied.append(
                        {
                            "code": "ANTHONY_TTH_LABEL_INVERSION",
                            "polyp_source_label": decisions.EPHYRAE_LABEL,
                            "ephyrae_source_label": decisions.POLYP_LABEL,
                        }
                    )
                operation = OPERATION_CREATE
                expected_current = None
                correction = corrections_by_key.get((code, decisions.ISO_YEAR, week))
                if correction is not None:
                    target = correction["target"]
                    if (polyps, ephyrae) != (target["polyps"], target["ephyrae"]):
                        raise SourceError(
                            f"Correction target for {code} W{week} differs from the workbook."
                        )
                    operation = OPERATION_CORRECTION
                    expected_current = dict(correction["expected_current"])
                    applied.append({"code": correction["reason"]})
                observations.append(
                    {
                        **base,
                        "year": decisions.ISO_YEAR,
                        "iso_week": week,
                        "measured_on": date.fromisocalendar(decisions.ISO_YEAR, week, 1).isoformat(),
                        "polyps": polyps,
                        "ephyrae": ephyrae,
                        "strobila": None,
                        "operation": operation,
                        "expected_current": expected_current,
                        "legacy_reading": legacy_reading,
                        "corrections": applied,
                    }
                )

    _require_all_decisions_used(
        used_aliases, used_exclusions, used_orientations, used_week_overrides, observations
    )
    seen = set()
    for item in observations:
        key = (item["box"], item["iso_week"])
        if key in seen:
            raise SourceError(f"Duplicate source observation for {key}.")
        seen.add(key)
    return _assemble(workbook, observations, excluded, boxes, skipped_sheets, ignored_merged)


def _require_all_decisions_used(aliases, exclusions, orientations, week_overrides, observations):
    if aliases != set(decisions.IDENTIFIER_ALIASES):
        raise SourceError("An approved identifier alias did not match the workbook.")
    expected_orientations = {
        (item["polyp_row"], item["ephyrae_row"]) for item in decisions.ORIENTATION_CORRECTIONS
    }
    if orientations != expected_orientations:
        raise SourceError("An approved TTH orientation block did not match the workbook.")
    if week_overrides != set(decisions.REVIEWED_WEEK_HEADERS):
        raise SourceError("The reviewed W20 column did not carry data.")
    if exclusions != {rule["polyp_row"] for rule in decisions.EXCLUDED_BLOCKS}:
        raise SourceError("An approved excluded block did not match the workbook.")
    present = {(o["box"], o["iso_week"]) for o in observations}
    for item in decisions.MEASUREMENT_CORRECTIONS:
        if (item["box"], item["iso_week"]) not in present:
            raise SourceError("An approved measurement correction has no source row.")


def _tally(items, key):
    counts = {}
    for item in items:
        counts[item[key]] = counts.get(item[key], 0) + 1
    return dict(sorted(counts.items()))


def _assemble(workbook, observations, excluded, boxes, skipped_sheets, ignored_merged):
    applied_counts = {}
    for item in observations:
        for applied in item["corrections"]:
            applied_counts[applied["code"]] = applied_counts.get(applied["code"], 0) + 1
    return {
        "schema": decisions.SCHEMA,
        "schema_version": decisions.SCHEMA_VERSION,
        "generator_version": decisions.GENERATOR_VERSION,
        "source": {"filename": decisions.SOURCE_FILENAME, "sha256": workbook.sha256},
        "target_organization": {"name": decisions.TARGET_ORGANIZATION_NAME},
        "conventions": {
            "iso_year": decisions.ISO_YEAR,
            "measured_on": decisions.MEASURED_ON_CONVENTION,
            "strobila": "null_not_measured",
            "user": "null_unknown_observer",
        },
        "decisions": {
            "identity_corrections": [dict(item) for item in decisions.IDENTITY_CORRECTIONS],
            "measurement_corrections": [dict(item) for item in decisions.MEASUREMENT_CORRECTIONS],
            "identifier_aliases": dict(decisions.IDENTIFIER_ALIASES),
            "approved_operational_taxa": list(decisions.APPROVED_OPERATIONAL_TAXA),
            "strain_label_exceptions": {
                key: dict(value) for key, value in decisions.STRAIN_LABEL_EXCEPTIONS.items()
            },
        },
        "counts": {
            "observations": len(observations),
            "operations": _tally(observations, "operation"),
            "excluded": _tally(excluded, "reason"),
            "corrections_applied": dict(sorted(applied_counts.items())),
            "alias_observations": _tally(
                [
                    {"source_box": item["source_box"]}
                    for item in observations
                    if item["source_box"] in decisions.IDENTIFIER_ALIASES
                ],
                "source_box",
            ),
            "boxes": len(boxes),
            "ignored_merged_non_anchor_values": ignored_merged,
        },
        "skipped_sheets": skipped_sheets,
        "boxes": [boxes[code] for code in sorted(boxes)],
        "measurements": observations,
        "excluded": excluded,
    }
