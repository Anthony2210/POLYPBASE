"""Current, equally weighted probe observations, separate from daily aggregates."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Case, Count, F, Max, Min, OuterRef, Subquery, When
from django.utils import timezone

from .models import Probe, ProbeTemperatureBucket, TemperatureMeasurement


def thermal_control_probes(zone):
    """Read both storage tiers in one statement, scoped to the zone's owner."""
    raw = TemperatureMeasurement.objects.filter(
        probe_id=OuterRef("pk"), probe__organization_id=zone.organization_id,
        probe__thermal_zone_id=zone.pk,
    ).order_by("-measured_at")
    bucket_scope = ProbeTemperatureBucket.objects.filter(
        probe__organization_id=zone.organization_id, probe__thermal_zone_id=zone.pk,
    )
    latest_bucket = bucket_scope.filter(probe_id=OuterRef("pk")).order_by("-last_measured_at")
    # This scalar lives inside the tie aggregate: skip that bucket query and
    # correlate to the outer Probe, not the bucket's primary key.
    latest_for_ties = bucket_scope.filter(
        probe_id=OuterRef(OuterRef("pk")),
    ).order_by("-last_measured_at").values("last_measured_at")[:1]
    # Both the latest lookup and the exact-instant tie lookup use the composite
    # (probe, last_measured_at) index; never group the probe's full history.
    buckets = bucket_scope.filter(
        probe_id=OuterRef("pk"), last_measured_at=Subquery(latest_for_ties),
    ).order_by().values("probe_id").annotate(
        total=Count("pk"), known=Count("last_temperature_c"),
        minimum=Min("last_temperature_c"), maximum=Max("last_temperature_c"),
    ).annotate(
        value=Case(When(total=F("known"), minimum=F("maximum"), then=F("minimum"))),
    )
    return Probe.objects.filter(
        thermal_zone_id=zone.pk, organization_id=zone.organization_id, is_active=True,
    ).order_by().annotate(
        raw_at=Subquery(raw.values("measured_at")[:1]),
        raw_value=Subquery(raw.values("temperature_c")[:1]),
        bucket_at=Subquery(latest_bucket.values("last_measured_at")[:1]),
        bucket_value=Subquery(buckets.values("value")[:1]),
    ).values(
        "sampling_interval_seconds", "raw_at", "raw_value", "bucket_at", "bucket_value",
    )


def zone_thermal_control(zone, *, now=None):
    """Use only latest known observations aged 0 through three cadences inclusive.

    Exclusion counts are disjoint, in this precedence: invalid interval, no
    reading, future, stale, unavailable. Timestamp range and earliest expiry
    describe included probes only. No observations or aggregates are written.
    """
    now = now if now is not None else timezone.now()
    summary = {
        "evaluated_at": now,
        "temperature_c": None,
        "included_probe_count": 0,
        "oldest_measured_at": None,
        "newest_measured_at": None,
        "expires_at": None,
        "eligible_probe_count": 0,
        "no_reading_probe_count": 0,
        "stale_probe_count": 0,
        "unavailable_probe_count": 0,
        "future_probe_count": 0,
        "invalid_interval_probe_count": 0,
    }
    total = Decimal(0)
    for probe in thermal_control_probes(zone):
        summary["eligible_probe_count"] += 1
        interval = probe["sampling_interval_seconds"]
        if interval not in (60, 600, 900):
            summary["invalid_interval_probe_count"] += 1
            continue
        raw_at, bucket_at = probe["raw_at"], probe["bucket_at"]
        if raw_at is None and bucket_at is None:
            summary["no_reading_probe_count"] += 1
            continue
        if bucket_at is None or (raw_at is not None and raw_at > bucket_at):
            measured_at, value = raw_at, probe["raw_value"]
        else:
            measured_at, value = bucket_at, probe["bucket_value"]
            if raw_at == bucket_at and probe["raw_value"] != value:
                value = None
        expires_at = measured_at + timedelta(seconds=3 * interval)
        if measured_at > now:
            summary["future_probe_count"] += 1
        elif now > expires_at:
            summary["stale_probe_count"] += 1
        elif value is None:
            summary["unavailable_probe_count"] += 1
        else:
            total += value
            summary["included_probe_count"] += 1
            oldest, newest, expiry = (
                summary["oldest_measured_at"], summary["newest_measured_at"], summary["expires_at"],
            )
            summary["oldest_measured_at"] = min(oldest, measured_at) if oldest is not None else measured_at
            summary["newest_measured_at"] = max(newest, measured_at) if newest is not None else measured_at
            summary["expires_at"] = min(expiry, expires_at) if expiry is not None else expires_at
    if summary["included_probe_count"]:
        summary["temperature_c"] = total / Decimal(summary["included_probe_count"])
    return summary
