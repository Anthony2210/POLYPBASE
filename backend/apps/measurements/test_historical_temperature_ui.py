"""Connection-free tests for the staged local-UI temperature import.

Run with config.test_settings or config.historical_temperature_test_settings.
Nothing here connects to PostgreSQL or to any scientific database.
"""

import hashlib
import importlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, override_settings

from . import historical_temperature_apply as apply_module
from . import historical_temperature_ui as ui
from . import historical_temperature_ui_sql as sql
from .historical_temperature_apply import APPLY_CONFIRMATION, local_ui_target
from .historical_temperature_partial import APPROVED_PLAN_FINGERPRINT, APPROVED_SNAPSHOT_SHA256

SETTINGS = "config.historical_temperature_ui_local_settings"
COMMON = {"raw_directory": "unused", "snapshot": "unused"}
MEASUREMENTS = Path(__file__).resolve().parent
# Columns whose change would alter identity, authorization, institution, Box, count or week.
SENSITIVE_COLUMNS = {
    "password", "username", "email", "is_superuser", "is_staff", "is_active", "role", "is_responsable",
    "starts_on", "ends_on", "user_id", "organization_id", "box_id", "global_code", "strain_id",
    "box_number", "polyp_count", "ephyrae_count", "measured_on", "week_start", "polyp_state_revision",
    "association_status", "temperature_c", "slug", "name",
}


class SqlContractTests(SimpleTestCase):
    def test_grants_are_minimal_and_never_destructive(self):
        text = sql.render_grants("polypbase_ui_temp_runtime")
        for word in ("DELETE", "TRUNCATE", "ALL PRIVILEGES", "REFERENCES", "TRIGGER"):
            self.assertNotIn(word, text)
        self.assertNotIn("SUPERUSER", text.replace("NOSUPERUSER", ""))
        self.assertNotRegex(text, r"GRANT UPDATE ON")  # never table-wide UPDATE
        self.assertNotRegex(text, r"GRANT INSERT ON TABLE public\.(auth_user|organizations_organization"
                                  r"|accounts_organizationmembership|cultures_box|measurements_biologicalmeasurement) ")
        for table in sql.INSERT_TABLES:
            self.assertIn(f"GRANT INSERT ON TABLE public.{table} ", text)
            self.assertIn(f"{table}_id_seq", text)
        for table in sql.WATCHED_TABLES:
            self.assertIn(f"GRANT SELECT ON TABLE public.{table} ", text)

    def test_user_membership_organization_cannot_be_changed_through_the_grants(self):
        text = sql.render_grants("polypbase_ui_temp_runtime")
        updates = re.findall(r"GRANT UPDATE \((\w+)\) ON TABLE public\.(\w+) ", text)
        self.assertEqual(dict((table, column) for column, table in updates), sql.ROW_LOCK_COLUMNS)
        self.assertEqual(len(updates), len(sql.ROW_LOCK_COLUMNS))  # exactly one column per table
        for table in ("auth_user", "accounts_organizationmembership", "organizations_organization"):
            self.assertNotIn(sql.ROW_LOCK_COLUMNS[table], SENSITIVE_COLUMNS)
            self.assertNotIn(f"MAINTAIN ON TABLE public.{table} ", text)
        for column in sql.ROW_LOCK_COLUMNS.values():
            self.assertNotIn(column, SENSITIVE_COLUMNS)

    def test_row_lock_columns_are_real_model_fields(self):
        models = {model._meta.db_table: model for model in apps.get_models()}
        for table, column in sql.ROW_LOCK_COLUMNS.items():
            self.assertIn(column, {field.column for field in models[table]._meta.concrete_fields}, table)

    def test_maintain_covers_exactly_the_importers_lock_table_statement(self):
        source = (MEASUREMENTS / "historical_temperature_apply.py").read_text(encoding="utf-8")
        statement = re.search(r"LOCK TABLE ([\w, ]+) IN SHARE ROW EXCLUSIVE MODE", source)
        self.assertIsNotNone(statement)
        self.assertEqual({name.strip() for name in statement.group(1).split(",")}, set(sql.LOCKED_TABLES))
        self.assertEqual(sql.contract()["MAINTAIN"], set(sql.LOCKED_TABLES))

    def test_row_lock_tables_cover_the_services_and_the_guard_triggers_locks(self):
        source = (MEASUREMENTS / "historical_temperature_apply.py").read_text(encoding="utf-8")
        for needle, table in (("Organization.objects.select_for_update()", "organizations_organization"),
                              ("get_user_model().objects.select_for_update()", "auth_user"),
                              ("OrganizationMembership.objects.select_for_update()", "accounts_organizationmembership")):
            self.assertIn(needle, source)
            self.assertIn(table, sql.ROW_LOCK_COLUMNS)
        guards = (MEASUREMENTS / "migrations" / "0012_historical_temperature_guards.py").read_text(encoding="utf-8")
        shared = set(re.findall(r"FROM (\w+)[^;]*?FOR SHARE", guards))
        self.assertEqual(shared, {"cultures_box", "measurements_biologicalmeasurement",
                                  "measurements_historicaltemperaturereading"})
        self.assertLessEqual(shared, set(sql.ROW_LOCK_COLUMNS))
        self.assertNotIn("SECURITY DEFINER", guards.upper())  # triggers really run as the invoker

    def test_revoke_removes_everything_and_roles_are_validated(self):
        text = sql.render_revoke("polypbase_ui_temp_runtime")
        for scope in ("TABLES", "SEQUENCES", "FUNCTIONS"):
            self.assertIn(f"REVOKE ALL ON ALL {scope} IN SCHEMA public FROM polypbase_ui_temp_runtime", text)
        for bad in ("postgres", "pg_write_all_data", "Bad Role", "x; DROP ROLE y", "", None):
            with self.assertRaises(ValueError):
                sql.check_role(bad)

    def test_backend_role_grants_are_narrow_and_separate(self):
        text = sql.render_backend_grants("polypbase_ui_backend")
        self.assertIn("GRANT UPDATE (association_status) ON TABLE public.measurements_historicaltemperaturereading", text)
        for word in ("INSERT", "DELETE", "TRUNCATE", "MAINTAIN"):
            self.assertNotIn(word, text)
        self.assertEqual(text.count("GRANT UPDATE"), 1)  # a single column of the reading table
        self.assertNotIn("auth_user", text)

    def test_contract_before_migration_excludes_new_tables(self):
        before = sql.contract(migrated=False)
        for key in ("SELECT", "INSERT", "MAINTAIN"):
            self.assertFalse(before[key] & set(sql.HISTORICAL_TABLES))
        self.assertFalse(set(before["UPDATE_COLUMNS"]) & set(sql.HISTORICAL_TABLES))
        self.assertTrue(set(sql.HISTORICAL_TABLES) <= sql.contract()["INSERT"])

    def test_tables_and_sequences_match_real_models(self):
        models = {model._meta.db_table: model for model in apps.get_models()}
        for table in sql.WATCHED_TABLES:
            if table != "django_migrations":  # Django's own recorder table has no model
                self.assertIn(table, models)
        for table in sql.INSERT_TABLES:
            self.assertIn(models[table]._meta.pk.get_internal_type(), {"AutoField", "BigAutoField"})

    def test_sql_module_has_no_django_or_database_imports(self):
        source = Path(sql.__file__).read_text(encoding="utf-8")
        self.assertNotRegex(source, r"(?m)^\s*(import|from)\s+(django|psycopg)")


class PrivilegeReportTests(SimpleTestCase):
    """The effective-privilege comparison, fed with a fake catalog (no connection)."""

    def fake(self, *, table=None, update=None, insert_elsewhere=(), references=None, tables=None):
        wanted = sql.contract()
        tables_by_privilege = {
            "SELECT": set(wanted["SELECT"]) | {"auth_permission"},
            "INSERT": set(wanted["INSERT"]), "MAINTAIN": set(wanted["MAINTAIN"]),
        }
        for privilege, changes in (table or {}).items():
            tables_by_privilege[privilege] = (tables_by_privilege.get(privilege, set()) | changes.get("add", set())) - changes.get("drop", set())
        update_columns = {t: {c} for t, c in wanted["UPDATE_COLUMNS"].items()}
        for target, column in (update or {}).items():
            update_columns.setdefault(target, set()).add(column)
        existing = set(tables or wanted["SELECT"]) | {"auth_permission"}

        def _all(query, params=None):
            if "has_table_privilege" in query:
                return [(t,) for t in sorted(tables_by_privilege.get(params[0], set()))]
            if "DISTINCT" in query:
                return [(t,) for t in sorted(set(insert_elsewhere))]
            if "has_column_privilege" in query:
                rows = update_columns if params[0] == "UPDATE" else (references or {})
                return [(t, c) for t, cs in sorted(rows.items()) for c in sorted(cs)]
            return [(t,) for t in sorted(existing)]
        return patch.object(ui, "_all", side_effect=_all)

    def test_exact_contract_passes(self):
        with self.fake():
            self.assertTrue(ui.privilege_report(migrated=True)["exact_contract"])

    def test_extra_update_on_a_sensitive_column_is_a_stop(self):
        for target, column in (("auth_user", "is_active"), ("accounts_organizationmembership", "role"),
                               ("organizations_organization", "is_active"), ("cultures_box", "global_code"),
                               ("measurements_biologicalmeasurement", "polyp_count")):
            with self.fake(update={target: column}), self.assertRaisesMessage(ui.UiOperationError, f"{target}.{column}"):
                ui.privilege_report(migrated=True)

    def test_table_wide_update_delete_truncate_or_missing_maintain_is_a_stop(self):
        for privilege, changes in (("DELETE", {"add": {"cultures_box"}}), ("TRUNCATE", {"add": {"auth_user"}}),
                                   ("TRIGGER", {"add": {"audit_auditlog"}}),
                                   ("MAINTAIN", {"drop": {"cultures_box"}}), ("MAINTAIN", {"add": {"auth_user"}}),
                                   ("INSERT", {"add": {"auth_user"}}), ("INSERT", {"drop": {"audit_auditlog"}})):
            with self.fake(table={privilege: changes}), self.assertRaisesMessage(ui.UiOperationError, "privileges_differ"):
                ui.privilege_report(migrated=True)

    def test_column_level_insert_or_references_elsewhere_is_a_stop(self):
        with self.fake(insert_elsewhere=["auth_user"]), self.assertRaisesMessage(ui.UiOperationError, "auth_user"):
            ui.privilege_report(migrated=True)
        with self.fake(references={"cultures_box": {"id"}}), self.assertRaisesMessage(ui.UiOperationError, "cultures_box.id"):
            ui.privilege_report(migrated=True)

    def test_historical_tables_must_match_the_migration_state(self):
        with self.fake(tables=set(sql.contract(migrated=False)["SELECT"])), self.assertRaisesMessage(ui.UiOperationError, "migration_state_mismatch"):
            ui.privilege_report(migrated=True)


class StageA0IndependenceTests(SimpleTestCase):
    """The pre-migration check must be read-only and independent of any runtime grant."""

    def test_pre_migration_verification_never_touches_runtime_grant_or_timeout_checks(self):
        snapshot = {"identity": {"organization_id": 1}, "boxes": [], "biological_measurements": []}
        partial = {"plan_fingerprint": APPROVED_PLAN_FINGERPRINT, "summary": {}}
        forbidden = Mock(side_effect=AssertionError("runtime-only check was called"))
        with patch.object(ui, "prepare_approved_partial", return_value=(snapshot, partial)), \
                patch.object(ui, "compare_approval"), patch.object(ui, "server_identity", return_value={}), \
                patch.object(ui, "role_report", return_value={}) as role, \
                patch.object(ui, "check_migrations", return_value=list(ui.EXPECTED_PENDING)), \
                patch.object(ui, "session_report", return_value={"other_sessions": 0, "idle_in_transaction": 0}), \
                patch.object(ui, "check_baseline", return_value={}), patch.object(ui, "table_counts", return_value={}), \
                patch.object(ui, "box_revision_digest", return_value="d"), patch.object(ui, "administrators", return_value=[]), \
                patch.object(ui, "privilege_report", forbidden), patch.object(ui, "timeout_report", forbidden), \
                patch.object(ui, "check_guards", forbidden):
            record = ui.verify("raw", b"{}", migrated=False, runtime=False)
        role.assert_called_once_with(migrator=True)
        self.assertNotIn("privileges", record)
        self.assertNotIn("timeouts", record)
        self.assertFalse(record["migrated"])

    def test_stage_a_helpers_issue_only_selects_and_the_command_is_read_only(self):
        source = (MEASUREMENTS / "historical_temperature_ui.py").read_text(encoding="utf-8")
        verify_body = source[source.index("def verify("):source.index("def write_private_json")]
        self.assertNotRegex(verify_body, r"(?i)\b(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALTER|DROP|GRANT|LOCK TABLE)\b")
        command = (MEASUREMENTS / "management" / "commands" / "historical_temperature_ui_verify.py").read_text(encoding="utf-8")
        self.assertIn("ui.read_only()", command)
        self.assertNotIn("call_command", command)

    def test_post_migration_preflight_refuses_the_owner_role_flag(self):
        with self.assertRaisesMessage(CommandError, "restricted runtime role"):
            call_command("historical_temperature_ui_verify", **COMMON, output_dir="unused",
                         expect_migrated=True, migrator_role=True)


_SYNTHETIC_TARGET = ("127.0.0.1", "65432", "synthetic_temperature_ui",
                     "1234567890123456789", "synthetic_temperature_owner", "synthetic_temperature_runtime")


class ServerIdentityTests(SimpleTestCase):
    def setUp(self):
        target = patch.object(ui, "_configured_target", return_value=_SYNTHETIC_TARGET)
        target.start()
        self.addCleanup(target.stop)
    def identity(self, version, **changes):
        row = ["synthetic_temperature_ui", "127.0.0.1", 65432, version, "origin", "public"]
        for index, value in changes.items():
            row[int(index)] = value
        return patch.object(ui, "_one", side_effect=[tuple(row), (_SYNTHETIC_TARGET[3],)])

    def test_exact_postgresql_18_loopback_target_is_accepted(self):
        with self.identity(180006):
            self.assertEqual(ui.server_identity()["server_version_num"], 180006)

    def test_control_identifier_mismatch_is_refused(self):
        row = ("synthetic_temperature_ui", "127.0.0.1", 65432, 180006, "origin", "public")
        with patch.object(ui, "_one", side_effect=[row, ("9999999999999999999",)]), \
                self.assertRaisesMessage(ui.UiOperationError, "unexpected_server_identity"):
            ui.server_identity()

    def test_other_versions_databases_addresses_and_ports_are_refused(self):
        refused = [self.identity(170004), self.identity(190000), self.identity(180006, **{"0": "synthetic_foreign_database"}),
                   self.identity(180006, **{"0": "synthetic_foreign_test_database"}),
                   self.identity(180006, **{"0": "synthetic_foreign_qa_database"}),
                   self.identity(180006, **{"1": "10.0.0.5"}), self.identity(180006, **{"2": 5432}),
                   self.identity(180006, **{"4": "replica"}), self.identity(180006, **{"5": "other"})]
        for context in refused:
            with context, self.assertRaisesMessage(ui.UiOperationError, "unexpected_server_identity"):
                ui.server_identity()


class BackupRecordTests(SimpleTestCase):
    def setUp(self):
        target = patch.object(ui, "_configured_target", return_value=_SYNTHETIC_TARGET)
        target.start()
        self.addCleanup(target.stop)
    def build(self, directory, **changes):
        dump = Path(directory) / "ui.dump"
        dump.write_bytes(b"PGDMP-synthetic")
        record = {
            "database": "synthetic_temperature_ui", "host": "127.0.0.1", "port": 65432, "pg_dump_major": 18,
            "format": "custom", "dump_path": str(dump), "dump_bytes": dump.stat().st_size,
            "dump_sha256": hashlib.sha256(dump.read_bytes()).hexdigest(), "pg_restore_list_entries": 583,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "restore_verified": {"instance_port": 55441, "tables_compared": 56, "all_table_hashes_identical": True},
        }
        record.update(changes)
        path = Path(directory) / "backup.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        return path, dump

    def test_complete_fresh_record_with_matching_dump_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path, dump = self.build(directory)
            self.assertEqual(ui.check_backup_record(path)["dump_sha256"], hashlib.sha256(dump.read_bytes()).hexdigest())

    def test_missing_unverified_stale_or_foreign_evidence_is_refused(self):
        old = (datetime.now(timezone.utc) - ui.BACKUP_MAX_AGE - timedelta(days=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        bad = [{"database": "synthetic_foreign_database"}, {"port": 55439}, {"pg_dump_major": 17},
               {"format": "plain"}, {"pg_restore_list_entries": 0}, {"created_at": old}, {"created_at": future},
               {"created_at": "2026-10-18T10:00:00"},
               {"restore_verified": {"instance_port": 55441, "tables_compared": 56, "all_table_hashes_identical": False}},
               {"restore_verified": {"instance_port": 65432, "tables_compared": 56, "all_table_hashes_identical": True}},
               {"restore_verified": {"instance_port": 55441, "tables_compared": 0, "all_table_hashes_identical": True}},
               {"restore_verified": {}}]
        for changes in bad:
            with tempfile.TemporaryDirectory() as directory:
                path, _dump = self.build(directory, **changes)
                with self.assertRaises(ui.UiOperationError, msg=changes):
                    ui.check_backup_record(path)

    def test_dump_that_changed_vanished_or_lives_in_the_repository_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path, dump = self.build(directory)
            dump.write_bytes(b"PGDMP-synthetic")  # same size, same bytes: still accepted
            ui.check_backup_record(path)
            dump.write_bytes(b"PGDMP-synthetiX")  # same size, other digest
            with self.assertRaisesMessage(ui.UiOperationError, "backup_dump_sha256_differs"):
                ui.check_backup_record(path)
            dump.write_bytes(b"short")
            with self.assertRaisesMessage(ui.UiOperationError, "backup_dump_missing_or_size_differs"):
                ui.check_backup_record(path)
            dump.unlink()
            with self.assertRaisesMessage(ui.UiOperationError, "backup_dump_missing_or_size_differs"):
                ui.check_backup_record(path)
        repository = Path(ui.__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as directory:
            path, _dump = self.build(directory, dump_path=str(repository / "x.dump"))
            with self.assertRaisesMessage(ui.UiOperationError, "backup_record_invalid_or_stale"):
                ui.check_backup_record(path)


class MigrationOrderTests(SimpleTestCase):
    @override_settings(MIGRATION_MODULES={})  # load the real graph without a connection
    def test_historical_migrations_remain_in_order_before_canonical_extension(self):
        graph = MigrationLoader(None, load=True).graph
        names = [name for app, name in graph.leaf_nodes("measurements")]
        self.assertEqual(names, ["0014_measurement_canonical_evidence"])
        chain = graph.forwards_plan(("measurements", "0014_measurement_canonical_evidence"))
        expected = [node for node in chain if node in ui.EXPECTED_PENDING]
        self.assertEqual(set(expected), set(ui.EXPECTED_PENDING))
        self.assertEqual(expected[-1], ("measurements", "0014_measurement_canonical_evidence"))
        self.assertIn(ui.EXPECTED_PENDING[0], graph.node_map[ui.EXPECTED_PENDING[1]].parents)
        self.assertIn(ui.EXPECTED_PENDING[1], graph.node_map[ui.EXPECTED_PENDING[2]].parents)

    def test_expected_pending_set_covers_historical_and_canonical_migrations(self):
        self.assertEqual(len(set(ui.EXPECTED_PENDING)), 5)


class SettingsTests(SimpleTestCase):
    def load(self, **env):
        base = {key: value for key, value in os.environ.items()
                if not key.upper().startswith("PG") and not key.startswith("POLYPBASE_UI_TEMP")}
        # Base settings may have loaded application variables from dotenv. These
        # imports exercise a synthetic target, not the parent process database.
        base.update({
            "POSTGRES_DB": "",
            "POLYPBASE_TEST_POSTGRES": "0",
            "PYTHON_DOTENV_DISABLED": "1",
            "POLYPBASE_UI_TEMP_DB_HOST": "127.0.0.1",
            "POLYPBASE_UI_TEMP_DB_PORT": "65432",
            "POLYPBASE_UI_TEMP_DB_DATABASE": "synthetic_temperature_ui",
            "POLYPBASE_UI_TEMP_DB_SYSTEM_IDENTIFIER": "1234567890123456789",
            "POLYPBASE_UI_TEMP_DB_OWNER": "synthetic_temperature_owner",
            "POLYPBASE_UI_TEMP_DB_RUNTIME": "synthetic_temperature_runtime",
        })
        base.update(env)
        sys.modules.pop(SETTINGS, None)
        with patch.dict(os.environ, base, clear=True), patch("dotenv.load_dotenv"):
            try:
                return importlib.import_module(SETTINGS)
            finally:
                sys.modules.pop(SETTINGS, None)

    def test_requires_role_and_exact_stage(self):
        with self.assertRaises((ImproperlyConfigured, RuntimeError)):
            self.load()
        with self.assertRaises((ImproperlyConfigured, RuntimeError)):
            self.load(POLYPBASE_UI_TEMP_DB_USER="synthetic_temperature_runtime", POLYPBASE_UI_TEMP_STAGE="E")

    def test_explicit_synthetic_target_single_alias_and_disabled_writes_by_default(self):
        module = self.load(POLYPBASE_UI_TEMP_DB_USER="synthetic_temperature_runtime", POLYPBASE_UI_TEMP_STAGE="C")
        self.assertEqual(set(module.DATABASES), {"default"})
        self.assertEqual(module.DATABASE_ROUTERS, [])
        database = module.DATABASES["default"]
        self.assertEqual((database["HOST"], database["PORT"], database["NAME"]),
                         ("127.0.0.1", "65432", "synthetic_temperature_ui"))
        self.assertEqual(set(database["OPTIONS"]), {"sslmode", "connect_timeout"})
        self.assertFalse(module.HISTORICAL_TEMPERATURE_LOCAL_UI_APPLY_ENABLED)
        self.assertFalse(module.HISTORICAL_TEMPERATURE_UI_MIGRATE_ENABLED)

    def test_write_switches_are_stage_specific(self):
        env = {"POLYPBASE_UI_TEMP_DB_USER": "synthetic_temperature_runtime",
               "POLYPBASE_UI_TEMP_ENABLE_APPLY": "1", "POLYPBASE_UI_TEMP_ALLOW_MIGRATE": "1"}
        self.assertTrue(self.load(**env, POLYPBASE_UI_TEMP_STAGE="C").HISTORICAL_TEMPERATURE_LOCAL_UI_APPLY_ENABLED)
        self.assertFalse(self.load(**env, POLYPBASE_UI_TEMP_STAGE="D").HISTORICAL_TEMPERATURE_LOCAL_UI_APPLY_ENABLED)
        self.assertFalse(self.load(**env, POLYPBASE_UI_TEMP_STAGE="C").HISTORICAL_TEMPERATURE_UI_MIGRATE_ENABLED)
        self.assertTrue(self.load(**env, POLYPBASE_UI_TEMP_STAGE="B").HISTORICAL_TEMPERATURE_UI_MIGRATE_ENABLED)

    def test_every_libpq_variable_except_the_password_file_is_refused(self):
        env = {"POLYPBASE_UI_TEMP_DB_USER": "synthetic_temperature_runtime", "POLYPBASE_UI_TEMP_STAGE": "A"}
        self.assertEqual(set(self.load(**env, PGPASSFILE="C:/private/pgpass.conf").DATABASES), {"default"})
        for redirect in ({"PGSERVICE": "x"}, {"PGHOST": "example.org"}, {"PGDATABASE": "synthetic_foreign_database"},
                         {"PGPORT": "5432"}, {"PGHOSTADDR": "10.0.0.5"}, {"PGHOSTADDR": "127.0.0.1"},
                         {"PGOPTIONS": "-c x=y"}, {"PGCONNECT_TIMEOUT": "1"}, {"PGSSLMODE": "require"},
                         {"PGUSER": "postgres"}, {"PGPASSWORD": "x"}, {"pghost": "x"}):
            with self.assertRaises((ImproperlyConfigured, RuntimeError), msg=redirect):
                self.load(**env, **redirect)


class SettingsIsolationTests(SimpleTestCase):
    def test_inherited_database_environment_is_isolated_and_restored(self):
        inherited = {"POSTGRES_DB": "synthetic_inherited_database", "POLYPBASE_TEST_POSTGRES": "1",
                     "PGHOST": "synthetic.invalid", "PYTHON_DOTENV_DISABLED": "0"}
        import dotenv

        original_loader = dotenv.load_dotenv
        with patch.dict(os.environ, inherited):
            module = SettingsTests().load(
                POLYPBASE_UI_TEMP_DB_USER="synthetic_temperature_runtime", POLYPBASE_UI_TEMP_STAGE="C")
            self.assertEqual(module.DATABASES["default"]["NAME"], "synthetic_temperature_ui")
            for name, value in inherited.items():
                self.assertEqual(os.environ[name], value)
        self.assertIs(dotenv.load_dotenv, original_loader)

    def test_explicit_forbidden_database_variables_are_still_refused(self):
        for forbidden in ({"POSTGRES_DB": "synthetic_forbidden"}, {"POLYPBASE_TEST_POSTGRES": "1"}):
            with self.subTest(forbidden=forbidden), self.assertRaisesMessage(
                RuntimeError, "Clear inherited application and test database configuration"
            ):
                SettingsTests().load(POLYPBASE_UI_TEMP_DB_USER="synthetic_temperature_runtime",
                                     POLYPBASE_UI_TEMP_STAGE="C", **forbidden)


@override_settings(HISTORICAL_TEMPERATURE_UI_STAGE="C", HISTORICAL_TEMPERATURE_LOCAL_UI_APPLY_ENABLED=True,
                   HISTORICAL_TEMPERATURE_UI_MIGRATE_ENABLED=True)
class FailClosedCommandTests(SimpleTestCase):
    """SimpleTestCase forbids database queries, so any attempt to connect would fail loudly."""

    IMPORT = {**COMMON, "preflight": "unused", "output_dir": "unused", "backup_record": "unused",
              "actor": "someone", "acknowledged_other_sessions": 0}

    def test_non_postgresql_or_wrong_target_is_refused_before_any_query(self):
        with self.assertRaisesMessage(ui.UiOperationError, "wrong_stage_or_target"):
            ui.require_stage("C", switch="HISTORICAL_TEMPERATURE_LOCAL_UI_APPLY_ENABLED")

    @override_settings(HISTORICAL_TEMPERATURE_UI_STAGE="A")
    def test_stage_mismatch_is_refused(self):
        with self.assertRaisesMessage(ui.UiOperationError, "wrong_stage_or_target"):
            ui.require_stage("C")

    def test_import_requires_exact_confirmation_and_plan_hash(self):
        for confirmation, plan_hash in (("wrong", APPROVED_PLAN_FINGERPRINT), (APPLY_CONFIRMATION, "0" * 64)):
            with self.assertRaisesMessage(CommandError, "confirmation_or_plan_hash_mismatch"):
                call_command("historical_temperature_ui_import", **self.IMPORT, confirmation=confirmation,
                             expected_plan_hash=plan_hash)

    def test_import_still_stops_on_wrong_target_with_correct_tokens(self):
        with self.assertRaisesMessage(CommandError, "STOP before any write: wrong_stage_or_target"):
            call_command("historical_temperature_ui_import", **self.IMPORT, confirmation=APPLY_CONFIRMATION,
                         expected_plan_hash=APPROVED_PLAN_FINGERPRINT)

    def test_import_and_migration_cannot_run_without_a_backup_record(self):
        without = {k: v for k, v in self.IMPORT.items() if k != "backup_record"}
        with self.assertRaisesMessage(CommandError, "backup-record"):
            call_command("historical_temperature_ui_import", **without, confirmation=APPLY_CONFIRMATION,
                         expected_plan_hash=APPROVED_PLAN_FINGERPRINT)
        with self.assertRaisesMessage(CommandError, "backup-record"):
            call_command("historical_temperature_ui_migrate", **COMMON, confirmation=ui.MIGRATE_CONFIRMATION)

    def test_backup_is_checked_before_any_connection_when_the_target_is_right(self):
        with patch.object(ui, "require_stage"), patch.object(ui, "check_backup_record",
                                                              side_effect=ui.UiOperationError("backup_record_invalid_or_stale")), \
                patch.object(ui, "load_preflight", side_effect=AssertionError("went past the backup gate")):
            with self.assertRaisesMessage(CommandError, "backup_record_invalid_or_stale"):
                call_command("historical_temperature_ui_import", **self.IMPORT, confirmation=APPLY_CONFIRMATION,
                             expected_plan_hash=APPROVED_PLAN_FINGERPRINT)
            with self.assertRaisesMessage(CommandError, "backup_record_invalid_or_stale"):
                call_command("historical_temperature_ui_migrate", **COMMON, backup_record="unused",
                             confirmation=ui.MIGRATE_CONFIRMATION)

    def test_migration_requires_its_own_confirmation_not_the_import_token(self):
        with self.assertRaisesMessage(CommandError, "separate_migration_confirmation_required"):
            call_command("historical_temperature_ui_migrate", **COMMON, backup_record="unused",
                         confirmation=APPLY_CONFIRMATION)

    def test_every_stage_command_skips_system_checks_that_could_connect(self):
        for name in ("verify", "migrate", "import", "postcheck"):
            module = importlib.import_module(f"apps.measurements.management.commands.historical_temperature_ui_{name}")
            self.assertEqual(module.Command.requires_system_checks, [])

    def test_tokens_differ_and_expected_counts_match_the_approval(self):
        self.assertNotEqual(ui.MIGRATE_CONFIRMATION, APPLY_CONFIRMATION)
        self.assertEqual((ui.EXPECTED_READINGS, ui.EXPECTED_EVIDENCES), (40291, 40292))
        self.assertEqual(APPROVED_SNAPSHOT_SHA256, "c23ca0375aadaff9db3f6789684609abe1d518fda1f9dfab475e90857787de99")
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesMessage(ValueError, "unsafe_or_disabled_target"):
            local_ui_target()


class PreflightFileTests(SimpleTestCase):
    def record(self, **changes):
        record = {"stage": "A", "migrated": True, "generated_at": datetime.now(timezone.utc).isoformat(),
                  "plan_fingerprint": APPROVED_PLAN_FINGERPRINT, "snapshot_sha256": APPROVED_SNAPSHOT_SHA256,
                  "counts": {"a": 1}, "box_revision_digest": "d", "baseline": {"boxes": 603}}
        record.update(changes)
        return record

    def load(self, **changes):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "p.json"
            path.write_text(json.dumps(self.record(**changes)), encoding="utf-8")
            return ui.load_preflight(path)

    def test_fresh_post_migration_record_is_accepted(self):
        self.assertEqual(self.load()["plan_fingerprint"], APPROVED_PLAN_FINGERPRINT)

    def test_stale_future_pre_migration_or_foreign_records_are_refused(self):
        old = (datetime.now(timezone.utc) - ui.PREFLIGHT_MAX_AGE - timedelta(minutes=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        for changes in ({"generated_at": old}, {"generated_at": future}, {"migrated": False}, {"stage": "D"},
                        {"plan_fingerprint": "0" * 64}, {"snapshot_sha256": "0" * 64}):
            with self.assertRaises(ui.UiOperationError, msg=changes):
                self.load(**changes)

    def test_any_state_change_since_the_preflight_stops_the_import(self):
        before = self.record()
        ui.compare_preflight(before, self.record())
        for key, value in (("counts", {"a": 2}), ("box_revision_digest", "e"), ("baseline", {"boxes": 604})):
            with self.assertRaisesMessage(ui.UiOperationError, f"state_changed_since_preflight:{key}"):
                ui.compare_preflight(before, self.record(**{key: value}))

    def test_private_output_stays_outside_the_repository_and_is_never_overwritten(self):
        repository = Path(ui.__file__).resolve().parents[3]
        with self.assertRaises(ui.UiOperationError):
            ui.write_private_json(repository / ".local-docs", "x.json", {})
        with tempfile.TemporaryDirectory() as directory:
            path = ui.write_private_json(directory, "x.json", {"zero": 0})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"zero": 0})
            with self.assertRaises(ui.UiOperationError):
                ui.write_private_json(directory, "x.json", {})
            with self.assertRaises(ui.UiOperationError):
                ui.write_private_json(Path(directory) / "missing", "x.json", {})
