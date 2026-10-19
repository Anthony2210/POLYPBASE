"""Synthetic load evidence for the parent's explicitly guarded disposable PG18 QA.

Importing this module does not configure Django, connect, or read any source file.
The parent must provide config.historical_temperature_pgqa.assert_disposable,
runtime_role and cleanup_test_data. There is deliberately no standalone launcher.
run_load returns a JSON-safe result, including on ordinary failures; callers must
check status == 'passed'. No result grants permission to import scientific data.
"""

import hashlib
import io
import os
import re
import sys
import tracemalloc
import zipfile
from contextlib import ExitStack
from datetime import date, timedelta
from importlib import import_module
from time import perf_counter


_TABLES = (
    "cultures_box", "measurements_biologicalmeasurement",
    "measurements_historicaltemperaturereading",
    "measurements_historicaltemperatureevidence",
    "measurements_historicaltemperatureimportreceipt",
)
_LOCK_SQL = "LOCK TABLE " + ", ".join(_TABLES) + " IN SHARE ROW EXCLUSIVE MODE"
_GUARDS = {
    "historical_reading_immutable", "historical_evidence_immutable",
    "historical_reading_no_truncate", "historical_evidence_no_truncate",
    "historical_reading_valid", "historical_evidence_valid", "historical_reading_nonempty",
    "historical_biology_guard", "historical_box_guard",
    "historical_receipt_immutable", "historical_receipt_no_truncate",
}


class _LoadError(RuntimeError):
    pass


def _require(condition, code):
    if not condition:
        raise _LoadError(code)


def _safe_failure(exc, stage):
    # Driver error text, SQL, connection settings and exception repr can contain
    # credentials. Only fixed local codes, class names and SQLSTATE leave here.
    cause = getattr(exc, "__cause__", None)
    state = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
    return {
        "stage": stage, "exception_type": type(exc).__name__,
        "code": str(exc) if isinstance(exc, _LoadError) else "load_failed",
        "sqlstate": state if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state) else None,
    }


def _guard(connection, assert_disposable):
    from django.conf import settings

    config = connection.settings_dict
    from config.temperature_pgqa_backend.runtime_config import load_target

    host, port, database, _system_identifier, _owner, _runtime = load_target()
    _require(
        connection.vendor == "postgresql"
        and (config.get("HOST"), str(config.get("PORT")), config.get("NAME")) == (host, port, database)
        and not getattr(settings, "HISTORICAL_TEMPERATURE_LOCAL_UI_APPLY_ENABLED", False),
        "unexpected_or_enabled_target",
    )
    assert_disposable(connection)


def _check_guards(connection):
    import_module("config.historical_temperature_pgqa").verify_schema(connection)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT t.tgname, t.tgenabled, t.tgdeferrable, t.tginitdeferred "
            "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE NOT t.tgisinternal AND n.nspname='public' "
            "AND c.relname = ANY(%s) AND t.tgname = ANY(%s)",
            [list(_TABLES), sorted(_GUARDS)],
        )
        rows = {name: (enabled, deferrable, deferred) for name, enabled, deferrable, deferred in cursor.fetchall()}
    _require(set(rows) == _GUARDS and all(row[0] == "O" for row in rows.values()), "scientific_guards_missing")
    _require(rows["historical_reading_nonempty"][1:] == (True, True), "nonempty_guard_not_deferred")


def _check_runtime_login(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_backend_pid(), current_user = session_user, "
            "(SELECT rolsuper OR rolreplication OR rolcreaterole OR rolbypassrls "
            "FROM pg_roles WHERE rolname=current_user), "
            "has_schema_privilege(current_user, 'public', 'CREATE'), "
            "EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname = ANY(%s) "
            "AND pg_has_role(current_user, c.relowner, 'MEMBER')), "
            "EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND p.proname LIKE 'historical_temperature_%%' "
            "AND pg_has_role(current_user, p.proowner, 'MEMBER'))",
            [list(_TABLES)],
        )
        pid, actual_login, privileged, schema_create, table_owner, function_owner = cursor.fetchone()
    _require(actual_login and not any((privileged, schema_create, table_owner, function_owner)),
             "runtime_must_be_unprivileged_actual_login")
    return pid


def _peak_rss():
    """Process lifetime peak, not an allocation delta or a PostgreSQL measurement."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return {"bytes": None, "source": "unavailable"}
            return {"bytes": int(counters.PeakWorkingSetSize), "source": "windows_peak_working_set"}
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {"bytes": int(value if sys.platform == "darwin" else value * 1024), "source": "getrusage_process_peak"}
    except (ImportError, OSError, AttributeError):
        return {"bytes": None, "source": "unavailable"}


def _prepare_prerequisites(reading_count):
    from django.contrib.auth import get_user_model
    from django.db import transaction

    from apps.accounts.models import OrganizationMembership
    from apps.cultures.models import Box
    from apps.organizations.models import Organization
    from apps.taxonomy.models import Species, Strain

    from .models import BiologicalMeasurement

    with transaction.atomic():
        organization = Organization.objects.create(name="Synthetic PGQA temperature load")
        actor = get_user_model().objects.create_user(username="synthetic-pgqa-load", email="synthetic-pgqa-load@example.org")
        OrganizationMembership.objects.create(
            user=actor, organization=organization, role=OrganizationMembership.Role.ADMIN,
        )
        species = Species.objects.create(scientific_name="Synthetic PGQA load species", genus_species_code="QAA")
        biology = []
        box_count = (reading_count + 51) // 52
        strain = None
        for index in range(box_count):
            strain_code = f"QAA-QAT-{index // 999 + 1}"
            if index % 999 == 0:
                strain = Strain.objects.create(
                    species=species, organization=organization, code=strain_code,
                )
            weeks = min(52, reading_count - index * 52)
            number = f"{index % 999 + 1:03d}"
            # Normal Box.save registers the namespace. Only prerequisites use a
            # bulk insert, never historical readings, evidence, receipts or audit.
            box = Box.objects.create(
                organization=organization, strain=strain, box_number=number,
                global_code=f"{strain_code}.{number}", status=Box.Status.PENDING_REVIEW,
                polyp_state_revision=weeks,
            )
            for week in range(weeks):
                monday = date(2024, 1, 1) + timedelta(weeks=week)
                biology.append(BiologicalMeasurement(
                    box=box, measured_on=monday, week_start=monday,
                    polyp_count=0, ephyrae_count=0, strobila_count=None,
                    polyp_state_sequence=week + 1,
                ))
        # Fresh synthetic boxes have no attribution, movements or prior state.
        # Explicit Mondays, sequence and Box revision reproduce save's derived
        # fields without 40k prerequisite save transactions. SQL constraints stay on.
        BiologicalMeasurement.objects.bulk_create(biology, batch_size=1000)
    return organization, actor, box_count


def _candidate(box_code, iso_year, iso_week, occurrence, source_sha256):
    from .historical_temperature_source import fingerprint

    row = 3 + occurrence * 3
    evidence = {
        "source_file": "synthetic-pgqa-load.json", "source_sha256": source_sha256,
        "sheet": "Synthetic only", "temperature_cell": f"C{row}",
        "temperature_merged_range": f"C{row}:C{row + 1}", "source_value": "0.000",
        "source_box": box_code, "polyp_cell": f"F{row}", "polyp_value": "0",
        "ephyrae_cell": f"F{row + 1}", "ephyrae_value": "0",
        "week_cell": f"F{row - 1}", "week_value": str(iso_week),
        "source_iso_year": iso_year, "source_iso_week": iso_week,
    }
    return {
        "candidate_id": fingerprint(evidence), "iso_year": iso_year, "iso_week": iso_week,
        "source_box": box_code, "canonical_box": box_code, "temperature_c": "0.000",
        "biological_state": "complete", "polyps": 0, "ephyrae": 0, "evidence": evidence,
    }


def _build_partial(snapshot, reading_count, evidence_count):
    from .historical_temperature_partial import select_partial, snapshot_baseline
    from .historical_temperature_plan import plan
    from .historical_temperature_source import canonical_json, fingerprint

    source_bytes = canonical_json({
        "synthetic_only": True, "reading_count": reading_count, "evidence_count": evidence_count,
        "recipe": "2024 ISO Mondays, 52 weeks per box, all values zero, unique three-row cell blocks",
    }).encode("utf-8")
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    boxes = {box["id"]: box["global_code"] for box in snapshot["boxes"]}
    candidates = []
    biology = snapshot["biological_measurements"]
    _require(len(biology) == reading_count, "synthetic_baseline_count_mismatch")
    for occurrence in range(evidence_count):
        # First one per reading, then extra distinct cell pairs supporting the
        # first readings. The default has exactly one two-evidence reading.
        measurement = biology[occurrence % reading_count]
        year, week, _day = date.fromisoformat(measurement["week_start"]).isocalendar()
        candidates.append(_candidate(boxes[measurement["box_id"]], year, week, occurrence, source_sha256))
    baseline = snapshot_baseline(snapshot)
    association_plan = plan(candidates, organization_id=baseline["organization_id"], baseline=baseline)
    report = {
        "synthetic_only": True, "organization_id": baseline["organization_id"],
        "association_plan": association_plan, "baseline_fingerprint": fingerprint(baseline),
        "summary": {"ready": association_plan["summary"]["ready"]},
        "source_extraction": {"exclusions": [], "header_issues": []},
    }
    report["report_fingerprint"] = fingerprint(report)
    partial = select_partial(report)
    partial["snapshot_sha256"] = fingerprint(snapshot)
    partial["plan_fingerprint"] = fingerprint({k: v for k, v in partial.items() if k != "plan_fingerprint"})
    _require(partial["summary"]["accepted_readings"] == reading_count, "synthetic_plan_reading_mismatch")
    _require(partial["summary"]["accepted_evidences"] == evidence_count, "synthetic_plan_evidence_mismatch")
    _require(not partial["excluded"], "synthetic_plan_has_exclusions")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr("synthetic-pgqa-load.json", source_bytes)
    return partial, archive.getvalue()


_CATEGORIES = (
    ('INSERT INTO "measurements_historicaltemperaturereading"', "reading_insert"),
    ('INSERT INTO "measurements_historicaltemperatureevidence"', "evidence_insert"),
    ('INSERT INTO "measurements_historicaltemperatureimportreceipt"', "receipt_insert"),
    ('INSERT INTO "audit_auditlog"', "audit_insert"),
)


def _progress(message, started):
    print(f"[load {perf_counter() - started:8.1f}s] {message}", flush=True)


def _check_deadline(deadline, code="max_duration_exceeded"):
    if deadline is not None and perf_counter() > deadline:
        raise _LoadError(code)


class _StatementTimer:
    """Time ORM statements by category, report progress and enforce the deadline.

    Raising inside the wrapped transaction makes atomic() roll everything back.
    """

    def __init__(self, deadline=None, started=None, reading_total=0, progress_every=5000):
        self.count = 0
        self.seconds = 0.0
        self.max_seconds = 0.0
        self.deadline, self.started = deadline, started
        self.reading_total, self.progress_every = reading_total, progress_every
        self.categories = {name: [0, 0.0] for _prefix, name in _CATEGORIES}
        self.categories["other"] = [0, 0.0]

    def __call__(self, execute, sql, params, many, context):
        _check_deadline(self.deadline)
        name = "other"
        if sql.startswith("INSERT"):
            name = next((label for prefix, label in _CATEGORIES if sql.startswith(prefix)), "other")
        start = perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            elapsed = perf_counter() - start
            self.count += 1
            self.seconds += elapsed
            self.max_seconds = max(self.max_seconds, elapsed)
            entry = self.categories[name]
            entry[0] += 1
            entry[1] += elapsed
            if name == "reading_insert" and self.started is not None and entry[0] % self.progress_every == 0:
                _progress(f"readings inserted {entry[0]}/{self.reading_total}, "
                          f"evidences {self.categories['evidence_insert'][0]}, statements {self.count}", self.started)

    def metrics(self):
        return {
            "statement_count": self.count, "client_execute_seconds": self.seconds,
            "by_category": {name: {"statements": n, "seconds": sec} for name, (n, sec) in self.categories.items()},
            "max_client_execute_seconds": self.max_seconds,
            "scope": "default connection transaction statements; excludes driver COMMIT and copied sessions",
            "trigger_time_isolated": False,
            "interpretation": "Driver wall time includes server work and triggers, not isolated SQL or trigger CPU time.",
        }


def _observe_locks(observer, pid, *, expect_held=True):
    start = perf_counter()
    with observer.cursor() as cursor:
        cursor.execute(
            "SELECT c.relname, l.mode, l.granted FROM pg_locks l "
            "JOIN pg_class c ON c.oid=l.relation JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE l.pid=%s AND l.locktype='relation' AND n.nspname='public' "
            "AND c.relname = ANY(%s) ORDER BY c.relname, l.mode",
            [pid, list(_TABLES)],
        )
        locks = [{"table": table, "mode": mode, "granted": granted} for table, mode, granted in cursor.fetchall()]
    held = {row["table"] for row in locks if row["granted"] and row["mode"] == "ShareRowExclusiveLock"}
    _require(held == (set(_TABLES) if expect_held else set()), "unexpected_acceptance_table_locks")
    return {"locks": locks, "observation_seconds": perf_counter() - start}


def _probe_writer(writer, box_id, *, expect_blocked):
    from django.db import DatabaseError

    with writer.cursor() as cursor:
        cursor.execute("SET lock_timeout = '250ms'" if expect_blocked else "SET lock_timeout = '5s'")
        cursor.execute("SET statement_timeout = '5s'")
        start = perf_counter()
        try:
            # A real UPDATE takes RowExclusiveLock even though the value is
            # unchanged. Never change identity/counts or invalidate associations.
            cursor.execute("UPDATE cultures_box SET notes=notes WHERE id=%s", [box_id])
        except DatabaseError as exc:
            state = _safe_failure(exc, "writer_probe")["sqlstate"]
            if not expect_blocked or state != "55P03":
                raise
            return {"outcome": "lock_timeout", "sqlstate": state, "seconds": perf_counter() - start}
        _require(not expect_blocked and cursor.rowcount == 1, "writer_probe_did_not_block_or_find_box")
        return {"outcome": "committed_noop_update", "seconds": perf_counter() - start, "rows": cursor.rowcount}


def _verify(snapshot, partial, archive, receipt_id, organization, actor, reading_count, evidence_count):
    from django.db.models import Count

    from apps.audit.models import AuditLog

    from .historical_temperature_apply import _EVIDENCE_FIELDS, _current_snapshot
    from .historical_temperature_source import fingerprint
    from .models import (
        BiologicalMeasurement,
        HistoricalTemperatureEvidence,
        HistoricalTemperatureImportReceipt,
        HistoricalTemperatureReading,
    )

    readings = HistoricalTemperatureReading.objects.filter(biological_measurement__box__organization=organization)
    evidences = HistoricalTemperatureEvidence.objects.filter(reading__biological_measurement__box__organization=organization)
    counts = {
        "readings": readings.count(), "evidences": evidences.count(),
        "confirmed_readings": readings.filter(association_status="confirmed").count(),
        "zero_temperature_readings": readings.filter(temperature_c="0.000").count(),
        "zero_source_evidences": evidences.filter(source_value="0.000", polyp_value="0", ephyrae_value="0").count(),
        "zero_biological_measurements": BiologicalMeasurement.objects.filter(
            box__organization=organization, polyp_count=0, ephyrae_count=0,
        ).count(),
        "receipts": HistoricalTemperatureImportReceipt.objects.filter(organization=organization).count(),
        "audits": AuditLog.objects.filter(organization=organization, object_type="historical_temperature_import").count(),
        "readings_without_evidence": readings.annotate(evidence_count=Count("evidences")).filter(evidence_count=0).count(),
    }
    _require(counts == {
        "readings": reading_count, "evidences": evidence_count, "confirmed_readings": reading_count,
        "zero_temperature_readings": reading_count, "zero_source_evidences": evidence_count,
        "zero_biological_measurements": reading_count, "receipts": 1, "audits": 1,
        "readings_without_evidence": 0,
    }, "postcommit_counts_or_zero_mismatch")
    _require(_current_snapshot(organization.pk) == {k: snapshot[k] for k in ("boxes", "biological_measurements")},
             "postcommit_baseline_changed")
    receipt = HistoricalTemperatureImportReceipt.objects.get(pk=receipt_id, organization=organization)
    _require(receipt.actor_id == actor.pk and receipt.accepted_plan == partial
             and receipt.plan_fingerprint == partial["plan_fingerprint"]
             and receipt.snapshot_sha256 == partial["snapshot_sha256"] and bytes(receipt.source_archive) == archive,
             "postcommit_receipt_mismatch")
    actual_readings = list(readings.order_by("biological_measurement_id").values(
        "id", "biological_measurement_id", "iso_year", "iso_week", "temperature_c",
    ))
    expected = {p["biological_measurement_id"]: p for p in partial["payloads"]}
    _require(all(all(row[key] == expected[row["biological_measurement_id"]][key]
                     for key in ("iso_year", "iso_week", "temperature_c")) for row in actual_readings),
             "postcommit_reading_payload_mismatch")
    _require(receipt.results == [{"biological_measurement_id": row["biological_measurement_id"], "reading_id": row["id"]}
                                 for row in actual_readings], "postcommit_receipt_results_mismatch")
    expected_evidence = sorted((p["biological_measurement_id"], fingerprint(e))
                               for p in partial["payloads"] for e in p["evidences"])
    actual_evidence = []
    for row in evidences.values("reading__biological_measurement_id", *_EVIDENCE_FIELDS).iterator(chunk_size=1000):
        biology_id = row.pop("reading__biological_measurement_id")
        actual_evidence.append((biology_id, fingerprint(row)))
    _require(sorted(actual_evidence) == expected_evidence, "postcommit_evidence_payload_mismatch")
    return counts


def run_load(reading_count=40291, evidence_count=None, *, max_seconds=1800, trace_memory=False):
    """Measure one synthetic acceptance, commit, locks and postcommit validation.

    evidence_count defaults to reading_count + 1 (one two-evidence reading).
    max_seconds is a fail-safe: phase boundaries and every ORM statement of the
    acceptance transaction check it, so overrun rolls the transaction back and
    still runs cleanup. trace_memory enables tracemalloc, which slows Python
    work several times and is off by default. KeyboardInterrupt is recorded and
    returned as a failed result after cleanup is attempted.

    All writes are deferred until the parent's exact disposable-target guard
    succeeds. Owner cleanup runs before setup and in finally, after runtime_role
    has restored the owner and all independent sessions have closed. No database
    exception text is returned. A failed cleanup makes the whole result failed.
    """
    result = {
        "status": "failed", "synthetic_only": True, "committed": False,
        "requested": {}, "timings_seconds": {}, "cleanup": {"status": "not_started"},
        "replay": {"status": "not_run", "reason": "Exactly one _accept_locked call is measured."},
        "limitations": [
            "Synthetic distribution is not a historical snapshot or authorization for a real import.",
            "Wall times include tracemalloc, statement timer and bounded lock-probe overhead.",
            "Client statement timing includes ORM-issued SQL and triggers; trigger-only timing is not measured.",
            "Peak RSS is a process-lifetime high-water mark, includes earlier tests and excludes PostgreSQL memory.",
            "No automatic readiness threshold or production performance extrapolation is applied.",
        ],
    }
    started = perf_counter()
    stage = "arguments"
    deadline = started + max_seconds if max_seconds else None
    cleanup_context = None
    owns_trace = False
    timer = None
    transaction_started = None
    try:
        if evidence_count is None:
            evidence_count = reading_count + 1
        _require(type(reading_count) is int and reading_count > 0, "invalid_reading_count")
        _require(type(evidence_count) is int and evidence_count >= reading_count, "invalid_evidence_count")
        result["requested"] = {"readings": reading_count, "evidences": evidence_count}
        stage = "preflight"
        from django.db import connection, transaction

        from .historical_temperature_apply import _accept_locked, _current_snapshot

        harness = import_module("config.historical_temperature_pgqa")
        assert_disposable = harness.assert_disposable
        cleanup_test_data = harness.cleanup_test_data
        runtime_role = harness.runtime_role
        _guard(connection, assert_disposable)
        _require(not connection.in_atomic_block and connection.get_autocommit(), "top_level_autocommit_required")
        _check_guards(connection)
        owns_trace = trace_memory and not tracemalloc.is_tracing()
        if owns_trace:
            tracemalloc.start()
        result["memory"] = {
            "rss_before": _peak_rss(),
            "tracemalloc_scope": (
                "after preflight through final cleanup" if owns_trace
                else "existing shared tracer including earlier allocations" if tracemalloc.is_tracing()
                else "disabled (default); RSS high-water marks only"),
        }
        _progress(f"preflight passed; {reading_count} readings, {evidence_count} evidences, max {max_seconds}s", started)
        stage = "initial_cleanup"
        # Never erase pre-existing rows merely because the database name matches.
        harness.require_run_lease()
        harness.assert_empty_business_data(connection)
        cleanup_context = (connection, assert_disposable, cleanup_test_data)
        start = perf_counter()
        cleanup_test_data()
        _guard(connection, assert_disposable)
        _check_guards(connection)
        result["timings_seconds"]["initial_cleanup"] = perf_counter() - start
        stage = "preparation"
        _check_deadline(deadline)
        start = perf_counter()
        organization, actor, box_count = _prepare_prerequisites(reading_count)
        result["timings_seconds"]["fixture_preparation"] = perf_counter() - start
        _progress(f"fixtures ready: {box_count} boxes, {reading_count} biological rows", started)
        _check_deadline(deadline)
        stage = "baseline_construction"
        start = perf_counter()
        snapshot = {
            **_current_snapshot(organization.pk), "historical_temperatures": [],
            "identity": {"organization_id": organization.pk}, "synthetic_only": True,
        }
        result["timings_seconds"]["baseline_construction"] = perf_counter() - start
        _progress("baseline snapshot built", started)
        _check_deadline(deadline)
        stage = "plan_construction"
        start = perf_counter()
        partial, archive = _build_partial(snapshot, reading_count, evidence_count)
        result["timings_seconds"]["plan_construction"] = perf_counter() - start
        _progress("plan built", started)
        _check_deadline(deadline)
        result["dataset"] = {
            "boxes": box_count, "weeks_per_box_max": 52, "iso_year": 2024,
            "all_temperatures_and_biological_counts_zero": True,
            "plan_fingerprint": partial["plan_fingerprint"], "snapshot_sha256": partial["snapshot_sha256"],
            "archive_bytes": len(archive),
        }
        stage = "runtime_setup"
        with runtime_role():
            _guard(connection, assert_disposable)
            _check_guards(connection)
            pid = _check_runtime_login(connection)
            result["runtime_actual_login"] = True
            result["debug_query_logging_enabled"] = connection.queries_logged
            with ExitStack() as sessions:
                # Copy only inside runtime_role: neither copied session inherits
                # owner credentials. Never register aliases in global settings.
                observer = connection.copy(alias="load_observer")
                sessions.callback(observer.close)
                _guard(observer, assert_disposable)
                with observer.cursor() as cursor:
                    cursor.execute("SET default_transaction_read_only = on")
                    cursor.execute("SET statement_timeout = '5s'")
                writer = connection.copy(alias="load_writer")
                sessions.callback(writer.close)
                _guard(writer, assert_disposable)
                box_id = snapshot["boxes"][0]["id"]
                timer = _StatementTimer(deadline, started, reading_count)
                stage = "acceptance_transaction"
                start = transaction_started = perf_counter()
                with connection.execute_wrapper(timer):
                    with transaction.atomic(durable=True):
                        with connection.cursor() as cursor:
                            cursor.execute("SET LOCAL lock_timeout = '10s'")
                            lock_start = perf_counter()
                            cursor.execute(_LOCK_SQL)
                        acquired = perf_counter()
                        result["timings_seconds"]["lock_acquisition"] = acquired - lock_start
                        result["locks_before_acceptance"] = _observe_locks(observer, pid)
                        result["contending_writer_while_locked"] = _probe_writer(writer, box_id, expect_blocked=True)
                        accept_start = perf_counter()
                        _progress("table locks held; accepting (readings, evidences, receipt, audit)", started)
                        receipt, replay = _accept_locked(snapshot, partial, archive, organization, actor)
                        result["timings_seconds"]["accept_helper"] = perf_counter() - accept_start
                        _progress("accept helper finished; enforcing deferred constraints and committing", started)
                        _require(not replay, "unexpected_replay_on_empty_synthetic_dataset")
                        stage = "constraint_enforcement"
                        constraint_start = perf_counter()
                        with connection.cursor() as cursor:
                            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                        result["timings_seconds"]["constraint_enforcement"] = perf_counter() - constraint_start
                        result["locks_before_commit"] = _observe_locks(observer, pid)
                        stage = "commit"
                        commit_start = perf_counter()
                    committed_at = perf_counter()
                result["committed"] = True
                result["timings_seconds"]["commit_exit"] = committed_at - commit_start
                result["timings_seconds"]["acceptance_transaction_including_commit"] = committed_at - start
                result["timings_seconds"]["table_locks_held_through_commit_upper_bound"] = committed_at - lock_start
                result["statements"] = timer.metrics()
                stage = "postcommit_writer"
                result["locks_after_commit"] = _observe_locks(observer, pid, expect_held=False)
                result["contending_writer_after_commit"] = _probe_writer(writer, box_id, expect_blocked=False)
                stage = "postcommit_validation"
                start = perf_counter()
                result["counts"] = _verify(
                    snapshot, partial, archive, receipt.pk, organization, actor, reading_count, evidence_count,
                )
                result["timings_seconds"]["postcommit_validation"] = perf_counter() - start
                _check_guards(connection)
        result["status"] = "passed"
    except KeyboardInterrupt:
        result["failure"] = {"stage": stage, "exception_type": "KeyboardInterrupt", "code": "interrupted", "sqlstate": None}
        result["interrupted"] = True
        if transaction_started is not None and not result["committed"]:
            result["timings_seconds"]["acceptance_transaction_until_failure"] = perf_counter() - transaction_started
    except Exception as exc:
        result["failure"] = _safe_failure(exc, stage)
        if transaction_started is not None and not result["committed"]:
            result["timings_seconds"]["acceptance_transaction_until_failure"] = perf_counter() - transaction_started
    finally:
        if timer is not None:
            result["statements"] = timer.metrics()
        if cleanup_context is not None:
            start = perf_counter()
            cleanup_connection, target_guard, cleanup = cleanup_context
            try:
                # runtime_role and ExitStack have exited even on a failed COMMIT.
                _guard(cleanup_connection, target_guard)
                cleanup()
                _guard(cleanup_connection, target_guard)
                _check_guards(cleanup_connection)
                result["cleanup"] = {"status": "passed", "guards_restored": True}
            except Exception as exc:
                result["status"] = "failed"
                result["cleanup"] = {"status": "failed", "failure": _safe_failure(exc, "cleanup")}
            result["timings_seconds"]["final_cleanup"] = perf_counter() - start
        if "memory" in result:
            if tracemalloc.is_tracing():
                current, peak = tracemalloc.get_traced_memory()
                result["memory"].update(tracemalloc_current_bytes=current, tracemalloc_peak_bytes=peak)
            result["memory"]["rss_after"] = _peak_rss()
        if owns_trace:
            tracemalloc.stop()
        result["timings_seconds"]["total"] = perf_counter() - started
    if "failure" in result:
        result["rolled_back"] = not result["committed"]
        _progress(f"stopped at {result['failure']['stage']}: {result['failure']['code']}; "
                  f"transaction {'committed' if result['committed'] else 'rolled back'}; cleanup {result['cleanup']['status']}", started)
    else:
        committed = result["timings_seconds"].get("acceptance_transaction_including_commit")
        if committed:
            result["throughput"] = {"readings_per_second": reading_count / committed,
                                    "evidences_per_second": evidence_count / committed,
                                    "basis": "acceptance transaction including commit"}
    result["feasibility"] = {
        "observation": "single_transaction_committed_and_verified" if result["status"] == "passed" else "not_established",
        "real_import_ready": None,
    }
    return result
