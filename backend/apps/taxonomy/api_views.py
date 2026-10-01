from django.db import IntegrityError, transaction
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
from .services import create_local_strain_identity
from .serializers import (
    BiologicalProvenanceSerializer,
    BiologicalProvenanceWriteSerializer,
    ProvenanceCodeSerializer,
    ProvenanceCodeWriteSerializer,
    SpeciesCodeSerializer,
    SpeciesCodeWriteSerializer,
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


def _check_species_code_conflicts(organization, *, species=None, code=None, exclude_pk=None):
    assignments = _species_code_queryset(organization)
    if exclude_pk is not None:
        assignments = assignments.exclude(pk=exclude_pk)
    if species is not None and assignments.filter(species=species).exists():
        raise ValidationError({"species": "This species already has a code in this organization."})
    if code is not None and assignments.filter(code=code).exists():
        raise ValidationError({"code": "This code is already used in this organization."})


def _raise_species_code_conflict(error):
    constraint = getattr(getattr(error.__cause__, "diag", None), "constraint_name", None)
    # SQLite test databases report unique columns rather than constraint names.
    if constraint is None:
        constraint = str(error)
    if constraint in (
        "unique_species_code_per_organization_species",
        "UNIQUE constraint failed: taxonomy_organizationspeciescode.organization_id, taxonomy_organizationspeciescode.species_id",
    ):
        raise ValidationError({"species": "This species already has a code in this organization."}) from error
    if constraint in (
        "unique_species_code_per_organization_code",
        "UNIQUE constraint failed: taxonomy_organizationspeciescode.organization_id, taxonomy_organizationspeciescode.code",
    ):
        raise ValidationError({"code": "This code is already used in this organization."}) from error
    raise error


class BiologicalProvenanceListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = get_active_organization_from_request(request)
        if organization is None or not user_can_write_lab_data(request.user, organization):
            raise PermissionDenied("Laboratory access is required.")
        return Response(BiologicalProvenanceSerializer(
            BiologicalProvenance.objects.order_by("pk"), many=True
        ).data)

    @transaction.atomic
    def post(self, request):
        _require_active_admin(request)
        serializer = BiologicalProvenanceWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        provenance = serializer.save()
        _write_audit_log(
            request,
            action=AuditLog.Action.CREATION,
            object_type="biological_provenance",
            instance=provenance,
            description=f"Biological provenance created: {provenance.name}",
        )
        return Response(
            BiologicalProvenanceSerializer(provenance).data,
            status=status.HTTP_201_CREATED,
        )


def _provenance_code_queryset(organization):
    return OrganizationProvenanceCode.objects.filter(
        organization=organization
    ).select_related("biological_provenance")


def _check_provenance_code_conflicts(organization, *, provenance, code):
    assignments = _provenance_code_queryset(organization)
    if assignments.filter(biological_provenance=provenance).exists():
        raise ValidationError({
            "biological_provenance": "This provenance already has a code in this organization."
        })
    if assignments.filter(code=code).exists():
        raise ValidationError({"code": "This code is already used in this organization."})


def _raise_provenance_code_conflict(error):
    constraint = getattr(getattr(error.__cause__, "diag", None), "constraint_name", None)
    # SQLite test databases report unique columns rather than constraint names.
    if constraint is None:
        constraint = str(error)
    if constraint in (
        "unique_provenance_code_per_organization_source",
        "UNIQUE constraint failed: taxonomy_organizationprovenancecode.organization_id, taxonomy_organizationprovenancecode.biological_provenance_id",
    ):
        raise ValidationError({
            "biological_provenance": "This provenance already has a code in this organization."
        }) from error
    if constraint in (
        "unique_provenance_code_per_organization_code",
        "UNIQUE constraint failed: taxonomy_organizationprovenancecode.organization_id, taxonomy_organizationprovenancecode.code",
    ):
        raise ValidationError({"code": "This code is already used in this organization."}) from error
    raise error


class ProvenanceCodeListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = get_active_organization_from_request(request)
        if organization is None or not user_can_write_lab_data(request.user, organization):
            raise PermissionDenied("Laboratory access is required.")
        return Response(ProvenanceCodeSerializer(
            _provenance_code_queryset(organization).order_by("pk"), many=True
        ).data)

    @transaction.atomic
    def post(self, request):
        organization = _require_active_admin(request)
        serializer = ProvenanceCodeWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        provenance = serializer.validated_data["biological_provenance"]
        code = serializer.validated_data["code"]
        _check_provenance_code_conflicts(organization, provenance=provenance, code=code)
        try:
            with transaction.atomic():
                assignment = serializer.save(organization=organization)
        except IntegrityError as error:
            _raise_provenance_code_conflict(error)
        _write_audit_log(
            request,
            action=AuditLog.Action.CREATION,
            object_type="organization_provenance_code",
            instance=assignment,
            description=f"Provenance code created for provenance {provenance.pk}",
        )
        return Response(ProvenanceCodeSerializer(assignment).data, status=status.HTTP_201_CREATED)


class SpeciesCodeListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = get_active_organization_from_request(request)
        if organization is None or not user_can_write_lab_data(request.user, organization):
            raise PermissionDenied("Laboratory access is required.")
        assignments = _species_code_queryset(organization).order_by("pk")
        return Response(SpeciesCodeSerializer(assignments, many=True).data)

    @transaction.atomic
    def post(self, request):
        organization = _require_active_admin(request)
        serializer = SpeciesCodeWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        species = serializer.validated_data["species"]
        code = serializer.validated_data["code"]
        _check_species_code_conflicts(organization, species=species, code=code)
        try:
            with transaction.atomic():
                assignment = serializer.save(organization=organization)
        except IntegrityError as error:
            _raise_species_code_conflict(error)
        _write_audit_log(
            request,
            action=AuditLog.Action.CREATION,
            object_type="organization_species_code",
            instance=assignment,
            description=f"Species code created for species {species.pk}",
        )
        return Response(SpeciesCodeSerializer(assignment).data, status=status.HTTP_201_CREATED)


class SpeciesCodeDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        organization = get_active_organization_from_request(request)
        if organization is None or not user_can_write_lab_data(request.user, organization):
            raise PermissionDenied("Laboratory access is required.")
        assignment = get_object_or_404(_species_code_queryset(organization), pk=pk)
        return Response(SpeciesCodeSerializer(assignment).data)

    @transaction.atomic
    def patch(self, request, pk):
        organization = _require_active_admin(request)
        assignment = get_object_or_404(_species_code_queryset(organization), pk=pk)
        serializer = SpeciesCodeWriteSerializer(assignment, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        if "code" not in serializer.validated_data:
            raise ValidationError({"code": "This field is required."})
        code = serializer.validated_data["code"]
        _check_species_code_conflicts(organization, code=code, exclude_pk=assignment.pk)
        try:
            with transaction.atomic():
                assignment = serializer.save()
        except IntegrityError as error:
            _raise_species_code_conflict(error)
        _write_audit_log(
            request,
            action=AuditLog.Action.UPDATE,
            object_type="organization_species_code",
            instance=assignment,
            description=f"Species code updated for species {assignment.species_id}",
        )
        return Response(SpeciesCodeSerializer(assignment).data)


class SpeciesReferenceListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        organization = _require_active_admin(request)
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
        species = _species_queryset(organization).get(pk=species.pk)
        return Response(
            SpeciesReferenceSerializer(species).data,
            status=status.HTTP_201_CREATED,
        )


class SpeciesReferenceDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, pk):
        organization = _require_active_admin(request)
        species = get_object_or_404(_species_queryset(organization), pk=pk)
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
        species = _species_queryset(organization).get(pk=species.pk)
        return Response(SpeciesReferenceSerializer(species).data)


class StrainReferenceListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        organization = _require_strain_creation_organization(request)
        serializer = StrainReferenceWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        species = serializer.validated_data["species"]
        assignment = _species_code_queryset(organization).filter(species=species).first()
        if assignment is None:
            raise ValidationError({
                "species": "Assign an AAA code to this species in the active institution in Administration before creating a strain."
            })
        strain = serializer.save(organization=organization)
        create_local_strain_identity(
            strain=strain,
            organization=organization,
            species_code_assignment=assignment,
        )
        _write_audit_log(
            request,
            action=AuditLog.Action.CREATION,
            object_type="strain",
            instance=strain,
            description=f"Strain created: {strain.code}",
        )
        strain = _strain_queryset(organization).get(pk=strain.pk)
        return Response(
            StrainReferenceSerializer(strain).data,
            status=status.HTTP_201_CREATED,
        )


class StrainReferenceDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, pk):
        organization = _require_active_admin(request)
        # Share the identity service's Strain-first lock through mutation and audit.
        strain = get_object_or_404(
            Strain.objects.select_for_update().filter(organization=organization), pk=pk
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
