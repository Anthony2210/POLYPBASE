"""Read side of collected probe temperatures: zone status and chart series."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db.models import (
    DateTimeField, DurationField, ExpressionWrapper, F, IntegerField, OuterRef, Subquery, Value,
)
from django.db.models.functions import Cast
from django.utils import timezone

from apps.cultures.models import ThermalZone

from .models import ProbeTemperatureBucket, TemperatureMeasurement
from .probe_collection import collected_probes

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


def _zone_series_rows(probe_ids, start, end):
    """Read both storage tiers in one statement, without any row lock.

    Condensation moves readings between tiers inside one transaction. A single
    statement sees one snapshot, so each reading comes from exactly one tier
    and reads neither wait for collection or manual entry nor delay them.
    """
    if not probe_ids:
        return []
    buckets = overlapping_probe_buckets(start, end).filter(
        probe_id__in=probe_ids,
    ).order_by().annotate(
        is_bucket=Value(1, output_field=IntegerField()),
    ).values_list(
        "probe_id", "is_bucket", "period_start", "min_temperature_c", "temperature_sum_c",
        "max_temperature_c", "measurement_count", "period_minutes", "last_measured_at",
    )
    minutes = TemperatureMeasurement.objects.filter(
        probe_id__in=probe_ids, measured_at__gte=start, measured_at__lt=end,
    ).order_by().annotate(
        is_bucket=Value(0, output_field=IntegerField()),
        instant=F("measured_at"), low=F("temperature_c"), total=F("temperature_c"),
        high=F("temperature_c"), samples=Value(1, output_field=IntegerField()),
        stored_minutes=Value(0, output_field=IntegerField()), last_at=F("measured_at"),
    ).values_list(
        "probe_id", "is_bucket", "instant", "low", "total", "high", "samples", "stored_minutes", "last_at",
    )
    return list(buckets.union(minutes, all=True))


def _split_tiers(rows):
    tiers = defaultdict(lambda: ([], []))
    for probe_id, is_bucket, instant, low, total, high, count, stored_minutes, last_at in rows:
        minutes, buckets = tiers[probe_id]
        if is_bucket:
            buckets.append((instant, low, total, high, count, stored_minutes, last_at))
        else:
            minutes.append((instant, total))
    return tiers


def zone_probe_series(probes, start, end, *, group_sources=False):
    """Return one common resolution and each probe's series from a single snapshot."""
    tiers = _split_tiers(_zone_series_rows([probe.pk for probe in probes], start, end))
    resolution = max(
        [series_resolution_minutes(start, end)]
        + [bucket[5] for _minutes, buckets in tiers.values() for bucket in buckets]
    )
    return resolution, {
        probe.pk: _build_series(*tiers[probe.pk], start, end, resolution, group_sources)
        for probe in probes
    }


def probe_series_with_sources(probe, start, end, resolution_minutes, *, group_sources=False):
    """Return legacy points and parallel lists of their exact storage sources.

    Sources are [start_ms, end_ms_or_none, sum_string, count, min, max, last_ms].
    Raw instants lie in [start, end); buckets intersect it by their full stored
    interval. Bucket statistics are never prorated or assigned sample instants.
    With ``group_sources``, each coarser group has one source with the same
    exact statistics, so a long period does not transfer every raw reading.
    """
    zone = ThermalZone.objects.get(pk=probe.thermal_zone_id, organization_id=probe.organization_id)
    collected_probes().get(pk=probe.pk, thermal_zone=zone, organization_id=zone.organization_id)
    minutes, buckets = _split_tiers(_zone_series_rows([probe.pk], start, end))[probe.pk]
    return _build_series(minutes, buckets, start, end, resolution_minutes, group_sources)


def _build_series(minutes, buckets, start, end, resolution_minutes, group_sources):
    minutes = sorted(minutes, key=lambda row: row[0])
    buckets = sorted(buckets, key=lambda row: row[0])
    # Direct callers also respect the effective storage resolution. API callers
    # already pass the common resolution computed from the same snapshot.
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
            point_sources.append(
                [_merged_group_source(period_start, resolution_minutes, group, start, end)]
                if group_sources else sorted(group["sources"], key=lambda source: source[0])
            )
    return points, point_sources


def _merged_group_source(period_start, resolution_minutes, group, start, end):
    """Summarize one group: raw readings stay within the request, buckets stay whole."""
    period_end = period_start + timedelta(minutes=resolution_minutes)
    raw_start = int(max(period_start, start).timestamp() * 1000)
    raw_end = int(min(period_end, end).timestamp() * 1000)
    sources = group["sources"]
    return [
        min(raw_start if source[1] is None else source[0] for source in sources),
        max(raw_end if source[1] is None else source[1] for source in sources),
        str(group["total"]), group["count"], _number(group["min"]), _number(group["max"]),
        max(source[6] for source in sources),
    ]
