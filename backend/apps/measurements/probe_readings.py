"""Read side of collected probe temperatures: zone status and chart series."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import ROUND_HALF_UP, Decimal, localcontext
from math import lcm

from django.conf import settings
from django.db import connection
from django.db.models import (
    Count, DateTimeField, DurationField, ExpressionWrapper, F, Func, IntegerField, Max, Min, OuterRef,
    Subquery, Sum, Value,
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
# Storage tiers in the single series statement, then the zone average rows.
RAW_ROW, BUCKET_ROW, RAW_GROUP_ROW, AVERAGE_ROW = 0, 1, 2, 3
# Product decision of 10 November 2026: an inter-probe average needs two probes.
MIN_AVERAGE_PROBES = 2
# Decimal places kept by intermediate means, far below the displayed 0.01.
AVERAGE_SCALE = 30


class EpochPeriodStart(Func):
    """PostgreSQL start of the fixed UTC-epoch period containing an instant.

    Same boundaries as ``_period_start``: floor(epoch / size) * size.
    """

    template = "to_timestamp(floor(extract(epoch from %(expressions)s) / %(seconds)s) * %(seconds)s)"
    output_field = DateTimeField()

    def __init__(self, expression, minutes):
        super().__init__(expression, seconds=int(minutes) * 60)


def sql_grouping_available(group_sources, resolution_minutes):
    """Group raw readings in the database only when the result is exactly equivalent.

    Only grouped responses (one merged source per period) can come from SQL
    groups. PostgreSQL sums ``numeric`` values exactly; SQLite would use
    floating point, so it keeps the Python reference path.
    """
    return bool(group_sources) and resolution_minutes > 1 and connection.vendor == "postgresql"


# The zone average reuses the series statement, so it sees the same snapshot
# as the probe series. Its period is the least common multiple of the requested
# resolution and every stored interval resolution read, so a stored interval
# always fits inside exactly one period and is never split or misattributed.
AVERAGE_SQL = """
WITH RECURSIVE
request (requested_seconds) AS (VALUES (%s::integer)),
buckets (probe_id, instant, low, total, high, samples, stored_minutes, last_at) AS (
    {buckets}
),
raw (probe_id, instant, value) AS (
    {raw}
),
resolutions (minutes) AS (
    SELECT DISTINCT minutes FROM (
        SELECT stored_minutes AS minutes FROM buckets WHERE stored_minutes > 0
        UNION ALL
        SELECT requested_seconds / 60 FROM request
    ) AS candidates
),
numbered (rn, minutes) AS (
    SELECT row_number() OVER (ORDER BY minutes), minutes FROM resolutions
),
lcm (rn, value) AS (
    SELECT 0::bigint, 1::bigint
    UNION ALL
    SELECT numbered.rn, (lcm.value * numbered.minutes) / gcd(lcm.value, numbered.minutes)
    FROM lcm JOIN numbered ON numbered.rn = lcm.rn + 1
),
period (seconds) AS (
    SELECT (SELECT value FROM lcm ORDER BY rn DESC LIMIT 1) * 60
),
raw_tier (probe_id, kind, instant, low, total, high, samples, stored_minutes, last_at) AS (
    {raw_tier}
),
tier_rows (probe_id, kind, instant, low, total, high, samples, stored_minutes, last_at) AS (
    SELECT probe_id, {bucket_kind}, instant, low, total, high, samples, stored_minutes, last_at FROM buckets
    UNION ALL
    SELECT probe_id, kind, instant, low, total, high, samples, stored_minutes, last_at FROM raw_tier
),
probe_periods AS (
    -- Level 1: one exact mean per probe and period, weighted by real counts.
    SELECT tier_rows.probe_id,
           floor(extract(epoch FROM tier_rows.instant) / period.seconds) AS period_index,
           CAST(SUM(tier_rows.total) AS numeric(60, {scale})) / SUM(tier_rows.samples) AS probe_mean
    FROM tier_rows CROSS JOIN period
    GROUP BY tier_rows.probe_id, floor(extract(epoch FROM tier_rows.instant) / period.seconds)
),
zone_periods AS (
    -- Level 2: each contributing probe has the same weight.
    SELECT period_index, AVG(probe_mean) AS mean, COUNT(*) AS probes
    FROM probe_periods
    GROUP BY period_index
    HAVING COUNT(*) >= %s
)
SELECT probe_id, kind, instant, low, total, high, samples, stored_minutes, last_at FROM tier_rows
UNION ALL
SELECT NULL, {average_kind},
       to_timestamp((zone_periods.period_index * period.seconds)::double precision),
       NULL, zone_periods.mean, NULL, CAST(zone_periods.probes AS integer), 0, NULL
FROM zone_periods CROSS JOIN period
"""

# Raw readings of the same statement, grouped per period or kept individual.
RAW_GROUP_TIER = """
SELECT raw.probe_id, {raw_kind},
       to_timestamp(floor(extract(epoch FROM raw.instant) / period.seconds) * period.seconds),
       MIN(raw.value), SUM(raw.value), MAX(raw.value), COUNT(*), 0, MAX(raw.instant)
FROM raw CROSS JOIN period
GROUP BY raw.probe_id, to_timestamp(floor(extract(epoch FROM raw.instant) / period.seconds) * period.seconds)
"""
RAW_ROW_TIER = """
SELECT raw.probe_id, {raw_kind}, raw.instant, raw.value, raw.value, raw.value, 1, 0, raw.instant FROM raw
"""


def sql_average_available():
    """PostgreSQL computes the zone average; SQLite keeps the Python reference."""
    return connection.vendor == "postgresql"


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


def _zone_series_rows(probe_ids, start, end, group_minutes=None, average_minutes=None):
    """Read both storage tiers in one statement, without any row lock.

    Condensation moves readings between tiers inside one transaction. A single
    statement sees one snapshot, so each reading comes from exactly one tier
    and reads neither wait for collection or manual entry nor delay them.

    With ``group_minutes``, raw readings arrive already grouped per period:
    exact sum, count, minimum, maximum and latest measured instant, instead of
    one row per reading. Stored intervals are always returned whole.

    With ``average_minutes`` (PostgreSQL only), the same statement also returns
    the zone average rows: probe_id NULL, kind AVERAGE_ROW, period start, mean
    in ``total`` and the number of contributing probes in ``samples``. The
    period is the least common multiple of the requested resolution and every
    stored interval resolution, so a stored interval is never split.
    """
    if not probe_ids:
        return []
    buckets = overlapping_probe_buckets(start, end).filter(probe_id__in=probe_ids).order_by()
    raw = TemperatureMeasurement.objects.filter(
        probe_id__in=probe_ids, measured_at__gte=start, measured_at__lt=end,
    ).order_by()
    if average_minutes:
        return _grouped_average_rows(
            buckets.values_list(
                "probe_id", "period_start", "min_temperature_c", "temperature_sum_c",
                "max_temperature_c", "measurement_count", "period_minutes", "last_measured_at",
            ),
            raw.values_list("probe_id", "measured_at", "temperature_c"),
            group_minutes, average_minutes,
        )
    bucket_rows = buckets.annotate(
        is_bucket=Value(BUCKET_ROW, output_field=IntegerField()),
    ).values_list(
        "probe_id", "is_bucket", "period_start", "min_temperature_c", "temperature_sum_c",
        "max_temperature_c", "measurement_count", "period_minutes", "last_measured_at",
    )
    if group_minutes:
        minutes = raw.annotate(
            group_start=EpochPeriodStart("measured_at", group_minutes),
        ).values("probe_id", "group_start").annotate(
            is_bucket=Value(RAW_GROUP_ROW, output_field=IntegerField()),
            low=Min("temperature_c"), total=Sum("temperature_c"), high=Max("temperature_c"),
            samples=Count("pk"), stored_minutes=Value(0, output_field=IntegerField()),
            last_at=Max("measured_at"),
        ).values_list(
            "probe_id", "is_bucket", "group_start", "low", "total", "high", "samples", "stored_minutes", "last_at",
        )
    else:
        minutes = raw.annotate(
            is_bucket=Value(RAW_ROW, output_field=IntegerField()),
            instant=F("measured_at"), low=F("temperature_c"), total=F("temperature_c"),
            high=F("temperature_c"), samples=Value(1, output_field=IntegerField()),
            stored_minutes=Value(0, output_field=IntegerField()), last_at=F("measured_at"),
        ).values_list(
            "probe_id", "is_bucket", "instant", "low", "total", "high", "samples", "stored_minutes", "last_at",
        )
    return list(bucket_rows.union(minutes, all=True))


def _grouped_average_rows(buckets, raw, group_minutes, average_minutes):
    """One statement: both storage tiers, the period mean and the probe count.

    The stored intervals and the raw readings are read once, in the same
    snapshot, and the period is computed inside the statement. No second read
    can observe a different state, so a condensation committed meanwhile never
    mixes two snapshots.
    """
    buckets_sql, buckets_params = buckets.query.sql_with_params()
    raw_sql, raw_params = raw.query.sql_with_params()
    raw_tier = (RAW_GROUP_TIER if group_minutes else RAW_ROW_TIER).format(
        raw_kind=RAW_GROUP_ROW if group_minutes else RAW_ROW,
    )
    sql = AVERAGE_SQL.format(
        buckets=buckets_sql, raw=raw_sql, raw_tier=raw_tier,
        bucket_kind=BUCKET_ROW, scale=AVERAGE_SCALE, average_kind=AVERAGE_ROW,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            sql,
            [int(average_minutes) * 60, *buckets_params, *raw_params, MIN_AVERAGE_PROBES],
        )
        return cursor.fetchall()


def _split_tiers(rows):
    tiers = defaultdict(lambda: ([], [], []))
    for probe_id, kind, instant, low, total, high, count, stored_minutes, last_at in rows:
        if kind == AVERAGE_ROW:
            continue
        minutes, buckets, raw_groups = tiers[probe_id]
        if kind == BUCKET_ROW:
            buckets.append((instant, low, total, high, count, stored_minutes, last_at))
        elif kind == RAW_GROUP_ROW:
            raw_groups.append((instant, low, total, high, count, last_at))
        else:
            minutes.append((instant, total))
    return tiers


def zone_probe_series(probes, start, end, *, group_sources=False):
    """Return one common resolution and each probe's series from a single snapshot."""
    resolution, series, _average = _zone_probe_read(probes, start, end, group_sources, with_average=False)
    return resolution, series


def zone_probe_series_and_average(probes, start, end, *, group_sources=False):
    """Also return the inter-probe average of the same snapshot and periods."""
    return _zone_probe_read(probes, start, end, group_sources, with_average=True)


def _zone_probe_read(probes, start, end, group_sources, *, with_average):
    probe_ids = [probe.pk for probe in probes]
    requested = series_resolution_minutes(start, end)
    group_minutes = requested if sql_grouping_available(group_sources, requested) else None
    average_minutes = requested if with_average and sql_average_available() else None
    # One statement only: the period is the least common multiple of the
    # requested resolution and every stored interval resolution, so raw groups
    # and stored intervals always align with the displayed periods.
    rows = _zone_series_rows(probe_ids, start, end, group_minutes, average_minutes)
    tiers = _split_tiers(rows)
    resolution = _common_resolution(requested, tiers)
    series = {
        probe.pk: _build_series(*tiers[probe.pk], start, end, resolution, group_sources)
        for probe in probes
    }
    if not with_average:
        return resolution, series, None
    average = _sql_average(rows) if average_minutes else python_zone_average(tiers, resolution)
    return resolution, series, average


def _common_resolution(requested, tiers):
    """Least common multiple of the requested resolution and stored intervals.

    A stored interval is epoch-aligned to its own resolution, so a period that
    is a multiple of every stored resolution contains each interval whole: no
    interval is ever split between two displayed periods or misattributed to
    its start period.
    """
    resolution = requested
    for _minutes, buckets, _groups in tiers.values():
        for bucket in buckets:
            resolution = lcm(resolution, bucket[5])
    return resolution


def _average_point(period_start, mean, probes):
    """[period start in ms, mean in °C, contributing probes]; the period lasts one resolution."""
    return [int(period_start.timestamp() * 1000), _number(mean), probes]


def _sql_average(rows):
    return sorted(
        _average_point(instant, total, count)
        for _probe, kind, instant, _low, total, _high, count, _minutes, _last in rows
        if kind == AVERAGE_ROW
    )


def python_zone_average(tiers, resolution_minutes):
    """Reference path of the zone average (SQLite), equivalent to ``AVERAGE_SQL``.

    Each probe first gets one exact mean per period from its real readings,
    SQL groups and whole stored intervals (total sum / total count). Periods
    where at least two probes contribute then average these means with equal
    weights. A missing reading is never zero and nothing is interpolated.
    """
    periods = defaultdict(lambda: defaultdict(lambda: [Decimal(0), 0]))
    for probe_id, (minutes, buckets, raw_groups) in tiers.items():
        contributions = [(instant, Decimal(value), 1) for instant, value in minutes]
        contributions += [(instant, Decimal(total), count) for instant, _low, total, _high, count, _last in raw_groups]
        contributions += [
            (instant, Decimal(total), count)
            for instant, _low, total, _high, count, _minutes, _last in buckets
        ]
        for instant, total, count in contributions:
            accumulator = periods[_period_start(instant, resolution_minutes)][probe_id]
            accumulator[0] += total
            accumulator[1] += count
    average = []
    with localcontext() as context:
        context.prec = 60
        for period_start, probes in sorted(periods.items()):
            if len(probes) < MIN_AVERAGE_PROBES:
                continue
            means = [total / count for total, count in probes.values()]
            average.append(_average_point(period_start, sum(means, Decimal(0)) / len(means), len(means)))
    return average


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
    minutes, buckets, raw_groups = _split_tiers(_zone_series_rows([probe.pk], start, end))[probe.pk]
    return _build_series(minutes, buckets, raw_groups, start, end, resolution_minutes, group_sources)


def _build_series(minutes, buckets, raw_groups, start, end, resolution_minutes, group_sources):
    minutes = sorted(minutes, key=lambda row: row[0])
    buckets = sorted(buckets, key=lambda row: row[0])
    # Direct callers also respect the effective storage resolution. API callers
    # already pass the common resolution computed from the same snapshot.
    if buckets:
        resolution_minutes = max(resolution_minutes, max(row[5] for row in buckets))
    # SQL groups carry no individual reading, so they only serve merged sources.
    assert group_sources or not raw_groups
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
    for group_start, minimum, total, maximum, count, last_measured_at in raw_groups:
        # Raw readings grouped by PostgreSQL: same statistics, already summed exactly.
        group = groups[_period_start(group_start, resolution_minutes)]
        minimum, maximum = Decimal(minimum), Decimal(maximum)
        group["min"] = minimum if group["min"] is None else min(group["min"], minimum)
        group["max"] = maximum if group["max"] is None else max(group["max"], maximum)
        group["total"] += Decimal(total)
        group["count"] += count
        group["sources"].append([
            int(group_start.timestamp() * 1000), None, str(total), count,
            _number(minimum), _number(maximum), int(last_measured_at.timestamp() * 1000),
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
