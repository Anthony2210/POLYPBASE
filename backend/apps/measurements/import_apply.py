"""Atomic exact-date CSV acceptance, independent of HTTP and file storage."""

from dataclasses import asdict
from datetime import date
from decimal import Decimal
import json
import hashlib

from django.db import IntegrityError, transaction

from apps.audit.models import AuditLog
from apps.cultures.models import Box

from .canonical import PAYLOAD_FIELDS
from .csv_import import read_csv
from .import_planner import (
    _baseline, authorize_measurement_import, build_measurement_import_plan,
)
from .models import BiologicalMeasurement, MeasurementImportReceipt
from .services import _measurement_audit_values


class MeasurementImportApplyError(Exception):
    """A machine-coded rejection; no source or scientific writes survive."""

    def __init__(self, code):
        self.code = self.error_code = code
        super().__init__(code)


def _receipt(preparation, organization):
    return MeasurementImportReceipt.objects.filter(
        organization=organization, source_sha256=preparation.source_sha256,
        preparation_fingerprint=preparation.fingerprint,
    ).first()


def _replay(receipt, preparation, expected_plan_fingerprint, organization, actor):
    # The original reviewed guard remains valid after acceptance, even if live
    # measurements have subsequently changed. A fresh preview may also replay.
    accepted = dict(receipt.accepted_plan, importing_actor_id=actor.pk)
    actor_plan_fingerprint = hashlib.sha256(json.dumps(
        accepted, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    if expected_plan_fingerprint not in {receipt.plan_fingerprint, actor_plan_fingerprint}:
        current = build_measurement_import_plan(preparation, organization=organization, actor=actor)
        if expected_plan_fingerprint != current.fingerprint:
            raise MeasurementImportApplyError("stale_plan")
    return receipt, True


def apply_measurement_import(
    source, mapping, delimiter, expected_preparation_fingerprint,
    expected_plan_fingerprint, organization, actor,
):
    """Accept confirmed bytes and interpretation once, or return their receipt.

    The transport must obtain explicit mapping confirmation before calling this
    function. Only CREATE, ALREADY_MATCHES and IGNORED may be accepted together.
    Replays never recreate deleted/corrected measurements or emit new audits.
    Source bytes live only in the private receipt table, in the same database
    transaction as the measurements and mandatory audits.
    """
    actor, organization = authorize_measurement_import(actor, organization)
    preparation = read_csv(
        source, source_id="diagnostic-upload", mapping=mapping,
        confirmed=True, delimiter=delimiter,
    )
    if preparation.fingerprint != expected_preparation_fingerprint:
        raise MeasurementImportApplyError("stale_preparation")
    try:
        with transaction.atomic():
            receipt = _receipt(preparation, organization)
            if receipt is not None:
                actor, organization = authorize_measurement_import(actor, organization)
                return _replay(receipt, preparation, expected_plan_fingerprint, organization, actor)

            # Lock all resolved candidates before planning, including already
            # matching slots. Stable ordering is shared with manual Box writes;
            # the unique weekly constraint remains the final database guard.
            references = sorted({
                value.value for record in preparation.records if record.row is not None
                for value in record.row.values if value.field == "box_reference"
            })
            boxes = list(Box.objects.select_for_update().filter(
                organization=organization, global_code__in=references, merged_into__isnull=True,
            ).order_by("pk"))
            actor, organization = authorize_measurement_import(actor, organization)
            # Another application may have committed while we waited for Box locks.
            receipt = _receipt(preparation, organization)
            if receipt is not None:
                return _replay(receipt, preparation, expected_plan_fingerprint, organization, actor)
            plan = build_measurement_import_plan(preparation, organization=organization, actor=actor)
            if plan.fingerprint != expected_plan_fingerprint:
                raise MeasurementImportApplyError("stale_plan")
            if not plan.ready or any(
                row.outcome not in {"CREATE", "ALREADY_MATCHES", "IGNORED"} for row in plan.rows
            ):
                raise MeasurementImportApplyError("plan_not_applicable")
            locked_ids = {box.pk for box in boxes}
            if any(row.box and row.box.id not in locked_ids for row in plan.rows):
                # A previously unknown reference appeared after lock resolution.
                raise MeasurementImportApplyError("stale_plan")

            receipt = MeasurementImportReceipt(
                organization=organization, actor_id=actor.pk,
                source_sha256=preparation.source_sha256, source_bytes=source,
                preparation_fingerprint=preparation.fingerprint,
                plan_fingerprint=plan.fingerprint,
                preparation=json.loads(preparation.canonical_json()),
                accepted_plan=json.loads(plan.canonical_json()),
            )
            results = []
            audit_ids = []
            for row in plan.rows:
                measurement = None
                audit_id = None
                if row.outcome == "CREATE":
                    time = row.source.row.time
                    if time.precision != "exact_date" or time.measured_on is None:
                        raise MeasurementImportApplyError("plan_not_applicable")
                    supplied = {value.field: value for value in row.source.row.values}
                    # Unknowns are NULL, never the interactive defaults. The exact
                    # absent/blank/null distinction remains in canonical evidence.
                    values = {
                        field: supplied[field].value if supplied[field].state == "value" else None
                        for field in PAYLOAD_FIELDS
                    }
                    if values["salinity_psu"] is not None:
                        values["salinity_psu"] = Decimal(values["salinity_psu"])
                    measurement = BiologicalMeasurement.objects.create(
                        box_id=row.box.id, measured_on=date.fromisoformat(time.measured_on),
                        temporal_precision=BiologicalMeasurement.TemporalPrecision.EXACT_DATE,
                        observer_status=BiologicalMeasurement.ObserverStatus.NOT_RECORDED,
                        original_observer=None, user=actor, imported_by=actor,
                        origin=BiologicalMeasurement.Origin.FILE_IMPORT,
                        import_format=BiologicalMeasurement.ImportFormat.CSV, **values,
                    )
                    audit = AuditLog.objects.create(
                        organization=organization, user=actor, action=AuditLog.Action.ENTRY,
                        object_type="box", object_id=row.box.global_code,
                        description="Biological measurement accepted from CSV",
                        metadata={
                            "receipt_id": str(receipt.pk), "measurement_id": measurement.pk,
                            "source_record": row.source.provenance.coordinate.record_number,
                            "measurement": asdict(_baseline(measurement)),
                            "valeurs": _measurement_audit_values(measurement),
                        },
                    )
                    audit_id = audit.pk
                    audit_ids.append(audit_id)
                elif row.outcome == "ALREADY_MATCHES":
                    measurement = BiologicalMeasurement.objects.canonical().get(
                        pk=row.existing.id, box__organization=organization,
                    )
                results.append({
                    "record_number": row.source.provenance.coordinate.record_number,
                    "outcome": row.outcome,
                    "measurement_id": measurement.pk if measurement else None,
                    "measurement": asdict(_baseline(measurement)) if measurement else None,
                    "audit_id": audit_id,
                })
            acceptance_audit = AuditLog.objects.create(
                organization=organization, user=actor, action=AuditLog.Action.IMPORT,
                object_type="measurement_import", object_id=str(receipt.pk),
                description="CSV measurement import accepted",
                metadata={
                    "receipt_id": str(receipt.pk), "source_sha256": receipt.source_sha256,
                    "preparation_fingerprint": receipt.preparation_fingerprint,
                    "plan_fingerprint": receipt.plan_fingerprint, "summary": asdict(plan.summary),
                },
            )
            audit_ids.append(acceptance_audit.pk)
            receipt.result = json.loads(json.dumps({
                "rows": results, "summary": asdict(plan.summary), "audit_ids": audit_ids,
                "acceptance_audit_id": acceptance_audit.pk,
            }))
            receipt.save()
            return receipt, False
    except IntegrityError:
        # Covers concurrent blank-only acceptances too: they have no Box to lock.
        # A unique receipt is evidence of success; any other storage failure must
        # propagate, after the entire failed scientific transaction rolled back.
        actor, organization = authorize_measurement_import(actor, organization)
        receipt = _receipt(preparation, organization)
        if receipt is not None:
            return _replay(receipt, preparation, expected_plan_fingerprint, organization, actor)
        raise
