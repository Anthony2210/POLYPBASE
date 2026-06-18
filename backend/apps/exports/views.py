from datetime import date

from django.http import HttpResponse
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import (
    get_authorized_organization_ids,
    get_authorized_organizations,
)
from apps.audit.models import AuditLog
from apps.cultures.models import Box, ThermalZone
from apps.taxonomy.models import Species, Strain

from .models import DataExport
from .serializers import MeasurementExportFilterSerializer
from .services import build_weekly_measurement_csv, build_weekly_measurement_preview


class MeasurementExportOptionsAPIView(APIView):
    """Return the values available in the cumulative export filters."""

    def get(self, request):
        organization_ids = get_authorized_organization_ids(request.user)
        boxes = Box.objects.filter(
            organization_id__in=organization_ids,
        ).select_related(
            "organization",
            "strain",
            "strain__species",
            "thermal_zone",
        ).order_by(
            "global_code",
        )
        species_ids = boxes.values_list("strain__species_id", flat=True).distinct()
        strain_ids = boxes.values_list("strain_id", flat=True).distinct()

        return Response(
            {
                "organizations": [
                    {"id": organization.id, "name": organization.name}
                    for organization in get_authorized_organizations(request.user).order_by("name")
                ],
                "species": [
                    {"id": species.id, "name": species.scientific_name}
                    for species in Species.objects.filter(id__in=species_ids).order_by("scientific_name")
                ],
                "strains": [
                    {
                        "id": strain.id,
                        "code": strain.code,
                        "species_id": strain.species_id,
                        "species_name": strain.species.scientific_name,
                    }
                    for strain in Strain.objects.filter(id__in=strain_ids)
                    .select_related("species")
                    .order_by("species__scientific_name", "code")
                ],
                "boxes": [
                    {
                        "id": box.id,
                        "global_code": box.global_code,
                        "local_code": box.local_code,
                        "species_id": box.strain.species_id,
                        "strain_id": box.strain_id,
                        "thermal_zone_id": box.thermal_zone_id,
                        "organization_id": box.organization_id,
                    }
                    for box in boxes
                ],
                "zones": [
                    {
                        "id": zone.id,
                        "name": zone.name,
                        "organization_id": zone.organization_id,
                    }
                    for zone in ThermalZone.objects.filter(
                        organization_id__in=organization_ids,
                    ).order_by("organization__name", "name")
                ],
            }
        )


class WeeklyMeasurementCSVExportAPIView(APIView):
    """Export biological measurements in the historical weekly wide format."""

    def get(self, request):
        filters = _get_validated_export_filters(request)
        boxes = _get_filtered_export_boxes(request.user, filters)

        if not boxes.exists():
            return Response(
                {"detail": "Aucune boîte ne correspond aux filtres sélectionnés."},
                status=400,
            )

        csv_content, metadata = build_weekly_measurement_csv(
            boxes=boxes,
            date_from=filters.get("date_from"),
            date_to=filters.get("date_to"),
        )
        filename = f"polypbase_suivi_{date.today():%Y%m%d}.csv"
        recorded_filters = {
            key: value.isoformat() if hasattr(value, "isoformat") else value
            for key, value in filters.items()
            if value not in (None, [], "")
        }

        exported_organization_ids = list(
            boxes.values_list("organization_id", flat=True).distinct()
        )
        for organization_id in exported_organization_ids:
            DataExport.objects.create(
                organization_id=organization_id,
                export_type=DataExport.ExportType.MEASUREMENTS,
                file_format=DataExport.FileFormat.CSV,
                filters=recorded_filters,
                file_name=filename,
                user=request.user,
            )
            AuditLog.objects.create(
                organization_id=organization_id,
                user=request.user,
                action=AuditLog.Action.EXPORT,
                object_type="measurements",
                object_id=filename,
                description="Weekly biological measurement CSV export",
                metadata={
                    **recorded_filters,
                    "box_count": metadata["box_count"],
                    "measurement_count": metadata["measurement_count"],
                    "week_count": metadata["week_count"],
                },
            )

        response = HttpResponse(
            f"\ufeff{csv_content}",
            content_type="text/csv; charset=utf-8",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class WeeklyMeasurementPreviewAPIView(APIView):
    """Return aggregated values used by the export preview chart."""

    def get(self, request):
        filters = _get_validated_export_filters(request)
        boxes = _get_filtered_export_boxes(request.user, filters)

        if not boxes.exists():
            return Response(
                {
                    "points": [],
                    "metadata": {
                        "box_count": 0,
                        "measurement_count": 0,
                        "week_count": 0,
                        "date_from": None,
                        "date_to": None,
                    },
                }
            )

        return Response(
            build_weekly_measurement_preview(
                boxes=boxes,
                date_from=filters.get("date_from"),
                date_to=filters.get("date_to"),
            )
        )


def _get_validated_export_filters(request):
    serializer = MeasurementExportFilterSerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _get_filtered_export_boxes(user, filters):
    authorized_organization_ids = set(get_authorized_organization_ids(user))
    requested_organization_ids = set(filters["organizations"])
    if requested_organization_ids - authorized_organization_ids:
        raise PermissionDenied("This user cannot export data from the requested organization.")

    organization_ids = requested_organization_ids or authorized_organization_ids
    boxes = Box.objects.filter(organization_id__in=organization_ids)
    return _apply_measurement_export_filters(boxes, filters)


def _apply_measurement_export_filters(boxes, filters):
    if filters["species"]:
        boxes = boxes.filter(strain__species_id__in=filters["species"])
    if filters["strains"]:
        boxes = boxes.filter(strain_id__in=filters["strains"])
    if filters["boxes"]:
        boxes = boxes.filter(id__in=filters["boxes"])
    if filters["zones"]:
        boxes = boxes.filter(thermal_zone_id__in=filters["zones"])
    return boxes.distinct()
