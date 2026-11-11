from datetime import timedelta

from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import get_active_organization_ids
from apps.cultures.models import ThermalZone

from .probe_collection import collected_probes
from .thermal_control import zone_thermal_control
from .probe_readings import MAX_SERIES_SPAN, zone_probe_monitoring, zone_probe_series_and_average

DEFAULT_SPAN = timedelta(hours=24)


def _parse_instant(value, name):
    try:
        moment = parse_datetime(value)
    except (ValueError, OverflowError):
        moment = None
    if moment is None:
        raise ValidationError({name: "Expected an ISO 8601 date and time."})
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment)
    return moment


class ThermalZoneThermalControlAPIView(APIView):
    """Compact current summary scoped to the active organization."""

    def get(self, request, pk):
        zone = get_object_or_404(
            ThermalZone.objects,
            pk=pk,
            organization_id__in=get_active_organization_ids(request),
        )
        return Response(zone_thermal_control(zone), headers={"Cache-Control": "no-store"})


class ThermalZoneProbeReadingsAPIView(APIView):
    """Per-probe temperature series of one zone, for its active organization."""

    def get(self, request, pk):
        # Inactive probes keep their history visible; their zone stays the scope.
        zone = get_object_or_404(
            ThermalZone.objects.prefetch_related(
                Prefetch(
                    "probes",
                    queryset=collected_probes().order_by("external_serial", "external_channel", "code"),
                )
            ),
            pk=pk,
            organization_id__in=get_active_organization_ids(request),
        )
        end_value = request.query_params.get("end")
        start_value = request.query_params.get("start")
        end = _parse_instant(end_value, "end") if end_value else timezone.now()
        start = _parse_instant(start_value, "start") if start_value else end - DEFAULT_SPAN
        if start >= end:
            raise ValidationError({"start": "The start must be before the end."})
        if end - start > MAX_SERIES_SPAN:
            raise ValidationError({"start": "The requested period cannot exceed 366 days."})

        probes = list(zone.probes.all())
        resolution, series, average = zone_probe_series_and_average(
            probes, start, end, group_sources=request.query_params.get("group_sources") == "1",
        )
        return Response(
            {
                "zone_id": zone.id,
                "start": start,
                "end": end,
                "resolution_minutes": resolution,
                "target_temperature_c": zone.target_temperature_c,
                "monitoring": zone_probe_monitoring(zone),
                "probes": [
                    {
                        "id": probe.id,
                        "code": probe.code,
                        "location": probe.location,
                        "is_active": probe.is_active,
                        "external_channel": probe.external_channel,
                        "sampling_interval_seconds": probe.sampling_interval_seconds,
                        "points": series[probe.pk][0],
                        "point_sources": series[probe.pk][1],
                    }
                    for probe in probes
                ],
                # [period start ms, mean °C, contributing probes], period of
                # resolution_minutes; only periods with at least two probes.
                "probe_average": average,
            }
        )
