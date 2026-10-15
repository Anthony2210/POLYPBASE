"""Daily temperatures supported by a box's recorded occupancy."""

from datetime import timezone as datetime_timezone

from django.db.models import Exists, OuterRef, Q, Value
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from apps.measurements.models import DailyTemperature

from .models import BoxLocation


MAX_TEMPERATURE_RANGE_DAYS = 366


def box_temperature_queryset(box, start, end):
    """Use EXISTS to avoid duplicates and queries per location, even on revisits."""
    # Preserve the detail serializer's UTC datetime.date() boundaries, rather
    # than silently changing historical attribution to the active timezone.
    occupancy = BoxLocation.objects.filter(
        box_id=box.pk,
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


def temperature_history_point(temperature):
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
