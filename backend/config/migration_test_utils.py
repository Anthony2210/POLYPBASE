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
