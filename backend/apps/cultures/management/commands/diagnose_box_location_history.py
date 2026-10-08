"""Read-only location history diagnostic; never performs corrective actions."""

import json
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.cultures.location_history_diagnostics import (
    IN_ORGANIZATION,
    MISSING,
    MALFORMED,
    UNRESOLVED_SCOPE,
    diagnose_box_location_history,
)
from apps.organizations.models import Organization


RULE = "=" * 78
SUBRULE = "-" * 78


class Command(BaseCommand):
    help = (
        "Read-only diagnostic of the persisted location history (BoxLocation, "
        "BoxMovement, related AuditLog) of explicitly named Boxes in one explicit "
        "organization. Boxes are matched by exact global_code. Nothing is written, "
        "repaired or inferred."
    )

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument(
            "--global-code",
            action="append",
            required=True,
            dest="global_codes",
            help="Exact Box global_code; repeat the option for several Boxes.",
        )
        parser.add_argument("--format", choices=("text", "json"), default="text")

    def handle(self, *args, **options):
        organization_id = options["organization_id"]
        try:
            organization = Organization.objects.only("id", "name", "is_active").get(pk=organization_id)
        except Organization.DoesNotExist as exc:
            raise CommandError(f"Organization {organization_id} does not exist.") from exc
        global_codes = options["global_codes"]
        if any(not code for code in global_codes):
            raise CommandError("Empty global codes are not accepted.")

        report = diagnose_box_location_history(organization=organization, global_codes=global_codes)
        if options["format"] == "json":
            self.stdout.write(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
        else:
            for line in render_text(report):
                self.stdout.write(line)

        if report["unresolved_global_codes"]:
            raise CommandError(
                "Some global codes were not found in this organization: "
                + ", ".join(report["unresolved_global_codes"]),
                returncode=1,
            )


def _local(value):
    if value is None:
        return ""
    local = timezone.localtime(datetime.fromisoformat(value))
    return f" (local {local:%Y-%m-%d %H:%M:%S %Z})"


def _zone(ref, *, short=False):
    if ref is None:
        return "none"
    if ref["reference_status"] == IN_ORGANIZATION and short:
        return f"#{ref['id']} \"{ref['name']}\""
    if ref["reference_status"] == IN_ORGANIZATION:
        target = ref["current_target_temperature_c"]
        target_text = "not set" if target is None else f"{target} C"
        activity = "active" if ref["is_active"] else "inactive"
        return f"#{ref['id']} \"{ref['name']}\" ({activity}; current target {target_text})"
    if ref["reference_status"] == MISSING:
        return f"#{ref['id']} MISSING (referenced zone does not exist)"
    if ref["reference_status"] == MALFORMED:
        return "MALFORMED REFERENCE (zone scope unresolved; withheld)"
    return "OUTSIDE REQUESTED ORGANIZATION (withheld)"


def _actor(ref):
    if ref is None:
        return "not recorded"
    if ref.get("reference_status") == UNRESOLVED_SCOPE:
        return "actor reference recorded (identity withheld; organization relationship not established)"
    return f"user #{ref['id']} {ref['label'] or '(no readable identity)'}"


def _duration(seconds):
    sign = "-" if seconds < 0 else ""
    days, rest = divmod(abs(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{sign}{days}d {hours:02d}:{minutes:02d}:{secs:02d}"


def _ids(values):
    return ", ".join(f"#{value}" for value in values) if values else "none"


def render_text(report):
    organization = report["organization"]
    yield "BOX LOCATION HISTORY DIAGNOSTIC (read-only; nothing is written or repaired)"
    yield f"Organization: {organization['name']} (id={organization['id']}, active={organization['is_active']})"
    yield "Requested global codes (exact match in this organization): " + ", ".join(report["requested_global_codes"])
    yield "Timestamps: persisted UTC values; local time is shown for reading only. Temperatures in C."
    yield (
        "Database safeguard: single read-only PostgreSQL snapshot."
        if report["database_read_only_transaction"]
        else "Database safeguard: ORM reads only; no database-level read-only snapshot on this run."
    )
    yield (
        "Legend: PERSISTED MOVEMENT = a BoxMovement row. PERIOD BOUNDARY = start or end of a "
        "BoxLocation row, derived from periods and never a movement. A coincidence requires the "
        "same instant and zone."
    )
    for box in report["boxes"]:
        yield from _render_box(box)
    yield RULE
    yield "UNRESOLVED GLOBAL CODES"
    if report["unresolved_global_codes"]:
        for code in report["unresolved_global_codes"]:
            yield f"  - {code}: no Box with this exact global_code in this organization"
    else:
        yield "  none"


def _render_box(box):
    state = box["current_state"]
    yield RULE
    yield f"BOX {state['global_code']} (id={state['id']})"
    yield SUBRULE
    yield "CURRENT STATE"
    yield f"  status: {state['status']}"
    yield f"  organization: {state['organization']['name']} (id={state['organization']['id']})"
    yield f"  local code: {state['local_code'] or '(empty)'}; box number: {state['box_number']}"
    yield f"  current zone pointer (Box.thermal_zone): {_zone(state['thermal_zone'])}"
    yield f"  open periods: {_ids(state['open_period_ids'])}"
    yield (
        f"  entered_on: {state['entered_on'] or 'not recorded'}; created_on: {state['created_on']}; "
        f"deactivated_on: {state['deactivated_on'] or 'not recorded'}"
    )

    yield "LOCATION PERIODS (BoxLocation rows, by start then id; repeated stays kept separate)"
    if not box["location_periods"]:
        yield "  none persisted"
    for index, period in enumerate(box["location_periods"], start=1):
        yield f"  [{index}] BoxLocation #{period['id']} - {period['state']}"
        yield f"      zone: {_zone(period['thermal_zone'])}"
        yield f"      starts_at: {period['starts_at']}{_local(period['starts_at'])}"
        if period["state"] == "OPEN":
            yield "      ends_at: none (open)"
        elif period["end_date_unknown"]:
            yield "      ends_at: unknown (end_date_unknown=true)"
        else:
            yield f"      ends_at: {period['ends_at']}{_local(period['ends_at'])}"
            yield f"      duration: {_duration(period['duration_seconds'])}"
        if period["notes"]:
            yield f"      notes: {period['notes']!r}"

    yield "PERIOD BOUNDARIES (derived from BoxLocation rows; not movements)"
    if not box["period_boundaries"]:
        yield "  none"
    for boundary in box["period_boundaries"]:
        coincidence = (
            "coincides with persisted BoxMovement " + _ids(boundary["coinciding_movement_ids"])
            if boundary["coinciding_movement_ids"]
            else "no coinciding persisted BoxMovement"
        )
        yield (
            f"  {boundary['at']} {boundary['kind']} of BoxLocation #{boundary['period_id']} "
            f"in {_zone(boundary['thermal_zone'], short=True)}: {coincidence}"
        )

    yield "PERSISTED MOVEMENTS (BoxMovement rows, by moved_at then id)"
    if not box["persisted_movements"]:
        yield "  none persisted (this does not prove that no physical move happened)"
    for movement in box["persisted_movements"]:
        yield f"  BoxMovement #{movement['id']}"
        yield f"      moved_at: {movement['moved_at']}{_local(movement['moved_at'])}"
        yield f"      from: {_zone(movement['from_thermal_zone'])}"
        yield f"      to: {_zone(movement['to_thermal_zone'])}"
        yield f"      recorded actor: {_actor(movement['recorded_actor'])}"
        if movement["notes"]:
            yield f"      notes: {movement['notes']!r}"
        yield f"      coinciding period start: {_ids(movement['coinciding_period_start_ids'])}"
        yield f"      coinciding period end: {_ids(movement['coinciding_period_end_ids'])}"
        yield f"      linked audit: {_ids(movement['linked_audit_ids'])}"

    yield "AUDIT EVIDENCE (AuditLog of this organization, location-related, by created_at then id)"
    if not box["audit_evidence"]:
        yield "  none found"
    for audit in box["audit_evidence"]:
        yield (
            f"  AuditLog #{audit['id']} {audit['created_at']} action={audit['action']} "
            f"via {audit['association']}; actor: {_actor(audit['recorded_actor'])}"
        )
        if audit["description_withheld"]:
            yield "      description: withheld (reference scope or metadata could not be validated)"
        else:
            yield f"      description: {audit['description']!r}"
        if audit["malformed_metadata_fields"]:
            yield "      malformed metadata fields: " + ", ".join(audit["malformed_metadata_fields"])
        if audit.get("parent_scope_validated") is False:
            yield "      candidate subculture evidence: parent scope could not be validated; details withheld"
        for key in ("transition", "recorded_moved_at", "occurred_at"):
            if key in audit:
                yield f"      {key}: {audit[key]}"
        if "movement_id" in audit:
            status = "persisted for this Box" if audit["movement_is_persisted_for_box"] else "NOT persisted for this Box"
            yield f"      movement_id: {audit['movement_id']} ({status})"
        for key in ("from_thermal_zone", "to_thermal_zone", "before_thermal_zone", "after_thermal_zone"):
            if key in audit:
                yield f"      {key}: {_zone(audit[key])}"
        for key in ("closed_location_ids", "legacy_closed_location_ids"):
            if key in audit:
                yield f"      {key}: {_ids(audit[key])}"
    if box["other_audit_counts"]:
        counts = ", ".join(f"{action}={count}" for action, count in box["other_audit_counts"].items())
        yield f"  other audit entries for this Box (not location-related, not shown): {counts}"
    if box["audits_excluded_box_id_mismatch"]:
        yield (
            f"  audit entries excluded because their box_id differs from this Box: "
            f"{box['audits_excluded_box_id_mismatch']}"
        )

    yield "DETECTED INCONSISTENCIES"
    yield from _findings(box["inconsistencies"])
    yield "FACTUAL OBSERVATIONS (not errors)"
    yield from _findings(box["observations"])
    yield "FACTS THAT CANNOT BE DETERMINED"
    for fact in box["undetermined"]:
        yield f"  - {fact}"


def _findings(findings):
    if not findings:
        yield "  none"
    for item in findings:
        yield f"  [{item['code']}] {item['detail']}"
