"""Authorized registration and editing of institutional Species information.

An ``InstitutionSpecies`` row is one institution's information (local scientific
name, optional WoRMS AphiaID, revision) over a shared technical ``Species``
reference. The shared reference stays the stable key of existing Strains, Boxes
and transfers: these services never modify its name or AphiaID, never modify an
assigned Species code and never touch Strain, Box or lineage identifiers.

Network calls to WoRMS happen before any transaction or row lock is opened.
Names, codes and AphiaIDs are never used to infer biological identity,
``GlobalStrainIdentity`` or institutional ownership.
"""

from contextlib import contextmanager

from django.db import IntegrityError, transaction
from django.db.models import Value
from rest_framework.exceptions import PermissionDenied

from apps.accounts.permissions import user_can_administer_organization
from apps.audit.models import AuditLog
from apps.cultures.box_codes import lock_local_id_registry
from apps.cultures.models import Box
from apps.organizations.models import Organization

from . import worms
from .institution_species import resolve_institution_species_codes
from .models import InstitutionSpecies, OrganizationSpeciesCode, Species, Strain
from .species_codes import allocate_species_code, lock_species_code_allocation
from .species_names import SCIENTIFIC_NAME_PATTERN, ScientificNameKey, normalize_scientific_name

MAX_NAME_LENGTH = 150
MAX_APHIA_ID = 2147483647
REGISTRATION_FIELDS = frozenset({"scientific_name", "worms_aphia_id"})
EDIT_FIELDS = frozenset({"expected_revision", "scientific_name", "worms_aphia_id"})


class InstitutionSpeciesInvalid(Exception):
    """Client input is malformed; ``errors`` maps field -> list of stable codes."""

    def __init__(self, errors):
        super().__init__(errors)
        self.errors = errors


class InstitutionSpeciesConflict(Exception):
    """The operation cannot be applied; ``code`` is a stable client-facing reason.

    ``details`` contains only the active institution's own information or the
    shared technical reference ID; never another institution's local data.
    """

    def __init__(self, code, **details):
        super().__init__(code)
        self.code = code
        self.details = details


class InstitutionSpeciesNotFound(Exception):
    pass


class WormsReferenceRejected(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class WormsReferenceUnavailable(Exception):
    code = "worms_reference_unavailable"


# Input validation -----------------------------------------------------------

def _clean_name(value, errors):
    if not isinstance(value, str):
        errors["scientific_name"] = ["invalid_name"]
        return None
    name = normalize_scientific_name(value)
    if not name or len(name) > MAX_NAME_LENGTH or not SCIENTIFIC_NAME_PATTERN.fullmatch(name):
        errors["scientific_name"] = ["invalid_name"]
        return None
    return name


def _clean_aphia_id(value, errors):
    # bool is an int subclass and is never an identifier; 0 is not a valid AphiaID.
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_APHIA_ID:
        errors["worms_aphia_id"] = ["invalid_aphia_id"]
        return None
    return value


def _check_fields(data, allowed, required):
    if not isinstance(data, dict):
        raise InstitutionSpeciesInvalid({"non_field_errors": ["invalid_payload"]})
    errors = {field: ["not_allowed"] for field in sorted(set(data) - allowed)}
    errors.update({field: ["required"] for field in sorted(required - set(data))})
    if errors:
        raise InstitutionSpeciesInvalid(errors)


def clean_registration(data):
    """Return (scientific_name, aphia_id). No code, translation or owner is accepted."""
    _check_fields(data, REGISTRATION_FIELDS, {"scientific_name"})
    errors = {}
    name = _clean_name(data["scientific_name"], errors)
    aphia_id = _clean_aphia_id(data.get("worms_aphia_id"), errors)
    if errors:
        raise InstitutionSpeciesInvalid(errors)
    return name, aphia_id


def clean_edit(data):
    """Return (expected_revision, scientific_name, aphia_id).

    The final state is explicit: ``worms_aphia_id`` must be sent, as null to
    remove the reference, so no AphiaID survives a name change implicitly.
    """
    _check_fields(data, EDIT_FIELDS, EDIT_FIELDS)
    errors = {}
    revision = data["expected_revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        errors["expected_revision"] = ["invalid_revision"]
    name = _clean_name(data["scientific_name"], errors)
    aphia_id = _clean_aphia_id(data["worms_aphia_id"], errors)
    if errors:
        raise InstitutionSpeciesInvalid(errors)
    return revision, name, aphia_id


# Authorization and scoped reads ---------------------------------------------

def require_institution_admin(user, organization):
    if user is None or not user.is_authenticated or not user.is_active:
        raise PermissionDenied()
    if (not isinstance(organization, Organization) or organization.pk is None
            or not user_can_administer_organization(user, organization)):
        raise PermissionDenied()


def _name_matches(queryset, name, field="scientific_name"):
    return queryset.annotate(_name_key=ScientificNameKey(field)).filter(
        _name_key=ScientificNameKey(Value(name)),
    )


def historical_species_ids(organization):
    """Shared references with evidence of use in this institution.

    Evidence: an institution code assignment, an owned Strain, or a Box of this
    institution. Unowned Strains are not thereby assigned to the institution.
    """
    ids = set(OrganizationSpeciesCode.objects.filter(organization=organization).values_list("species_id", flat=True))
    ids.update(Strain.objects.filter(organization=organization).values_list("species_id", flat=True))
    ids.update(Box.objects.filter(organization=organization).values_list("strain__species_id", flat=True))
    ids.discard(None)
    return ids


def _has_history(organization, species):
    return (
        OrganizationSpeciesCode.objects.filter(organization=organization, species=species).exists()
        or Strain.objects.filter(organization=organization, species=species).exists()
        or Box.objects.filter(organization=organization, strain__species=species).exists()
    )


def _own_code(organization, species_id):
    return OrganizationSpeciesCode.objects.filter(
        organization=organization, species_id=species_id,
    ).values_list("code", flat=True).first()


def _existing_profile_conflict(profile):
    return InstitutionSpeciesConflict(
        "institution_species_exists",
        institution_species_id=profile.pk,
        species_id=profile.species_id,
        scientific_name=profile.scientific_name,
        species_code=_own_code(profile.organization_id, profile.species_id),
    )


def _audit(*, organization, user, action, object_type, object_id, description, metadata):
    AuditLog.objects.create(
        organization=organization, user=user, action=action, object_type=object_type,
        object_id=str(object_id), description=description, metadata=metadata,
    )


# WoRMS ----------------------------------------------------------------------

def _verify_reference(aphia_id, scientific_name):
    """Confirm the final name/AphiaID pair with WoRMS. Must run outside DB locks."""
    try:
        worms.verify_species_reference(aphia_id, scientific_name)
    except worms.WormsReferenceRejected as rejection:
        raise WormsReferenceRejected(rejection.code) from None
    except worms.WormsError as error:
        raise WormsReferenceUnavailable() from error


# Registration ---------------------------------------------------------------

def _resolve_shared_reference(organization, name, aphia_id):
    """Return (shared Species, created) for a genuinely new local registration.

    The normalized scientific name is the shared reference's technical key. An
    existing reference is reused only when this institution has no profile and
    no historical use of it, and when its AphiaID does not contradict the
    submitted one. A newly created reference stores the name only: the AphiaID
    belongs to the institution's information, never to the shared record.
    """
    for _attempt in range(2):
        shared = _name_matches(Species.objects.all(), name).order_by("pk").first()
        if shared is not None:
            profile = InstitutionSpecies.objects.for_organization(organization).filter(species=shared).first()
            if profile is not None:
                raise _existing_profile_conflict(profile)
            if _has_history(organization, shared):
                raise InstitutionSpeciesConflict(
                    "historical_adoption_required",
                    species_id=shared.pk,
                    shared_scientific_name=shared.scientific_name,
                    species_code=_own_code(organization, shared.pk),
                )
            if aphia_id is not None and shared.worms_aphia_id not in (None, aphia_id):
                # Same name, different taxon record (e.g. a homonym): never merged.
                raise InstitutionSpeciesConflict("shared_reference_conflict", species_id=shared.pk)
            return shared, False
        try:
            with transaction.atomic():
                return Species.objects.create(scientific_name=name), True
        except IntegrityError:
            # A writer outside the allocation gate committed an equivalent name.
            continue
    raise InstitutionSpeciesConflict("shared_reference_conflict")


def register_institution_species(*, user, organization, data):
    """Register a new Species in the active institution and return (profile, code).

    Profile, shared reference (when created), generated code and audit entries
    share one transaction. A historical reference already used here without a
    profile is never adopted or given a first code implicitly.
    """
    require_institution_admin(user, organization)
    name, aphia_id = clean_registration(data)
    if aphia_id is not None:
        _verify_reference(aphia_id, name)

    with transaction.atomic():
        # Global allocation gate first, then the institution row (documented order).
        lock_species_code_allocation()
        locked = Organization.objects.select_for_update().get(pk=organization.pk)
        require_institution_admin(user, locked)
        profiles = InstitutionSpecies.objects.for_organization(locked)
        existing = _name_matches(profiles, name).first()
        if existing is not None:
            raise _existing_profile_conflict(existing)
        if aphia_id is not None:
            same_reference = profiles.filter(worms_aphia_id=aphia_id).first()
            if same_reference is not None:
                raise InstitutionSpeciesConflict(
                    "institution_aphia_id_exists",
                    institution_species_id=same_reference.pk,
                    scientific_name=same_reference.scientific_name,
                )

        shared, shared_created = _resolve_shared_reference(locked, name, aphia_id)
        try:
            with transaction.atomic():
                profile = InstitutionSpecies.objects.create(
                    organization=locked, species=shared, scientific_name=name, worms_aphia_id=aphia_id,
                )
        except IntegrityError:
            current = (_name_matches(profiles, name).first()
                       or profiles.filter(species=shared).first())
            if current is None:
                raise
            raise _existing_profile_conflict(current) from None

        assignment, code_created = allocate_species_code(organization=locked, species=shared)
        if not code_created:
            # An assignment appeared for a reference without history: never reuse silently.
            raise InstitutionSpeciesConflict(
                "historical_adoption_required", species_id=shared.pk,
                shared_scientific_name=shared.scientific_name, species_code=assignment.code,
            )

        if shared_created:
            _audit(
                organization=locked, user=user, action=AuditLog.Action.CREATION,
                object_type="species", object_id=shared.pk,
                description=f"Species reference created: {shared.scientific_name}",
                metadata={"source": "institution_species_registration", "scientific_name": shared.scientific_name},
            )
        _audit(
            organization=locked, user=user, action=AuditLog.Action.CREATION,
            object_type="institution_species", object_id=profile.pk,
            description=f"Institution species registered: {profile.scientific_name}",
            metadata={
                "species_id": shared.pk,
                "shared_reference_created": shared_created,
                "scientific_name": profile.scientific_name,
                "worms_aphia_id": profile.worms_aphia_id,
                "species_code": assignment.code,
                "code_assignment_id": assignment.pk,
                "revision": profile.revision,
            },
        )
        _audit(
            organization=locked, user=user, action=AuditLog.Action.CREATION,
            object_type="organization_species_code", object_id=assignment.pk,
            description=f"Species code generated for species {shared.pk}",
            metadata={"species_id": shared.pk, "code": assignment.code, "institution_species_id": profile.pk},
        )
    return profile, assignment.code


@contextmanager
def species_action_transaction():
    """Share normal writer gates before organization and Species row locks.

    The Local ID registry coordinates Box writers and code-change application;
    the allocation gate coordinates manual code assignments and registration.
    This does not protect against writers that do not participate in either gate.
    """
    with transaction.atomic():
        lock_local_id_registry()
        lock_species_code_allocation()
        yield


# Explicit-submit association ----------------------------------------------
#
# An existing Species used by the institution without an ``InstitutionSpecies``
# profile is associated only inside the explicitly submitted business operation
# (an identification save or a Strain creation), never when a dialog opens.
# Callers hold species_action_transaction, then the organization and Species
# row locks, and have rechecked authority under them.

# Writer-owned audit sources of association side effects (see audit services).
EDIT_ASSOCIATION_SOURCE = "institution_species_edit"
STRAIN_ASSOCIATION_SOURCE = "institution_strain_creation"


def lock_managed_species(*, user, organization, species_id):
    """Return (locked organization, locked Species, own profile or None).

    The Species must already be managed here: an own profile or persisted
    institutional history (code assignment, owned Strain or own Box). A client
    supplied ID alone never establishes it.
    """
    locked = Organization.objects.select_for_update().get(pk=organization.pk)
    species = Species.objects.select_for_update().filter(pk=species_id).first()
    require_institution_admin(user, locked)
    if species is None:
        raise InstitutionSpeciesNotFound()
    profile = InstitutionSpecies.objects.for_organization(locked).filter(species=species).first()
    if profile is None and not _has_history(locked, species):
        raise InstitutionSpeciesNotFound()
    return locked, species, profile


def _check_local_identity_free(organization, species, name, aphia_id):
    """Refuse a name or AphiaID already used by this institution for another Species."""
    profiles = InstitutionSpecies.objects.for_organization(organization).exclude(species=species)
    clash = _name_matches(profiles, name).first()
    if clash is not None:
        raise InstitutionSpeciesConflict(
            "institution_species_name_exists",
            institution_species_id=clash.pk, scientific_name=clash.scientific_name,
        )
    if aphia_id is not None:
        clash = profiles.filter(worms_aphia_id=aphia_id).first()
        if clash is not None:
            raise InstitutionSpeciesConflict(
                "institution_aphia_id_exists",
                institution_species_id=clash.pk, scientific_name=clash.scientific_name,
            )


def associate_existing_species(*, organization, species, scientific_name, worms_aphia_id):
    """Create the missing profile of a locked, managed Species. No audit here.

    Never modifies the shared reference. A concurrent association surfaces as
    ``stale_revision`` so no caller silently overwrites another user's save.
    """
    _check_local_identity_free(organization, species, scientific_name, worms_aphia_id)
    try:
        with transaction.atomic():
            return InstitutionSpecies.objects.create(
                organization=organization, species=species,
                scientific_name=scientific_name, worms_aphia_id=worms_aphia_id,
            )
    except IntegrityError:
        current = InstitutionSpecies.objects.for_organization(organization).filter(species=species).first()
        if current is not None:
            raise _stale(current.revision, institution_species_id=current.pk) from None
        raise InstitutionSpeciesConflict("institution_species_name_exists") from None


def audit_association(*, user, organization, profile, source, **extra):
    _audit(
        organization=organization, user=user, action=AuditLog.Action.CREATION,
        object_type="institution_species", object_id=profile.pk,
        description=f"Institution species information enabled: {profile.scientific_name}",
        metadata={
            "source": source, "species_id": profile.species_id,
            "scientific_name": profile.scientific_name, "worms_aphia_id": profile.worms_aphia_id,
            "revision": profile.revision, "shared_reference_created": False, **extra,
        },
    )


def register_recorded_species_code(*, organization, species):
    """Return (assignment, created) for a locked, managed Species. No audit here.

    An existing assignment is returned unchanged, even if unusable (the strict
    readiness check reports it). A missing one is registered only from the
    single AAA proven by current institution-scoped evidence; missing,
    contradictory or colliding evidence is refused and nothing is generated.
    """
    existing = OrganizationSpeciesCode.objects.filter(organization=organization, species=species).first()
    if existing is not None:
        return existing, False
    codes, issues = resolve_institution_species_codes(organization, [species.pk])
    if species.pk in issues:
        raise InstitutionSpeciesConflict(issues[species.pk])
    recorded_code = codes.get(species.pk)
    if recorded_code is None:
        raise InstitutionSpeciesConflict("species_code_missing")
    # Fails with historical_code_conflict rather than substituting a code.
    return allocate_species_code(organization=organization, species=species, recorded_code=recorded_code)


def audit_recorded_code(*, user, organization, assignment, source, **extra):
    _audit(
        organization=organization, user=user, action=AuditLog.Action.CREATION,
        object_type="organization_species_code", object_id=assignment.pk,
        description=f"Species code assigned for species {assignment.species_id}",
        metadata={
            "source": source, "species_id": assignment.species_id,
            "code": assignment.code, "recorded_code_preserved": True, **extra,
        },
    )


def _is_unchanged_from_shared(species, name, aphia_id):
    return normalize_scientific_name(species.scientific_name) == name and aphia_id == species.worms_aphia_id


def edit_existing_species(*, user, organization, species_id, data):
    """Explicit identification save for a managed Species without a profile.

    Returns (profile or None, changed). The dialog was prefilled from the shared
    reference; an unchanged submission writes nothing. Otherwise the profile is
    created with the submitted final name and AphiaID, with its association
    audit and the business update audit, atomically. The shared reference, the
    Species code and all Strain/Box identifiers stay untouched; no AAA is
    required or generated. If a profile appeared meanwhile, the intent is stale.
    """
    require_institution_admin(user, organization)
    _check_fields(data, REGISTRATION_FIELDS, REGISTRATION_FIELDS)
    errors = {}
    name = _clean_name(data["scientific_name"], errors)
    aphia_id = _clean_aphia_id(data["worms_aphia_id"], errors)
    if errors:
        raise InstitutionSpeciesInvalid(errors)
    if type(species_id) is not int or species_id < 1:
        raise InstitutionSpeciesNotFound()

    def check(locked_organization, species, profile):
        if profile is not None:
            raise _stale(profile.revision, institution_species_id=profile.pk)
        return _is_unchanged_from_shared(species, name, aphia_id)

    # Short pre-pass in its own transaction, holding no gate: report not-found,
    # stale or unchanged intents before any WoRMS call. Rechecked under locks.
    with transaction.atomic():
        if check(*lock_managed_species(user=user, organization=organization, species_id=species_id)):
            return None, False
    if aphia_id is not None:
        _verify_reference(aphia_id, name)

    with species_action_transaction():
        locked, species, profile = lock_managed_species(user=user, organization=organization, species_id=species_id)
        if check(locked, species, profile):
            return None, False
        # A historical reference still shown here under its shared name keeps it.
        profiled = InstitutionSpecies.objects.for_organization(locked).values("species_id")
        for shared in _name_matches(Species.objects.exclude(pk=species.pk), name).exclude(pk__in=profiled):
            if _has_history(locked, shared):
                raise InstitutionSpeciesConflict(
                    "historical_name_collision", species_id=shared.pk,
                    species_code=_own_code(locked, shared.pk),
                )
        profile = associate_existing_species(
            organization=locked, species=species, scientific_name=name, worms_aphia_id=aphia_id,
        )
        audit_association(user=user, organization=locked, profile=profile, source=EDIT_ASSOCIATION_SOURCE)
        changes = {
            field: {"before": before, "after": after}
            for field, before, after in (
                ("scientific_name", species.scientific_name, profile.scientific_name),
                ("worms_aphia_id", species.worms_aphia_id, profile.worms_aphia_id),
            )
            if before != after
        }
        _audit(
            organization=locked, user=user, action=AuditLog.Action.UPDATE,
            object_type="institution_species", object_id=profile.pk,
            description=f"Institution species information updated: {profile.scientific_name}",
            metadata={
                "species_id": species.pk,
                "revision": {"before": None, "after": profile.revision},
                "changes": changes,
                "institution_species_created": True,
            },
        )
    return profile, True


# Modification ---------------------------------------------------------------

def _stale(profile_revision, **details):
    return InstitutionSpeciesConflict("stale_revision", current_revision=profile_revision, **details)


def update_institution_species(*, user, organization, pk, data):
    """Apply an explicit final local name and AphiaID; return (profile, changed).

    ``expected_revision`` protects against lost updates. A kept or new AphiaID
    is re-verified against the final name; a mismatch is refused, never
    replaced by WoRMS's accepted name. Sending ``worms_aphia_id: null`` is the
    explicit manual path and needs no network.
    """
    require_institution_admin(user, organization)
    expected_revision, name, aphia_id = clean_edit(data)
    current = InstitutionSpecies.objects.for_organization(organization).filter(pk=pk).first()
    if current is None:
        raise InstitutionSpeciesNotFound()
    if current.revision != expected_revision:
        raise _stale(current.revision)
    if name == current.scientific_name and aphia_id == current.worms_aphia_id:
        return current, False
    if aphia_id is not None:
        _verify_reference(aphia_id, name)

    with transaction.atomic():
        locked_organization = Organization.objects.select_for_update().get(pk=organization.pk)
        require_institution_admin(user, locked_organization)
        profiles = InstitutionSpecies.objects.for_organization(locked_organization)
        before = profiles.select_for_update().filter(pk=pk).first()
        if before is None:
            raise InstitutionSpeciesNotFound()
        if before.revision != expected_revision:
            raise _stale(before.revision)
        clash = _name_matches(profiles.exclude(pk=pk), name).first()
        if clash is not None:
            raise InstitutionSpeciesConflict(
                "institution_species_name_exists",
                institution_species_id=clash.pk, scientific_name=clash.scientific_name,
            )
        if aphia_id is not None:
            clash = profiles.exclude(pk=pk).filter(worms_aphia_id=aphia_id).first()
            if clash is not None:
                raise InstitutionSpeciesConflict(
                    "institution_aphia_id_exists",
                    institution_species_id=clash.pk, scientific_name=clash.scientific_name,
                )
        # A historical reference still shown here under its shared name keeps it.
        profiled = profiles.values("species_id")
        for shared in _name_matches(Species.objects.exclude(pk=before.species_id), name).exclude(pk__in=profiled):
            if _has_history(locked_organization, shared):
                raise InstitutionSpeciesConflict(
                    "historical_name_collision", species_id=shared.pk,
                    species_code=_own_code(locked_organization, shared.pk),
                )
        try:
            with transaction.atomic():
                updated = InstitutionSpecies.objects.compare_and_swap(
                    organization=locked_organization, pk=pk, expected_revision=expected_revision,
                    scientific_name=name, worms_aphia_id=aphia_id,
                )
        except IntegrityError:
            raise InstitutionSpeciesConflict("institution_species_name_exists") from None
        if updated != 1:
            latest = profiles.filter(pk=pk).values_list("revision", flat=True).first()
            if latest is None:
                raise InstitutionSpeciesNotFound()
            raise _stale(latest)
        after = profiles.get(pk=pk)
        changes = {
            field: {"before": getattr(before, field), "after": getattr(after, field)}
            for field in ("scientific_name", "worms_aphia_id")
            if getattr(before, field) != getattr(after, field)
        }
        _audit(
            organization=locked_organization, user=user, action=AuditLog.Action.UPDATE,
            object_type="institution_species", object_id=after.pk,
            description=f"Institution species information updated: {after.scientific_name}",
            metadata={
                "species_id": after.species_id,
                "revision": {"before": before.revision, "after": after.revision},
                "changes": changes,
            },
        )
    return after, True
