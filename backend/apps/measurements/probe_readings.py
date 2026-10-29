"""Read side of collected probe temperatures: zone status and chart series."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import (
    DateTimeField, DurationField, ExpressionWrapper, F, IntegerField, Max, Min, OuterRef,
    Subquery, Value,
)
from django.db.models.functions import Cast
from django.utils import timezone

from apps.cultures.models import ThermalZone

from .models import ProbeTemperatureBucket, TemperatureMeasurement
from .probe_collection import collected_probes, lock_probe_zone

RAW_MAX_SPAN = timedelta(days=2)
QUARTER_HOUR_MAX_SPAN = timedelta(days=31)
MAX_SERIES_SPAN = timedelta(days=366)
TWO_PLACES = Decimal("0.01")


def zone_probe_monitoring(zone, *, last_reading_at=None, now=None):
    """Return the server-authoritative probe status of one thermal zone.

    ``manual_entry_available`` is False only while the zone has an active
    collected probe whose latest reading is recent enough.
    """
    probes = [
        probe for probe in zone.probes.all()
        if probe.is_active and probe.is_collected
        and probe.probe_type == probe.ProbeType.IMINILIDE
        and probe.organization_id == zone.organization_id
    ]
    if not probes:
        return {
            "has_collected_probes": False,
            "last_reading_at": None,
            "manual_entry_available": True,
            "fallback_after_hours": settings.PROBE_MANUAL_FALLBACK_HOURS,
        }
    if last_reading_at is None:
        # Zone summaries prefetch each probe's latest per-minute reading.
        prefetched = [getattr(probe, "latest_reading_at", None) for probe in probes]
        if all(hasattr(probe, "latest_reading_at") for probe in probes) and any(prefetched):
            last_reading_at = max(value for value in prefetched if value)
        else:
            last_reading_at = latest_zone_reading_at(zone.id, organization_id=zone.organization_id)
    now = now or timezone.now()
    fallback = timedelta(hours=settings.PROBE_MANUAL_FALLBACK_HOURS)
    is_stale = last_reading_at is None or now - last_reading_at > fallback
    return {
        "has_collected_probes": True,
        "last_reading_at": last_reading_at,
        "manual_entry_available": is_stale,
        "fallback_after_hours": settings.PROBE_MANUAL_FALLBACK_HOURS,
    }


def latest_zone_reading_at(zone_id, *, organization_id):
    # Both storage tiers are observed in one SQL statement snapshot.
    readings = collected_probes().filter(
        thermal_zone_id=zone_id, organization_id=organization_id, is_active=True,
    ).annotate(
        latest_minute=Subquery(
            TemperatureMeasurement.objects.filter(probe_id=OuterRef("pk"))
            .order_by("-measured_at").values("measured_at")[:1]
        ),
        latest_bucket=Subquery(
            ProbeTemperatureBucket.objects.filter(probe_id=OuterRef("pk"))
            .order_by("-last_measured_at").values("last_measured_at")[:1]
        ),
    ).values_list("latest_minute", "latest_bucket")
    candidates = [instant for pair in readings for instant in pair if instant is not None]
    return max(candidates) if candidates else None


def series_resolution_minutes(start, end, *, now=None):
    span = end - start
    if span > QUARTER_HOUR_MAX_SPAN:
        return 60
    retention_start = (now or timezone.now()) - timedelta(days=settings.PROBE_READING_RETENTION_DAYS)
    if span > RAW_MAX_SPAN or start < retention_start:
        return max(15, settings.PROBE_BUCKET_MINUTES)
    return 1


def _period_start(moment, minutes):
    epoch = int(moment.timestamp())
    size = minutes * 60
    return datetime.fromtimestamp(epoch - epoch % size, tz=datetime_timezone.utc)


def _number(value):
    return float(Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP))


def overlapping_probe_buckets(start, end):
    """Select stored intervals intersecting the half-open request, on either DB."""
    duration = ExpressionWrapper(
        Cast("period_minutes", IntegerField()) * Value(timedelta(minutes=1)),
        output_field=DurationField(),
    )
    return ProbeTemperatureBucket.objects.alias(
        period_end=ExpressionWrapper(
            F("period_start") + duration, output_field=DateTimeField(),
        ),
    ).filter(period_start__lt=end, period_end__gt=start)


def probe_series(probe, start, end, resolution_minutes):
    """Keep the points-only contract for existing direct callers."""
    return probe_series_with_sources(probe, start, end, resolution_minutes)[0]


@transaction.atomic
def probe_series_with_sources(probe, start, end, resolution_minutes):
    """Return legacy points and parallel lists of their exact storage sources.

    Sources are [start_ms, end_ms_or_none, sum_string, count, min, max, last_ms].
    Raw instants lie in [start, end); buckets intersect it by their full stored
    interval. Bucket statistics are never prorated or assigned sample instants.
    """
    lock_probe_zone(probe)
    minutes = TemperatureMeasurement.objects.filter(
        probe=probe, measured_at__gte=start, measured_at__lt=end
    ).order_by("measured_at").values_list("measured_at", "temperature_c")
    buckets = overlapping_probe_buckets(start, end).filter(
        probe=probe,
    ).order_by("period_start").values_list(
        "period_start", "min_temperature_c", "temperature_sum_c", "max_temperature_c",
        "measurement_count", "period_minutes", "last_measured_at",
    )

    # Direct callers also respect the effective storage resolution. API callers
    # select one common resolution for every probe while holding the zone lock.
    buckets = list(buckets)
    if buckets:
        resolution_minutes = max(resolution_minutes, max(row[5] for row in buckets))
    if resolution_minutes == 1 and not buckets:
        points, point_sources = [], []
        for measured_at, value in minutes:
            instant = int(measured_at.timestamp() * 1000)
            number = _number(value)
            points.append([instant, number, None, None])
            point_sources.append([[instant, None, str(value), 1, number, number, instant]])
        return points, point_sources

    groups = defaultdict(lambda: {
        "min": None, "max": None, "total": Decimal(0), "count": 0, "sources": [],
    })
    for measured_at, value in minutes:
        group = groups[_period_start(measured_at, resolution_minutes)]
        value = Decimal(value)
        group["min"] = value if group["min"] is None else min(group["min"], value)
        group["max"] = value if group["max"] is None else max(group["max"], value)
        group["total"] += value
        group["count"] += 1
        instant = int(measured_at.timestamp() * 1000)
        group["sources"].append([
            instant, None, str(value), 1, _number(value), _number(value), instant,
        ])
    for period_start, minimum, total, maximum, count, stored_minutes, last_measured_at in buckets:
        group = groups[_period_start(period_start, resolution_minutes)]
        minimum, maximum = Decimal(minimum), Decimal(maximum)
        group["min"] = minimum if group["min"] is None else min(group["min"], minimum)
        group["max"] = maximum if group["max"] is None else max(group["max"], maximum)
        group["total"] += Decimal(total)
        group["count"] += count
        group["sources"].append([
            int(period_start.timestamp() * 1000),
            int((period_start + timedelta(minutes=stored_minutes)).timestamp() * 1000),
            str(total), count, _number(minimum), _number(maximum),
            int(last_measured_at.timestamp() * 1000),
        ])

    points, point_sources = [], []
    for period_start, group in sorted(groups.items()):
        if group["count"]:
            points.append([
                int(period_start.timestamp() * 1000),
                _number(group["total"] / group["count"]),
                _number(group["min"]),
                _number(group["max"]),
            ])
            point_sources.append(sorted(group["sources"], key=lambda source: source[0]))
    return points, point_sources


@transaction.atomic
def zone_reading_extent(zone):
    """Return the first and last instants in this organization's zone."""
    ThermalZone.objects.select_for_update().get(pk=zone.pk, organization_id=zone.organization_id)
    probe_ids = collected_probes().filter(
        thermal_zone=zone, organization_id=zone.organization_id,
    ).values_list("pk", flat=True)
    first_minute = TemperatureMeasurement.objects.filter(probe_id__in=probe_ids).aggregate(
        first=Min("measured_at"), last=Max("measured_at")
    )
    first_bucket = ProbeTemperatureBucket.objects.filter(probe_id__in=probe_ids).aggregate(
        first=Min("period_start"), last=Max("last_measured_at")
    )
    firsts = [value for value in (first_minute["first"], first_bucket["first"]) if value]
    lasts = [value for value in (first_minute["last"], first_bucket["last"]) if value]
    return (min(firsts) if firsts else None, max(lasts) if lasts else None)
