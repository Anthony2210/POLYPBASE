"""Add the optional planned ISO weekday of a Box; no data is written.

SQLite cannot add a CHECK to an existing table without rebuilding it, and
rebuilding cultures_box breaks the history triggers that reference it (see
0022). On SQLite the named CHECK is therefore attached to the new column in
place, and rollback drops that column in place. PostgreSQL uses the ordinary
operations.
"""

import django.core.validators
from django.db import NotSupportedError, migrations, models

CONSTRAINT = models.CheckConstraint(
    condition=models.Q(planned_weekday__isnull=True)
    | models.Q(planned_weekday__gte=1, planned_weekday__lte=7),
    name="box_planned_weekday_iso_range",
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
        schema_editor.execute(
            f"ALTER TABLE {schema_editor.quote_name(model._meta.db_table)} "
            f"ADD COLUMN {schema_editor.quote_name(field.column)} {column_sql} "
            f"{CONSTRAINT.constraint_sql(model, schema_editor)}",
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
    ]
