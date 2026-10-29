"""Collection, daily aggregation and condensation of probe temperatures.

Business rules (decided with the product owner, 2026-10-29):

- every per-minute reading of a collected probe is stored as a
  ``TemperatureMeasurement``;
- the daily temperature of a zone is computed from all readings of its
  collected probes for that local day (both probes of a zone combined);
- a ``DailyTemperature`` holding any manual or historical value is never
  overwritten by probe collection;
- per-minute readings older than ``PROBE_READING_RETENTION_DAYS`` are condensed
  into ``ProbeTemperatureBucket`` rows (min, mean, max per period), then the
  per-minute rows are removed.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone as datetime_timezone
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Max, Min, Sum
from django.utils import timezone

from apps.cultures.models import ThermalZone

from .iminilide import IminilideClient, IminilideError, collection_window_start
from .models import DailyTemperature, Probe, ProbeTemperatureBucket, TemperatureMeasurement

logger = logging.getLogger(__name__)

IMINILIDE_SOURCE = "iminilide"
TWO_PLACES = Decimal("0.01")


def collected_probes():
    return Probe.objects.filter(
        probe_type=Probe.ProbeType.IMINILIDE,
        organization_id=F("thermal_zone__organization_id"),
        external_channel__isnull=False,
    ).exclude(external_serial="")


def lock_probe_zone(probe, *, require_active=False):
    """Inside atomic(), lock zone before probe and revalidate collection scope.

    Collection, condensation and series reads share the zone lock with manual
    entry. Restrict the probe lock to its own table, not joined organizations.
    """
    zone = ThermalZone.objects.select_for_update().get(
        pk=probe.thermal_zone_id, organization_id=probe.organization_id,
    )
    locked = collected_probes().select_for_update(of=("self",)).get(
        pk=probe.pk, thermal_zone=zone, organization_id=zone.organization_id,
    )
    if require_active and not locked.is_active:
        raise IminilideError("This probe was deactivated or replaced during collection; no readings were stored.")
    return zone


def _local_zone():
    return ZoneInfo(settings.TIME_ZONE)


def local_day_bounds(day):
    """Return the aware [start, end) of one local calendar day."""
    zone = _local_zone()
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return start, end


def _round(value):
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


@dataclass
class ProbeResult:
    probe_code: str
    created: int = 0
    skipped_existing: int = 0
    missing_values: int = 0
    out_of_range_values: int = 0
    invalid_rows: int = 0
    error: str = ""


@dataclass
class CollectionResult:
    probes: list = field(default_factory=list)
    days_recomputed: int = 0
    days_kept_manual: int = 0

    @property
    def created(self):
        return sum(result.created for result in self.probes)

    @property
    def failed(self):
        return [result for result in self.probes if result.error]


def channel_last_reading(probe, *, predecessors_only=False):
    """Use the exact latest instant across both tiers of a channel's history."""
    peers = collected_probes().filter(
        organization_id=probe.organization_id, probe_type=probe.probe_type,
        external_serial=probe.external_serial, external_channel=probe.external_channel,
    )
    if predecessors_only:
        peers = peers.exclude(pk=probe.pk)
    ids = peers.values("pk")
    minute = TemperatureMeasurement.objects.filter(probe_id__in=ids).aggregate(
        latest=Max("measured_at"),
    )["latest"]
    bucket = ProbeTemperatureBucket.objects.filter(probe_id__in=ids).aggregate(
        latest=Max("last_measured_at"),
    )["latest"]
    candidates = [instant for instant in (minute, bucket) if instant is not None]
    return max(candidates) if candidates else None


@transaction.atomic
def collection_start(probe, now=None):
    """Resume the channel history, rewinding DST only for timestamp parsing."""
    lock_probe_zone(probe, require_active=True)
    now = now or timezone.now()
    earliest = now - timedelta(days=settings.IMINILIDE_MAX_LOOKBACK_DAYS)
    latest = channel_last_reading(probe)
    start = earliest if latest is None or latest < earliest else latest
    # store_readings enforces the predecessor fence even after this DST rewind.
    return max(earliest, collection_window_start(start))


@transaction.atomic
def store_readings(probe, readings):
    """Insert and recompute atomically; return (created, present, day outcomes)."""
    zone = lock_probe_zone(probe, require_active=True)
    boundary = channel_last_reading(probe, predecessors_only=True)
    if boundary is not None:
        readings = [reading for reading in readings if reading.measured_at.astimezone(datetime_timezone.utc) > boundary]
    if not readings:
        return 0, 0, {}
    # Periods already condensed must not receive minutes again (double counting).
    latest_bucket = (
        ProbeTemperatureBucket.objects.filter(probe=probe)
        .order_by("-period_start")
        .values_list("period_start", "period_minutes")
        .first()
    )
    if latest_bucket is not None:
        condensed_until = latest_bucket[0] + timedelta(minutes=latest_bucket[1])
        readings = [reading for reading in readings if reading.measured_at >= condensed_until]
        if not readings:
            return 0, 0, {}
    by_instant = {}
    for reading in readings:
        # A duplicated instant in one answer keeps its first value.
        by_instant.setdefault(reading.measured_at.astimezone(datetime_timezone.utc), reading)
    instants = sorted(by_instant)
    existing = set(
        TemperatureMeasurement.objects.filter(
            probe=probe,
            measured_at__gte=instants[0],
            measured_at__lte=instants[-1],
        ).values_list("measured_at", flat=True)
    )
    existing = {value.astimezone(datetime_timezone.utc) for value in existing}
    new_rows = [
        TemperatureMeasurement(
            probe=probe,
            measured_at=instant,
            temperature_c=by_instant[instant].temperature_c,
            source=IMINILIDE_SOURCE,
        )
        for instant in instants
        if instant not in existing
    ]
    TemperatureMeasurement.objects.bulk_create(new_rows, batch_size=1000)
    # Replays also repair any pre-existing incomplete daily aggregate.
    days = {instant.astimezone(_local_zone()).date() for instant in instants}
    outcomes = {
        day: recompute_daily_temperature(zone.pk, day, organization_id=zone.organization_id)
        for day in sorted(days)
    }
    return len(new_rows), len(instants) - len(new_rows), outcomes


def _zone_day_statistics(zone, day):
    start, end = local_day_bounds(day)
    probe_ids = list(collected_probes().filter(
        thermal_zone=zone, organization_id=zone.organization_id,
    ).values_list("id", flat=True))
    if not probe_ids:
        return None
    minutes = TemperatureMeasurement.objects.filter(
        probe_id__in=probe_ids, measured_at__gte=start, measured_at__lt=end
    ).aggregate(
        minimum=Min("temperature_c"),
        maximum=Max("temperature_c"),
        total=Sum("temperature_c"),
        count=Count("id"),
    )
    minimum = minutes["minimum"]
    maximum = minutes["maximum"]
    total = Decimal(minutes["total"] or 0)
    count = minutes["count"] or 0
    buckets = ProbeTemperatureBucket.objects.filter(
        probe_id__in=probe_ids, period_start__gte=start, period_start__lt=end
    ).values_list("min_temperature_c", "temperature_sum_c", "max_temperature_c", "measurement_count")
    for bucket_min, bucket_sum, bucket_max, bucket_count in buckets:
        minimum = bucket_min if minimum is None else min(minimum, bucket_min)
        maximum = bucket_max if maximum is None else max(maximum, bucket_max)
        total += Decimal(bucket_sum)
        count += bucket_count
    if count == 0:
        return None
    return {
        "min_temperature_c": _round(Decimal(minimum)),
        "average_temperature_c": _round(total / Decimal(count)),
        "max_temperature_c": _round(Decimal(maximum)),
        "measurement_count": count,
    }


def recompute_daily_temperature(zone_id, day, *, organization_id):
    """Recompute one zone day from probes. Return 'updated', 'kept_manual' or 'empty'."""
    with transaction.atomic():
        # Same lock order as manual temperature entry: zone first, then the day.
        zone = ThermalZone.objects.select_for_update().get(pk=zone_id, organization_id=organization_id)
        daily = DailyTemperature.objects.select_for_update().filter(thermal_zone_id=zone_id, date=day).first()
        if daily is not None and not daily.computed_from_probes:
            return "kept_manual"
        values = _zone_day_statistics(zone, day)
        if values is None:
            return "empty"
        if daily is None:
            DailyTemperature.objects.create(
                thermal_zone_id=zone_id, date=day, computed_from_probes=True, **values
            )
        else:
            for name, value in values.items():
                setattr(daily, name, value)
            daily.save(update_fields=[*values.keys()])
    return "updated"


def collect_iminilide(*, client=None, serial=None, now=None, stdout=None):
    """Collect every active iMinilide probe and refresh affected daily temperatures."""
    probes = collected_probes().filter(is_active=True).select_related("thermal_zone").order_by(
        "external_serial", "external_channel"
    )
    if serial:
        probes = probes.filter(external_serial=serial)
    result = CollectionResult()
    probes = list(probes)
    if not probes:
        return result

    client = client or IminilideClient()
    outcomes = {}
    for probe in probes:
        probe_result = ProbeResult(probe_code=probe.code)
        result.probes.append(probe_result)
        try:
            parsed = client.fetch_readings(
                serial=probe.external_serial,
                channel=probe.external_channel,
                since=collection_start(probe, now=now),
            )
            created, existing, days = store_readings(probe, parsed.readings)
        except IminilideError as error:
            # One failing channel must not stop the others.
            probe_result.error = str(error)
            logger.warning("iMinilide collection failed for probe %s: %s", probe.pk, error)
            continue
        probe_result.missing_values = parsed.missing_values
        probe_result.out_of_range_values = parsed.out_of_range_values
        probe_result.invalid_rows = parsed.invalid_rows
        probe_result.created = created
        probe_result.skipped_existing = existing
        outcomes.update(((probe.thermal_zone_id, day), outcome) for day, outcome in days.items())

    for outcome in outcomes.values():
        if outcome == "updated":
            result.days_recomputed += 1
        elif outcome == "kept_manual":
            result.days_kept_manual += 1
    return result


@dataclass
class CondenseResult:
    buckets_created: int = 0
    buckets_merged: int = 0
    readings_condensed: int = 0


def _bucket_start(moment, minutes):
    epoch = int(moment.timestamp())
    size = minutes * 60
    return datetime.fromtimestamp(epoch - epoch % size, tz=datetime_timezone.utc)


def condense_probe_readings(*, now=None, retention_days=None, bucket_minutes=None, batch_days=1):
    """Condense per-minute readings older than the retention into buckets."""
    now = now or timezone.now()
    retention_days = settings.PROBE_READING_RETENTION_DAYS if retention_days is None else retention_days
    bucket_minutes = bucket_minutes or settings.PROBE_BUCKET_MINUTES
    # Only whole buckets are condensed, so a bucket never receives late minutes.
    cutoff = _bucket_start(now - timedelta(days=retention_days), bucket_minutes)
    result = CondenseResult()

    for probe in collected_probes().order_by("pk"):
        probe_id = probe.pk
        while True:
            oldest = (
                TemperatureMeasurement.objects.filter(probe_id=probe_id, measured_at__lt=cutoff)
                .aggregate(oldest=Min("measured_at"))["oldest"]
            )
            if oldest is None:
                break
            batch_end = min(cutoff, _bucket_start(oldest, bucket_minutes) + timedelta(days=batch_days))
            _condense_batch(probe, oldest, batch_end, bucket_minutes, result)
    return result


def _condense_batch(probe, start, end, bucket_minutes, result):
    with transaction.atomic():
        lock_probe_zone(probe)
        probe_id = probe.pk
        rows = list(
            TemperatureMeasurement.objects.select_for_update()
            .filter(probe_id=probe_id, measured_at__gte=start, measured_at__lt=end)
            .values_list("id", "measured_at", "temperature_c")
        )
        if not rows:
            return
        groups = defaultdict(list)
        last_instants = {}
        for _, measured_at, value in rows:
            period = _bucket_start(measured_at, bucket_minutes)
            groups[period].append(Decimal(value))
            last_instants[period] = max(last_instants.get(period, measured_at), measured_at)
        existing = {
            bucket.period_start.astimezone(datetime_timezone.utc): bucket
            for bucket in ProbeTemperatureBucket.objects.select_for_update().filter(
                probe_id=probe_id, period_start__in=list(groups)
            )
        }
        new_buckets = []
        for period_start, values in groups.items():
            total = sum(values, Decimal(0))
            bucket = existing.get(period_start)
            if bucket is None:
                new_buckets.append(
                    ProbeTemperatureBucket(
                        probe_id=probe_id,
                        period_start=period_start,
                        period_minutes=bucket_minutes,
                        last_measured_at=last_instants[period_start],
                        min_temperature_c=min(values),
                        average_temperature_c=_round(total / len(values)),
                        max_temperature_c=max(values),
                        measurement_count=len(values),
                        temperature_sum_c=total,
                    )
                )
                continue
            # A previous interrupted run already condensed part of this period.
            count = bucket.measurement_count + len(values)
            merged_total = bucket.temperature_sum_c + total
            bucket.min_temperature_c = min(bucket.min_temperature_c, min(values))
            bucket.max_temperature_c = max(bucket.max_temperature_c, max(values))
            bucket.average_temperature_c = _round(merged_total / count)
            bucket.measurement_count = count
            bucket.temperature_sum_c = merged_total
            bucket.last_measured_at = max(bucket.last_measured_at, last_instants[period_start])
            bucket.save(
                update_fields=[
                    "min_temperature_c",
                    "average_temperature_c",
                    "max_temperature_c",
                    "measurement_count",
                    "temperature_sum_c",
                    "last_measured_at",
                ]
            )
            result.buckets_merged += 1
        ProbeTemperatureBucket.objects.bulk_create(new_buckets, batch_size=1000)
        TemperatureMeasurement.objects.filter(id__in=[row[0] for row in rows]).delete()
        result.buckets_created += len(new_buckets)
        result.readings_condensed += len(rows)
