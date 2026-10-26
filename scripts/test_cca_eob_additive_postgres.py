"""Run only additive-baseline proofs on a newly created, disposable PG18 cluster.

From backend: uv run python -B ../scripts/test_cca_eob_additive_postgres.py
    --pg-bin "C:/Program Files/PostgreSQL/18/bin"
No existing database, application .env, password file or production target is used.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
LABEL = "apps.cultures.test_cca_eob_additive"


def worker(labels):
    sys.path.insert(0, str(ROOT / "backend"))
    from config import historical_temperature_pgqa as qa

    # Trust authentication is confined to the fresh loopback-only test cluster.
    qa.CREDENTIALS.update({qa.OWNER: "disposable-only", qa.RUNTIME: "disposable-only"})
    qa.AUTHORIZED = True
    import django
    django.setup()
    from django.db import connection, connections

    lease = None
    try:
        qa.assert_disposable(connection)
        qa.assert_empty_business_data(connection)
        lease = connection.copy(alias="additive_qa_lease")
        with lease.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(20261018,1)")
            if cursor.fetchone() != (True,):
                raise RuntimeError("Disposable cluster lease unavailable")
        qa.RUN_LEASE = lease
        qa.EMPTY_START_VERIFIED = True
        runner = qa.IsolatedTemperatureRunner(verbosity=2, interactive=False, parallel=0)
        failures = runner.run_tests(labels)
        print(json.dumps(runner.result_summary, indent=2))
        qa.assert_empty_business_data(connection)
        return int(bool(failures or not runner.result_summary or runner.result_summary["skipped"]))
    finally:
        if lease is not None:
            lease.close()
        connections.close_all()
        qa.CREDENTIALS.clear()
        qa.AUTHORIZED = False
        qa.RUN_LEASE = None
        qa.EMPTY_START_VERIFIED = False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg-bin", default=os.getenv("POLYPBASE_PG18_BIN"))
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("labels", nargs="*", default=[LABEL])
    args = parser.parse_args()
    if any(label != LABEL and not label.startswith(LABEL + ".") for label in args.labels):
        parser.error("Only the new additive module's labels are allowed")
    if args.worker:
        return worker(args.labels)
    if not args.pg_bin:
        parser.error("--pg-bin or POLYPBASE_PG18_BIN is required")
    bin_path = Path(args.pg_bin).resolve()

    def exe(name):
        path = bin_path / (name + (".exe" if os.name == "nt" else ""))
        if not path.is_file():
            parser.error(f"Missing executable: {path}")
        return str(path)

    version = subprocess.run([exe("postgres"), "--version"], capture_output=True, text=True, check=True, timeout=10)
    if " 18." not in version.stdout:
        parser.error("PostgreSQL 18 is required")
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("PG", "POSTGRES_", "POLYPBASE_QA_"))}
    environment.update(
        PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1", POSTGRES_DB="",
        DJANGO_SECRET_KEY="django-insecure-disposable-additive-qa-only", DJANGO_DEBUG="0",
        POLYPBASE_TEST_POSTGRES="1", POLYPBASE_HISTORICAL_POSTGRES_QA="1",
    )
    temporary = tempfile.mkdtemp(prefix="cca_eob_additive_pg18_")
    server = log = None
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        if port == 55438:
            raise RuntimeError("Refusing the forbidden shared port")
        data = str(Path(temporary) / "data")
        subprocess.run([exe("initdb"), "-D", data, "-U", "postgres", "--auth=trust", "-E", "UTF8", "--locale=C"],
                       check=True, capture_output=True, env=environment, timeout=180)
        log = open(Path(temporary) / "server.log", "wb")
        options = ["-p", str(port), "-c", "listen_addresses=127.0.0.1", "-c", "fsync=off"]
        if os.name != "nt":
            options += ["-c", f"unix_socket_directories={temporary}"]
        server = subprocess.Popen([exe("postgres"), "-D", data, *options], stdout=log, stderr=log, env=environment)

        def sql(text, database="postgres"):
            return subprocess.run([exe("psql"), "-X", "-q", "-v", "ON_ERROR_STOP=1", "-At",
                                   "-h", "127.0.0.1", "-p", str(port), "-U", "postgres", "-d", database],
                                  input=text, capture_output=True, text=True, env=environment, timeout=30)

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if server.poll() is not None:
                raise RuntimeError("Fresh PostgreSQL cluster exited before readiness")
            if sql("SELECT 1").returncode == 0:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("Fresh PostgreSQL cluster readiness timed out")
        result = sql("CREATE ROLE polypbase LOGIN; CREATE ROLE cca_additive_runtime LOGIN; CREATE DATABASE cca_additive OWNER polypbase;")
        result.check_returncode()
        result = sql("""ALTER SCHEMA public OWNER TO polypbase;
            REVOKE ALL ON DATABASE cca_additive FROM PUBLIC;
            GRANT CONNECT ON DATABASE cca_additive TO polypbase, cca_additive_runtime;
            REVOKE CREATE ON SCHEMA public FROM PUBLIC;
            GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO polypbase, cca_additive_runtime;""", "cca_additive")
        result.check_returncode()
        result = sql("SELECT system_identifier::text FROM pg_catalog.pg_control_system()", "cca_additive")
        result.check_returncode()
        identifier = result.stdout.strip()
        migration_environment = {**environment, "POSTGRES_DB": "cca_additive", "POSTGRES_USER": "polypbase",
                                 "POSTGRES_PASSWORD": "", "POSTGRES_HOST": "127.0.0.1", "POSTGRES_PORT": str(port),
                                 "POSTGRES_SSLMODE": "disable", "DJANGO_SETTINGS_MODULE": "config.settings"}
        subprocess.run([sys.executable, "-B", str(ROOT / "backend/manage.py"), "migrate", "--noinput", "--verbosity=0"],
                       check=True, env=migration_environment, cwd=ROOT / "backend", timeout=300)
        environment.update(
            POLYPBASE_QA_HOST="127.0.0.1", POLYPBASE_QA_PORT=str(port), POLYPBASE_QA_DATABASE="cca_additive",
            POLYPBASE_QA_OWNER="polypbase", POLYPBASE_QA_RUNTIME="cca_additive_runtime",
            POLYPBASE_QA_SYSTEM_IDENTIFIER=identifier, DJANGO_SETTINGS_MODULE="config.historical_temperature_pgqa_settings",
        )
        print("Running synthetic additive tests on a newly initialized loopback-only PG18 cluster", flush=True)
        return subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--worker", *args.labels],
                              env=environment, cwd=ROOT / "backend", timeout=900).returncode
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
        if log is not None:
            log.close()
        shutil.rmtree(temporary)


if __name__ == "__main__":
    raise SystemExit(main())
