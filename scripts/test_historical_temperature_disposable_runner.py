"""Disposable runner regressions; no settings setup or real database activity."""

from copy import deepcopy
import importlib
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, call, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import historical_temperature_cluster_lease as cluster_lease
import historical_temperature_disposable_runner as runner


class DisposableCreationTests(unittest.TestCase):
    def setUp(self):
        self.lease = (Path(__file__).resolve().parent
                      / "polypbase-ht-production-synthetic-unit" / "data")
        self.config = {
            "HOST": "127.0.0.1", "PORT": "65432", "NAME": "subculture_qa",
            "USER": "subculture_qa", "PASSWORD": "",
            "TEST": {"NAME": "test_subculture_qa"},
            "OPTIONS": {"options": "-c statement_timeout=120000", "application_name": "unit"},
        }
        self.original = deepcopy(self.config)
        self.connection = Mock(settings_dict=self.config)
        self.creation = runner.DisposableCreation(self.connection)
        self.enterContext(patch.dict(runner.os.environ, {
            "POLYPBASE_HISTORICAL_TEMPERATURE_DISPOSABLE_CLUSTER": str(self.lease),
            "PYTHON_DOTENV_DISABLED": "1",
        }, clear=True))
        self.launch = cluster_lease.Launch(
            runner.os.getpid(), str(self.lease.resolve()), 65432, "123456", 12345, 1700000000)
        self.require_launch = self.enterContext(patch.object(
            runner, "require_launch", return_value=self.launch))
        self.verify_server = self.enterContext(patch.object(runner, "verify_server"))
        self.is_dir = self.enterContext(patch.object(Path, "is_dir", return_value=True))
        self.connect = self.enterContext(patch.object(runner.psycopg, "connect"))
        self.admin = self.connect.return_value
        self.admin.__enter__.return_value = self.admin

        self.base_create = self.enterContext(patch.object(
            runner.DatabaseCreation, "_create_test_db", return_value="test_subculture_qa"))
        self.base_lifecycle = self.enterContext(patch.object(
            runner.DatabaseCreation, "create_test_db", return_value="test_subculture_qa"))
        self.executor_type = self.enterContext(patch.object(runner, "MigrationExecutor"))
        self.attest = self.enterContext(patch.object(runner.contract, "attest"))
        self.provision = self.enterContext(patch.object(
            runner.contract, "provision_sql", return_value="PROVISION SQL"))
        self.enterContext(patch("builtins.print"))

    def assert_no_mutation(self):
        self.base_create.assert_not_called()
        self.base_lifecycle.assert_not_called()
        self.executor_type.assert_not_called()
        self.provision.assert_not_called()
        self.attest.assert_not_called()
        self.connection.close.assert_not_called()
        self.assertEqual(self.config, self.original)

    def test_import_requires_neither_settings_nor_dotenv_nor_database(self):
        from django.conf import LazySettings

        with patch.object(LazySettings, "_setup", side_effect=AssertionError("settings accessed")), \
                patch("django.setup", side_effect=AssertionError("Django setup called")), \
                patch("dotenv.load_dotenv", side_effect=AssertionError("dotenv loaded")):
            importlib.reload(runner)
        self.connect.assert_not_called()
        self.assert_no_mutation()

    def test_untrusted_connection_settings_refused_before_connect(self):
        invalid = {
            "HOST": "remote.invalid", "PORT": "65431", "USER": "other", "NAME": "other",
            "PASSWORD": "not-empty", "TEST": {"NAME": "other"},
        }
        for key, value in invalid.items():
            with self.subTest(setting=key):
                self.config.clear()
                self.config.update(deepcopy(self.original))
                self.config[key] = value
                before = deepcopy(self.config)
                with self.assertRaisesRegex(RuntimeError, "outside the fresh disposable launcher"):
                    self.creation._create_test_db(0, True)
                self.assertEqual(self.config, before)
                self.connect.assert_not_called()
                self.base_create.assert_not_called()
                self.provision.assert_not_called()
        self.config.clear()
        self.config.update(deepcopy(self.original))
        self.assert_no_mutation()

    def test_untrusted_lease_refused_before_connect(self):
        invalid = [
            ({"PYTHON_DOTENV_DISABLED": "0"}, True),
            ({"PYTHON_DOTENV_DISABLED": ""}, True),
            ({}, False),
            ({"POLYPBASE_HISTORICAL_TEMPERATURE_DISPOSABLE_CLUSTER":
              str(self.lease.parent / "not-data")}, True),
            ({"POLYPBASE_HISTORICAL_TEMPERATURE_DISPOSABLE_CLUSTER":
              str(self.lease.parent.parent / "untrusted" / "data")}, True),
        ]
        for environment, exists in invalid:
            with self.subTest(environment=environment, exists=exists):
                self.is_dir.return_value = exists
                with patch.dict(runner.os.environ, environment):
                    with self.assertRaisesRegex(RuntimeError, "outside the fresh disposable launcher"):
                        self.creation._create_test_db(0, True)
                self.connect.assert_not_called()
                self.assert_no_mutation()

    def test_missing_lease_or_dotenv_disable_refused_before_connect(self):
        for key, error in [
            ("POLYPBASE_HISTORICAL_TEMPERATURE_DISPOSABLE_CLUSTER", KeyError),
            ("PYTHON_DOTENV_DISABLED", RuntimeError),
        ]:
            with self.subTest(variable=key):
                environment = dict(runner.os.environ)
                del environment[key]
                with patch.dict(runner.os.environ, environment, clear=True):
                    with self.assertRaises(error):
                        self.creation._create_test_db(0, True)
                self.connect.assert_not_called()
                self.assert_no_mutation()

    def test_server_verification_failure_closes_before_any_mutation(self):
        for error in (RuntimeError("Fresh PostgreSQL 18 instance identity mismatch"),
                      OSError("lease query failed"), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                self.admin.reset_mock()
                self.verify_server.reset_mock()
                self.verify_server.side_effect = error
                with self.assertRaises(type(error)) as raised:
                    self.creation._create_test_db(0, True)
                self.assertIs(raised.exception, error)
                self.verify_server.assert_called_once_with(self.admin)
                self.admin.close.assert_called_once_with()
                self.admin.execute.assert_not_called()
                self.admin.__enter__.assert_not_called()
                self.assert_no_mutation()

    def test_launch_required_before_connection_settings_access(self):
        error = RuntimeError("no launch")
        self.require_launch.side_effect = error
        self.creation.connection = Mock(spec=[])
        with self.assertRaises(RuntimeError) as raised:
            self.creation.admin_connection("subculture_qa")
        self.assertIs(raised.exception, error)
        self.require_launch.assert_called_once_with()
        self.verify_server.assert_not_called()
        self.connect.assert_not_called()
        self.assert_no_mutation()

    def test_forged_environment_without_active_launch_refused_before_connect(self):
        with patch.object(cluster_lease, "_active_launch", None), \
                patch.object(runner, "require_launch", cluster_lease.require_launch):
            with self.assertRaisesRegex(RuntimeError, "live disposable launcher child is required"):
                self.creation._create_test_db(0, True)
        self.connect.assert_not_called()
        self.verify_server.assert_not_called()
        self.assert_no_mutation()

    def test_valid_launch_returns_verified_admin_connection(self):
        events = []
        self.require_launch.side_effect = lambda: events.append("launch") or self.launch
        self.connect.side_effect = lambda **kwargs: events.append("connect") or self.admin
        self.verify_server.side_effect = lambda connection: events.append("verify")
        self.assertIs(self.creation.admin_connection("test_subculture_qa"), self.admin)
        self.assertEqual(events, ["launch", "connect", "verify"])
        self.require_launch.assert_called_once_with()
        self.verify_server.assert_called_once_with(self.admin)
        self.connect.assert_called_once_with(
            host="127.0.0.1", port="65432", dbname="test_subculture_qa",
            user="subculture_qa", password="", sslmode="disable", connect_timeout=5,
            autocommit=True, options="-c lock_timeout=10000 -c statement_timeout=120000")
        self.admin.close.assert_not_called()
        self.assert_no_mutation()

    def test_existing_database_refused_even_with_keepdb(self):
        self.admin.execute.return_value.fetchone.return_value = (1,)
        with self.assertRaisesRegex(RuntimeError, "test database already exists"):
            self.creation._create_test_db(0, True, keepdb=True)
        self.require_launch.assert_called_once_with()
        self.verify_server.assert_called_once_with(self.admin)
        self.admin.execute.assert_called_once_with(
            "SELECT 1 FROM pg_database WHERE datname=%s", ["test_subculture_qa"])
        self.admin.__exit__.assert_called_once()
        self.assert_no_mutation()

    def stage_admins(self):
        admins = []
        for _ in range(3):
            admin = Mock()
            context = Mock()
            context.__enter__ = Mock(return_value=admin)
            context.__exit__ = Mock(return_value=False)
            admins.append((context, admin))
        admins[0][1].execute.return_value.fetchone.return_value = None
        lease = self.enterContext(patch.object(
            self.creation, "admin_connection", side_effect=[item[0] for item in admins]))
        return lease, [item[1] for item in admins]

    def test_stages_runtime_then_admin_provision_then_actual_migrator(self):
        lease, admins = self.stage_admins()
        other = ("other_app", "0002_other")
        executor = self.executor_type.return_value
        executor.loader.graph.leaf_nodes.return_value = [runner.MIGRATION, other]
        executor.loader.graph.forwards_plan.side_effect = lambda node: {
            runner.PREVIOUS: [runner.PREVIOUS],
            runner.MIGRATION: [runner.PREVIOUS, runner.MIGRATION],
            other: [other],
        }[node]
        events = []

        def pre_migrate(targets):
            self.assertEqual(self.config["USER"], runner.contract.RUNTIME)
            self.assertEqual(self.config["NAME"], "test_subculture_qa")
            self.assertEqual(self.config["OPTIONS"], self.original["OPTIONS"])
            self.assertEqual(targets, [other, runner.PREVIOUS])
            events.append("runtime")

        def provision():
            self.assertEqual(self.config, self.original)
            events.append("administrator")
            return "PROVISION SQL"

        def actual_django_create(*args, **kwargs):
            self.assertEqual(self.creation._create_test_db(1, False, keepdb=False),
                             "test_subculture_qa")
            self.assertEqual(self.config["USER"], runner.contract.MIGRATOR)
            self.assertEqual(self.config["OPTIONS"], {
                **self.original["OPTIONS"],
                "options": self.original["OPTIONS"]["options"] + " -c role=box1902_owner",
            })
            self.assertEqual(self.original["OPTIONS"], self.connection.settings_dict["OPTIONS"]
                             | {"options": self.original["OPTIONS"]["options"]})
            self.config["NAME"] = "test_subculture_qa"
            events.append("migrator-owner")
            return "test_subculture_qa"

        def attest(connection, allow_retired):
            self.assertIs(connection, self.connection)
            self.assertFalse(allow_retired)
            self.assertEqual(self.config["USER"], runner.contract.MIGRATOR)
            events.append("attest")

        executor.migrate.side_effect = pre_migrate
        self.provision.side_effect = provision
        self.base_lifecycle.side_effect = actual_django_create
        self.attest.side_effect = attest
        with patch.object(runner, "historical_targets", wraps=runner.historical_targets) as targets:
            self.assertEqual(self.creation.create_test_db(verbosity=1, keepdb=False), "test_subculture_qa")
        targets.assert_called_once_with(executor.loader, {"measurements"}, [runner.PREVIOUS])
        self.assertEqual(events, ["runtime", "administrator", "migrator-owner", "attest"])
        self.base_lifecycle.assert_called_once_with(verbosity=1, keepdb=False)
        self.base_create.assert_called_once_with(1, False, False)
        self.executor_type.assert_called_once_with(self.connection)
        lease.assert_has_calls([call("subculture_qa"), call("test_subculture_qa"),
                               call("test_subculture_qa")])
        self.assertEqual(admins[1].execute.call_count, 2)
        self.assertEqual(admins[1].execute.call_args_list[0], call(
            "CREATE ROLE polypbase LOGIN NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"))
        self.assertEqual(admins[1].execute.call_args_list[1].args[0].as_string(),
                         'ALTER DATABASE "test_subculture_qa" OWNER TO polypbase')
        admins[2].execute.assert_called_once_with("PROVISION SQL")
        self.attest.assert_called_once_with(self.connection, allow_retired=False)
        self.assertEqual(self.config["USER"], self.original["USER"])
        self.assertEqual(self.config["OPTIONS"], self.original["OPTIONS"])
        self.assertEqual(self.config["NAME"], "test_subculture_qa")
        self.assertEqual(self.connection.close.call_count, 3)
        self.connect.assert_not_called()

    def test_pre_migration_failure_restores_settings_and_closes(self):
        lease, admins = self.stage_admins()
        error = RuntimeError("pre-0018 failed")
        self.executor_type.return_value.migrate.side_effect = error
        with self.assertRaises(RuntimeError) as raised:
            self.creation._create_test_db(0, False)
        self.assertIs(raised.exception, error)
        self.assertEqual(self.config, self.original)
        self.assertEqual(self.connection.close.call_count, 2)
        self.provision.assert_not_called()
        self.attest.assert_not_called()
        self.assertEqual(lease.call_count, 2)
        admins[2].execute.assert_not_called()

    def test_create_test_db_restores_credentials_and_closes_on_every_exit(self):
        for failure in (None, "create", "attest"):
            with self.subTest(failure=failure):
                self.config.clear()
                self.config.update(deepcopy(self.original))
                self.connection.close.reset_mock()
                self.attest.reset_mock()
                error = RuntimeError("injected failure")

                def create(*args, **kwargs):
                    self.config.update(USER=runner.contract.MIGRATOR)
                    self.config["OPTIONS"]["options"] = "-c role=box1902_owner"
                    self.config["OPTIONS"]["extra"] = "changed"
                    if failure == "create":
                        raise error
                    return "test_subculture_qa"

                self.base_lifecycle.side_effect = create
                self.attest.side_effect = error if failure == "attest" else None
                if failure:
                    with self.assertRaises(RuntimeError) as raised:
                        self.creation.create_test_db(verbosity=0)
                    self.assertIs(raised.exception, error)
                else:
                    self.assertEqual(self.creation.create_test_db(verbosity=0), "test_subculture_qa")
                self.assertEqual(self.config, self.original)
                self.connection.close.assert_called_once_with()
                if failure == "create":
                    self.attest.assert_not_called()
                else:
                    self.attest.assert_called_once_with(self.connection, allow_retired=False)
                self.connect.assert_not_called()


class ActualMigrationGraphTests(unittest.TestCase):
    def test_pre_capability_bootstrap_excludes_descendant_0019_in_actual_graph(self):
        root = Path(__file__).resolve().parents[1]
        environment = {
            key: value for key, value in os.environ.items()
            if key.upper() in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "TEMP", "TMP", "PATH"}
        }
        environment.update(
            PYTHON_DOTENV_DISABLED="1", POLYPBASE_TEST_POSTGRES="0", DJANGO_DEBUG="1",
            DJANGO_SETTINGS_MODULE="config.test_settings",
            PYTHONPATH=os.pathsep.join((str(root / "backend"), str(root / "scripts"))),
        )
        result = subprocess.run(
            [sys.executable, "-B", "-c", r'''
from unittest.mock import Mock, patch
import dotenv
with patch.object(dotenv, "load_dotenv", return_value=False), patch(
    "psycopg.connect", side_effect=AssertionError("Graph regression must not connect")
):
    import django
    django.setup()
    from django.conf import settings
    from django.db.migrations.loader import MigrationLoader
    import historical_temperature_disposable_runner as runner

    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
    loader = MigrationLoader(None)
    descendant = ("measurements", "0019_box_creation_initial_origin")
    assert descendant in loader.graph.nodes
    assert runner.MIGRATION in loader.graph.forwards_plan(descendant)
    executor = Mock(loader=loader)
    connection = Mock(settings_dict={
        "NAME": "subculture_qa", "USER": "subculture_qa", "OPTIONS": {},
        "TEST": {"NAME": "test_subculture_qa"},
    })
    creation = runner.DisposableCreation(connection)
    with patch.object(creation, "admin_connection") as admin, patch.object(
        runner.DatabaseCreation, "_create_test_db", return_value="test_subculture_qa"
    ), patch.object(runner, "MigrationExecutor", return_value=executor), patch.object(
        runner.contract, "provision_sql", return_value="OFFLINE PROVISION"
    ):
        admin.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = None
        creation._create_test_db(0, False)

    targets = executor.migrate.call_args.args[0]
    projected = {node for target in targets for node in loader.graph.forwards_plan(target)}
    expected = {node for node in loader.graph.forwards_plan(runner.PREVIOUS)
                if node[0] == "measurements"}
    assert runner.PREVIOUS in targets
    assert runner.MIGRATION not in projected
    assert descendant not in projected
    assert {node for node in projected if node[0] == "measurements"} == expected
    print("Actual graph excludes capability and descendant 0019 before provisioning")
'''],
            cwd=root, env=environment, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Actual graph excludes capability and descendant 0019", result.stdout)


class DisposableRunnerTests(unittest.TestCase):
    def test_forged_context_refused_before_connections_or_base_setup(self):
        with patch.object(cluster_lease, "_active_launch", None), \
                patch.object(runner, "require_launch", cluster_lease.require_launch), \
                patch.object(runner, "connections") as connections, \
                patch.object(runner.DiscoverRunner, "setup_databases") as setup, \
                patch.object(runner.psycopg, "connect") as connect:
            with self.assertRaisesRegex(RuntimeError, "live disposable launcher child is required"):
                runner.DisposableRunner(verbosity=0).setup_databases()
        connections.__getitem__.assert_not_called()
        setup.assert_not_called()
        connect.assert_not_called()

    def test_valid_launch_installs_creation_before_base_setup(self):
        launch = cluster_lease.Launch(1, "unused/data", 65432, "123456", 12345, 1700000000)
        events = []
        with patch.object(runner, "require_launch", side_effect=lambda: events.append("launch") or launch), \
                patch.object(runner, "connections") as connections, \
                patch.object(runner.DiscoverRunner, "setup_databases") as setup, \
                patch.object(runner.psycopg, "connect") as connect:
            connection = connections.__getitem__.return_value

            def get_connection(alias):
                self.assertEqual(alias, "default")
                events.append("connection")
                return connection

            def base_setup(**kwargs):
                self.assertEqual(kwargs, {"aliases": {"default"}})
                self.assertIsInstance(connection.creation, runner.DisposableCreation)
                self.assertIs(connection.creation.connection, connection)
                events.append("setup")
                return "database configuration"

            connections.__getitem__.side_effect = get_connection
            setup.side_effect = base_setup
            with patch("config.disposable_postgres.install_disposable_flush",
                       side_effect=lambda: events.append("flush")):
                self.assertEqual(runner.DisposableRunner(verbosity=0).setup_databases(
                    aliases={"default"}), "database configuration")
        # Cleanup is installed only once the verified test database exists.
        self.assertEqual(events, ["launch", "connection", "setup", "flush"])
        setup.assert_called_once_with(aliases={"default"})
        connect.assert_not_called()

    def test_teardown_restores_flush_before_destroying_databases(self):
        events = []
        with patch("config.disposable_postgres.uninstall_disposable_flush",
                   side_effect=lambda: events.append("restore")), \
                patch.object(runner.DiscoverRunner, "teardown_databases",
                             side_effect=lambda config, **kwargs: events.append(("teardown", config))):
            runner.DisposableRunner(verbosity=0).teardown_databases("database configuration")
        self.assertEqual(events, ["restore", ("teardown", "database configuration")])


if __name__ == "__main__":
    unittest.main()
