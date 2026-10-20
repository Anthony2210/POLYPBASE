"""Connection-free historical target policy and simulated Linux boundary proofs."""

import copy
import inspect
import os
import stat
from contextlib import ExitStack, contextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings
from config import pca_protected_artifacts as artifacts
from config.test_pca_protected_artifacts import SimulatedLinux
from apps.cultures import pca_production as pca
from . import historical_temperature_target as production


def synthetic_target():
    return {"schema": "historical_temperature_target_v1", "machine_id": "a" * 32,
            "configured_host": "/var/run/postgresql", "configured_port": "5432",
            "database": "synthetic_history", "role": "history_import", "role_oid": 5678,
            "backup_role": "history_backup", "backup_role_oid": 5679, "database_oid": 1234,
            "server_address": None, "server_port": None, "connection_mode": "unix",
            "socket_uid": 110, "socket_gid": 115, "server_version_num": 180006,
            "postmaster_started_at": "2026-01-01T00:00:00.000000Z"}


class HistoricalAttestationBoundaryTests(SimpleTestCase):
    def setUp(self):
        self.fs = SimulatedLinux()
        self.fs.add(production.PINNED_ATTESTATION, stat.S_IFREG | 0o644, data=b'{"historical": true}')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.fs.install(self.stack)

    def tearDown(self):
        self.assertEqual(self.fs.handles, {})

    def test_dedicated_reader_and_pca_contract_remain_separate(self):
        self.assertEqual(str(inspect.signature(artifacts.read_historical_temperature_attestation)), "()")
        self.assertEqual(artifacts.PINNED_ATTESTATION, "/etc/polypbase/pca-production-target.json")
        self.assertEqual(artifacts.read_attestation(), {"target": "pinned"})
        self.assertEqual(production.read_target(production.PINNED_ATTESTATION), {"historical": True})
        for path in (artifacts.PINNED_ATTESTATION, "/srv/pca/plan.json", "/etc/polypbase/../target.json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                production.read_target(path)
        with self.assertRaises(TypeError):
            artifacts.read_historical_temperature_attestation(path="/fake")

    def test_no_fallback_to_pca_when_historical_target_missing(self):
        del self.fs.nodes[production.PINNED_ATTESTATION]
        with self.assertRaises(FileNotFoundError):
            production.read_target(production.PINNED_ATTESTATION)

    def test_root_ownership_permissions_types_and_links_at_every_level(self):
        for path in ("/", "/etc", "/etc/polypbase", production.PINNED_ATTESTATION):
            original = copy.copy(self.fs.nodes[path])
            for field, value in (("st_uid", 1000), ("st_mode", original.st_mode | 0o022),
                                 ("st_mode", stat.S_IFLNK | 0o777)):
                with self.subTest(path=path, field=field, value=value):
                    setattr(self.fs.nodes[path], field, value)
                    with self.assertRaises((ValueError, OSError)):
                        artifacts.read_historical_temperature_attestation()
                    self.fs.nodes[path] = copy.copy(original)
        self.fs.nodes[production.PINNED_ATTESTATION].st_nlink = 2
        with self.assertRaises(ValueError):
            artifacts.read_historical_temperature_attestation()

    def test_runtime_linux_unprivileged_capabilities_and_namespace_fail_closed(self):
        with patch.object(artifacts.sys, "platform", "win32"), self.assertRaises(ValueError):
            artifacts.read_historical_temperature_attestation()
        for status in (self.fs.status.replace("1000", "0"),
                       self.fs.status.replace("CapEff:\t0000000000000000", "CapEff:\t0000000000000001"),
                       self.fs.status.replace("NoNewPrivs:\t1", "NoNewPrivs:\t0")):
            original = self.fs.status
            self.fs.status = status
            with self.assertRaises(ValueError):
                artifacts.read_historical_temperature_attestation()
            self.fs.status = original
        self.fs.uid_map = "0 1000 1\n"
        with self.assertRaises(ValueError):
            artifacts.read_historical_temperature_attestation()

    def test_replacement_during_read_fails_and_closes_descriptors(self):
        def hook(event, path):
            if event == "read" and path == production.PINNED_ATTESTATION:
                self.fs.hook = lambda *args: None
                self.fs.add(path, stat.S_IFREG | 0o644, data=b'{"historical": true}')
        self.fs.hook = hook
        with self.assertRaises(ValueError):
            artifacts.read_historical_temperature_attestation()


@override_settings(SETTINGS_MODULE="config.settings", DEBUG=False,
                   HISTORICAL_TEMPERATURE_PGQA=False, DATABASE_ROUTERS=[])
class HistoricalTargetTests(SimpleTestCase):
    def setUp(self):
        self.target = synthetic_target()
        self.config = {"ENGINE": "django.db.backends.postgresql", "HOST": self.target["configured_host"],
                       "PORT": "5432", "NAME": self.target["database"], "USER": self.target["role"],
                       "PASSWORD": "", "OPTIONS": {"sslmode": "prefer"}}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {}, clear=True))
        self.stack.enter_context(self.settings(DATABASES={"default": self.config}, SETTINGS_MODULE="config.settings"))
        self.attestation = self.stack.enter_context(patch.object(
            artifacts, "read_historical_temperature_attestation", return_value=copy.deepcopy(self.target)))
        self.pca_reader = self.stack.enter_context(patch.object(
            artifacts, "read_attestation", side_effect=AssertionError("PCA must not authorize history")))
        self.stack.enter_context(patch.object(pca, "machine_id", return_value=self.target["machine_id"]))
        self.guard = self.stack.enter_context(patch.object(
            artifacts, "protected_postgresql_socket", side_effect=lambda *args: nullcontext()))
        self.django = self.stack.enter_context(patch.object(production, "connection"))
        self.django.settings_dict = self.config
        self.native = self.make_database()
        self.django.connection = self.native
        self.backup = self.make_database(backup=True)
        self.connect = self.stack.enter_context(patch.object(production.psycopg, "connect", return_value=self.backup))
        self.update_guards = self.stack.enter_context(patch.object(production, "check_update_guards"))
        self.backup_timeouts = self.stack.enter_context(patch.object(production, "check_backup_timeouts"))

    def make_database(self, backup=False):
        database = MagicMock()
        database.pgconn.used_password = False
        role = self.target["backup_role" if backup else "role"]
        database.info = SimpleNamespace(host=self.target["configured_host"], hostaddr="", port=5432,
                                        dbname=self.target["database"], user=role)
        cursor = database.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = self.identity(backup)
        cursor.fetchall.return_value = []
        return database

    def identity(self, backup=False):
        role = self.target["backup_role" if backup else "role"]
        oid = self.target["backup_role_oid" if backup else "role_oid"]
        return (self.target["database"], 1234, role, role, oid, None, None,
                self.target["postmaster_started_at"], 180006, "origin", ["public"], False, 5432)

    def test_valid_target_never_connects_or_uses_pca_authorization(self):
        self.assertFalse(production.require_target(self.target))
        self.django.ensure_connection.assert_not_called()
        self.connect.assert_not_called()
        self.pca_reader.assert_not_called()
        self.guard.assert_called_with(self.target["configured_host"], "5432", 110, 115)
        production.verify_target(self.target, in_snapshot=True)
        self.django.ensure_connection.assert_called_once()

    def test_incomplete_invalid_and_self_declared_targets_fail_preconnect(self):
        changes = ({"schema": "pca_target_v3"}, {"backup_role": "history_import"},
                   {"backup_role_oid": 5678}, {"backup_role_oid": True}, {"role_oid": 0},
                   {"backup_role": "owner;SET ROLE postgres"}, {"role": "bad-name"},
                   {"connection_mode": "tcp"}, {"configured_host": "/tmp/postgresql"},
                   {"server_address": "127.0.0.1"}, {"server_port": 5432}, {"socket_uid": 0},
                   {"socket_gid": None}, {"configured_port": "５４３２"}, {"configured_port": "65536"},
                   {"database": "postgres"}, {"server_version_num": 170006},
                   {"server_version_num": 190000}, {"machine_id": "invalid"},
                   {"postmaster_started_at": ""}, {"backup_role_oid": 9999},
                   {"backup_role": "other"}, {"role": "other"}, {"role_oid": 9999},
                   {"postmaster_started_at": "other-boot"})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                production.require_target({**self.target, **change})
        for target in (None, {}, {k: v for k, v in self.target.items() if k != "backup_role"},
                       {**self.target, "extra": True}):
            with self.subTest(target=target), self.assertRaises(ValueError):
                production.require_target(target)
        self.django.ensure_connection.assert_not_called()
        self.connect.assert_not_called()
        self.guard.assert_not_called()

    def test_pinned_reader_errors_propagate_before_database_access(self):
        for error in (FileNotFoundError("missing"), ValueError("unsafe permissions")):
            self.attestation.side_effect = error
            with self.assertRaises(type(error)):
                production.require_target(self.target)
        self.connect.assert_not_called()
        self.django.ensure_connection.assert_not_called()

    def test_no_pg_environment_settings_redirects_or_qa_bypass(self):
        for variable in ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGOPTIONS", "PGPASSWORD", "pguser"):
            with self.subTest(variable=variable), patch.dict(os.environ, {variable: "other"}), self.assertRaises(ValueError):
                production.require_target(self.target)
        for options in ({}, {"service": "other"}, {"sslmode": "prefer", "options": "-c role=owner"}):
            self.django.settings_dict = {**self.config, "OPTIONS": options}
            with self.assertRaises(ValueError):
                production.require_target(self.target)
        self.django.settings_dict = self.config
        for values in ({"DEBUG": True}, {"SETTINGS_MODULE": "config.test_settings"},
                       {"HISTORICAL_TEMPERATURE_PGQA": True}, {"DATABASE_ROUTERS": [object()]},
                       {"DATABASES": {"default": self.config, "backup": self.config}}):
            with self.subTest(values=values), self.settings(**values), self.assertRaises(ValueError):
                production.require_target(self.target)
        for field, value in (("USER", self.target["backup_role"]), ("HOST", "/run/postgresql"),
                             ("NAME", "other"), ("PORT", "5433"), ("ENGINE", "other"), ("PASSWORD", "forbidden")):
            self.django.settings_dict = {**self.config, field: value}
            with self.assertRaises(ValueError):
                production.require_target(self.target)
        self.django.settings_dict = self.config
        with patch.object(pca, "machine_id", return_value="b" * 32), self.assertRaises(ValueError):
            production.require_target(self.target)
        self.connect.assert_not_called()
        self.django.ensure_connection.assert_not_called()

    def test_socket_failure_is_preconnect(self):
        self.guard.side_effect = ValueError("Unsafe socket")
        with self.assertRaisesMessage(ValueError, "Unsafe socket"):
            production.verify_target(self.target)
        self.django.ensure_connection.assert_not_called()

    def test_both_live_roles_check_every_identity_field_and_effective_endpoint(self):
        for backup in (False, True):
            for index in range(13):
                database = self.make_database(backup)
                row = list(self.identity(backup))
                row[index] = "wrong"
                database.cursor.return_value.__enter__.return_value.fetchone.return_value = row
                with self.subTest(backup=backup, index=index), self.assertRaises(ValueError):
                    production._verify_live_target(self.target, database, backup=backup)
            for field in ("host", "hostaddr", "port", "dbname", "user"):
                database = self.make_database(backup)
                setattr(database.info, field, "other")
                with self.subTest(backup=backup, field=field), self.assertRaises(ValueError):
                    production._verify_live_target(self.target, database, backup=backup)
                database.cursor.assert_not_called()

    def test_update_guards_are_required_only_for_import_identity(self):
        production.verify_target(self.target)
        self.update_guards.assert_called_once_with(self.native, self.target["role"], self.target["backup_role_oid"])
        production._verify_live_target(self.target, self.backup, backup=True)
        self.assertEqual(self.update_guards.call_count, 1)
        self.update_guards.side_effect = ValueError("Missing UPDATE guard")
        with self.assertRaisesMessage(ValueError, "Missing UPDATE guard"):
            production.verify_target(self.target)

    def test_backup_timeout_refusal_precedes_snapshot_import(self):
        self.backup_timeouts.side_effect = ValueError("Backup timeout too short")
        with self.assertRaisesMessage(ValueError, "timeout too short"):
            with production.backup_database(self.target, "a-b-1"):
                self.fail("Insufficient timeout reached dump")
        self.backup_timeouts.assert_called_once_with(self.backup)
        commands = [call.args[0] for call in self.backup.cursor.return_value.__enter__.return_value.execute.call_args_list]
        self.assertNotIn("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY", commands)
        self.backup.close.assert_called_once()

    def test_password_authentication_is_refused_for_both_roles(self):
        for backup in (False, True):
            database = self.make_database(backup)
            database.pgconn.used_password = True
            with self.subTest(backup=backup), self.assertRaisesMessage(ValueError, "not a password"):
                production._verify_live_target(self.target, database, backup=backup)
            database.cursor.assert_not_called()

    def test_privileged_flags_and_memberships_refused_for_both_roles(self):
        for backup in (False, True):
            database = self.make_database(backup)
            cursor = database.cursor.return_value.__enter__.return_value
            cursor.fetchall.return_value = [("unsafe",)]
            with self.assertRaisesMessage(ValueError, "Unsafe PostgreSQL role"):
                production._verify_live_target(self.target, database, backup=backup)
            query = cursor.execute.call_args.args[0]
            for clause in ("rolsuper", "rolcreaterole", "rolcreatedb", "rolreplication", "rolbypassrls",
                           "'MEMBER'", "'pg_'", "pg_database_owner"):
                self.assertIn(clause, query)

    def test_database_or_relation_ownership_only_allowed_for_backup(self):
        cursor = self.native.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [[], [(1,)]]
        with self.assertRaisesMessage(ValueError, "must not own"):
            production.verify_target(self.target)
        query = cursor.execute.call_args.args[0]
        self.assertIn("datdba", query)
        self.assertIn("relowner", query)
        production._verify_live_target(self.target, self.backup, backup=True)
        self.assertEqual(self.backup.cursor.return_value.__enter__.return_value.execute.call_count, 2)

    def test_reused_helpers_are_unchanged(self):
        for name in ("_is_disposable_qa", "socket_guard", "read_artifact", "write_artifact", "_validate_backup"):
            self.assertIs(getattr(production, name), getattr(pca, name))

    def test_backup_explicit_peer_config_read_only_snapshot_order_no_set_role(self):
        original = copy.deepcopy(self.config)
        active = []

        @contextmanager
        def guard(*args):
            active.append(True)
            try:
                yield
            finally:
                active.pop()

        self.guard.side_effect = guard
        snapshot = "00000003-0000001B-1"
        with production.backup_database(self.target, snapshot) as config:
            self.assertTrue(active)
            self.assertEqual(config, {"ENGINE": "django.db.backends.postgresql", "HOST": self.target["configured_host"],
                                      "PORT": "5432", "NAME": "synthetic_history", "USER": "history_backup",
                                      "PASSWORD": "", "OPTIONS": {"sslmode": "disable", "passfile": "/dev/null"}})
            self.backup.rollback.assert_not_called()
            self.backup.close.assert_not_called()
        self.connect.assert_called_once_with(host=self.target["configured_host"], port="5432",
                                             dbname="synthetic_history", user="history_backup", password="", passfile="/dev/null",
                                             sslmode="disable", connect_timeout=5, autocommit=True)
        commands = [call.args[0] for call in self.backup.cursor.return_value.__enter__.return_value.execute.call_args_list]
        texts = [command if isinstance(command, str) else command.as_string() for command in commands]
        begin = texts.index("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        self.assertEqual(texts[begin - 1], "SET default_transaction_read_only = on")
        self.assertEqual(texts[begin + 1], "SET TRANSACTION SNAPSHOT '00000003-0000001B-1'")
        self.assertEqual(texts[begin + 2], pca.IDENTITY_SQL)
        self.assertFalse(any("SET ROLE" in text.upper() for text in texts))
        self.backup.rollback.assert_called_once()
        self.backup.close.assert_called_once()
        self.backup_timeouts.assert_called_once_with(self.backup)
        calls = self.backup.mock_calls
        rollback = next(index for index, call in enumerate(calls) if call[0] == "rollback")
        self.assertTrue(any(call[0].endswith(".execute") and call.args == (pca.IDENTITY_SQL,)
                            for call in calls[rollback + 1:]))
        self.assertEqual(self.config, original)
        self.django.ensure_connection.assert_not_called()
        self.django.cursor.assert_not_called()
        self.assertEqual(self.attestation.call_count, 2)
        self.assertFalse(active)

    def test_snapshot_missing_or_invalid_fails_before_connect(self):
        for snapshot in (None, "", 0, "snapshot", "00000003-0000001B-1'; SET ROLE postgres;--", "a-b-1\n"):
            with self.subTest(snapshot=snapshot), self.assertRaises(ValueError):
                with production.backup_database(self.target, snapshot):
                    self.fail("Invalid snapshot yielded")
        self.connect.assert_not_called()

    def test_backup_rechecks_pinned_target_and_server_after_rollback(self):
        cursor = self.backup.cursor.return_value.__enter__.return_value
        row = list(self.identity(True))
        row[7] = "restarted"
        cursor.fetchone.side_effect = [self.identity(True), self.identity(True), row]
        with self.assertRaises(ValueError):
            with production.backup_database(self.target, "a-b-1"):
                pass
        self.backup.rollback.assert_called_once()
        self.backup.close.assert_called_once()
        cursor.fetchone.side_effect = None
        self.attestation.side_effect = [copy.deepcopy(self.target), {**self.target, "backup_role_oid": 9999}]
        with self.assertRaisesMessage(ValueError, "pinned"):
            with production.backup_database(self.target, "a-b-1"):
                pass

    def test_cleanup_on_identity_snapshot_body_rollback_and_recheck_failure(self):
        for stage in ("identity", "snapshot", "body", "rollback", "recheck"):
            database = self.make_database(backup=True)
            self.connect.return_value = database
            cursor = database.cursor.return_value.__enter__.return_value
            if stage == "identity":
                cursor.fetchone.return_value = self.identity(False)
            if stage == "snapshot":
                def execute(command):
                    if not isinstance(command, str):
                        raise RuntimeError("snapshot no longer exists")
                cursor.execute.side_effect = execute
            if stage == "rollback":
                database.rollback.side_effect = RuntimeError("rollback failed")
            if stage == "recheck":
                cursor.fetchone.side_effect = [self.identity(True), self.identity(True), RuntimeError("read failed")]
            with self.subTest(stage=stage), self.assertRaises((ValueError, RuntimeError)):
                with production.backup_database(self.target, "a-b-1"):
                    if stage == "body":
                        raise RuntimeError("dump failed")
            database.close.assert_called_once()

    def test_post_work_environment_and_socket_replacement_fail_closed(self):
        with self.assertRaisesMessage(ValueError, "PG*"):
            with production.backup_database(self.target, "a-b-1"):
                os.environ["PGSERVICE"] = "untrusted"
        os.environ.pop("PGSERVICE")
        self.backup.close.assert_called_once()
        self.backup.reset_mock()

        @contextmanager
        def replaced_socket(*args):
            yield
            raise ValueError("socket replaced")

        # The initial preconnect guard succeeds; the long-lived guard fails on exit.
        self.guard.side_effect = [nullcontext(), replaced_socket(), nullcontext()]
        with self.assertRaisesMessage(ValueError, "socket replaced"):
            with production.backup_database(self.target, "a-b-1"):
                pass
        self.backup.close.assert_called_once()

    def test_connect_failure_never_yields(self):
        self.connect.side_effect = RuntimeError("peer authentication failed")
        with self.assertRaisesMessage(RuntimeError, "peer authentication"):
            with production.backup_database(self.target, "a-b-1"):
                self.fail("Failed connection yielded")
