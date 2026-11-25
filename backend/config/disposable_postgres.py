"""Fixture cleanup for TransactionTestCase on a verified disposable PostgreSQL cluster.

Django's flush truncates every table, but append-only guards (historical
temperature, Box Local ID aliases) deliberately forbid TRUNCATE. Test cleanup is
not a business operation, so it may bypass ordinary triggers, but only:

- after proving the server is the disposable cluster created for this run;
- inside one transaction with SET LOCAL session_replication_role = replica,
  which PostgreSQL resets on commit or rollback. No trigger, function or table
  is altered, so the guards stay active for the tests themselves.
"""

from importlib import import_module

from pathlib import Path


from django.db import connection, transaction
from django.test import TransactionTestCase


def _launcher_lease():
    """Return the fresh-cluster launcher lease, or None outside that launcher.

    The lease lives only in this process memory and is handed over by the
    launcher; no environment variable or file can create it.
    """
    try:
        lease = import_module("historical_temperature_cluster_lease")
    except ImportError:
        return None
    try:
        return lease.require_launch()
    except RuntimeError:
        return None


def disposable_postgres_declared():
    """True when a disposable cluster is declared; checked again before cleanup."""
    if connection.vendor != "postgresql":
        return False
    from .backend_postgres_ci import active

    service = active()
    return _launcher_lease() is not None or (service is not None and service.test_template is not None)


def require_disposable_cluster():
    """Raise unless the live server is the declared disposable test cluster."""
    if connection.vendor != "postgresql":
        raise RuntimeError("Disposable cluster verification requires PostgreSQL.")
    launch = _launcher_lease()
    if launch is None:
        raise RuntimeError("PostgreSQL fixture cleanup requires the live fresh-cluster launcher.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_setting('data_directory'), "
                       "(pg_catalog.pg_control_system()).system_identifier::text")
        database, data_directory, system_identifier = cursor.fetchone()
    test_name = connection.creation._get_test_db_name()
    if database != test_name or not database.startswith("test_"):
        raise RuntimeError("Fixture cleanup is allowed only in the Django test database.")
    if (Path(data_directory).resolve() != Path(launch.data).resolve()
            or system_identifier != launch.system_identifier):
        raise RuntimeError("Server does not match the fresh launcher cluster.")
    # Reuse the launcher's full live-session attestation as well as the
    # database/path checks above (restart, role and postmaster identity).
    import_module("historical_temperature_cluster_lease").verify_server(connection.connection)


def _verified_flush(test, original):
    """Run ``original`` (Django's flush) with ordinary triggers suppressed.

    Only after verifying the disposable cluster, and only inside one
    transaction: SET LOCAL ends with it, then normal execution is checked.
    """
    require_disposable_cluster()
    if connection.in_atomic_block:
        raise RuntimeError("Fixture cleanup requires its own transaction.")
    with connection.cursor() as cursor:
        cursor.execute("SHOW session_replication_role")
        if cursor.fetchone()[0] != "origin":
            raise RuntimeError("Fixture cleanup requires normal trigger execution.")
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL session_replication_role = replica")
            original(test)
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SHOW session_replication_role")
            if cursor.fetchone()[0] != "origin":
                raise RuntimeError("Trigger execution was not restored after fixture cleanup.")


_ORIGINAL_TEARDOWN = None


def _installed_teardown(self):
    if connection.vendor != "postgresql":
        return _ORIGINAL_TEARDOWN(self)
    return _verified_flush(self, _ORIGINAL_TEARDOWN)


def install_disposable_flush():
    """Cover every TransactionTestCase of a fresh-launcher run.

    Called by the launcher's runner after its test database exists. The
    launcher lease is required here and verified again before each cleanup.
    """
    global _ORIGINAL_TEARDOWN
    if connection.vendor != "postgresql" or _launcher_lease() is None:
        raise RuntimeError("Runner-level fixture cleanup requires the fresh-cluster launcher.")
    require_disposable_cluster()
    if _ORIGINAL_TEARDOWN is None:
        _ORIGINAL_TEARDOWN = TransactionTestCase._fixture_teardown
        TransactionTestCase._fixture_teardown = _installed_teardown


def uninstall_disposable_flush():
    global _ORIGINAL_TEARDOWN
    if _ORIGINAL_TEARDOWN is not None:
        TransactionTestCase._fixture_teardown = _ORIGINAL_TEARDOWN
        _ORIGINAL_TEARDOWN = None


class DisposablePostgresFlushMixin:
    """Place before TransactionTestCase in the bases.

    Under the launcher the runner-level cleanup already covers every
    TransactionTestCase. Outside it, PostgreSQL cleanup fails closed; a
    declared directory alone is not proof of a disposable cluster.
    """

    def _fixture_teardown(self):
        from .backend_postgres_ci import active

        service = active()
        if (connection.vendor != "postgresql" or _ORIGINAL_TEARDOWN is not None
                or (service is not None and service.test_template is not None)):
            return super()._fixture_teardown()
        parent = super()._fixture_teardown
        return _verified_flush(self, lambda _test: parent())
