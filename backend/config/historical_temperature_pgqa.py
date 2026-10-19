"""Fail-closed infrastructure for one explicitly authorized disposable database.

Credentials live only in this process and are populated by the interactive launcher.
Cluster identity is an administrator attestation, verified with the read-only pg_control_system function.
"""

import os
import re
import threading
from time import monotonic
from contextlib import contextmanager
from importlib import import_module

from .temperature_pgqa_backend.runtime_config import load_target

_TARGET = load_target()
HOST, PORT, DATABASE, SYSTEM_IDENTIFIER, OWNER, RUNTIME = _TARGET
ENGINE = "config.temperature_pgqa_backend"
SETTINGS = "config.historical_temperature_pgqa_settings"
OPTIONS = "-c lock_timeout=10000 -c statement_timeout=30000 -c search_path=public -c default_transaction_isolation=read\\ committed"
CREDENTIALS = {}
AUTHORIZED = False
RUN_LEASE = None
EMPTY_START_VERIFIED = False
UNSAFE_WORKERS = False
RAW_CONNECTIONS = []
RAW_CONNECTIONS_LOCK = threading.Lock()
TRIGGER_SPEC = {
    "historical_reading_immutable": ("measurements_historicaltemperaturereading", "historical_temperature_reading_immutable", 27),
    "historical_evidence_immutable": ("measurements_historicaltemperatureevidence", "historical_temperature_immutable", 27),
    "historical_reading_no_truncate": ("measurements_historicaltemperaturereading", "historical_temperature_immutable", 34),
    "historical_evidence_no_truncate": ("measurements_historicaltemperatureevidence", "historical_temperature_immutable", 34),
    "historical_reading_valid": ("measurements_historicaltemperaturereading", "historical_temperature_reading_valid", 7),
    "historical_evidence_valid": ("measurements_historicaltemperatureevidence", "historical_temperature_evidence_valid", 7),
    "historical_reading_nonempty": ("measurements_historicaltemperaturereading", "historical_temperature_nonempty", 5),
    "historical_biology_guard": ("measurements_biologicalmeasurement", "historical_temperature_biology_guard", 19),
    "historical_box_guard": ("cultures_box", "historical_temperature_box_guard", 19),
    "historical_receipt_immutable": ("measurements_historicaltemperatureimportreceipt", "historical_temperature_immutable", 27),
    "historical_receipt_no_truncate": ("measurements_historicaltemperatureimportreceipt", "historical_temperature_immutable", 34),
}
TRIGGERS = {
    "historical_reading_immutable", "historical_evidence_immutable",
    "historical_reading_no_truncate", "historical_evidence_no_truncate",
    "historical_reading_valid", "historical_evidence_valid", "historical_reading_nonempty",
    "historical_biology_guard", "historical_box_guard",
    "historical_receipt_immutable", "historical_receipt_no_truncate",
}
FUNCTIONS = {
    "historical_temperature_immutable", "historical_temperature_reading_immutable",
    "historical_temperature_decimal", "historical_temperature_reading_valid",
    "historical_temperature_evidence_valid", "historical_temperature_nonempty",
    "historical_temperature_biology_guard", "historical_temperature_box_guard",
}


class IsolationError(RuntimeError):
    """A failed invariant with non-sensitive expected/actual values only."""

    def __init__(self, invariant, expected, actual):
        super().__init__(f"{invariant}: expected {expected!r}, actual {actual!r}")
        self.invariant, self.expected, self.actual = invariant, str(expected), str(actual)


def register_raw(raw):
    with RAW_CONNECTIONS_LOCK:
        RAW_CONNECTIONS[:] = [(ident, conn) for ident, conn in RAW_CONNECTIONS if not conn.closed]
        RAW_CONNECTIONS.append((threading.get_ident(), raw))


def join_workers(threads, timeout=25):
    """A live worker can never authorize destructive owner cleanup."""
    global UNSAFE_WORKERS
    deadline = monotonic() + timeout
    for thread in threads:
        if thread.ident is not None:
            thread.join(timeout=max(0, deadline - monotonic()))
    alive = [thread for thread in threads if thread.is_alive()]
    if not alive:
        return
    identifiers = {thread.ident for thread in alive}
    with RAW_CONNECTIONS_LOCK:
        raw = [conn for ident, conn in RAW_CONNECTIONS if ident in identifiers and not conn.closed]
    for conn in raw:
        try:
            conn.cancel_safe(timeout=2)
        except Exception:
            pass  # Termination, not cancellation success, controls cleanup eligibility.
    for thread in alive:
        thread.join(timeout=5)
    UNSAFE_WORKERS = any(thread.is_alive() for thread in alive)
    raise RuntimeError("Worker exceeded its deadline; cancellation attempted; do not continue the rehearsal")


def require_run_lease():
    if UNSAFE_WORKERS or not EMPTY_START_VERIFIED or RUN_LEASE is None or RUN_LEASE.connection is None or RUN_LEASE.connection.closed:
        raise RuntimeError("Synthetic cleanup/mutation requires an empty-start rehearsal and live exclusive lease")


def require_opt_in():
    if not AUTHORIZED or any(os.getenv(name) != "1" for name in (
        "PYTHON_DOTENV_DISABLED", "POLYPBASE_TEST_POSTGRES", "POLYPBASE_HISTORICAL_POSTGRES_QA",
    )):
        raise RuntimeError("Use the interactive isolated PostgreSQL launcher with both explicit opt-ins")


def assert_config(config):
    """Validate before any network or lifecycle operation, including thread copies."""
    require_opt_in()
    if (
        config.get("ENGINE") != ENGINE
        or (config.get("HOST"), str(config.get("PORT")), config.get("NAME")) != (HOST, PORT, DATABASE)
        or config.get("USER") not in (OWNER, RUNTIME)
        or not CREDENTIALS.get(config.get("USER"))
        or config.get("PASSWORD") != CREDENTIALS.get(config.get("USER"))
        or config.get("OPTIONS") != {
            "sslmode": "disable", "connect_timeout": 5, "options": OPTIONS,
            "application_name": "polypbase-temperature-isolated-qa", "passfile": os.devnull,
        }
        or config.get("TEST", {}).get("NAME") != DATABASE
        or config.get("TEST", {}).get("MIRROR")
    ):
        raise RuntimeError("Refusing a connection outside the fixed disposable QA configuration")


def verify_raw(raw, role):
    """Verify the control identity and restricted role without a maintenance connection."""
    with raw.cursor() as cursor:
        cursor.execute("SELECT system_identifier::text FROM pg_catalog.pg_control_system()")
        if cursor.fetchone()[0] != SYSTEM_IDENTIFIER:
            raise RuntimeError("Cluster system identifier does not match the explicit QA attestation")
        # host() omits the address mask.
        cursor.execute(
            "SELECT current_database(), host(inet_server_addr()), inet_server_port(), "
            "current_setting('server_version_num')::integer, session_user, current_user, "
            "current_schema(), current_setting('session_replication_role'), "
            "current_setting('transaction_isolation')"
        )
        row = cursor.fetchone()
        if row[:3] != (DATABASE, HOST, int(PORT)):
            raise IsolationError("server identity (database, address, port)", (DATABASE, HOST, int(PORT)), row[:3])
        if not 180000 <= row[3] < 190000:
            raise IsolationError("PostgreSQL major version 18", "180000..189999", row[3])
        if row[4:] != (role, role, "public", "origin", "read committed"):
            raise IsolationError(
                "session_user, current_user, schema, replication role, isolation",
                (role, role, "public", "origin", "read committed"), row[4:],
            )
        cursor.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
            "FROM pg_roles WHERE rolname=current_user"
        )
        attributes = cursor.fetchone()
        if attributes != (False, False, False, False, False):
            raise IsolationError(
                "cluster attributes (super, createdb, createrole, replication, bypassrls)",
                (False,) * 5, attributes,
            )


def assert_disposable(connection):
    from django.conf import settings

    if not getattr(settings, "HISTORICAL_TEMPERATURE_PGQA", False):
        raise RuntimeError("Dedicated disposable settings are required")
    if getattr(settings, "HISTORICAL_TEMPERATURE_LOCAL_UI_APPLY_ENABLED", False):
        raise RuntimeError("The real importer must remain disabled")
    assert_config(connection.settings_dict)
    connection.ensure_connection()
    verify_raw(connection.connection, connection.settings_dict["USER"])


@contextmanager
def runtime_role():
    """Use a real runtime login, never SET ROLE from an owner/superuser session.

    Call only outside transactions and join worker threads before leaving.
    """
    from django.conf import settings
    from django.db import connection

    assert_disposable(connection)
    if connection.in_atomic_block or connection.settings_dict["USER"] != OWNER:
        raise RuntimeError("Runtime switch requires an idle owner connection")
    connection.close()
    config = connection.settings_dict
    previous = (config["USER"], config["PASSWORD"])
    config.update(USER=RUNTIME, PASSWORD=CREDENTIALS[RUNTIME])
    settings.DATABASES["default"] = config
    try:
        assert_disposable(connection)
        yield
    finally:
        connection.close()
        config.update(USER=previous[0], PASSWORD=previous[1])
        settings.DATABASES["default"] = config


def require_owner(connection):
    assert_disposable(connection)
    if connection.settings_dict["USER"] != OWNER or connection.in_atomic_block:
        raise RuntimeError("Disposable maintenance requires the idle dedicated owner")


def assert_empty_business_data(connection):
    require_owner(connection)
    from django.apps import apps

    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
        managed = {model._meta.db_table for model in apps.get_models(include_auto_created=True) if model._meta.managed}
        if tables - managed - {"django_migrations"}:
            raise IsolationError("only managed application tables exist", "none unexpected", sorted(tables - managed - {"django_migrations"}))
        for table in tables - {"django_migrations", "django_content_type", "auth_permission"}:
            cursor.execute(f'SELECT EXISTS (SELECT 1 FROM {connection.ops.quote_name(table)})')
            if cursor.fetchone()[0]:
                raise IsolationError("disposable database starts empty (no application rows)", "empty", f"rows in {table}")
        cursor.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND nspname NOT LIKE 'pg_%'")
        extra = [row[0] for row in cursor.fetchall()]
        if extra:
            raise IsolationError("no user schemas besides public", "none", extra)


def clean_synthetic_leftovers(connection):
    """Remove rows left by an interrupted synthetic run, only if every row is provably synthetic.

    Synthetic means: every organization name is one the harness creates and every user email is
    @example.org. Anything else aborts untouched. Needs the owner, the lease and a verified target.
    """
    require_owner(connection)
    if RUN_LEASE is None or RUN_LEASE.connection is None or RUN_LEASE.connection.closed:
        raise RuntimeError("Synthetic cleanup requires the live exclusive lease")
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM organizations_organization")
        names = [row[0] for row in cursor.fetchall()]
        cursor.execute("SELECT email FROM auth_user")
        emails = [row[0] for row in cursor.fetchall()]
    prefixes = ("Synthetic ", "Foreign synthetic")
    foreign = [name for name in names if not name.startswith(prefixes)]
    if foreign or any(not email.endswith("@example.org") for email in emails):
        raise IsolationError("leftover rows are provably synthetic", "synthetic organizations and @example.org users",
                             f"{len(foreign)} unrecognized organizations, {sum(not e.endswith('@example.org') for e in emails)} other users")
    global EMPTY_START_VERIFIED
    EMPTY_START_VERIFIED = True  # Attested by the checks above for this cleanup only.
    try:
        cleanup_test_data()
    finally:
        EMPTY_START_VERIFIED = False
    assert_empty_business_data(connection)
    return {"organizations": len(names), "users": len(emails)}


def grant_runtime(connection):
    """Only database-local object ACLs; never ALTER ROLE/DATABASE or authentication."""
    from psycopg import sql

    assert_disposable(connection)
    if connection.settings_dict["USER"] != OWNER:
        raise RuntimeError("Only the dedicated owner can grant disposable object privileges")
    with connection.cursor() as cursor:
        cursor.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(RUNTIME)))
        tables = connection.introspection.table_names(cursor)
        for table in tables:
            privileges = "SELECT" if table == "django_migrations" else "SELECT, INSERT, UPDATE, DELETE"
            cursor.execute(sql.SQL("GRANT " + privileges + " ON TABLE {} TO {}").format(
                sql.Identifier("public", table), sql.Identifier(RUNTIME),
            ))
        cursor.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(RUNTIME)))
        for table in ("measurements_historicaltemperaturereading", "measurements_historicaltemperatureevidence", "measurements_historicaltemperatureimportreceipt"):
            cursor.execute(sql.SQL("GRANT TRUNCATE ON TABLE {} TO {}").format(sql.Identifier(table), sql.Identifier(RUNTIME)))
        cursor.execute("SELECT p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname LIKE 'historical_temperature_%'")
        signatures = [row[0] for row in cursor.fetchall()]
        for signature in signatures:
            # Catalog-generated signatures, not caller input.
            cursor.execute(sql.SQL("REVOKE ALL ON FUNCTION " + signature + " FROM PUBLIC"))
            cursor.execute(sql.SQL("GRANT EXECUTE ON FUNCTION " + signature + " TO {}").format(sql.Identifier(RUNTIME)))


def verify_schema(connection):
    assert_disposable(connection)
    with connection.cursor() as cursor:
        cursor.execute("SELECT t.tgname, t.tgenabled, t.tgdeferrable, t.tginitdeferred, c.relname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal AND t.tgname LIKE 'historical_%'")
        triggers = cursor.fetchall()
        if {row[0] for row in triggers} != TRIGGERS or any(row[1] != "O" for row in triggers):
            raise RuntimeError("Historical PostgreSQL guards are missing or disabled")
        deferred = [row for row in triggers if row[0] == "historical_reading_nonempty"]
        if len(deferred) != 1 or deferred[0][2:4] != (True, True):
            raise RuntimeError("Evidence nonempty constraint must be deferred to COMMIT")
        cursor.execute("SELECT p.proname, p.prosecdef, p.proowner::regrole::text, p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname LIKE 'historical_temperature_%'")
        functions = cursor.fetchall()
        if {row[0] for row in functions} != FUNCTIONS or any(row[1] or row[2] != OWNER for row in functions):
            raise RuntimeError("Unexpected guard functions or ownership/security-definer privileges")
        if any("search_path=pg_catalog, public, pg_temp" not in (row[3] or []) for row in functions):
            raise RuntimeError("Guard function search path is not pinned")
        cursor.execute("SELECT name FROM django_migrations WHERE app='measurements' AND name LIKE '001_%%' ORDER BY name")
        migrations = [row[0] for row in cursor.fetchall()]
        if not {"0013_historical_temperature_receipt", "0014_measurement_canonical_evidence"} <= set(migrations):
            raise RuntimeError("Integrated historical and canonical migrations were not applied")
        cursor.execute("SELECT conname, contype, condeferrable, condeferred, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid IN ('measurements_historicaltemperaturereading'::regclass, 'measurements_historicaltemperatureevidence'::regclass, 'measurements_historicaltemperatureimportreceipt'::regclass) ORDER BY conname")
        constraints = cursor.fetchall()
        names = {row[0] for row in constraints}
        expected = {"historical_temp_year_valid", "historical_temp_week_valid", "historical_temp_status_valid", "historical_temp_src_year_valid", "historical_temp_src_week_valid", "unique_historical_temp_evidence"}
        if not expected <= names or sum(row[1] == "f" for row in constraints) != 3:
            raise RuntimeError("Historical checks/uniqueness/foreign keys are incomplete")
        if not any(row[1] == "u" and "biological_measurement_id" in row[4] for row in constraints):
            raise RuntimeError("Reading biological source must be unique")
        cursor.execute("SELECT current_setting('server_version')")
        version = cursor.fetchone()[0]
    return {"server_version": version, "target": [HOST, PORT, DATABASE],
            "cluster_system_identifier": SYSTEM_IDENTIFIER,
            "cluster_identity_source": "explicit provisioning attestation verified with pg_control_system",
            "triggers": [list(row) for row in triggers], "functions": [list(row) for row in functions],
            "constraints": [list(row) for row in constraints], "measurements_migrations": migrations}


def cleanup_test_data():
    """Explicitly destructive synthetic-fixture cleanup, only on the fixed target.

    TransactionTestCase cannot flush with immutable guards installed. Drop guards,
    flush and reinstall atomically; no populated migration rollback is bypassed on
    any scientific target. The database itself is never dropped or recreated.
    """
    from django.core.management import call_command
    from django.db import connection, transaction

    require_owner(connection)
    require_run_lease()
    verify_schema(connection)
    guards = import_module("apps.measurements.migrations.0012_historical_temperature_guards")
    receipt = import_module("apps.measurements.migrations.0013_historical_temperature_receipt")
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("DROP TRIGGER historical_receipt_immutable ON measurements_historicaltemperatureimportreceipt; DROP TRIGGER historical_receipt_no_truncate ON measurements_historicaltemperatureimportreceipt;")
            cursor.execute(guards.REVERSE_SQL)
        call_command("flush", database="default", interactive=False, verbosity=0,
                     reset_sequences=False, inhibit_post_migrate=True)
        with connection.schema_editor() as editor:
            guards.forwards(None, editor)
            receipt.guards(None, editor)
            import_module("apps.measurements.migrations.0014_measurement_canonical_evidence").forwards(None, editor)
        grant_runtime(connection)
    verify_schema(connection)


def migration_rehearsal(connection):
    """Real migration executor round trip, then stop at the complete current graph."""
    from django.core.management import call_command
    from django.db.migrations.executor import MigrationExecutor

    require_run_lease()
    assert_empty_business_data(connection)
    call_command("migrate", database="default", interactive=False, verbosity=1)
    verify_schema(connection)
    executor = MigrationExecutor(connection)
    executor.migrate([("measurements", "0010_measurement_import_receipt")])
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
        if any(table in tables for table in (
            "measurements_historicaltemperaturereading", "measurements_historicaltemperatureevidence",
            "measurements_historicaltemperatureimportreceipt",
        )):
            raise RuntimeError("Empty reverse migration left historical storage behind")
        cursor.execute("SELECT 1 FROM pg_trigger WHERE tgname LIKE 'historical_%'")
        if cursor.fetchone():
            raise RuntimeError("Empty reverse migration left historical triggers behind")
        cursor.execute("SELECT 1 FROM django_migrations WHERE app='measurements' AND name IN ('0011_historical_temperature_storage','0012_historical_temperature_guards','0013_historical_temperature_receipt')")
        if cursor.fetchone():
            raise RuntimeError("Empty reverse did not update migration records")
    call_command("migrate", database="default", interactive=False, verbosity=1)
    grant_runtime(connection)
    return {"forward_through_0013": "passed", "empty_reverse_through_0010": "passed",
            "forward_again_through_0013": "passed", "schema": verify_schema(connection)}


# Importing this class has no connection side effects.
from django.test.runner import DiscoverRunner  # noqa: E402


class IsolatedTemperatureRunner(DiscoverRunner):
    """Use a pre-provisioned database; no Django database lifecycle machinery."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.parallel not in (0, 1):
            raise RuntimeError("Parallel database cloning is forbidden")
        self.result_summary = None

    def setup_databases(self, **kwargs):
        from django.db import connection, connections

        if set(connections) != {"default"}:
            raise RuntimeError("Additional database aliases are forbidden")
        assert_disposable(connection)
        verify_schema(connection)
        return None

    def teardown_databases(self, old_config, **kwargs):
        from django.db import connections

        connections.close_all()

    def run_suite(self, suite, **kwargs):
        result = super().run_suite(suite, **kwargs)
        self.result_summary = {
            "run": result.testsRun, "failed": len(result.failures), "errors": len(result.errors),
            "skipped": len(result.skipped),
            "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
            "failed_tests": [test.id() for test, _ in result.failures + result.errors],
            "skipped_tests": [(test.id(), reason) for test, reason in result.skipped],
        }
        return result
