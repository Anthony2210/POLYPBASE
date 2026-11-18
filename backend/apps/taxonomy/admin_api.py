"""Administration > Species and strains: legacy catalog read and WoRMS lookup.

The catalog still lists shared references; institution codes, strains and Box
counts are read only for the explicitly selected institution, which the user
must administer. WoRMS results are suggestions only. Species creation and code
generation through this module are closed: institutional registration and
editing live in ``institution_species_services`` / ``institution_species_api``.
"""

import logging

from django.db.models import Count, Prefetch, Q
from django.db.models.functions import Lower
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import (
    get_required_active_organization_from_request,
    user_can_administer_organization,
)
from apps.cultures.box_identity import (
    USABLE,
    institution_species_codes,
    strain_box_identity,
    strain_display_name,
)
from apps.cultures.models import Box

from . import worms
from .institution_species_services import historical_species_ids
from .models import InstitutionSpecies, Species, SpeciesTranslation, StrainTranslation
from .scoping import eligible_strains
from .serializers import available_content_languages

logger = logging.getLogger(__name__)

def _require_admin(request):
    organization = get_required_active_organization_from_request(request)
    if organization is None or not user_can_administer_organization(request.user, organization):
        raise PermissionDenied("Administrator access is required.")
    return organization


def _strain_payload(strain, organization, species_codes, *, include_provenance=False):
    identity = strain_box_identity(strain, organization, species_codes=species_codes)
    is_usable = identity["status"] == USABLE
    payload = {
        "id": strain.pk,
        "code": strain.code,
        "name": strain_display_name(strain),
        "strain_number": identity["strain_number"] if is_usable else None,
        "active_box_count": strain.active_box_count,
        "is_box_usable": is_usable,
    }
    if include_provenance:
        # Only the active institution's verified recorded provenance; never a
        # provider, place or acquisition event, and never inferred when absent.
        payload["recorded_provenance_name"] = identity["recorded_provenance_name"] if is_usable else None
        payload["recorded_country_code"] = identity["recorded_country_code"] if is_usable else None
    return payload


def _species_payload(species, species_codes, strains):
    return {
        "id": species.pk,
        "scientific_name": species.scientific_name,
        "worms_aphia_id": species.worms_aphia_id,
        "is_described": species.is_described,
        "translations": {
            item.language_code: {"name": item.name, "description": item.description}
            for item in species.translations.all()
        },
        "species_code": species_codes.get(species.pk),
        "strains": strains,
    }


def build_catalog(organization, *, species_ids=None):
    species_queryset = Species.objects.prefetch_related(
        Prefetch("translations", queryset=SpeciesTranslation.objects.order_by("language_code"))
    ).order_by("scientific_name", "pk")
    strain_queryset = (
        eligible_strains(organization)
        .select_related(
            "local_identity__species_code_assignment",
            "local_identity__provenance_code_assignment__biological_provenance",
        )
        .prefetch_related(Prefetch("translations", queryset=StrainTranslation.objects.order_by("language_code")))
        .annotate(active_box_count=Count(
            "boxes",
            filter=Q(boxes__organization=organization, boxes__status=Box.Status.ACTIVE),
        ))
        .order_by("code", "pk")
    )
    if species_ids is not None:
        species_queryset = species_queryset.filter(pk__in=species_ids)
        strain_queryset = strain_queryset.filter(species_id__in=species_ids)
    species_list = list(species_queryset)
    species_codes = institution_species_codes(organization, [species.pk for species in species_list])
    strains_by_species = {}
    for strain in strain_queryset:
        strains_by_species.setdefault(strain.species_id, []).append(
            _strain_payload(strain, organization, species_codes)
        )
    return [
        _species_payload(species, species_codes, strains_by_species.get(species.pk, []))
        for species in species_list
    ]


class SpeciesCatalogAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _require_admin(request)
        return Response({
            "languages": available_content_languages(),
            "species": build_catalog(organization),
        })


class _ClosedMutationAPIView(APIView):
    """Former shared-reference write path, closed by the institutional workflow.

    Creating a shared Species with a code but no institutional profile, or
    giving a historical Species its first code outside a reviewed adoption,
    would bypass ``institution_species_services``. Authorization is still
    checked first so the refusal discloses nothing to other users.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk=None):
        _require_admin(request)
        return Response(
            {"code": "superseded_by_institution_species"}, status=status.HTTP_410_GONE,
        )


class AdminSpeciesCreateAPIView(_ClosedMutationAPIView):
    """Closed: use the institutional registration endpoint instead."""


class AdminSpeciesCodeAPIView(_ClosedMutationAPIView):
    """Closed: historical first assignments require a reviewed adoption process."""


def _local_matches(organization, records):
    """Index the active institution's own Species by AphiaID and lower-cased name.

    Only this institution's profiles (local name and AphiaID) and the historical
    references it already uses without a profile are considered. Other
    institutions' information, and shared references unused here, never match.
    """
    aphia_ids = set()
    names = set()
    for record in records:
        aphia_ids.add(record["aphia_id"])
        names.add(record["scientific_name"].lower())
        if record["valid_aphia_id"]:
            aphia_ids.add(record["valid_aphia_id"])
            names.add(record["valid_name"].lower())
    names.discard("")
    by_aphia = {}
    by_name = {}
    if not aphia_ids and not names:
        return by_aphia, by_name
    profiles = (
        InstitutionSpecies.objects.for_organization(organization)
        .annotate(name_key=Lower("scientific_name"))
        .filter(Q(worms_aphia_id__in=aphia_ids) | Q(name_key__in=names))
        .order_by("pk")
    )
    for profile in profiles:
        reference = {
            "institution_species_id": profile.pk,
            "species_id": profile.species_id,
            "scientific_name": profile.scientific_name,
            "requires_historical_adoption": False,
        }
        if profile.worms_aphia_id is not None:
            by_aphia.setdefault(profile.worms_aphia_id, reference)
        by_name.setdefault(profile.name_key, reference)
    profiled = InstitutionSpecies.objects.for_organization(organization).values("species_id")
    historical = (
        Species.objects.filter(pk__in=historical_species_ids(organization))
        .exclude(pk__in=profiled)
        .annotate(name_key=Lower("scientific_name"))
        .filter(Q(worms_aphia_id__in=aphia_ids) | Q(name_key__in=names))
        .order_by("pk")
    )
    for species in historical:
        reference = {
            "institution_species_id": None,
            "species_id": species.pk,
            "scientific_name": species.scientific_name,
            "requires_historical_adoption": True,
        }
        if species.worms_aphia_id is not None:
            by_aphia.setdefault(species.worms_aphia_id, reference)
        by_name.setdefault(species.name_key, reference)
    return by_aphia, by_name


def _match(by_aphia, by_name, aphia_id, name):
    if aphia_id and aphia_id in by_aphia:
        return by_aphia[aphia_id]
    return by_name.get(name.lower()) if name else None


class WormsSearchAPIView(APIView):
    """Server-side WoRMS name lookup for the Species creation assistant."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = _require_admin(request)
        query = worms.normalize_query(request.query_params.get("q"))
        if query is None:
            return Response({"code": "invalid_query"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            records, truncated = worms.search_by_name(query)
        except worms.WormsError as error:
            logger.warning("WoRMS search failed (%s)", error.code)
            return Response({"code": error.code}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        # Exact name matches first; WoRMS order is otherwise preserved.
        query_key = query.lower()
        records = sorted(records, key=lambda record: record["scientific_name"].lower() != query_key)
        by_aphia, by_name = _local_matches(organization, records)
        results = []
        for record in records:
            is_synonym = bool(record["valid_aphia_id"]) and record["valid_aphia_id"] != record["aphia_id"]
            results.append({
                **record,
                "url": worms.record_url(record["aphia_id"]),
                "local_species": _match(by_aphia, by_name, record["aphia_id"], record["scientific_name"]),
                "valid_local_species": (
                    _match(by_aphia, by_name, record["valid_aphia_id"], record["valid_name"])
                    if is_synonym else None
                ),
            })
        # "truncated": WoRMS returned more than the displayed limit; refine the query.
        return Response({"query": query, "results": results, "truncated": truncated, "limit": worms.MAX_RESULTS})
