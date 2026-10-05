"""Guarded removal of the already-reviewed test data, separate from the import.

Only the exact objects listed below can ever be removed, and only while every
identifying field and relation still matches the reviewed state. The relation
closure is recomputed under lock before deletion; any extra dependent object,
organization mismatch or changed field aborts the whole operation.
"""

import hashlib
import json
from datetime import date

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from datetime import timezone as dt_timezone

from apps.audit.models import AuditLog
from apps.cultures.models import Box, BoxLineage, BoxLocation, SubcultureEvent
from apps.measurements.models import BiologicalMeasurement
from apps.measurements.services import get_active_measurement_role
from apps.accounts.models import OrganizationMembership
from apps.organizations.models import Organization

RECEIPT_OBJECT_TYPE = "reviewed_test_data_cleanup"


def _m(pk, box, week_start, measured_on, polyps, ephyrae, *, salinity, user_id, created_at, notes=""):
    """One reviewed test measurement and every stored field that guards its deletion.

    The values are the stored state recorded for each row in the reviewed
    2026-09-30 database snapshot. Unreviewed internals (polyp_state_sequence,
    row ordering) are intentionally not guarded.
    """
    return {
        "pk": pk,
        "box": box,
        "week_start": week_start,
        "measured_on": measured_on,
        "polyps": polyps,
        "ephyrae": ephyrae,
        # NULL is "not measured" and never equals 0: strobila 0 is the reviewed value.
        "strobila_count": 0,
        "salinity_psu": salinity,
        "culture_status": "not_specified",
        "needs_attention": False,
        "notes": notes,
        "user_id": user_id,
        "created_at": created_at,
    }


APPROVED_TEST_MEASUREMENTS = (
    _m(100163, "AAL-FGU-1.001", "2026-08-17", "2026-08-23", 6, 4,
       salinity="61.00", user_id=6, created_at="2026-08-23T18:34:13.118844+00:00"),
    _m(100164, "AAL-FGU-1.001", "2026-08-24", "2026-08-24", 100, 25,
       salinity="61.00", user_id=6, created_at="2026-08-24T11:16:39.760368+00:00"),
    _m(100165, "ALA-JKA-1.009", "2026-08-24", "2026-08-25", 87, 25,
       salinity="5.00", user_id=6, created_at="2026-08-25T21:00:58.525843+00:00", notes="test"),
    _m(100166, "NBR-JKA-1.006", "2026-08-24", "2026-08-25", 189, 89,
       salinity=None, user_id=6, created_at="2026-08-25T21:01:35.074408+00:00", notes="test"),
    _m(100167, "PCA-ABE-2.004", "2026-08-24", "2026-08-25", 100, 78,
       salinity="6.00", user_id=6, created_at="2026-08-25T21:01:59.971107+00:00", notes="test"),
    _m(100168, "AAL-FGU-1.002", "2026-08-24", "2026-08-26", 11, 250,
       salinity="33.00", user_id=9, created_at="2026-08-26T04:18:27.250553+00:00"),
    _m(100169, "ALA-JKA-1.009", "2026-09-14", "2026-09-15", 0, 0,
       salinity="33.00", user_id=10, created_at="2026-09-15T07:02:05.424595+00:00"),
    _m(100170, "AAL-FGU-1.001", "2026-09-14", "2026-09-18", 55, 55,
       salinity="61.00", user_id=6, created_at="2026-09-18T09:47:26.081885+00:00"),
    _m(100171, "AAL-FGU-1.001", "2026-09-21", "2026-09-23", 23, 4,
       salinity="61.00", user_id=6, created_at="2026-09-23T06:30:54.651245+00:00", notes="test"),
)

TEST_BOX = {
    "pk": 2312,
    "global_code": "AAU-NBE-1.003",
    "box_number": "003",
    "strain_code": "AAU-NBE-1",
    "parent_code": "AAU-NBE-1.001",
}
TEST_LOCATION = {
    "pk": 5992,
    "notes": "Initial location after subculture.",
    "start_date": "2026-09-19",
}
TEST_LINEAGE_PK = 4
TEST_EVENT = {"pk": 4, "event_date": "2026-09-19"}
# Reviewed AuditLog candidates. A listed row that exists must be tied to a
# removed object; a listed row that is absent is simply already gone.
REVIEWED_AUDIT_IDS = (
    706, 719, 733, 735, 737, 745, 1349, 1369, 1373, 1381, 1382, 1386, 1387, 1388, 1416, 1417,
)

_BOX_ALLOWED = {"locations": {5992}, "parent_lineages": {4}}


def measurement_spec_by_pk():
    return {item["pk"]: item for item in APPROVED_TEST_MEASUREMENTS}


def _same(expected, actual):
    """Strict equality where NULL never equals 0 and bool never equals int."""
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(expected, bool) != isinstance(actual, bool):
        return False
    return expected == actual


def measurement_mismatches(measurement, spec, organization):
    """Return [(field, expected, actual)] for every guarded field that differs."""
    actual_values = {
        "pk": measurement.pk,
        "organization_id": measurement.box.organization_id,
        "box": measurement.box.global_code,
        "week_start": measurement.week_start.isoformat(),
        "measured_on": measurement.measured_on.isoformat(),
        "polyps": measurement.polyp_count,
        "ephyrae": measurement.ephyrae_count,
        "strobila_count": measurement.strobila_count,
        "salinity_psu": None if measurement.salinity_psu is None else str(measurement.salinity_psu),
        "culture_status": measurement.culture_status,
        "needs_attention": measurement.needs_attention,
        "notes": measurement.notes,
        "user_id": measurement.user_id,
        "created_at": measurement.created_at.astimezone(dt_timezone.utc).isoformat(),
    }
    expected_values = {**{k: v for k, v in spec.items()}, "organization_id": organization.pk}
    return [
        (field, expected_values[field], actual)
        for field, actual in actual_values.items()
        if not _same(expected_values[field], actual)
    ]


def measurement_matches_spec(measurement, spec, organization):
    """True only when every guarded field equals the reviewed snapshot."""
    return not measurement_mismatches(measurement, spec, organization)


class CleanupPlan:
    def __init__(self, organization):
        self.organization = organization
        self.states = {}  # label -> "PRESENT" | "ABSENT"
        self.blockers = []
        self.objects = {}

    def block(self, label, detail):
        self.blockers.append({"target": label, "detail": detail})

    @property
    def plan_hash(self):
        body = {"organization_id": self.organization.pk, "states": sorted(self.states.items())}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()

    def report(self):
        present = sorted(label for label, state in self.states.items() if state == "PRESENT")
        absent = sorted(label for label, state in self.states.items() if state == "ABSENT")
        return {
            "plan_hash": self.plan_hash,
            "present": present,
            "absent": absent,
            "blockers": self.blockers,
            "dependency_closure": self.objects.get("closure", {}),
        }


def _check_measurements(plan, organization, lock):
    queryset = BiologicalMeasurement.objects.filter(
        pk__in=[item["pk"] for item in APPROVED_TEST_MEASUREMENTS]
    ).select_related("box")
    if lock:
        queryset = queryset.select_for_update(of=("self",)).order_by("pk")
    found = {m.pk: m for m in queryset}
    plan.objects["measurements"] = []
    for spec in APPROVED_TEST_MEASUREMENTS:
        label = f"measurement:{spec['pk']}"
        measurement = found.get(spec["pk"])
        if measurement is None:
            plan.states[label] = "ABSENT"
            continue
        plan.states[label] = "PRESENT"
        mismatches = measurement_mismatches(measurement, spec, organization)
        if mismatches:
            plan.block(
                label,
                "stored measurement differs from the reviewed snapshot: "
                + "; ".join(f"{field} expected {expected!r}, found {actual!r}" for field, expected, actual in mismatches),
            )
            continue
        plan.objects["measurements"].append(measurement)


def _related_pks(instance, accessor):
    related = getattr(instance, accessor)
    return {obj.pk for obj in related.all()}


def _check_island(plan, organization, lock):
    box_qs = Box.objects.select_related("strain", "organization").filter(pk=TEST_BOX["pk"])
    if lock:
        box_qs = box_qs.select_for_update(of=("self",))
    box = box_qs.first()
    lineage = BoxLineage.objects.select_related("parent_box", "child_box").filter(pk=TEST_LINEAGE_PK).first()
    event = SubcultureEvent.objects.select_related("parent_box").filter(pk=TEST_EVENT["pk"]).first()
    location = BoxLocation.objects.filter(pk=TEST_LOCATION["pk"]).first()
    for label, pk, obj in (
        ("box", TEST_BOX["pk"], box),
        ("lineage", TEST_LINEAGE_PK, lineage),
        ("event", TEST_EVENT["pk"], event),
        ("location", TEST_LOCATION["pk"], location),
    ):
        plan.states[f"{label}:{pk}"] = "PRESENT" if obj is not None else "ABSENT"
    plan.objects["island"] = {}
    if box is None:
        for label, obj in (("lineage", lineage), ("event", event), ("location", location)):
            if obj is not None:
                plan.block(label, "island member exists although the reviewed test Box is gone")
        return
    if (
        box.organization_id != organization.pk
        or box.global_code != TEST_BOX["global_code"]
        or box.box_number != TEST_BOX["box_number"]
        or box.strain.code != TEST_BOX["strain_code"]
    ):
        plan.block("box:2312", "test Box identity or organization differs")
        return
    closure = {}
    for relation in Box._meta.related_objects:
        accessor = relation.get_accessor_name()
        value = getattr(box, accessor, None)
        if value is None:
            continue
        pks = _related_pks(box, accessor) if hasattr(value, "all") else {value.pk}
        if pks:
            closure[accessor] = sorted(pks)
        if pks - _BOX_ALLOWED.get(accessor, set()):
            plan.block(f"box:2312/{accessor}", f"unexpected dependent objects {sorted(pks - _BOX_ALLOWED.get(accessor, set()))}")
    plan.objects["closure"] = closure
    if location is None or location.box_id != box.pk or location.notes != TEST_LOCATION["notes"] or location.ends_at is not None or (
        timezone.localtime(location.starts_at).date().isoformat() != TEST_LOCATION["start_date"]
    ):
        plan.block("location:5992", "location differs from the reviewed state")
    if (
        lineage is None
        or lineage.child_box_id != box.pk
        or lineage.parent_box.global_code != TEST_BOX["parent_code"]
        or lineage.parent_box.organization_id != organization.pk
        or lineage.subculture_event_id != TEST_EVENT["pk"]
        or lineage.relationship_type != BoxLineage.RelationshipType.SUBCULTURE
    ):
        plan.block("lineage:4", "lineage differs from the reviewed state")
    if (
        event is None
        or event.parent_box_id != (lineage.parent_box_id if lineage else None)
        or event.occurred_at is not None
        or event.event_date.isoformat() != TEST_EVENT["event_date"]
    ):
        plan.block("event:4", "subculture event differs from the reviewed state")
    elif set(event.lineages.values_list("pk", flat=True)) != {TEST_LINEAGE_PK}:
        plan.block("event:4", "the subculture event has another child")
    else:
        for relation in SubcultureEvent._meta.related_objects:
            accessor = relation.get_accessor_name()
            if accessor == "lineages":
                continue
            value = getattr(event, accessor, None)
            if value is not None and hasattr(value, "all") and value.exists():
                plan.block("event:4", f"unexpected dependent objects through {accessor}")
    plan.objects["island"] = {"box": box, "lineage": lineage, "event": event, "location": location}


def _audit_links_to_removed_objects(audit, measurement_pks):
    metadata = audit.metadata or {}
    return (
        metadata.get("measurement_id") in measurement_pks
        or metadata.get("box_id") == TEST_BOX["pk"]
        or TEST_BOX["pk"] in (metadata.get("child_box_ids") or [])
        or metadata.get("subculture_event_id") == TEST_EVENT["pk"]
        or (audit.object_type == "box" and audit.object_id == TEST_BOX["global_code"])
    )


def _check_audits(plan, organization, lock):
    queryset = AuditLog.objects.filter(pk__in=REVIEWED_AUDIT_IDS)
    if lock:
        queryset = queryset.select_for_update().order_by("pk")
    found = {a.pk: a for a in queryset}
    measurement_pks = {item["pk"] for item in APPROVED_TEST_MEASUREMENTS}
    plan.objects["audits"] = []
    for pk in REVIEWED_AUDIT_IDS:
        label = f"audit:{pk}"
        audit = found.get(pk)
        if audit is None:
            plan.states[label] = "ABSENT"
            continue
        plan.states[label] = "PRESENT"
        if audit.organization_id != organization.pk or not _audit_links_to_removed_objects(audit, measurement_pks):
            plan.block(label, "audit entry is not tied to the reviewed test objects")
            continue
        plan.objects["audits"].append(audit)
    # Closure: no other entry may reference the removed objects.
    codes = {item["box"] for item in APPROVED_TEST_MEASUREMENTS} | {
        TEST_BOX["global_code"],
        TEST_BOX["parent_code"],
    }
    for audit in AuditLog.objects.filter(organization=organization, object_type="box", object_id__in=codes):
        if audit.pk in REVIEWED_AUDIT_IDS:
            continue
        metadata = audit.metadata or {}
        if (
            metadata.get("measurement_id") in measurement_pks
            or metadata.get("box_id") == TEST_BOX["pk"]
            or TEST_BOX["pk"] in (metadata.get("child_box_ids") or [])
            or metadata.get("subculture_event_id") == TEST_EVENT["pk"]
            or audit.object_id == TEST_BOX["global_code"]
        ):
            plan.block(f"audit:{audit.pk}", "unlisted audit entry references the reviewed test objects")


def build_cleanup_plan(organization, *, lock=False):
    plan = CleanupPlan(organization)
    _check_measurements(plan, organization, lock)
    _check_island(plan, organization, lock)
    _check_audits(plan, organization, lock)
    return plan


def apply_cleanup(organization, *, actor, expected_plan_hash):
    if not expected_plan_hash:
        raise ValueError("An expected plan hash from a reviewed dry-run is required.")
    role = get_active_measurement_role(user=actor, organization=organization)
    if role != OrganizationMembership.Role.ADMIN:
        raise PermissionDenied("Only an active administrator of the organization can run this cleanup.")
    with transaction.atomic():
        locked = Organization.objects.select_for_update().get(pk=organization.pk)
        # Deterministic lock order: boxes by pk first, then rows.
        box_pks = sorted(
            set(
                Box.objects.filter(
                    global_code__in={i["box"] for i in APPROVED_TEST_MEASUREMENTS}
                    | {TEST_BOX["global_code"], TEST_BOX["parent_code"]}
                ).values_list("pk", flat=True)
            )
        )
        list(Box.objects.select_for_update().filter(pk__in=box_pks).order_by("pk"))
        plan = build_cleanup_plan(locked, lock=True)
        if plan.blockers:
            raise CleanupBlocked(plan)
        if plan.plan_hash != expected_plan_hash:
            raise ValueError("The database changed since the reviewed dry-run.")
        if not any(state == "PRESENT" for state in plan.states.values()):
            return plan, None

        snapshot = _snapshot(plan)
        affected_boxes = {m.box_id for m in plan.objects["measurements"]}
        for measurement in plan.objects["measurements"]:
            measurement.delete()
        # Removing a reading changes the resolved state: invalidate stale intents.
        Box.objects.filter(pk__in=affected_boxes).update(polyp_state_revision=F("polyp_state_revision") + 1)
        island = plan.objects["island"]
        if island.get("lineage") is not None:
            island["lineage"].delete()
        if island.get("event") is not None:
            island["event"].delete()
        if island.get("box") is not None:
            island["box"].delete()  # cascades only the reviewed location
        for audit in plan.objects["audits"]:
            audit.delete()
        receipt = AuditLog.objects.create(
            organization=locked,
            user=actor,
            action=AuditLog.Action.UPDATE,
            object_type=RECEIPT_OBJECT_TYPE,
            object_id=plan.plan_hash,
            description=f"Reviewed test data removed for {locked.name}",
            metadata={"plan_hash": plan.plan_hash, "removed": snapshot},
        )
        return plan, receipt


class CleanupBlocked(Exception):
    def __init__(self, plan):
        super().__init__(f"Cleanup blocked by {len(plan.blockers)} unexpected state(s).")
        self.plan = plan


def _snapshot(plan):
    island = plan.objects["island"]

    def jsonable(value):
        return value.isoformat() if isinstance(value, date) or hasattr(value, "isoformat") else value

    return {
        "measurements": [
            {
                "pk": m.pk,
                "box": m.box.global_code,
                "measured_on": m.measured_on.isoformat(),
                "polyps": m.polyp_count,
                "ephyrae": m.ephyrae_count,
                "strobila": m.strobila_count,
                "user_id": m.user_id,
                "created_at": jsonable(m.created_at),
            }
            for m in plan.objects["measurements"]
        ],
        "box": (
            {"pk": island["box"].pk, "global_code": island["box"].global_code, "status": island["box"].status}
            if island.get("box")
            else None
        ),
        "lineage": island["lineage"].pk if island.get("lineage") else None,
        "event": island["event"].pk if island.get("event") else None,
        "location": island["location"].pk if island.get("location") else None,
        "audits": [
            {
                "pk": a.pk,
                "action": a.action,
                "object_type": a.object_type,
                "object_id": a.object_id,
                "description": a.description,
                "metadata": a.metadata,
                "created_at": jsonable(a.created_at),
            }
            for a in plan.objects["audits"]
        ],
    }
