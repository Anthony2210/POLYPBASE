"""Fresh PostgreSQL 18 maintenance tests; SCRAM only, synthetic data only."""

import argparse
from copy import deepcopy
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import tempfile

from historical_temperature_cluster_lease import capture_launch, require_launch, run_django_tests

ROOT = Path(__file__).resolve().parents[1]
LABELS = (
    "apps.measurements.test_historical_temperature_reconfirm",
)


def admin_connection(database="test_subculture_qa"):
    """Authenticate and attest the exact disposable instance before fixture DDL."""
    import psycopg

    launch = require_launch()
    conn = psycopg.connect(
        host="127.0.0.1", port=launch.port, dbname=database, user="subculture_qa",
        password=os.environ["POLYPBASE_HT_ADMIN_PASSWORD"], autocommit=True,
        sslmode="disable", connect_timeout=5,
        options="-c lock_timeout=10000 -c statement_timeout=120000",
    )
    try:
        row = conn.execute(
            "SELECT current_setting('server_version_num')::int, current_setting('data_directory'), "
            "(pg_control_system()).system_identifier::text, session_user, current_user, "
            "split_part(pg_read_file('postmaster.pid'), chr(10), 1)::int, "
            "split_part(pg_read_file('postmaster.pid'), chr(10), 3)::bigint"
        ).fetchone()
        if (not 180000 <= row[0] < 190000 or Path(row[1]).resolve() != Path(launch.data)
                or row[2:] != (launch.system_identifier, "subculture_qa", "subculture_qa",
                              launch.postmaster_pid, launch.started_at)):
            raise RuntimeError("Disposable maintenance instance identity mismatch")
        require_launch()
        return conn
    except BaseException:
        conn.close()
        raise


def initialize_database():
    with admin_connection("postgres") as admin:
        admin.execute("CREATE DATABASE subculture_qa")


def configure_creation(connection):
    """Use real pre-0018 migrations, provisioning, then owner-role migration."""
    from django.db.backends.postgresql.creation import DatabaseCreation
    from django.db.migrations.executor import MigrationExecutor
    from psycopg import sql
    from apps.measurements import box_1902_sql as contract

    class MaintenanceCreation(DatabaseCreation):
        def _create_test_db(self, verbosity, autoclobber, keepdb=False):
            require_launch()
            name = self._get_test_db_name()
            if name != "test_subculture_qa":
                raise RuntimeError("Unexpected maintenance test database")
            with admin_connection("subculture_qa") as admin:
                if admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", [name]).fetchone():
                    raise RuntimeError("Refuse to adopt an existing test database")
            name = super()._create_test_db(verbosity, autoclobber, keepdb)
            with admin_connection(name) as admin:
                admin.execute("CREATE ROLE polypbase LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                              "NOINHERIT NOREPLICATION NOBYPASSRLS")
                # Parameterized set_config avoids placing generated secrets in SQL logs.
                admin.execute("SELECT set_config('maintenance.password', %s, false)",
                              [os.environ["POLYPBASE_HT_RUNTIME_PASSWORD"]])
                admin.execute("DO $$ BEGIN EXECUTE format('ALTER ROLE polypbase PASSWORD %L', "
                              "current_setting('maintenance.password')); END $$")
                admin.execute(sql.SQL("ALTER DATABASE {} OWNER TO polypbase").format(sql.Identifier(name)))
            original = deepcopy(self.connection.settings_dict)
            self.connection.close()
            self.connection.settings_dict.update(NAME=name, USER="polypbase",
                                                PASSWORD=os.environ["POLYPBASE_HT_RUNTIME_PASSWORD"])
            try:
                executor = MigrationExecutor(self.connection)
                executor.migrate([("measurements", "0017_bucket_probe_last_measured_index")
                                  if node == ("measurements", "0018_box_1902_preservation") else node
                                  for node in executor.loader.graph.leaf_nodes()])
            finally:
                self.connection.close()
                self.connection.settings_dict.update(original)
            with admin_connection(name) as admin:
                admin.execute(contract.provision_sql())
                admin.execute("SELECT set_config('maintenance.password', %s, false)",
                              [os.environ["POLYPBASE_HT_MIGRATOR_PASSWORD"]])
                admin.execute("DO $$ BEGIN EXECUTE format('ALTER ROLE box1902_migrator PASSWORD %L', "
                              "current_setting('maintenance.password')); END $$")
            self.connection.settings_dict.update(
                USER="box1902_migrator", PASSWORD=os.environ["POLYPBASE_HT_MIGRATOR_PASSWORD"],
                OPTIONS={"options": "-c role=box1902_owner -c lock_timeout=10000 -c statement_timeout=120000"},
            )
            return name

        def create_test_db(self, *args, **kwargs):
            original = deepcopy(self.connection.settings_dict)
            try:
                return super().create_test_db(*args, **kwargs)
            finally:
                self.connection.close()
                self.connection.settings_dict.update(
                    USER=original["USER"], PASSWORD=original["PASSWORD"], OPTIONS=original["OPTIONS"],
                )

    connection.creation = MaintenanceCreation(connection)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--pg-bin", type=Path, required=True)
    parser.add_argument("--test-timeout", type=int, default=900)
    args = parser.parse_args()
    if not args.run:
        parser.error("--run is required to create a fresh disposable cluster")
    if not 1 <= args.test_timeout <= 1800:
        parser.error("--test-timeout must be 1..1800 seconds")
    suffix = ".exe" if os.name == "nt" else ""
    binaries = {name: args.pg_bin.resolve() / (name + suffix)
                for name in ("initdb", "pg_ctl", "postgres", "pg_controldata")}
    if not all(path.is_file() for path in binaries.values()):
        parser.error("Expected an installed PostgreSQL binary directory")
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("PG", "POSTGRES_", "POLYPBASE_", "DJANGO_"))}
    env.update(PYTHON_DOTENV_DISABLED="1", POLYPBASE_TEST_POSTGRES="0",
               DJANGO_SECRET_KEY="django-insecure-disposable-synthetic-tests-only", LC_ALL="C", LANGUAGE="C")
    version = subprocess.check_output([str(binaries["postgres"]), "--version"], env=env, text=True, timeout=15)
    if not re.search(r"PostgreSQL\) 18\.", version):
        parser.error("PostgreSQL 18 is required")
    for role in ("ADMIN", "MIGRATOR", "RUNTIME"):
        env[f"POLYPBASE_HT_{role}_PASSWORD"] = secrets.token_urlsafe(40)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    directory = Path(tempfile.mkdtemp(prefix="polypbase-ht-maintenance-synthetic-"))
    data = directory / "data"
    pwfile = directory / "initdb-password"

    def run(name, *arguments, timeout=60):
        # Windows PostgreSQL descendants can inherit pipe handles. communicate()
        # then waits for EOF even after the direct child exits or is killed.
        # Files avoid that unbounded pipe-reader wait, including during initdb.
        with tempfile.TemporaryFile() as output:
            result = subprocess.run([str(binaries[name]), *map(str, arguments)], env=env,
                                    stdin=subprocess.DEVNULL, stdout=output,
                                    stderr=subprocess.STDOUT, timeout=timeout)
            if result.returncode:
                # Never print server/SQL logs or credentials on command failures.
                raise RuntimeError(f"Disposable {name} failed with exit code {result.returncode}")
            output.seek(0)
            return output.read().decode("utf-8", errors="replace")

    try:
        pwfile.write_text(env["POLYPBASE_HT_ADMIN_PASSWORD"] + "\n", encoding="ascii")
        pwfile.chmod(0o600)
        print("Initializing fresh SCRAM cluster...", flush=True)
        run("initdb", "-D", data, "--username=subculture_qa", "--auth-local=scram-sha-256",
            "--auth-host=scram-sha-256", "--pwfile", pwfile, "--encoding=UTF8", "--no-locale",
            "--no-sync", timeout=180)
        pwfile.unlink()
        control = run("pg_controldata", data)
        match = re.search(r"^Database system identifier:\s+(\d+)\s*$", control, re.MULTILINE)
        if match is None:
            raise RuntimeError("Could not identify fresh initdb cluster")
        with (data / "postgresql.conf").open("a", encoding="ascii") as stream:
            stream.write(f"\nlisten_addresses='127.0.0.1'\nunix_socket_directories=''\nport={port}\n"
                         "max_connections=60\npassword_encryption='scram-sha-256'\n"
                         "log_statement='none'\nlog_min_error_statement=panic\nlog_parameter_max_length=0\n"
                         "log_parameter_max_length_on_error=0\nfsync=off\nsynchronous_commit=off\nfull_page_writes=off\n")
        print("Starting fresh loopback cluster...", flush=True)
        run("pg_ctl", "-D", data, "-l", directory / "server.log", "-w", "-t", "30", "start")
        launch = capture_launch(data, port, match.group(1))
        settings = directory / "ht_maintenance_settings.py"
        settings.write_text(
            "from historical_temperature_cluster_lease import require_launch\nrequire_launch()\n"
            "from test_historical_temperature_maintenance import initialize_database, configure_creation\n"
            "initialize_database()\nimport os, dotenv\n"
            "dotenv.load_dotenv = lambda *a, **k: False\n"
            "from config.test_settings import *\n"
            "from django.test.runner import DiscoverRunner\nfrom django.db import connections\n"
            "class MaintenanceRunner(DiscoverRunner):\n"
            "    def setup_databases(self, **kwargs):\n"
            "        require_launch()\n        configure_creation(connections['default'])\n"
            "        return super().setup_databases(**kwargs)\n"
            "    def run_suite(self, suite, **kwargs):\n"
            "        result = super().run_suite(suite, **kwargs)\n"
            "        print('MAINTENANCE_RESULT tests=%s failures=%s errors=%s skips=%s' % "
            "(result.testsRun, len(result.failures), len(result.errors), len(result.skipped)), flush=True)\n"
            "        return result\n"
            "TEST_RUNNER = 'ht_maintenance_settings.MaintenanceRunner'\n"
            "DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', "
            "'NAME': 'subculture_qa', 'USER': 'subculture_qa', "
            "'PASSWORD': os.environ['POLYPBASE_HT_ADMIN_PASSWORD'], "
            f"'HOST': '127.0.0.1', 'PORT': '{port}', "
            "'TEST': {'NAME': 'test_subculture_qa'}, "
            "'OPTIONS': {'options': '-c lock_timeout=10000 -c statement_timeout=120000'}}}\n",
            encoding="utf-8",
        )
        env["PYTHONPATH"] = os.pathsep.join(map(str, (directory, ROOT / "backend", ROOT / "scripts")))
        env["POLYPBASE_HISTORICAL_TEMPERATURE_DISPOSABLE_CLUSTER"] = str(data.resolve())
        env["POLYPBASE_HT_MAINTENANCE_SCRAM"] = "1"
        print(f"Fresh synthetic PostgreSQL 18 SCRAM cluster: loopback port {port}", flush=True)
        report = directory / "tests.log"
        try:
            result = run_django_tests(
                ["manage.py", "test", *LABELS, "--settings=ht_maintenance_settings", "--noinput", "-v", "2"],
                cwd=ROOT / "backend", env=env, report=report, launch=launch, timeout=args.test_timeout,
            )
            output = report.read_text(encoding="utf-8", errors="replace")
            completed = re.search(r"^MAINTENANCE_RESULT tests=(\d+) failures=0 errors=0 skips=(\d+)$",
                                  output, re.MULTILINE)
            if result == 0 and (completed is None or int(completed[1]) < 41):
                raise RuntimeError("Worker exited without proof of a complete successful maintenance suite")
            return result
        finally:
            if report.is_file():
                output = report.read_text(encoding="utf-8", errors="replace")
                for role in ("ADMIN", "MIGRATOR", "RUNTIME"):
                    output = output.replace(env[f"POLYPBASE_HT_{role}_PASSWORD"], "[REDACTED]")
                retained = ROOT / '.local-docs' / 'historical-temperature-maintenance-tests.log'
                retained.parent.mkdir(exist_ok=True)
                retained.write_text(output, encoding='utf-8')
                print(output, flush=True)
                print(f"Sanitized test report: {retained.relative_to(ROOT)}", flush=True)
    finally:
        pwfile.unlink(missing_ok=True)
        status = subprocess.run([str(binaries["pg_ctl"]), "-D", str(data), "status"],
                                env=env, capture_output=True, timeout=15)
        if status.returncode == 0:
            run("pg_ctl", "-D", data, "-m", "immediate", "-w", "-t", "15", "stop", timeout=25)
            status = subprocess.run([str(binaries["pg_ctl"]), "-D", str(data), "status"],
                                    env=env, capture_output=True, timeout=15)
        if status.returncode != 3:
            raise RuntimeError("Cannot prove disposable cluster stopped; manual cleanup required")
        shutil.rmtree(directory)
        print("Fresh synthetic cluster stopped and removed.", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
