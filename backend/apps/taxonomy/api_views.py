from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.validators import UniqueValidator

from apps.accounts.permissions import (
    get_active_admin_organization_ids,
    get_active_organization_from_request,
    get_authorized_organizations,
    user_can_write_lab_data,
)
from apps.audit.models import AuditLog
from apps.cultures.box_codes import lock_local_id_registry
from apps.cultures.models import Box

from .models import (
    BiologicalProvenance,
    InstitutionSpecies,
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
        lock_species_code_allocation()
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
        lock_species_code_allocation()
        assignment = get_object_or_404(_species_code_queryset(organization), pk=pk)
        serializer = SpeciesCodeWriteSerializer(assignment, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        if "code" not in serializer.validated_data:
            raise ValidationError({"code": "This field is required."})
        if serializer.validated_data["code"] != assignment.code:
            # Assigned codes prefix issued Strain and Box identifiers: immutable.
            return Response(
                {"detail": "An assigned species code cannot be changed.", "error": "species_code_immutable"},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(SpeciesCodeSerializer(assignment).data)


def _save_species_reference(serializer):
    try:
        with transaction.atomic():
            return serializer.save()
    except IntegrityError as error:
        constraint = getattr(getattr(error.__cause__, "diag", None), "constraint_name", None)
        if constraint in (
            "taxonomy_species_scientific_name_key",
            "unique_species_scientific_name_ci",
            "unique_species_scientific_name_normalized",
        ) or any(name in str(error) for name in (
            "taxonomy_species.scientific_name",
            "unique_species_scientific_name_ci",
            "unique_species_scientific_name_normalized",
        )):
            raise ValidationError({"scientific_name": [UniqueValidator.message]}) from error
        raise


def _shared_species_update_refusal(organization, species, validated_data):
    """Return why the legacy shared-reference edit is refused, or None.

    Shared information (name, AphiaID, translations, notes) is visible to every
    institution using the reference, so an institution may not change it once
    another institution uses it. A reference carrying institutional information
    is frozen: local corrections go through the institutional edit service.
    Legacy single-institution edits otherwise keep their current behavior.
    """
    used_elsewhere = (
        OrganizationSpeciesCode.objects.filter(species=species).exclude(organization=organization).exists()
        or InstitutionSpecies.objects.filter(species=species).exclude(organization=organization).exists()
        or Strain.objects.filter(species=species, organization__isnull=False)
        .exclude(organization=organization).exists()
        or Box.objects.filter(strain__species=species, organization__isnull=False)
        .exclude(organization=organization).exists()
    )
    if used_elsewhere:
        return "shared_species_used_by_another_institution"
    identity_changed = any(
        field in validated_data and validated_data[field] != getattr(species, field)
        for field in ("scientific_name", "worms_aphia_id")
    )
    if identity_changed and InstitutionSpecies.objects.filter(species=species).exists():
        return "shared_species_reference_frozen"
    return None


class SpeciesReferenceListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        organization = _require_active_admin(request)
        serializer = SpeciesReferenceWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        species = _save_species_reference(serializer)
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
        # The guard below reads other institutions' use of this reference, so it
        # must not race a concurrent attachment. Every product path that can
        # newly attach an institution already serializes on one of these locks:
        # institutional registration and code assignment take the allocation
        # gate; the Transfer v1 import row-locks the Species it resolves. Box
        # and Strain creation require a prior attachment. Gate before row lock,
        # matching registration's gate-first order.
        lock_species_code_allocation()
        get_object_or_404(Species.objects.select_for_update(no_key=True), pk=pk)
        species = get_object_or_404(_species_queryset(organization), pk=pk)
        serializer = SpeciesReferenceWriteSerializer(
            species,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        refusal = _shared_species_update_refusal(organization, species, serializer.validated_data)
        if refusal is not None:
            return Response(
                {"detail": "This shared species reference cannot be modified here.", "error": refusal},
                status=status.HTTP_409_CONFLICT,
            )
        species = _save_species_reference(serializer)
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
