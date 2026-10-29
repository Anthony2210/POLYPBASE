"""Explicit, additive reference preparation; never a culture correction."""

from difflib import SequenceMatcher
import re
import unicodedata

from django.db.models import Q

from apps.audit.models import AuditLog
from apps.cultures.models import Box
from apps.cultures.pca_identity_unification import snapshot
from apps.organizations.models import Organization
from apps.taxonomy.models import LocalStrainIdentity, OrganizationSpeciesCode, Species, Strain
from apps.taxonomy.scoping import eligible_strains
from apps.taxonomy.services import create_local_strain_identity

from . import identity_execution as execution
from . import identity_reconciliation as reconciliation

OPERATION = "cla_sme_references_20261027_v1"
fingerprint = reconciliation.fingerprint


def _rows(query):
    return [snapshot(row) for row in query.order_by("pk")]


def _normalized(value):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", value).casefold())


def _lactea_candidate(name):
    # Compare whole words with the epithet, not a "lacte" substring: a distinct
    # epithet such as "lacteoides" is another taxon, not a lactea spelling.
    folded = "".join(c for c in unicodedata.normalize("NFKD", name).casefold() if not unicodedata.combining(c))
    return (SequenceMatcher(None, _normalized(name), "chrysaoralactea").ratio() >= 0.82
            or any(SequenceMatcher(None, word, "lactea").ratio() >= 0.75
                   for word in re.split(r"[^a-z0-9]+", folded) if word))


def _validate_scope(organization_id, family, source_strain_id, target_strain_id):
    reconciliation._specs(family)
    for value in (organization_id, source_strain_id):
        if type(value) is not int or value <= 0:
            raise ValueError("Explicit positive organization and source Strain PKs are required.")
    if target_strain_id is not None and (type(target_strain_id) is not int or target_strain_id <= 0):
        raise ValueError("The target Strain PK must be a positive integer.")


def build_plan(organization_id, *, family, source_strain_id, target_strain_id=None):
    _validate_scope(organization_id, family, source_strain_id, target_strain_id)
    with execution.session(organization_id=organization_id, family=family,
                           target_strain_id=target_strain_id, operation=OPERATION) as binding:
        return _build_plan(organization_id, family=family, source_strain_id=source_strain_id,
                           target_strain_id=target_strain_id, binding=binding)


def _build_plan(organization_id, *, family, source_strain_id, target_strain_id=None, binding):
    """Read raw state inside the caller's execution session, including apply revalidation."""
    _validate_scope(organization_id, family, source_strain_id, target_strain_id)
    spec = reconciliation._specs(family)[0]
    old_code, code = spec["old_strain_code"], spec["strain_code"]
    old_aaa, aaa = old_code[:3], spec["aaa"]
    organization = Organization.objects.filter(pk=organization_id).first()
    eligible = eligible_strains(organization).select_related("species")
    source = eligible.filter(pk=source_strain_id).first()
    target = eligible.filter(pk=target_strain_id).first() if target_strain_id is not None else None
    conflicts, missing, entries = [], [], []
    if organization is None:
        missing.append("Organization is missing.")
    if source is None:
        missing.append("Source Strain is missing or ineligible in this institution.")
    elif source.code != old_code or source.species.scientific_name != spec["old_species"]:
        conflicts.append("Source must be the exact old Strain and Species identity.")

    species_candidates = list(Species.objects.filter(scientific_name=spec["species"]))
    if family == "cla":
        species_candidates = [s for s in Species.objects.order_by("pk") if _lactea_candidate(s.scientific_name)]
        if any(s.scientific_name != spec["species"] for s in species_candidates):
            conflicts.append("Approximate lactea Species spelling requires separate review.")
    target_species = next((s for s in species_candidates if s.scientific_name == spec["species"]), None)
    if len(species_candidates) > 1:
        conflicts.append("Multiple target Species candidates require separate review.")
    create_species = target_species is None and family == "cla" and target_strain_id is None
    if target_species is None and not create_species:
        missing.append("Exact target Species is missing; no automatic creation is authorized.")
    entries.append({"reference": "target_species", "classification": "MISSING_REFERENCE" if target_species is None else "ALREADY_PRESENT",
                    "create": create_species, "scientific_name": spec["species"],
                    "pk": target_species.pk if target_species else None})

    # Include owned alternatives and eligible shared references only. A hidden
    # collision is a boolean guard, never a foreign reference snapshot.
    candidate_query = Q(code__iexact=code)
    if target_species is not None:
        candidate_query |= Q(species=target_species, number=1, origin_code="JKA")
    candidates = list(eligible.filter(candidate_query).order_by("pk"))
    if target_strain_id is None and family == "cla":
        if len(candidates) == 1:
            target = candidates[0]
        elif len(candidates) > 1:
            conflicts.append("Multiple target Strain candidates require an explicit separate review.")
    if target is not None:
        if target.code != code or target.species.scientific_name != spec["species"]:
            conflicts.append("Target must have the exact approved Strain code and Species.")
        if any(s.pk != target.pk for s in candidates):
            conflicts.append("The institutional target Strain namespace is ambiguous.")
    elif target_strain_id is not None or family == "sme":
        missing.append("Supply an existing eligible target Strain PK; a global target requires a prior own Box witness.")

    collision = Strain.objects.filter(candidate_query)
    if target is not None:
        collision = collision.exclude(pk=target.pk)
    # Identical codes at a different institution may exist, but cannot be
    # adopted. They also prevent treating the corrected reference as absent.
    hidden_collision = collision.exclude(pk__in=[s.pk for s in candidates]).exists()
    if target is None and hidden_collision:
        conflicts.append("A target reference already exists outside the eligible candidate set; contents withheld.")
    create_strain = (family == "cla" and target_strain_id is None and target is None
                     and not candidates and not hidden_collision)
    if target is None and not create_strain and not missing:
        missing.append("Target Strain is unavailable.")
    entries.append({"reference": "target_strain", "classification": "ALREADY_PRESENT" if target else "MISSING_REFERENCE",
                    "create": create_strain, "code": code, "pk": target.pk if target else None})

    assignments = list(OrganizationSpeciesCode.objects.filter(organization_id=organization_id).order_by("pk"))
    for label, assigned_species, prefix in (("source_assignment", source.species if source else None, old_aaa),
                                             ("target_assignment", target_species, aaa)):
        by_code = [a for a in assignments if a.code.casefold() == prefix.casefold()]
        by_species = [a for a in assignments if assigned_species and a.species_id == assigned_species.pk]
        valid = (len(by_code) == 1 and assigned_species is not None
                 and by_code[0].code == prefix and by_code[0].species_id == assigned_species.pk)
        inconsistent = bool((by_code and not valid) or (by_species and any(a.code != prefix for a in by_species)))
        if inconsistent:
            conflicts.append(f"Institutional {prefix} Species assignment conflicts with the exact approved identity.")
        can_create = not by_code and not by_species and (assigned_species is not None or (label == "target_assignment" and create_species))
        entries.append({"reference": label, "classification": "CONFLICT" if inconsistent else (
            "ALREADY_PRESENT" if valid else "MISSING_REFERENCE"), "create": can_create,
            "code": prefix, "pk": by_code[0].pk if valid else None})

    references = {s.pk: s for s in [source, target, *candidates] if s is not None}
    local_rows = []
    for strain in sorted(references.values(), key=lambda s: s.pk):
        local = LocalStrainIdentity.objects.filter(strain=strain).select_related(
            "species_code_assignment", "provenance_code_assignment").first()
        if local:
            assignment, provenance = local.species_code_assignment, local.provenance_code_assignment
            if (strain.organization_id != organization_id or assignment.organization_id != organization_id
                    or assignment.species_id != strain.species_id
                    or (provenance is not None and provenance.organization_id != organization_id)):
                conflicts.append("An eligible reference has an inconsistent local identity; contents withheld.")
            elif strain.organization_id == organization_id:
                local_rows.append({"identity": snapshot(local), "species_assignment": snapshot(assignment),
                                   "provenance_assignment": snapshot(provenance) if provenance else None})
    entries.append({"reference": "target_local_identity", "classification": "MISSING_REFERENCE" if create_strain else "ALREADY_PRESENT",
                    "create": create_strain})

    witness_boxes = Box.objects.filter(organization_id=organization_id, strain_id__in=references)
    namespace_boxes = Box.objects.filter(organization_id=organization_id).filter(
        Q(global_code__startswith=code + ".") | Q(local_code__startswith=code + "."))
    if namespace_boxes.exclude(strain_id=target.pk if target else None).exists():
        conflicts.append("The institution's target Box namespace contains an incompatible Strain; no Box may be changed here.")
    global_witness_count = witness_boxes.filter(strain=target).count() if target and target.organization_id is None else 0
    state = {"organization": snapshot(organization) if organization else None,
             "references": [snapshot(s) for s in sorted(references.values(), key=lambda s: s.pk)],
             "species": _rows(Species.objects.filter(pk__in={s.species_id for s in references.values()} | {s.pk for s in species_candidates})),
             "assignments": [snapshot(a) for a in assignments], "local_identities": local_rows,
             "witness_boxes": _rows(witness_boxes), "namespace_boxes": _rows(namespace_boxes),
             "hidden_collision": hidden_collision, "global_target_witness_count": global_witness_count}
    return {"operation": OPERATION, "execution": dict(binding), "schema": reconciliation._schema_state(),
            "organization_id": organization_id, "family": family, "source_strain_id": source_strain_id,
            "requested_target_strain_id": target_strain_id, "target_strain_id": target.pk if target else None,
            "classification": "CONFLICT" if conflicts else "MISSING_REFERENCE" if missing else (
                "READY" if any(e["create"] for e in entries) else "ALREADY_PRESENT"),
            "conflicts": conflicts, "missing_references": missing, "entries": entries, "state": state}


def apply_plan(organization_id, *, family, source_strain_id, target_strain_id=None, actor, expected_plan_hash):
    _validate_scope(organization_id, family, source_strain_id, target_strain_id)
    if actor is None or not actor.pk or not expected_plan_hash:
        raise ValueError("An explicit actor and reviewed plan hash are required.")
    with execution.session(apply=True, organization_id=organization_id, family=family,
                           target_strain_id=target_strain_id, actor_id=actor.pk,
                           expected_plan_hash=expected_plan_hash, operation=OPERATION) as binding:
        reconciliation._lock_application_tables()
        organization, user = reconciliation._authorize(organization_id, actor)
        plan = _build_plan(organization_id, family=family, source_strain_id=source_strain_id,
                           target_strain_id=target_strain_id, binding=binding)
        if fingerprint(plan) != expected_plan_hash:
            raise ValueError("Persisted state changed since review; run a new dry-run.")
        if plan["classification"] not in {"READY", "ALREADY_PRESENT"}:
            raise ValueError("Unresolved conflicts or missing required references block preparation.")
        if plan["classification"] == "ALREADY_PRESENT":
            return plan
        spec = reconciliation._specs(family)[0]
        create = {e["reference"] for e in plan["entries"] if e["create"]}
        created = []
        species = Species.objects.filter(scientific_name=spec["species"]).first()
        if "target_species" in create:
            species = Species.objects.create(scientific_name=spec["species"])
            created.append(species)
        source = Strain.objects.get(pk=source_strain_id)
        for label, assigned_species, prefix in (("source_assignment", source.species, spec["old_strain_code"][:3]),
                                                ("target_assignment", species, spec["aaa"])):
            if label in create:
                created.append(OrganizationSpeciesCode.objects.create(
                    organization=organization, species=assigned_species, code=prefix))
        target_id = plan["target_strain_id"]
        if "target_strain" in create:
            target = Strain.objects.create(organization=organization, species=species,
                                          code=spec["strain_code"], number=1, origin_code="JKA")
            created.append(target)
            assignment = OrganizationSpeciesCode.objects.get(organization=organization, species=species, code=spec["aaa"])
            created.append(create_local_strain_identity(strain=target, organization=organization,
                species_code_assignment=assignment, provenance_code_assignment=None, biological_provenance=None))
            target_id = target.pk
        for row in created:
            AuditLog.objects.create(organization=organization, user=user, action=AuditLog.Action.CREATION,
                object_type=row._meta.model_name, object_id=str(row.pk),
                description="Reviewed historical identity reference preparation",
                metadata={"operation": OPERATION, "plan_hash": expected_plan_hash, "created": snapshot(row)})
        AuditLog.objects.create(organization=organization, user=user, action=AuditLog.Action.CREATION,
            object_type="identity_reference_preparation", object_id=family,
            description="Historical identity reference preparation receipt",
            metadata={"operation": OPERATION, "plan_hash": expected_plan_hash, "reviewed_plan": plan,
                      "target_strain_id": target_id,
                      "created": [{"model": row._meta.label_lower, "after": snapshot(row)} for row in created]})
        return {**plan, "prepared_target_strain_id": target_id}


def safe_summary(plan):
    """Operator output deliberately excludes snapshots, notes, and other history."""
    return {key: plan[key] for key in ("operation", "organization_id", "family", "source_strain_id",
            "target_strain_id", "classification")} | {
        "plan_hash": fingerprint({k: v for k, v in plan.items() if k != "prepared_target_strain_id"}),
        "global_target_witness_count": plan["state"]["global_target_witness_count"],
        "references": [{k: e[k] for k in ("reference", "classification", "create")} for e in plan["entries"]],
        "conflicts": plan["conflicts"], "missing_references": plan["missing_references"],
        **({"prepared_target_strain_id": plan["prepared_target_strain_id"]} if "prepared_target_strain_id" in plan else {})}
