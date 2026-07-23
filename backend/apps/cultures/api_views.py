from collections import defaultdict
from decimal import Decimal
from datetime import timedelta
import re

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
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
    get_active_admin_organization_ids,
    get_active_organization_ids,
    get_authorized_organization_ids,
    get_authorized_organizations,
    user_can_write_lab_data,
)
from apps.audit.models import Alert, AuditLog
from apps.measurements.models import BiologicalMeasurement, DailyTemperature, SalinityMeasurement
from apps.organizations.serializers import OrganizationSummarySerializer
from apps.taxonomy.models import Species, Strain

from .models import Box, BoxLineage, BoxLocation, BoxMovement, BoxTransferImport, ThermalZone
from .serializers import (
    AlertSummarySerializer,
    AuditLogAccessSerializer,
    BiologicalMeasurementCreateSerializer,
    BiologicalMeasurementSerializer,
    BoxCreateSerializer,
    BoxDetailSerializer,
    BoxListSerializer,
    BoxMoveCreateSerializer,
    BoxTransferCreateSerializer,
    ManualTemperatureCreateSerializer,
    ProbeCreateSerializer,
    SubcultureCreateSerializer,
    SubcultureEventSerializer,
    ThermalZoneCreateSerializer,
    ThermalZoneSerializer,
)
from .services import build_lineage_graph, create_subculture, move_box_to_thermal_zone


TEMPERATURE_ALERT_THRESHOLD_C = Decimal("1.0")


def _next_unique_box_identity(strain):
    """Generate the next globally unique ``<strain>.<number>`` identity."""
    prefix = f"{strain.code}."
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    numbers = []
    for global_code in Box.objects.select_for_update().filter(
        global_code__startswith=prefix
    ).values_list("global_code", flat=True):
        match = pattern.match(global_code)
        if match:
            numbers.append(int(match.group(1)))
    next_number = max(numbers, default=0) + 1
    while True:
        box_number = str(next_number).zfill(3)
        global_code = f"{prefix}{box_number}"
        if not Box.objects.filter(global_code=global_code).exists():
            return global_code, box_number
        next_number += 1


def _resolve_alerts(queryset, *, user):
    queryset.filter(resolved_at__isnull=True).update(
        resolved_at=timezone.now(),
        resolved_by=user,
    )


def _sync_polyp_drop_alert(*, box, measurement, user):
    """Keep one persistent alert in sync with the latest polyp trend."""
    previous = (
        BiologicalMeasurement.objects.filter(
            box=box,
            measured_on__lt=measurement.measured_on,
        )
        .order_by("-measured_on", "-created_at")
        .first()
    )
    active_alerts = Alert.objects.filter(
        organization=box.organization,
        box=box,
        alert_type=Alert.AlertType.BIOLOGICAL,
        resolved_at__isnull=True,
    )

    if previous is None or measurement.polyp_count >= previous.polyp_count:
        _resolve_alerts(active_alerts, user=user)
        return

    decrease = previous.polyp_count - measurement.polyp_count
    message = (
        f"Baisse de {decrease} polype{'s' if decrease > 1 else ''} "
        f"({previous.polyp_count} → {measurement.polyp_count})"
    )
    alert = active_alerts.order_by("-created_at").first()
    if alert:
        alert.message = message
        alert.level = Alert.Level.WARNING
        alert.save(update_fields=["message", "level"])
    else:
        Alert.objects.create(
            organization=box.organization,
            box=box,
            alert_type=Alert.AlertType.BIOLOGICAL,
            level=Alert.Level.WARNING,
            message=message,
            created_by=user,
        )


def _sync_temperature_alert(*, zone, temperature_c, user):
    """Create or resolve the zone alert using the configured ±1 °C rule."""
    active_alerts = Alert.objects.filter(
        organization=zone.organization,
        thermal_zone=zone,
        alert_type=Alert.AlertType.TEMPERATURE,
        resolved_at__isnull=True,
    )
    if zone.target_temperature_c is None:
        _resolve_alerts(active_alerts, user=user)
        return

    deviation = abs(temperature_c - zone.target_temperature_c)
    if deviation < TEMPERATURE_ALERT_THRESHOLD_C:
        _resolve_alerts(active_alerts, user=user)
        return

    message = (
        f"Température à vérifier : {temperature_c} °C mesuré, "
        f"consigne {zone.target_temperature_c} °C"
    )
    alert = active_alerts.order_by("-created_at").first()
    if alert:
        alert.message = message
        alert.level = Alert.Level.WARNING
        alert.save(update_fields=["message", "level"])
    else:
        Alert.objects.create(
            organization=zone.organization,
            thermal_zone=zone,
            alert_type=Alert.AlertType.TEMPERATURE,
            level=Alert.Level.WARNING,
            message=message,
            created_by=user,
        )


def _json_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _measurement_audit_values(measurement):
    return {
        "date": _json_value(measurement.measured_on),
        "polypes": measurement.polyp_count,
        "ephyrules": measurement.ephyrae_count,
        "strobiles": measurement.strobila_count,
        "salinite_psu": _json_value(measurement.salinity_psu),
        "statut_culture": measurement.culture_status,
        "a_verifier": measurement.needs_attention,
        "note": measurement.notes,
    }


def _changed_values(before, after):
    return {
        key: {"avant": before.get(key), "apres": after.get(key)}
        for key in after
        if before.get(key) != after.get(key)
    }


def _record_measurement_audit(*, box, measurement, user, action, metadata):
    """Keep a single history entry per measurement.

    Correcting a measurement overwrites the stored row, so adding a second entry
    would show the same reading twice. The existing entry is updated in place
    instead; created_at is auto_now_add, so it keeps the date the measurement
    was first recorded, and the metadata carries what changed.
    """
    existing = (
        AuditLog.objects.filter(
            object_type="box",
            metadata__measurement_id=measurement.id,
        )
        .order_by("created_at")
        .first()
    )
    description = f"Biological measurement for {measurement.measured_on}"

    if existing is not None:
        existing.organization = box.organization
        existing.user = user
        existing.action = action
        existing.object_id = box.global_code
        existing.description = description
        existing.metadata = metadata
        # created_at keeps the date the measurement was first recorded, so the
        # correction would otherwise stay buried at its old position. edited_at
        # is what makes it surface, and says the entry was corrected.
        existing.edited_at = timezone.now()
        existing.edited_by = user
        existing.save(
            update_fields=[
                "organization",
                "user",
                "action",
                "object_id",
                "description",
                "metadata",
                "edited_at",
                "edited_by",
            ]
        )
        return existing

    return AuditLog.objects.create(
        organization=box.organization,
        user=user,
        action=action,
        object_type="box",
        object_id=box.global_code,
        description=description,
        metadata=metadata,
    )


def _thermal_zone_audit_values(zone):
    return {
        "nom": zone.name,
        "type": zone.zone_type,
        "temperature_consigne": _json_value(zone.target_temperature_c),
        "capacite": zone.capacity,
        "active": zone.is_active,
    }


def box_queryset_for_user(user, organization_ids=None):
    """Return boxes the user can access, with data needed by serializers."""
    organization_ids = organization_ids or get_authorized_organization_ids(user)
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


def box_list_queryset_for_user(user, organization_ids=None):
    """Lightweight queryset for the box list.

    The list serializer only needs the latest measurement and the active alert
    count, so we avoid the heavy detail prefetches (full history, lineages,
    movements, locations, tags). Instead we prefetch only the 10 most recent
    measurements per box and annotate the active alert count via subqueries.
    Both subqueries are portable (correlated with LIMIT), so they run on
    PostgreSQL and SQLite alike.
    """
    organization_ids = organization_ids or get_authorized_organization_ids(user)

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
        organization_ids = get_active_organization_ids(request)
        organizations = get_authorized_organizations(request.user).filter(
            id__in=organization_ids,
        ).order_by("name")

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


class OverviewActiveBoxesAPIView(APIView):
    """Return active boxes with recent biological and temperature history."""

    def get(self, request):
        months = self._get_months(request)
        start_date = timezone.localdate() - timedelta(days=months * 31)
        organization_ids = get_active_organization_ids(request)
        boxes = list(
            box_list_queryset_for_user(request.user, organization_ids=organization_ids)
            .filter(status=Box.Status.ACTIVE)
            .order_by("strain__species__scientific_name", "global_code")
        )
        box_ids = [box.id for box in boxes]
        zone_ids = {box.thermal_zone_id for box in boxes if box.thermal_zone_id}
        app_tracked_box_ids = set(
            BiologicalMeasurement.objects.filter(
                box_id__in=box_ids,
                user__isnull=False,
            )
            .values_list("box_id", flat=True)
            .distinct()
        )

        measurements_by_box = defaultdict(list)
        measurements = (
            BiologicalMeasurement.objects.filter(
                box_id__in=box_ids,
                measured_on__gte=start_date,
            )
            .order_by("box_id", "measured_on", "created_at")
        )
        for measurement in measurements:
            measurements_by_box[measurement.box_id].append(
                {
                    "date": measurement.measured_on.isoformat(),
                    "polyp_count": measurement.polyp_count,
                    "ephyrae_count": measurement.ephyrae_count,
                }
            )

        temperatures_by_zone = defaultdict(list)
        temperatures = (
            DailyTemperature.objects.filter(
                thermal_zone_id__in=zone_ids,
                date__gte=start_date,
            )
            .order_by("thermal_zone_id", "date")
        )
        for temperature in temperatures:
            temperatures_by_zone[temperature.thermal_zone_id].append(
                {
                    "date": temperature.date.isoformat(),
                    "average_temperature_c": float(temperature.average_temperature_c),
                }
            )

        return Response(
            {
                "months": months,
                "results": [
                    self._box_payload(
                        box,
                        measurements_by_box[box.id],
                        temperatures_by_zone[box.thermal_zone_id] if box.thermal_zone_id else [],
                        box.id in app_tracked_box_ids,
                    )
                    for box in boxes
                ],
            }
        )

    def _get_months(self, request):
        try:
            months = int(request.query_params.get("months", 6))
        except (TypeError, ValueError):
            months = 6
        return max(1, min(months, 12))

    def _box_payload(self, box, measurements, temperatures, tracked_in_app):
        return {
            "id": box.id,
            "global_code": box.global_code,
            "species_name": box.strain.species.scientific_name,
            "strain_code": box.strain.code,
            "tracked_in_app": tracked_in_app,
            "thermal_zone": (
                {
                    "id": box.thermal_zone.id,
                    "name": box.thermal_zone.name,
                }
                if box.thermal_zone
                else None
            ),
            "measurements": measurements,
            "temperatures": temperatures,
        }


class BoxListAPIView(generics.ListCreateAPIView):
    def get_serializer_class(self):
        if self.request.method == "POST":
            return BoxCreateSerializer
        return BoxListSerializer

    def get_queryset(self):
        queryset = box_list_queryset_for_user(
            self.request.user,
            organization_ids=get_active_organization_ids(self.request),
        ).order_by("global_code")

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

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        box = serializer.save()
        AuditLog.objects.create(
            organization=box.organization,
            user=request.user,
            action=AuditLog.Action.CREATION,
            object_type="box",
            object_id=box.global_code,
            description=f"Box created manually: {box.global_code}",
            metadata={
                "box_id": box.id,
                "valeurs": {
                    "code_global": box.global_code,
                    "numero_boite": box.box_number,
                    "souche": box.strain.code,
                    "espece": box.strain.species.scientific_name,
                    "emplacement": box.thermal_zone.name if box.thermal_zone else None,
                    "date_entree": box.entered_on.isoformat() if box.entered_on else None,
                    "volume_litres": _json_value(box.volume_liters),
                    "note": box.notes,
                },
            },
        )
        created_box = get_object_or_404(
            box_queryset_for_user(
                request.user,
                organization_ids=get_active_organization_ids(request),
            ),
            id=box.id,
        )
        return Response(BoxDetailSerializer(created_box).data, status=status.HTTP_201_CREATED)


class BoxDetailAPIView(generics.RetrieveAPIView):
    serializer_class = BoxDetailSerializer

    def get_queryset(self):
        return box_queryset_for_user(
            self.request.user,
            organization_ids=get_active_organization_ids(self.request),
        )


class BoxAccessAPIView(APIView):
    """Store a box consultation for the current account across devices."""

    def post(self, request, box_id):
        box = get_object_or_404(
            box_queryset_for_user(request.user, organization_ids=get_active_organization_ids(request)),
            id=box_id,
        )
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


class BoxArchiveAPIView(APIView):
    """Mark a box inactive without deleting its history."""

    def post(self, request, box_id):
        box = get_object_or_404(
            box_queryset_for_user(request.user, organization_ids=get_active_organization_ids(request)),
            id=box_id,
        )
        if box.organization_id not in get_active_admin_organization_ids(request):
            raise PermissionDenied("This user cannot archive this box.")

        before_values = {
            "statut": box.status,
            "raison_arret": box.stop_reason,
        }
        box.status = Box.Status.ARCHIVED
        if not box.stop_reason:
            box.stop_reason = "Mise inactive depuis l'administration."
        box.save(update_fields=["status", "stop_reason"])

        after_values = {
            "statut": box.status,
            "raison_arret": box.stop_reason,
        }
        AuditLog.objects.create(
            organization=box.organization,
            user=request.user,
            action=AuditLog.Action.ARCHIVE,
            object_type="box",
            object_id=box.global_code,
            description=f"Box archived: {box.global_code}",
            metadata={
                "box_id": box.id,
                "valeurs": after_values,
                "modifications": _changed_values(before_values, after_values),
            },
        )

        updated_box = get_object_or_404(
            box_queryset_for_user(request.user, organization_ids=get_active_organization_ids(request)),
            id=box.id,
        )
        return Response(BoxDetailSerializer(updated_box).data, status=status.HTTP_200_OK)


class BoxActivateAPIView(APIView):
    """Reactivate an archived box when an admin made a mistake."""

    def post(self, request, box_id):
        box = get_object_or_404(
            box_queryset_for_user(request.user, organization_ids=get_active_organization_ids(request)),
            id=box_id,
        )
        if box.organization_id not in get_active_admin_organization_ids(request):
            raise PermissionDenied("This user cannot activate this box.")

        before_values = {
            "statut": box.status,
            "raison_arret": box.stop_reason,
        }
        box.status = Box.Status.ACTIVE
        box.stop_reason = ""
        box.save(update_fields=["status", "stop_reason"])

        after_values = {
            "statut": box.status,
            "raison_arret": box.stop_reason,
        }
        AuditLog.objects.create(
            organization=box.organization,
            user=request.user,
            action=AuditLog.Action.UPDATE,
            object_type="box",
            object_id=box.global_code,
            description=f"Box activated: {box.global_code}",
            metadata={
                "box_id": box.id,
                "valeurs": after_values,
                "modifications": _changed_values(before_values, after_values),
            },
        )

        updated_box = get_object_or_404(
            box_queryset_for_user(request.user, organization_ids=get_active_organization_ids(request)),
            id=box.id,
        )
        return Response(BoxDetailSerializer(updated_box).data, status=status.HTTP_200_OK)


class BoxMeasurementListCreateAPIView(generics.GenericAPIView):
    serializer_class = BiologicalMeasurementSerializer

    def get(self, request, box_id):
        box = self._get_box(request, box_id)
        queryset = box.biological_measurements.select_related("user").order_by("-measured_on", "-created_at")
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response({"results": serializer.data})

    def post(self, request, box_id):
        box = self._get_box(request, box_id)
        if not user_can_write_lab_data(request.user, box.organization):
            raise PermissionDenied("This user cannot create or update lab measurements.")

        serializer = BiologicalMeasurementCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data.copy()
        measured_on = data.pop("measured_on")
        existing_measurement = BiologicalMeasurement.objects.filter(
            box=box,
            measured_on=measured_on,
        ).first()
        before_values = _measurement_audit_values(existing_measurement) if existing_measurement else None

        measurement, created = BiologicalMeasurement.objects.update_or_create(
            box=box,
            measured_on=measured_on,
            defaults={**data, "user": request.user},
        )
        _sync_polyp_drop_alert(box=box, measurement=measurement, user=request.user)
        after_values = _measurement_audit_values(measurement)
        metadata = {
            "measurement_id": measurement.id,
            "valeurs": after_values,
        }
        if before_values is not None:
            metadata["modifications"] = _changed_values(before_values, after_values)

        _record_measurement_audit(
            box=box,
            measurement=measurement,
            user=request.user,
            action=AuditLog.Action.ENTRY if created else AuditLog.Action.UPDATE,
            metadata=metadata,
        )

        response_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(BiologicalMeasurementSerializer(measurement).data, status=response_status)

    def _get_box(self, request, box_id):
        return get_object_or_404(
            box_queryset_for_user(request.user, organization_ids=get_active_organization_ids(request)),
            id=box_id,
        )


class BoxMeasurementDetailAPIView(generics.GenericAPIView):
    """Edit a single existing measurement (used by the 'Modifier' action)."""

    serializer_class = BiologicalMeasurementSerializer

    def patch(self, request, box_id, pk):
        box = get_object_or_404(
            box_queryset_for_user(request.user, organization_ids=get_active_organization_ids(request)),
            id=box_id,
        )
        if not user_can_write_lab_data(request.user, box.organization):
            raise PermissionDenied("This user cannot update lab measurements.")

        measurement = get_object_or_404(box.biological_measurements, id=pk)
        serializer = BiologicalMeasurementCreateSerializer(
            measurement, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        before_values = _measurement_audit_values(measurement)
        measurement = serializer.save(user=request.user)
        _sync_polyp_drop_alert(box=box, measurement=measurement, user=request.user)
        after_values = _measurement_audit_values(measurement)

        _record_measurement_audit(
            box=box,
            measurement=measurement,
            user=request.user,
            action=AuditLog.Action.UPDATE,
            metadata={
                "measurement_id": measurement.id,
                "valeurs": after_values,
                "modifications": _changed_values(before_values, after_values),
            },
        )
        return Response(BiologicalMeasurementSerializer(measurement).data)


class BoxSubcultureCreateAPIView(generics.GenericAPIView):
    serializer_class = SubcultureCreateSerializer

    def post(self, request, box_id):
        parent_box = get_object_or_404(
            box_queryset_for_user(request.user, organization_ids=get_active_organization_ids(request)),
            id=box_id,
        )
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
        box = get_object_or_404(
            box_queryset_for_user(request.user, organization_ids=get_active_organization_ids(request)),
            id=box_id,
        )
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

        updated_box = get_object_or_404(
            box_queryset_for_user(request.user, organization_ids=get_active_organization_ids(request)),
            id=box.id,
        )
        return Response(BoxDetailSerializer(updated_box).data, status=status.HTTP_200_OK)


class BoxLineageGraphAPIView(APIView):
    def get(self, request, box_id):
        organization_ids = get_active_organization_ids(request)
        root_box = get_object_or_404(
            box_queryset_for_user(request.user, organization_ids=organization_ids),
            id=box_id,
        )
        return Response(
            build_lineage_graph(
                root_box=root_box,
                organization_ids=organization_ids,
            )
        )


class ThermalZoneListCreateAPIView(generics.ListCreateAPIView):
    def get_serializer_class(self):
        if self.request.method == "POST":
            return ThermalZoneCreateSerializer
        return ThermalZoneSerializer

    def get_queryset(self):
        organization_ids = get_active_organization_ids(self.request)
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
            box_count=Count("boxes", filter=Q(boxes__status=Box.Status.ACTIVE))
        ).order_by(
            "organization__name",
            "name",
        )

    def perform_create(self, serializer):
        # Creating a zone is reserved to administrators of the owning organization.
        organization = serializer.validated_data["organization"]
        if organization.id not in get_active_admin_organization_ids(self.request):
            raise PermissionDenied("Ce compte ne peut pas créer de zone pour cette structure.")
        zone = serializer.save()
        AuditLog.objects.create(
            organization=zone.organization,
            user=self.request.user,
            action=AuditLog.Action.CREATION,
            object_type="thermal_zone",
            object_id=zone.name,
            description=f"Thermal zone created: {zone.name}",
            metadata={
                "thermal_zone_id": zone.id,
                "valeurs": _thermal_zone_audit_values(zone),
            },
        )


class ThermalZoneDetailAPIView(generics.RetrieveUpdateAPIView):
    serializer_class = ThermalZoneCreateSerializer

    def get_queryset(self):
        organization_ids = get_active_organization_ids(self.request)
        return ThermalZone.objects.filter(organization_id__in=organization_ids)

    def perform_update(self, serializer):
        zone = self.get_object()
        if zone.organization_id not in get_active_admin_organization_ids(self.request):
            raise PermissionDenied("Ce compte ne peut pas modifier cette zone.")
        before_values = _thermal_zone_audit_values(zone)
        zone = serializer.save(organization=zone.organization)
        after_values = _thermal_zone_audit_values(zone)
        AuditLog.objects.create(
            organization=zone.organization,
            user=self.request.user,
            action=AuditLog.Action.UPDATE,
            object_type="thermal_zone",
            object_id=zone.name,
            description=f"Thermal zone updated: {zone.name}",
            metadata={
                "thermal_zone_id": zone.id,
                "valeurs": after_values,
                "modifications": _changed_values(before_values, after_values),
            },
        )


class ThermalZoneManualTemperatureAPIView(APIView):
    def post(self, request, pk):
        zone = get_object_or_404(
            ThermalZone.objects.select_related("organization"),
            pk=pk,
            organization_id__in=get_active_organization_ids(request),
        )
        if not user_can_write_lab_data(request.user, zone.organization):
            raise PermissionDenied("Ce compte ne peut pas saisir de température pour cet emplacement.")

        serializer = ManualTemperatureCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        measured_on = serializer.validated_data["measured_on"]
        temperature_c = serializer.validated_data["temperature_c"]

        daily_temperature, created = DailyTemperature.objects.get_or_create(
            thermal_zone=zone,
            date=measured_on,
            defaults={
                "min_temperature_c": temperature_c,
                "average_temperature_c": temperature_c,
                "max_temperature_c": temperature_c,
                "measurement_count": 1,
            },
        )
        if not created:
            previous_count = daily_temperature.measurement_count or 1
            previous_average = daily_temperature.average_temperature_c
            next_count = previous_count + 1
            daily_temperature.average_temperature_c = (
                (previous_average * Decimal(previous_count)) + temperature_c
            ) / Decimal(next_count)
            daily_temperature.min_temperature_c = min(
                value
                for value in [
                    daily_temperature.min_temperature_c,
                    previous_average,
                    temperature_c,
                ]
                if value is not None
            )
            daily_temperature.max_temperature_c = max(
                value
                for value in [
                    daily_temperature.max_temperature_c,
                    previous_average,
                    temperature_c,
                ]
                if value is not None
            )
            daily_temperature.measurement_count = next_count
            daily_temperature.save(
                update_fields=[
                    "min_temperature_c",
                    "average_temperature_c",
                    "max_temperature_c",
                    "measurement_count",
                ]
            )
        _sync_temperature_alert(zone=zone, temperature_c=temperature_c, user=request.user)
        AuditLog.objects.create(
            organization=zone.organization,
            user=request.user,
            action=AuditLog.Action.UPDATE,
            object_type="thermal_zone",
            object_id=zone.name,
            description=f"Manual temperature recorded: {zone.name}",
            metadata={
                "thermal_zone_id": zone.id,
                "valeurs": {
                    "date": measured_on.isoformat(),
                    "temperature_c": _json_value(temperature_c),
                },
            },
        )

        refreshed_zone = (
            ThermalZone.objects.filter(pk=zone.pk)
            .select_related("organization")
            .prefetch_related(
                Prefetch(
                    "daily_temperatures",
                    queryset=DailyTemperature.objects.order_by("-date"),
                ),
                Prefetch(
                    "salinity_measurements",
                    queryset=SalinityMeasurement.objects.select_related("user").order_by("-measured_on"),
                ),
                "probes",
            )
            .annotate(box_count=Count("boxes", filter=Q(boxes__status=Box.Status.ACTIVE)))
            .get()
        )
        return Response(ThermalZoneSerializer(refreshed_zone).data, status=status.HTTP_201_CREATED)


class ProbeCreateAPIView(generics.CreateAPIView):
    serializer_class = ProbeCreateSerializer

    def perform_create(self, serializer):
        # A probe inherits its organization from the chosen zone; only that
        # organization's admins may register it.
        zone = serializer.validated_data["thermal_zone"]
        if zone.organization_id not in get_active_admin_organization_ids(self.request):
            raise PermissionDenied("Ce compte ne peut pas ajouter de sonde à cette zone.")
        probe = serializer.save(organization=zone.organization)
        AuditLog.objects.create(
            organization=zone.organization,
            user=self.request.user,
            action=AuditLog.Action.CREATION,
            object_type="probe",
            object_id=probe.code,
            description=f"Probe created: {probe.code}",
            metadata={
                "probe_id": probe.id,
                "valeurs": {
                    "code": probe.code,
                    "emplacement": zone.name,
                    "type": probe.probe_type,
                    "position": probe.location,
                },
            },
        )


class BoxTransferCreateAPIView(generics.CreateAPIView):
    serializer_class = BoxTransferCreateSerializer

    def perform_create(self, serializer):
        # The source organization is the box owner; only its admins may record
        # a transfer out of it. The box itself is not reassigned here.
        box = serializer.validated_data["box"]
        if box.organization_id not in get_active_admin_organization_ids(self.request):
            raise PermissionDenied("Ce compte ne peut pas transférer cette boîte.")
        transfer = serializer.save(from_organization=box.organization, user=self.request.user)
        AuditLog.objects.create(
            organization=box.organization,
            user=self.request.user,
            action=AuditLog.Action.TRANSFER,
            object_type="box",
            object_id=box.global_code,
            description=f"Box transfer prepared: {box.global_code}",
            metadata={
                "transfer_id": transfer.id,
                "box_id": box.id,
                "code_global": box.global_code,
                "to_organization": transfer.to_organization.name,
                "date": transfer.transfer_date.isoformat(),
                "polypes": transfer.polyp_count,
                "note": transfer.notes,
            },
        )


class BoxTransferImportAPIView(APIView):
    """Validate one Polypbase transfer CSV row and create a destination box."""

    REQUIRED_SOURCE_FIELDS = {
        "format",
        "transfer_id",
        "source_organization_name",
        "source_global_code",
        "species_scientific_name",
        "strain_code",
        "transferred_polyp_count",
    }

    @transaction.atomic
    def post(self, request):
        source = request.data.get("source_data")
        if not isinstance(source, dict):
            raise DRFValidationError({"source_data": "Le contenu CSV est invalide."})
        missing = sorted(field for field in self.REQUIRED_SOURCE_FIELDS if not str(source.get(field, "")).strip())
        if missing:
            raise DRFValidationError({"source_data": f"Colonnes obligatoires manquantes : {', '.join(missing)}"})
        if source["format"] != "polypbase.box_transfer.v1":
            raise DRFValidationError({"source_data": "Version de transfert Polypbase non reconnue."})

        organization = get_object_or_404(
            get_authorized_organizations(request.user).filter(id__in=get_active_organization_ids(request)),
            pk=request.data.get("organization"),
        )
        if organization.id not in get_active_admin_organization_ids(request):
            raise PermissionDenied("Ce compte ne peut pas importer dans cette structure.")
        zone = get_object_or_404(
            ThermalZone,
            pk=request.data.get("thermal_zone"),
            organization=organization,
            is_active=True,
        )
        if BoxTransferImport.objects.filter(
            format_version=source["format"],
            source_organization_name=source["source_organization_name"],
            source_transfer_id=str(source["transfer_id"]),
        ).exists():
            raise DRFValidationError("Ce transfert a déjà été importé.")
        try:
            polyp_count = int(source["transferred_polyp_count"])
        except (TypeError, ValueError) as exc:
            raise DRFValidationError({"source_data": "Le nombre de polypes est invalide."}) from exc
        if polyp_count < 1:
            raise DRFValidationError({"source_data": "Le nombre de polypes doit être positif."})

        species, _ = Species.objects.get_or_create(
            scientific_name=str(source["species_scientific_name"]).strip(),
            defaults={
                "common_name": str(source.get("species_common_name", "")).strip(),
                "genus_species_code": str(source.get("species_code", "")).strip(),
            },
        )
        strain, _ = Strain.objects.get_or_create(
            species=species,
            code=str(source["strain_code"]).strip(),
            defaults={"origin_code": str(source.get("strain_origin_code", "")).strip()},
        )
        suggested_global_code, suggested_box_number = _next_unique_box_identity(strain)
        requested_global_code = str(request.data.get("global_code", "")).strip()
        if requested_global_code:
            code_match = re.fullmatch(rf"{re.escape(strain.code)}\.(\d+)", requested_global_code)
            if not code_match:
                raise DRFValidationError({
                    "global_code": (
                        f"Le code doit commencer par {strain.code}. et finir par un numéro. "
                        f"Suggestion : {suggested_global_code}"
                    )
                })
            if Box.objects.filter(global_code=requested_global_code).exists():
                raise DRFValidationError({
                    "global_code": f"Ce code existe déjà. Suggestion : {suggested_global_code}"
                })
            global_code = requested_global_code
            box_number = code_match.group(1)
        else:
            global_code, box_number = suggested_global_code, suggested_box_number
        box = Box.objects.create(
            organization=organization,
            global_code=global_code,
            local_code="",
            box_number=box_number,
            strain=strain,
            thermal_zone=zone,
            entered_on=timezone.localdate(),
            notes=(
                f"Import du transfert {source['transfer_id']} depuis "
                f"{source['source_organization_name']} (boîte source {source['source_global_code']})."
            ),
        )
        BoxLocation.objects.create(box=box, thermal_zone=zone, starts_at=timezone.now())
        BiologicalMeasurement.objects.create(
            box=box,
            measured_on=timezone.localdate(),
            polyp_count=polyp_count,
            ephyrae_count=0,
            culture_status=str(source.get("latest_culture_status") or "not_specified"),
            notes="Nombre initial reçu lors du transfert.",
            user=request.user,
        )
        transfer_import = BoxTransferImport.objects.create(
            format_version=source["format"],
            source_transfer_id=str(source["transfer_id"]),
            source_organization_name=str(source["source_organization_name"]),
            source_global_code=str(source["source_global_code"]),
            destination_organization=organization,
            created_box=box,
            imported_by=request.user,
            source_data=source,
        )
        AuditLog.objects.create(
            organization=organization,
            user=request.user,
            action=AuditLog.Action.IMPORT,
            object_type="box",
            object_id=box.global_code,
            description=f"Transfer imported from {source['source_organization_name']}",
            metadata={
                "transfer_import_id": transfer_import.id,
                "source_transfer_id": source["transfer_id"],
                "source_global_code": source["source_global_code"],
                "created_box_id": box.id,
            },
        )
        return Response(
            BoxDetailSerializer(
                box_queryset_for_user(
                    request.user,
                    organization_ids=get_active_organization_ids(request),
                ).get(pk=box.pk)
            ).data,
            status=201,
        )


class AlertResolveAPIView(APIView):
    def post(self, request, pk):
        alert = get_object_or_404(
            Alert.objects.select_related("organization"),
            pk=pk,
            organization_id__in=get_active_organization_ids(request),
        )
        if not user_can_write_lab_data(request.user, alert.organization):
            raise PermissionDenied("Ce compte ne peut pas résoudre cette alerte.")
        if alert.alert_type == Alert.AlertType.BIOLOGICAL:
            raise PermissionDenied(
                "Cette alerte de polypes se résout automatiquement au prochain relevé."
            )
        if alert.resolved_at is None:
            alert.resolved_at = timezone.now()
            alert.resolved_by = request.user
            alert.save(update_fields=["resolved_at", "resolved_by"])
            AuditLog.objects.create(
                organization=alert.organization,
                user=request.user,
                action=AuditLog.Action.UPDATE,
                object_type="alert",
                object_id=str(alert.id),
                description=f"Alert resolved: {alert.message}",
                metadata={"alert_id": alert.id, "alert_type": alert.alert_type},
            )
        return Response({"id": alert.id, "resolved": True})
