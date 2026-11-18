"""Historical schema rehearsals, not PostgreSQL capability acceptance tests."""

from contextlib import contextmanager
from copy import copy, deepcopy
from importlib import import_module
from uuid import uuid4

from django.db.migrations.executor import MigrationExecutor as DjangoMigrationExecutor


BOX_1902_CAPABILITY = ("measurements", "0018_box_1902_preservation")


class MigrationExecutor(DjangoMigrationExecutor):
    """Exclude a PostgreSQL-only capability from SQLite historical rehearsals.

    0018 has no SQLite schema or data operations, but deliberately refuses reverse
    execution on every backend. Historical schema tests must not try to retire
    that capability. Use a private loader copy of its empty SQLite operations;
    never change the real migration, its rollback guard, or PostgreSQL execution.
    """

    def __init__(self, connection, progress_callback=None):
        super().__init__(connection, progress_callback)
        if connection.vendor == "sqlite":
            migration = copy(self.loader.graph.nodes[BOX_1902_CAPABILITY])
            migration.operations = []
            self.loader.graph.nodes[BOX_1902_CAPABILITY] = migration
            self.loader.disk_migrations[BOX_1902_CAPABILITY] = migration


def rehearsal_latest_targets(loader, vendor):
    """Keep PostgreSQL historical rehearsals strictly before the capability."""
    if vendor == "postgresql":
        return historical_targets(loader, {"measurements"}, [
            ("measurements", "0017_bucket_probe_last_measured_index"),
        ])
    return loader.graph.leaf_nodes()


@contextmanager
def historical_rehearsal_database(connection):
    """Use a new database, never reverse an installed PostgreSQL capability.

    Only the live fresh-cluster launcher grants creation authority. The original
    Django wrapper is retained so historical model managers still use default.
    Restore it before TransactionTestCase flushes the runner's real test schema.
    """
    if connection.vendor == "sqlite":
        yield
        return
    if connection.vendor != "postgresql":
        raise RuntimeError("Historical rehearsals require SQLite or the fresh PostgreSQL launcher")
    lease = import_module("historical_temperature_cluster_lease")
    launch = lease.require_launch()
    original = deepcopy(connection.settings_dict)
    if (original["HOST"] != "127.0.0.1" or int(original["PORT"]) != launch.port
            or original["USER"] != "subculture_qa" or original["PASSWORD"] != ""
            or original["NAME"] != "test_subculture_qa"
            or connection.in_atomic_block):
        raise RuntimeError("Refuse historical rehearsal outside the launcher's test database")
    from psycopg import sql

    name = "test_history_" + uuid4().hex
    with lease.connect_leased(
        host="127.0.0.1", port=launch.port, dbname="postgres", user="subculture_qa",
        password="", sslmode="disable", connect_timeout=5, autocommit=True,
        options="-c lock_timeout=10000 -c statement_timeout=120000",
    ) as admin:
        # CREATE has no IF NOT EXISTS: a collision never adopts an existing DB.
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
        oid = admin.execute("SELECT oid FROM pg_database WHERE datname=%s", [name]).fetchone()[0]
        try:
            connection.close()
            connection.settings_dict["NAME"] = name
            connection.ensure_connection()
            lease.verify_server(connection.connection)
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_database(), oid FROM pg_database WHERE datname=current_database()")
                if cursor.fetchone() != (name, oid):
                    raise RuntimeError("Historical rehearsal database identity mismatch")
            yield
        finally:
            connection.close()
            connection.settings_dict.clear()
            connection.settings_dict.update(original)
            # Drop only the database this context created on the attested server.
            # No FORCE or session termination: unexpected users fail cleanup closed.
            lease.verify_server(admin)
            row = admin.execute("SELECT oid FROM pg_database WHERE datname=%s", [name]).fetchone()
            if row != (oid,):
                raise RuntimeError("Refuse dropping a replaced historical rehearsal database")
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


class HistoricalRehearsalMixin:
    """Place before TransactionTestCase; cleanup precedes Django fixture teardown."""

    def setUp(self):
        super().setUp()
        from django.db import connection

        self.enterContext(historical_rehearsal_database(connection))


def historical_targets(loader, historical_apps, historical):
    """Return the target list for a historical schema rehearsal.

    ``historical`` pins exact migration states for ``historical_apps``. Raise
    ValueError if their dependencies require migrations beyond another pin.
    Every other app stays at its latest migration, except a leaf that depends on
    a migration of a historical app beyond the historical point. Such a leaf
    would pull those recent migrations back into the projected state while the
    physical schema stays historical, so it is left out of the rehearsal.
    """
    historical_apps = set(historical_apps)
    plans = {target: set(loader.graph.forwards_plan(target)) for target in historical}
    allowed = set().union(*plans.values())
    for target, plan in plans.items():
        conflicts = sorted(node for node in allowed - plan if node[0] == target[0])
        if conflicts:
            required = ", ".join(".".join(node) for node in conflicts)
            raise ValueError(
                f"Incompatible historical target {'.'.join(target)}: "
                f"requested targets require {required} beyond its historical state."
            )
    targets = []
    for node in loader.graph.leaf_nodes():
        if node[0] in historical_apps:
            continue
        dependencies = {
            migration
            for migration in loader.graph.forwards_plan(node)
            if migration[0] in historical_apps
        }
        if dependencies <= allowed:
            targets.append(node)
    return targets + list(historical)
