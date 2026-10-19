"""Backup tests use temporary files and mocked commands, never Django or a DB."""

import copy
import datetime
import hashlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from config import database_backup as backup


class DatabaseBackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.database = {
            "ENGINE": "django.db.backends.postgresql", "HOST": "test-host",
            "PORT": "5433", "USER": "test-user", "NAME": "test-db",
            "PASSWORD": "private-password", "OPTIONS": {"sslmode": "require"},
        }
        self.target = {"database": "test-db", "cluster": "test-cluster", "port": 5433}
        self.plan_hash = "a" * 64
        self.runner = patch.object(backup.subprocess, "run", side_effect=self.run_command).start()
        self.addCleanup(patch.stopall)

    def run_command(self, command, **kwargs):
        if command[0] == "/usr/bin/pg_dump":
            Path(command[command.index("--file") + 1]).write_bytes(b"archive-content")
        return subprocess.CompletedProcess(command, 0)

    def evidence(self):
        return backup.create_evidence(
            self.database, self.target, self.plan_hash, self.directory,
            snapshot="00000003-0000001B-1",
        )

    def resign(self, evidence):
        payload = {key: value for key, value in evidence.items() if key != "evidence_hash"}
        evidence["evidence_hash"] = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")).hexdigest()
        return evidence

    def test_archive_commands_environment_snapshot_and_permissions(self):
        with patch.dict(os.environ, {
            "PGHOST": "wrong-host", "PGHOSTADDR": "192.0.2.99",
            "PGSERVICE": "wrong-service",
            "PGSERVICEFILE": "wrong-file", "PGPASSFILE": "wrong-password-file",
            "PGOPTIONS": "wrong-options", "PGDATABASE": "wrong-db",
            "PGPASSWORD": "wrong-password", "PGSSLMODE": "disable",
            "KEEP_BACKUP_TEST": "keep",
        }):
            archive = backup.create_archive(
                self.database, self.directory / "new", "manual", snapshot="snapshot-1",
            )
        self.assertEqual(archive["size_bytes"], len(b"archive-content"))
        self.assertEqual(archive["sha256"], hashlib.sha256(b"archive-content").hexdigest())
        self.assertTrue(Path(archive["path"]).is_absolute())
        dump, restore = self.runner.call_args_list
        command = dump.args[0]
        for flag, value in (
            ("--host", "test-host"), ("--port", "5433"),
            ("--username", "test-user"), ("--dbname", "test-db"),
            ("--snapshot", "snapshot-1"),
        ):
            self.assertEqual(command[command.index(flag) + 1], value)
        for flag in ("--format=custom", "--no-owner", "--no-acl", "--no-password"):
            self.assertIn(flag, command)
        self.assertNotIn(self.database["PASSWORD"], repr(command))
        self.assertEqual(
            {k: v for k, v in dump.kwargs["env"].items() if k.upper().startswith("PG")},
            {"PGPASSWORD": "private-password", "PGSSLMODE": "require"},
        )
        self.assertEqual(dump.kwargs["env"]["KEEP_BACKUP_TEST"], "keep")
        self.assertFalse(any(k.upper().startswith("PG") for k in restore.kwargs["env"]))
        self.assertEqual(restore.args[0], ["/usr/bin/pg_restore", "--list", archive["path"]])
        for call in (dump, restore):
            self.assertEqual(call.kwargs["timeout"], 600)
            self.assertTrue(call.kwargs["check"])
            for stream in ("stdin", "stdout", "stderr"):
                self.assertEqual(call.kwargs[stream], subprocess.DEVNULL)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(Path(archive["path"]).stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(Path(archive["path"]).parent.stat().st_mode), 0o700)

    def test_normal_archive_has_no_snapshot_and_defaults_sslmode(self):
        self.database.pop("OPTIONS")
        backup.create_archive(self.database, self.directory, "backup")
        dump = self.runner.call_args_list[0]
        self.assertNotIn("--snapshot", dump.args[0])
        self.assertEqual(dump.kwargs["env"]["PGSSLMODE"], "prefer")

    def test_hostaddr_pins_localhost_without_changing_tls_hostname(self):
        self.database["HOST"] = "localhost"
        self.database["OPTIONS"]["sslmode"] = "verify-full"
        for index, address in enumerate(("127.0.0.1", "::1")):
            with self.subTest(address=address), patch.dict(os.environ, {
                "PGHOSTADDR": "192.0.2.99", "PGHOST": "wrong-host",
            }):
                self.runner.reset_mock()
                backup.create_archive(
                    self.database, self.directory, f"pinned-{index}", hostaddr=address,
                )
                dump, restore = self.runner.call_args_list
                command = dump.args[0]
                self.assertEqual(command[command.index("--host") + 1], "localhost")
                self.assertEqual(dump.kwargs["env"]["PGHOSTADDR"], address)
                self.assertEqual(dump.kwargs["env"]["PGSSLMODE"], "verify-full")
                self.assertNotIn("PGHOST", dump.kwargs["env"])
                self.assertNotIn("PGHOSTADDR", restore.kwargs["env"])

    def test_socket_evidence_preserves_host_port_snapshot_and_clean_environment(self):
        self.database["HOST"] = "/var/run/postgresql/"
        snapshot = "00000003-0000001B-1"
        with patch.dict(os.environ, {
            "PGHOST": "wrong-host", "PGHOSTADDR": "192.0.2.99",
            "PGPORT": "9999", "PGDATABASE": "wrong-db",
            "PGUSER": "wrong-user", "PGPASSWORD": "wrong-password",
            "PGSSLMODE": "disable", "PGSERVICE": "wrong-service",
            "PGSERVICEFILE": "wrong-file", "PGPASSFILE": "wrong-password-file",
            "PGOPTIONS": "wrong-options", "PGUNKNOWN": "wrong-value",
            "KEEP_BACKUP_TEST": "keep",
        }):
            evidence = backup.create_evidence(
                self.database, self.target, self.plan_hash, self.directory,
                snapshot=snapshot,
            )
        dump, restore = self.runner.call_args_list
        self.assertEqual(dump.args[0], [
            "/usr/bin/pg_dump", "--format=custom", "--no-owner", "--no-acl",
            "--no-password", "--file", evidence["archive"]["path"],
            "--host", "/var/run/postgresql/", "--port", "5433",
            "--username", "test-user", "--dbname", "test-db",
            "--snapshot", snapshot,
        ])
        self.assertEqual(
            {k: v for k, v in dump.kwargs["env"].items() if k.upper().startswith("PG")},
            {"PGPASSWORD": "private-password", "PGSSLMODE": "require"},
        )
        self.assertNotIn("PGHOSTADDR", dump.kwargs["env"])
        self.assertEqual(dump.kwargs["env"]["KEEP_BACKUP_TEST"], "keep")
        self.assertFalse(any(k.upper().startswith("PG") for k in restore.kwargs["env"]))

    def test_socket_hostaddr_is_rejected_before_creating_files(self):
        self.database["HOST"] = "/var/run/postgresql"
        output = self.directory / "not-created"
        for address in ("127.0.0.1", "::1", ""):
            with self.subTest(address=address), self.assertRaisesRegex(
                ValueError, "Host address cannot be supplied for a Unix socket host",
            ):
                backup.create_evidence(
                    self.database, self.target, self.plan_hash, output,
                    snapshot="snapshot-1", hostaddr=address,
                )
        self.runner.assert_not_called()
        self.assertFalse(output.exists())
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_socket_requires_explicit_port(self):
        self.database["HOST"] = "/var/run/postgresql"
        for port in (None, "", " "):
            with self.subTest(port=port), self.assertRaisesRegex(
                ValueError, "Explicit database connection parameters are required",
            ):
                backup.create_archive(
                    {**self.database, "PORT": port}, self.directory, "socket",
                    snapshot="snapshot-1",
                )
        self.runner.assert_not_called()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_hostaddr_requires_canonical_numeric_ip_before_creating_files(self):
        for address in ("", "localhost", "127.000.0.1", "127.0.0.1,::1",
                        " 127.0.0.1", "127.0.0.1/32", "[::1]", "0:0:0:0:0:0:0:1",
                        "2001:DB8::1", "fe80::1%eth0", 2130706433, True):
            with self.subTest(address=address), self.assertRaises(ValueError):
                backup.create_archive(
                    self.database, self.directory, "invalid", hostaddr=address,
                )
        self.runner.assert_not_called()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_evidence_forwards_verified_server_address(self):
        self.database["HOST"] = "localhost"
        self.target["server_address"] = "127.0.0.1"
        evidence = backup.create_evidence(
            self.database, self.target, self.plan_hash, self.directory,
            snapshot="snapshot-1", hostaddr=self.target["server_address"],
        )
        dump = self.runner.call_args_list[0]
        self.assertEqual(dump.kwargs["env"]["PGHOSTADDR"], self.target["server_address"])
        self.assertEqual(dump.args[0][dump.args[0].index("--host") + 1], "localhost")
        self.assertEqual(evidence["target"], self.target)
        backup.validate_evidence(evidence, self.target, self.plan_hash)

    def test_invalid_inputs_do_not_run_commands(self):
        for label in ("", "../escape", "a/b", "a\\b", "*", "-option", "a\n", "a" * 81):
            with self.subTest(label=label), self.assertRaises(ValueError):
                backup.create_archive(self.database, self.directory, label)
        for key, value in (("ENGINE", "sqlite3"), ("HOST", ""), ("HOST", None), ("PORT", ""),
                           ("USER", ""), ("NAME", "host=elsewhere"),
                           ("NAME", "postgresql://elsewhere/db")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                backup.create_archive({**self.database, key: value}, self.directory, "backup")
        for snapshot in ("", " ", 123):
            with self.subTest(snapshot=snapshot), self.assertRaises(ValueError):
                backup.create_archive(self.database, self.directory, "backup", snapshot=snapshot)
        self.runner.assert_not_called()

    def test_existing_file_is_never_overwritten(self):
        fixed = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        with patch.object(backup.datetime, "datetime") as clock:
            clock.now.return_value = fixed
            archive = backup.create_archive(self.database, self.directory, "backup")
            self.runner.reset_mock()
            with self.assertRaises(FileExistsError):
                backup.create_archive(self.database, self.directory, "backup")
        self.runner.assert_not_called()
        self.assertEqual(Path(archive["path"]).read_bytes(), b"archive-content")

    def test_command_failures_are_sanitized_and_remove_failed_archive(self):
        failures = (
            subprocess.CalledProcessError(1, ["secret"], stderr="private-password"),
            subprocess.TimeoutExpired(["secret"], 600), OSError("private-password"),
        )
        for stage in ("dump", "restore"):
            for failure in failures:
                with self.subTest(stage=stage, failure=type(failure).__name__):
                    def fail(command, **kwargs):
                        if (command[0].endswith("pg_dump")) == (stage == "dump"):
                            raise failure
                        return self.run_command(command, **kwargs)
                    self.runner.side_effect = fail
                    with self.assertRaises(ValueError) as caught:
                        backup.create_archive(self.database, self.directory, "backup")
                    self.assertNotIn("private-password", str(caught.exception))
                    self.assertNotIn("secret", str(caught.exception))
                    self.assertEqual(list(self.directory.iterdir()), [])

    def test_verify_streams_and_matches_optional_metadata(self):
        path = self.directory / "large.dump"
        content = b"x" * (2 * 1024 * 1024 + 17)
        path.write_bytes(content)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("must stream")):
            archive = backup.verify_archive({"path": str(path)})
            self.assertEqual(archive["sha256"], hashlib.sha256(content).hexdigest())
            self.assertEqual(backup.verify_archive(archive), archive)
        for changes in ({"sha256": "b" * 64}, {"size_bytes": 1}, {"size_bytes": True},
                        {"size_bytes": 0}, {"sha256": "invalid"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                backup.verify_archive({**archive, **changes})
        path.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            backup.verify_archive(archive)

    def test_verify_rejects_missing_empty_directory_relative_and_symlink(self):
        empty = self.directory / "empty.dump"
        empty.touch()
        for value in (None, "reference", {}, {"path": "relative.dump"},
                      {"path": str(self.directory / "missing")},
                      {"path": str(empty)}, {"path": str(self.directory)}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                backup.verify_archive(value)
        self.runner.assert_not_called()
        linked = self.directory / "linked.dump"
        try:
            linked.symlink_to(empty)
        except OSError:
            # Windows may require privileges; exercise the lstat rejection anyway.
            with patch.object(Path, "lstat", return_value=types.SimpleNamespace(
                st_mode=stat.S_IFLNK | 0o777,
            )), self.assertRaises(ValueError):
                backup.verify_archive({"path": str(linked)})
        else:
            with self.assertRaises(ValueError):
                backup.verify_archive({"path": str(linked)})
        self.runner.assert_not_called()

    def test_evidence_round_trip_and_independent_fingerprint(self):
        evidence = self.evidence()
        self.assertEqual(evidence["schema"], "pca_backup_v1")
        self.assertEqual(evidence["target"], self.target)
        self.assertIsNot(evidence["target"], self.target)
        self.assertEqual(evidence["verification"], "archive_readability_only")
        self.assertIs(evidence["restoration_tested"], False)
        self.assertEqual(evidence, self.resign(copy.deepcopy(evidence)))
        self.runner.reset_mock()
        self.assertIs(backup.validate_evidence(evidence, self.target, self.plan_hash), evidence)
        self.assertEqual(self.runner.call_args.args[0][0], "/usr/bin/pg_restore")
        self.assertEqual(len(list(self.directory.iterdir())), 1)  # No manifest written.

    def test_evidence_age_boundaries(self):
        evidence = self.evidence()
        created = datetime.datetime.fromisoformat(evidence["created_at"])
        for seconds in (0, 3600):
            backup.validate_evidence(evidence, self.target, self.plan_hash,
                                     now=created + datetime.timedelta(seconds=seconds))
        for seconds in (-1, 3601):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                backup.validate_evidence(evidence, self.target, self.plan_hash,
                                         now=created + datetime.timedelta(seconds=seconds))

    def test_evidence_rejects_naive_timestamp_before_age_comparison(self):
        evidence = self.evidence()
        created = datetime.datetime.fromisoformat(evidence["created_at"])
        evidence["created_at"] = created.replace(tzinfo=None).isoformat()
        self.resign(evidence)
        with self.assertRaisesRegex(ValueError, "requires a UTC timestamp"):
            backup.validate_evidence(evidence, self.target, self.plan_hash, now=created)

    def test_evidence_rejects_wrong_target_plan_structure_and_tampering(self):
        evidence = self.evidence()
        for invalid in (None, "backup-reference", {"path": evidence["archive"]["path"]},
                        {**evidence, "extra": True}, {**evidence, "evidence_hash": "0" * 64}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                backup.validate_evidence(invalid, self.target, self.plan_hash)
        for field, value in (
            ("schema", "other"), ("target", {**self.target, "port": "5433"}),
            ("plan_hash", "b" * 64), ("plan_hash", "invalid"),
            ("verification", "restore_tested"), ("restoration_tested", True),
            ("restoration_tested", 0), ("created_at", "bad"),
            ("created_at", "2026-01-01T12:00:00"), ("created_at", 1),
            ("archive", {"path": evidence["archive"]["path"]}),
        ):
            invalid = self.resign({**evidence, field: value})
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                backup.validate_evidence(invalid, self.target, self.plan_hash)
        with self.assertRaises(ValueError):
            backup.validate_evidence(evidence, self.target, "c" * 64)
        invalid = copy.deepcopy(evidence)
        invalid["target"]["cluster"] = "different"
        with self.assertRaises(ValueError):
            backup.validate_evidence(invalid, invalid["target"], self.plan_hash)

    def test_evidence_rechecks_archive_even_with_valid_fingerprint(self):
        evidence = self.evidence()
        Path(evidence["archive"]["path"]).write_bytes(b"damaged-content")
        with self.assertRaises(ValueError):
            backup.validate_evidence(evidence, self.target, self.plan_hash)
        Path(evidence["archive"]["path"]).unlink()
        with self.assertRaises(ValueError):
            backup.validate_evidence(evidence, self.target, self.plan_hash)

    def test_evidence_rejects_unreadable_archive_despite_matching_hashes(self):
        evidence = self.evidence()
        self.runner.side_effect = subprocess.CalledProcessError(1, ["pg_restore"])
        with self.assertRaises(ValueError):
            backup.validate_evidence(evidence, self.target, self.plan_hash)

    def test_empty_dump_is_removed(self):
        self.runner.side_effect = None
        with self.assertRaises(ValueError):
            backup.create_archive(self.database, self.directory, "empty")
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_evidence_creation_requires_snapshot_and_hash(self):
        for snapshot, plan in ((None, self.plan_hash), ("", self.plan_hash),
                               ("snapshot", "arbitrary-reference")):
            with self.subTest(snapshot=snapshot, plan=plan), self.assertRaises(ValueError):
                backup.create_evidence(self.database, self.target, plan, self.directory,
                                       snapshot=snapshot)
        self.runner.assert_not_called()

    def test_script_keeps_output_and_retention_without_pca_behavior(self):
        script = Path(__file__).resolve().parents[2] / "deploy/scripts/backup_database.py"
        spec = importlib.util.spec_from_file_location("backup_script_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        old = self.directory / "normal-20000101T000000Z.dump"
        old.write_bytes(b"old")
        os.utime(old, (1, 1))
        unrelated = self.directory / "other-20000101T000000Z.dump"
        unrelated.write_bytes(b"other")
        fake_django = types.ModuleType("django")
        fake_django.setup = lambda: None
        fake_conf = types.ModuleType("django.conf")
        fake_conf.settings = types.SimpleNamespace(DATABASES={"default": self.database})
        output = io.StringIO()
        with patch.dict(sys.modules, {"django": fake_django, "django.conf": fake_conf}), \
                patch.object(module, "parse_args", return_value=types.SimpleNamespace(
                    output_dir=self.directory, label="normal", retention_days=14)), \
                patch.object(module.os, "umask"), patch.dict(os.environ), \
                patch.object(sys, "path", list(sys.path)), redirect_stdout(output):
            module.main()
        lines = output.getvalue().splitlines()
        self.assertRegex(lines[0], r"^BACKUP_OK normal-\d{8}T\d{6}Z.dump 15 bytes sha256=[a-f0-9]{64}$")
        self.assertEqual(lines[1:], [f"BACKUP_REMOVED {old.name}"])
        self.assertFalse(old.exists())
        self.assertTrue(unrelated.exists())
        self.assertNotIn("--snapshot", self.runner.call_args_list[0].args[0])


if __name__ == "__main__":
    unittest.main()
