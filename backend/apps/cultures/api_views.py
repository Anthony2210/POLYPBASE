import re
from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError, connection, transaction
from django.db.models import (
    Case,
    Count,
    DateField,
    F,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    When,
)
from django.db.models.functions import Coalesce, ExtractYear
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.pagination import LimitOffsetPagination
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
from apps.measurements.models import (
    BiologicalMeasurement,
    DailyTemperature,
    SalinityMeasurement,
)
from apps.organizations.serializers import OrganizationSummarySerializer
from apps.taxonomy.models import Species, Strain

from .models import (
    Box,
    BoxLineage,
    BoxLocation,
    BoxMovement,
    BoxTransferImport,
    ThermalZone,
)
from .serializers import (
    BOX_INVENTORY_BATCH_MAX_ITEMS,
    HISTORICAL_BOX_IMPORT_DATE,
    AlertSummarySerializer,
    AuditLogAccessSerializer,
    BiologicalMeasurementCreateSerializer,
    BiologicalMeasurementSerializer,
    BoxActivateSerializer,
    BoxBatchQualifySerializer,
    BoxCreateSerializer,
    BoxDeactivateSerializer,
    BoxDetailSerializer,
    BoxInitialLocationSerializer,
    BoxInventorySerializer,
    BoxListSerializer,
    BoxMoveCreateSerializer,
    BoxQualifySerializer,
    BoxTransferCreateSerializer,
    ManualTemperatureCreateSerializer,
    ProbeCreateSerializer,
    SubcultureCreateSerializer,
    SubcultureEventSerializer,
    ThermalZoneCreateSerializer,
    ThermalZoneSerializer,
)
from .services import (
    StaleBoxLocationError,
    assign_unlocated_active_box,
    build_lineage_graph,
    create_subculture,
    deactivate_box,
    move_box_to_thermal_zone,
    qualify_pending_box,
    reactivate_box,
)

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
            queryset=BoxLocation.objects.filter(
                thermal_zone__organization_id__in=organization_ids,
            ).select_related("thermal_zone").order_by("-starts_at"),
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
    movements, locations, tags). Instead we prefetch only the latest
    measurement per box and annotate the active alert count via subqueries.
    Both subqueries are portable (correlated with LIMIT), so they run on
    PostgreSQL and SQLite alike.
    """
    organization_ids = organization_ids or get_authorized_organization_ids(user)

    latest_measurement_id = Subquery(
        BiologicalMeasurement.objects.filter(box_id=OuterRef("box_id"))
        .order_by("-measured_on", "-created_at")
        .values("id")[:1]
    )
    latest_measurements = (
        BiologicalMeasurement.objects.filter(id__in=latest_measurement_id)
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
            Prefetch("biological_measurements", queryset=latest_measurements)
        )
        .filter(organization_id__in=organization_ids)
    )


def box_inventory_queryset_for_user(user, organization_ids=None):
    """Minimal queryset for the paginated administration inventory."""
    organization_ids = organization_ids or get_authorized_organization_ids(user)

    first_measurement_on = Subquery(
        BiologicalMeasurement.objects.filter(box_id=OuterRef("pk"))
        .order_by("measured_on", "created_at")
        .values("measured_on")[:1]
    )

    latest_measurement_on = Subquery(
        BiologicalMeasurement.objects.filter(box_id=OuterRef("pk"))
        .order_by("-measured_on", "-created_at")
        .values("measured_on")[:1],
        output_field=DateField(),
    )

    latest_measurement_id = Subquery(
        BiologicalMeasurement.objects.filter(box_id=OuterRef("box_id"))
        .order_by("-measured_on", "-created_at")
        .values("id")[:1]
    )
    latest_measurements = (
        BiologicalMeasurement.objects.filter(id__in=latest_measurement_id)
        .select_related("user")
        .order_by("-measured_on", "-created_at")
    )

    latest_location_id = Subquery(
        BoxLocation.objects.filter(
            box_id=OuterRef("box_id"),
            thermal_zone__organization_id__in=organization_ids,
        )
        .order_by("-starts_at", "-id")
        .values("id")[:1]
    )
    latest_locations = (
        BoxLocation.objects.filter(
            id__in=latest_location_id,
            thermal_zone__organization_id__in=organization_ids,
        )
        .select_related("thermal_zone")
        .order_by("-starts_at", "-id")
    )

    return (
        Box.objects.select_related(
            "strain",
            "strain__species",
            "thermal_zone",
        )
        .annotate(
            first_measurement_on_annotation=first_measurement_on,
            inventory_created_on_annotation=Case(
                When(
                    created_on=HISTORICAL_BOX_IMPORT_DATE,
                    then=Coalesce(first_measurement_on, F("created_on")),
                ),
                default=F("created_on"),
                output_field=DateField(),
            ),
            latest_measurement_on_annotation=latest_measurement_on,
        )
        .annotate(
            inventory_created_year_annotation=ExtractYear(
                "inventory_created_on_annotation",
            ),
        )
        .prefetch_related(
            Prefetch("biological_measurements", queryset=latest_measurements),
            Prefetch(
                "locations",
                queryset=latest_locations,
                to_attr="inventory_last_locations",
            ),
        )
        .filter(organization_id__in=organization_ids)
    )


def thermal_zone_summary_queryset(queryset):
    """Attach only the latest readings needed by the zone summary serializer."""
    latest_temperature_id = Subquery(
        DailyTemperature.objects.filter(thermal_zone_id=OuterRef("thermal_zone_id"))
        .order_by("-date", "-id")
        .values("id")[:1]
    )
    latest_salinity_id = Subquery(
        SalinityMeasurement.objects.filter(thermal_zone_id=OuterRef("thermal_zone_id"))
        .order_by("-measured_on", "-id")
        .values("id")[:1]
    )

    return queryset.select_related("organization").prefetch_related(
        Prefetch(
            "daily_temperatures",
            queryset=DailyTemperature.objects.filter(id__in=latest_temperature_id).order_by("-date", "-id"),
        ),
        Prefetch(
            "salinity_measurements",
            queryset=(
                SalinityMeasurement.objects.filter(id__in=latest_salinity_id)
                .select_related("user")
                .order_by("-measured_on", "-id")
            ),
        ),
        "probes",
    ).annotate(
        box_count=Count("boxes", filter=Q(boxes__status=Box.Status.ACTIVE))
    )


class HealthAPIView(APIView):
    """Small public endpoint used by deployments and infrastructure checks."""

    permission_classes = [AllowAny]

    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except DatabaseError:
            return Response(
                {
                    "status": "unavailable",
                    "service": "polypbase",
                    "timestamp": timezone.now().isoformat(),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

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
    """Return every active box with its recent biological and temperature history."""

    def get(self, request):
        months = self._get_months(request)
        history_start_date = timezone.localdate() - timedelta(days=months * 31)
        organization_ids = get_active_organization_ids(request)
        location_history = (
            BoxLocation.objects.filter(
                Q(ends_at__isnull=True, end_date_unknown=False)
                | Q(ends_at__date__gte=history_start_date)
            )
            .select_related("thermal_zone")
            .order_by("starts_at")
        )
        boxes = list(
            box_list_queryset_for_user(request.user, organization_ids=organization_ids)
            .filter(status=Box.Status.ACTIVE)
            .prefetch_related(
                Prefetch("locations", queryset=location_history, to_attr="overview_locations")
            )
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
                measured_on__gte=history_start_date,
            )
            .order_by("box_id", "measured_on", "created_at")
        )
        for measurement in measurements:
            measurements_by_box[measurement.box_id].append(
                {
                    "date": measurement.measured_on.isoformat(),
                    "polyp_count": measurement.polyp_count,
                    "ephyrae_count": measurement.ephyrae_count,
                    "salinity_psu": _json_value(measurement.salinity_psu),
                }
            )

        temperatures_by_zone = defaultdict(list)
        temperatures = (
            DailyTemperature.objects.filter(
                thermal_zone_id__in=zone_ids,
                date__gte=history_start_date,
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
            "locations": [
                {
                    "id": location.id,
                    "thermal_zone": {
                        "id": location.thermal_zone.id,
                        "name": location.thermal_zone.name,
                    },
                    "starts_at": location.starts_at.isoformat(),
                    "ends_at": location.ends_at.isoformat() if location.ends_at else None,
                    "end_date_unknown": location.end_date_unknown,
                    "notes": location.notes,
                }
                for location in box.overview_locations
            ],
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


class BoxInventoryPagination(LimitOffsetPagination):
    default_limit = 24
    max_limit = 96


def _subtract_calendar_months(value, months):
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def _box_inventory_filter_state(request):
    reference_date = timezone.localdate()
    raw_reference_date = request.query_params.get("reference_date", "").strip()
    if raw_reference_date:
        try:
            reference_date = date.fromisoformat(raw_reference_date)
        except ValueError as error:
            raise DRFValidationError({"reference_date": "Invalid reference date."}) from error

    creation_year = None
    raw_creation_year = request.query_params.get("creation_year", "").strip()
    if raw_creation_year:
        try:
            creation_year = int(raw_creation_year)
        except ValueError as error:
            raise DRFValidationError({"creation_year": "Invalid creation year."}) from error
        if creation_year < 1 or creation_year > 9999:
            raise DRFValidationError({"creation_year": "Invalid creation year."})

    measurement_filter = request.query_params.get("measurement_filter", "").strip()
    if measurement_filter not in {"", "older_than", "none"}:
        raise DRFValidationError({"measurement_filter": "Unknown measurement filter."})

    age_months = None
    if measurement_filter == "older_than":
        raw_age_months = request.query_params.get("age_months", "").strip()
        try:
            age_months = int(raw_age_months)
        except ValueError as error:
            raise DRFValidationError({"age_months": "Enter a valid number of months."}) from error
        if age_months < 1 or age_months > 1200:
            raise DRFValidationError({"age_months": "Enter between 1 and 1200 months."})

    return {
        "age_months": age_months,
        "creation_year": creation_year,
        "measurement_filter": measurement_filter,
        "reference_date": reference_date,
    }


def _apply_box_inventory_filters(queryset, request, organization_ids, filter_state):
    status_filter = request.query_params.get("status", "").strip()
    if status_filter:
        if status_filter not in Box.Status.values:
            raise DRFValidationError({"status": "Unknown box status."})
        queryset = queryset.filter(status=status_filter)

    location_filter = request.query_params.get("location", "").strip()
    if location_filter == "none":
        queryset = queryset.filter(thermal_zone__isnull=True)
    elif location_filter:
        try:
            thermal_zone_id = int(location_filter)
        except (TypeError, ValueError) as error:
            raise DRFValidationError({"location": "Invalid thermal zone."}) from error
        if not ThermalZone.objects.filter(
            id=thermal_zone_id,
            organization_id__in=organization_ids,
            is_active=True,
        ).exists():
            raise DRFValidationError({"location": "Unknown thermal zone."})
        queryset = queryset.filter(thermal_zone_id=thermal_zone_id)

    search = request.query_params.get("q", "").strip()
    if search:
        queryset = queryset.filter(
            Q(global_code__icontains=search)
            | Q(local_code__icontains=search)
            | Q(strain__species__scientific_name__icontains=search)
        )

    if filter_state["creation_year"] is not None:
        queryset = queryset.filter(
            inventory_created_year_annotation=filter_state["creation_year"],
        )

    if filter_state["measurement_filter"] == "none":
        queryset = queryset.filter(latest_measurement_on_annotation__isnull=True)
    elif filter_state["measurement_filter"] == "older_than":
        cutoff_date = _subtract_calendar_months(
            filter_state["reference_date"],
            filter_state["age_months"],
        )
        queryset = queryset.filter(latest_measurement_on_annotation__lt=cutoff_date)

    return queryset


def _order_box_inventory(queryset):
    return queryset.annotate(
        inventory_status_order=Case(
            When(status=Box.Status.PENDING_REVIEW, then=0),
            When(status=Box.Status.ACTIVE, then=1),
            When(status=Box.Status.INACTIVE, then=2),
            default=3,
            output_field=IntegerField(),
        )
    ).order_by("inventory_status_order", "global_code")


class AdminBoxInventoryListAPIView(generics.ListAPIView):
    """Paginated inventory for administrators of the active organization."""

    serializer_class = BoxInventorySerializer
    pagination_class = BoxInventoryPagination

    def get_queryset(self):
        organization_ids = get_active_admin_organization_ids(self.request)
        if not organization_ids:
            raise PermissionDenied("This user cannot view this box inventory.")

        # Institution-wide counters, independent of the current page and filters.
        self.inventory_summary = Box.objects.filter(
            organization_id__in=organization_ids,
        ).aggregate(
            pending_review_count=Count("pk", filter=Q(status=Box.Status.PENDING_REVIEW)),
            active_without_location_count=Count(
                "pk", filter=Q(status=Box.Status.ACTIVE, thermal_zone__isnull=True),
            ),
            pending_without_location_count=Count(
                "pk", filter=Q(status=Box.Status.PENDING_REVIEW, thermal_zone__isnull=True),
            ),
        )
        queryset = box_inventory_queryset_for_user(
            self.request.user,
            organization_ids=organization_ids,
        )
        self.inventory_filter_state = _box_inventory_filter_state(self.request)
        self.available_creation_years = list(
            queryset.order_by()
            .values_list("inventory_created_year_annotation", flat=True)
            .distinct()
            .order_by("inventory_created_year_annotation")
        )
        queryset = _apply_box_inventory_filters(
            queryset,
            self.request,
            organization_ids,
            self.inventory_filter_state,
        )
        self.selection_eligible_count = queryset.filter(
            status=Box.Status.PENDING_REVIEW,
        ).count()
        return _order_box_inventory(queryset)

    def get_paginated_response(self, data):
        response = super().get_paginated_response(data)
        response.data["summary"] = self.inventory_summary
        response.data["filter_options"] = {
            "creation_years": self.available_creation_years,
            "reference_date": self.inventory_filter_state["reference_date"].isoformat(),
        }
        response.data["selection"] = {
            "eligible_count": self.selection_eligible_count,
            "max_count": BOX_INVENTORY_BATCH_MAX_ITEMS,
        }
        return response


class AdminBoxInventorySelectionAPIView(APIView):
    """Return only the filtered pending boxes needed for an explicit selection."""

    def get(self, request):
        organization_ids = get_active_admin_organization_ids(request)
        if not organization_ids:
            raise PermissionDenied("This user cannot select this box inventory.")

        filter_state = _box_inventory_filter_state(request)
        queryset = _apply_box_inventory_filters(
            box_inventory_queryset_for_user(request.user, organization_ids=organization_ids),
            request,
            organization_ids,
            filter_state,
        )
        counts = queryset.order_by().aggregate(
            matched_count=Count("pk"),
            eligible_count=Count(
                "pk",
                filter=Q(status=Box.Status.PENDING_REVIEW),
            ),
        )
        eligible_count = counts["eligible_count"]
        if eligible_count > BOX_INVENTORY_BATCH_MAX_ITEMS:
            return Response(
                {
                    "detail": (
                        f"The filtered selection contains {eligible_count} eligible boxes. "
                        f"Narrow the filters to {BOX_INVENTORY_BATCH_MAX_ITEMS} boxes or fewer."
                    ),
                    "code": "selection_too_large",
                    "eligible_count": eligible_count,
                    "max_count": BOX_INVENTORY_BATCH_MAX_ITEMS,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        selected_boxes = list(
            _order_box_inventory(queryset.filter(status=Box.Status.PENDING_REVIEW)).values(
                "id",
                "global_code",
                "status",
                "thermal_zone_id",
                "strain__species__scientific_name",
            )
        )
        return Response(
            {
                "matched_count": counts["matched_count"],
                "eligible_count": eligible_count,
                "ineligible_count": counts["matched_count"] - eligible_count,
                "max_count": BOX_INVENTORY_BATCH_MAX_ITEMS,
                "results": [
                    {
                        "id": item["id"],
                        "global_code": item["global_code"],
                        "status": item["status"],
                        "has_location": item["thermal_zone_id"] is not None,
                        "species_name": item["strain__species__scientific_name"],
                    }
                    for item in selected_boxes
                ],
            }
        )


class AdminBoxInventoryBatchQualifyAPIView(APIView):
    """Qualify explicitly selected historical boxes with per-box atomicity."""

    def post(self, request):
        organization_ids = get_active_admin_organization_ids(request)
        if not organization_ids:
            raise PermissionDenied("This user cannot qualify this box inventory.")

        serializer = BoxBatchQualifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        box_ids = serializer.validated_data["box_ids"]
        target_status = serializer.validated_data["target_status"]
        reason = serializer.validated_data["reason"]
        reason_missing = serializer.validated_data["reason_missing_from_history"]

        successes = []
        failures = []
        for box_id in box_ids:
            global_code = None
            success_item = None
            try:
                with transaction.atomic():
                    box = (
                        Box.objects.select_for_update()
                        .filter(id=box_id, organization_id__in=organization_ids)
                        .first()
                    )
                    if box is None:
                        failures.append(
                            {
                                "box_id": box_id,
                                "global_code": None,
                                "error": "Box not found in the active institution.",
                            }
                        )
                        continue

                    global_code = box.global_code
                    qualified_box = qualify_pending_box(
                        box=box,
                        target_status=target_status,
                        user=request.user,
                        reason=reason,
                        reason_missing_from_history=reason_missing,
                    )
                    success_item = {
                        "box_id": qualified_box.id,
                        "global_code": qualified_box.global_code,
                        "status": qualified_box.status,
                        "has_location": qualified_box.thermal_zone_id is not None,
                    }
                successes.append(success_item)
            except DjangoValidationError as exc:
                failures.append(
                    {
                        "box_id": box_id,
                        "global_code": global_code,
                        "error": "; ".join(str(message) for message in exc.messages),
                    }
                )
            except Box.DoesNotExist:
                failures.append(
                    {
                        "box_id": box_id,
                        "global_code": global_code,
                        "error": "Box no longer exists in the active institution.",
                    }
                )
            except DatabaseError:
                failures.append(
                    {
                        "box_id": box_id,
                        "global_code": global_code,
                        "error": "The box could not be updated because of a database error.",
                    }
                )

        active_with_location_count = sum(
            1
            for item in successes
            if item["status"] == Box.Status.ACTIVE and item["has_location"]
        )
        active_without_location_count = sum(
            1
            for item in successes
            if item["status"] == Box.Status.ACTIVE and not item["has_location"]
        )
        return Response(
            {
                "requested_count": len(box_ids),
                "success_count": len(successes),
                "failure_count": len(failures),
                "active_with_location_count": active_with_location_count,
                "active_without_location_count": active_without_location_count,
                "successes": successes,
                "failures": failures,
            },
            status=status.HTTP_200_OK,
        )


class AdminBoxInitialLocationAPIView(APIView):
    """Assign a first current location to an active historical box."""

    def post(self, request, box_id):
        organization_ids = get_active_admin_organization_ids(request)
        if not organization_ids:
            raise PermissionDenied("This user cannot assign box locations.")
        box = get_object_or_404(
            box_queryset_for_user(request.user, organization_ids=organization_ids),
            id=box_id,
        )
        serializer = BoxInitialLocationSerializer(
            data=request.data,
            context={"box": box},
        )
        serializer.is_valid(raise_exception=True)
        try:
            assign_unlocated_active_box(
                box=box,
                thermal_zone=serializer.validated_data["thermal_zone"],
                user=request.user,
                notes=serializer.validated_data["notes"],
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages) from exc

        updated_box = get_object_or_404(
            box_queryset_for_user(request.user, organization_ids=organization_ids),
            id=box.id,
        )
        return Response(BoxDetailSerializer(updated_box).data, status=status.HTTP_200_OK)


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


class BoxDeactivateAPIView(APIView):
    """Mark a box inactive without deleting its history."""

    def post(self, request, box_id):
        box = get_object_or_404(
            box_queryset_for_user(request.user, organization_ids=get_active_organization_ids(request)),
            id=box_id,
        )
        if box.organization_id not in get_active_admin_organization_ids(request):
            raise PermissionDenied("This user cannot deactivate this box.")
        serializer = BoxDeactivateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            deactivate_box(
                box=box,
                user=request.user,
                reason=serializer.validated_data["reason"],
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages) from exc

        updated_box = get_object_or_404(
            box_queryset_for_user(request.user, organization_ids=get_active_organization_ids(request)),
            id=box.id,
        )
        return Response(BoxDetailSerializer(updated_box).data, status=status.HTTP_200_OK)


class BoxActivateAPIView(APIView):
    """Reactivate an inactive box in an explicitly selected location."""

    def post(self, request, box_id):
        box = get_object_or_404(
            box_queryset_for_user(request.user, organization_ids=get_active_organization_ids(request)),
            id=box_id,
        )
        if box.organization_id not in get_active_admin_organization_ids(request):
            raise PermissionDenied("This user cannot activate this box.")
        serializer = BoxActivateSerializer(
            data=request.data,
            context={"box": box},
        )
        serializer.is_valid(raise_exception=True)
        try:
            reactivate_box(
                box=box,
                thermal_zone=serializer.validated_data["thermal_zone"],
                user=request.user,
                notes=serializer.validated_data["notes"],
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages) from exc

        updated_box = get_object_or_404(
            box_queryset_for_user(request.user, organization_ids=get_active_organization_ids(request)),
            id=box.id,
        )
        return Response(BoxDetailSerializer(updated_box).data, status=status.HTTP_200_OK)


class BoxQualifyAPIView(APIView):
    """Resolve the initial review state of one historical box."""

    def post(self, request, box_id):
        box = get_object_or_404(
            box_queryset_for_user(request.user, organization_ids=get_active_organization_ids(request)),
            id=box_id,
        )
        if box.organization_id not in get_active_admin_organization_ids(request):
            raise PermissionDenied("This user cannot qualify this box.")
        serializer = BoxQualifySerializer(data=request.data, context={"box": box})
        serializer.is_valid(raise_exception=True)
        try:
            qualify_pending_box(
                box=box,
                target_status=serializer.validated_data["target_status"],
                user=request.user,
                reason=serializer.validated_data["reason"],
                reason_missing_from_history=serializer.validated_data[
                    "reason_missing_from_history"
                ],
                thermal_zone=serializer.validated_data.get("thermal_zone"),
            )
        except DjangoValidationError as exc:
            raise DRFValidationError(exc.messages) from exc

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
        self._validate_write(request, box)

        serializer = BiologicalMeasurementCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data.copy()
        measured_on = data.pop("measured_on")

        with transaction.atomic():
            box = self._get_box(request, box_id, for_update=True)
            self._validate_write(request, box)
            existing_measurement = (
                BiologicalMeasurement.objects.select_for_update()
                .filter(box=box, measured_on=measured_on)
                .first()
            )
            before_values = (
                _measurement_audit_values(existing_measurement)
                if existing_measurement
                else None
            )

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

    def _get_box(self, request, box_id, *, for_update=False):
        if for_update:
            queryset = Box.objects.select_for_update().select_related("organization").filter(
                organization_id__in=get_active_organization_ids(request)
            )
        else:
            queryset = box_queryset_for_user(
                request.user,
                organization_ids=get_active_organization_ids(request),
            )
        return get_object_or_404(
            queryset,
            id=box_id,
        )

    @staticmethod
    def _validate_write(request, box):
        if not user_can_write_lab_data(request.user, box.organization):
            raise PermissionDenied("This user cannot create or update lab measurements.")
        if box.status == Box.Status.INACTIVE:
            raise DRFValidationError("An inactive box cannot receive a new measurement.")


class BoxMeasurementDetailAPIView(generics.GenericAPIView):
    """Edit a single existing measurement (used by the 'Modifier' action)."""

    serializer_class = BiologicalMeasurementSerializer

    def patch(self, request, box_id, pk):
        with transaction.atomic():
            box = get_object_or_404(
                Box.objects.select_for_update().select_related("organization").filter(
                    organization_id__in=get_active_organization_ids(request)
                ),
                id=box_id,
            )
            if not user_can_write_lab_data(request.user, box.organization):
                raise PermissionDenied("This user cannot update lab measurements.")

            measurement = get_object_or_404(
                BiologicalMeasurement.objects.select_for_update().filter(box=box),
                id=pk,
            )
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
        except StaleBoxLocationError as error:
            return Response(
                {
                    "code": "box_location_changed",
                    "detail": error.messages[0],
                    "expected_thermal_zone_id": error.expected_thermal_zone_id,
                    "current_thermal_zone_id": error.current_thermal_zone_id,
                    "current_thermal_zone_name": error.current_thermal_zone_name,
                },
                status=status.HTTP_409_CONFLICT,
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
        return thermal_zone_summary_queryset(
            ThermalZone.objects.filter(organization_id__in=organization_ids)
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
    @transaction.atomic
    def post(self, request, pk):
        zone = get_object_or_404(
            # The zone exists before its daily aggregate, so locking it also
            # serializes concurrent first readings for the same zone.
            ThermalZone.objects.select_for_update().select_related("organization"),
            pk=pk,
            organization_id__in=get_active_organization_ids(request),
        )
        if not user_can_write_lab_data(request.user, zone.organization):
            raise PermissionDenied("Ce compte ne peut pas saisir de température pour cet emplacement.")

        serializer = ManualTemperatureCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        measured_on = serializer.validated_data["measured_on"]
        temperature_c = serializer.validated_data["temperature_c"]

        daily_temperature, created = DailyTemperature.objects.select_for_update().get_or_create(
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

        refreshed_zone = thermal_zone_summary_queryset(
            ThermalZone.objects.filter(pk=zone.pk)
        ).get()
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
