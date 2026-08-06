from django.db.models import Count, Prefetch
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import (
    get_active_admin_organization_ids,
    get_active_organization_from_request,
)
from apps.audit.models import AuditLog

from .models import Species, SpeciesTranslation, Strain, StrainTranslation
from .serializers import (
    SpeciesReferenceSerializer,
    SpeciesReferenceWriteSerializer,
    StrainReferenceSerializer,
    StrainReferenceWriteSerializer,
    available_content_languages,
)


def _require_active_admin(request):
    if not get_active_admin_organization_ids(request):
        raise PermissionDenied("Administrator access is required.")
    return get_active_organization_from_request(request)


def _species_queryset():
    return (
        Species.objects.annotate(strain_count=Count("strains"))
        .prefetch_related(
            Prefetch(
                "translations",
                queryset=SpeciesTranslation.objects.order_by("language_code"),
            )
        )
        .order_by("scientific_name")
    )


def _strain_queryset():
    return (
        Strain.objects.select_related("species")
        .prefetch_related(
            Prefetch(
                "translations",
                queryset=StrainTranslation.objects.order_by("language_code"),
            )
        )
        .order_by("species__scientific_name", "code")
    )


def _write_audit_log(request, *, action, object_type, instance, description):
    AuditLog.objects.create(
        organization=get_active_organization_from_request(request),
        user=request.user,
        action=action,
        object_type=object_type,
        object_id=str(instance.pk),
        description=description,
    )


class TaxonomyReferenceListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _require_active_admin(request)
        return Response(
            {
                "languages": available_content_languages(),
                "species": SpeciesReferenceSerializer(
                    _species_queryset(),
                    many=True,
                ).data,
                "strains": StrainReferenceSerializer(
                    _strain_queryset(),
                    many=True,
                ).data,
            }
        )


class SpeciesReferenceListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _require_active_admin(request)
        serializer = SpeciesReferenceWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        species = serializer.save()
        _write_audit_log(
            request,
            action=AuditLog.Action.CREATION,
            object_type="species",
            instance=species,
            description=f"Species created: {species.scientific_name}",
        )
        species = _species_queryset().get(pk=species.pk)
        return Response(
            SpeciesReferenceSerializer(species).data,
            status=status.HTTP_201_CREATED,
        )


class SpeciesReferenceDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        _require_active_admin(request)
        species = get_object_or_404(_species_queryset(), pk=pk)
        serializer = SpeciesReferenceWriteSerializer(
            species,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        species = serializer.save()
        _write_audit_log(
            request,
            action=AuditLog.Action.UPDATE,
            object_type="species",
            instance=species,
            description=f"Species updated: {species.scientific_name}",
        )
        species = _species_queryset().get(pk=species.pk)
        return Response(SpeciesReferenceSerializer(species).data)


class StrainReferenceListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _require_active_admin(request)
        serializer = StrainReferenceWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        strain = serializer.save()
        _write_audit_log(
            request,
            action=AuditLog.Action.CREATION,
            object_type="strain",
            instance=strain,
            description=f"Strain created: {strain.code}",
        )
        strain = _strain_queryset().get(pk=strain.pk)
        return Response(
            StrainReferenceSerializer(strain).data,
            status=status.HTTP_201_CREATED,
        )


class StrainReferenceDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        _require_active_admin(request)
        strain = get_object_or_404(_strain_queryset(), pk=pk)
        serializer = StrainReferenceWriteSerializer(
            strain,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        strain = serializer.save()
        _write_audit_log(
            request,
            action=AuditLog.Action.UPDATE,
            object_type="strain",
            instance=strain,
            description=f"Strain updated: {strain.code}",
        )
        strain = _strain_queryset().get(pk=strain.pk)
        return Response(StrainReferenceSerializer(strain).data)
