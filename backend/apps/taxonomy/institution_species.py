"""Inactive institutional Species foundation: scoped reads and advisory preparation.

No public route, write service, backfill or implicit code allocation lives here.
The preparation manifest is an internal maintenance diagnostic, not permission
or evidence sufficient to apply historical assignments automatically.
"""

import re
from collections import defaultdict

from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

from apps.accounts.permissions import (
    get_required_active_organization_from_request,
    user_can_administer_organization,
)
from apps.cultures.box_identity import CANONICAL_STRAIN_CODE, USABLE, strain_box_identity
from apps.cultures.models import Box
from apps.organizations.models import Organization

from .models import InstitutionSpecies, LocalStrainIdentity, OrganizationSpeciesCode, Species, Strain
from .species_names import ScientificNameKey
from .scoping import eligible_strains


CURRENT_BOX_CODE = re.compile(r"^([A-Z]{3})-[A-Z]{3}-[0-9]+\.[0-9]+$")


def resolve_institution_species_codes(organization, species_ids):
    """Return (codes, issues) from current institutional evidence, without writes.

    Assignments are authoritative; absent one, use consistent associated Strain
    prefixes, then current Box Local IDs. Conflicting current evidence is never
    resolved by picking a row. Aliases, audit snapshots and previous IDs are not
    consulted. Merged Boxes cannot provide current evidence or associate an
    unowned Strain. A resolved display code grants no allocation or editing authority.
    """
    if organization is None or organization.pk is None:
        return {}, {}
    species_ids = set(species_ids)
    assignments = dict(OrganizationSpeciesCode.objects.filter(
        organization=organization, species_id__in=species_ids,
    ).values_list("species_id", "code"))
    evidence = defaultdict(set)
    inconsistent = set()
    for species_id, code in assignments.items():
        if re.fullmatch(r"[A-Z]{3}", code or ""):
            evidence[species_id].add(code)
        else:
            inconsistent.add(species_id)
    strains = eligible_strains(organization).filter(
        Q(organization=organization)
        | Q(boxes__organization=organization, boxes__merged_into__isnull=True),
        species_id__in=species_ids,
    ).distinct().select_related(
        "local_identity__species_code_assignment",
        "local_identity__provenance_code_assignment__biological_provenance",
    )
    for strain in strains:
        match = CANONICAL_STRAIN_CODE.fullmatch(strain.code or "")
        if match is None:
            continue
        if strain_box_identity(strain, organization, species_codes=assignments)["status"] != USABLE:
            inconsistent.add(strain.species_id)
        evidence[strain.species_id].add(match.group(1))
    for species_id, owner_id, code in Box.objects.filter(
        organization=organization, strain__species_id__in=species_ids, merged_into__isnull=True,
    ).values_list("strain__species_id", "strain__organization_id", "global_code"):
        if owner_id not in (None, organization.pk):
            inconsistent.add(species_id)
            continue
        if match := CURRENT_BOX_CODE.fullmatch(code or ""):
            evidence[species_id].add(match.group(1))
    # An existing local assignment for a different Species is a contradiction,
    # not permission to display its code on this reference.
    assigned_species = dict(OrganizationSpeciesCode.objects.filter(
        organization=organization,
    ).values_list("code", "species_id"))
    codes, issues = {}, {}
    for species_id in species_ids:
        candidates = evidence[species_id]
        if (species_id in inconsistent or len(candidates) > 1
                or any(assigned_species.get(code, species_id) != species_id for code in candidates)):
            issues[species_id] = "species_code_inconsistent"
        elif candidates:
            codes[species_id] = assignments.get(species_id) or next(iter(candidates))
    return codes, issues


def strain_creation_issue(organization, species_id):
    """Require a persisted, usable assignment consistent with current evidence.

    A display code inferred from labels is never authority to create a Strain.
    This read-only check must be repeated inside the creation transaction.
    """
    assignment = OrganizationSpeciesCode.objects.filter(
        organization=organization, species_id=species_id,
    ).first()
    if assignment is None:
        return "species_code_missing"
    if re.fullmatch(r"[A-Z]{3}", assignment.code or "") is None:
        return "species_code_unusable"
    codes, issues = resolve_institution_species_codes(organization, [species_id])
    if species_id in issues or codes.get(species_id) != assignment.code:
        return "species_code_inconsistent"
    # An unassigned different Species can already carry this AAA locally. Only
    # current evidence counts; foreign use, aliases and previous IDs do not.
    prefix = f"{assignment.code}-"
    strain_codes = eligible_strains(organization).filter(
        Q(organization=organization)
        | Q(boxes__organization=organization, boxes__merged_into__isnull=True),
        code__startswith=prefix,
    ).exclude(species_id=species_id).values_list("code", flat=True).distinct()
    if any(CANONICAL_STRAIN_CODE.fullmatch(code or "") for code in strain_codes):
        return "species_code_inconsistent"
    box_codes = Box.objects.filter(
        organization=organization, merged_into__isnull=True, global_code__startswith=prefix,
    ).exclude(strain__species_id=species_id).values_list("global_code", flat=True)
    if any(CURRENT_BOX_CODE.fullmatch(code or "") for code in box_codes):
        return "species_code_inconsistent"
    return None


def institution_species_for_admin(request):
    """Explicitly authorized queryset; shared Species membership grants no access."""
    if not request.user.is_authenticated or not request.user.is_active:
        raise PermissionDenied()
    organization = get_required_active_organization_from_request(request)
    if organization is None or not user_can_administer_organization(request.user, organization):
        raise PermissionDenied()
    return InstitutionSpecies.objects.for_organization(organization)


def prepare_institution_species_manifest(*, organization):
    """Read-only, institution-bounded evidence inventory for a human-reviewed plan.

    Call from trusted maintenance tooling only. Multiple reads are advisory, not
    an immutable snapshot or an apply authorization; recheck before any future
    migration. Unowned Strain usage is reported, never converted to ownership.
    No other institution's editable information is read or returned.
    """
    if (not isinstance(organization, Organization) or organization.pk is None
            or organization._state.adding
            or not Organization.objects.filter(pk=organization.pk).exists()):
        raise ValueError("An existing explicit organization is required.")

    assignments = {
        item.species_id: item for item in
        OrganizationSpeciesCode.objects.filter(organization=organization).order_by("pk")
    }
    profiles = {
        item.species_id: item for item in
        InstitutionSpecies.objects.for_organization(organization)
        .annotate(name_key=ScientificNameKey("scientific_name")).order_by("pk")
    }
    owned = list(Strain.objects.filter(organization=organization).order_by("pk"))
    legacy = list(Strain.objects.filter(
        organization__isnull=True, boxes__organization=organization,
    ).distinct().order_by("pk"))
    own_box_ids = defaultdict(list)
    for pk, strain_id in Box.objects.filter(
        organization=organization, strain_id__in=[item.pk for item in legacy],
    ).order_by("pk").values_list("pk", "strain_id"):
        own_box_ids[strain_id].append(pk)
    also_used_elsewhere = set(Box.objects.filter(
        strain_id__in=[item.pk for item in legacy],
    ).exclude(organization=organization).values_list("strain_id", flat=True))

    owned_by_species = defaultdict(list)
    legacy_by_species = defaultdict(list)
    for strain in owned:
        owned_by_species[strain.species_id].append(strain)
    for strain in legacy:
        legacy_by_species[strain.species_id].append(strain.pk)
    species_ids = set(assignments) | set(profiles) | set(owned_by_species) | set(legacy_by_species)
    references = Species.objects.filter(pk__in=species_ids).annotate(
        name_key=ScientificNameKey("scientific_name"),
    ).order_by("pk")

    candidates, conflicts = [], []
    for box_id, strain_id, species_id in Box.objects.filter(
        organization=organization, strain__organization__isnull=False,
    ).exclude(strain__organization=organization).order_by("pk").values_list(
        "pk", "strain_id", "strain__species_id",
    ):
        conflicts.append({
            "kind": "foreign_owned_strain_usage", "box_id": box_id,
            "strain_id": strain_id, "species_id": species_id,
        })
    names = defaultdict(list)
    for species in references:
        profile = profiles.get(species.pk)
        assignment = assignments.get(species.pk)
        strains = owned_by_species[species.pk]
        prefixes = sorted({
            match.group(1) for strain in strains
            if (match := CANONICAL_STRAIN_CODE.fullmatch(strain.code or ""))
        })
        item = {
            "species_id": species.pk,
            "shared_scientific_name": species.scientific_name,
            "institution_species_id": profile.pk if profile else None,
            "proposed_scientific_name": profile.scientific_name if profile else species.scientific_name,
            "code_assignment_id": assignment.pk if assignment else None,
            "assigned_code": assignment.code if assignment else None,
            "owned_strain_ids": [strain.pk for strain in strains],
            "legacy_usage_strain_ids": legacy_by_species[species.pk],
            "historical_owned_prefixes": prefixes,
            "requires_review": True,
        }
        candidates.append(item)
        names[profile.name_key if profile else species.name_key].append(item)
        if len(prefixes) > 1:
            conflicts.append({"kind": "multiple_owned_prefixes", "species_id": species.pk})
        if assignment and any(prefix != assignment.code for prefix in prefixes):
            conflicts.append({"kind": "assignment_prefix_mismatch", "species_id": species.pk})
        if assignment:
            prefix = f"{assignment.code}-"
            if (OrganizationSpeciesCode.objects.filter(code=assignment.code).exclude(species=species).exists()
                    or Strain.objects.filter(code__startswith=prefix).exclude(species=species).exists()
                    or Box.objects.filter(global_code__startswith=prefix).exclude(strain__species=species).exists()):
                conflicts.append({"kind": "code_used_for_other_species", "species_id": species.pk})

    # Inspect only this institution's owned Strains; never expose a foreign assignment.
    for identity in LocalStrainIdentity.objects.filter(
        strain_id__in=[item.pk for item in owned],
    ).select_related("strain", "species_code_assignment", "provenance_code_assignment").order_by("pk"):
        assignment = identity.species_code_assignment
        provenance = identity.provenance_code_assignment
        if (assignment.organization_id != organization.pk
                or assignment.species_id != identity.strain.species_id
                or (provenance is not None and provenance.organization_id != organization.pk)):
            conflicts.append({"kind": "local_identity_mismatch", "local_identity_id": identity.pk})

    for identity_id in LocalStrainIdentity.objects.filter(
        species_code_assignment__organization=organization,
    ).exclude(strain__organization=organization).order_by("pk").values_list("pk", flat=True):
        conflicts.append({"kind": "assignment_used_by_nonowned_strain", "local_identity_id": identity_id})

    return {
        "format": "polypbase.institution_species.preparation.v1",
        "organization_id": organization.pk,
        "advisory_only": True,
        "candidates": candidates,
        "species_without_institution_code": [item["species_id"] for item in candidates if item["code_assignment_id"] is None],
        "unowned_strains": [{
            "strain_id": strain.pk,
            "species_id": strain.species_id,
            "institution_box_ids": own_box_ids[strain.pk],
            "also_used_by_another_institution": strain.pk in also_used_elsewhere,
            "ownership_unresolved": True,
        } for strain in legacy],
        "conflicts": conflicts,
        "potential_name_collisions": [{
            "species_ids": [item["species_id"] for item in group],
            "names": [item["proposed_scientific_name"] for item in group],
        } for group in names.values() if len(group) > 1],
    }
