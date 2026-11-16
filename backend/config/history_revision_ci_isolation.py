"""Per-test PostgreSQL reset for the disposable Phase 5A QA.

Django TransactionTestCase cleans up with TRUNCATE. The real measurements.0012
and measurements.0013 migrations install BEFORE TRUNCATE guards on historical
scientific tables. Those guards are correct and must remain active.

This module restores the test database from a migrated, fixture-free template
created by this same QA execution. It never truncates a protected table,
disables a trigger, changes session_replication_role, calls flush, or swallows
a cleanup failure. The template and test database names are fixed, and the
template is dropped only while its creation OID still matches.
"""

from dataclasses import dataclass
from threading import Lock

from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from django.test import TransactionTestCase


BASE_DATABASE_NAME = "history_revision_ci"
TEST_DATABASE_NAME = "test_history_revision_ci"
TEMPLATE_DATABASE_NAME = "test_history_revision_ci_template"


@dataclass
class _State:
    maintenance: object
    base_name: str
    test_name: str
    template_name: str
    quote: object
    verify_base: object
    verify_test: object
    template_oid: int
    test_oid: int
    resets: int = 0


_STATE = None
_LOCK = Lock()
_ORIGINAL_TEARDOWN = None


def _require(condition, message):
    if not condition:
        raise ImproperlyConfigured("History revision CI isolation: " + message)


def _database_row(cursor, name):
    cursor.execute(
        "SELECT oid, pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s",
        [name],
    )
    return cursor.fetchone()


def _role(cursor):
    cursor.execute("SELECT current_user")
    return cursor.fetchone()[0]


def _require_base_cursor(cursor, base_name):
    cursor.execute("SELECT current_database()")
    _require(cursor.fetchone()[0] == base_name, "maintenance is not on the marked base database.")


def _drop_template(maintenance, base_name, template_name, template_oid, quote, verify_base):
    verify_base()
    with maintenance.cursor() as cursor:
        _require_base_cursor(cursor, base_name)
        role = _role(cursor)
        row = _database_row(cursor, template_name)
        _require(row is not None and row[0] == template_oid,
                 "template identity changed; refusing to drop an unknown database.")
        _require(row[1] == role, "template owner changed; refusing to drop an unknown database.")
        cursor.execute(f"DROP DATABASE {quote(template_name)}")


def prepare(maintenance, *, base_name, test_name, template_name, quote, verify_base, verify_test):
    """Create the empty template and install the reset teardown."""
    global _STATE, _ORIGINAL_TEARDOWN
    with _LOCK:
        _require(_STATE is None, "isolation was already prepared.")
        _require(base_name == BASE_DATABASE_NAME, "unexpected base database name.")
        _require(test_name == TEST_DATABASE_NAME, "unexpected test database name.")
        _require(template_name == TEMPLATE_DATABASE_NAME, "unexpected template database name.")
        _require(getattr(maintenance, "vendor", None) == "postgresql", "isolation requires PostgreSQL.")
        _require(maintenance.settings_dict.get("NAME") == base_name,
                 "maintenance connection is not on the marked base database.")
        verify_base()
        verify_test()
        connection = connections["default"]
        _require(connection.settings_dict.get("NAME") == test_name,
                 "default connection is not on the expected test database.")
        # CREATE DATABASE ... WITH TEMPLATE refuses a source with active sessions.
        connections.close_all()
        template_oid = None
        try:
            with maintenance.cursor() as cursor:
                _require_base_cursor(cursor, base_name)
                role = _role(cursor)
                _require(_database_row(cursor, template_name) is None,
                         "template database already exists; refusing to reuse or replace it.")
                test_row = _database_row(cursor, test_name)
                _require(test_row is not None and test_row[1] == role,
                         "test database is missing or not owned by the QA role.")
                test_oid = test_row[0]
                cursor.execute(
                    f"CREATE DATABASE {quote(template_name)} "
                    f"WITH TEMPLATE {quote(test_name)} OWNER {quote(role)}"
                )
                row = _database_row(cursor, template_name)
                _require(row is not None and row[1] == role, "template creation failed.")
                template_oid = row[0]
            connection.connect()
            verify_test()
        except BaseException as error:
            if template_oid is not None:
                try:
                    _drop_template(maintenance, base_name, template_name, template_oid, quote, verify_base)
                except BaseException as cleanup_error:
                    raise RuntimeError("Template cleanup failed after setup error.") from cleanup_error
            raise error
        _STATE = _State(
            maintenance=maintenance, base_name=base_name, test_name=test_name,
            template_name=template_name, quote=quote, verify_base=verify_base,
            verify_test=verify_test, template_oid=template_oid, test_oid=test_oid,
        )
        if _ORIGINAL_TEARDOWN is None:
            _ORIGINAL_TEARDOWN = TransactionTestCase._fixture_teardown
        TransactionTestCase._fixture_teardown = _teardown


def _reset(db_name):
    state = _STATE
    _require(state is not None, "isolation is not prepared.")
    _require(db_name == "default", "only the default database alias may be reset.")
    connection = connections[db_name]
    _require(connection.settings_dict.get("NAME") == state.test_name,
             "unexpected test database.")
    state.verify_test()
    connections.close_all()
    state.verify_base()
    with state.maintenance.cursor() as cursor:
        _require_base_cursor(cursor, state.base_name)
        role = _role(cursor)
        row = _database_row(cursor, state.test_name)
        _require(row is not None and row[1] == role,
                 "test database is missing or not owned by the QA role; refusing to reset.")
        _require(row[0] == state.test_oid,
                 "test database identity changed; refusing to drop an unknown database.")
        # No WITH (FORCE): a leaked session must fail closed instead of being killed.
        cursor.execute(f"DROP DATABASE {state.quote(state.test_name)}")
        cursor.execute(
            f"CREATE DATABASE {state.quote(state.test_name)} "
            f"WITH TEMPLATE {state.quote(state.template_name)} OWNER {state.quote(role)}"
        )
        row = _database_row(cursor, state.test_name)
        _require(row is not None and row[1] == role, "test database recreation failed.")
        state.test_oid = row[0]
    connection.connect()
    state.verify_test()
    state.resets += 1


def _teardown(self):
    # Replace Django's TRUNCATE-based flush with a full migrated-template reset.
    # This is deliberately not a call to the original cleanup: protected tables
    # must never be truncated, and the reset removes every committed fixture.
    state = _STATE
    if state is None:
        return _ORIGINAL_TEARDOWN(self)
    names = self._databases_names(include_mirrors=False)
    _require(names == ["default"], "only the default database alias may be reset.")
    for db_name in names:
        _reset(db_name)


def finish():
    """Restore the original teardown and drop only the template this run created."""
    global _STATE, _ORIGINAL_TEARDOWN
    with _LOCK:
        state = _STATE
        if state is None:
            return None
        try:
            state.verify_base()
            connections.close_all()
            _drop_template(
                state.maintenance, state.base_name, state.template_name,
                state.template_oid, state.quote, state.verify_base,
            )
            return {
                "template": state.template_name,
                "template_oid": state.template_oid,
                "resets": state.resets,
            }
        finally:
            if _ORIGINAL_TEARDOWN is not None:
                TransactionTestCase._fixture_teardown = _ORIGINAL_TEARDOWN
            _STATE = None