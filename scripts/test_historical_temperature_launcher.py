"""Launcher regression tests: all PostgreSQL and Django subprocesses are mocked."""

import contextlib
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import historical_temperature_cluster_lease as cluster_lease
import test_historical_temperature_production as launcher


class LauncherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="polypbase-launcher-unit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / "fresh-cluster"
        self.directory.mkdir()
        self.data = self.directory / "data"
        self.pg_bin = self.root / "pg-bin"
        self.pg_bin.mkdir()
        suffix = ".exe" if launcher.os.name == "nt" else ""
        for name in ("initdb", "pg_ctl", "postgres", "createdb", "pg_dump", "pg_restore", "pg_controldata"):
            (self.pg_bin / (name + suffix)).touch()
        self.existing = self.root / "existing-cluster"
        self.existing.mkdir()
        (self.existing / "sentinel").write_text("untouched", encoding="ascii")
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(patch.object(launcher, "ROOT", self.root))
        self.enterContext(patch.object(launcher.sys, "argv", [
            "launcher", "--run", "--pg-bin", str(self.pg_bin),
        ]))
        self.enterContext(patch.dict(launcher.os.environ, {
            "PGDATA": str(self.existing), "PGHOST": "existing.invalid",
            "PGPORT": "5432", "POSTGRES_DB": "existing",
            "POLYPBASE_TEST_POSTGRES": "1", "DJANGO_SETTINGS_MODULE": "existing.settings",
        }, clear=True))
        listener = self.enterContext(patch.object(launcher.socket, "socket"))
        listener.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 65432)
        self.allocate = self.enterContext(patch.object(
            launcher.tempfile, "mkdtemp", return_value=str(self.directory)))
        self.version = self.enterContext(patch.object(
            launcher.subprocess, "check_output", side_effect=self.check_output))
        self.control_output = "Database system identifier: 123456\n"
        self.control_error = None
        self.pid_directory = self.data
        self.pid_port = 65432
        self.commands = []
        self.start_error = None
        self.init_error = None
        self.status_code = 3
        self.status_codes = []
        self.cleanup_error = None
        self.log_bytes = b"actual server diagnostic: startup failed\ninvalid byte: \xff\n"
        self.test_code = 0
        self.test_error = None
        self.run = self.enterContext(patch.object(launcher.subprocess, "run", side_effect=self.run_command))
        self.worker = self.enterContext(patch.object(
            launcher, "run_django_tests", side_effect=self.run_django_tests))
        self.real_rmtree = shutil.rmtree
        self.remove = self.enterContext(patch.object(launcher.shutil, "rmtree", side_effect=self.remove_cluster))
        self.output_before_removal = None

    def assert_clean_environment(self, env):
        self.assertNotIn("PGDATA", env)
        self.assertNotIn("PGHOST", env)
        self.assertNotIn("PGPORT", env)
        self.assertNotIn("POLYPBASE_TEST_POSTGRES", env)
        self.assertEqual(env["POSTGRES_DB"], "")
        self.assertEqual(env["PYTHON_DOTENV_DISABLED"], "1")
        self.assertNotIn("DJANGO_SETTINGS_MODULE", env)
        self.assertEqual(env["LC_ALL"], "C")
        self.assertEqual(env["LANGUAGE"], "C")

    def check_output(self, command, **kwargs):
        self.assert_clean_environment(kwargs["env"])
        self.assertTrue(kwargs["text"])
        self.assertEqual(kwargs["timeout"], 15)
        self.assertEqual(Path(command[0]).parent, self.pg_bin)
        if Path(command[0]).stem == "postgres":
            self.assertEqual(command[1:], ["--version"])
            return "postgres (PostgreSQL) 18.6\n"
        self.assertEqual(Path(command[0]).stem, "pg_controldata")
        self.assertEqual(command[1:], [str(self.data)])
        self.assertTrue(self.data.is_dir())
        if self.control_error:
            raise self.control_error
        return self.control_output

    def run_django_tests(self, argv, *, cwd, env, report, launch, timeout):
        self.assert_clean_environment(env)
        self.assertEqual(argv, [
            "manage.py", "test", "apps.measurements.test_historical_temperature_production",
            "--settings=ht_disposable_settings", "--noinput", "-v", "2",
        ])
        self.assertEqual(cwd, self.root / "backend")
        self.assertEqual(env["POLYPBASE_HISTORICAL_TEMPERATURE_DISPOSABLE_CLUSTER"], str(self.data.resolve()))
        settings = (self.directory / "ht_disposable_settings.py").read_text(encoding="utf-8")
        self.assertTrue(settings.startswith(
            "from historical_temperature_cluster_lease import require_launch, initialize_database\n"
                        "require_launch()\ninitialize_database()\n"))
        self.assertIn("TEST_RUNNER = 'historical_temperature_disposable_runner.DisposableRunner'", settings)
        self.assertEqual(env["PYTHONPATH"], launcher.os.pathsep.join(map(str, (
            self.directory, self.root / "backend", self.root / "scripts",
        ))))
        self.assertEqual(launch, cluster_lease.Launch(
            launcher.os.getpid(), str(self.data.resolve()), 65432, "123456", 12345, 1700000000))
        self.assertIsNone(cluster_lease._active_launch)
        self.assertEqual(timeout, 1800)
        self.assertEqual(report, self.root / ".local-docs" / "historical-temperature-production-tests.log")
        report.write_text("mocked Django test output\n", encoding="utf-8")
        if self.test_error:
            raise self.test_error
        return self.test_code

    def run_command(self, command, **kwargs):
        self.commands.append(command)
        self.assert_clean_environment(kwargs["env"])
        self.assertNotIn(str(self.existing), command)
        self.assertEqual(Path(command[0]).parent, self.pg_bin)
        name = Path(command[0]).stem
        if name == "pg_ctl":
            self.assertEqual(command[1:3], ["-D", str(self.data)])
        if name == "initdb":
            self.assertFalse(self.data.exists())
            self.assertEqual(command[1:3], ["-D", str(self.data)])
            if self.init_error:
                raise self.init_error
            self.data.mkdir()
        elif name == "pg_ctl" and command[-1] == "start":
            self.assertTrue((self.data / "postgresql.conf").is_file())
            config = (self.data / "postgresql.conf").read_text(encoding="ascii").splitlines()
            self.assertIn("listen_addresses='127.0.0.1'", config)
            self.assertIn("unix_socket_directories=''", config)
            self.assertIn("port=65432", config)
            self.assertEqual(command[3:5], ["-l", str(self.directory / "server.log")])
            if self.log_bytes is not None:
                (self.directory / "server.log").write_bytes(self.log_bytes)
            kwargs["stdout"].write("pg_ctl startup output\n")
            if isinstance(self.start_error, subprocess.TimeoutExpired):
                raise self.start_error
            if self.start_error:
                result = Mock(returncode=1)
                result.check_returncode.side_effect = self.start_error
                return result
            (self.data / "postmaster.pid").write_text(
                f"12345\n{self.pid_directory.resolve()}\n1700000000\n{self.pid_port}\n"
                "\n127.0.0.1\n0\nready\n", encoding="utf-8")
        elif name == "pg_ctl" and command[-1] == "status":
            if self.start_error:
                self.assertIn("PostgreSQL startup failed; server.log:", self.output.getvalue())
                self.assertNotIn("createdb", [Path(cmd[0]).stem for cmd in self.commands])
            self.assertTrue(kwargs["capture_output"])
            self.assertEqual(kwargs["timeout"], 15)
            status = self.status_codes.pop(0) if self.status_codes else self.status_code
            if isinstance(status, Exception):
                raise status
            return subprocess.CompletedProcess(command, status)
        elif name == "pg_ctl" and command[-1] == "stop":
            self.assertEqual(command[3:], ["-m", "immediate", "-w", "-t", "15", "stop"])
            self.assertEqual(kwargs["timeout"], 25)
            if self.cleanup_error:
                raise self.cleanup_error
            self.status_code = 3
        elif name == "createdb":
            self.assertEqual(command[1:], ["-h", "127.0.0.1", "-p", "65432", "-U", "subculture_qa", "subculture_qa"])
        else:
            self.fail(f"Unexpected subprocess: {command}")
        return subprocess.CompletedProcess(command, 0)

    def remove_cluster(self, directory):
        self.assertEqual(directory, self.directory)
        self.assertTrue(self.directory.is_dir())
        self.output_before_removal = self.output.getvalue()
        if self.cleanup_error:
            raise self.cleanup_error
        self.real_rmtree(directory)

    def assert_removed(self):
        self.remove.assert_called_once_with(self.directory)
        self.assertFalse(self.directory.exists())
        self.assertEqual((self.existing / "sentinel").read_text(encoding="ascii"), "untouched")
        self.allocate.assert_called_once_with(prefix="polypbase-ht-production-synthetic-")

    def assert_retained(self):
        self.remove.assert_not_called()
        self.assertTrue(self.directory.is_dir())
        self.assertEqual((self.existing / "sentinel").read_text(encoding="ascii"), "untouched")
        self.assertIn(f"retained directory: {self.directory}", self.output.getvalue())
        self.assertNotIn("stopped and removed", self.output.getvalue())

    def assert_start_failure(self, error):
        self.start_error = error
        with self.assertRaises(type(error)) as caught:
            launcher.main()
        self.assertIs(caught.exception, error)
        self.worker.assert_not_called()
        self.assertFalse(any(Path(command[0]).stem == "createdb" for command in self.commands))

    def test_nonzero_start_prints_server_log_before_cleanup(self):
        self.assert_start_failure(subprocess.CalledProcessError(1, ["pg_ctl", "start"]))
        self.assert_removed()
        self.assertIn("pg_ctl startup output", self.output_before_removal)
        self.assertIn("PostgreSQL startup failed; server.log:", self.output_before_removal)
        self.assertIn(self.log_bytes.decode("utf-8", errors="replace"), self.output_before_removal)
        self.assertEqual(self.commands[-1][-1], "status")

    def test_timeout_prints_server_log_and_stops_partial_start_before_cleanup(self):
        self.status_code = 0
        self.assert_start_failure(subprocess.TimeoutExpired(["pg_ctl", "start"], 60))
        self.assert_removed()
        self.assertIn(self.log_bytes.decode("utf-8", errors="replace"), self.output_before_removal)
        self.assertEqual([command[-1] for command in self.commands[-3:]], ["status", "stop", "status"])

    def test_missing_server_log_preserves_startup_exception_and_cleans_up(self):
        self.log_bytes = None
        self.status_code = 3
        self.assert_start_failure(subprocess.TimeoutExpired(["pg_ctl", "start"], 60))
        self.assert_removed()
        self.assertIn("Could not read or print server.log:", self.output_before_removal)

    def test_unreadable_server_log_preserves_startup_exception_and_cleans_up(self):
        read_text = Path.read_text

        def read(path, *args, **kwargs):
            if path == self.directory / "server.log":
                raise PermissionError("mocked unreadable log")
            return read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", read):
            self.assert_start_failure(subprocess.CalledProcessError(1, ["pg_ctl", "start"]))
        self.assert_removed()
        self.assertIn("Could not read or print server.log: PermissionError('mocked unreadable log')",
                      self.output_before_removal)

    def test_server_log_encoding_failure_preserves_startup_exception_and_cleans_up(self):
        write = self.output.write

        def write_ascii(text):
            text.encode("ascii")
            return write(text)

        with patch.object(self.output, "write", side_effect=write_ascii):
            self.assert_start_failure(subprocess.CalledProcessError(1, ["pg_ctl", "start"]))
        self.assert_removed()
        self.assertIn("Could not read or print server.log: UnicodeEncodeError(", self.output_before_removal)
        self.assertIn("\\ufffd", self.output_before_removal)
        self.assertTrue(self.output_before_removal.isascii())

    def test_failed_stop_preserves_startup_exception_and_retains_cluster(self):
        self.status_code = 0
        self.cleanup_error = subprocess.TimeoutExpired(["pg_ctl", "stop"], 25)
        self.assert_start_failure(subprocess.CalledProcessError(1, ["pg_ctl", "start"]))
        self.remove.assert_not_called()
        self.assertTrue(self.directory.exists())
        self.assertIn("actual server diagnostic", self.output.getvalue())
        self.assertIn("Fresh synthetic cluster cleanup failed:", self.output.getvalue())
        self.assert_retained()

    def test_inaccessible_status_retains_cluster_without_stopping(self):
        self.status_code = 4
        with self.assertRaisesRegex(RuntimeError, "pg_ctl status returned 4"):
            launcher.main()
        self.assertEqual(self.commands[-1][-1], "status")
        self.assertFalse(any(command[-1] == "stop" for command in self.commands))
        self.assert_retained()

    def test_inaccessible_status_preserves_startup_exception(self):
        self.status_code = 4
        self.log_bytes = None
        self.assert_start_failure(subprocess.TimeoutExpired(["pg_ctl", "start"], 60))
        self.assertIn("Could not read or print server.log:", self.output.getvalue())
        self.assertIn("pg_ctl status returned 4", self.output.getvalue())
        self.assertFalse(any(command[-1] == "stop" for command in self.commands))
        self.assert_retained()

    def test_unknown_status_retains_cluster_without_stopping(self):
        self.status_code = 1
        with self.assertRaisesRegex(RuntimeError, "pg_ctl status returned 1"):
            launcher.main()
        self.assertFalse(any(command[-1] == "stop" for command in self.commands))
        self.assert_retained()

    def test_nonzero_stop_retains_cluster_and_raises(self):
        self.status_code = 0
        error = subprocess.CalledProcessError(1, ["pg_ctl", "stop"])
        self.cleanup_error = error
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            launcher.main()
        self.assertIs(caught.exception, error)
        self.assertEqual([command[-1] for command in self.commands[-2:]], ["status", "stop"])
        self.assert_retained()

    def assert_unverified_stop_retained(self, status):
        self.status_codes = [0, status]
        with self.assertRaisesRegex(RuntimeError, f"pg_ctl status after stop returned {status}"):
            launcher.main()
        self.assertEqual([command[-1] for command in self.commands[-3:]], ["status", "stop", "status"])
        self.assert_retained()

    def test_successful_stop_with_running_status_retains_cluster(self):
        self.assert_unverified_stop_retained(0)

    def test_successful_stop_with_inaccessible_status_retains_cluster(self):
        self.assert_unverified_stop_retained(4)

    def test_status_timeout_retains_cluster_and_raises(self):
        error = subprocess.TimeoutExpired(["pg_ctl", "status"], 15)
        self.status_codes = [error]
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            launcher.main()
        self.assertIs(caught.exception, error)
        self.assertFalse(any(command[-1] == "stop" for command in self.commands))
        self.assertIn("TimeoutExpired", self.output.getvalue())
        self.assert_retained()

    def test_post_stop_status_timeout_preserves_django_exception(self):
        self.test_error = OSError("mocked subprocess failure")
        self.status_codes = [0, subprocess.TimeoutExpired(["pg_ctl", "status"], 15)]
        with self.assertRaises(OSError) as caught:
            launcher.main()
        self.assertIs(caught.exception, self.test_error)
        self.assertEqual([command[-1] for command in self.commands[-3:]], ["status", "stop", "status"])
        self.assertIn("TimeoutExpired", self.output.getvalue())
        self.assert_retained()

    def test_failed_removal_preserves_startup_exception(self):
        self.cleanup_error = PermissionError("mocked removal failure")
        self.assert_start_failure(subprocess.TimeoutExpired(["pg_ctl", "start"], 60))
        self.assertIn("actual server diagnostic", self.output_before_removal)
        self.assertTrue(self.directory.exists())
        self.assertIn("mocked removal failure", self.output.getvalue())

    def test_initdb_failure_never_starts_or_uses_an_existing_cluster(self):
        self.init_error = subprocess.CalledProcessError(1, ["initdb"])
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            launcher.main()
        self.assertIs(caught.exception, self.init_error)
        self.assertEqual([Path(command[0]).stem for command in self.commands], ["initdb", "pg_ctl"])
        self.assertEqual(self.commands[-1][-1], "status")
        self.assertNotIn("server.log:", self.output.getvalue())
        self.assert_removed()

    def test_initdb_identity_read_failure_never_starts_or_launches_worker(self):
        self.control_error = subprocess.CalledProcessError(1, ["pg_controldata"])
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            launcher.main()
        self.assertIs(caught.exception, self.control_error)
        self.assertEqual([Path(command[0]).stem for command in self.commands], ["initdb", "pg_ctl"])
        self.assertEqual(self.commands[-1][-1], "status")
        self.worker.assert_not_called()
        self.assert_removed()

    def test_missing_initdb_identity_never_starts_or_launches_worker(self):
        self.control_output = "Database cluster state: shut down\n"
        with self.assertRaisesRegex(RuntimeError, "Could not identify this invocation's initdb cluster"):
            launcher.main()
        self.assertEqual([Path(command[0]).stem for command in self.commands], ["initdb", "pg_ctl"])
        self.assertEqual(self.commands[-1][-1], "status")
        self.worker.assert_not_called()
        self.assert_removed()

    def test_stale_startup_identity_never_creates_database_or_launches_worker(self):
        for mismatch in ("directory", "port"):
            with self.subTest(mismatch=mismatch):
                self.pid_directory = self.existing if mismatch == "directory" else self.data
                self.pid_port = 65431 if mismatch == "port" else 65432
                self.status_code = 0
                self.commands.clear()
                self.remove.reset_mock()
                if not self.directory.exists():
                    self.directory.mkdir()
                self.allocate.reset_mock()
                with self.assertRaisesRegex(RuntimeError, "Fresh cluster startup identity mismatch"):
                    launcher.main()
                self.worker.assert_not_called()
                self.assertNotIn("createdb", [Path(command[0]).stem for command in self.commands])
                self.assertEqual([command[-1] for command in self.commands[-3:]], ["status", "stop", "status"])
                self.assert_removed()

    def test_success_returns_test_result_and_cleans_up_without_startup_diagnostics(self):
        self.status_code = 0
        self.test_code = 7
        self.assertEqual(launcher.main(), 7)
        self.worker.assert_called_once()
        self.assert_removed()
        self.assertNotIn("PostgreSQL startup failed", self.output.getvalue())
        self.assertIn("mocked Django test output", self.output.getvalue())
        self.assertEqual([command[-1] for command in self.commands[-3:]], ["status", "stop", "status"])

    def test_django_timeout_stops_and_removes_cluster(self):
        self.status_code = 0
        self.test_error = subprocess.TimeoutExpired(["manage.py", "test"], 1800)
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            launcher.main()
        self.assertIs(caught.exception, self.test_error)
        self.worker.assert_called_once()
        self.assertIn("mocked Django test output", self.output.getvalue())
        self.assert_removed()
        self.assertEqual([command[-1] for command in self.commands[-3:]], ["status", "stop", "status"])

    def test_django_exception_stops_and_removes_cluster(self):
        self.status_code = 0
        self.test_error = OSError("mocked subprocess failure")
        with self.assertRaises(OSError) as caught:
            launcher.main()
        self.assertIs(caught.exception, self.test_error)
        self.worker.assert_called_once()
        self.assertIn("mocked Django test output", self.output.getvalue())
        self.assert_removed()
        self.assertEqual([command[-1] for command in self.commands[-3:]], ["status", "stop", "status"])

    def test_cleanup_failure_without_original_exception_is_not_swallowed(self):
        self.cleanup_error = PermissionError("mocked removal failure")
        with self.assertRaises(PermissionError) as caught:
            launcher.main()
        self.assertIs(caught.exception, self.cleanup_error)
        self.assertTrue(self.directory.exists())

    def test_missing_run_opt_in_fails_before_allocation_or_subprocesses(self):
        with patch.object(launcher.sys, "argv", ["launcher", "--pg-bin", str(self.pg_bin)]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                launcher.main()
        self.assertEqual(caught.exception.code, 2)
        self.allocate.assert_not_called()
        self.version.assert_not_called()
        self.run.assert_not_called()
        self.worker.assert_not_called()
        self.remove.assert_not_called()


if __name__ == "__main__":
    unittest.main()
