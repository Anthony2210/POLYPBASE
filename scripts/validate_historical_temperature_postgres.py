"""Interactive, explicit-target synthetic PostgreSQL validation; never a real import."""

import argparse
import getpass
import json
import os
import re
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / ".local-docs" / "historical-temperature-pgqa-validation.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Authorize mutations of the explicit disposable target only")
    parser.add_argument("--tests-only", action="store_true", help="Defer the representative load test; verdict stays CHANGES REQUIRED")
    parser.add_argument("--load-only", action="store_true", help="Skip migrations and the 52 tests; run only the synthetic load")
    parser.add_argument("--readings", type=int, default=40291, help="Synthetic readings (evidences = readings + 1)")
    parser.add_argument("--max-seconds", type=int, default=1800, help="Fail-safe: roll back and clean up past this duration")
    parser.add_argument("--trace-memory", action="store_true", help="Enable tracemalloc (slows Python work several times)")
    parser.add_argument("--clean-synthetic", action="store_true", help="With --load-only: remove provably synthetic leftovers first")
    parser.add_argument("--labels", nargs="+", help="Override focused test labels; no database lifecycle")
    arguments = parser.parse_args()
    if arguments.tests_only and arguments.load_only:
        parser.error("--tests-only and --load-only are mutually exclusive")
    if arguments.labels and not arguments.tests_only:
        parser.error("--labels requires --tests-only; a focused suite is not full validation")
    if arguments.labels and any(not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", label) for label in arguments.labels):
        parser.error("Invalid test label")
    if not 1 <= arguments.readings <= 40291 or not 1 <= arguments.max_seconds <= 7200:
        parser.error("--readings must be 1..40291 and --max-seconds 1..7200")
    if arguments.clean_synthetic and not arguments.load_only:
        parser.error("--clean-synthetic requires --load-only")
    if not arguments.run:
        parser.error("--run is required; use --help for a connection-free description")
    if any(os.getenv(flag) != "1" for flag in ("POLYPBASE_TEST_POSTGRES", "POLYPBASE_HISTORICAL_POSTGRES_QA")):
        parser.error("Both POLYPBASE_TEST_POSTGRES=1 and POLYPBASE_HISTORICAL_POSTGRES_QA=1 are required")
    if not sys.stdin.isatty():
        parser.error("Run locally in an interactive terminal; credentials cannot come from redirected input")
    # Do not inspect inherited values or let libpq/.env provide credentials/routes.
    for name in list(os.environ):
        if name.upper().startswith(("PG", "POSTGRES_")):
            os.environ.pop(name)
    os.environ.update(
        PYTHON_DOTENV_DISABLED="1", POSTGRES_DB="", DJANGO_DEBUG="0",
        DJANGO_SECRET_KEY="django-insecure-disposable-temperature-qa-only",
        DJANGO_SETTINGS_MODULE="config.historical_temperature_pgqa_settings",
    )
    sys.path.insert(0, str(ROOT / "backend"))
    from config import historical_temperature_pgqa as qa

    print("Target loaded from explicit runtime QA configuration (identity values withheld).")
    print("Cluster system identifier will be verified; PostgreSQL major must be 18.")
    print("Synthetic data only. No database creation/drop/clone, global role changes, or real importer activation.")
    print("Passwords are hidden and held only in this process. Do not run under a transcript/debugger.")
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(), "verdict": "CHANGES REQUIRED",
        "target": [qa.HOST, qa.PORT, qa.DATABASE], "real_importer_enabled": False,
        "scientific_data_accessed": False, "stage": "credentials",
        "postgresql_tests": {"status": "not_run"}, "load": {"status": "not_run"},
    }
    lease = None
    connections = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            for role in (qa.OWNER, qa.RUNTIME):
                password = getpass.getpass("Disposable owner password: " if role == qa.OWNER else "Disposable runtime password: ")
                if not password:
                    raise RuntimeError("Empty credentials refused")
                qa.CREDENTIALS[role] = password
            del password
        qa.AUTHORIZED = True
        import django
        django.setup()
        from django.core.management import call_command
        from django.db import connection, connections

        report["stage"] = "identity_and_privilege_preflight"
        qa.assert_disposable(connection)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()")
            if cursor.fetchone()[0] != qa.OWNER:
                raise qa.IsolationError("database owner", qa.OWNER, "a different role")
        # Observe metadata only; never attempt a connection to another database.
        with qa.runtime_role():
            with connection.cursor() as cursor:
                cursor.execute("SELECT has_schema_privilege(current_user,'public','CREATE'), has_database_privilege(current_user,current_database(),'CREATE'), pg_has_role(current_user,%s,'MEMBER'), pg_has_role(current_user,%s,'SET')", [qa.OWNER, qa.OWNER])
                privileges = cursor.fetchone()
                if privileges != (False, False, False, False):
                    raise qa.IsolationError(
                        "runtime lacks (schema CREATE, database CREATE, owner MEMBER, owner SET)",
                        (False,) * 4, privileges,
                    )
                cursor.execute("SELECT rolname FROM pg_roles WHERE rolname<>current_user AND pg_has_role(current_user,oid,'MEMBER') AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls OR rolname IN ('pg_read_all_data','pg_write_all_data','pg_execute_server_program','pg_write_server_files','pg_read_server_files'))")
                unsafe = [row[0] for row in cursor.fetchall()]
                if unsafe:
                    raise qa.IsolationError("runtime inherits no privileged cluster role", "none", unsafe)
                cursor.execute("SELECT count(*) FROM pg_database WHERE datallowconn AND datname<>current_database() AND has_database_privilege(current_user,oid,'CONNECT')")
                report["runtime_other_database_connect_count_metadata_only"] = cursor.fetchone()[0]
        report["database_boundary"] = "Enforced by fixed backend and runner, not inferred from role attributes or CONNECT ACLs"
        lease = connection.copy(alias="qa_exclusive_lease")
        qa.assert_disposable(lease)
        with lease.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(20261018,1)")
            if not cursor.fetchone()[0]:
                raise qa.IsolationError("exclusive advisory lease", "acquired", "held by another session")
        qa.RUN_LEASE = lease
        if arguments.clean_synthetic:
            report["stage"] = "synthetic_leftover_cleanup"
            try:
                qa.assert_empty_business_data(connection)
                report["synthetic_leftovers"] = "none"
            except qa.IsolationError as exc:
                if not exc.invariant.startswith("disposable database starts empty"):
                    raise
                print(f"  leftovers present ({exc.invariant}: {exc.actual}); verifying they are synthetic")
                report["synthetic_leftovers"] = qa.clean_synthetic_leftovers(connection)
                print("  synthetic leftovers removed")
        qa.assert_empty_business_data(connection)
        qa.EMPTY_START_VERIFIED = True
        if arguments.load_only:
            qa.verify_schema(connection)
            report["stage"] = "representative_synthetic_load"
            from apps.measurements.historical_temperature_pgqa_load import run_load

            report["load"] = run_load(arguments.readings, max_seconds=arguments.max_seconds,
                                      trace_memory=arguments.trace_memory)
            print(json.dumps({k: report["load"].get(k) for k in ("status", "timings_seconds", "throughput", "failure", "cleanup", "memory")}, indent=2, default=str))
            qa.EMPTY_START_VERIFIED = True
            qa.assert_empty_business_data(connection)
            report["stage"] = "complete"
            if report["load"]["status"] != "passed":
                raise RuntimeError("Synthetic load did not pass")
            report["verdict"] = "LOAD-ONLY RUN PASSED (not a full validation)"
            return 0
        report["stage"] = "migration_round_trip"
        report["migrations"] = qa.migration_rehearsal(connection)
        report["stage"] = "django_checks"
        call_command("check", database=["default"], verbosity=1)
        call_command("makemigrations", check=True, dry_run=True, verbosity=1)
        report["django_checks"] = "passed"
        report["stage"] = "postgresql_tests"
        runner = qa.IsolatedTemperatureRunner(verbosity=2, interactive=False, parallel=0, failfast=True)
        failures = runner.run_tests(arguments.labels or [
            "apps.measurements.test_historical_temperature_postgres",
            "apps.measurements.test_historical_temperature_pgqa_migrations",
            "apps.measurements.test_historical_temperature_pgqa_runtime",
        ])
        report["postgresql_tests"] = runner.result_summary or {"status": "runner_failed"}
        if failures or not runner.result_summary or runner.result_summary["skipped"]:
            raise RuntimeError("PostgreSQL validation has failures or skipped proofs")
        qa.assert_empty_business_data(connection)
        report["stage"] = "representative_synthetic_load"
        if arguments.tests_only:
            report["load"] = {"status": "not_run", "reason": "--tests-only"}
        else:
            from apps.measurements.historical_temperature_pgqa_load import run_load

            report["load"] = run_load(arguments.readings, max_seconds=arguments.max_seconds,
                                      trace_memory=arguments.trace_memory)
            if report["load"]["status"] != "passed":
                raise RuntimeError("Representative synthetic load failed; inspect sanitized load metrics")
        qa.assert_empty_business_data(connection)
        report["final_schema"] = qa.verify_schema(connection)
        report["stage"] = "complete"
        if arguments.labels:
            report["verdict"] = "FOCUSED TESTS PASSED (not a full historical validation)"
        elif not arguments.tests_only:
            report["verdict"] = "READY FOR LOCAL UI IMPORT PREPARATION"
    except (Exception, KeyboardInterrupt, SystemExit) as exc:
        # Never serialize exception repr, locals, connection dictionaries or secrets.
        cause = getattr(exc, "__cause__", None)
        report["failure"] = {"exception_type": type(exc).__name__,
                             "sqlstate": getattr(cause or exc, "sqlstate", None)}
        if isinstance(exc, qa.IsolationError):
            # Invariant name and non-sensitive values only; never credentials.
            report["failure"].update(invariant=exc.invariant, expected=exc.expected, actual=exc.actual)
            print(f"  failed invariant: {exc.invariant}")
            print("  Identity details withheld; inspect the private local report if needed.")
        elif isinstance(exc, RuntimeError):
            report["failure"]["message"] = str(exc)[:200]
            print(f"  message: {str(exc)[:200]}")
        print(f"STOP at {report['stage']}: {type(exc).__name__}; no fallback target attempted.")
    finally:
        if lease is not None:
            lease.close()
        if connections is not None:
            connections.close_all()
        qa.CREDENTIALS.clear()
        qa.AUTHORIZED = False
        qa.RUN_LEASE = None
        qa.EMPTY_START_VERIFIED = False
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        # Dedicated report file only; no snapshots/source archives/credentials.
        REPORT.parent.mkdir(exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        print(f"Sanitized report: {REPORT}")
        print(report["verdict"])
    return 0 if report["verdict"] in {
        "READY FOR LOCAL UI IMPORT PREPARATION",
        "FOCUSED TESTS PASSED (not a full historical validation)",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
