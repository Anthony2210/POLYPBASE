import csv
from collections import Counter
from datetime import timedelta
from io import StringIO

from django.db.models import Prefetch
from django.utils import timezone

from apps.cultures.models import BoxLocation
from apps.measurements.models import BiologicalMeasurement, DailyTemperature


def build_weekly_measurement_csv(*, boxes, date_from=None, date_to=None):
    """Build a weekly wide CSV similar to the historical tracking workbooks."""
    selected_boxes = list(
        boxes.select_related(
            "organization",
            "strain",
            "strain__species",
            "thermal_zone",
        ).prefetch_related(
            Prefetch(
                "locations",
                queryset=BoxLocation.objects.select_related("thermal_zone"),
            )
        )
    )
    export_codes = _build_export_codes(selected_boxes)
    selected_boxes.sort(key=lambda box: export_codes[box.id])

    measurements = BiologicalMeasurement.objects.filter(
        box_id__in=[box.id for box in selected_boxes],
    ).select_related(
        "box",
        "box__thermal_zone",
    ).order_by(
        "measured_on",
        "created_at",
    )

    if date_from:
        measurements = measurements.filter(measured_on__gte=date_from)
    if date_to:
        measurements = measurements.filter(measured_on__lte=date_to)

    measurement_list = list(measurements)
    measurement_by_week_and_box = {}
    for measurement in measurement_list:
        week_key = _iso_week_key(measurement.measured_on)
        measurement_by_week_and_box[(week_key, measurement.box_id)] = measurement

    effective_start, effective_end = _get_effective_period(
        measurement_list=measurement_list,
        date_from=date_from,
        date_to=date_to,
    )
    week_keys = _iter_iso_weeks(effective_start, effective_end)

    zone_ids = {
        zone_id
        for box in selected_boxes
        for zone_id in _box_zone_ids(box)
    }
    temperature_dates = {
        measurement.measured_on
        for measurement in measurement_list
    }
    daily_temperatures = {
        (temperature.thermal_zone_id, temperature.date): temperature.average_temperature_c
        for temperature in DailyTemperature.objects.filter(
            thermal_zone_id__in=zone_ids,
            date__in=temperature_dates,
        )
    }

    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    header = ["Date", "année", "semaines cumulées", "semaines"]
    for box in selected_boxes:
        code = export_codes[box.id]
        header.extend(
            [
                "numéro de boite",
                f"{code}_polypes",
                f"{code}_ephyrules",
                f"{code}_temperature",
            ]
        )
    writer.writerow(header)

    for cumulative_week, week_key in enumerate(week_keys, start=1):
        row = [
            f"{week_key[0]}_S{week_key[1]}",
            week_key[0],
            cumulative_week,
            week_key[1],
        ]
        for box in selected_boxes:
            code = export_codes[box.id]
            measurement = measurement_by_week_and_box.get((week_key, box.id))
            if measurement is None:
                row.extend([code, "", "", ""])
                continue

            temperature = _measurement_temperature(
                box=box,
                measured_on=measurement.measured_on,
                daily_temperatures=daily_temperatures,
            )
            row.extend(
                [
                    code,
                    measurement.polyp_count,
                    measurement.ephyrae_count,
                    _format_decimal(temperature),
                ]
            )
        writer.writerow(row)

    return output.getvalue(), {
        "box_count": len(selected_boxes),
        "measurement_count": len(measurement_list),
        "week_count": len(week_keys),
        "date_from": effective_start,
        "date_to": effective_end,
    }


def build_weekly_measurement_preview(*, boxes, date_from=None, date_to=None):
    """Build aggregated chart data from the same selection as the CSV export."""
    csv_content, metadata = build_weekly_measurement_csv(
        boxes=boxes,
        date_from=date_from,
        date_to=date_to,
    )
    rows = list(csv.reader(StringIO(csv_content)))
    if not rows:
        return {"points": [], "metadata": metadata}

    header = rows[0]
    polyp_columns = [
        index for index, column in enumerate(header) if column.endswith("_polypes")
    ]
    ephyrae_columns = [
        index for index, column in enumerate(header) if column.endswith("_ephyrules")
    ]
    temperature_columns = [
        index for index, column in enumerate(header) if column.endswith("_temperature")
    ]

    points = []
    for row in rows[1:]:
        if not row:
            continue

        polyp_total = sum(_parse_int(row, index) for index in polyp_columns)
        ephyrae_total = sum(_parse_int(row, index) for index in ephyrae_columns)
        temperatures = [
            value
            for value in (_parse_float(row, index) for index in temperature_columns)
            if value is not None
        ]
        measurement_count = sum(
            1
            for index in polyp_columns
            if index < len(row) and row[index] not in ("", None)
        )

        points.append(
            {
                "label": row[0],
                "polyp_count": polyp_total,
                "ephyrae_count": ephyrae_total,
                "average_temperature_c": (
                    round(sum(temperatures) / len(temperatures), 2)
                    if temperatures
                    else None
                ),
                "measurement_count": measurement_count,
            }
        )

    return {"points": points, "metadata": metadata}


def _build_export_codes(boxes):
    candidate_codes = {
        box.id: _normalize_short_box_code(box.local_code) or _derive_short_box_code(box)
        for box in boxes
    }
    code_counts = Counter(code for code in candidate_codes.values() if code)
    return {
        box.id: (
            candidate_codes[box.id]
            if candidate_codes[box.id] and code_counts[candidate_codes[box.id]] == 1
            else box.global_code
        )
        for box in boxes
    }


def _normalize_short_box_code(value):
    code = str(value or "").strip()
    if not code:
        return ""
    parts = code.split(".")
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        return f"{int(parts[0])}.{int(parts[1]):02d}"
    return ""


def _derive_short_box_code(box):
    strain_number = box.strain.number or _extract_last_number(box.strain.code)
    box_number = _extract_last_number(box.box_number)
    if strain_number is None or box_number is None:
        return ""
    return f"{strain_number}.{box_number:02d}"


def _extract_last_number(value):
    digits = ""
    for character in reversed(str(value or "")):
        if character.isdigit():
            digits = character + digits
        elif digits:
            break
    return int(digits) if digits else None


def _get_effective_period(*, measurement_list, date_from, date_to):
    measurement_dates = [measurement.measured_on for measurement in measurement_list]
    first_measurement = min(measurement_dates) if measurement_dates else None
    last_measurement = max(measurement_dates) if measurement_dates else None

    effective_start = date_from or first_measurement or date_to
    effective_end = date_to or last_measurement or date_from
    return effective_start, effective_end


def _iter_iso_weeks(start_date, end_date):
    if not start_date or not end_date:
        return []

    current_monday = start_date - timedelta(days=start_date.weekday())
    end_monday = end_date - timedelta(days=end_date.weekday())
    week_keys = []

    while current_monday <= end_monday:
        week_keys.append(_iso_week_key(current_monday))
        current_monday += timedelta(days=7)

    return week_keys


def _iso_week_key(value):
    iso_calendar = value.isocalendar()
    return iso_calendar.year, iso_calendar.week


def _box_zone_ids(box):
    zone_ids = {box.thermal_zone_id} if box.thermal_zone_id else set()
    zone_ids.update(location.thermal_zone_id for location in box.locations.all())
    return zone_ids


def _measurement_temperature(*, box, measured_on, daily_temperatures):
    thermal_zone = _thermal_zone_for_date(box, measured_on)
    if thermal_zone is None:
        return None

    measured_temperature = daily_temperatures.get((thermal_zone.id, measured_on))
    if measured_temperature is not None:
        return measured_temperature
    return thermal_zone.target_temperature_c


def _thermal_zone_for_date(box, measured_on):
    for location in box.locations.all():
        starts_on = _local_date(location.starts_at)
        ends_on = _local_date(location.ends_at) if location.ends_at else None
        if starts_on <= measured_on and (ends_on is None or measured_on <= ends_on):
            return location.thermal_zone
    return box.thermal_zone


def _local_date(value):
    if timezone.is_aware(value):
        return timezone.localtime(value).date()
    return value.date()


def _format_decimal(value):
    if value is None:
        return ""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _parse_int(row, index):
    if index >= len(row) or row[index] == "":
        return 0
    try:
        return int(row[index])
    except ValueError:
        return 0


def _parse_float(row, index):
    if index >= len(row) or row[index] == "":
        return None
    try:
        return float(row[index])
    except ValueError:
        return None
