"""Dry-run and apply service for the reviewed 2026 historical manifest.

The importer never reads Excel. It consumes the immutable manifest, classifies
every row against the *current* database state, and only writes when an
explicit, reviewed plan hash is supplied. Existing science is never
overwritten: the only value change is the single approved LDR correction,
guarded by an exact expected old state.
"""

import hashlib
import json
from collections import Counter
from datetime import date

from django.core.exceptions import PermissionDenied
from django.db import transaction

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.api_views import (
    _changed_values,
    _measurement_audit_values,
    _record_measurement_audit,
)
from apps.cultures.models import Box, IdentificationTag
from . import reviewed_cleanup
from apps.measurements.models import BiologicalMeasurement
from apps.measurements.services import get_active_measurement_role
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

EXACT_ALREADY_PRESENT = "EXACT_ALREADY_PRESENT"
CREATE = "CREATE"
EXPECTED_EXPLICIT_CORRECTION = "EXPECTED_EXPLICIT_CORRECTION"
EXPECTED_TTH_CORRECTION = "EXPECTED_TTH_CORRECTION"
EXPECTED_TEST_DATA_COLLISION = "EXPECTED_TEST_DATA_COLLISION"
CONFLICT = "CONFLICT"
MISSING_BOX = "MISSING_BOX"
IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
EXCLUDED_SOURCE = "EXCLUDED_SOURCE"
CLASSIFICATIONS = (
    EXACT_ALREADY_PRESENT,
    CREATE,
    EXPECTED_EXPLICIT_CORRECTION,
    EXPECTED_TTH_CORRECTION,
    EXPECTED_TEST_DATA_COLLISION,
    CONFLICT,
    MISSING_BOX,
    IDENTITY_MISMATCH,
    EXCLUDED_SOURCE,
)
# Classes that stop an apply: a genuine conflict, a mismatch, or reviewed test
# data that the separate cleanup must remove first.
BLOCKING = (CONFLICT, IDENTITY_MISMATCH, EXPECTED_TEST_DATA_COLLISION)

BOX_PRESENT = "PRESENT"
BOX_IDENTITY_CORRECTION = "IDENTITY_CORRECTION"
BOX_MATERIALIZE = "MATERIALIZE"
BOX_IDENTITY_MISMATCH = "IDENTITY_MISMATCH"

IMPORT_OBJECT_TYPE = "historical_import_2026"


class HistoricalImportError(Exception):
    """The import cannot proceed; nothing has been written."""


class HistoricalImportBlocked(HistoricalImportError):
    """The plan holds conflicts or identity mismatches."""

    def __init__(self, plan):
        super().__init__(
            "Import blocked: "
            f"{plan.counts.get(CONFLICT, 0)} conflict(s), "
            f"{plan.counts.get(IDENTITY_MISMATCH, 0)} identity mismatch(es)."
        )
        self.plan = plan


class Plan:
    """Read-only classification of a manifest against the current database."""

    def __init__(self, manifest, organization):
        self.manifest = manifest
        self.organization = organization
        self.boxes = {}  # code -> dict(state, box, detail, spec)
        self.rows = []  # dicts: item, classification, detail, box
        self.excluded = Counter()
        self.informational = {"species_label_differences": []}

    @property
    def counts(self):
        counts = Counter(row["classification"] for row in self.rows)
        if self.excluded:
            counts[EXCLUDED_SOURCE] = sum(self.excluded.values())
        return dict(counts)

    @property
    def blockers(self):
        blockers = [
            {
                "box": code,
                "iso_week": None,
                "classification": IDENTITY_MISMATCH,
                "detail": state["detail"],
            }
            for code, state in sorted(self.boxes.items())
            if state["state"] == BOX_IDENTITY_MISMATCH
        ]
        blockers += [
            {
                "box": row["item"]["box"],
                "iso_week": row["item"]["iso_week"],
                "classification": row["classification"],
                "detail": row["detail"],
            }
            for row in self.rows
            if row["classification"] in BLOCKING
        ]
        return blockers

    @property
    def has_changes(self):
        classes = {row["classification"] for row in self.rows}
        return bool(
            classes & {CREATE, EXPECTED_EXPLICIT_CORRECTION, EXPECTED_TTH_CORRECTION, MISSING_BOX}
            or any(
                state["state"] in (BOX_IDENTITY_CORRECTION, BOX_MATERIALIZE)
                for state in self.boxes.values()
            )
        )

    def tth_breakdown(self):
        names = {
            CREATE: "TTH_CREATE",
            MISSING_BOX: "TTH_CREATE",
            EXPECTED_TTH_CORRECTION: "TTH_EXPECTED_CORRECTION",
            EXACT_ALREADY_PRESENT: "TTH_ALREADY_CORRECT",
        }
        counts = Counter({"TTH_CREATE": 0, "TTH_EXPECTED_CORRECTION": 0, "TTH_ALREADY_CORRECT": 0, "TTH_CONFLICT": 0})
        for row in self.rows:
            if row["item"]["legacy_reading"] is None:
                continue
            counts[names.get(row["classification"], "TTH_CONFLICT")] += 1
        return dict(counts)

    def box_states(self):
        return dict(sorted(Counter(state["state"] for state in self.boxes.values()).items()))

    def new_measurement_totals(self):
        existing_boxes = sum(1 for row in self.rows if row["classification"] == CREATE)
        new_boxes = sum(1 for row in self.rows if row["classification"] == MISSING_BOX)
        return {
            "on_existing_boxes": existing_boxes,
            "on_newly_materialized_boxes": new_boxes,
            "total": existing_boxes + new_boxes,
        }

    @property
    def plan_hash(self):
        body = {
            "fingerprint": self.manifest["fingerprint"],
            "organization_id": self.organization.pk,
            "boxes": sorted((code, state["state"]) for code, state in self.boxes.items()),
            "rows": [
                (row["item"]["box"], row["item"]["iso_week"], row["classification"])
                for row in self.rows
            ],
            "excluded": dict(sorted(self.excluded.items())),
        }
        payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def report(self):
        return {
            "manifest_fingerprint": self.manifest["fingerprint"],
            "organization": self.organization.name,
            "plan_hash": self.plan_hash,
            "classification_counts": {key: self.counts.get(key, 0) for key in CLASSIFICATIONS},
            "box_states": self.box_states(),
            "new_measurements": self.new_measurement_totals(),
            "boxes_to_materialize": sorted(
                code for code, state in self.boxes.items() if state["state"] == BOX_MATERIALIZE
            ),
            "identity_corrections": sorted(
                code for code, state in self.boxes.items() if state["state"] == BOX_IDENTITY_CORRECTION
            ),
            "tth": self.tth_breakdown(),
            "alias_observations": self.manifest["counts"].get("alias_observations", {}),
            "explicit_corrections": [
                {
                    "box": row["item"]["box"],
                    "iso_week": row["item"]["iso_week"],
                    "from": row["item"]["expected_current"],
                    "to": {"polyps": row["item"]["polyps"], "ephyrae": row["item"]["ephyrae"]},
                }
                for row in self.rows
                if row["classification"] == EXPECTED_EXPLICIT_CORRECTION
            ],
            "excluded_source": dict(sorted(self.excluded.items())),
            "blockers": self.blockers,
            "informational": self.informational,
        }


def _manifest_week_starts(manifest):
    return {
        date.fromisocalendar(item["year"], item["iso_week"], 1)
        for item in manifest["measurements"]
    }


def _plan_materialization(spec, organization, strains_by_code):
    """Return (state, detail) for a Box missing from the database."""
    strain = strains_by_code.get(spec["strain_code"])
    if strain is None:
        return BOX_MATERIALIZE, "new strain"
    if len(strain) > 1:
        return BOX_IDENTITY_MISMATCH, "several strains share this code"
    strain = strain[0]
    if strain.organization_id not in (None, organization.pk):
        return BOX_IDENTITY_MISMATCH, "strain belongs to another organization"
    if strain.organization_id is None and not Box.objects.filter(
        organization=organization, strain=strain
    ).exists():
        return BOX_IDENTITY_MISMATCH, "unowned strain without a box in this organization"
    exception = spec["_exception"]
    if strain.species.scientific_name != spec["species_name"] and not (
        exception and exception["source_label"] == spec["species_name"]
    ):
        return BOX_IDENTITY_MISMATCH, (
            f"strain species {strain.species.scientific_name!r} differs from "
            f"source {spec['species_name']!r}"
        )
    return BOX_MATERIALIZE, "existing strain"


def build_plan(manifest, organization, *, lock=False):
    plan = Plan(manifest, organization)
    corrections = {
        item["to_code"]: item for item in manifest["decisions"]["identity_corrections"]
    }
    exceptions = manifest["decisions"]["strain_label_exceptions"]
    codes = {spec["code"] for spec in manifest["boxes"]}
    codes |= {item["from_code"] for item in corrections.values()}
    queryset = Box.objects.filter(global_code__in=codes).select_related("strain", "strain__species")
    if lock:
        queryset = queryset.select_for_update(of=("self",)).order_by("global_code")
    by_code = {box.global_code: box for box in queryset}

    strains_by_code = {}
    for strain in Strain.objects.filter(
        code__in={spec["strain_code"] for spec in manifest["boxes"]}
    ).select_related("species"):
        strains_by_code.setdefault(strain.code, []).append(strain)

    for spec in manifest["boxes"]:
        code = spec["code"]
        spec = {**spec, "_exception": exceptions.get(spec["strain_code"])}
        existing = by_code.get(code)
        correction = corrections.get(code)
        if existing is not None and correction is not None and correction["from_code"] in by_code:
            plan.boxes[code] = _mismatch(
                spec, f"both {correction['from_code']} and {code} exist: the culture would be duplicated"
            )
            continue
        if existing is not None:
            plan.boxes[code] = _plan_present(plan, spec, existing, organization)
            continue
        source = by_code.get(correction["from_code"]) if correction else None
        if source is not None:
            plan.boxes[code] = _plan_identity_correction(spec, correction, source, organization)
            continue
        state, detail = _plan_materialization(spec, organization, strains_by_code)
        plan.boxes[code] = {"state": state, "box": None, "detail": detail, "spec": spec}

    existing_measurements = {}
    present_boxes = [s["box"] for s in plan.boxes.values() if s["box"] is not None]
    for measurement in BiologicalMeasurement.objects.filter(
        box_id__in=[box.pk for box in present_boxes],
        week_start__in=_manifest_week_starts(manifest),
    ).select_related("box"):
        existing_measurements[(measurement.box_id, measurement.week_start)] = measurement

    for item in manifest["measurements"]:
        state = plan.boxes[item["box"]]
        row = {"item": item, "detail": "", "box": state["box"]}
        row["classification"], row["detail"] = _classify_row(item, state, existing_measurements, organization)
        plan.rows.append(row)
    for item in manifest["excluded"]:
        plan.excluded[item["reason"]] += 1
    return plan


def _plan_present(plan, spec, existing, organization):
    if existing.organization_id != organization.pk:
        return _mismatch(spec, "box belongs to another organization")
    if existing.strain.code != spec["strain_code"]:
        return _mismatch(spec, f"box strain is {existing.strain.code!r}")
    if existing.local_code and existing.local_code != spec["local_code"]:
        return _mismatch(spec, f"local code is {existing.local_code!r}")
    if existing.box_number and existing.box_number != spec["box_number"]:
        return _mismatch(spec, f"box number is {existing.box_number!r}")
    if existing.strain.species.scientific_name != spec["species_name"]:
        plan.informational["species_label_differences"].append(
            {
                "box": spec["code"],
                "database": existing.strain.species.scientific_name,
                "source": spec["species_name"],
            }
        )
    return {"state": BOX_PRESENT, "box": existing, "detail": "", "spec": spec}


def _plan_identity_correction(spec, correction, source, organization):
    if source.organization_id != organization.pk:
        return _mismatch(spec, "identity-correction source belongs to another organization")
    if source.strain.code != correction["from_strain_code"]:
        return _mismatch(spec, f"source box strain is {source.strain.code!r}")
    if source.box_number and source.box_number != spec["box_number"]:
        return _mismatch(spec, f"source box number is {source.box_number!r}")
    return {
        "state": BOX_IDENTITY_CORRECTION,
        "box": source,
        "detail": f"{correction['from_code']} -> {correction['to_code']}",
        "spec": spec,
        "correction": correction,
    }


def _mismatch(spec, detail):
    return {"state": BOX_IDENTITY_MISMATCH, "box": None, "detail": detail, "spec": spec}


def _classify_row(item, state, existing_measurements, organization):
    if state["state"] == BOX_IDENTITY_MISMATCH:
        return IDENTITY_MISMATCH, state["detail"]
    if state["state"] == BOX_MATERIALIZE:
        return MISSING_BOX, "box will be materialized"
    week_start = date.fromisocalendar(item["year"], item["iso_week"], 1)
    existing = existing_measurements.get((state["box"].pk, week_start))
    is_correction = item["operation"] == "EXPLICIT_CORRECTION"
    if existing is None:
        if is_correction:
            return CONFLICT, "expected existing row to correct is missing"
        return CREATE, ""
    spec = reviewed_cleanup.measurement_spec_by_pk().get(existing.pk)
    if spec is not None and reviewed_cleanup.measurement_matches_spec(existing, spec, organization):
        return EXPECTED_TEST_DATA_COLLISION, "reviewed test measurement awaiting the separate cleanup"
    current = (existing.polyp_count, existing.ephyrae_count)
    legacy = item["legacy_reading"]
    if (
        legacy is not None
        and current == (legacy["polyps"], legacy["ephyrae"])
        and current != (item["polyps"], item["ephyrae"])
        and existing.measured_on.isoformat() == item["measured_on"]
        and existing.box.organization_id == organization.pk
    ):
        return EXPECTED_TTH_CORRECTION, "stored value is the label-literal TTH reading"
    # The weekly slot is the identity: the same counts on another day of the
    # same ISO week already satisfy the row, and nothing is moved or rewritten.
    if current == (item["polyps"], item["ephyrae"]):
        return EXACT_ALREADY_PRESENT, (
            "" if existing.measured_on.isoformat() == item["measured_on"]
            else f"same counts, measured on {existing.measured_on}"
        )
    if is_correction and current == (
        item["expected_current"]["polyps"],
        item["expected_current"]["ephyrae"],
    ):
        return EXPECTED_EXPLICIT_CORRECTION, ""
    detail = (
        f"existing {current[0]}/{current[1]} measured on {existing.measured_on} "
        f"differs from {item['polyps']}/{item['ephyrae']}"
    )
    if current == (item["ephyrae"], item["polyps"]) and any(
        applied["code"] == "ANTHONY_TTH_LABEL_INVERSION" for applied in item["corrections"]
    ):
        detail += " (existing row equals the uncorrected label-literal TTH reading)"
    return CONFLICT, detail


# -- apply ------------------------------------------------------------------


def _require_admin(actor, organization):
    role = get_active_measurement_role(user=actor, organization=organization)
    if role != OrganizationMembership.Role.ADMIN:
        raise PermissionDenied("Only an active administrator of the organization can apply this import.")


def _audit(organization, actor, action, object_type, object_id, description, metadata):
    return AuditLog.objects.create(
        organization=organization,
        user=actor,
        action=action,
        object_type=object_type,
        object_id=object_id,
        description=description,
        metadata=metadata,
    )


def _species_and_strain(spec, organization, *, species_name=None, species_code=None):
    """Get or create the operational Species and the Strain for a box spec."""
    strain = Strain.objects.select_for_update().filter(code=spec["strain_code"]).select_related("species").first()
    created = {"species": None, "strain": None}
    if strain is not None:
        return strain, created
    name = species_name or spec["species_name"]
    species = Species.objects.filter(scientific_name=name).first()
    if species is None:
        species = Species.objects.create(
            scientific_name=name,
            genus_species_code=(species_code or spec["species_aaa"])[:12],
        )
        created["species"] = species
    strain = Strain.objects.create(
        species=species,
        code=spec["strain_code"],
        organization=organization,
        number=spec["strain_number"],
        origin_code=spec["origin_code"][:12],
    )
    created["strain"] = strain
    return strain, created


def _apply_identity_correction(plan, state, actor, fingerprint):
    spec, correction, box = state["spec"], state["correction"], state["box"]
    organization = plan.organization
    strain, created = _species_and_strain(
        spec,
        organization,
        species_name=correction["to_species_name"],
        species_code=correction["to_species_code"],
    )
    if strain.organization_id not in (None, organization.pk):
        raise HistoricalImportError(f"Strain {strain.code} belongs to another organization.")
    if strain.species.scientific_name != correction["to_species_name"]:
        raise HistoricalImportError(
            f"Strain {strain.code} is not the expected {correction['to_species_name']!r} reference."
        )
    for kind in ("species", "strain"):
        if created[kind] is not None:
            _audit(
                organization, actor, AuditLog.Action.CREATION, kind, str(created[kind].pk),
                f"{kind.capitalize()} created: "
                f"{getattr(created[kind], 'scientific_name', None) or created[kind].code}",
                {"source": IMPORT_OBJECT_TYPE, "manifest_fingerprint": fingerprint},
            )
    old_code, old_local = box.global_code, box.local_code
    box.strain = strain
    box.global_code = correction["to_code"]
    box.local_code = spec["local_code"]
    box.save(update_fields=["strain", "global_code", "local_code"])
    # Keep the box history reachable under the corrected code, as the existing
    # normalize_box_codes operation does for its code corrections.
    audit_rows = AuditLog.objects.filter(object_type="box", object_id=old_code).update(
        object_id=box.global_code
    )
    tags = 0
    old_tag = IdentificationTag.objects.filter(code=f"QR-{old_code}").first()
    if old_tag is not None and not IdentificationTag.objects.filter(code=f"QR-{box.global_code}").exists():
        old_tag.code = f"QR-{box.global_code}"
        old_tag.save(update_fields=["code"])
        tags = 1
    _audit(
        organization, actor, AuditLog.Action.UPDATE, "box", box.global_code,
        f"Box identity corrected: {old_code} -> {box.global_code}",
        {
            "box_id": box.pk,
            "source": IMPORT_OBJECT_TYPE,
            "manifest_fingerprint": fingerprint,
            "reason": correction["reason"],
            "previous_global_code": old_code,
            "previous_local_code": old_local,
            "previous_strain_code": correction["from_strain_code"],
            "new_strain_code": strain.code,
            "audit_rows_remapped": audit_rows,
            "tags_remapped": tags,
        },
    )
    return box


def _materialize_box(plan, state, actor, fingerprint):
    spec, organization = state["spec"], plan.organization
    strain, created = _species_and_strain(spec, organization)
    exception = spec["_exception"]
    if strain.species.scientific_name != spec["species_name"] and not (
        exception and exception["source_label"] == spec["species_name"]
    ):
        raise HistoricalImportError(f"Strain {strain.code} species changed during the import.")
    if strain.organization_id not in (None, organization.pk):
        raise HistoricalImportError(f"Strain {strain.code} belongs to another organization.")
    for kind in ("species", "strain"):
        if created[kind] is not None:
            _audit(
                organization, actor, AuditLog.Action.CREATION, kind, str(created[kind].pk),
                f"{kind.capitalize()} created: "
                f"{getattr(created[kind], 'scientific_name', None) or created[kind].code}",
                {"source": IMPORT_OBJECT_TYPE, "manifest_fingerprint": fingerprint},
            )
    # Historical convention: pending_review, no location, no invented dates.
    box = Box.objects.create(
        organization=organization,
        global_code=spec["code"],
        local_code=spec["local_code"],
        box_number=spec["box_number"],
        strain=strain,
        status=Box.Status.PENDING_REVIEW,
    )
    _audit(
        organization, actor, AuditLog.Action.CREATION, "box", box.global_code,
        f"Box created from historical import: {box.global_code}",
        {
            "box_id": box.pk,
            "source": IMPORT_OBJECT_TYPE,
            "manifest_fingerprint": fingerprint,
            "status": box.status,
            "strain_code": strain.code,
            "species": strain.species.scientific_name,
            "source_sheet": spec["sheet"],
            "source_row": spec["first_source_row"],
            "source_identifier": spec["source_identifier"],
            "identifier_correction": spec["identifier_correction"],
        },
    )
    return box


def _apply_measurement_correction(row, box, actor, fingerprint, organization):
    """Guarded value correction: only the exact expected old state is replaced."""
    item = row["item"]
    if row["classification"] == EXPECTED_TTH_CORRECTION:
        expected, reason = item["legacy_reading"], "ANTHONY_TTH_LABEL_INVERSION"
    else:
        expected, reason = item["expected_current"], item["corrections"][-1]["code"]
    week_start = date.fromisocalendar(item["year"], item["iso_week"], 1)
    measurement = BiologicalMeasurement.objects.select_for_update().get(box=box, week_start=week_start)
    if (
        box.organization_id != organization.pk
        or box.global_code != item["box"]
        or measurement.measured_on.isoformat() != item["measured_on"]
        or (measurement.polyp_count, measurement.ephyrae_count) != (expected["polyps"], expected["ephyrae"])
    ):
        raise HistoricalImportError(
            f"{item['box']} W{item['iso_week']} is no longer the expected "
            f"{expected['polyps']}/{expected['ephyrae']} state."
        )
    before = _measurement_audit_values(measurement)
    measurement.polyp_count = item["polyps"]
    measurement.ephyrae_count = item["ephyrae"]
    measurement.save(update_fields=["polyp_count", "ephyrae_count"])
    after = _measurement_audit_values(measurement)
    _record_measurement_audit(
        box=box,
        measurement=measurement,
        user=actor,
        action=AuditLog.Action.UPDATE,
        metadata={
            "measurement_id": measurement.pk,
            "before": before,
            "after": after,
            "valeurs": after,
            "modifications": _changed_values(before, after),
            "source": IMPORT_OBJECT_TYPE,
            "manifest_fingerprint": fingerprint,
            "reason": reason,
            "source_cells": [item["polyp_cell"], item["ephyrae_cell"]],
        },
    )


def apply_import(manifest, organization, *, actor, expected_plan_hash):
    """Apply the manifest atomically; return (plan, receipt or None)."""
    if not expected_plan_hash:
        raise HistoricalImportError("An expected plan hash from a reviewed dry-run is required.")
    _require_admin(actor, organization)
    fingerprint = manifest["fingerprint"]
    with transaction.atomic():
        locked_org = Organization.objects.select_for_update().get(pk=organization.pk)
        plan = build_plan(manifest, locked_org, lock=True)
        if plan.blockers:
            raise HistoricalImportBlocked(plan)
        if plan.plan_hash != expected_plan_hash:
            raise HistoricalImportError(
                "The database changed since the reviewed dry-run "
                f"(plan {plan.plan_hash} != {expected_plan_hash})."
            )
        if not plan.has_changes:
            return plan, None

        boxes = {}
        for code in sorted(plan.boxes):
            state = plan.boxes[code]
            if state["state"] == BOX_IDENTITY_CORRECTION:
                boxes[code] = _apply_identity_correction(plan, state, actor, fingerprint)
            elif state["state"] == BOX_MATERIALIZE:
                boxes[code] = _materialize_box(plan, state, actor, fingerprint)
            else:
                boxes[code] = state["box"]

        applied = Counter()
        for row in sorted(plan.rows, key=lambda r: (r["item"]["box"], r["item"]["iso_week"])):
            item, box = row["item"], boxes[row["item"]["box"]]
            if row["classification"] in (EXPECTED_EXPLICIT_CORRECTION, EXPECTED_TTH_CORRECTION):
                _apply_measurement_correction(row, box, actor, fingerprint, locked_org)
                applied[row["classification"]] += 1
            elif row["classification"] in (CREATE, MISSING_BOX):
                BiologicalMeasurement(
                    box=box,
                    measured_on=date.fromisoformat(item["measured_on"]),
                    polyp_count=item["polyps"],
                    ephyrae_count=item["ephyrae"],
                    strobila_count=None,
                    user=None,
                ).save()
                applied[CREATE] += 1

        receipt = _audit(
            locked_org, actor, AuditLog.Action.IMPORT, IMPORT_OBJECT_TYPE, fingerprint,
            f"Historical 2026 import applied for {locked_org.name}",
            {
                "manifest_fingerprint": fingerprint,
                "source_sha256": manifest["source"]["sha256"],
                "plan_hash": plan.plan_hash,
                "classification_counts": plan.report()["classification_counts"],
                "measurements_created": applied[CREATE],
                "measurements_corrected": applied[EXPECTED_EXPLICIT_CORRECTION],
                "tth_measurements_corrected": applied[EXPECTED_TTH_CORRECTION],
                "boxes_materialized": plan.report()["boxes_to_materialize"],
                "identity_corrections": plan.report()["identity_corrections"],
                "excluded_source": dict(sorted(plan.excluded.items())),
            },
        )
        return plan, receipt
