"""Historical schema rehearsals, not PostgreSQL capability acceptance tests."""

from copy import copy

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
