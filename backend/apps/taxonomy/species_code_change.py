"""Read-only impact plan for changing an institutional Species code (AAA).

Approved identifier rule: Strain Local ID ``AAA-BBB-X`` and Box Local ID
``AAA-BBB-X.YYY``. An administrator may change the AAA of their institution;
this module only *plans* that change. It never writes: no Strain code, Box
Local ID (``Box.global_code``), Global ID, alias, namespace, counter or audit is
created or modified, and no lock is taken.

The plan is advisory. It is computed from several reads and can be stale as
soon as it is returned. A future mutation service must recompute it inside its
own transaction, under its own locks, and compare ``plan_fingerprint``; a
preview is never an authorization to mutate.

Scope and disclosure: only the active institution's records are enumerated.
Identifiers are globally unique, so a collision with another institution is
reported as ``scope: "outside_institution"`` without naming its record or owner.

What the plan refuses to decide (reported as blockers, never resolved here):

- Strains this institution does not exclusively own (used by another
  institution's Boxes, unowned legacy, or owned by another institution);
- identifiers that contradict the current assignment;
- unresolved or conflicting current/previous/registered Local ID claims,
  and the reviewed historical identities guarded by ``Box.save``;
- any collision with a current or historically reserved identifier.
"""

import hashlib
import json
from collections import Counter, defaultdict

from django.db.models import Q

from apps.audit.models import AuditLog
from apps.cultures.box_codes import _reconciled_high_water
from apps.cultures.box_identity import CANONICAL_STRAIN_CODE
from apps.cultures.models import (
    AutomaticBoxCreationRequest, Box, BoxCodeNamespace, BoxLineage, BoxLocalIdAlias, BoxTransferImport,
    IdentificationTag, SubcultureAllocation, TransferItem,
)

from .institution_species_services import (
    InstitutionSpeciesInvalid, InstitutionSpeciesNotFound, require_institution_admin,
)
from .institution_strains import SEGMENT
from .models import InstitutionSpecies, LocalStrainIdentity, OrganizationSpeciesCode, Strain

PLAN_FORMAT = "polypbase.species_code_change.preview.v1"

# Strain ownership as seen from the active institution.
OWNED = "institution_owned"          # owned here, used only by this institution's Boxes
SHARED = "shared"                    # owned here, also used by another institution's Boxes
LEGACY_UNOWNED = "legacy_unowned"    # no owning institution, used by Boxes here
FOREIGN_OWNED = "foreign_owned"      # owned by another institution, used by Boxes here

RENAME = "rename"
UNCHANGED = "unchanged"
BLOCKED = "blocked"

# Blocker categories.
COLLISION = "collision"
SHARED_DATA = "shared_data"
HISTORICAL_IDENTITY = "historical_identity"
CONSISTENCY = "consistency"
VALIDATION = "validation"

_OWNERSHIP_BLOCKERS = {
    SHARED: "strain_used_by_another_institution",
    LEGACY_UNOWNED: "strain_without_owner",
    FOREIGN_OWNED: "strain_owned_by_another_institution",
}

# Code-based consumers found by inspection. They are not countable per plan; a
# mutation must keep each of them correct. Keys are stable, not user-facing.
CODE_BASED_LOOKUPS = (
    # /boxes/<Local ID> resolves current, previous and registered Local IDs.
    {"kind": "box_local_id_url", "effect": "requires_alias"},
    # Box list search matches current, previous and registered IDs and Strain codes.
    {"kind": "box_search", "effect": "requires_alias"},
    # Audit entries store the Local ID at the time; links resolve by current ID.
    {"kind": "audit_box_link_resolution", "effect": "requires_alias"},
    # CSV importers and the measurement import planner match Boxes by Local ID.
    {"kind": "csv_import_matching", "effect": "requires_review"},
    # Exports emit the current Local ID and Strain code.
    {"kind": "exports", "effect": "requires_review"},
    # Transfer v1 import matches a Strain by Species name then Strain code.
    {"kind": "transfer_v1_strain_matching", "effect": "requires_review"},
    # QR codes target /bac/<id>/ (primary key), not the Local ID.
    {"kind": "qr_scan_url", "effect": "unaffected"},
    # Global ID routes resolve by UUID.
    {"kind": "box_global_id_url", "effect": "unaffected"},
)


def clean_proposed_code(value):
    """Normalize like the collection-location code: trimmed, uppercased, ``[A-Z]{3}``."""
    if not isinstance(value, str) or not value.strip():
        raise InstitutionSpeciesInvalid({"proposed_code": ["required"]})
    code = value.strip().upper()
    if not SEGMENT.fullmatch(code):
        raise InstitutionSpeciesInvalid({"proposed_code": ["invalid_format"]})
    return code


def _scope(organization, organization_id):
    return "this_institution" if organization_id == organization.pk else "outside_institution"


def _ownership(strain, organization, used_elsewhere):
    if strain.organization_id == organization.pk:
        return SHARED if strain.pk in used_elsewhere else OWNED
    return LEGACY_UNOWNED if strain.organization_id is None else FOREIGN_OWNED


def _fingerprint(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _identity_history(box_ids, proposed_prefix):
    """Read relevant claims globally; keep their graph snapshot internal only.

    Include incoming merge sources, even from other Species, so renaming a
    terminal cannot silently invalidate an older donor's Local IDs. Legacy
    conflicts skipped by migration 0023 still live in the previous field.
    """
    fields = ("pk", "organization_id", "global_code", "previous_global_code", "merged_into_id")
    related = {pk: {pk} for pk in box_ids}
    frontier = set(related)
    while frontier:
        incoming = list(Box.objects.filter(merged_into_id__in=frontier).values(*fields))
        frontier = set()
        for row in incoming:
            impacted = related.setdefault(row["pk"], set())
            added = related[row["merged_into_id"]] - impacted
            if added:
                impacted.update(added)
                frontier.add(row["pk"])
    rows = {row["pk"]: row for row in Box.objects.filter(pk__in=related).values(*fields)}
    aliases = list(BoxLocalIdAlias.objects.filter(
        Q(box_id__in=related) | Q(code__startswith=proposed_prefix),
    ).order_by("code").values("code", "box_id"))
    codes = {code for row in rows.values() for code in (row["global_code"], row["previous_global_code"]) if code}
    codes.update(alias["code"] for alias in aliases)
    rows.update({row["pk"]: row for row in Box.objects.filter(
        Q(global_code__in=codes) | Q(previous_global_code__in=codes)
        | Q(global_code__startswith=proposed_prefix) | Q(previous_global_code__startswith=proposed_prefix),
    ).values(*fields)})
    aliases = list(BoxLocalIdAlias.objects.filter(
        Q(code__in=codes) | Q(code__startswith=proposed_prefix),
    ).order_by("code").values("code", "box_id"))
    missing = {alias["box_id"] for alias in aliases} - rows.keys()
    missing.update({row["merged_into_id"] for row in rows.values()} - {None} - rows.keys())
    while missing:
        loaded = {row["pk"]: row for row in Box.objects.filter(pk__in=missing).values(*fields)}
        rows.update(loaded)
        missing = {row["merged_into_id"] for row in loaded.values()} - {None} - rows.keys()

    def canonical(source):
        seen = set()
        owner = rows[source]["organization_id"] if source in rows else None
        while source in rows and source not in seen:
            seen.add(source)
            row = rows[source]
            if row["organization_id"] != owner:
                return None
            if row["merged_into_id"] is None:
                return (owner, source)
            source = row["merged_into_id"]
        return None

    claims = defaultdict(set)
    for row in rows.values():
        for code in (row["global_code"], row["previous_global_code"]):
            if code:
                claims[code].add(row["pk"])
    for alias in aliases:
        claims[alias["code"]].add(alias["box_id"])
    return related, rows, aliases, claims, canonical


def plan_species_code_change(*, user, organization, profile_pk, proposed_code):
    """Return the complete impact of changing this institution's AAA; write nothing.

    Raises ``PermissionDenied`` (not an active Admin of ``organization``),
    ``InstitutionSpeciesNotFound`` (no such profile in this institution) or
    ``InstitutionSpeciesInvalid`` (malformed code). Every other unsafe condition
    is returned as a blocker so the administrator sees the whole picture.
    """
    require_institution_admin(user, organization)
    proposed = clean_proposed_code(proposed_code)
    profile = InstitutionSpecies.objects.for_organization(organization).filter(pk=profile_pk).first()
    if profile is None:
        raise InstitutionSpeciesNotFound()
    species_id = profile.species_id
    assignment = OrganizationSpeciesCode.objects.filter(organization=organization, species_id=species_id).first()
    current = assignment.code if assignment else None

    blockers, warnings = [], []

    def block(code, category, **details):
        blockers.append({"code": code, "category": category, **details})

    # Code-level validation -------------------------------------------------
    if assignment is None:
        block("species_code_missing", VALIDATION)
    elif not SEGMENT.fullmatch(current):
        block("current_code_unusable", VALIDATION)
    elif proposed == current:
        block("code_unchanged", VALIDATION)
    plannable = not blockers

    taken = (
        OrganizationSpeciesCode.objects.filter(organization=organization, code=proposed)
        .exclude(species_id=species_id).first()
    )
    if taken is not None:
        own_name = (
            InstitutionSpecies.objects.for_organization(organization)
            .filter(species_id=taken.species_id).values_list("scientific_name", flat=True).first()
        )
        block(
            "code_assigned_in_institution", COLLISION, species_id=taken.species_id,
            scientific_name=own_name or taken.species.scientific_name,
        )
    proposed_prefix = f"{proposed}-"
    other_species_here = (
        Strain.objects.filter(organization=organization, code__startswith=proposed_prefix)
        .exclude(species_id=species_id).exists()
        or Box.objects.filter(organization=organization, global_code__startswith=proposed_prefix)
        .exclude(strain__species_id=species_id).exists()
    )
    if other_species_here:
        block("proposed_code_used_by_other_species", COLLISION)
    # Automatic generation avoids codes other institutions use; manual reuse
    # across institutions has no approved rule, so it is reported, not decided.
    if (OrganizationSpeciesCode.objects.filter(code=proposed).exclude(organization=organization)
            .exclude(species_id=species_id).exists()
            or Strain.objects.filter(code__startswith=proposed_prefix)
            .exclude(organization=organization).exclude(species_id=species_id).exists()
            or Box.objects.filter(global_code__startswith=proposed_prefix)
            .exclude(organization=organization).exclude(strain__species_id=species_id).exists()):
        warnings.append({"code": "proposed_code_used_outside_institution"})

    # Enumeration (never paginated or filtered by status) ---------------------
    box_rows = list(
        Box.objects.filter(organization=organization, strain__species_id=species_id).order_by("pk")
        .values("pk", "strain_id", "global_code", "previous_global_code", "merged_into_id", "status",
                "global_identity_id", "local_code")
    )
    strains = list(
        Strain.objects.filter(species_id=species_id)
        .filter(Q(organization=organization) | Q(pk__in={row["strain_id"] for row in box_rows}))
        .select_related("local_identity").order_by("pk")
    )
    strain_ids = [strain.pk for strain in strains]
    used_elsewhere = set(
        Box.objects.filter(strain_id__in=strain_ids).exclude(organization=organization)
        .values_list("strain_id", flat=True)
    )
    boxes_by_strain = Counter(row["strain_id"] for row in box_rows)

    strain_items, proposed_strain_codes = [], {}
    for strain in strains:
        ownership = _ownership(strain, organization, used_elsewhere)
        item = {
            "id": strain.pk, "current_code": strain.code, "proposed_code": None,
            "ownership": ownership, "action": UNCHANGED, "reasons": [],
            "box_count": boxes_by_strain.get(strain.pk, 0),
        }
        if ownership == LEGACY_UNOWNED:
            item["also_used_by_another_institution"] = strain.pk in used_elsewhere
        strain_items.append(item)

        # A missing reverse one-to-one raises an AttributeError subclass.
        identity = getattr(strain, "local_identity", None)
        if (identity is not None and assignment is not None
                and identity.species_code_assignment_id != assignment.pk):
            item["reasons"].append("local_identity_mismatch")
            item["action"] = BLOCKED
            block("local_identity_mismatch", CONSISTENCY, strain_id=strain.pk)

        match = CANONICAL_STRAIN_CODE.fullmatch(strain.code or "")
        if match is None:
            item["reasons"].append("code_format")
            warnings.append({"code": "noncanonical_strain_unchanged", "strain_id": strain.pk})
            continue
        if not plannable:
            continue
        if match.group(1) != current:
            item["reasons"].append("prefix_mismatch")
            item["action"] = BLOCKED
            block("strain_prefix_mismatch", CONSISTENCY, strain_id=strain.pk, identifier=strain.code)
            continue
        item["proposed_code"] = f"{proposed}-{match.group(2)}-{match.group(3)}"
        proposed_strain_codes[strain.pk] = item["proposed_code"]
        if ownership in _OWNERSHIP_BLOCKERS:
            reason = _OWNERSHIP_BLOCKERS[ownership]
            item["reasons"].append(reason)
            item["action"] = BLOCKED
            block(reason, SHARED_DATA, strain_id=strain.pk, identifier=strain.code)
        elif item["action"] != BLOCKED:
            item["action"] = RENAME

    # This assignment referenced by a Strain outside the plan (never named).
    if assignment is not None:
        stray = LocalStrainIdentity.objects.filter(species_code_assignment=assignment).exclude(
            strain_id__in=[s.pk for s in strains if s.organization_id == organization.pk],
        ).count()
        if stray:
            block("assignment_used_by_unplanned_strain", CONSISTENCY, count=stray)

    # Strain collisions -------------------------------------------------------
    items_by_strain = {item["id"]: item for item in strain_items}

    def block_strain(strain_id, code, **details):
        item = items_by_strain[strain_id]
        item["action"] = BLOCKED
        if code not in item["reasons"]:
            item["reasons"].append(code)
        block(code, COLLISION, strain_id=strain_id, **details)

    strains_by_proposed = defaultdict(list)
    for strain_id, code in proposed_strain_codes.items():
        strains_by_proposed[code].append(strain_id)
    for code, ids in sorted(strains_by_proposed.items()):
        if len(ids) > 1:
            for strain_id in ids:
                block_strain(strain_id, "strain_code_collision_in_batch", identifier=code)
    if proposed_strain_codes:
        for existing in Strain.objects.filter(code__in=set(proposed_strain_codes.values())).order_by("pk"):
            scope = _scope(organization, existing.organization_id)
            for strain_id in strains_by_proposed[existing.code]:
                details = {"identifier": existing.code, "scope": scope}
                if scope == "this_institution":
                    details["conflicting_strain_id"] = existing.pk
                block_strain(strain_id, "strain_code_collision", **details)
        # A Strain code is also the global Box namespace.
        for namespace in BoxCodeNamespace.objects.filter(
            namespace__in=set(proposed_strain_codes.values()),
        ).order_by("namespace"):
            for strain_id in strains_by_proposed[namespace.namespace]:
                block_strain(strain_id, "box_namespace_reserved", identifier=namespace.namespace)

    # Boxes -------------------------------------------------------------------
    strain_codes = {strain.pk: strain.code for strain in strains}
    box_items = []
    for row in box_rows:
        item = {
            "id": row["pk"], "strain_id": row["strain_id"], "status": row["status"],
            "current_local_id": row["global_code"], "proposed_local_id": None,
            "previous_local_id": row["previous_global_code"] or None,
            "global_id": str(row["global_identity_id"]) if row["global_identity_id"] else None,
            "is_merged_alias": row["merged_into_id"] is not None,
            "action": UNCHANGED, "reasons": [],
        }
        box_items.append(item)
        code = row["global_code"]
        if row["merged_into_id"] is not None:
            # A merged record is itself a historical alias and stays read-only.
            item["reasons"].append("merged_alias_retained")
            continue
        if not plannable:
            continue
        strain_code = strain_codes[row["strain_id"]]
        proposed_strain = proposed_strain_codes.get(row["strain_id"])
        if proposed_strain is not None and code.startswith(f"{strain_code}."):
            item["proposed_local_id"] = proposed_strain + code[len(strain_code):]
            if items_by_strain[row["strain_id"]]["action"] == BLOCKED:
                item["action"] = BLOCKED
                item["reasons"].append("strain_blocked")
            else:
                item["action"] = RENAME
        elif code.startswith(f"{current}-"):
            item["action"] = BLOCKED
            item["reasons"].append("local_id_strain_mismatch")
            block("box_local_id_strain_mismatch", CONSISTENCY, box_id=row["pk"], identifier=code)
        else:
            item["reasons"].append("local_id_without_current_code")
            warnings.append({"code": "box_without_current_code_unchanged", "box_id": row["pk"]})

    box_by_id = {item["id"]: item for item in box_items}
    affected = [item for item in box_items if item["proposed_local_id"] is not None]

    def block_box(box_id, code, category, **details):
        item = box_by_id[box_id]
        item["action"] = BLOCKED
        if code not in item["reasons"]:
            item["reasons"].append(code)
        block(code, category, box_id=box_id, **details)

    related, history_rows, aliases, claims, canonical = _identity_history(box_by_id, proposed_prefix)
    # Never assume the registry backfill certified every legacy previous ID.
    # Every retained claim must still resolve to the same authorized terminal.
    for source, impacted in sorted(related.items()):
        row = history_rows[source]
        source_codes = {row["global_code"], row["previous_global_code"]} - {""}
        source_codes.update(alias["code"] for alias in aliases if alias["box_id"] == source)
        identity = canonical(source)
        unsafe = identity is None or identity[0] != organization.pk or not row["global_code"]
        for code in source_codes:
            targets = {canonical(owner) for owner in claims[code]}
            if (not code.strip() or code != code.strip() or len(code) > 100
                    or targets != {identity} or None in targets):
                unsafe = True
        if unsafe:
            for box_id in sorted(impacted):
                if "local_id_history_unresolved" not in box_by_id[box_id]["reasons"]:
                    block_box(box_id, "local_id_history_unresolved", HISTORICAL_IDENTITY)

    reviewed = Box._reviewed_identity_codes
    boxes_by_proposed = defaultdict(list)
    for item in affected:
        boxes_by_proposed[item["proposed_local_id"]].append(item["id"])

        if item["current_local_id"] in reviewed or item["proposed_local_id"] in reviewed:
            block_box(item["id"], "reviewed_identity_protected", HISTORICAL_IDENTITY,
                      identifier=item["current_local_id"])
    for code, ids in sorted(boxes_by_proposed.items()):
        if len(ids) > 1:
            for box_id in ids:
                block_box(box_id, "box_local_id_collision_in_batch", COLLISION, identifier=code)
    if boxes_by_proposed:
        proposed_ids = set(boxes_by_proposed)
        for existing in Box.objects.filter(
            Q(global_code__in=proposed_ids) | Q(previous_global_code__in=proposed_ids),
        ).order_by("pk").values("pk", "organization_id", "global_code", "previous_global_code"):
            scope = _scope(organization, existing["organization_id"])
            for field, reason in (("global_code", "box_local_id_collision"),
                                  ("previous_global_code", "box_local_id_historically_reserved")):
                for box_id in boxes_by_proposed.get(existing[field], ()):
                    details = {"identifier": existing[field], "scope": scope}
                    if scope == "this_institution":
                        details["conflicting_box_id"] = existing["pk"]
                    block_box(box_id, reason, COLLISION, **details)
    for alias in aliases:
        scope = _scope(organization, history_rows[alias["box_id"]]["organization_id"])
        for box_id in boxes_by_proposed.get(alias["code"], ()):
            details = {"identifier": alias["code"], "scope": scope}
            if scope == "this_institution":
                details["conflicting_box_id"] = alias["box_id"]
            block_box(box_id, "box_local_id_historically_reserved", COLLISION, **details)

    # Any other Box already numbered in a proposed namespace, in any institution.
    if proposed_strain_codes and plannable:
        namespaces = {f"{code}.": strain_id for strain_id, code in proposed_strain_codes.items()}
        seen = set()
        for existing in Box.objects.filter(
            Q(global_code__startswith=proposed_prefix) | Q(previous_global_code__startswith=proposed_prefix),
        ).order_by("pk").values("organization_id", "global_code", "previous_global_code"):
            for value in (existing["global_code"], existing["previous_global_code"]):
                for namespace, strain_id in namespaces.items():
                    key = (strain_id, _scope(organization, existing["organization_id"]))
                    if value.startswith(namespace) and key not in seen:
                        seen.add(key)
                        block_strain(strain_id, "box_namespace_in_use", identifier=namespace[:-1], scope=key[1])
        for alias in aliases:
            scope = _scope(organization, history_rows[alias["box_id"]]["organization_id"])
            for namespace, strain_id in namespaces.items():
                key = (strain_id, scope)
                if alias["code"].startswith(namespace) and key not in seen:
                    seen.add(key)
                    block_strain(strain_id, "box_namespace_in_use", identifier=namespace[:-1], scope=scope)
        # A Strain blocked by a namespace collision blocks its Boxes too.
        for item in affected:
            if item["action"] == RENAME and items_by_strain[item["strain_id"]]["action"] == BLOCKED:
                item["action"] = BLOCKED
                item["reasons"].append("strain_blocked")

    # Current AAA carried by this institution's Boxes of another Species.
    if plannable:
        stray_boxes = list(
            Box.objects.filter(organization=organization, global_code__startswith=f"{current}-")
            .exclude(strain__species_id=species_id).order_by("pk").values_list("pk", flat=True)
        )
        if stray_boxes:
            warnings.append({"code": "current_code_on_other_species_boxes", "box_ids": stray_boxes})

    # Missing Global IDs: reported, never created here.
    missing_global = [item["id"] for item in affected if item["global_id"] is None]
    if missing_global:
        warnings.append({"code": "missing_global_id", "box_ids": missing_global})

    # Dependencies ------------------------------------------------------------
    affected_ids = [item["id"] for item in affected]
    affected_codes = [item["current_local_id"] for item in affected]
    live = [item for item in affected if item["status"] != Box.Status.INACTIVE]
    renamed_strain_codes = sorted({strain_codes[pk] for pk in proposed_strain_codes})
    dependencies = [
        {"kind": "audit_entries_by_local_id", "effect": "requires_alias", "count": AuditLog.objects.filter(
            organization=organization, object_type="box", object_id__in=affected_codes).count()},
        {"kind": "historical_alias_records", "effect": "keeps_old_identifier",
         "count": sum(1 for item in box_items if item["is_merged_alias"])},
        {"kind": "boxes_with_previous_local_id", "effect": "requires_alias",
         "count": sum(1 for item in affected if item["previous_local_id"])},
        {"kind": "qr_tags", "effect": "unaffected", "count": IdentificationTag.objects.filter(
            box_id__in=affected_ids, tag_type=IdentificationTag.TagType.QR).count()},
        {"kind": "physical_labels", "effect": "requires_review", "count": len(live)},
        {"kind": "lineage_links", "effect": "unaffected", "count": BoxLineage.objects.filter(
            Q(parent_box_id__in=affected_ids) | Q(child_box_id__in=affected_ids)).count()},
        {"kind": "subculture_allocation_snapshots", "effect": "keeps_old_identifier",
         "count": SubcultureAllocation.objects.filter(child_box_id__in=affected_ids).count()},
        {"kind": "transfer_v2_item_snapshots", "effect": "keeps_old_identifier",
         "count": TransferItem.objects.filter(source_box_id__in=affected_ids).count()},
        {"kind": "transfer_v1_import_records", "effect": "unaffected",
         "count": BoxTransferImport.objects.filter(created_box_id__in=affected_ids).count()},
        {"kind": "automatic_creation_receipts", "effect": "unaffected",
         "count": AutomaticBoxCreationRequest.objects.filter(box_id__in=affected_ids).count()},
        {"kind": "box_code_namespaces", "effect": "requires_carry_over", "count": BoxCodeNamespace.objects.filter(
            namespace__in=renamed_strain_codes).count()},
        {"kind": "box_local_code_field", "effect": "requires_review", "count": sum(
            1 for row in box_rows if current and row["local_code"].startswith(f"{current}-"))},
    ]

    summary = {
        "strains": len(strain_items),
        "strains_to_rename": sum(1 for item in strain_items if item["action"] == RENAME),
        "strains_blocked": sum(1 for item in strain_items if item["action"] == BLOCKED),
        "strains_unchanged": sum(1 for item in strain_items if item["action"] == UNCHANGED),
        "boxes": len(box_items),
        "boxes_to_rename": sum(1 for item in box_items if item["action"] == RENAME),
        "boxes_blocked": sum(1 for item in box_items if item["action"] == BLOCKED),
        "boxes_unchanged": sum(1 for item in box_items if item["action"] == UNCHANGED),
        "boxes_missing_global_id": len(missing_global),
        "blockers": len(blockers),
    }
    plan = {
        "format": PLAN_FORMAT,
        "organization_id": organization.pk,
        "institution_species": {
            "id": profile.pk, "species_id": species_id,
            "scientific_name": profile.scientific_name, "revision": profile.revision,
        },
        "code_assignment_id": assignment.pk if assignment else None,
        "current_code": current,
        "proposed_code": proposed,
        "feasible": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "strains": strain_items,
        "boxes": box_items,
        "summary": summary,
        "dependencies": dependencies,
        "code_based_lookups": [dict(item) for item in CODE_BASED_LOOKUPS],
    }
    # Deterministic digest of the identity state the plan depends on, for
    # revalidation by a mutation. Counts of unrelated activity (audit entries,
    # tags, Box status) are excluded so they cannot make a valid plan look stale.
    # The mutator carries source allocation floors forward. Read exactly the
    # allocator's effective floor, including historical claims on other Boxes;
    # never reconcile/create namespace rows while building a preview.
    source_names = sorted({strain_codes[pk] for pk in proposed_strain_codes})
    stored_floors = dict(BoxCodeNamespace.objects.filter(
        namespace__in=source_names,
    ).values_list("namespace", "high_water"))
    source_namespaces = [
        {"namespace": name, "exists": name in stored_floors,
         "stored_high_water": stored_floors.get(name),
         "effective_high_water": _reconciled_high_water(name, stored_floors.get(name, 0))}
        for name in source_names
    ]
    basis = {
        "format": PLAN_FORMAT, "organization_id": organization.pk, "institution_species_id": profile.pk,
        "species_id": species_id, "code_assignment_id": plan["code_assignment_id"],
        "profile_revision": profile.revision,
        "source_namespaces": source_namespaces,
        "identity_history": [history_rows[pk] for pk in sorted(history_rows)],
        "aliases": aliases,
        "local_codes": [[row["pk"], row["local_code"]] for row in box_rows],
        "local_assignments": list(LocalStrainIdentity.objects.filter(
            Q(strain_id__in=strain_ids) | Q(species_code_assignment=assignment) if assignment else Q(strain_id__in=strain_ids),
        ).order_by("pk").values(
            "pk", "strain_id", "species_code_assignment_id", "provenance_code_assignment_id",
            "species_code_assignment__organization_id", "species_code_assignment__species_id",
            "species_code_assignment__code", "provenance_code_assignment__organization_id",
            "provenance_code_assignment__code", "provenance_code_assignment__biological_provenance_id",
        )),
        "current_code": current, "proposed_code": proposed, "blockers": blockers,
        "strains": [
            [item["id"], item["current_code"], item["proposed_code"], item["ownership"], item["action"]]
            for item in strain_items
        ],
        "boxes": [
            [item["id"], item["strain_id"], item["current_local_id"], item["proposed_local_id"],
             item["previous_local_id"], item["global_id"], item["is_merged_alias"], item["action"]]
            for item in box_items
        ],
    }
    return {
        **plan,
        "plan_fingerprint": _fingerprint(basis),
        "advisory_only": True,
        "revalidation_required": True,
        "mutation_available": False,
    }
