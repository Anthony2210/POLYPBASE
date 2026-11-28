"""Add the optional planned ISO weekday of a Box; no data is written.

SQLite cannot add a CHECK to an existing table without rebuilding it, and
rebuilding cultures_box breaks the history triggers that reference it (see
0022). On SQLite the named CHECK is therefore attached to the new column in
place, and rollback drops that column in place. PostgreSQL uses the ordinary
operations.

Rollback is refused while any Box has a planned weekday: dropping the column
would discard planning data. config.rollback_preflight runs the same guard
before any migration of a plan is unapplied.
"""

import django.core.validators
from django.db import NotSupportedError, migrations, models
from django.db.migrations.exceptions import IrreversibleError

# Explicit membership, not a range: SQLite would keep a fractional REAL value.
CONSTRAINT = models.CheckConstraint(
    condition=models.Q(planned_weekday__isnull=True)
    | models.Q(planned_weekday__in=[1, 2, 3, 4, 5, 6, 7]),
    name="box_planned_weekday_iso_range",
)


def refuse_planned_weekday_loss(apps, schema_editor):
    """Allow rollback only when no Box has a planned weekday."""
    connection = schema_editor.connection
    Box = apps.get_model("cultures", "Box")
    if connection.vendor == "postgresql":
        if not connection.in_atomic_block:
            raise IrreversibleError(
                "Planned weekday rollback requires an atomic migration."
            )
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_isolation")
            if cursor.fetchone()[0] != "read committed":
                raise IrreversibleError(
                    "Planned weekday rollback requires READ COMMITTED isolation."
                )
            # Exclude writers before counting; NOWAIT refuses a busy table
            # instead of waiting. The lock is held through the column drop.
            table = connection.ops.quote_name(Box._meta.db_table)
            cursor.execute(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE NOWAIT")
    count = (
        Box.objects.using(connection.alias)
        .filter(planned_weekday__isnull=False)
        .count()
    )
    if count:
        raise IrreversibleError(
            f"Rollback would discard the planned weekday of {count} box(es). "
            "Every value is kept in the audit log (event "
            "box_planned_weekday_updated). Clear them or retain migration 0026."
        )


class AddPlannedWeekdayField(migrations.AddField):
    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "sqlite":
            return super().database_forwards(
                app_label, schema_editor, from_state, to_state
            )
        model = to_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return
        field = model._meta.get_field(self.name)
        column_sql, params = schema_editor.column_sql(
            model, field, include_default=False
        )
        column = schema_editor.quote_name(field.column)
        # Same rule as CONSTRAINT, written without commas: Django's SQLite
        # introspection splits the stored column definitions on commas, so an
        # inline IN (1, 2, ...) would break every later schema read. typeof()
        # rejects a REAL such as 1.5, as the membership check does.
        check = (
            f"CONSTRAINT {schema_editor.quote_name(CONSTRAINT.name)} CHECK "
            f"({column} IS NULL OR ({column} BETWEEN 1 AND 7 "
            f"AND typeof({column}) = 'integer'))"
        )
        schema_editor.execute(
            f"ALTER TABLE {schema_editor.quote_name(model._meta.db_table)} "
            f"ADD COLUMN {column} {column_sql} {check}",
            params,
        )

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "sqlite":
            return super().database_backwards(
                app_label, schema_editor, from_state, to_state
            )
        model = from_state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return
        if not schema_editor.connection.features.can_alter_table_drop_column:
            raise NotSupportedError(
                "Planned weekday removal requires SQLite native DROP COLUMN."
            )
        field = model._meta.get_field(self.name)
        schema_editor.execute(
            f"ALTER TABLE {schema_editor.quote_name(model._meta.db_table)} "
            f"DROP COLUMN {schema_editor.quote_name(field.column)}"
        )


class AddPlannedWeekdayConstraint(migrations.AddConstraint):
    # On SQLite the column definition above already carries this constraint.
    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "sqlite":
            super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        if schema_editor.connection.vendor != "sqlite":
            super().database_backwards(app_label, schema_editor, from_state, to_state)


class Migration(migrations.Migration):
    dependencies = [
        ("cultures", "0025_merge_local_id_hardening_and_donations"),
    ]

    operations = [
        AddPlannedWeekdayField(
            model_name="box",
            name="planned_weekday",
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(7),
                ],
            ),
        ),
        AddPlannedWeekdayConstraint(model_name="box", constraint=CONSTRAINT),
        # Last operation, so its reverse runs before the constraint and column drop.
        migrations.RunPython(migrations.RunPython.noop, refuse_planned_weekday_loss),
    ]
