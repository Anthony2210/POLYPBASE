"""Backup tests use temporary files and mocked commands, never Django or a DB."""

import copy
import datetime
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from config import database_backup as backup


def empty_backup_connection():
    connection = MagicMock()
    connection.__enter__.return_value = connection
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [(180006, True), ("snapshot-complete",)]
    cursor.fetchall.return_value = []
    return connection


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
        if command[0].endswith("/pg_dump"):
            Path(command[command.index("--file") + 1]).write_bytes(b"archive-content")
        return subprocess.CompletedProcess(command, 0, stdout=b"")

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

    def test_explicit_peer_backup_disables_default_and_inherited_password_files(self):
        self.database["PASSWORD"] = ""
        self.database["OPTIONS"] = {"sslmode": "disable", "passfile": "/dev/null"}
        with patch.dict(os.environ, {"PGPASSFILE": "inherited-private-file"}):
            self.evidence()
        dump, restore = self.runner.call_args_list
        self.assertEqual(dump.kwargs["env"]["PGPASSFILE"], "/dev/null")
        self.assertEqual(dump.kwargs["env"]["PGPASSWORD"], "")
        self.assertNotIn("PGPASSFILE", restore.kwargs["env"])

    def test_normal_archive_has_no_snapshot_and_defaults_sslmode(self):
        self.database.pop("OPTIONS")
        backup.create_archive(self.database, self.directory, "backup")
        dump = self.runner.call_args_list[0]
        self.assertNotIn("--snapshot", dump.args[0])
        self.assertEqual(dump.kwargs["env"]["PGSSLMODE"], "prefer")
        self.assertIn("--no-owner", dump.args[0])
        self.assertIn("--no-acl", dump.args[0])
        for preserve in (False, True):
            with self.subTest(preserve_authority=preserve):
                self.runner.reset_mock()
                backup.create_archive(self.database, self.directory, f"authority-{preserve}",
                                      preserve_authority=preserve)
                command = self.runner.call_args_list[0].args[0]
                self.assertEqual("--no-owner" in command, not preserve)
                self.assertEqual("--no-acl" in command, not preserve)

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
                patch.object(backup, "require_client", side_effect=lambda name, major: f"/usr/lib/postgresql/{major}/bin/{name}"), \
                patch.object(backup, "_connect_backup", return_value=empty_backup_connection()), \
                patch.object(module.os, "umask"), patch.dict(os.environ), \
                patch.object(sys, "path", list(sys.path)), redirect_stdout(output):
            module.main()
        lines = output.getvalue().splitlines()
        self.assertRegex(lines[0], r"^BACKUP_OK normal-\d{8}T\d{6}Z.dump 15 bytes sha256=[a-f0-9]{64}$")
        self.assertEqual(lines[1:], [f"BACKUP_REMOVED {old.name}"])
        self.assertFalse(old.exists())
        self.assertTrue(unrelated.exists())
        self.assertIn("--snapshot", self.runner.call_args_list[0].args[0])
        self.assertNotIn("--no-owner", self.runner.call_args_list[0].args[0])
        self.assertNotIn("--no-acl", self.runner.call_args_list[0].args[0])
        self.assertEqual(self.runner.call_args_list[0].args[0][0], "/usr/lib/postgresql/18/bin/pg_dump")
        self.assertEqual(self.runner.call_args_list[1].args[0][0], "/usr/lib/postgresql/18/bin/pg_restore")

    def test_script_failure_has_no_success_marker_or_retention(self):
        script = Path(__file__).resolve().parents[2] / "deploy/scripts/backup_database.py"
        spec = importlib.util.spec_from_file_location("backup_script_failure_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        old = self.directory / "normal-20000101T000000Z.dump"
        old.write_bytes(b"old")
        os.utime(old, (1, 1))
        fake_django = types.ModuleType("django")
        fake_django.setup = lambda: None
        fake_conf = types.ModuleType("django.conf")
        fake_conf.settings = types.SimpleNamespace(DATABASES={"default": self.database})
        for failure in ("PostgreSQL 18 pg_dump is unavailable", "archive command failed or timed out",
                        "Incomplete backup authority", "Incomplete backup archive"):
            with self.subTest(failure=failure):
                output = io.StringIO()
                with patch.dict(sys.modules, {"django": fake_django, "django.conf": fake_conf}), \
                        patch.object(module, "parse_args", return_value=types.SimpleNamespace(
                            output_dir=self.directory, label="normal", retention_days=14)), \
                        patch.object(backup, "create_complete_archive", side_effect=ValueError(failure)) as create, \
                        patch.object(module.os, "umask"), patch.dict(os.environ), \
                        patch.object(sys, "path", list(sys.path)), redirect_stdout(output):
                    with self.assertRaisesRegex(ValueError, failure):
                        module.main()
                create.assert_called_once_with(self.database, self.directory, "normal", client_major=18)
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(old.read_bytes(), b"old")


class CompleteBackupTests(unittest.TestCase):
    def inventory_cursor(self, *, version=180006, direct=True, schemas=None,
                         relations=None, large_objects=None):
        cursor = MagicMock()
        cursor.fetchone.return_value = (version, direct)
        cursor.fetchall.side_effect = [
            [(1, True)] if schemas is None else schemas,
            [(10, 'r', True, True, False), (11, 'S', True, True, False),
             (12, 'p', True, True, False), (13, 'm', True, True, False)]
            if relations is None else relations,
            [] if large_objects is None else large_objects,
        ]
        results = list(cursor.fetchall.side_effect)
        results[1] = [(*row, 'public', f'relation_{row[0]}', 'owner') for row in results[1]]
        cursor.fetchall.side_effect = results
        return cursor

    def test_inventory_covers_tables_sequences_and_populated_views(self):
        self.assertEqual(backup._complete_inventory(self.inventory_cursor()), {
            (10, 'TABLE DATA'), ('public relation_11 owner', 'SEQUENCE SET'),
            ('public relation_13 owner', 'MATERIALIZED VIEW DATA'),
        })

    def test_inventory_fails_closed_on_unreadable_or_unsupported_sources(self):
        cases = [dict(version=170000), dict(direct=False), dict(schemas=[(1, False)]),
                 dict(relations=[(10, 'r', True, False, False)]),
                 dict(relations=[(10, 'S', True, False, False)]),
                 dict(relations=[(10, 'r', True, True, True)]),
                 dict(relations=[(10, 'f', True, True, False)]),
                 dict(large_objects=[(1, True)])]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                backup._complete_inventory(self.inventory_cursor(**case))

    def test_toc_uses_oids_not_ambiguous_or_quoted_names(self):
        toc = b'1; 0 10 TABLE DATA private name with spaces owner\n2; 0 11 SEQUENCE SET public seq owner\n'
        with patch.object(backup, 'require_client', return_value='pg_restore'), \
                patch.object(backup.subprocess, 'run', return_value=types.SimpleNamespace(stdout=toc)):
            backup._verify_inventory({'path': '/archive'}, {(10, 'TABLE DATA'), ('public seq owner', 'SEQUENCE SET')}, 18)
            with self.assertRaisesRegex(ValueError, 'expected data entries'):
                backup._verify_inventory({'path': '/archive'}, {(12, 'TABLE DATA')}, 18)

    def test_connection_routing_is_explicit_and_ambient_overrides_are_refused(self):
        database = {'HOST': '/private/socket', 'PORT': '55499', 'NAME': 'isolated',
                    'USER': 'backup', 'PASSWORD': '', 'OPTIONS': {'sslmode': 'disable'}}
        with patch.dict(os.environ, {}, clear=True), patch('psycopg.connect') as connect:
            backup._connect_backup(database)
            parameters = connect.call_args.kwargs
            self.assertEqual(parameters['host'], '/private/socket')
            self.assertEqual(parameters['port'], '55499')
            self.assertEqual(parameters['passfile'], os.devnull)
            self.assertEqual(parameters['password'], '')
            self.assertEqual(parameters['hostaddr'], '')
            self.assertEqual(parameters['options'], '-c row_security=off')
            self.assertTrue(parameters['autocommit'])
        with patch.dict(os.environ, {'PGSERVICE': 'untrusted-service'}), patch('psycopg.connect') as connect:
            with self.assertRaisesRegex(ValueError, 'Ambient PostgreSQL'):
                backup._connect_backup(database)
            connect.assert_not_called()
        with patch.dict(os.environ, {}, clear=True), patch('psycopg.connect') as connect:
            with self.assertRaisesRegex(ValueError, 'connection string'):
                backup._connect_backup(dict(database, NAME='host=untrusted'))
            connect.assert_not_called()

    def test_incomplete_archive_is_removed_and_connection_error_is_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'test.dump'
            archive.write_bytes(b'archive')
            with patch.object(backup, 'require_client'), \
                    patch.object(backup, '_connect_backup', return_value=empty_backup_connection()), \
                    patch.object(backup, 'create_archive', return_value={'path': str(archive)}) as create, \
                    patch.object(backup, '_verify_inventory', side_effect=ValueError('Incomplete backup archive')):
                with self.assertRaisesRegex(ValueError, 'Incomplete backup archive'):
                    backup.create_complete_archive({'ENGINE': 'django.db.backends.postgresql'}, directory, 'test')
                self.assertEqual(create.call_args.kwargs['snapshot'], 'snapshot-complete')
                self.assertIs(create.call_args.kwargs['preserve_authority'], True)
            self.assertFalse(archive.exists())
        with patch.object(backup, 'require_client'), \
                patch.object(backup, '_connect_backup', side_effect=RuntimeError('private details')):
            with self.assertRaisesRegex(ValueError, '^Complete deployment backup verification failed.$'):
                backup.create_complete_archive({'ENGINE': 'django.db.backends.postgresql'}, '.', 'test')

    def test_script_selects_separate_login_without_reusing_runtime_credentials(self):
        root = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location('separate_backup_test', root / 'deploy/scripts/backup_database.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        runtime = {'ENGINE': 'django.db.backends.postgresql', 'USER': 'polypbase',
                   'PASSWORD': 'runtime-secret', 'OPTIONS': {'passfile': 'runtime-file'}}
        original = copy.deepcopy(runtime)
        fake_django = types.ModuleType('django')
        fake_django.setup = lambda: None
        fake_conf = types.ModuleType('django.conf')
        fake_conf.settings = types.SimpleNamespace(DATABASES={'default': runtime})
        for environment, password in [({'POLYPBASE_BACKUP_USER': 'box1902_backup'}, ''),
                                      ({'POLYPBASE_BACKUP_USER': 'box1902_backup',
                                        'POLYPBASE_BACKUP_PASSWORD': 'backup-secret'}, 'backup-secret')]:
            with self.subTest(environment=list(environment)), \
                    patch.dict(os.environ, environment, clear=True), \
                    patch.dict(sys.modules, {'django': fake_django, 'django.conf': fake_conf}), \
                    patch.object(module, 'parse_args', return_value=types.SimpleNamespace(
                        output_dir=Path('.'), label='backup', retention_days=None)), \
                    patch.object(module.os, 'umask'), patch.object(sys, 'path', list(sys.path)), \
                    patch.object(backup, 'create_complete_archive', side_effect=ValueError('incomplete')) as create, \
                    redirect_stdout(io.StringIO()) as output:
                with self.assertRaisesRegex(ValueError, 'incomplete'):
                    module.main()
                selected = create.call_args.args[0]
                self.assertEqual(selected['USER'], 'box1902_backup')
                self.assertEqual(selected['PASSWORD'], password)
                self.assertEqual(selected['OPTIONS']['passfile'], os.devnull)
                self.assertEqual(runtime, original)
                self.assertEqual(output.getvalue(), '')


class PinnedClientTests(unittest.TestCase):
    """Versioned client checks use a fake filesystem and never execute a binary."""

    CLIENT = "/usr/lib/postgresql/18/bin/pg_restore"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name).resolve() / "release.dump"
        self.path.write_bytes(b"archive-content")
        self.entries = {
            path: types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
            for path in ("/", "/usr", "/usr/lib", "/usr/lib/postgresql",
                         "/usr/lib/postgresql/18", "/usr/lib/postgresql/18/bin")
        }
        self.dump_client = "/usr/lib/postgresql/18/bin/pg_dump"
        for client in (self.CLIENT, self.dump_client):
            self.entries[client] = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o755, st_uid=0)
        self.version = b"pg_restore (PostgreSQL) 18.6 (Debian 18.6-1.pgdg13+1)\n"
        real_lstat = os.lstat

        def lstat(path):
            if isinstance(path, str) and path in self.entries:
                if self.entries[path] is None:
                    raise FileNotFoundError(path)
                return self.entries[path]
            if isinstance(path, str) and path.startswith("/usr/lib/postgresql"):
                raise FileNotFoundError(path)
            return real_lstat(path)

        self.lstat = patch.object(backup.os, "lstat", side_effect=lstat).start()
        self.runner = patch.object(backup.subprocess, "run", side_effect=self.run_command).start()
        self.addCleanup(patch.stopall)

    def run_command(self, command, **kwargs):
        if command[1:] == ["--version"]:
            output = self.version.replace(b"pg_restore", b"pg_dump") if command[0] == self.dump_client else self.version
            return subprocess.CompletedProcess(command, 0, stdout=output)
        if command[0] == self.dump_client:
            Path(command[command.index("--file") + 1]).write_bytes(b"archive-content")
        return subprocess.CompletedProcess(command, 0, stdout=b"")

    def commands(self):
        return [call.args[0] for call in self.runner.call_args_list]

    def test_default_verification_keeps_the_historical_wrapper_without_probe(self):
        backup.verify_archive({"path": str(self.path)})
        self.assertEqual(self.commands(), [["/usr/bin/pg_restore", "--list", str(self.path)]])
        self.assertFalse(any(str(call.args[0]).startswith("/usr/lib/postgresql")
                             for call in self.lstat.call_args_list))

    def test_pinned_client_is_checked_then_used_for_the_listing(self):
        with patch.dict(os.environ, {"PGCLUSTER": "17/main", "PGPORT": "5432", "KEEP": "1"}):
            archive = backup.verify_archive({"path": str(self.path)}, client_major=18)
        self.assertEqual(archive["sha256"], hashlib.sha256(b"archive-content").hexdigest())
        self.assertEqual(self.commands(), [[self.CLIENT, "--version"],
                                           [self.CLIENT, "--list", str(self.path)]])
        probe, listing = self.runner.call_args_list
        self.assertEqual(probe.kwargs["timeout"], backup.CLIENT_VERSION_TIMEOUT_SECONDS)
        self.assertEqual(probe.kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(listing.kwargs["timeout"], backup.TIMEOUT_SECONDS)
        for call in (probe, listing):
            self.assertTrue(call.kwargs["check"])
            self.assertEqual(call.kwargs["env"]["KEEP"], "1")
            self.assertFalse(any(key.upper().startswith("PG") for key in call.kwargs["env"]))
        self.assertEqual(backup.require_client("pg_restore", 18), self.CLIENT)
        self.version = b"pg_restore (PostgreSQL) 18.0\n"
        self.assertEqual(backup.require_client("pg_restore", 18), self.CLIENT)

    def test_unsafe_missing_or_other_version_clients_fail_closed_before_listing(self):
        regular, directory = stat.S_IFREG | 0o755, stat.S_IFDIR | 0o755
        cases = {
            "missing binary": {self.CLIENT: None},
            "missing major": {"/usr/lib/postgresql/18": None},
            "symlinked binary": {self.CLIENT: (stat.S_IFLNK | 0o777, 0)},
            "binary not root": {self.CLIENT: (regular, 1000)},
            "binary group writable": {self.CLIENT: (regular | 0o020, 0)},
            "binary other writable": {self.CLIENT: (regular | 0o002, 0)},
            "binary not executable": {self.CLIENT: (stat.S_IFREG | 0o644, 0)},
            "symlinked ancestor": {"/usr/lib/postgresql/18": (stat.S_IFLNK | 0o777, 0)},
            "ancestor not root": {"/usr/lib/postgresql/18/bin": (directory, 1000)},
            "ancestor writable": {"/usr/lib/postgresql": (directory | 0o002, 0)},
            "root writable": {"/": (directory | 0o020, 0)},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                saved = dict(self.entries)
                for path, value in changes.items():
                    self.entries[path] = None if value is None else types.SimpleNamespace(
                        st_mode=value[0], st_uid=value[1])
                self.runner.reset_mock()
                with self.assertRaisesMessage("PostgreSQL 18 pg_restore is unavailable"):
                    backup.verify_archive({"path": str(self.path)}, client_major=18)
                self.assertEqual(self.commands(), [])
                self.entries = saved
        for output in (b"pg_restore (PostgreSQL) 17.10 (Debian 17.10-1.pgdg13+1)\n",
                       b"pg_restore (PostgreSQL) 180.1\n", b"pg_dump (PostgreSQL) 18.6\n",
                       b"pg_restore (PostgreSQL) 18.6\nextra\n", b"", b"\xff",
                       b"pg_restore (PostgreSQL) 18\n"):
            with self.subTest(output=output):
                self.version = output
                self.runner.reset_mock()
                with self.assertRaisesMessage("PostgreSQL 18 pg_restore is unavailable"):
                    backup.verify_archive({"path": str(self.path)}, client_major=18)
                self.assertEqual(self.commands(), [[self.CLIENT, "--version"]])
        self.version = b"pg_restore (PostgreSQL) 18.6\n"
        for failure in (subprocess.CalledProcessError(1, ["secret"]),
                        subprocess.TimeoutExpired(["secret"], 30), PermissionError("secret")):
            with self.subTest(failure=type(failure).__name__):
                self.runner.side_effect = failure
                with self.assertRaisesMessage("PostgreSQL 18 pg_restore is unavailable") as caught:
                    backup.verify_archive({"path": str(self.path)}, client_major=18)
                self.assertNotIn("secret", str(caught.exception))
        self.runner.side_effect = self.run_command

    def test_release_backup_does_not_import_web_owned_checkout_or_settings(self):
        root = Path(__file__).resolve().parents[2]
        source = (root / "deploy/scripts/deploy_release.sh").read_text(encoding="utf-8")
        section = source.split('CURRENT_STEP="database backup"', 1)[1].split(
            'CURRENT_STEP="fast-forward update"', 1,
        )[0]
        self.assertIn('/usr/bin/sudo -n -u polypbase_backup', section)
        self.assertIn('/usr/local/libexec/polypbase-deployment-backup "$TARGET_COMMIT"', section)
        self.assertNotIn('.venv', section)
        self.assertNotIn('database_backup.py', section)
        self.assertNotIn('POLYPBASE_BACKUP_PASSWORD', section)
        # Executable hash/approval/environment/complete-helper tests live in
        # deploy/tests/test_trusted_deployment_backup.py; no web bootstrap remains.

    def database(self):
        return {"ENGINE": "django.db.backends.postgresql", "HOST": "/var/run/postgresql",
                "PORT": "5433", "USER": "test-user", "NAME": "test-db",
                "OPTIONS": {"sslmode": "disable", "passfile": "/dev/null"}}

    def test_creation_pins_both_clients_and_keeps_database_identity(self):
        directory = self.path.parent / "new"
        with patch.dict(os.environ, {"PGCLUSTER": "17/main", "PGPORT": "5432"}):
            archive = backup.create_archive(self.database(), directory, "release", client_major=18)
        commands = self.commands()
        self.assertEqual(commands[:2], [[self.dump_client, "--version"], [self.CLIENT, "--version"]])
        self.assertEqual(commands[2][0], self.dump_client)
        self.assertEqual(commands[3:], [[self.CLIENT, "--version"],
                                       [self.CLIENT, "--list", archive["path"]]])
        for flag, value in (("--host", "/var/run/postgresql"), ("--port", "5433"),
                            ("--username", "test-user"), ("--dbname", "test-db")):
            self.assertEqual(commands[2][commands[2].index(flag) + 1], value)
        self.assertEqual(archive["sha256"], hashlib.sha256(b"archive-content").hexdigest())
        self.assertEqual(archive["size_bytes"], 15)
        dump = self.runner.call_args_list[2]
        self.assertEqual(dump.kwargs["timeout"], 600)
        self.assertEqual(dump.kwargs["env"]["PGPASSFILE"], "/dev/null")
        self.assertNotIn("PGCLUSTER", dump.kwargs["env"])
        self.assertNotIn("PGPORT", dump.kwargs["env"])
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(Path(archive["path"]).stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)

    def test_creation_refuses_missing_unsafe_or_wrong_clients_before_files(self):
        for client in (self.dump_client, self.CLIENT):
            for entry in (None, types.SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0),
                          types.SimpleNamespace(st_mode=stat.S_IFREG | 0o777, st_uid=0),
                          types.SimpleNamespace(st_mode=stat.S_IFREG | 0o755, st_uid=1000)):
                with self.subTest(client=client, entry=entry):
                    saved = self.entries[client]
                    self.entries[client] = entry
                    self.runner.reset_mock()
                    with self.assertRaises(ValueError):
                        backup.create_archive(self.database(), self.path.parent / "new", "release", client_major=18)
                    self.assertFalse((self.path.parent / "new").exists())
                    self.assertTrue(all(command[1:] == ["--version"] for command in self.commands()))
                    self.entries[client] = saved
            for failure in (b"pg_restore (PostgreSQL) 17.10\n", b"", b"invalid"):
                with self.subTest(client=client, version=failure):
                    def wrong_version(command, **kwargs):
                        if command == [client, "--version"]:
                            return subprocess.CompletedProcess(command, 0, stdout=failure.replace(b"pg_restore", Path(client).name.encode()))
                        return self.run_command(command, **kwargs)
                    self.runner.side_effect = wrong_version
                    with self.assertRaises(ValueError):
                        backup.create_archive(self.database(), self.path.parent / "new", "release", client_major=18)
                    self.assertFalse((self.path.parent / "new").exists())
            self.runner.side_effect = self.run_command

    def test_pinned_command_failures_remove_archive_without_fallback(self):
        for stage in ("pg_dump", "pg_restore"):
            for failure in (OSError("secret"), subprocess.TimeoutExpired(["secret"], 600),
                            subprocess.CalledProcessError(1, ["secret"])):
                with self.subTest(stage=stage, failure=type(failure).__name__):
                    def fail(command, **kwargs):
                        if command[1:] != ["--version"] and command[0].endswith(stage):
                            raise failure
                        return self.run_command(command, **kwargs)
                    self.runner.side_effect = fail
                    self.runner.reset_mock()
                    with self.assertRaisesRegex(ValueError, "archive command failed or timed out") as caught:
                        backup.create_archive(self.database(), self.path.parent, "release", client_major=18)
                    self.assertNotIn("secret", str(caught.exception))
                    self.assertEqual(list(self.path.parent.glob("release-*.dump")), [])
                    self.assertTrue(all(command[0].startswith("/usr/lib/postgresql/18/bin/")
                                        for command in self.commands()))

    def test_invalid_client_requests_are_refused_without_io(self):
        for name, major in (("pg_restore", True), ("pg_restore", "18"), ("pg_restore", 9),
                            ("pg_restore", 100), ("pg_restore", 18.0), ("psql", 18),
                            ("../pg_restore", 18)):
            with self.subTest(name=name, major=major), \
                    self.assertRaisesMessage("Unsupported PostgreSQL client request"):
                backup.require_client(name, major)
        with self.assertRaisesMessage("Unsupported PostgreSQL client request"):
            backup.verify_archive({"path": str(self.path)}, client_major="18")
        self.runner.assert_not_called()
        self.assertFalse(any(str(call.args[0]).startswith("/usr/lib/postgresql")
                             for call in self.lstat.call_args_list))

    def assertRaisesMessage(self, message):
        return self.assertRaisesRegex(ValueError, re.escape(message))


if __name__ == "__main__":
    unittest.main()
