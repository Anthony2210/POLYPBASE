"""Scoped CI migrations, not database identity or production authorization.

Callers must establish the disposable PostgreSQL service identity before calling
these helpers. SQLite is accepted only in memory for offline regression tests.
The command/executor integration is deliberately pinned to Django 5.2.17.
"""

from collections import Counter
from threading import Lock
from unittest.mock import patch

import django
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.commands import migrate as migrate_command
from django.db import connections
from django.db.models import AutoField, BigAutoField
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder


TARGETS = (
    ("accounts", "0008_interface_language_japanese"),
    ("admin", "0003_logentry_add_action_flag_choices"),
    ("audit", "0005_auditlog_deletion_action"),
    ("auth", "0012_alter_user_first_name_max_length"),
    ("contenttypes", "0002_remove_content_type_name"),
    ("cultures", "0021_merge_dna_and_history"),
    ("exports", "0001_initial"),
    ("measurements", "0017_bucket_probe_last_measured_index"),
    ("organizations", "0002_organization_portable_id"),
    ("sessions", "0001_initial"),
    ("sharing", "0001_initial"),
    ("taxonomy", "0009_localstrainnumbercounter"),
)
EXCLUDED = frozenset({("measurements", "0018_box_1902_preservation")})
# Frozen from MigrationLoader, not inferred from numbering or current leaves.
_APPROVED_NAMES = {
    "accounts": (
        "0001_initial", "0002_userpreference",
        "0003_alter_organizationmembership_role_and_more", "0004_authenticationthrottle",
        "0005_email_identity", "0006_organizationmembership_is_responsable",
        "0007_team_invitations", "0008_interface_language_japanese",
    ),
    "admin": ("0001_initial", "0002_logentry_remove_auto_add", "0003_logentry_add_action_flag_choices"),
    "audit": (
        "0001_initial", "0002_alter_auditlog_action",
        "0003_auditlog_edited_at_auditlog_edited_by_and_more",
        "0004_auditlog_audit_box_numeric_idx_and_more", "0005_auditlog_deletion_action",
    ),
    "auth": (
        "0001_initial", "0002_alter_permission_name_max_length", "0003_alter_user_email_max_length",
        "0004_alter_user_username_opts", "0005_alter_user_last_login_null",
        "0006_require_contenttypes_0002", "0007_alter_validators_add_error_messages",
        "0008_alter_user_username_max_length", "0009_alter_user_last_name_max_length",
        "0010_alter_group_name_max_length", "0011_update_proxy_permissions",
        "0012_alter_user_first_name_max_length",
    ),
    "contenttypes": ("0001_initial", "0002_remove_content_type_name"),
    "cultures": (
        "0001_initial", "0002_alter_box_status_alter_boxlineage_relationship_type_and_more",
        "0003_thermalzone_capacity", "0004_thermalzone_salinity_psu", "0005_boxtransfer_polyp_count",
        "0006_boxtransferimport", "0007_box_inventory_lifecycle", "0008_transfer_v2_source_package",
        "0009_portable_lineage", "0010_quantitative_subculture", "0011_protect_subculture_history",
        "0012_optional_subculture_allocations", "0013_box_movement_kind_week_links",
        "0014_box_movement_week_matches_moved_at",
        "0015_box_merged_into_box_previous_global_code_and_more", "0016_automaticboxcreationrequest",
        "0017_automatic_box_intent_lifecycle", "0018_historicalboxmovement",
        "0019_box_dna_validation", "0019_history_revision_storage", "0020_history_revision_guards",
        "0021_merge_dna_and_history",
    ),
    "exports": ("0001_initial",),
    "measurements": (
        "0001_initial", "0002_alter_biologicalmeasurement_culture_status_and_more",
        "0003_biologicalmeasurement_salinity_psu", "0004_biologicalmeasurement_unique_box_date",
        "0005_biologicalmeasurement_week_start", "0006_quantitative_subculture",
        "0007_nullable_strobila_count", "0008_observer_origin", "0009_temporal_precision",
        "0010_measurement_import_receipt", "0011_historical_temperature_storage",
        "0012_historical_temperature_guards", "0013_historical_temperature_receipt",
        "0014_measurement_canonical_evidence", "0015_iminilide_probe_collection",
        "0016_probe_sampling_interval_and_last_temperature", "0017_bucket_probe_last_measured_index",
    ),
    "organizations": ("0001_initial", "0002_organization_portable_id"),
    "sessions": ("0001_initial",),
    "sharing": ("0001_initial",),
    "taxonomy": (
        "0001_initial", "0002_speciestranslation_straintranslation",
        "0003_globalstrainidentity_strain_global_identity", "0004_strain_organization",
        "0005_biologicalprovenance_organizationprovenancecode_and_more", "0006_localstrainidentity",
        "0007_localstrainidentity_provenance_code_assignment", "0008_strain_canonical_global_identity",
        "0009_localstrainnumbercounter",
    ),
}
APPROVED = frozenset((app, name) for app, names in _APPROVED_NAMES.items() for name in names)
# Pin this integration's edges as well as its node inventory. Other migrations
# retain the existing closure checks, not a full dependency-edge manifest.
_INTEGRATION_DEPENDENCIES = {
    ("cultures", "0019_box_dna_validation"): (
        ("cultures", "0018_historicalboxmovement"), ("auth", "__first__"),
    ),
    ("cultures", "0019_history_revision_storage"): (
        ("cultures", "0018_historicalboxmovement"),
        ("organizations", "0002_organization_portable_id"), ("auth", "__first__"),
    ),
    ("cultures", "0020_history_revision_guards"): (
        ("cultures", "0019_history_revision_storage"),
    ),
    ("cultures", "0021_merge_dna_and_history"): (
        ("cultures", "0019_box_dna_validation"), ("cultures", "0020_history_revision_guards"),
    ),
}
_COMMAND_LOCK = Lock()


def _require(condition, message):
    if not condition:
        raise ImproperlyConfigured("History revision migration scope: " + message)


def _names(nodes):
    return [f"{app}.{name}" for app, name in sorted(nodes)]


def _guard(connection):
    _require(django.VERSION[:3] == (5, 2, 17), "requires Django 5.2.17.")
    _require(connection.alias == "default" and connection is connections["default"],
             "only the default connection is allowed.")
    _require(set(connections) == {"default"}, "extra database aliases are forbidden.")
    _require(connection.vendor in {"sqlite", "postgresql"}, "unsupported database backend.")
    if connection.vendor == "sqlite":
        _require(connection.settings_dict["NAME"] == ":memory:", "SQLite must be in memory.")
    _require(connection.settings_dict.get("TEST", {}).get("MIGRATE", True) is True,
             "TEST.MIGRATE must be True.")
    _require(not settings.MIGRATION_MODULES, "MIGRATION_MODULES overrides are forbidden.")
    _require(not settings.DATABASE_ROUTERS, "database routers are forbidden.")


def _validate_integration(loader):
    for key, expected in _INTEGRATION_DEPENDENCIES.items():
        migration = loader.disk_migrations[key]
        label = ".".join(key)
        _require(len(migration.dependencies) == len(expected)
                 and set(migration.dependencies) == set(expected),
                 f"integration dependencies differ for {label}.")
        _require(not migration.run_before and not migration.replaces,
                 f"integration ordering/replacement metadata differs for {label}.")
        parents = {parent.key for parent in loader.graph.node_map[key].parents}
        resolved = {loader.check_key(dependency, key[0]) for dependency in expected}
        _require(parents == resolved, f"integration graph parents differ for {label}.")
    merge = loader.disk_migrations[("cultures", "0021_merge_dna_and_history")]
    _require(merge.operations == [] and merge.initial is None and merge.atomic is True,
             "integration merge must be empty, non-initial and atomic.")


def migration_scope(loader):
    """Return JSON-safe targets, frozen closure, excluded nodes and per-app counts.

    This validates graph metadata only and does not query a database.
    """
    nodes = set(loader.graph.nodes)
    _require(set(TARGETS) <= nodes, "an explicit target is missing.")
    _require(nodes == APPROVED | EXCLUDED and set(loader.disk_migrations) == nodes,
             "migration inventory differs from the frozen graph.")
    _require(not loader.replacements, "replacement migrations require a new review.")
    _require(loader.migrated_apps == set(_APPROVED_NAMES), "migrated app inventory changed.")
    closure = {node for target in TARGETS for node in loader.graph.forwards_plan(target)}
    _require(not closure & EXCLUDED, "excluded migration is a target dependency.")
    _require(closure == APPROVED, "dependency closure differs from the approved 83 nodes.")
    _require(not loader.detect_conflicts(), "conflicting migrations are forbidden.")
    _validate_integration(loader)
    return {
        "targets": [f"{app}.{name}" for app, name in TARGETS],
        "closure": _names(closure),
        "excluded": _names(EXCLUDED),
        "counts": dict(sorted(Counter(app for app, _ in closure).items())),
    }


def _relation(field):
    if field.is_relation and (field.many_to_one or field.one_to_one):
        return (field.target_field.model._meta.db_table, field.target_field.column)
    return None


def _field_shape(field, connection):
    internal_type = field.get_internal_type()
    parameters = field.db_parameters(connection)
    owner = field.model._meta.auto_created
    if (
        owner and field.primary_key and field.auto_created
        and type(field) in (AutoField, BigAutoField)
        and any(relation.remote_field.through is field.model for relation in owner._meta.local_many_to_many)
    ):
        # StateApps reconstructs implicit M2M PKs using DEFAULT_AUTO_FIELD, while
        # runtime apps can override it (auth uses AutoField). Normalize only this
        # generated PK's width, including PostgreSQL integer/bigint SQL types;
        # its column and all other field/relationship properties stay exact.
        internal_type = "ImplicitM2MAutoField"
        parameters = {**parameters, "type": "ImplicitM2MAutoField"}
    return (
        field.name, field.column, internal_type, parameters,
        field.null, field.primary_key, field.unique, field.db_index,
        field.max_length, getattr(field, "max_digits", None), getattr(field, "decimal_places", None),
        getattr(field, "db_constraint", None), _relation(field),
    )


def _model_shapes(registry, connection):
    result = {}
    for model in registry.get_models(include_auto_created=True):
        opts = model._meta
        if opts.proxy or opts.swapped:
            continue
        m2m = {
            field.name: (
                field.m2m_db_table(), field.remote_field.through._meta.label_lower,
                field.m2m_column_name(), field.m2m_reverse_name(),
                field.remote_field.model._meta.label_lower,
            )
            for field in opts.local_many_to_many
        }
        result[opts.label_lower] = (
            opts.db_table, opts.managed,
            {field.name: _field_shape(field, connection) for field in opts.concrete_fields}, m2m,
        )
    return result


def _validate_models(loader, connection):
    _require(all(not model._meta.managed or model._meta.app_label in _APPROVED_NAMES
                 for model in apps.get_models(include_auto_created=True)),
             "managed models without approved migrations are forbidden.")
    historical = loader.project_state(nodes=list(TARGETS)).apps
    old = _model_shapes(historical, connection)
    current = _model_shapes(apps, connection)
    _require(old.keys() == current.keys(), "historical/runtime model inventory differs.")
    for label in old:
        _require(old[label] == current[label], f"historical/runtime fields differ for {label}.")
    return historical


def _recorded(connection):
    return set(MigrationRecorder(connection).applied_migrations())


def _validate_plan(plan, before):
    keys = [(migration.app_label, migration.name) for migration, _ in plan]
    _require(not any(backwards for _, backwards in plan), "backwards migration is forbidden.")
    _require(len(keys) == len(set(keys)) and set(keys) == APPROVED - before,
             "execution plan differs from the remaining approved closure.")
    return keys


def _prepare(connection):
    _guard(connection)
    executor = MigrationExecutor(connection)
    migration_scope(executor.loader)
    _validate_models(executor.loader, connection)
    before = _recorded(connection)
    _require(before <= APPROVED, "unapproved migrations are already recorded.")
    executor.loader.check_consistent_history(connection)
    return executor, before


def scoped_plan(connection):
    """Return Django (Migration, backwards) pairs for the approved remaining scope.

    Reads migration history, but performs no schema or recorder writes.
    """
    executor, before = _prepare(connection)
    plan = executor.migration_plan(list(TARGETS))
    _validate_plan(plan, before)
    return plan


def verify_schema(connection):
    """Validate historical/runtime fields and live managed tables, columns and FKs.

    Includes auto-created M2M tables. Returns JSON-safe checked table/column data;
    this is not an attestation of SQL functions, triggers or all constraints.
    """
    _guard(connection)
    loader = MigrationLoader(None)
    migration_scope(loader)
    _validate_models(loader, connection)
    models = [model for model in apps.get_models(include_auto_created=True)
              if model._meta.managed and not model._meta.proxy and not model._meta.swapped]
    checked = {}
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
        for model in models:
            opts = model._meta
            table = opts.db_table
            _require(table in tables, f"missing managed table {table}.")
            columns = {column.name for column in connection.introspection.get_table_description(cursor, table)}
            expected = {field.column for field in opts.local_concrete_fields}
            _require(columns == expected, f"live columns differ for {table}.")
            constraints = connection.introspection.get_constraints(cursor, table)
            foreign_keys = {
                (tuple(item["columns"]), tuple(item["foreign_key"]))
                for item in constraints.values() if item.get("foreign_key")
            }
            expected_fks = {
                ((field.column,), _relation(field)) for field in opts.local_concrete_fields
                if _relation(field) is not None and field.db_constraint
            }
            _require(foreign_keys == expected_fks, f"live foreign keys differ for {table}.")
            for _, (target_table, target_column) in expected_fks:
                _require(target_table in tables, f"missing FK target table {target_table}.")
                target_columns = {
                    column.name for column in connection.introspection.get_table_description(cursor, target_table)
                }
                _require(target_column in target_columns, f"missing FK target column {target_table}.{target_column}.")
            checked[table] = sorted(columns)
    return dict(sorted(checked.items()))


class _ScopedExecutor(MigrationExecutor):
    def __init__(self, connection, progress_callback, *, fresh):
        super().__init__(connection, self._progress)
        self._callback = progress_callback
        migration_scope(self.loader)
        _validate_models(self.loader, connection)
        self.before = _recorded(connection)
        _require(self.before <= APPROVED, "unapproved migrations are already recorded.")
        _require(not fresh or not self.before, "fresh execution refuses preapplied migrations.")
        self.started = []
        self.completed = []
        self.expected = []

    def _targets(self, targets):
        targets = list(targets)
        _require(len(targets) == len(set(targets)) and set(targets) in (
            set(self.loader.graph.leaf_nodes()), set(TARGETS),
        ), "unexpected migration target request.")
        return list(TARGETS)

    def migration_plan(self, targets, clean_start=False):
        if clean_start:
            return super().migration_plan(targets, clean_start=True)
        plan = super().migration_plan(self._targets(targets))
        _validate_plan(plan, self.before)
        return plan

    def _progress(self, action, migration=None, fake=False):
        if action in {"apply_start", "apply_success"}:
            _require(not fake, "fake migration callback is forbidden.")
            key = (migration.app_label, migration.name)
            if action == "apply_start":
                _require(len(self.started) == len(self.completed), "overlapping migration callbacks.")
                _require(self.expected[len(self.started):len(self.started) + 1] == [key],
                         "unexpected apply_start callback.")
                self.started.append(key)
            else:
                _require(self.started[len(self.completed):] == [key], "unexpected apply_success callback.")
                self.completed.append(key)
        _require(not action.startswith("unapply"), "reverse execution is forbidden.")
        if self._callback:
            self._callback(action, migration, fake)

    def migrate(self, targets, plan=None, state=None, fake=False, fake_initial=False):
        _require(not fake and not fake_initial, "fake execution is forbidden.")
        targets = self._targets(targets)
        expected_plan = self.migration_plan(targets)
        _require(plan is None or plan == expected_plan, "supplied plan differs from the scoped plan.")
        _require(_recorded(self.connection) == self.before, "migration history changed before execution.")
        keys = set(_validate_plan(expected_plan, self.before))
        # Django applies the selected migrations in full clean-start graph order.
        full_plan = super().migration_plan(self.loader.graph.leaf_nodes(), clean_start=True)
        self.expected = [(m.app_label, m.name) for m, _ in full_plan if (m.app_label, m.name) in keys]
        result = super().migrate(targets, plan=expected_plan, state=state, fake=False, fake_initial=False)
        _require(self.started == self.completed == self.expected, "actual callbacks do not match the plan.")
        _require(_recorded(self.connection) == APPROVED, "final recorder differs from the approved closure.")
        return result


class _ScopedCommand(migrate_command.Command):
    def __init__(self, *, fresh):
        super().__init__()
        self.fresh = fresh
        self.executor = None

    def handle(self, *args, **options):
        _require(not args and not options["app_label"] and not options["migration_name"],
                 "only default multi-app execution is supported.")
        _require(options["database"] == "default" and options["interactive"] is False,
                 "only noninteractive default database execution is supported.")
        _require(not any(options[name] for name in (
            "fake", "fake_initial", "run_syncdb", "plan", "check_unapplied", "prune",
        )), "unsupported migrate options.")
        _guard(connections["default"])

        def executor(connection, progress_callback):
            _require(self.executor is None, "multiple command executors are forbidden.")
            self.executor = _ScopedExecutor(connection, progress_callback, fresh=self.fresh)
            return self.executor

        _require(_COMMAND_LOCK.acquire(blocking=False), "concurrent scoped commands are forbidden.")
        try:
            with patch.object(migrate_command, "MigrationExecutor", executor):
                return super().handle(*args, **options)
        finally:
            _COMMAND_LOCK.release()


def migrate_scoped(connection, *, verbosity=1, fresh):
    """Apply the scope through the real migrate command and return execution evidence.

    fresh=True refuses any recorded migration. fresh=False permits only a
    consistent approved subset (e.g. a separately rehearsed 0020 reversal).
    Identity checks and database creation/destruction remain the caller's job.
    """
    _require(type(fresh) is bool, "fresh must be an explicit bool.")
    executor, before = _prepare(connection)
    _require(not fresh or not before, "fresh execution refuses preapplied migrations.")
    planned = executor.migration_plan(list(TARGETS))
    _validate_plan(planned, before)
    command = _ScopedCommand(fresh=fresh)
    call_command(command, database="default", interactive=False, verbosity=verbosity,
                 run_syncdb=False, fake=False, fake_initial=False)
    _require(command.executor is not None, "command did not create its executor.")
    after = _recorded(connection)
    _require(after == APPROVED, "recorder changed during command signals.")
    schema = verify_schema(connection)
    return {
        **migration_scope(executor.loader),
        "fresh": fresh,
        "before": _names(before),
        "after": _names(after),
        "planned": [f"{m.app_label}.{m.name}" for m, _ in planned],
        "started": [f"{app}.{name}" for app, name in command.executor.started],
        "completed": [f"{app}.{name}" for app, name in command.executor.completed],
        "schema": schema,
    }
