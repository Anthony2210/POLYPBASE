"""Provenance checks: real spawn handoff, plus isolated identity failure tests."""

from dataclasses import replace
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

import historical_temperature_cluster_lease as lease


class LaunchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="polypbase-lease-unit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / "polypbase-ht-production-synthetic-plausible" / "data"
        self.data.mkdir(parents=True)
        self.pid = self.data / "postmaster.pid"
        self.pid.write_text(f"12345\n{self.data}\n1700000000\n65432\n", encoding="utf-8")
        self.launch = lease.capture_launch(self.data, 65432, "123456789")
        self.parent = Mock(pid=os.getpid())
        self.parent.is_alive.return_value = True
        self.enterContext(patch.object(lease, "_active_launch", self.launch))
        self.enterContext(patch.object(lease.multiprocessing, "parent_process", return_value=self.parent))
        self.enterContext(patch.object(lease.os, "getppid", return_value=os.getpid()))

    def test_spawned_child_accepts_current_in_memory_launch(self):
        self.assertEqual(lease.require_launch(), self.launch)

    def test_environment_and_plausible_existing_directory_cannot_grant_authority(self):
        with patch.object(lease, "_active_launch", None), patch.dict(os.environ, {
            "POLYPBASE_HISTORICAL_TEMPERATURE_DISPOSABLE_CLUSTER": str(self.data),
            "PYTHON_DOTENV_DISABLED": "1",
        }):
            with self.assertRaisesRegex(RuntimeError, "environment markers grant no authority"):
                lease.require_launch()

    def test_no_multiprocessing_parent_is_refused(self):
        with patch.object(lease.multiprocessing, "parent_process", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "live disposable launcher child"):
                lease.require_launch()

    def test_dead_parent_or_mismatched_parent_identity_is_refused(self):
        for parent_pid, os_parent_pid, alive in [(1, os.getpid(), True),
                                                  (os.getpid(), 1, True),
                                                  (os.getpid(), os.getpid(), False)]:
            with self.subTest(parent_pid=parent_pid, os_parent_pid=os_parent_pid, alive=alive):
                self.parent.pid = parent_pid
                self.parent.is_alive.return_value = alive
                with patch.object(lease.os, "getppid", return_value=os_parent_pid):
                    with self.assertRaisesRegex(RuntimeError, "live disposable launcher child"):
                        lease.require_launch()

    def test_restarted_postmaster_or_replaced_directory_is_refused(self):
        for text in [f"54321\n{self.data}\n1700000000\n65432\n",
                     f"12345\n{self.data}\n1700000001\n65432\n",
                     f"12345\n{self.root}\n1700000000\n65432\n",
                     f"12345\n{self.data}\n1700000000\n65433\n"]:
            with self.subTest(text=text):
                self.pid.write_text(text, encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    lease.require_launch()

    def test_missing_startup_evidence_is_refused(self):
        self.pid.unlink()
        with self.assertRaises(OSError):
            lease.require_launch()

    def test_capture_refuses_invalid_system_identifier(self):
        with self.assertRaisesRegex(RuntimeError, "startup identity mismatch"):
            lease.capture_launch(self.data, 65432, "forged")

    def test_actual_server_identity_must_match_every_launch_component(self):
        good = [180006, str(self.data), "subculture_qa", "123456789", 1700000000, 12345, "subculture_qa"]
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = good
        lease.verify_server(connection)
        for index, value in [(0, 179999), (0, 190000), (1, str(self.root)),
                             (2, "other"), (3, "987654321"), (4, 1700000001), (5, 54321), (6, "other")]:
            with self.subTest(field=index, value=value):
                row = good.copy()
                row[index] = value
                connection.reset_mock()
                connection.execute.return_value.fetchone.return_value = row
                with self.assertRaisesRegex(RuntimeError, "instance identity mismatch"):
                    lease.verify_server(connection)
                self.assertEqual(connection.execute.call_count, 1)
                self.assertTrue(connection.execute.call_args.args[0].startswith("SELECT "))

    def test_failed_local_verification_prevents_even_identity_query(self):
        connection = Mock()
        with patch.object(lease, "_active_launch", None):
            with self.assertRaises(RuntimeError):
                lease.verify_server(connection)
        connection.execute.assert_not_called()

    def connection_parameters(self):
        return dict(host="127.0.0.1", port=65432, dbname="postgres", user="subculture_qa", password="")

    def test_raw_connections_require_launch_before_psycopg(self):
        with patch("psycopg.connect") as connect, patch.object(lease, "_active_launch", None):
            with self.assertRaises(RuntimeError):
                lease.connect_leased(**self.connection_parameters())
        connect.assert_not_called()

    def test_raw_connections_reject_wrong_endpoint_before_psycopg(self):
        with patch("psycopg.connect") as connect:
            for key, value in [("host", "remote.invalid"), ("port", 65433), ("password", "other")]:
                parameters = self.connection_parameters() | {key: value}
                with self.subTest(key=key), self.assertRaises(RuntimeError):
                    lease.connect_leased(**parameters)
            connect.assert_not_called()

    def test_initial_database_created_only_after_actual_instance_verification(self):
        with patch("psycopg.connect") as connect:
            connection = connect.return_value
            connection.__enter__.return_value = connection
            connection.execute.return_value.fetchone.return_value = (
                180006, str(self.data), "subculture_qa", "123456789", 1700000000, 12345, "subculture_qa",
            )
            lease.initialize_database()
            commands = [c.args[0] for c in connection.execute.call_args_list]
            self.assertEqual(len(commands), 2)
            self.assertTrue(commands[0].startswith("SELECT "))
            self.assertEqual(commands[1], "CREATE DATABASE subculture_qa")
            self.assertEqual(connect.call_args.kwargs["dbname"], "postgres")

    def test_failed_instance_verification_prevents_initial_database_creation(self):
        with patch("psycopg.connect") as connect:
            connection = connect.return_value
            connection.execute.return_value.fetchone.return_value = (
                180006, str(self.data), "subculture_qa", "old-cluster", 1700000000, 12345, "subculture_qa",
            )
            with self.assertRaisesRegex(RuntimeError, "instance identity mismatch"):
                lease.initialize_database()
            connection.close.assert_called_once_with()
            connection.__enter__.assert_not_called()
            commands = [c.args[0] for c in connection.execute.call_args_list]
            self.assertEqual(len(commands), 1)
            self.assertTrue(commands[0].startswith("SELECT "))

    def role_connections(self, connect, user="box1902_migrator", role=None):
        target, administrator = MagicMock(), MagicMock()
        connect.side_effect = [target, administrator]
        administrator.__enter__.return_value = administrator
        snapshot = (180006, 1700000000, user, role or user, 321, "synthetic_test",
                    "127.0.0.1", 55000, "127.0.0.1", 65432)
        target.info.backend_pid = 321
        target.execute.return_value.fetchone.side_effect = [snapshot, snapshot]
        administrator.execute.return_value.fetchone.side_effect = [
            (180006, str(self.data), "subculture_qa", "123456789", 1700000000, 12345, "subculture_qa"),
            ("backend-start", "synthetic_test", user, "127.0.0.1", 55000, 1700000000),
        ]
        return target, administrator, snapshot

    def test_every_nondefault_role_is_bound_to_attested_instance_without_system_grants(self):
        roles = ["polypbase", "box1902_migrator", "box1902_approver", "box1902_executor", "box1902_backup"]
        for user in roles:
            with self.subTest(user=user), patch("psycopg.connect") as connect:
                target, administrator, _ = self.role_connections(connect, user)
                result = lease.connect_leased(**(self.connection_parameters() | {"user": user}))
                self.assertIs(result, target)
                self.assertEqual(connect.call_count, 2)
                self.assertEqual(target.execute.call_count, 2)
                commands = [c.args[0] for c in target.execute.call_args_list]
                self.assertTrue(all(sql.startswith("SELECT ") for sql in commands))
                self.assertTrue(all("pg_control_system" not in sql and "pg_read_file" not in sql for sql in commands))
                administrator.execute.assert_called_with(
                    "SELECT backend_start, datname, usename, pg_catalog.host(client_addr), client_port, "
                                        "pg_catalog.pg_postmaster_start_time() "
                    "FROM pg_catalog.pg_stat_activity WHERE pid=%s AND backend_type='client backend'", [321])
                target.close.assert_not_called()
                administrator.__exit__.assert_called_once()

    def test_sql_start_timestamp_is_matched_between_connections_not_rounded_pid_file_time(self):
        with patch("psycopg.connect") as connect:
            target, administrator, snapshot = self.role_connections(connect)
            public_timestamp = 1700000001
            snapshot = snapshot[:1] + (public_timestamp,) + snapshot[2:]
            target.execute.return_value.fetchone.side_effect = [snapshot, snapshot]
            administrator.execute.return_value.fetchone.side_effect = [
                (180006, str(self.data), "subculture_qa", "123456789", 1700000000, 12345, "subculture_qa"),
                ("backend-start", "synthetic_test", "box1902_migrator", "127.0.0.1", 55000, public_timestamp),
            ]
            self.assertIs(lease.connect_leased(
                **(self.connection_parameters() | {"user": "box1902_migrator"}),
            ), target)

    def test_migrator_with_explicit_owner_role_is_attested_without_role_changes(self):
        with patch("psycopg.connect") as connect:
            target, _, _ = self.role_connections(connect, role="box1902_owner")
            result = lease.connect_leased(
                **(self.connection_parameters() | {"user": "box1902_migrator", "options": "-c role=box1902_owner"}),
                expected_role="box1902_owner",
            )
            self.assertIs(result, target)
            self.assertTrue(all(c.args[0].startswith("SELECT ") for c in target.execute.call_args_list))

    def test_wrong_instance_closes_connections_for_every_login_before_any_mutation(self):
        for user in ["subculture_qa", "polypbase", "box1902_migrator", "box1902_approver",
                     "box1902_executor", "box1902_backup"]:
            with self.subTest(user=user), patch("psycopg.connect") as connect:
                target, administrator, _ = self.role_connections(connect, user)
                wrong = (180006, str(self.data), "subculture_qa", "another-instance", 1700000000, 12345, "subculture_qa")
                if user == "subculture_qa":
                    connect.side_effect = [target]
                    target.execute.return_value.fetchone.side_effect = [wrong]
                else:
                    administrator.execute.return_value.fetchone.side_effect = [wrong]
                with self.assertRaisesRegex(RuntimeError, "instance identity mismatch"):
                    lease.connect_leased(**(self.connection_parameters() | {"user": user}))
                target.close.assert_called_once_with()
                if user != "subculture_qa":
                    administrator.close.assert_called_once_with()
                    administrator.__enter__.assert_not_called()
                for connection in (target, administrator):
                    self.assertTrue(all(c.args[0].startswith("SELECT ") for c in connection.execute.call_args_list))

    def test_missing_or_mismatched_backend_membership_is_refused(self):
        rows = [None, (None, "synthetic_test", "box1902_migrator", "127.0.0.1", 55000, 1700000000),
                ("backend-start", "other_database", "box1902_migrator", "127.0.0.1", 55000, 1700000000),
                ("backend-start", "synthetic_test", "other_login", "127.0.0.1", 55000, 1700000000),
                ("backend-start", "synthetic_test", "box1902_migrator", "127.0.0.1", 55001, 1700000000),
                ("backend-start", "synthetic_test", "box1902_migrator", "127.0.0.1", 55000, 1700000001)]
        for peer in rows:
            with self.subTest(peer=peer), patch("psycopg.connect") as connect:
                target, administrator, _ = self.role_connections(connect)
                administrator.execute.return_value.fetchone.side_effect = [
                    (180006, str(self.data), "subculture_qa", "123456789", 1700000000, 12345, "subculture_qa"), peer,
                ]
                with self.assertRaisesRegex(RuntimeError, "does not belong"):
                    lease.connect_leased(**(self.connection_parameters() | {"user": "box1902_migrator"}))
                target.close.assert_called_once_with()

    def test_wrong_role_endpoint_startup_or_libpq_backend_identity_is_refused(self):
        for index, value in [(0, 179999), (1, None), (2, "other"), (3, "box1902_owner"),
                             (4, 322), (6, "192.0.2.1"), (7, 0), (8, "192.0.2.1"), (9, 65433)]:
            with self.subTest(field=index), patch("psycopg.connect") as connect:
                target, _, snapshot = self.role_connections(connect)
                changed = list(snapshot)
                changed[index] = value
                target.execute.return_value.fetchone.side_effect = [tuple(changed)]
                with self.assertRaisesRegex(RuntimeError, "session identity mismatch"):
                    lease.connect_leased(**(self.connection_parameters() | {"user": "box1902_migrator"}))
                self.assertEqual(connect.call_count, 1)
                target.close.assert_called_once_with()

    def test_changed_connection_or_role_during_attestation_is_refused(self):
        for index, value in [(3, "box1902_owner"), (4, 322), (7, 55001)]:
            with self.subTest(field=index), patch("psycopg.connect") as connect:
                target, _, snapshot = self.role_connections(connect)
                changed = list(snapshot)
                changed[index] = value
                target.execute.return_value.fetchone.side_effect = [snapshot, tuple(changed)]
                with self.assertRaisesRegex(RuntimeError, "connection changed"):
                    lease.connect_leased(**(self.connection_parameters() | {"user": "box1902_migrator"}))
                target.close.assert_called_once_with()

    def test_restart_during_attestation_is_refused_and_both_connections_closed(self):
        with patch("psycopg.connect") as connect:
            target, administrator, _ = self.role_connections(connect)
            execute = administrator.execute

            def restarted(*args):
                self.pid.write_text(f"54321\n{self.data}\n1700000001\n65432\n", encoding="utf-8")
                return Mock(fetchone=Mock(return_value=(
                    180006, str(self.data), "subculture_qa", "123456789", 1700000000, 12345, "subculture_qa",
                )))

            execute.side_effect = restarted
            with self.assertRaisesRegex(RuntimeError, "Stale disposable cluster startup identity"):
                lease.connect_leased(**(self.connection_parameters() | {"user": "box1902_migrator"}))
            target.close.assert_called_once_with()
            administrator.close.assert_called_once_with()

    def test_unavailable_verification_or_lost_socket_never_falls_back(self):
        for stage in ("target", "administrator", "final_read"):
            with self.subTest(stage=stage), patch("psycopg.connect") as connect:
                target, administrator, snapshot = self.role_connections(connect)
                error = ConnectionError("synthetic verification unavailable")
                if stage == "target":
                    target.execute.side_effect = error
                elif stage == "administrator":
                    administrator.execute.side_effect = error
                else:
                    target.execute.return_value.fetchone.side_effect = [snapshot, error]
                with self.assertRaises(ConnectionError) as raised:
                    lease.connect_leased(**(self.connection_parameters() | {"user": "box1902_migrator"}))
                self.assertIs(raised.exception, error)
                target.close.assert_called_once_with()

    def test_self_managed_qa_suites_refuse_independent_forged_context(self):
        root = Path(lease.__file__).resolve().parents[1]
        code = (
            "import django; django.setup()\n"
            "from importlib import import_module\n"
            "from unittest.mock import patch\n"
            "cases = [('test_box_1902_authority_postgres','Box1902AuthorityPostgreSQLTests'),"
            "('test_box_1902_correction_v2_postgres','Box1902CorrectionV2PostgreSQLTests'),"
            "('test_box_1902_provisioning_postgres','Box1902ProvisioningPostgreSQLTests')]\n"
            "with patch('psycopg.connect', side_effect=AssertionError('database access forbidden')) as connect:\n"
            " for module, name in cases:\n"
            "  cls = getattr(import_module('apps.cultures.'+module),name)\n"
            "  cls.port = 65432\n"
            "  try: cls.connect()\n"
            "  except RuntimeError as error:\n"
            "   assert 'environment markers grant no authority' in str(error)\n"
            "   print(name, 'refused')\n"
            "  else: raise AssertionError('Independent raw suite was authorized')\n"
            " connect.assert_not_called()\n"
        )
        env = dict(os.environ,
                   PYTHONPATH=os.pathsep.join(map(str, (root / 'scripts', root / 'backend'))),
                   DJANGO_SETTINGS_MODULE="config.preflight_settings",
                   DJANGO_SECRET_KEY="django-insecure-disposable-lease-regression-only",
                   PYTHON_DOTENV_DISABLED="1", POLYPBASE_TEST_POSTGRES="0",
                   POLYPBASE_HISTORICAL_TEMPERATURE_DISPOSABLE_CLUSTER=str(self.data))
        result = subprocess.run([sys.executable, "-c", code], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count(" refused"), 3)

    def run_child(self, program, *, launch=None, timeout=15):
        (self.root / "manage.py").write_text(program, encoding="utf-8")
        report = self.root / "report.log"
        env = {"PYTHONPATH": str(Path(lease.__file__).parent), "PYTHON_DOTENV_DISABLED": "1"}
        # No PostgreSQL server exists in these tests. The synthetic pid file
        # exercises transport/provenance only, not SQL instance attestation.
        result = lease.run_django_tests(["manage.py"], cwd=self.root, env=env, report=report,
                                       launch=launch or self.launch, timeout=timeout)
        return result, report.read_text(encoding="utf-8")

    def test_real_spawn_handoff_and_guarded_settings_import(self):
        code = ("from historical_temperature_cluster_lease import require_launch\n"
                "print('authorized', require_launch().system_identifier, flush=True)\n")
        result, report = self.run_child(code)
        self.assertEqual(result, 0)
        self.assertIn("authorized 123456789", report)
        self.assertEqual(multiprocessing.active_children(), [])

    def test_spawn_cannot_replay_evidence_from_another_launcher(self):
        result, report = self.run_child("raise AssertionError('must not execute')\n",
                                        launch=replace(self.launch, launcher_pid=1))
        self.assertNotEqual(result, 0)
        self.assertNotIn("must not execute", report)
        self.assertEqual(multiprocessing.active_children(), [])

    def test_worker_timeout_terminates_and_reaps_only_its_child(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_child("import time\ntime.sleep(30)\n", timeout=0.2)
        self.assertEqual(multiprocessing.active_children(), [])

    def test_worker_exception_returns_failure_and_is_reaped(self):
        result, _ = self.run_child("raise RuntimeError('synthetic worker failure')\n")
        self.assertNotEqual(result, 0)
        self.assertEqual(multiprocessing.active_children(), [])

    def test_independent_process_cannot_recreate_launch_from_environment(self):
        env = dict(os.environ, PYTHONPATH=str(Path(lease.__file__).parent),
                   PYTHON_DOTENV_DISABLED="1",
                   POLYPBASE_HISTORICAL_TEMPERATURE_DISPOSABLE_CLUSTER=str(self.data))
        result = subprocess.run(
            [sys.executable, "-c", "from historical_temperature_cluster_lease import require_launch; require_launch()"],
            env=env, capture_output=True, text=True, timeout=15,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("environment markers grant no authority", result.stderr)


if __name__ == "__main__":
    unittest.main()
