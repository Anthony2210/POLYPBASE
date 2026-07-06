from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, OuterRef, Prefetch, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied, ValidationError as DRFValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import (
    get_admin_organization_ids,
    get_authorized_organization_ids,
    get_authorized_organizations,
    user_can_write_lab_data,
)
from apps.audit.models import Alert, AuditLog
from apps.measurements.models import BiologicalMeasurement, DailyTemperature, SalinityMeasurement
from apps.organizations.serializers import OrganizationSummarySerializer

from .models import Box, BoxLineage, BoxLocation, BoxMovement, ThermalZone
from .serializers import (
    AlertSummarySerializer,
    AuditLogAccessSerializer,
    BiologicalMeasurementCreateSerializer,
    BiologicalMeasurementSerializer,
    BoxDetailSerializer,
    BoxListSerializer,
    BoxMoveCreateSerializer,
    BoxTransferCreateSerializer,
    ProbeCreateSerializer,
    SubcultureCreateSerializer,
    SubcultureEventSerializer,
    ThermalZoneCreateSerializer,
    ThermalZoneSerializer,
)
from .services import build_lineage_graph, create_subculture, move_box_to_thermal_zone


def box_queryset_for_user(user):
    """Return boxes the user can access, with data needed by serializers."""
    organization_ids = get_authorized_organization_ids(user)
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
            queryset=BoxLineage.objects.filter(
                parent_box__organization_id__in=organization_ids,
            ).select_related(
                "parent_box",
                "parent_box__strain",
                "parent_box__strain__species",
                "parent_box__thermal_zone",
                "subculture_event",
                "subculture_event__user",
            ),
        ),
        Prefetch(
            "child_lineages",
            queryset=BoxLineage.objects.filter(
                child_box__organization_id__in=organization_ids,
            ).select_related(
                "child_box",
                "child_box__strain",
                "child_box__strain__species",
                "child_box__thermal_zone",
                "subculture_event",
                "subculture_event__user",
            ),
        ),
        Prefetch(
            "locations",
            queryset=BoxLocation.objects.select_related("thermal_zone").order_by("-starts_at"),
        ),
        Prefetch(
            "movements",
            queryset=BoxMovement.objects.select_related(
                "from_thermal_zone",
                "to_thermal_zone",
                "user",
            ).order_by("-moved_at"),
        ),
        "tags",
    ).filter(organization_id__in=organization_ids)


def box_list_queryset_for_user(user):
    """Lightweight queryset for the box list.

    The list serializer only needs the latest measurement and the active alert
    count, so we avoid the heavy detail prefetches (full history, lineages,
    movements, locations, tags). Instead we prefetch only the 10 most recent
    measurements per box and annotate the active alert count via subqueries.
    Both subqueries are portable (correlated with LIMIT), so they run on
    PostgreSQL and SQLite alike.
    """
    organization_ids = get_authorized_organization_ids(user)

    recent_measurement_ids = Subquery(
        BiologicalMeasurement.objects.filter(box_id=OuterRef("box_id"))
        .order_by("-measured_on", "-created_at")
        .values("id")[:10]
    )
    recent_measurements = (
        BiologicalMeasurement.objects.filter(id__in=recent_measurement_ids)
        .select_related("user")
        .order_by("-measured_on", "-created_at")
    )

    active_alert_count = Coalesce(
        Subquery(
            Alert.objects.filter(box_id=OuterRef("pk"), resolved_at__isnull=True)
            .order_by()
            .values("box_id")
            .annotate(count=Count("*"))
            .values("count")
        ),
        0,
    )

    latest_salinity = Subquery(
        BiologicalMeasurement.objects.filter(box_id=OuterRef("pk"), salinity_psu__isnull=False)
        .order_by("-measured_on", "-created_at")
        .values("salinity_psu")[:1]
    )

    return (
        Box.objects.select_related(
            "organization",
            "strain",
            "strain__species",
            "strain__origin",
            "origin",
            "thermal_zone",
        )
        .annotate(
            active_alert_count_annotation=active_alert_count,
            latest_salinity_annotation=latest_salinity,
        )
        .prefetch_related(
            Prefetch("biological_measurements", queryset=recent_measurements)
        )
        .filter(organization_id__in=organization_ids)
    )


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
        access_candidates = AuditLog.objects.filter(
            organization_id__in=organization_ids,
            user=request.user,
            action__in=[AuditLog.Action.SCAN, AuditLog.Action.VIEW],
            object_type="box",
        ).select_related("user").order_by("-created_at")[:40]
        recent_accesses = []
        accessed_box_codes = set()
        for access in access_candidates:
            if access.object_id in accessed_box_codes:
                continue
            recent_accesses.append(access)
            accessed_box_codes.add(access.object_id)
            if len(recent_accesses) == 8:
                break
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
                "recent_accesses": AuditLogAccessSerializer(recent_accesses, many=True).data,
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
        queryset = box_list_queryset_for_user(self.request.user).order_by("global_code")

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


class BoxAccessAPIView(APIView):
    """Store a box consultation for the current account across devices."""

    def post(self, request, box_id):
        box = get_object_or_404(box_queryset_for_user(request.user), id=box_id)
        AuditLog.objects.create(
            organization=box.organization,
            user=request.user,
            action=AuditLog.Action.VIEW,
            object_type="box",
            object_id=box.global_code,
            description=f"Box opened: {box.global_code}",
            metadata={"box_id": box.id, "source": "web_app"},
        )
        return Response(status=status.HTTP_201_CREATED)


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


class BoxSubcultureCreateAPIView(generics.GenericAPIView):
    serializer_class = SubcultureCreateSerializer

    def post(self, request, box_id):
        parent_box = get_object_or_404(box_queryset_for_user(request.user), id=box_id)
        if not user_can_write_lab_data(request.user, parent_box.organization):
            raise PermissionDenied("This user cannot create subculture events.")

        serializer = self.get_serializer(
            data=request.data,
            context={"parent_box": parent_box},
        )
        serializer.is_valid(raise_exception=True)
        event, child_boxes = create_subculture(
            parent_box=parent_box,
            user=request.user,
            **serializer.validated_data,
        )
        response_serializer = SubcultureEventSerializer(
            event,
            context={"child_boxes": child_boxes},
        )
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class BoxMoveAPIView(generics.GenericAPIView):
    serializer_class = BoxMoveCreateSerializer

    def post(self, request, box_id):
        box = get_object_or_404(box_queryset_for_user(request.user), id=box_id)
        if not user_can_write_lab_data(request.user, box.organization):
            raise PermissionDenied("This user cannot move boxes.")

        serializer = self.get_serializer(data=request.data, context={"box": box})
        serializer.is_valid(raise_exception=True)

        try:
            move_box_to_thermal_zone(
                box=box,
                user=request.user,
                **serializer.validated_data,
            )
        except DjangoValidationError as error:
            raise DRFValidationError(error.messages) from error

        updated_box = get_object_or_404(box_queryset_for_user(request.user), id=box.id)
        return Response(BoxDetailSerializer(updated_box).data, status=status.HTTP_200_OK)


class BoxLineageGraphAPIView(APIView):
    def get(self, request, box_id):
        root_box = get_object_or_404(box_queryset_for_user(request.user), id=box_id)
        return Response(
            build_lineage_graph(
                root_box=root_box,
                organization_ids=get_authorized_organization_ids(request.user),
            )
        )


class ThermalZoneListCreateAPIView(generics.ListCreateAPIView):
    def get_serializer_class(self):
        if self.request.method == "POST":
            return ThermalZoneCreateSerializer
        return ThermalZoneSerializer

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

    def perform_create(self, serializer):
        # Creating a zone is reserved to administrators of the owning organization.
        organization = serializer.validated_data["organization"]
        if organization.id not in get_admin_organization_ids(self.request.user):
            raise PermissionDenied("Ce compte ne peut pas créer de zone pour cette structure.")
        serializer.save()


class ProbeCreateAPIView(generics.CreateAPIView):
    serializer_class = ProbeCreateSerializer

    def perform_create(self, serializer):
        # A probe inherits its organization from the chosen zone; only that
        # organization's admins may register it.
        zone = serializer.validated_data["thermal_zone"]
        if zone.organization_id not in get_admin_organization_ids(self.request.user):
            raise PermissionDenied("Ce compte ne peut pas ajouter de sonde à cette zone.")
        serializer.save(organization=zone.organization)


class BoxTransferCreateAPIView(generics.CreateAPIView):
    serializer_class = BoxTransferCreateSerializer

    def perform_create(self, serializer):
        # The source organization is the box owner; only its admins may record
        # a transfer out of it. The box itself is not reassigned here.
        box = serializer.validated_data["box"]
        if box.organization_id not in get_admin_organization_ids(self.request.user):
            raise PermissionDenied("Ce compte ne peut pas transférer cette boîte.")
        serializer.save(from_organization=box.organization, user=self.request.user)
