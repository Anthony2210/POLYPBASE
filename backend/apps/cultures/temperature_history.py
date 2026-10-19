"""Daily temperatures supported by a box's recorded occupancy."""

from datetime import timedelta, timezone as datetime_timezone

from django.db.models import Count, Exists, OuterRef, Q, Value
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from apps.measurements.models import DailyTemperature, HistoricalTemperatureReading
from apps.measurements.temperature_series import historical_temperature_point

from .models import Box, BoxLocation


MAX_TEMPERATURE_RANGE_DAYS = 366


def identity_box_ids(box):
    """Return the canonical box and the retired same-institution identities merged into it."""
    merged = Box.objects.filter(merged_into_id=box.pk, organization_id=box.organization_id)
    return [box.pk, *merged.order_by("pk").values_list("pk", flat=True)]


def box_temperature_queryset(box, start, end):
    """Use EXISTS to avoid duplicates and queries per location, even on revisits.

    Daily temperatures belong to thermal zones. A zone temperature is listed
    only when a recorded period of this culture, or of a retired identity merged
    into it, covers that day; no temperature is ever created for a period.
    """
    # Preserve the detail serializer's UTC datetime.date() boundaries, rather
    # than silently changing historical attribution to the active timezone.
    occupancy = BoxLocation.objects.filter(
        Q(box_id=box.pk) | Q(box__merged_into_id=box.pk),
        box__organization_id=box.organization_id,
        thermal_zone__organization_id=box.organization_id,
        thermal_zone_id=OuterRef("thermal_zone_id"),
    ).annotate(
        start_day=TruncDate("starts_at", tzinfo=datetime_timezone.utc),
        end_day=Coalesce(
                    TruncDate("ends_at", tzinfo=datetime_timezone.utc),
                    Value(timezone.localdate()),
                ),
    ).filter(start_day__lte=OuterRef("date")).filter(
        Q(end_date_unknown=True, start_day=OuterRef("date"))
        | Q(end_date_unknown=False, end_day__gte=OuterRef("date"))
    )
    return DailyTemperature.objects.filter(
        thermal_zone__organization_id=box.organization_id,
        date__range=(start, end),
    ).filter(Exists(occupancy)).select_related("thermal_zone").order_by("date", "thermal_zone_id")


def _recorded_code(box):
    return box.previous_global_code or box.global_code


def _utc_day(value):
    return value.astimezone(datetime_timezone.utc).date() if value is not None else None


def temperature_support_periods(box):
    """Recorded periods per identity; each keeps the box record it came from."""
    ids = identity_box_ids(box)
    boxes = {row.pk: row for row in Box.objects.filter(pk__in=ids, organization_id=box.organization_id)}
    today = timezone.localdate()
    periods = []
    for location in BoxLocation.objects.filter(
        box_id__in=ids, thermal_zone__organization_id=box.organization_id,
    ).select_related("thermal_zone").order_by("starts_at", "pk"):
        start = _utc_day(location.starts_at)
        unknown = location.end_date_unknown
        periods.append({
            "location_id": location.pk, "box_id": location.box_id,
            "box_code": _recorded_code(boxes[location.box_id]),
            "zone_id": location.thermal_zone_id, "zone_name": location.thermal_zone.name,
            "start": start, "end_unknown": unknown,
            # An unknown end proves only the recorded start day.
            "last_proven_day": start if unknown else (_utc_day(location.ends_at) or today),
            "open_ended": unknown or location.ends_at is None,
        })
    return periods


def location_provenance_conflicts(box):
    """List different-zone periods of the merged identities that may overlap.

    Both histories are retained as recorded. Nothing here decides which zone
    was the physical one; an unknown end is treated as possibly still open.
    """
    periods = temperature_support_periods(box)
    conflicts = []
    for index, first in enumerate(periods):
        for second in periods[index + 1:]:
            if first["box_id"] == second["box_id"] or first["zone_id"] == second["zone_id"]:
                continue
            first_end = None if first["open_ended"] else first["last_proven_day"]
            second_end = None if second["open_ended"] else second["last_proven_day"]
            overlaps = (
                (first_end is None or second["start"] <= first_end)
                and (second_end is None or first["start"] <= second_end)
            )
            if overlaps:
                conflicts.append({
                    "locations": [
                        {key: period[key] for key in ("location_id", "box_id", "box_code", "zone_id", "zone_name")}
                        | {"end_unknown": period["end_unknown"]}
                        for period in (first, second)
                    ],
                    "basis": "end_unknown" if first["end_unknown"] or second["end_unknown"] else "recorded",
                })
    return conflicts


def has_merged_identity(box):
    """True for a merge target or a retired identity; both always keep a previous code."""
    return bool(box.previous_global_code)


def temperature_history_point(temperature, support_periods=None):
    point = temperature_history_point_values(temperature)
    if support_periods is not None:
        point["supported_by"] = [
            {"location_id": period["location_id"], "box_id": period["box_id"], "box_code": period["box_code"]}
            for period in support_periods
            if period["zone_id"] == temperature.thermal_zone_id
            and period["start"] <= temperature.date
            and (temperature.date == period["start"] if period["end_unknown"]
                 else temperature.date <= period["last_proven_day"])
        ]
    return point


def temperature_history_point_values(temperature):
    return {
        "date": temperature.date,
        "average_temperature_c": float(temperature.average_temperature_c),
        "min_temperature_c": (
            float(temperature.min_temperature_c)
            if temperature.min_temperature_c is not None else None
        ),
        "max_temperature_c": (
            float(temperature.max_temperature_c)
            if temperature.max_temperature_c is not None else None
        ),
        "zone_id": temperature.thermal_zone_id,
        "zone_name": temperature.thermal_zone.name,
    }


def box_historical_temperature_queryset(box, start, end):
    """Week-precision readings whose ISO week overlaps the inclusive range.

    Scope comes from the biological measurement's Box, never a client id.
    Pending associations are excluded from the confirmed series.
    """
    first_week = start - timedelta(days=start.weekday())
    return HistoricalTemperatureReading.objects.filter(
        biological_measurement__box_id=box.pk,
        biological_measurement__duplicate_of__isnull=True,
        biological_measurement__box__merged_into__isnull=True,
        biological_measurement__box__organization_id=box.organization_id,
        association_status=HistoricalTemperatureReading.AssociationStatus.CONFIRMED,
        biological_measurement__week_start__gte=first_week,
        biological_measurement__week_start__lte=end,
    ).annotate(evidence_total=Count("evidences")).select_related(
        "biological_measurement",
    ).order_by("biological_measurement__week_start", "pk")


def historical_temperature_history_point(reading):
    return historical_temperature_point(
        reading_id=reading.pk,
        biological_measurement_id=reading.biological_measurement_id,
        box_id=reading.biological_measurement.box_id,
        iso_year=reading.iso_year,
        iso_week=reading.iso_week,
        temperature_c=reading.temperature_c,
        evidence_count=reading.evidence_total,
        association_status=reading.association_status,
    )
