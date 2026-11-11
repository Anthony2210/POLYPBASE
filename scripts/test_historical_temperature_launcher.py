"""Launcher regression tests: all PostgreSQL and Django subprocesses are mocked."""

import contextlib
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

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
        for name in ("initdb", "pg_ctl", "postgres", "createdb", "pg_dump", "pg_restore"):
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
            launcher.subprocess, "check_output", return_value="postgres (PostgreSQL) 18.6\n"))
        self.commands = []
        self.start_error = None
        self.init_error = None
        self.status_code = 3
        self.cleanup_error = None
        self.log_bytes = b"actual server diagnostic: startup failed\ninvalid byte: \xff\n"
        self.test_code = 0
        self.run = self.enterContext(patch.object(launcher.subprocess, "run", side_effect=self.run_command))
        self.real_rmtree = shutil.rmtree
        self.remove = self.enterContext(patch.object(launcher.shutil, "rmtree", side_effect=self.remove_cluster))
        self.output_before_removal = None

    def run_command(self, command, **kwargs):
        self.commands.append(command)
        env = kwargs["env"]
        self.assertNotIn("PGDATA", env)
        self.assertNotIn("PGHOST", env)
        self.assertNotIn("PGPORT", env)
        self.assertNotIn("POLYPBASE_TEST_POSTGRES", env)
        self.assertEqual(env["POSTGRES_DB"], "")
        self.assertEqual(env["PYTHON_DOTENV_DISABLED"], "1")
        self.assertNotIn(str(self.existing), command)
        if "manage.py" in command:
            self.assertEqual(kwargs["cwd"], self.root / "backend")
            self.assertEqual(env["POLYPBASE_HISTORICAL_TEMPERATURE_DISPOSABLE_CLUSTER"], str(self.data.resolve()))
            kwargs["stdout"].write("mocked Django test output\n")
            return subprocess.CompletedProcess(command, self.test_code)
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
        elif name == "pg_ctl" and command[-1] == "status":
            if self.start_error:
                self.assertIn("PostgreSQL startup failed; server.log:", self.output.getvalue())
                self.assertNotIn("createdb", [Path(cmd[0]).stem for cmd in self.commands])
            return subprocess.CompletedProcess(command, self.status_code)
        elif name == "pg_ctl" and command[-1] == "stop":
            self.assertEqual(command[3:], ["-m", "immediate", "-w", "-t", "15", "stop"])
            if self.cleanup_error:
                raise self.cleanup_error
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

    def assert_start_failure(self, error):
        self.start_error = error
        with self.assertRaises(type(error)) as caught:
            launcher.main()
        self.assertIs(caught.exception, error)
        self.assertFalse(any("manage.py" in command for command in self.commands))
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
        self.assertEqual([command[-1] for command in self.commands[-2:]], ["status", "stop"])

    def test_missing_server_log_preserves_startup_exception_and_cleans_up(self):
        self.log_bytes = None
        self.status_code = 4
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

    def test_success_returns_test_result_and_cleans_up_without_startup_diagnostics(self):
        self.status_code = 0
        self.test_code = 7
        self.assertEqual(launcher.main(), 7)
        self.assert_removed()
        self.assertNotIn("PostgreSQL startup failed", self.output.getvalue())
        self.assertIn("mocked Django test output", self.output.getvalue())
        self.assertEqual([command[-1] for command in self.commands[-2:]], ["status", "stop"])

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
        self.remove.assert_not_called()


if __name__ == "__main__":
    unittest.main()
