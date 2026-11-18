"""Raw-only four-hour thermal monitoring, independent of daily summaries."""

from datetime import timedelta
from decimal import Decimal, localcontext

from django.utils import timezone

from .models import Probe, TemperatureMeasurement
from .probe_readings import _build_series, _number, python_zone_average


def zone_thermal_monitoring(zone):
    """Build chart and summary from one inclusive raw measurement snapshot.

    The window mean is the equal-weight mean of each contributing probe's
    arithmetic mean over all its readings, with no time weighting. Deviation
    subtracts the target from that unrounded mean. Decimal calculations use
    60-digit precision; only output numbers round to two places (half up),
    consistently with the historical chart. Min/max are real observations.
    """
    end = timezone.now()
    start = end - timedelta(hours=4)
    probes = list(Probe.objects.filter(
        thermal_zone_id=zone.pk, organization_id=zone.organization_id,
    ).order_by("external_serial", "external_channel", "code"))
    tiers = {probe.pk: ([], [], []) for probe in probes}
    # Materialize once: chart, sources, averages and metrics share these rows.
    rows = list(TemperatureMeasurement.objects.filter(
        probe_id__in=tiers,
        probe__thermal_zone_id=zone.pk,
        probe__organization_id=zone.organization_id,
        measured_at__gte=start,
        measured_at__lte=end,
    ).order_by("measured_at", "probe_id").values_list(
        "probe_id", "measured_at", "temperature_c",
    ))
    for probe_id, measured_at, value in rows:
        tiers[probe_id][0].append((measured_at, value))

    with localcontext() as context:
        context.prec = 60
        means = [
            sum((value for _, value in minutes), Decimal(0)) / len(minutes)
            for minutes, _, _ in tiers.values() if minutes
        ]
        mean = sum(means, Decimal(0)) / len(means) if means else None
        deviation = (
            mean - zone.target_temperature_c
            if mean is not None and zone.target_temperature_c is not None else None
        )
        summary = {
            "mean_temperature_c": _number(mean) if mean is not None else None,
            "target_deviation_c": _number(deviation) if deviation is not None else None,
            "min_temperature_c": _number(min(row[2] for row in rows)) if rows else None,
            "max_temperature_c": _number(max(row[2] for row in rows)) if rows else None,
            "contributing_probe_count": len(means),
            "measurement_count": len(rows),
        }

    payload_probes = []
    for probe in probes:
        points, sources = _build_series(*tiers[probe.pk], start, end, 1, False)
        payload_probes.append({
            "id": probe.pk,
            "code": probe.code,
            "location": probe.location,
            "is_active": probe.is_active,
            "external_channel": probe.external_channel,
            "sampling_interval_seconds": probe.sampling_interval_seconds,
            "points": points,
            "point_sources": sources,
        })
    return {
        "zone_id": zone.pk,
        "start": start,
        "end": end,
        "resolution_minutes": 1,
        "target_temperature_c": zone.target_temperature_c,
        "probes": payload_probes,
        "probe_average": python_zone_average(tiers, 1),
        "summary": summary,
    }
