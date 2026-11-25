from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import (
    get_active_admin_organization_ids,
    get_active_organization_from_request,
    get_authorized_organizations,
    user_can_write_lab_data,
)
from apps.audit.models import AuditLog
from apps.cultures.box_codes import lock_local_id_registry

from .models import (
    BiologicalProvenance,
    LocalStrainIdentity,
    OrganizationProvenanceCode,
    OrganizationSpeciesCode,
    Species,
    SpeciesTranslation,
    Strain,
    StrainTranslation,
)
from .scoping import eligible_strains
from .species_codes import lock_species_code_allocation
from .serializers import (
    BiologicalProvenanceSerializer,
    ProvenanceCodeSerializer,
    SpeciesCodeSerializer,
    SpeciesReferenceSerializer,
    StrainReferenceSerializer,
    StrainReferenceWriteSerializer,
    available_content_languages,
)


def _require_active_admin(request):
    if not get_active_admin_organization_ids(request):
        raise PermissionDenied("Administrator access is required.")
    return get_active_organization_from_request(request)


def _require_strain_creation_organization(request):
    if (
        not request.headers.get("X-Organization-Id")
        and get_authorized_organizations(request.user).filter(is_active=True).count() != 1
    ):
        raise PermissionDenied("Explicit organization context is required to create a strain.")
    return _require_active_admin(request)


def _species_queryset(organization):
    return (
        Species.objects.annotate(
            strain_count=Count(
                "strains",
                filter=Q(strains__pk__in=eligible_strains(organization).values("pk")),
                distinct=True,
            )
        )
        .prefetch_related(
            Prefetch(
                "translations",
                queryset=SpeciesTranslation.objects.order_by("language_code"),
            )
        )
        .order_by("scientific_name")
    )


def _strain_queryset(organization):
    return (
        eligible_strains(organization).select_related("species")
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
        organization = get_active_organization_from_request(request)
        if organization is None or not user_can_write_lab_data(request.user, organization):
            raise PermissionDenied("Laboratory access is required.")
        return Response(
            {
                "languages": available_content_languages(),
                "species": SpeciesReferenceSerializer(
                    _species_queryset(organization),
                    many=True,
                ).data,
                "strains": StrainReferenceSerializer(
                    _strain_queryset(organization),
                    many=True,
                ).data,
            }
        )


def _species_code_queryset(organization):
    return OrganizationSpeciesCode.objects.filter(organization=organization).select_related("species")


def _visible_provenances(organization):
    """Provenances explicitly associated with the active institution.

    A shared ``BiologicalProvenance`` row is visible only through the active
    institution's own ``OrganizationProvenanceCode``. Unassigned legacy rows and
    another institution's private collection locations are never returned.
    """
    return BiologicalProvenance.objects.filter(
        local_code_assignments__organization=organization,
    ).distinct().order_by("pk")


class BiologicalProvenanceListAPIView(APIView):
    """Read-only legacy collection; creation belongs to institutional Strain creation."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = get_active_organization_from_request(request)
        if organization is None or not user_can_write_lab_data(request.user, organization):
            raise PermissionDenied("Laboratory access is required.")
        return Response(BiologicalProvenanceSerializer(
            _visible_provenances(organization), many=True
        ).data)


def _provenance_code_queryset(organization):
    return OrganizationProvenanceCode.objects.filter(
        organization=organization
    ).select_related("biological_provenance")


class ProvenanceCodeListAPIView(APIView):
    """Read verified assignments without allowing historical location claims by ID."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = get_active_organization_from_request(request)
        if organization is None or not user_can_write_lab_data(request.user, organization):
            raise PermissionDenied("Laboratory access is required.")
        return Response(ProvenanceCodeSerializer(
            _provenance_code_queryset(organization).order_by("pk"), many=True
        ).data)


class SpeciesCodeListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = get_active_organization_from_request(request)
        if organization is None or not user_can_write_lab_data(request.user, organization):
            raise PermissionDenied("Laboratory access is required.")
        assignments = _species_code_queryset(organization).order_by("pk")
        return Response(SpeciesCodeSerializer(assignments, many=True).data)


class SpeciesCodeDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        organization = get_active_organization_from_request(request)
        if organization is None or not user_can_write_lab_data(request.user, organization):
            raise PermissionDenied("Laboratory access is required.")
        assignment = get_object_or_404(_species_code_queryset(organization), pk=pk)
        return Response(SpeciesCodeSerializer(assignment).data)


class StrainReferenceListCreateAPIView(APIView):
    """Closed: X is allocated and AAA-BBB-X built by the server.

    Strains are created from an institutional Species in Administration
    (``POST /api/admin/institution-species/<id>/strains/``); manually supplied
    codes or numbers are no longer accepted.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        _require_strain_creation_organization(request)
        return Response(
            {"code": "superseded_by_institution_species_strains"}, status=status.HTTP_410_GONE,
        )


STRAIN_IDENTITY_FIELDS = ("code", "number", "origin_code")


def _requested_strain_identity_changes(strain, data):
    """Return the identity fields whose requested value differs from the stored one.

    ``code`` (AAA-BBB-X), ``number`` (X) and ``origin_code`` (BBB) prefix issued
    Box identifiers and back the local identity, so this ordinary endpoint never
    rewrites them. Values are normalized like the write serializer; a value that
    cannot be read is a change. ``0`` is a real number, distinct from ``null``.
    """
    if not hasattr(data, "keys"):
        return []
    fields = StrainReferenceWriteSerializer().fields
    changed = []
    for name in STRAIN_IDENTITY_FIELDS:
        if name not in data:
            continue
        try:
            value = fields[name].run_validation(data[name])
        except ValidationError:
            changed.append(name)
            continue
        if isinstance(value, str):
            value = value.strip().upper()
        if value != getattr(strain, name):
            changed.append(name)
    return changed


class StrainReferenceDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, pk):
        organization = _require_active_admin(request)
        # Coordinate Species reassignment with readiness checks before row locks.
        lock_local_id_registry()
        lock_species_code_allocation()
        strain = get_object_or_404(
            Strain.objects.select_for_update().filter(organization=organization), pk=pk
        )
        identity_changes = _requested_strain_identity_changes(strain, request.data)
        if identity_changes:
            # Rejected before any write or audit; never silently ignored.
            return Response(
                {
                    "detail": "Strain identity fields cannot be changed here.",
                    "error": "strain_identity_immutable",
                    "fields": identity_changes,
                },
                status=status.HTTP_409_CONFLICT,
            )
        serializer = StrainReferenceWriteSerializer(
            strain,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        species = serializer.validated_data.get("species")
        if (
            species is not None
            and species.pk != strain.species_id
            and LocalStrainIdentity.objects.filter(strain_id=strain.pk).exists()
        ):
            raise ValidationError({
                "species": _("The species of a strain with a local identity cannot be changed through this endpoint.")
            })
        strain = serializer.save()
        _write_audit_log(
            request,
            action=AuditLog.Action.UPDATE,
            object_type="strain",
            instance=strain,
            description=f"Strain updated: {strain.code}",
        )
        strain = _strain_queryset(organization).get(pk=strain.pk)
        return Response(StrainReferenceSerializer(strain).data)
