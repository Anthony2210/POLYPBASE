"""Bounded deactivation of the stakeholder HS list, separate from the import.

Reuses the audited lifecycle service ``deactivate_box``. Only boxes of the
explicit organization that are active are ever touched; nothing is deleted and
unapproved identifiers are left as pending confirmation.
"""

import hashlib
import json

from django.core.exceptions import PermissionDenied
from django.db import transaction

from apps.accounts.models import OrganizationMembership
from apps.cultures.models import Box
from apps.cultures.services import deactivate_box
from apps.measurements.services import get_active_measurement_role
from apps.organizations.models import Organization

from . import decisions

READY = "READY"
ALREADY_INACTIVE = "ALREADY_INACTIVE"
PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
NOT_FOUND = "NOT_FOUND"
IDENTITY_CORRECTION_PENDING = "IDENTITY_CORRECTION_PENDING"
NOT_ACTIVE = "NOT_ACTIVE"
V2_CONTRADICTION = "V2_CONTRADICTION"


def deactivation_block_reason(*codes):
    """Protect exact HS labels, approved remaps and the corrected CLC identity."""
    for listed, evidence in decisions.HS_V2_CONTRADICTIONS.items():
        resolved = decisions.HS_CODE_CORRECTIONS.get(listed, listed)
        corrected_clc = listed == "CLA-JKA-1.009" and "CLC-JKA-1.009" in codes
        if listed in codes or resolved in codes or corrected_clc:
            detail = (
                f"HS deactivation blocked: V2 observations under {evidence['literal']} "
                f"({evidence['merged_range']}) through 2026-W{evidence['last_week']:02d} "
                f"contradict the HS label {listed}; human review is required. "
                "A reviewed plan hash cannot override this protection."
            )
            if listed == "CLA-JKA-1.009":
                detail += " CLA/CLC identity correction does not authorize deactivation."
            return detail
    for code in codes:
        if code in decisions.HS_PENDING_CONFIRMATION:
            return decisions.HS_PENDING_CONFIRMATION[code]
    return ""


def build_deactivation_plan(organization, *, lock=False):
    """Classify each listed identifier; never inspects another organization."""
    entries = []
    identity_sources = {item["to_code"]: item["from_code"] for item in decisions.IDENTITY_CORRECTIONS}
    queryset = Box.objects.filter(organization=organization)
    if lock:
        queryset = queryset.select_for_update().order_by("global_code")
    by_code = {box.global_code: box for box in queryset}
    for listed in decisions.HS_BOXES:
        resolved = decisions.HS_CODE_CORRECTIONS.get(listed, listed)
        entry = {
            "listed_code": listed,
            "resolved_code": resolved,
            "corrected": resolved != listed,
            "box": None,
            "detail": "",
        }
        if listed in decisions.HS_PENDING_CONFIRMATION:
            entry["status"] = PENDING_CONFIRMATION
            entry["detail"] = decisions.HS_PENDING_CONFIRMATION[listed]
        elif reason := deactivation_block_reason(listed, resolved):
            entry["status"] = V2_CONTRADICTION
            entry["detail"] = reason
        elif resolved in by_code:
            box = by_code[resolved]
            entry["box"] = box
            if box.status == Box.Status.ACTIVE:
                entry["status"] = READY
            elif box.status == Box.Status.INACTIVE:
                entry["status"] = ALREADY_INACTIVE
            else:
                entry["status"] = NOT_ACTIVE
                entry["detail"] = f"status is {box.status}"
            if entry["corrected"] and listed in by_code:
                entry["detail"] = f"the original code {listed} also exists and is left untouched"
        elif identity_sources.get(resolved) in by_code:
            entry["status"] = IDENTITY_CORRECTION_PENDING
            entry["detail"] = "apply the reviewed identity correction first"
        else:
            entry["status"] = NOT_FOUND
        entries.append(entry)
    return entries


def plan_hash(organization, entries):
    body = {
        "organization_id": organization.pk,
        "entries": [(e["listed_code"], e["resolved_code"], e["status"]) for e in entries],
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def summarize(entries):
    summary = {}
    for entry in entries:
        summary.setdefault(entry["status"], []).append(entry["listed_code"])
    return summary


def apply_deactivations(organization, *, actor, expected_plan_hash):
    if not expected_plan_hash:
        raise ValueError("An expected plan hash from a reviewed dry-run is required.")
    role = get_active_measurement_role(user=actor, organization=organization)
    if role != OrganizationMembership.Role.ADMIN:
        raise PermissionDenied("Only an active administrator of the organization can deactivate boxes.")
    with transaction.atomic():
        locked = Organization.objects.select_for_update().get(pk=organization.pk)
        entries = build_deactivation_plan(locked, lock=True)
        if plan_hash(locked, entries) != expected_plan_hash:
            raise ValueError("The database changed since the reviewed dry-run.")
        # Validate every target before any lifecycle call, independently of READY
        # and the reviewed hash, so a misclassified plan cannot bypass the guard.
        for entry in entries:
            if entry["status"] == READY:
                reason = deactivation_block_reason(
                    entry["listed_code"], entry["resolved_code"], entry["box"].global_code
                )
                if reason:
                    raise ValueError(reason)
        done = []
        for entry in entries:
            if entry["status"] == READY:
                deactivate_box(box=entry["box"], user=actor, reason=decisions.HS_REASON)
                done.append(entry["resolved_code"])
        return entries, done
