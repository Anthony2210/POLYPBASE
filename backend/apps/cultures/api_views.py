from django.db.models import Count, Prefetch, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import (
    get_authorized_organization_ids,
    get_authorized_organizations,
    user_can_write_lab_data,
)
from apps.audit.models import Alert, AuditLog
from apps.measurements.models import BiologicalMeasurement, DailyTemperature, SalinityMeasurement
from apps.organizations.serializers import OrganizationSummarySerializer

from .models import Box, BoxLineage, ThermalZone
from .serializers import (
    AlertSummarySerializer,
    AuditLogScanSerializer,
    BiologicalMeasurementCreateSerializer,
    BiologicalMeasurementSerializer,
    BoxDetailSerializer,
    BoxListSerializer,
    ThermalZoneSerializer,
)


def box_queryset_for_user(user):
    """Return boxes the user can access, with data needed by serializers."""
    return Box.objects.select_related(
        "organization",
        "strain",
        "strain__species",
        "strain__origin",
        "origin",
        "thermal_zone",
    ).prefetch_related(
        Prefetch(
            "biological_measurements",
            queryset=BiologicalMeasurement.objects.select_related("user").order_by("-measured_on", "-created_at"),
        ),
        Prefetch(
            "alerts",
            queryset=Alert.objects.order_by("-created_at"),
        ),
        Prefetch(
            "parent_lineages",
            queryset=BoxLineage.objects.select_related("parent_box", "child_box"),
        ),
        Prefetch(
            "child_lineages",
            queryset=BoxLineage.objects.select_related("parent_box", "child_box"),
        ),
        "tags",
    ).filter(organization_id__in=get_authorized_organization_ids(user))


class HealthAPIView(APIView):
    """Small public endpoint used by deployments and local checks."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response(
            {
                "status": "ok",
                "service": "polypbase",
                "timestamp": timezone.now().isoformat(),
            }
        )


class DashboardAPIView(APIView):
    """Return the first dashboard payload consumed by the React app."""

    def get(self, request):
        organizations = get_authorized_organizations(request.user).order_by("name")
        organization_ids = list(organizations.values_list("id", flat=True))

        boxes = Box.objects.filter(organization_id__in=organization_ids)
        measurements = BiologicalMeasurement.objects.filter(box__organization_id__in=organization_ids)
        alerts = Alert.objects.filter(
            organization_id__in=organization_ids,
            resolved_at__isnull=True,
        ).select_related("box", "thermal_zone")
        latest_scans = AuditLog.objects.filter(
            organization_id__in=organization_ids,
            action=AuditLog.Action.SCAN,
        ).select_related("user").order_by("-created_at")[:8]
        latest_entries = measurements.select_related("user").order_by("-measured_on", "-created_at")[:8]
        measurement_totals = measurements.aggregate(
            polyps=Sum("polyp_count"),
            ephyrae=Sum("ephyrae_count"),
            strobilae=Sum("strobila_count"),
        )

        return Response(
            {
                "organizations": OrganizationSummarySerializer(organizations, many=True).data,
                "stats": {
                    "boxes_total": boxes.count(),
                    "active_boxes": boxes.filter(status=Box.Status.ACTIVE).count(),
                    "species_count": boxes.values("strain__species").distinct().count(),
                    "thermal_zones": ThermalZone.objects.filter(organization_id__in=organization_ids).count(),
                    "active_alerts": alerts.count(),
                    "measured_polyps": measurement_totals["polyps"] or 0,
                    "measured_ephyrae": measurement_totals["ephyrae"] or 0,
                    "measured_strobilae": measurement_totals["strobilae"] or 0,
                },
                "latest_entries": BiologicalMeasurementSerializer(latest_entries, many=True).data,
                "latest_scans": AuditLogScanSerializer(latest_scans, many=True).data,
                "alerts": self._alert_payload(alerts[:12]),
            }
        )

    def _alert_payload(self, alerts):
        data = AlertSummarySerializer(alerts, many=True).data
        for item, alert in zip(data, alerts):
            item["box"] = alert.box.global_code if alert.box else None
            item["thermal_zone"] = alert.thermal_zone.name if alert.thermal_zone else None
        return data


class BoxListAPIView(generics.ListAPIView):
    serializer_class = BoxListSerializer

    def get_queryset(self):
        queryset = box_queryset_for_user(self.request.user).order_by("global_code")

        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        organization_filter = self.request.query_params.get("organization")
        if organization_filter:
            queryset = self._filter_by_organization(queryset, organization_filter)

        search = self.request.query_params.get("q")
        if search:
            queryset = queryset.filter(
                Q(global_code__icontains=search)
                | Q(local_code__icontains=search)
                | Q(box_number__icontains=search)
                | Q(strain__code__icontains=search)
                | Q(strain__species__scientific_name__icontains=search)
            )

        return queryset

    def _filter_by_organization(self, queryset, value):
        if value.isdigit():
            return queryset.filter(organization_id=int(value))
        return queryset.filter(organization__slug=value)


class BoxDetailAPIView(generics.RetrieveAPIView):
    serializer_class = BoxDetailSerializer

    def get_queryset(self):
        return box_queryset_for_user(self.request.user)


class BoxMeasurementListCreateAPIView(generics.GenericAPIView):
    serializer_class = BiologicalMeasurementSerializer

    def get(self, request, box_id):
        box = self._get_box(request.user, box_id)
        queryset = box.biological_measurements.select_related("user").order_by("-measured_on", "-created_at")
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response({"results": serializer.data})

    def post(self, request, box_id):
        box = self._get_box(request.user, box_id)
        if not user_can_write_lab_data(request.user, box.organization):
            raise PermissionDenied("This user cannot create or update lab measurements.")

        serializer = BiologicalMeasurementCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data.copy()
        measured_on = data.pop("measured_on")

        measurement, created = BiologicalMeasurement.objects.update_or_create(
            box=box,
            measured_on=measured_on,
            defaults={**data, "user": request.user},
        )
        AuditLog.objects.create(
            organization=box.organization,
            user=request.user,
            action=AuditLog.Action.ENTRY if created else AuditLog.Action.UPDATE,
            object_type="box",
            object_id=box.global_code,
            description=f"Biological measurement for {measurement.measured_on}",
            metadata={"measurement_id": measurement.id},
        )

        response_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(BiologicalMeasurementSerializer(measurement).data, status=response_status)

    def _get_box(self, user, box_id):
        return get_object_or_404(box_queryset_for_user(user), id=box_id)


class ThermalZoneListAPIView(generics.ListAPIView):
    serializer_class = ThermalZoneSerializer

    def get_queryset(self):
        organization_ids = get_authorized_organization_ids(self.request.user)
        return ThermalZone.objects.filter(
            organization_id__in=organization_ids,
        ).select_related("organization").prefetch_related(
            Prefetch(
                "daily_temperatures",
                queryset=DailyTemperature.objects.order_by("-date"),
            ),
            Prefetch(
                "salinity_measurements",
                queryset=SalinityMeasurement.objects.select_related("user").order_by("-measured_on"),
            ),
            "probes",
        ).annotate(
            box_count=Count("boxes")
        ).order_by(
            "organization__name",
            "name",
        )
