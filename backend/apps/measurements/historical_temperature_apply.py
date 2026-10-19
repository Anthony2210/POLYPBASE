"""Separately authorized local-UI application service. No automatic entry point.

Offline preparation is in historical_temperature_partial; importing this module
never connects. The public writer refuses SQLite, unexpected settings and targets.
"""

import hashlib
import io
import zipfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box
from apps.organizations.models import Organization

from .historical_temperature_partial import (
    PartialImportError, compare_approval, prepare_approved_partial,
)
from .historical_temperature_source import SOURCES, fingerprint
from .import_planner import authorize_measurement_import
from .models import (
    BiologicalMeasurement, HistoricalTemperatureEvidence,
    HistoricalTemperatureImportReceipt, HistoricalTemperatureReading,
)

APPLY_CONFIRMATION = "APPLY_APPROVED_HISTORICAL_TEMPERATURES_TO_LOCAL_UI"
def local_ui_target():
    """Validate explicit local configuration without opening a connection."""
    from config.temperature_pgqa_backend.runtime_config import load_target

    try:
        return load_target("POLYPBASE_UI_TEMP_DB_")
    except RuntimeError:
        raise PartialImportError("unsafe_or_disabled_target") from None


_EVIDENCE_FIELDS = (
    "source_file", "source_sha256", "sheet", "temperature_cell", "temperature_merged_range",
    "source_value", "source_box", "polyp_cell", "polyp_value", "ephyrae_cell", "ephyrae_value",
    "week_cell", "week_value", "source_iso_year", "source_iso_week",
)
_BOX_FIELDS = ("id", "strain_id", "box_number", "local_code", "global_code", "organization_id", "merged_into_id", "previous_global_code")
_BIOLOGY_FIELDS = ("id", "box_id", "week_start", "measured_on", "polyp_count", "ephyrae_count", "temporal_precision", "duplicate_of_id")


def _validate_target():
    config = connection.settings_dict
    if (not getattr(settings, "HISTORICAL_TEMPERATURE_LOCAL_UI_APPLY_ENABLED", False)
            or settings.SETTINGS_MODULE != "config.historical_temperature_ui_local_settings"
            or connection.vendor != "postgresql"):
        raise PartialImportError("unsafe_or_disabled_target")
    host, port, database, system_identifier, _owner, runtime = local_ui_target()
    if ((config.get("HOST"), str(config.get("PORT")), config.get("NAME")) != (host, port, database)
            or config.get("USER") != runtime
            or config.get("OPTIONS") != {"sslmode": "disable", "connect_timeout": 5}
            or settings.DATABASE_ROUTERS):
        raise PartialImportError("unsafe_or_disabled_target")
    # Only reached after explicit opt-in and exact loopback configuration checks.
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), host(inet_server_addr()), inet_server_port(), current_setting('session_replication_role'), current_setting('transaction_isolation'), current_schema()")
        name, address, port, replication, isolation, schema = cursor.fetchone()
        if (name != database or address != host or port != int(config["PORT"])
                or replication != "origin" or isolation != "read committed" or schema != "public"):
            raise PartialImportError("unexpected_server_identity")
        cursor.execute("SELECT system_identifier::text FROM pg_catalog.pg_control_system()")
        if cursor.fetchone() != (system_identifier,):
            raise PartialImportError("unexpected_server_identity")
        cursor.execute("SELECT session_user::text, current_user::text")
        if cursor.fetchone() != (runtime, runtime):
            raise PartialImportError("unexpected_database_role")
        cursor.execute("SELECT rolsuper OR rolreplication OR rolcreaterole OR rolcreatedb OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
        if cursor.fetchone()[0]:
            raise PartialImportError("unsafe_privileged_database_role")
        cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname IN ('cultures_box', 'measurements_biologicalmeasurement', 'measurements_historicaltemperaturereading', 'measurements_historicaltemperatureevidence', 'measurements_historicaltemperatureimportreceipt') AND pg_has_role(current_user, c.relowner, 'MEMBER'))")
        if cursor.fetchone()[0]:
            raise PartialImportError("unsafe_schema_owner_role")
        cursor.execute("SELECT has_schema_privilege(current_user, 'public', 'CREATE') OR EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname LIKE 'historical_temperature_%' AND pg_has_role(current_user, p.proowner, 'MEMBER'))")
        if cursor.fetchone()[0]:
            raise PartialImportError("unsafe_function_or_schema_privileges")
        cursor.execute("SELECT name FROM django_migrations WHERE app = 'measurements' AND name = '0014_measurement_canonical_evidence'")
        if cursor.fetchone() is None:
            raise PartialImportError("scientific_guards_not_migrated")
        cursor.execute("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgenabled = 'O' AND tgname LIKE 'historical_%'")
        names = {row[0] for row in cursor.fetchall()}
        required = {
            "historical_reading_immutable", "historical_evidence_immutable",
            "historical_reading_no_truncate", "historical_evidence_no_truncate",
            "historical_reading_valid", "historical_evidence_valid", "historical_reading_nonempty",
            "historical_biology_guard", "historical_box_guard",
            "historical_receipt_immutable", "historical_receipt_no_truncate",
        }
        if not required <= names:
            raise PartialImportError("scientific_guards_not_enabled")


def _archive_sources(raw_directory):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, (_year, digest) in SOURCES.items():
            data = (Path(raw_directory) / name).read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise PartialImportError("source_changed_before_acceptance")
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return output.getvalue()


def _current_snapshot(organization_id):
    boxes = list(Box.objects.filter(organization_id=organization_id).order_by("pk").values(*_BOX_FIELDS))
    biology = list(BiologicalMeasurement.objects.filter(box__organization_id=organization_id).order_by("pk").values(*_BIOLOGY_FIELDS))
    for row in biology:
        row["week_start"] = row["week_start"].isoformat()
        row["measured_on"] = row["measured_on"].isoformat()
    return {"boxes": boxes, "biological_measurements": biology}


def _validate_current(snapshot, partial, receipt):
    organization_id = partial["organization_id"]
    current = _current_snapshot(organization_id)
    for table in ("boxes", "biological_measurements"):
        if current[table] != sorted(snapshot[table], key=lambda row: row["id"]):
            raise PartialImportError(f"stale_baseline:{table}")
    readings = list(HistoricalTemperatureReading.objects.filter(
        biological_measurement__box__organization_id=organization_id,
    ).order_by("pk"))
    if receipt is None:
        if readings or snapshot["historical_temperatures"]:
            raise PartialImportError("unexpected_existing_temperature")
        return
    if (receipt.organization_id != organization_id or receipt.accepted_plan != partial
            or receipt.snapshot_sha256 != partial["snapshot_sha256"]):
        raise PartialImportError("receipt_mismatch")
    expected = {p["biological_measurement_id"]: p for p in partial["payloads"]}
    if len(readings) != len(expected):
        raise PartialImportError("replay_reading_set_changed")
    for reading in readings:
        if reading.association_status != HistoricalTemperatureReading.AssociationStatus.CONFIRMED:
            raise PartialImportError("pending_association_requires_review")
        payload = expected.get(reading.biological_measurement_id)
        if payload is None or any(getattr(reading, field) != payload[field] for field in ("iso_year", "iso_week", "temperature_c")):
            raise PartialImportError("replay_temperature_changed")
        evidences = list(reading.evidences.values(*_EVIDENCE_FIELDS))
        if sorted(evidences, key=fingerprint) != sorted(payload["evidences"], key=fingerprint):
            raise PartialImportError("replay_evidence_changed")
    if receipt.results != [{"biological_measurement_id": r.biological_measurement_id, "reading_id": r.pk}
                           for r in sorted(readings, key=lambda row: row.biological_measurement_id)]:
        raise PartialImportError("replay_results_changed")


def _accept_locked(snapshot, partial, archive, organization, actor):
    """Caller owns transaction, authorization and table locks; no partial commit."""
    receipt = HistoricalTemperatureImportReceipt.objects.filter(plan_fingerprint=partial["plan_fingerprint"]).first()
    _validate_current(snapshot, partial, receipt)
    if receipt is not None:
        if bytes(receipt.source_archive) != archive:
            raise PartialImportError("replay_sources_changed")
        return receipt, True
    results = []
    for payload in partial["payloads"]:
        # Resolve every ID through the explicit institution, even after whole-snapshot checks.
        biology = BiologicalMeasurement.objects.get(
            pk=payload["biological_measurement_id"], box__organization=organization,
            duplicate_of__isnull=True, box__merged_into__isnull=True,
        )
        reading = HistoricalTemperatureReading.objects.create(
            biological_measurement=biology, **{k: payload[k] for k in ("iso_year", "iso_week", "temperature_c")},
        )
        for evidence in payload["evidences"]:
            HistoricalTemperatureEvidence.objects.create(reading=reading, **evidence)
        results.append({"biological_measurement_id": biology.pk, "reading_id": reading.pk})
    results.sort(key=lambda row: row["biological_measurement_id"])
    receipt = HistoricalTemperatureImportReceipt.objects.create(
        organization=organization, actor_id=actor.pk, plan_fingerprint=partial["plan_fingerprint"],
        snapshot_sha256=partial["snapshot_sha256"], accepted_plan=partial,
        source_archive=archive, results=results,
    )
    AuditLog.objects.create(
        organization=organization, user=actor, action=AuditLog.Action.IMPORT,
        object_type="historical_temperature_import", object_id=str(receipt.pk),
        description="Approved partial historical temperature import",
        metadata={"receipt_id": receipt.pk, "plan_fingerprint": partial["plan_fingerprint"],
                  "snapshot_sha256": partial["snapshot_sha256"], "policy": partial["policy"],
                  "global_ready": partial["global_ready"], **partial["summary"]},
    )
    return receipt, False


def apply_approved_partial(*, raw_directory, snapshot_bytes, expected_plan_fingerprint,
                           organization_id, actor, confirmation):
    """Explicit future action only; disabled by default and never called by dry-run."""
    if confirmation != APPLY_CONFIRMATION:
        raise PartialImportError("separate_apply_confirmation_required")
    snapshot, partial = prepare_approved_partial(raw_directory, snapshot_bytes)
    compare_approval(partial, expected_plan_fingerprint)
    if type(organization_id) is not int or organization_id != partial["organization_id"]:
        raise PartialImportError("institution_mismatch")
    archive = _archive_sources(raw_directory)
    _validate_target()
    with transaction.atomic():
        # A one-off scientific import deliberately takes a short maintenance window.
        # Table locks exclude phantoms and serialize competing importers as well as
        # direct SQL writes, not merely cooperative writers with Box row locks.
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '10s'")
            cursor.execute("LOCK TABLE cultures_box, measurements_biologicalmeasurement, measurements_historicaltemperaturereading, measurements_historicaltemperatureevidence, measurements_historicaltemperatureimportreceipt IN SHARE ROW EXCLUSIVE MODE")
        _validate_target()
        organization = Organization.objects.select_for_update().get(pk=organization_id)
        if not actor or not actor.pk:
            raise PermissionDenied("An active institution administrator is required.")
        actor = get_user_model().objects.select_for_update().get(pk=actor.pk)
        membership = OrganizationMembership.objects.select_for_update().filter(
            user=actor, organization=organization, is_active=True, role=OrganizationMembership.Role.ADMIN,
        ).first()
        if membership is None:
            raise PermissionDenied("An active institution administrator is required.")
        actor, organization = authorize_measurement_import(actor, organization)
        result = _accept_locked(snapshot, partial, archive, organization, actor)
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        return result
