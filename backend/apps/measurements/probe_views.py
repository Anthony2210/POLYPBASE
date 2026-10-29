from datetime import timedelta

from django.db import transaction
from django.db.models import Max, Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import get_active_organization_ids
from apps.cultures.models import ThermalZone

from .models import ProbeTemperatureBucket
from .probe_collection import collected_probes
from .probe_readings import (
    MAX_SERIES_SPAN,
    probe_series,
    series_resolution_minutes,
    zone_probe_monitoring,
    zone_reading_extent,
)

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


class ThermalZoneProbeReadingsAPIView(APIView):
    """Per-probe temperature series of one zone, for its active organization."""

    @transaction.atomic
    def get(self, request, pk):
        zone = get_object_or_404(
            ThermalZone.objects.select_for_update().prefetch_related(
                Prefetch("probes", queryset=collected_probes())
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

        resolution = series_resolution_minutes(start, end)
        # Inactive probes keep their history visible; their zone stays the scope.
        probes = list(
            collected_probes()
            .filter(thermal_zone=zone, organization_id=zone.organization_id)
            .order_by("external_serial", "external_channel", "code")
        )
        stored_resolution = ProbeTemperatureBucket.objects.filter(
            probe_id__in=[probe.id for probe in probes], period_start__gte=start, period_start__lt=end,
        ).aggregate(minutes=Max("period_minutes"))["minutes"]
        resolution = max(resolution, stored_resolution or 1)
        first_reading_at, last_reading_at = zone_reading_extent(zone)
        return Response(
            {
                "zone_id": zone.id,
                "start": start,
                "end": end,
                "resolution_minutes": resolution,
                "target_temperature_c": zone.target_temperature_c,
                "first_reading_at": first_reading_at,
                "last_reading_at": last_reading_at,
                "monitoring": zone_probe_monitoring(zone),
                "probes": [
                    {
                        "id": probe.id,
                        "code": probe.code,
                        "location": probe.location,
                        "is_active": probe.is_active,
                        "external_channel": probe.external_channel,
                        "points": probe_series(probe, start, end, resolution),
                    }
                    for probe in probes
                ],
            }
        )
