"""Strain creation from an institutional Species (Administration).

Approved identifier rule: a Strain code is ``AAA-BBB-X`` and prefixes Box Local
IDs (``AAA-BBB-X.YYY``).

- AAA is the active institution's code for the Species (``OrganizationSpeciesCode``).
- BBB is the code of the geographical location where the original polyps were
  collected. It is stored as the institution's ``OrganizationProvenanceCode`` of a
  ``BiologicalProvenance`` (the coded collection location). A location keeps the
  same BBB across Species; BBB never identifies an institution, a supplier or a
  current Box location.
- Within one institution a country has exactly one BBB and a BBB exactly one
  country (``OrganizationProvenanceCode.country_code`` plus database
  constraints). A new Strain names its country (ISO 3166-1 alpha-2): a country
  already coded reuses its BBB; a new country receives the BBB entered by the
  administrator, or is explicitly linked to a coded location without a country.
- X is allocated by the server, never entered by an administrator.

The server builds and validates the final code. Nothing here rewrites an existing
Strain or Box code, and no country is inferred from other data.
"""

import re
import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.audit.models import AuditLog
from apps.organizations.models import Organization

from .countries import normalize_country_code
from .institution_species import strain_creation_issue
from .institution_species_services import (
    InstitutionSpeciesConflict,
    InstitutionSpeciesInvalid,
    InstitutionSpeciesNotFound,
    require_institution_admin,
    species_action_transaction,
)
from .models import (
    BiologicalProvenance, InstitutionSpecies, InstitutionStrainCreationRequest,
    OrganizationProvenanceCode, OrganizationSpeciesCode, Origin, Species, Strain, StrainTranslation,
)
from .services import allocate_next_local_strain_number, create_local_strain_identity

# Same strict three-letter grammar as the Box identifier check (box_identity.py).
SEGMENT = re.compile(r"^[A-Z]{3}$")
CREATION_FIELDS = frozenset({
    "request_id", "country", "country_name", "bbb_code", "link_collection_location",
    "acquisition_type", "donor_institution", "latitude", "longitude", "name", "name_language",
})
# Product-approved acquisition types of a new Strain (existing Origin choices).
ACQUISITION_TYPES = (
    Origin.SourceType.DONATION,
    Origin.SourceType.FIELD_COLLECTION,
    Origin.SourceType.ARTIFICIAL_FERTILIZATION_WILD,
    Origin.SourceType.ARTIFICIAL_FERTILIZATION_CAPTIVE,
)
LOCATION_NAME_MAX_LENGTH = 150
TEXT_MAX_LENGTH = 150
# Origin.latitude/longitude: DecimalField(max_digits=9, decimal_places=6).
COORDINATE_DECIMAL_PLACES = 6
# Bounded search above historical identifiers that already use a candidate X.
MAX_ALLOCATION_ATTEMPTS = 1000


def collection_locations(organization):
    """The active institution's coded collection locations, never another's."""
    return (
        OrganizationProvenanceCode.objects.filter(organization=organization)
        .select_related("biological_provenance").order_by("code", "pk")
    )


def location_payload(assignment):
    return {
        "id": assignment.pk, "code": assignment.code, "name": assignment.biological_provenance.name,
        "country": assignment.country_code,
    }


# Input validation -----------------------------------------------------------

def _clean_text(value, errors, field, *, max_length=TEXT_MAX_LENGTH):
    """Optional single-line text: None or blank is absent, never a stored empty value."""
    if value is None:
        return None
    if not isinstance(value, str):
        errors[field] = ["invalid"]
        return None
    value = " ".join(value.split())
    if not value:
        return None
    if len(value) > max_length:
        errors[field] = ["too_long"]
    return value


def _clean_coordinate(value, errors, field, limit):
    """Decimal degrees; 0 is a real coordinate, only None/"" is absent."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        errors[field] = ["invalid"]
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        errors[field] = ["invalid"]
        return None
    if not number.is_finite():
        errors[field] = ["invalid"]
        return None
    if number < -limit or number > limit:
        errors[field] = ["out_of_range"]
        return None
    if -number.as_tuple().exponent > COORDINATE_DECIMAL_PLACES:
        errors[field] = ["too_precise"]
        return None
    return number.quantize(Decimal(1).scaleb(-COORDINATE_DECIMAL_PLACES))


def _clean_creation(data):
    """Return the cleaned creation request or refuse the payload.

    ``request_id`` is the client idempotency key: a UUID that identifies one
    deliberate creation intent. It is required so a retry can never allocate a
    second X by accident.
    """
    if not isinstance(data, dict):
        raise InstitutionSpeciesInvalid({"non_field_errors": ["invalid"]})
    errors = {field: ["not_allowed"] for field in set(data) - CREATION_FIELDS}
    request_id = data.get("request_id")
    if isinstance(request_id, uuid.UUID):
        pass
    elif isinstance(request_id, str):
        try:
            request_id = uuid.UUID(request_id)
        except (ValueError, AttributeError, TypeError):
            errors["request_id"] = ["invalid"]
    else:
        errors["request_id"] = ["invalid"]

    raw_country = data.get("country")
    country = normalize_country_code(raw_country)
    if country is None:
        errors["country"] = ["required" if raw_country in (None, "") else "invalid"]
    country_name = _clean_text(
        data.get("country_name"), errors, "country_name", max_length=LOCATION_NAME_MAX_LENGTH,
    )

    bbb_code = data.get("bbb_code")
    if bbb_code in (None, ""):
        bbb_code = None
    elif not isinstance(bbb_code, str) or not SEGMENT.fullmatch(bbb_code.strip().upper()):
        errors["bbb_code"] = ["invalid_format"]
        bbb_code = None
    else:
        bbb_code = bbb_code.strip().upper()
    link = data.get("link_collection_location")
    if link is not None and (type(link) is not int or link <= 0):
        errors["link_collection_location"] = ["invalid"]
    elif link is not None and bbb_code is not None:
        errors["link_collection_location"] = ["exclusive_with_bbb_code"]

    acquisition = data.get("acquisition_type")
    if acquisition in (None, ""):
        acquisition = None
    elif acquisition not in ACQUISITION_TYPES:
        errors["acquisition_type"] = ["invalid"]
        acquisition = None
    donor = _clean_text(data.get("donor_institution"), errors, "donor_institution")
    if donor is not None and acquisition != Origin.SourceType.DONATION and "acquisition_type" not in errors:
        errors["donor_institution"] = ["donation_only"]

    latitude = _clean_coordinate(data.get("latitude"), errors, "latitude", 90)
    longitude = _clean_coordinate(data.get("longitude"), errors, "longitude", 180)
    if "latitude" not in errors and "longitude" not in errors and (latitude is None) != (longitude is None):
        errors["latitude" if latitude is None else "longitude"] = ["required_pair"]

    name = _clean_text(data.get("name"), errors, "name")
    name_language = data.get("name_language")
    if name is None:
        name_language = None
    elif name_language not in {code for code, _label in settings.LANGUAGES}:
        errors["name_language"] = ["invalid"]
    if errors:
        raise InstitutionSpeciesInvalid(errors)
    return {
        "request_id": request_id, "country": country, "country_name": country_name, "bbb_code": bbb_code,
        "link_collection_location": link, "acquisition_type": acquisition, "donor_institution": donor,
        "latitude": latitude, "longitude": longitude, "name": name, "name_language": name_language,
    }


def _normalized_intent(*, profile_pk, cleaned):
    """The material request identity compared across replays of one key.

    The profile is part of the intent, so reusing a key for another Species is a
    conflict rather than a silent second creation.
    """
    return {
        "profile": profile_pk,
        "country": cleaned["country"],
        "country_name": cleaned["country_name"],
        "bbb_code": cleaned["bbb_code"],
        "link_collection_location": cleaned["link_collection_location"],
        "acquisition_type": None if cleaned["acquisition_type"] is None else str(cleaned["acquisition_type"]),
        "donor_institution": cleaned["donor_institution"],
        "latitude": None if cleaned["latitude"] is None else str(cleaned["latitude"]),
        "longitude": None if cleaned["longitude"] is None else str(cleaned["longitude"]),
        "name": cleaned["name"],
        "name_language": cleaned["name_language"],
    }


# Location ---------------------------------------------------------------------

def _create_location(*, user, organization, name, code, country):
    """Register a new coded country location for this institution only.

    The BBB is unique per institution (existing constraint); a location already
    coded here under the same code or name is reported, never duplicated.
    """
    assignments = collection_locations(organization)
    taken = assignments.filter(code=code).first()
    if taken is not None:
        raise InstitutionSpeciesConflict("collection_location_code_taken", collection_location=location_payload(taken))
    key = name.casefold()
    for assignment in assignments:
        if " ".join(assignment.biological_provenance.name.split()).casefold() == key:
            raise InstitutionSpeciesConflict(
                "collection_location_name_taken", collection_location=location_payload(assignment),
            )
    provenance = BiologicalProvenance.objects.create(name=name)
    try:
        with transaction.atomic():
            assignment = OrganizationProvenanceCode.objects.create(
                organization=organization, biological_provenance=provenance, code=code, country_code=country,
            )
    except IntegrityError as error:
        raise InstitutionSpeciesConflict("collection_location_code_taken") from error
    # Same audit records as the existing provenance and BBB APIs.
    AuditLog.objects.create(
        organization=organization, user=user, action=AuditLog.Action.CREATION,
        object_type="biological_provenance", object_id=str(provenance.pk),
        description=f"Biological provenance created: {provenance.name}",
    )
    AuditLog.objects.create(
        organization=organization, user=user, action=AuditLog.Action.CREATION,
        object_type="organization_provenance_code", object_id=str(assignment.pk),
        description=f"Provenance code created for provenance {provenance.pk}",
        metadata={"code": code, "country_code": country},
    )
    return assignment


def _link_location(*, user, organization, assignment, country):
    """Record the country of a coded location that has none, on explicit request.

    Only NULL becomes a country: a recorded country is never replaced, and no
    code, Strain or Box identifier changes.
    """
    if assignment.country_code is not None:
        raise InstitutionSpeciesConflict(
            "collection_location_country_conflict", collection_location=location_payload(assignment),
        )
    try:
        with transaction.atomic():
            updated = OrganizationProvenanceCode.objects.filter(
                pk=assignment.pk, organization=organization, country_code__isnull=True,
            ).update(country_code=country)
    except IntegrityError as error:
        raise InstitutionSpeciesConflict("country_already_coded") from error
    if updated != 1:
        raise InstitutionSpeciesConflict("collection_location_country_conflict")
    assignment.country_code = country
    AuditLog.objects.create(
        organization=organization, user=user, action=AuditLog.Action.UPDATE,
        object_type="organization_provenance_code", object_id=str(assignment.pk),
        description=f"Country recorded for provenance code {assignment.code}: {country}",
        metadata={"code": assignment.code, "before": {"country_code": None}, "after": {"country_code": country}},
    )
    return assignment


def _country_location(*, user, organization, cleaned):
    """Return the institution's single coded location for the requested country."""
    country = cleaned["country"]
    mapped = collection_locations(organization).filter(country_code=country).first()
    if mapped is not None:
        # A country already coded here always reuses its BBB.
        link = cleaned["link_collection_location"]
        if (cleaned["bbb_code"] is not None and cleaned["bbb_code"] != mapped.code) or (
            link is not None and link != mapped.pk
        ):
            raise InstitutionSpeciesConflict("country_already_coded", collection_location=location_payload(mapped))
        return mapped
    if cleaned["link_collection_location"] is not None:
        assignment = collection_locations(organization).filter(pk=cleaned["link_collection_location"]).first()
        if assignment is None:
            raise InstitutionSpeciesInvalid({"link_collection_location": ["not_found"]})
        return _link_location(user=user, organization=organization, assignment=assignment, country=country)
    if cleaned["bbb_code"] is None:
        raise InstitutionSpeciesInvalid({"bbb_code": ["required"]})
    return _create_location(
        user=user, organization=organization, name=cleaned["country_name"] or country,
        code=cleaned["bbb_code"], country=country,
    )


# X allocation -------------------------------------------------------------------

def _used_numbers(prefix):
    """X values already carried by any Strain code or Box Local ID with this prefix.

    Codes are compared numerically (``AAA-BBB-01`` uses X=1) across all Species and
    institutions: a Strain code is also the global Box Local ID namespace.
    """
    from apps.cultures.models import Box

    strain_pattern = re.compile(rf"^{re.escape(prefix)}-0*(\d+)$")
    box_pattern = re.compile(rf"^{re.escape(prefix)}-0*(\d+)\.")
    used = set()
    for code in Strain.objects.filter(code__startswith=f"{prefix}-").values_list("code", flat=True):
        match = strain_pattern.match(code)
        if match:
            used.add(int(match.group(1)))
    for code in Box.objects.filter(global_code__startswith=f"{prefix}-").values_list("global_code", flat=True):
        match = box_pattern.match(code)
        if match:
            used.add(int(match.group(1)))
    return used


def _code_in_use(code):
    from apps.cultures.models import Box

    return (Strain.objects.filter(code=code).exists()
            or Box.objects.filter(global_code__startswith=f"{code}.").exists())


def _allocate_code(*, organization, species, provenance, prefix):
    """Return (code, X): the counter's next free X, serialized on its Box namespace."""
    from apps.cultures.box_codes import locked_namespace

    used = _used_numbers(prefix)
    for _attempt in range(MAX_ALLOCATION_ATTEMPTS):
        number = allocate_next_local_strain_number(
            organization=organization, species=species, biological_provenance=provenance,
        )
        if number in used:
            continue  # Gaps are allowed; an existing identifier is never reused.
        code = f"{prefix}-{number}"
        # Serialize writers of this code across Species and institutions, then
        # recheck with a fresh READ COMMITTED snapshot.
        locked_namespace(code, reconcile=False)
        if _code_in_use(code):
            continue
        return code, number
    raise InstitutionSpeciesConflict("strain_number_unavailable")


# Creation -----------------------------------------------------------------------

def create_institution_strain(*, user, organization, profile_pk, data):
    """Create one owned Strain of an institutional Species; return (strain, created).

    Country location (when new or linked), Strain, acquisition record, optional
    name, local identity, audit and the idempotency receipt share one
    transaction. Replaying the same request key with the same normalized intent
    returns the recorded Strain without allocating another X or writing another
    location, identity, counter or audit. A different intent is a conflict.
    """
    require_institution_admin(user, organization)
    cleaned = _clean_creation(data)
    request_id = cleaned["request_id"]
    intent = _normalized_intent(profile_pk=profile_pk, cleaned=cleaned)
    with species_action_transaction():
        # Share registry -> allocation gate -> organization -> Species order
        # with resolution, before checking current institutional evidence.
        locked = Organization.objects.select_for_update().get(pk=organization.pk)
        require_institution_admin(user, locked)
        previous = InstitutionStrainCreationRequest.objects.filter(
            organization=locked, actor=user, request_id=request_id,
        ).first()
        if previous is not None:
            if previous.intent != intent:
                raise InstitutionSpeciesConflict("strain_creation_request_conflict")
            return previous.strain, False
        profile = InstitutionSpecies.objects.for_organization(locked).filter(pk=profile_pk).first()
        if profile is None:
            raise InstitutionSpeciesNotFound()
        species = Species.objects.select_for_update().get(pk=profile.species_id)
        require_institution_admin(user, locked)
        issue = strain_creation_issue(locked, species.pk)
        if issue is not None:
            raise InstitutionSpeciesConflict(issue)
        species_assignment = OrganizationSpeciesCode.objects.get(organization=locked, species=species)

        location = _country_location(user=user, organization=locked, cleaned=cleaned)
        if not SEGMENT.fullmatch(location.code):
            raise InstitutionSpeciesConflict("collection_location_code_unusable")
        provenance = location.biological_provenance

        prefix = f"{species_assignment.code}-{location.code}"
        code, number = _allocate_code(organization=locked, species=species, provenance=provenance, prefix=prefix)
        origin = None
        if any(cleaned[field] is not None for field in ("acquisition_type", "donor_institution", "latitude")):
            # This Strain's own acquisition record: its GPS is never a property
            # of the whole country.
            origin = Origin.objects.create(
                source_type=cleaned["acquisition_type"] or Origin.SourceType.UNKNOWN,
                origin_institution_name=cleaned["donor_institution"] or "",
                latitude=cleaned["latitude"], longitude=cleaned["longitude"],
            )
        try:
            with transaction.atomic():
                strain = Strain.objects.create(
                    species=species, organization=locked, code=code, number=number, origin_code=location.code,
                    origin=origin,
                )
        except IntegrityError as error:
            raise InstitutionSpeciesConflict("strain_code_conflict") from error
        if cleaned["name"] is not None:
            # A new Strain has no translation to preserve. Only an entered name is
            # stored; a display fallback ("Strain X") is never persisted.
            StrainTranslation.objects.create(
                strain=strain, language_code=cleaned["name_language"], name=cleaned["name"],
            )
        try:
            create_local_strain_identity(
                strain=strain, organization=locked, species_code_assignment=species_assignment,
                provenance_code_assignment=location, biological_provenance=provenance,
            )
        except ValidationError as error:
            raise InstitutionSpeciesConflict("strain_identity_invalid") from error
        AuditLog.objects.create(
            organization=locked, user=user, action=AuditLog.Action.CREATION,
            object_type="strain", object_id=str(strain.pk), description=f"Strain created: {strain.code}",
            metadata={
                "institution_species_id": profile.pk, "species_id": species.pk, "code": strain.code,
                "species_code": species_assignment.code, "collection_location_code": location.code,
                "collection_location_name": provenance.name, "strain_number": number,
                "country_code": location.country_code,
                "acquisition_type": intent["acquisition_type"],
                "donor_institution": cleaned["donor_institution"],
                "latitude": intent["latitude"], "longitude": intent["longitude"],
                "name": cleaned["name"], "name_language": cleaned["name_language"],
            },
        )
        InstitutionStrainCreationRequest.objects.create(
            organization=locked, actor=user, request_id=request_id, intent=intent, strain=strain,
        )
    return strain, True
