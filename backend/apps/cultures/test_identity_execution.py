"""Synthetic execution-boundary checks; never connect to PostgreSQL."""

import copy
import datetime as dt
import os
from contextlib import contextmanager
from unittest import skipUnless
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db import connection
from django.test import SimpleTestCase, TestCase

from apps.cultures.historical_2026 import identity_execution as execution
from apps.cultures.models import Box


class SQLiteExecutionTests(TestCase):
    def test_fixed_binding_and_nested_testcase(self):
        with execution.session() as first:
            self.assertEqual(first["mode"], "sqlite")
            self.assertEqual(len(first["target_hash"]), 64)
            self.assertEqual(Box.objects.count(), 0)
        with execution.session(apply=True) as second:
            self.assertEqual(first, second)

    def test_preparation_uses_same_stable_target_binding(self):
        with execution.session() as identity_binding:
            pass
        with execution.session(operation=execution.PREPARATION_OPERATION) as preparation_binding:
            self.assertEqual(identity_binding, preparation_binding)

    def test_plan_rejects_orm_write(self):
        with execution.session():
            with self.assertRaisesMessage(ValueError, "forbid SQL writes"):
                Box.objects.all().update(notes="forbidden")

    def test_plan_rejects_raw_writes_and_disguises(self):
        for sql in ("DELETE FROM cultures_box", "WITH x AS (SELECT 1) DELETE FROM cultures_box",
                    "PRAGMA user_version=1", "/* SELECT */ DELETE FROM cultures_box",
                    "SELECT 1; DELETE FROM cultures_box"):
            with self.subTest(sql=sql), execution.session():
                with self.assertRaisesMessage(ValueError, "forbid SQL writes"):
                    with connection.cursor() as cursor:
                        cursor.execute(sql)

    def test_guard_is_removed_on_exit(self):
        with execution.session():
            pass
        with connection.cursor() as cursor:
            cursor.execute("CREATE TEMP TABLE identity_execution_test (id integer)")
            cursor.execute("DROP TABLE identity_execution_test")


class BoundaryTests(SimpleTestCase):
    def setUp(self):
        self.target = {
            "schema": execution.TARGET_SCHEMA, "machine_id": "a" * 32,
            "configured_host": "/var/run/postgresql", "configured_port": "5433",
            "database": "example_app", "role": "example_app", "server_address": None,
            "server_port": None, "database_oid": 123, "role_oid": 456,
            "postmaster_started_at": "2026-10-27T10:00:00.000000Z",
            "server_version_num": 180001, "connection_mode": "unix", "socket_uid": 100,
            "socket_gid": 101,
        }
        self.config = {
            "ENGINE": "django.db.backends.postgresql", "HOST": "/var/run/postgresql",
            "PORT": "5433", "NAME": "example_app", "USER": "example_app", "PASSWORD": "",
            "OPTIONS": {"sslmode": "prefer"}, "AUTOCOMMIT": True,
        }
        self.runtime_settings = SimpleNamespace(SETTINGS_MODULE="config.settings", DEBUG=False,
                                        DATABASE_ROUTERS=[], DATABASES={"default": self.config})
        self.db = MagicMock(settings_dict=self.config, in_atomic_block=False, connection=None)
        self.db.autocommit = True
        self.stack = []
        for item in (patch.object(execution, "settings", self.runtime_settings),
                     patch.object(execution, "connection", self.db),
                     patch.dict(execution.os.environ, {}, clear=True)):
            item.start()
            self.addCleanup(item.stop)

    def _authorization(self, *, operation=execution.OPERATION, target_strain_id=2):
        now = dt.datetime.now(dt.UTC)
        self.scope = {"organization_id": 1, "family": "CLA", "target_strain_id": target_strain_id,
                      "actor_id": 3, "plan_hash": "b" * 64}
        return {"schema": execution.AUTHORIZATION_SCHEMA, "operation": operation,
                **self.scope, "target_hash": execution.fingerprint(self.target),
                "authorized_at": (now - dt.timedelta(minutes=1)).isoformat(),
                "expires_at": (now + dt.timedelta(minutes=30)).isoformat(),
                "backup": {"created_at": (now - dt.timedelta(minutes=2)).isoformat(),
                           "restoration_tested": True,
                           "archive": {"path": "/private/backup.dump", "sha256": "c" * 64, "size_bytes": 123}}}

    def test_config_validation_is_connection_free(self):
        with patch.object(execution, "_target", return_value=self.target), \
                patch.object(execution, "socket_guard") as guard:
            self.assertEqual(execution.require_database(), "production")
        self.db.ensure_connection.assert_not_called()
        self.db.cursor.assert_not_called()
        self.db.get_autocommit.assert_not_called()
        guard.assert_called_once_with(self.target)

    def test_inherited_libpq_and_unsafe_config_refused_before_connect(self):
        for key, value in (("PASSWORD", "not-a-real-secret"), ("OPTIONS", {"service": "redirect"}),
                           ("ENGINE", "django.db.backends.sqlite3"), ("AUTOCOMMIT", False)):
            with self.subTest(key=key), patch.dict(self.config, {key: value}):
                with self.assertRaises(ValueError):
                    execution.require_database()
        for name in ("PGHOST", "pgservice", "PGPASSFILE"):
            with self.subTest(name=name), patch.dict(execution.os.environ, {name: "untrusted"}):
                with self.assertRaisesMessage(ValueError, "PG*"):
                    execution.require_database()
        self.db.cursor.assert_not_called()

    def test_no_arbitrary_test_or_debug_bypass(self):
        for module in ("config.test_settings", "untrusted_settings", "ht_disposable_settings"):
            self.runtime_settings.SETTINGS_MODULE = module
            with self.assertRaises(ValueError):
                execution.require_database()
        self.runtime_settings.SETTINGS_MODULE = "config.settings"
        self.runtime_settings.DEBUG = True
        with self.assertRaises(ValueError):
            execution.require_database()

    def test_root_document_uses_own_pinned_path(self):
        with patch.object(execution.artifacts, "assert_unprivileged_runtime") as runtime, \
                patch.object(execution.artifacts, "_read_json", return_value={}) as reader:
            execution._root_document(execution.TARGET_PATH)
        runtime.assert_called_once_with()
        reader.assert_called_once_with("/etc/polypbase/cla-sme-production-target.json", {0}, False)

    def test_target_rejects_tcp_wrong_schema_and_wrong_version(self):
        for key, value in (("connection_mode", "tcp"), ("schema", "pca_target_v3"),
                           ("server_version_num", 170005), ("configured_host", "localhost"),
                           ("configured_port", "5433,5432"), ("socket_uid", True)):
            target = {**self.target, key: value}
            with self.subTest(key=key), patch.object(execution, "_root_document", return_value=target), \
                    patch.object(execution.Path, "read_text", return_value="a" * 32):
                with self.assertRaises(ValueError):
                    execution._target()

    def test_authorization_checks_archive_under_guard(self):
        doc = self._authorization()
        events = []

        @contextmanager
        def protected(path):
            events.append("enter")
            yield
            events.append("exit")

        with patch.object(execution, "_root_document", side_effect=[doc, doc, self.target]), \
                patch.object(execution.artifacts, "protected_file", side_effect=protected), \
                patch.object(execution.database_backup, "verify_archive", side_effect=lambda *a, **k: events.append(k)):
            self.assertEqual(execution._authorization(self.target, self.scope), doc)
        self.assertEqual(events, ["enter", {"client_major": 18}, "exit"])

    def test_authorization_rejects_wrong_scope_hash_times_restore_and_extra_keys(self):
        original = self._authorization()
        invalid = [
            {**original, "actor_id": 99}, {**original, "organization_id": True},
            {**original, "plan_hash": "d" * 64}, {**original, "target_hash": "d" * 64},
            {**original, "authorized_at": "2026-10-27T10:00:00"},
            {**original, "expires_at": (dt.datetime.now(dt.UTC) + dt.timedelta(hours=2)).isoformat()},
            {**original, "backup": {**original["backup"], "restoration_tested": False}},
            {**original, "extra": True},
        ]
        for doc in invalid:
            with self.subTest(doc=doc), patch.object(execution, "_root_document", return_value=doc), \
                    patch.object(execution.database_backup, "verify_archive") as archive:
                with self.assertRaises(ValueError):
                    execution._authorization(self.target, self.scope)
                archive.assert_not_called()

    def test_effective_connection_redirect_refused_before_identity_sql(self):
        self.db.connection = SimpleNamespace(info=SimpleNamespace(
            host="localhost", port=5433, dbname="example_app", user="example_app", hostaddr="127.0.0.1"))
        with self.assertRaisesMessage(ValueError, "Effective connection"):
            execution._verify_production(self.target)
        self.db.cursor.assert_not_called()

    def _session_patches(self, *, apply=False, changed=False,
                         operation=execution.OPERATION, target_strain_id=2):
        events = self.stack

        @contextmanager
        def guard(target):
            events.append("socket-enter")
            yield
            events.append("socket-exit")

        @contextmanager
        def atomic(**kwargs):
            events.append("begin")
            try:
                yield
            except Exception:
                events.append("rollback")
                raise
            else:
                events.append("commit")

        self.db.cursor.return_value.__enter__.return_value.execute.side_effect = lambda sql: events.append(sql)
        doc = self._authorization(operation=operation, target_strain_id=target_strain_id)
        other = copy.deepcopy(doc)
        if changed:
            other["expires_at"] = dt.datetime.now(dt.UTC).isoformat()
        patches = [patch.object(execution, "require_database", return_value="production"),
                   patch.object(execution, "_target", return_value=self.target),
                   patch.object(execution, "_verify_production", side_effect=lambda t: events.append("identity")),
                   patch.object(execution, "socket_guard", side_effect=guard),
                   patch.object(execution.transaction, "atomic", side_effect=atomic),
                   patch.object(execution, "_authorization", side_effect=[doc, other])]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return dict(apply=apply, organization_id=1, family="CLA", target_strain_id=target_strain_id,
                    actor_id=3, expected_plan_hash="b" * 64, operation=operation)

    def test_plan_sets_read_only_before_any_identity_and_guard_closes_before_commit(self):
        kwargs = self._session_patches()
        with execution.session(**kwargs) as binding:
            self.assertEqual(binding, {"mode": "production", "target_hash": execution.fingerprint(self.target)})
            self.stack.append("business-read")
        self.assertEqual(self.stack[:3], ["socket-enter", "begin",
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"])
        self.assertLess(self.stack.index("identity"), self.stack.index("business-read"))
        self.assertEqual(self.stack[-2:], ["socket-exit", "commit"])
        self.assertFalse(any("FOR UPDATE" in s for s in self.stack))

    def test_preparation_allows_missing_target_and_binds_operation_on_both_checks(self):
        kwargs = self._session_patches(apply=True, operation=execution.PREPARATION_OPERATION,
                                       target_strain_id=None)
        with execution.session(**kwargs) as binding:
            self.assertEqual(binding["target_hash"], execution.fingerprint(self.target))
        self.assertEqual(execution._authorization.call_count, 2)
        for call in execution._authorization.call_args_list:
            self.assertEqual(call.kwargs, {"operation": execution.PREPARATION_OPERATION})
            self.assertIsNone(call.args[1]["target_strain_id"])
        self.assertEqual(self.stack[-1], "commit")

    def test_identity_still_requires_target_and_preparation_still_requires_actor_and_family(self):
        kwargs = self._session_patches(apply=True)
        invalid = [{**kwargs, "target_strain_id": None},
                   {**kwargs, "operation": execution.PREPARATION_OPERATION, "target_strain_id": False},
                   {**kwargs, "operation": execution.PREPARATION_OPERATION, "target_strain_id": 0},
                   {**kwargs, "operation": execution.PREPARATION_OPERATION, "target_strain_id": None, "actor_id": None},
                   {**kwargs, "operation": execution.PREPARATION_OPERATION, "target_strain_id": None, "actor_id": True},
                   {**kwargs, "operation": execution.PREPARATION_OPERATION, "target_strain_id": None, "family": ""}]
        for arguments in invalid:
            with self.subTest(arguments=arguments), self.assertRaisesMessage(ValueError, "explicit scope"):
                with execution.session(**arguments):
                    self.fail("must not yield")
        execution._authorization.assert_not_called()
        self.assertEqual(self.stack, [])

    def test_unknown_operation_fails_before_configuration_or_sql(self):
        with patch.object(execution, "require_database") as require_database:
            for operation in ("arbitrary", None, True, [], execution.PREPARATION_OPERATION + " "):
                with self.subTest(operation=operation), self.assertRaisesMessage(ValueError, "exact CLA/SME"):
                    with execution.session(operation=operation):
                        self.fail("must not yield")
            require_database.assert_not_called()
        self.db.cursor.assert_not_called()

    def test_root_authorization_cannot_be_reused_between_operations(self):
        for requested, authorized in ((execution.OPERATION, execution.PREPARATION_OPERATION),
                                      (execution.PREPARATION_OPERATION, execution.OPERATION)):
            doc = self._authorization(operation=authorized)
            with self.subTest(requested=requested), patch.object(execution, "_root_document", return_value=doc), \
                    patch.object(execution.database_backup, "verify_archive") as archive:
                with self.assertRaisesMessage(ValueError, "exact plan, target and scope"):
                    execution._authorization(self.target, self.scope, operation=requested)
                archive.assert_not_called()

    def test_preparation_authorization_accepts_explicit_null_target(self):
        doc = self._authorization(operation=execution.PREPARATION_OPERATION, target_strain_id=None)
        with patch.object(execution, "_root_document", side_effect=[doc, doc, self.target]), \
                patch.object(execution.artifacts, "protected_file"), \
                patch.object(execution.database_backup, "verify_archive"):
            self.assertEqual(execution._authorization(self.target, self.scope,
                             operation=execution.PREPARATION_OPERATION), doc)

    def test_apply_rechecks_authorization_before_commit(self):
        kwargs = self._session_patches(apply=True, changed=True)
        with self.assertRaisesMessage(ValueError, "authorization changed"):
            with execution.session(**kwargs):
                self.stack.append("business-write")
        self.assertIn("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE", self.stack)
        self.assertEqual(self.stack[-1], "rollback")
        self.assertNotIn("commit", self.stack)

    def test_nested_postgresql_refused_before_transaction(self):
        kwargs = self._session_patches()
        self.db.in_atomic_block = True
        with self.assertRaisesMessage(ValueError, "owned outer transaction"):
            with execution.session(**kwargs):
                self.fail("must not yield")
        self.assertEqual(self.stack, [])

    def test_socket_replacement_failure_rolls_back_before_commit(self):
        kwargs = self._session_patches(apply=True)

        @contextmanager
        def replaced(target):
            yield
            raise ValueError("socket replaced")

        with patch.object(execution, "socket_guard", side_effect=replaced):
            with self.assertRaisesMessage(ValueError, "socket replaced"):
                with execution.session(**kwargs):
                    pass
        self.assertEqual(self.stack[-1], "rollback")
        self.assertNotIn("commit", self.stack)

    def test_missing_authorization_refused_before_transaction(self):
        kwargs = self._session_patches(apply=True)
        with patch.object(execution, "_authorization", side_effect=ValueError("not authorized")):
            with self.assertRaisesMessage(ValueError, "not authorized"):
                with execution.session(**kwargs):
                    pass
        self.assertEqual(self.stack, [])

    def test_disposable_live_identity_must_match_offline_control_data(self):
        identity = {"system_identifier": "123456", "data_directory": "/synthetic/data", "port": "54321"}
        self.db.connection = SimpleNamespace(info=SimpleNamespace(
            host="127.0.0.1", port=54321, dbname="test_subculture_qa", user="subculture_qa", hostaddr="127.0.0.1"))
        cursor = self.db.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("123456", "/synthetic/data", "54321", 180001,
                                       "subculture_qa", "subculture_qa", ["public"], "origin", False)
        execution._verify_disposable(identity)
        for replacement in (("999999", "/synthetic/data", "54321"),
                            ("123456", "/another/data", "54321"),
                            ("123456", "/synthetic/data", "54322")):
            cursor.fetchone.return_value = (*replacement, 180001, "subculture_qa", "subculture_qa",
                                           ["public"], "origin", False)
            with self.assertRaisesMessage(ValueError, "offline identity"):
                execution._verify_disposable(identity)

    @skipUnless(os.name == "nt", "Windows path spelling regression")
    def test_disposable_accepts_windows_slashes_and_case_but_not_another_directory(self):
        identity = {"system_identifier": "123456", "data_directory": r"C:\Synthetic\Cluster\data", "port": "54321"}
        self.db.connection = SimpleNamespace(info=SimpleNamespace(
            host="127.0.0.1", port=54321, dbname="test_subculture_qa", user="subculture_qa", hostaddr="127.0.0.1"))
        cursor = self.db.cursor.return_value.__enter__.return_value
        for path in ("C:/Synthetic/Cluster/data", "c:/synthetic/cluster/data", r"C:\Synthetic\Cluster\data"):
            with self.subTest(path=path):
                cursor.fetchone.return_value = ("123456", path, "54321", 180001, "subculture_qa",
                                               "subculture_qa", ["public"], "origin", False)
                execution._verify_disposable(identity)
        cursor.fetchone.return_value = ("123456", "C:/Synthetic/Other/data", "54321", 180001,
                                       "subculture_qa", "subculture_qa", ["public"], "origin", False)
        with self.assertRaisesMessage(ValueError, "offline identity: data_directory"):
            execution._verify_disposable(identity)

    def test_disposable_mismatch_reports_only_differing_field_names(self):
        identity = {"system_identifier": "123456", "data_directory": "/synthetic/data", "port": "54321"}
        self.db.connection = SimpleNamespace(info=SimpleNamespace(
            host="127.0.0.1", port=54321, dbname="test_subculture_qa", user="subculture_qa", hostaddr="127.0.0.1"))
        cursor = self.db.cursor.return_value.__enter__.return_value
        expected = ("123456", "/synthetic/data", "54321", 180001,
                    "subculture_qa", "subculture_qa", ["public"], "origin", False)
        for index, value, field in ((0, "999999", "system_identifier"),
                                    (1, "/another/data", "data_directory"),
                                    (2, "54322", "port"), (3, 170001, "server_version_num"),
                                    (4, "other-role", "current_user"), (5, "other-role", "session_user"),
                                    (6, ["other-schema"], "schemas"), (7, "replica", "replication_role"),
                                    (8, True, "in_recovery")):
            row = list(expected)
            row[index] = value
            cursor.fetchone.return_value = row
            with self.subTest(field=field), self.assertRaises(ValueError) as error:
                execution._verify_disposable(identity)
            self.assertEqual(str(error.exception), "Live disposable cluster differs from its offline identity: " + field + ".")

    def test_production_metadata_oid_schema_origin_and_recovery_are_bound(self):
        self.db.connection = SimpleNamespace(info=SimpleNamespace(
            host="/var/run/postgresql", port=5433, dbname="example_app", user="example_app", hostaddr=""))
        expected = ("example_app", 123, "example_app", "example_app", 456, None, None,
                    self.target["postmaster_started_at"], 180001, "origin", ["public"], False, 5433)
        cursor = self.db.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = expected
        cursor.fetchall.return_value = []
        execution._verify_production(self.target)
        for index, value in ((1, 999), (4, 999), (9, "replica"), (10, ["public", "other"]), (11, True), (12, 5432)):
            row = list(expected)
            row[index] = value
            cursor.fetchone.return_value = row
            with self.assertRaisesMessage(ValueError, "root target"):
                execution._verify_production(self.target)

    def test_disposable_environment_alone_is_not_a_bypass(self):
        self.runtime_settings.SETTINGS_MODULE = "ht_disposable_settings"
        self.runtime_settings.RUNNING_TESTS = True
        self.config.update(HOST="127.0.0.1", NAME="test_subculture_qa", USER="subculture_qa",
                           OPTIONS=execution.DISPOSABLE_OPTIONS)
        with self.assertRaisesMessage(ValueError, "launcher is required"):
            execution.require_database()
        self.db.cursor.assert_not_called()
