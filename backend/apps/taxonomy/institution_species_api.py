"""Administration API for institutional Species information (backend only).

Every route requires an explicit ``X-Organization-Id`` and active Admin
authority on it. Responses carry only the active institution's information:
``id`` is the institutional profile, ``species_id`` the shared technical
reference kept by Strains, Boxes and transfers. The Administration Species
workspace (pending approval) is their only frontend consumer.
"""

import logging

from django.db.models import Count, Prefetch, Q
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import get_required_active_organization_from_request
from apps.cultures.box_identity import institution_species_codes
from apps.cultures.models import Box

from . import worms
from .countries import ISO_3166_ALPHA2_CODES
from .admin_api import _strain_payload
from .institution_species import institution_species_for_admin, resolve_institution_species_codes, strain_creation_issue
from .institution_strains import collection_locations, create_institution_strain, location_payload
from .institution_species_services import (
    InstitutionSpeciesConflict,
    InstitutionSpeciesInvalid,
    InstitutionSpeciesNotFound,
    WormsReferenceRejected,
    WormsReferenceUnavailable,
    edit_existing_species,
    historical_species_ids,
    register_institution_species,
    update_institution_species,
)
from .models import InstitutionSpecies, Species, StrainTranslation
from .scoping import eligible_strains
from .species_code_change import plan_species_code_change
from .species_codes import SpeciesCodeError

logger = logging.getLogger(__name__)


def _authorized(request):
    """Return (organization, scoped profiles) after explicit Admin authorization."""
    profiles = institution_species_for_admin(request)
    return get_required_active_organization_from_request(request), profiles


def _strain_counts(organization, species_ids):
    return dict(
        eligible_strains(organization).filter(species_id__in=species_ids)
        .values("species_id").annotate(total=Count("pk", distinct=True))
        .values_list("species_id", "total")
    )


def profile_payload(profile, *, species_code, strain_count, species_code_issue=None):
    return {
        "id": profile.pk,
        "species_id": profile.species_id,
        "scientific_name": profile.scientific_name,
        "worms_aphia_id": profile.worms_aphia_id,
        "worms_url": worms.record_url(profile.worms_aphia_id) if profile.worms_aphia_id else None,
        "species_code": species_code,
        "revision": profile.revision,
        "strain_count": strain_count,
        **({"species_code_issue": species_code_issue} if species_code_issue else {}),
    }


def _profile_response(organization, profile):
    codes, issues = resolve_institution_species_codes(organization, [profile.species_id])
    counts = _strain_counts(organization, [profile.species_id])
    return profile_payload(
        profile, species_code=codes.get(profile.species_id), strain_count=counts.get(profile.species_id, 0),
        species_code_issue=issues.get(profile.species_id),
    )


def _strains(organization, species_id, *, include_metadata=True):
    queryset = (
        eligible_strains(organization).filter(species_id=species_id)
        .select_related(
            "origin",
            "local_identity__species_code_assignment",
            "local_identity__provenance_code_assignment__biological_provenance",
        )
        .prefetch_related(Prefetch("translations", queryset=StrainTranslation.objects.order_by("language_code")))
        .annotate(active_box_count=Count(
            "boxes", filter=Q(boxes__organization=organization, boxes__status=Box.Status.ACTIVE),
        ))
        .order_by("code", "pk")
    )
    codes = institution_species_codes(organization, [species_id])
    return [
        # Unowned legacy Strains used here are listed, never claimed as owned.
        {
            **_strain_payload(strain, organization, codes, include_provenance=True),
            "is_owned": strain.organization_id == organization.pk,
            **({
                "translations": {
                    item.language_code: {"name": item.name, "description": item.description}
                    for item in strain.translations.all()
                },
                "origin": _recorded_origin(strain, organization),
            } if include_metadata else {}),
        }
        for strain in queryset
    ]


def _recorded_origin(strain, organization):
    """Owned recorded event metadata only; never infer provenance or ownership."""
    if strain.organization_id != organization.pk or strain.origin_id is None:
        return None
    origin = strain.origin
    return {
        "source_type": origin.source_type,
        "event_date": origin.event_date,
        "description": origin.description,
        "origin_institution_name": origin.origin_institution_name,
        "latitude": str(origin.latitude) if origin.latitude is not None else None,
        "longitude": str(origin.longitude) if origin.longitude is not None else None,
        "technique": origin.technique,
    }


def _conflict(error):
    return Response({"code": error.code, **error.details}, status=status.HTTP_409_CONFLICT)


def _service_errors(call):
    try:
        return call()
    except InstitutionSpeciesInvalid as error:
        return Response(error.errors, status=status.HTTP_400_BAD_REQUEST)
    except InstitutionSpeciesNotFound:
        return Response({"code": "not_found"}, status=status.HTTP_404_NOT_FOUND)
    except InstitutionSpeciesConflict as error:
        return _conflict(error)
    except SpeciesCodeError as error:
        return Response({"code": error.code}, status=status.HTTP_409_CONFLICT)
    except WormsReferenceRejected as rejection:
        return Response({"code": rejection.code}, status=status.HTTP_400_BAD_REQUEST)
    except WormsReferenceUnavailable as error:
        logger.warning("WoRMS reference check failed (%s)", getattr(error.__cause__, "code", "unknown"))
        return Response({"code": error.code}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


class InstitutionSpeciesListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization, profiles = _authorized(request)
        profiles = list(profiles.order_by("scientific_name", "pk"))
        profiled = {profile.species_id for profile in profiles}
        historical = sorted(historical_species_ids(organization) - profiled)
        species_ids = list(profiled) + historical
        codes, issues = resolve_institution_species_codes(organization, species_ids)
        counts = _strain_counts(organization, species_ids)
        return Response({
            "institution_species": [
                profile_payload(
                    profile, species_code=codes.get(profile.species_id),
                    strain_count=counts.get(profile.species_id, 0),
                    species_code_issue=issues.get(profile.species_id),
                )
                for profile in profiles
            ],
            # Used here before institutional information existed. Shown with the
            # shared name and never adopted or given a code by this read.
            "historical_species": [
                {
                    "species_id": species.pk,
                    "shared_scientific_name": species.scientific_name,
                    "species_code": codes.get(species.pk),
                    **({"species_code_issue": issues[species.pk]} if species.pk in issues else {}),
                    "strain_count": counts.get(species.pk, 0),
                    "requires_historical_adoption": True,
                }
                for species in Species.objects.filter(pk__in=historical).order_by("scientific_name", "pk")
            ],
        })

    def post(self, request):
        organization, _profiles = _authorized(request)

        def register():
            profile, _code = register_institution_species(
                user=request.user, organization=organization, data=request.data,
            )
            return Response(_profile_response(organization, profile), status=status.HTTP_201_CREATED)

        return _service_errors(register)


def _strain_creation_payload(organization, species_id):
    """Read-only readiness of the explicit Strain creation.

    A missing AAA registration that the creation would itself register from
    proven institutional evidence is not an obstacle; missing, contradictory or
    colliding evidence still is. Nothing is registered by this read.
    """
    issue = strain_creation_issue(organization, species_id, include_recoverable=True)
    return {"can_create_strain": issue is None, "strain_creation_issue": issue}


def _detail_response(organization, profile):
    return {
        **_profile_response(organization, profile),
        **_strain_creation_payload(organization, profile.species_id),
        "strains": _strains(organization, profile.species_id),
    }


class InstitutionSpeciesResolveAPIView(APIView):
    """Retired: preparing a historical Species no longer persists anything.

    The former POST silently created an institutional profile and could assign
    a first Species code when an edit or Strain dialog was opened. The route
    stays mounted so old clients get an explicit, non-mutating answer instead
    of a generic 404; authorization is still checked first.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        _authorized(request)
        return Response(
            {"code": "institution_species_resolution_retired"},
            status=status.HTTP_410_GONE,
        )


class InstitutionSpeciesDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        organization, profiles = _authorized(request)
        profile = profiles.filter(pk=pk).first()
        if profile is None:
            return Response({"code": "not_found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(_detail_response(organization, profile))

    def patch(self, request, pk):
        organization, _profiles = _authorized(request)

        def update():
            profile, _changed = update_institution_species(
                user=request.user, organization=organization, pk=pk, data=request.data,
            )
            return Response(_profile_response(organization, profile))

        return _service_errors(update)


class InstitutionSpeciesStrainsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        organization, profiles = _authorized(request)
        profile = profiles.filter(pk=pk).first()
        if profile is None:
            return Response({"code": "not_found"}, status=status.HTTP_404_NOT_FOUND)
        return Response({"institution_species_id": profile.pk, "strains": _strains(organization, profile.species_id)})

    def post(self, request, pk):
        """Create one Strain: the server allocates X and builds AAA-BBB-X.

        The request carries a client idempotency key (``request_id``). Replaying
        the same key with the same intent returns the recorded Strain (200); a
        different intent is a 409 conflict. A new intent is 201.
        """
        organization, _profiles = _authorized(request)
        return _service_errors(lambda: _strain_creation_response(
            organization, *create_institution_strain(
                user=request.user, organization=organization, profile_pk=pk, data=request.data,
            ),
        ))


def _strain_creation_response(organization, strain, created):
    """Creation (201) or idempotent replay (200), with the profile now established."""
    strains = _strains(organization, strain.species_id, include_metadata=False)
    profile_id = InstitutionSpecies.objects.for_organization(organization).filter(
        species_id=strain.species_id,
    ).values_list("pk", flat=True).first()
    return Response({
        "institution_species_id": profile_id,
        "strain": next(item for item in strains if item["id"] == strain.pk),
        "strains": strains,
    }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class InstitutionSpeciesCodeChangePreviewAPIView(APIView):
    """Read-only impact preview of changing this institution's Species code.

    ``GET ?proposed_code=AAA``. Nothing is written or reserved; the result is
    advisory and never authorizes a later mutation.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        organization, _profiles = _authorized(request)
        return _service_errors(lambda: Response(plan_species_code_change(
            user=request.user, organization=organization, profile_pk=pk,
            proposed_code=request.query_params.get("proposed_code"),
        )))


class CollectionLocationListAPIView(APIView):
    """The active institution's coded collection locations (BBB), for strain creation.

    ``country`` is the location's recorded ISO country (or null); ``countries``
    lists the accepted country identifiers, localized by the client.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization, _profiles = _authorized(request)
        return Response({
            "collection_locations": [
                location_payload(assignment) for assignment in collection_locations(organization)
            ],
            "countries": list(ISO_3166_ALPHA2_CODES),
        })


class HistoricalSpeciesDetailAPIView(APIView):
    """Detail of a Species used here, addressed by its shared reference ID.

    An own profile or own persisted usage is required. The shared name remains
    available, while local information takes precedence in the editable fields.
    GET writes nothing. PATCH is the explicit identification save of a Species
    without an institutional profile (see ``edit_existing_species``).
    """

    permission_classes = [IsAuthenticated]

    def patch(self, request, species_id):
        organization, _profiles = _authorized(request)

        def edit():
            profile, changed = edit_existing_species(
                user=request.user, organization=organization, species_id=species_id, data=request.data,
            )
            if not changed:
                # Unchanged prefill: nothing was associated, audited or written.
                return self.get(request, species_id)
            return Response(_profile_response(organization, profile), status=status.HTTP_201_CREATED)

        return _service_errors(edit)

    def get(self, request, species_id):
        organization, profiles = _authorized(request)
        profile = profiles.filter(species_id=species_id).first()
        if profile is None and species_id not in historical_species_ids(organization):
            return Response({"code": "not_found"}, status=status.HTTP_404_NOT_FOUND)
        species = Species.objects.get(pk=species_id)
        codes, issues = resolve_institution_species_codes(organization, [species_id])
        strains = _strains(organization, species_id)
        aphia_id = profile.worms_aphia_id if profile else species.worms_aphia_id
        return Response({
            "species_id": species.pk,
            "shared_scientific_name": species.scientific_name,
            "institution_species_id": profile.pk if profile else None,
            "scientific_name": profile.scientific_name if profile else species.scientific_name,
            "worms_aphia_id": aphia_id,
            "worms_url": worms.record_url(aphia_id) if aphia_id else None,
            **_strain_creation_payload(organization, species_id),
            "species_code": codes.get(species_id),
            **({"species_code_issue": issues[species_id]} if species_id in issues else {}),
            "strain_count": len(strains),
            "requires_historical_adoption": profile is None,
            "strains": strains,
        })


class HistoricalSpeciesStrainsAPIView(APIView):
    """Explicit Strain creation for a Species used here, addressed by shared ID.

    Same payload, idempotency and response as the profile route. The missing
    institutional profile and AAA registration are established by this
    submission only, inside the creation transaction.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, species_id):
        organization, _profiles = _authorized(request)
        return _service_errors(lambda: _strain_creation_response(
            organization, *create_institution_strain(
                user=request.user, organization=organization, species_id=species_id, data=request.data,
            ),
        ))
