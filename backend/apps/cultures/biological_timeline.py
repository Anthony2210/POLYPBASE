"""Typed biological history: operations are not editable measurements."""

from .models import SubcultureAllocation


def _author(user, name=None):
    return {"id": user.pk if user else None, "username": name or (user.get_username() if user else None)}


def _allocations(event, organization_id):
    return [
        {"id": row.pk, "position": row.position, "child_box_id": row.child_box_id,
         "child_global_code": row.child_global_code, "allocated_polyps": row.allocated_polyps}
        for row in event.allocations.all()
        if row.child_box.organization_id == organization_id
    ]


def biological_timeline(box, *, context):
    from .serializers import BiologicalMeasurementSerializer

    entries = []
    for measurement in box.biological_measurements.all():
        data = BiologicalMeasurementSerializer(measurement, context=context).data
        entries.append({
            "kind": "measurement", "id": measurement.pk, "identity": f"measurement:{measurement.pk}",
            "author": _author(measurement.user), "timestamp": measurement.created_at.isoformat(),
            "effective_date": measurement.measured_on.isoformat(),
            "polyp_count_before": None, "polyp_count_after": measurement.polyp_count,
            "allocated_polyps": None, "allocations": [], "children": [],
            "can_edit": data["can_edit"], "measurement": data,
            "state_sequence": measurement.polyp_state_sequence,
        })
    events = box.source_subculture_events.select_related("user").prefetch_related("allocations__child_box", "lineages__child_box")
    for event in events:
        allocations = _allocations(event, box.organization_id)
        child_rows = event.allocations.all() if event.occurred_at is not None else event.lineages.all()
        children = [
            {"id": row.child_box_id, "global_code": row.child_box.global_code}
            for row in child_rows if row.child_box.organization_id == box.organization_id
        ]
        entries.append({
            "kind": "subculture", "id": event.pk, "identity": f"subculture:{event.pk}",
            "author": _author(event.user, event.author_name),
            "timestamp": event.occurred_at.isoformat() if event.occurred_at else None,
            "effective_date": event.event_date.isoformat(), "state_sequence": event.parent_state_sequence,
            "polyp_count_before": event.parent_polyp_count_before,
            "polyp_count_after": event.parent_polyp_count_after, "allocated_polyps": event.allocated_polyp_count,
            "parent_state_snapshot": event.parent_state_snapshot,
            "allocations": allocations, "children": children, "can_edit": False,
            "reason": event.reason, "notes": event.notes,
        })
    allocation = SubcultureAllocation.objects.select_related("event", "event__user", "event__parent_box").filter(
        child_box=box, event__parent_box__organization_id=box.organization_id,
                event__occurred_at__isnull=False, allocated_polyps__isnull=False,
    ).first()
    if allocation:
        event = allocation.event
        entries.append({
            "kind": "subculture_initialization", "id": allocation.pk,
            "identity": f"subculture_initialization:{allocation.pk}",
            "event_id": event.pk, "author": _author(event.user, event.author_name),
            "timestamp": event.occurred_at.isoformat(), "effective_date": event.event_date.isoformat(),
            "state_sequence": allocation.child_state_sequence,
            "polyp_count_before": None, "polyp_count_after": allocation.allocated_polyps,
            "allocated_polyps": allocation.allocated_polyps, "allocations": [], "children": [],
            "parent": {"id": event.parent_box_id, "global_code": event.parent_box.global_code},
            "can_edit": False,
        })
    return sorted(entries, key=lambda entry: (
        entry["effective_date"], entry["state_sequence"] or 0, entry["timestamp"] or "", entry["identity"],
    ), reverse=True)
