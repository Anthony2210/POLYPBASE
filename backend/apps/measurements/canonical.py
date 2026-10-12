"""Versioned, transport-independent measurement preparation. No Django imports."""

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Context, Decimal
import json
import re
from typing import Mapping

SCHEMA = "polypbase.biological-measurement-entry"
VERSION = "1.0"
FIELDS = (
    "box_reference", "measured_on", "iso_year", "iso_week", "polyp_count",
    "ephyrae_count", "strobila_count", "salinity_psu", "culture_status",
    "needs_attention", "notes", "observer_reference",
)
PAYLOAD_FIELDS = FIELDS[4:11]
NULLABLE_FIELDS = {"strobila_count", "salinity_psu", "observer_reference"}
CULTURE_STATUSES = {"good", "medium", "bad", "dead", "not_specified"}
MAX_COUNT = 2147483647  # Portable PositiveIntegerField range.


@dataclass(frozen=True)
class SourceCoordinate:
    source_id: str
    record_number: int
    line_start: int
    line_end: int


@dataclass(frozen=True)
class Provenance:
    coordinate: SourceCoordinate
    headers: tuple[str, ...]
    original_values: tuple[str, ...]
    mapping: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True)
class Issue:
    code: str
    field: str | None = None
    column: int | None = None  # One-based source column.


@dataclass(frozen=True)
class FieldValue:
    field: str
    state: str  # absent (unmapped), null, blank (no default), or value
    value: str | int | bool | None = None


@dataclass(frozen=True)
class MeasurementTime:
    precision: str  # exact_date or iso_week
    iso_year: int
    iso_week: int
    measured_on: str | None


@dataclass(frozen=True)
class CanonicalRow:
    time: MeasurementTime
    values: tuple[FieldValue, ...]
    provenance: Provenance
    schema: str = SCHEMA
    schema_version: str = VERSION

    def to_dict(self):
        return asdict(self)

    def canonical_json(self):
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class RowResult:
    status: str  # valid, blank, invalid
    provenance: Provenance
    row: CanonicalRow | None = None
    issues: tuple[Issue, ...] = ()


def validate_row(values: Mapping[str, str], provenance: Provenance) -> RowResult:
    """Validate source strings; XLSX adapters must supply explicit string conventions.

    Missing keys are unsupplied, not scientific zero or an instruction to clear.
    Values are decoded source cells, never evaluated expressions.
    """
    issues = []
    columns = {
        field: index + 1 for index, (_, field) in enumerate(provenance.mapping)
        if field is not None
    }

    def error(field, code):
        issues.append(Issue(code, field, columns.get(field)))

    unknown = sorted(set(values) - set(FIELDS))
    for field in unknown:
        error(field, "unknown_field")
    for field in FIELDS:
        if field not in values:
            continue
        value = values[field]
        if not isinstance(value, str):
            error(field, "expected_source_string")
        elif len(value) > 16384:
            error(field, "field_limit")
    if issues:
        return RowResult("invalid", provenance, issues=tuple(issues))
    text = {key: value.strip() for key, value in values.items()}
    if not any(text.get(field, "") for field in PAYLOAD_FIELDS):
        return RowResult("blank", provenance)

    normalized = []
    for field in FIELDS:
        if field in {"measured_on", "iso_year", "iso_week"}:
            continue
        raw = text.get(field)
        if raw is None:
            normalized.append(FieldValue(field, "absent"))
            if field in {"box_reference", "polyp_count", "ephyrae_count"}:
                error(field, "required")
            continue
        if not raw:
            if field in {"box_reference", "polyp_count", "ephyrae_count"}:
                error(field, "required")
            normalized.append(FieldValue(field, "null" if field in NULLABLE_FIELDS else "blank"))
            continue
        value = raw
        if field.endswith("_count"):
            if raw.startswith(("=", "+", "@")):
                error(field, "numeric_formula")
            elif not re.fullmatch(r"[0-9]+", raw):
                error(field, "non_negative_integer_required")
            elif len(raw.lstrip("0")) > 10 or int(raw.lstrip("0") or "0") > MAX_COUNT:
                error(field, "count_out_of_range")
            else:
                value = int(raw.lstrip("0") or "0")
        elif field == "salinity_psu":
            if raw.startswith(("=", "+", "@")):
                error(field, "numeric_formula")
            elif not re.fullmatch(r"-?[0-9]+(?:\.[0-9]{1,2})?", raw):
                error(field, "decimal_format")
            else:
                decimal = Decimal(raw)
                if decimal.copy_abs() > Decimal("999.99"):
                    error(field, "decimal_out_of_range")
                else:
                    value = format(decimal.quantize(Decimal("0.01"), context=Context(prec=5)), ".2f")
        elif field == "culture_status" and raw not in CULTURE_STATUSES:
            error(field, "invalid_choice")
        elif field == "needs_attention":
            if raw not in {"true", "false"}:
                error(field, "boolean_format")
            else:
                value = raw == "true"
        elif field == "notes":
            value = values[field]  # Keep meaningful whitespace in free text.
        normalized.append(FieldValue(field, "value", value))

    exact_date = None
    if text.get("measured_on"):
        raw = text["measured_on"]
        try:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw):
                raise ValueError
            exact_date = date.fromisoformat(raw)
        except ValueError:
            error("measured_on", "iso_date_required")
    year = week = None
    has_week = bool(text.get("iso_year") or text.get("iso_week"))
    if has_week:
        for field in ("iso_year", "iso_week"):
            raw = text.get(field, "")
            if not raw:
                error(field, "required")
            elif not re.fullmatch(r"[0-9]{4}" if field == "iso_year" else r"[0-9]{1,2}", raw):
                error(field, "iso_week_format")
            elif field == "iso_year":
                year = int(raw)
            else:
                week = int(raw)
        if year is not None and week is not None:
            try:
                date.fromisocalendar(year, week, 1)  # Validate only; never retain a synthetic date.
            except ValueError:
                error("iso_week", "invalid_iso_week")
    if exact_date is not None:
        iso = exact_date.isocalendar()
        if has_week and year is not None and week is not None and (year, week) != (iso.year, iso.week):
            error("iso_week", "date_week_mismatch")
        time = MeasurementTime("exact_date", iso.year, iso.week, exact_date.isoformat())
    elif has_week and year is not None and week is not None:
        time = MeasurementTime("iso_week", year, week, None)
    else:
        error("measured_on", "date_or_iso_week_required")
        time = None
    if issues:
        return RowResult("invalid", provenance, issues=tuple(issues))
    return RowResult("valid", provenance, CanonicalRow(time, tuple(normalized), provenance))
