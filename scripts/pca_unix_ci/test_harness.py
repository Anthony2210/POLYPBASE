"""Connection-free tests for the container harness, runnable from the repo root."""

from contextlib import ExitStack
import builtins
import io
import json
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from scripts.pca_unix_ci import provision, run_tests


PAYLOAD = {"owner_password": "ephemeral-owner-secret",
           "runtime_password": "ephemeral-runtime-secret", "system_identifier": "123456789"}
POSTGRES = SimpleNamespace(pw_uid=999, pw_gid=998)


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.process = self.stack.enter_context(patch.object(
            subprocess, "run", side_effect=AssertionError("Unexpected process start")))
        self.connect = self.stack.enter_context(patch.object(
            provision, "connect", side_effect=AssertionError("Unexpected SQL connection")))
        self.stack.enter_context(patch("sys.stdout", new_callable=io.StringIO))

    def test_root_refusal_never_starts_process_or_mutates_environment(self):
        for platform, uid, euid in (("win32", 0, 0), ("darwin", 0, 0),
                                    ("linux", 10001, 0), ("linux", 0, 10001)):
            with self.subTest(platform=platform, uid=uid, euid=euid), ExitStack() as stack:
                stack.enter_context(patch.object(provision.sys, "platform", platform))
                stack.enter_context(patch.object(os, "getuid", return_value=uid, create=True))
                stack.enter_context(patch.object(os, "geteuid", return_value=euid, create=True))
                environment = stack.enter_context(patch.dict(os.environ, {"SENTINEL": "keep"}))
                machine = stack.enter_context(patch.object(provision, "machine_id"))
                with self.assertRaisesRegex(RuntimeError, "Linux container root"):
                    provision.main()
                self.assertEqual(environment["SENTINEL"], "keep")
                machine.assert_not_called()
        self.process.assert_not_called()
        self.connect.assert_not_called()

    def test_clean_environment_and_root_commands_do_not_inherit_secrets(self):
        inherited = {"PGPASSWORD": "secret", "PGDATA": "/host", "POSTGRES_PASSWORD": "secret",
                     "AWS_SECRET_ACCESS_KEY": "secret", "PYTHONPATH": "/host", "TOKEN": "secret"}
        with patch.dict(os.environ, inherited, clear=True):
            environment = provision.clean_environment()
            self.process.side_effect = None
            provision.postgres_command("pg_controldata", str(provision.DATA))
        self.assertTrue(set(environment).isdisjoint(inherited))
        self.assertFalse(any(key.upper().startswith(("PG", "POSTGRES_")) for key in environment))
        self.process.assert_called_once_with(
            ["runuser", "--user", "postgres", "--", "pg_controldata", str(provision.DATA)],
            check=True, timeout=30, cwd=provision.ROOT, env=environment)

    def test_child_drops_privileges_and_sends_credentials_only_on_stdin(self):
        self.process.side_effect = None
        with patch.dict(os.environ, {"PGPASSWORD": "inherited", "POSTGRES_USER": "host",
                                     "PRIVATE_TOKEN": "inherited"}, clear=True):
            provision.child(PAYLOAD)
            provision.child(PAYLOAD, probe="socket-group", postgres_gid=POSTGRES.pw_gid)
        for invocation, group, timeout in zip(self.process.call_args_list,
                                             ("--clear-groups", "--groups=998"), (600, 30)):
            command = invocation.args[0]
            options = invocation.kwargs
            self.assertEqual(command[0], "setpriv")
            for flag in ("--reuid=10001", "--regid=10001", group, "--no-new-privs",
                         "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all"):
                self.assertIn(flag, command)
            self.assertEqual(json.loads(options["input"]), PAYLOAD)
            self.assertTrue(options["input"].endswith("\n"))
            self.assertEqual(options["timeout"], timeout)
            self.assertTrue(options["check"] and options["text"])
            self.assertEqual(options["cwd"], provision.ROOT)
            environment = options["env"]
            self.assertFalse(any(key.upper().startswith(("PG", "POSTGRES_")) for key in environment))
            self.assertNotIn("PRIVATE_TOKEN", environment)
            for key in ("owner_password", "runtime_password"):
                self.assertNotIn(PAYLOAD[key], json.dumps(command))
                self.assertNotIn(PAYLOAD[key], json.dumps(environment))
        self.assertEqual(self.process.call_args.args[0][-2:], ["--probe", "socket-group"])

    def probe_mocks(self):
        child = self.stack.enter_context(patch.object(provision, "child"))
        chown = self.stack.enter_context(patch.object(os, "chown", create=True))
        chmod = self.stack.enter_context(patch.object(Path, "chmod"))
        run = self.stack.enter_context(patch.object(provision, "run"))
        write = self.stack.enter_context(patch.object(provision, "write_json"))
        self.connect.side_effect = None
        connection = self.connect.return_value.__enter__.return_value
        return child, chown, chmod, run, write, connection

    def test_only_group_probe_adds_postgres_supplemental_group(self):
        child, *_ = self.probe_mocks()
        provision.probes(PAYLOAD, {"target": "original"}, POSTGRES)
        probes = [item for item in child.call_args_list if item.kwargs["probe"] != "success"]
        self.assertEqual(len(probes), 6)
        for item in probes:
            self.assertEqual(item.kwargs["postgres_gid"],
                             POSTGRES.pw_gid if item.kwargs["probe"] == "socket-group" else None)
        self.assertEqual(sum(item.kwargs["probe"] == "success" for item in child.call_args_list), 7)

    def test_each_failed_probe_restores_mutation_and_rechecks_success(self):
        child, chown, chmod, run, write, connection = self.probe_mocks()
        target = {"postmaster_started_at": "original"}
        socket = provision.SOCKET_DIRECTORY / f".s.PGSQL.{provision.PORT}"
        for case in ("socket-owner", "socket-group", "socket-acl", "socket-world-write",
                     "server-identity", "role-privileges"):
            for failure in (AssertionError("child failed"), subprocess.TimeoutExpired("child", 30)):
                with self.subTest(case=case, failure=type(failure).__name__):
                    for mock in (child, chown, chmod, run, write, connection):
                        mock.reset_mock()

                    def fail_case(payload, *, probe, postgres_gid=None):
                        if probe == case:
                            raise failure

                    child.side_effect = fail_case
                    with self.assertRaises(type(failure)) as caught:
                        provision.probes(PAYLOAD, target, POSTGRES)
                    self.assertIs(caught.exception, failure)
                    self.assertEqual(child.call_args, call(PAYLOAD, probe="success"))
                    if case == "socket-owner":
                        self.assertEqual(chown.call_args_list[-2:], [
                            call(socket, 0, POSTGRES.pw_gid), call(socket, POSTGRES.pw_uid, POSTGRES.pw_gid)])
                    elif case == "socket-acl":
                        self.assertEqual(run.call_args_list[-2:], [
                            call(["setfacl", "-m", "u:10001:rwx", str(provision.SOCKET_DIRECTORY)]),
                            call(["setfacl", "-b", str(provision.SOCKET_DIRECTORY)])])
                        chmod.assert_called_with(0o2775)
                    elif case == "socket-world-write":
                        self.assertEqual(chmod.call_args_list[-2:], [call(0o2777), call(0o2775)])
                    elif case == "server-identity":
                        self.assertNotEqual(write.call_args_list[-2].args[1], target)
                        write.assert_called_with(provision.ATTESTATION, target)
                    elif case == "role-privileges":
                        self.assertEqual(connection.execute.call_args_list[-2:], [
                            call(f"ALTER ROLE {provision.RUNTIME} CREATEDB"),
                            call(f"ALTER ROLE {provision.RUNTIME} NOCREATEDB")])

    def test_attestation_requires_unique_control_identity_and_matching_sql(self):
        control = self.stack.enter_context(patch.object(provision, "postgres_command"))
        write = self.stack.enter_context(patch.object(provision, "write_json"))
        self.stack.enter_context(patch.object(Path, "mkdir"))
        self.stack.enter_context(patch.object(provision, "trusted_directory"))
        self.connect.side_effect = None
        fetch = self.connect.return_value.__enter__.return_value.execute.return_value.fetchone
        valid = (provision.DATABASE, 42, provision.RUNTIME, provision.RUNTIME, 43, None, None,
                 "2026-01-01T00:00:00.000000Z", 180001, "origin", ["public"], False, provision.PORT)
        for text in ("", "Database system identifier: 123\nDatabase system identifier: 456\n"):
            control.return_value.stdout = text
            with self.subTest(control=text), self.assertRaisesRegex(RuntimeError, "independently attest"):
                provision.attest(POSTGRES, "a" * 32)
        self.connect.assert_not_called()
        control.return_value.stdout = "Database system identifier: 123456789\n"
        fetch.return_value = ("wrong",)
        with self.assertRaisesRegex(RuntimeError, "Control-file and SQL"):
            provision.attest(POSTGRES, "a" * 32)
        for index, wrong in ((0, "other"), (2, provision.OWNER), (3, provision.OWNER),
                             (5, "127.0.0.1"), (6, provision.PORT), (8, 170000),
                             (8, 190000), (9, "replica"), (10, []), (11, True), (12, 5432)):
            row = list(valid)
            row[index] = wrong
            fetch.side_effect = [(PAYLOAD["system_identifier"],), tuple(row)]
            with self.subTest(index=index, wrong=wrong), self.assertRaisesRegex(RuntimeError, "identity"):
                provision.attest(POSTGRES, "a" * 32)
        write.assert_not_called()
        fetch.side_effect = [(PAYLOAD["system_identifier"],), valid]
        identifier, target = provision.attest(POSTGRES, "a" * 32)
        self.assertEqual(identifier, PAYLOAD["system_identifier"])
        self.assertEqual((target["connection_mode"], target["socket_uid"], target["socket_gid"]),
                         ("unix", POSTGRES.pw_uid, POSTGRES.pw_gid))
        self.assertEqual(write.call_args_list[0], call(provision.ATTESTATION, target, exclusive=True))
        self.assertEqual(write.call_args.args[1]["system_identifier"], identifier)
        control.assert_called_with("pg_controldata", str(provision.DATA), capture_output=True, text=True)

    def driver_fixture(self):
        self.stack.enter_context(patch.object(run_tests, "ROOT", Path(run_tests.__file__).resolve().parents[2]))
        self.stack.enter_context(patch.object(run_tests.sys, "platform", "linux"))
        self.stack.enter_context(patch.object(os, "getuid", return_value=10001, create=True))
        self.stack.enter_context(patch.object(os, "geteuid", return_value=10001, create=True))
        container = self.stack.enter_context(patch.object(Path, "is_file", return_value=True))
        self.stack.enter_context(patch.object(os, "listdir", return_value=["lo"]))
        marker = {"schema": "pca_unix_ci_v1", "container_only": True, "network": "none",
                  "system_identifier": PAYLOAD["system_identifier"], "database": run_tests.DATABASE,
                  "port": int(run_tests.PORT), "owner": run_tests.OWNER, "runtime": run_tests.RUNTIME}
        read = self.stack.enter_context(patch.object(Path, "read_text", return_value=json.dumps(marker)))
        self.stack.enter_context(patch.object(Path, "lstat", autospec=True, side_effect=lambda path:
            SimpleNamespace(st_uid=0, st_mode=(stat.S_IFREG if path == run_tests.MARKER else stat.S_IFDIR) | 0o755)))
        environment = {"POLYPBASE_QA_HOST": "127.0.0.1", "POLYPBASE_QA_PORT": run_tests.PORT,
                       "POLYPBASE_QA_DATABASE": run_tests.DATABASE, "POLYPBASE_QA_OWNER": run_tests.OWNER,
                       "POLYPBASE_QA_RUNTIME": run_tests.RUNTIME,
                       "POLYPBASE_QA_SYSTEM_IDENTIFIER": PAYLOAD["system_identifier"],
                       "PYTHON_DOTENV_DISABLED": "1", "POLYPBASE_TEST_POSTGRES": "1",
                       "POLYPBASE_HISTORICAL_POSTGRES_QA": "1",
                       "DJANGO_SETTINGS_MODULE": "config.historical_temperature_pgqa_settings"}
        self.stack.enter_context(patch.dict(os.environ, environment, clear=True))
        return container, read, marker

    def test_production_wrapper_restores_after_success_and_failure_without_connecting(self):
        import sys

        backend = str(Path(__file__).resolve().parents[2] / "backend")
        with patch.object(sys, "path", [backend, *sys.path]), patch.dict(os.environ, {
                "DJANGO_SETTINGS_MODULE": "config.test_settings", "PYTHON_DOTENV_DISABLED": "1",
                "POSTGRES_DB": "", "POLYPBASE_TEST_POSTGRES": "",
                "DJANGO_SECRET_KEY": "django-insecure-connection-free-ci-test-only"}):
            import django
            from django.db import connections
            from django.db.backends.postgresql.base import DatabaseWrapper
            from django.conf import settings

            django.setup()
            original = connections["default"]
            with patch.object(DatabaseWrapper, "ensure_connection",
                              side_effect=AssertionError("Connection-free check opened PostgreSQL")) as ensure:
                for fail in (False, True):
                    with self.subTest(fail=fail):
                        try:
                            with run_tests.production_connection("ephemeral-test-password") as wrapper:
                                self.assertIs(connections["default"], wrapper)
                                self.assertEqual(wrapper.settings_dict["HOST"], "/var/run/postgresql")
                                self.assertEqual(wrapper.settings_dict["USER"], run_tests.RUNTIME)
                                self.assertEqual(settings.SETTINGS_MODULE, "config.settings")
                                self.assertFalse(settings.HISTORICAL_TEMPERATURE_PGQA)
                                if fail:
                                    raise ValueError("synthetic body failure")
                        except ValueError as error:
                            self.assertTrue(fail)
                            self.assertEqual(str(error), "synthetic body failure")
                        self.assertIs(connections["default"], original)
                        self.assertEqual(settings.SETTINGS_MODULE, "config.test_settings")
                ensure.assert_not_called()

    def test_driver_preflight_rejects_before_django_or_database_imports(self):
        container, read, marker = self.driver_fixture()
        run_tests.preflight(PAYLOAD)
        original_import = builtins.__import__
        forbidden = Mock(side_effect=AssertionError("Django/database import before refusal"))

        def guarded_import(name, *args, **kwargs):
            if name.split(".")[0] in ("django", "config", "psycopg", "psycopg2"):
                return forbidden(name)
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import), \
                patch.object(run_tests.sys, "argv", ["run_tests.py"]):
            cases = [("container", "dedicated network-none", {}),
                     ("marker", "independent CI provisioning record", {}),
                     ("environment", "Inherited database configuration", {"POLYPBASE_QA_HOST": "remote"}),
                     ("PG", "Inherited database configuration", {"pGpAsSwOrD": "secret"}),
                     ("POSTGRES", "Inherited database configuration", {"POSTGRES_PASSWORD": "secret"})]
            for case, message, environment in cases:
                with self.subTest(case=case), patch.dict(os.environ, environment), \
                        patch.object(run_tests.sys, "stdin", io.StringIO(json.dumps(PAYLOAD))):
                    container.return_value = case != "container"
                    read.return_value = json.dumps({**marker, "system_identifier": "wrong"} if case == "marker" else marker)
                    with self.assertRaisesRegex(RuntimeError, message):
                        run_tests.main()
        forbidden.assert_not_called()
        self.process.assert_not_called()
        self.connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
