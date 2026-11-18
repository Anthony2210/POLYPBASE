"""Django database lifecycle for the fresh-cluster PostgreSQL QA launcher only."""

from copy import deepcopy
import os
from pathlib import Path

import psycopg
from psycopg import sql
from django.db import connections
from django.db.backends.postgresql.creation import DatabaseCreation
from django.db.migrations.executor import MigrationExecutor
from django.test.runner import DiscoverRunner

from apps.measurements import box_1902_sql as contract
from config.migration_test_utils import historical_targets
from historical_temperature_cluster_lease import require_launch, verify_server


MIGRATION = ("measurements", "0018_box_1902_preservation")
PREVIOUS = ("measurements", "0017_bucket_probe_last_measured_index")


class DisposableCreation(DatabaseCreation):
    def record_diagnostic(self, name, stage):
        if os.environ.get("POLYPBASE_HT_DIAGNOSE_LEGACY_GUARDS") != "1":
            return
        from historical_temperature_guard_diagnostic import record_guard_diagnostic

        original = deepcopy(self.connection.settings_dict)
        self.connection.close()
        self.connection.settings_dict.update(
            NAME=name, USER=contract.RUNTIME,
            OPTIONS={"options": "-c lock_timeout=10000 -c statement_timeout=120000"},
        )
        try:
            record_guard_diagnostic(self.connection, stage)
        finally:
            self.connection.close()
            self.connection.settings_dict.update(original)

    def admin_connection(self, database):
        launch = require_launch()
        config = self.connection.settings_dict
        lease = Path(os.environ["POLYPBASE_HISTORICAL_TEMPERATURE_DISPOSABLE_CLUSTER"]).resolve()
        if (config["HOST"] != "127.0.0.1" or config["USER"] != "subculture_qa"
                or config["NAME"] != "subculture_qa" or config["PASSWORD"] != ""
                or self._get_test_db_name() != "test_subculture_qa"
                or database not in {"subculture_qa", "test_subculture_qa"}
                or lease != Path(launch.data) or int(config["PORT"]) != launch.port
                or os.environ.get("PYTHON_DOTENV_DISABLED") != "1"
                or not lease.is_dir() or lease.name != "data"
                or not lease.parent.name.startswith("polypbase-ht-production-synthetic-")):
            raise RuntimeError("Refuse provisioning outside the fresh disposable launcher")
        connection = psycopg.connect(
            host="127.0.0.1", port=config["PORT"], dbname=database,
            user="subculture_qa", password="", sslmode="disable", connect_timeout=5,
            autocommit=True, options="-c lock_timeout=10000 -c statement_timeout=120000",
        )
        try:
            verify_server(connection)
        except BaseException:
            connection.close()
            raise
        return connection

    def create_test_db(self, *args, **kwargs):
        original = deepcopy(self.connection.settings_dict)
        try:
            name = super().create_test_db(*args, **kwargs)
            contract.attest(self.connection, allow_retired=False)
            self.record_diagnostic(name, "after_0018")
            print("Disposable QA: 0018 installed and SQL guards attested as "
                  "box1902_migrator SET ROLE box1902_owner", flush=True)
            return name
        finally:
            # Tests deliberately need trusted DDL for fault injection and fixture
            # cleanup. This authority is never used to install the capability.
            self.connection.close()
            self.connection.settings_dict.update(
                USER=original["USER"], OPTIONS=original["OPTIONS"],
            )

    def _create_test_db(self, verbosity, autoclobber, keepdb=False):
        # Verify the lease before even creating the test database, then refuse
        # collisions rather than adopting an existing test schema.
        with self.admin_connection("subculture_qa") as admin:
            if admin.execute("SELECT 1 FROM pg_database WHERE datname=%s",
                             [self._get_test_db_name()]).fetchone():
                raise RuntimeError("Disposable test database already exists")
        name = super()._create_test_db(verbosity, autoclobber, keepdb)
        with self.admin_connection(name) as admin:
            admin.execute("CREATE ROLE polypbase LOGIN NOSUPERUSER NOCREATEDB "
                          "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS")
            admin.execute(sql.SQL("ALTER DATABASE {} OWNER TO polypbase").format(sql.Identifier(name)))
        original = deepcopy(self.connection.settings_dict)
        self.connection.close()
        self.connection.settings_dict.update(NAME=name, USER=contract.RUNTIME)
        try:
            executor = MigrationExecutor(self.connection)
            targets = historical_targets(executor.loader, {"measurements"}, [PREVIOUS])
            print("Disposable QA: applying real pre-0018 migrations as polypbase", flush=True)
            executor.migrate(targets)
        finally:
            self.connection.close()
            self.connection.settings_dict.update(original)
        with self.admin_connection(name) as admin:
            admin.execute(contract.provision_sql())
        self.record_diagnostic(name, "before_0018")
        self.connection.settings_dict["USER"] = contract.MIGRATOR
        self.connection.settings_dict["OPTIONS"] = {
            **original["OPTIONS"],
            "options": original["OPTIONS"].get("options", "") + " -c role=box1902_owner",
        }
        return name


class DisposableRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        require_launch()
        connection = connections["default"]
        connection.creation = DisposableCreation(connection)
        config = super().setup_databases(**kwargs)
        # Append-only guards forbid Django's TRUNCATE flush. Suppress them for
        # fixture cleanup only, after re-verifying this launch's cluster.
        from config.disposable_postgres import install_disposable_flush

        install_disposable_flush()
        return config

    def teardown_databases(self, old_config, **kwargs):
        from config.disposable_postgres import uninstall_disposable_flush

        uninstall_disposable_flush()
        return super().teardown_databases(old_config, **kwargs)
