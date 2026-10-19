"""Pure contracts for historical temperatures; no queries or endpoint integration."""

import re
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def temperature_decimal(value: str) -> Decimal:
    """Read an exact finite numeric lexeme, never a binary float or missing value.

    Keep the original string for storage/provenance. Decimal construction and
    equality do not round to the current decimal context's precision.
    """
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value.strip()):
        raise ValueError("Temperature must be a finite decimal string.")
    try:
        result = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError("Temperature must be a finite decimal string.") from exc
    if not result.is_finite():
        raise ValueError("Temperature must be finite.")
    return result


def agreed_temperature(values: Iterable[str]) -> str:
    """Require unanimous numeric evidence; preserve the finest supplied scale.

    Ties use lexical ordering for an input-order-independent representation.
    This selects a spelling of an agreed value, not a winning observation.
    Every original spelling remains in its evidence row.
    """
    parsed = [(temperature_decimal(value), value) for value in values]
    if not parsed:
        raise ValueError("At least one temperature evidence is required.")
    if any(value != parsed[0][0] for value, _ in parsed):
        raise ValueError("Conflicting source temperatures cannot form one reading.")
    return min(parsed, key=lambda item: (item[0].as_tuple().exponent, item[1]))[1]


def iso_week_bounds(iso_year: int, iso_week: int) -> tuple[date, date]:
    if type(iso_year) is not int or type(iso_week) is not int:
        raise ValueError("ISO year and week must be integers.")
    return date.fromisocalendar(iso_year, iso_week, 1), date.fromisocalendar(iso_year, iso_week, 7)


def historical_temperature_point(
    *, reading_id: int, biological_measurement_id: int, box_id: int,
    iso_year: int, iso_week: int, temperature_c: str, evidence_count: int,
    association_status: str,
) -> dict | None:
    """Project explicitly supplied, authorized scalars without ORM access.

    Bounds describe uncertainty, never observation dates or plot timestamps.
    Evidence details remain a separately authorized provenance resource.
    Pending associations have no confirmed series point. Callers must supply
    the persisted status, not infer approval from matching counts or evidence.
    """
    if association_status == "pending_review":
        return None
    if association_status != "confirmed":
        raise ValueError("A known historical association status is required.")
    temperature_decimal(temperature_c)
    first, last = iso_week_bounds(iso_year, iso_week)
    if type(evidence_count) is not int or evidence_count < 1:
        raise ValueError("A historical reading requires supporting evidence.")
    return {
        "id": reading_id,
        "key": f"historical_weekly:{reading_id}",
        "source_kind": "historical_weekly",
        "temporal_precision": "iso_week",
        "statistic": "source_reading",
        "temperature_c": temperature_c,
        "unit": "degC",
        "iso_year": iso_year,
        "iso_week": iso_week,
        "interval_start": first.isoformat(),
        "interval_end": last.isoformat(),
        "measured_on": None,
        "measured_at": None,
        "biological_measurement_id": biological_measurement_id,
        "box_id": box_id,
        "thermal_zone_id": None,
        "probe_id": None,
        "evidence_count": evidence_count,
    }


def daily_temperature_point(
    *, reading_id: int, box_id: int, thermal_zone_id: int, day: str,
    average_temperature_c: str, min_temperature_c: str | None,
    max_temperature_c: str | None, measurement_count: int,
) -> dict:
    """Project a stored daily aggregate, without asserting its acquisition source."""
    parsed_day = date.fromisoformat(day)
    if parsed_day.isoformat() != day:
        raise ValueError("An ISO calendar date is required.")
    temperature_decimal(average_temperature_c)
    for value in (min_temperature_c, max_temperature_c):
        if value is not None:
            temperature_decimal(value)
    if type(measurement_count) is not int or measurement_count < 0:
        raise ValueError("A stored nonnegative measurement count is required.")
    return {
        "id": reading_id, "key": f"daily_aggregate:{reading_id}",
        "source_kind": "daily_aggregate", "temporal_precision": "calendar_day",
        "statistic": "daily_mean", "temperature_c": average_temperature_c, "unit": "degC",
        "interval_start": day, "interval_end": day,
        "measured_on": day, "measured_at": None,
        "biological_measurement_id": None, "box_id": box_id,
        "thermal_zone_id": thermal_zone_id, "probe_id": None,
        "min_temperature_c": min_temperature_c, "max_temperature_c": max_temperature_c,
        "measurement_count": measurement_count, "acquisition_origin": "not_recorded",
    }


def probe_temperature_point(
    *, reading_id: int, box_id: int, thermal_zone_id: int, probe_id: int,
    measured_at: str, temperature_c: str, source: str,
) -> dict:
    """Project a genuine probe record; a zone/Box match must be authorized upstream."""
    timestamp = datetime.fromisoformat(measured_at)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("A genuine timestamp with an explicit offset is required.")
    temperature_decimal(temperature_c)
    return {
        "id": reading_id, "key": f"probe_observation:{reading_id}",
        "source_kind": "probe_observation", "temporal_precision": "timestamp",
        "statistic": "individual_reading", "temperature_c": temperature_c, "unit": "degC",
        "interval_start": None, "interval_end": None,
        "measured_on": None, "measured_at": measured_at,
        "biological_measurement_id": None, "box_id": box_id,
        "thermal_zone_id": thermal_zone_id, "probe_id": probe_id, "source": source,
    }
