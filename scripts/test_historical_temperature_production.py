"""Run synthetic tests in a newly allocated PostgreSQL 18 cluster, never an existing one."""

import argparse
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile

from historical_temperature_cluster_lease import capture_launch, run_django_tests

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--pg-bin", type=Path, required=True)
    parser.add_argument("labels", nargs="+", help="Explicit Django apps.* test labels")
    args = parser.parse_args()
    if not args.run:
        parser.error("--run is required to create a fresh disposable cluster")
    if any(not re.fullmatch(r"apps\.[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", label) for label in args.labels):
        parser.error("Only explicit apps.* test labels are accepted")

    suffix = ".exe" if os.name == "nt" else ""
    binaries = {name: args.pg_bin.resolve() / (name + suffix)
                for name in ("initdb", "pg_ctl", "postgres", "pg_dump", "pg_restore", "pg_controldata")}
    if not all(path.is_file() for path in binaries.values()):
        parser.error("Expected an installed PostgreSQL binary directory")
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("PG", "POSTGRES_", "POLYPBASE_", "DJANGO_"))}
    env.update(PYTHON_DOTENV_DISABLED="1", POSTGRES_DB="",
               DJANGO_SECRET_KEY="django-insecure-disposable-synthetic-tests-only", LC_ALL="C", LANGUAGE="C")
    version = subprocess.check_output([str(binaries["postgres"]), "--version"], env=env, text=True, timeout=15)
    if not re.search(r"PostgreSQL\) 18\.", version):
        parser.error("PostgreSQL 18 is required")
    print(version.strip(), flush=True)
    # The port is allocated independently; a bind race fails startup, never reuses a server.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    directory = Path(tempfile.mkdtemp(prefix="polypbase-ht-production-synthetic-"))
    data = directory / "data"


    def run(name, *arguments, timeout=60):
        with (directory / (name + ".log")).open("w", encoding="utf-8") as log:
            result = subprocess.run([str(binaries[name]), *map(str, arguments)], env=env,
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        if result.returncode:
            print((directory / (name + ".log")).read_text(encoding="utf-8", errors="replace"))
            result.check_returncode()
        return result

    try:
        run("initdb", "-D", data, "--username=subculture_qa", "--auth=trust", "--encoding=UTF8", "--no-locale", "--no-sync", "-c", "fsync=off", timeout=180)
        control = subprocess.check_output(
            [str(binaries["pg_controldata"]), str(data)], env=env, text=True, timeout=15,
        )
        match = re.search(r"^Database system identifier:\s+(\d+)\s*$", control, re.MULTILINE)
        if match is None:
            raise RuntimeError("Could not identify this invocation's initdb cluster")
        system_identifier = match.group(1)
        with (data / "postgresql.conf").open("a", encoding="ascii") as stream:
            stream.write(f"\nlisten_addresses='127.0.0.1'\nunix_socket_directories=''\nport={port}\nmax_connections=60\n"
                                     "fsync=off\nsynchronous_commit=off\nfull_page_writes=off\n")
        print(f"Fresh synthetic cluster only: {directory.name}, loopback port {port}", flush=True)
        try:
            run("pg_ctl", "-D", data, "-l", directory / "server.log", "-w", "-t", "30", "start")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            try:
                print("PostgreSQL startup failed; server.log:", flush=True)
                print((directory / "server.log").read_text(encoding="utf-8", errors="replace"), flush=True)
            except (OSError, UnicodeError) as error:
                print(f"Could not read or print server.log: {ascii(error)}", flush=True)
            raise

        launch = capture_launch(data, port, system_identifier)

        settings = directory / "ht_disposable_settings.py"
        settings.write_text(
            "from historical_temperature_cluster_lease import require_launch, initialize_database\n"
            "require_launch()\n"
            "initialize_database()\n"
            "import os, dotenv\n"
            "dotenv.load_dotenv = lambda *a, **k: False\n"
            "from config.test_settings import *\n"
            "TEST_RUNNER = 'historical_temperature_disposable_runner.DisposableRunner'\n"
            "DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', "
            "'NAME': 'subculture_qa', 'USER': 'subculture_qa', 'PASSWORD': '', "
            f"'HOST': '127.0.0.1', 'PORT': '{port}', "
            "'TEST': {'NAME': 'test_subculture_qa'}, "
            "'OPTIONS': {'options': '-c lock_timeout=10000 -c statement_timeout=120000'}}}\n",
            encoding="utf-8")
        env["PYTHONPATH"] = os.pathsep.join(map(str, (directory, ROOT / "backend", ROOT / "scripts")))
        env["POLYPBASE_HISTORICAL_TEMPERATURE_DISPOSABLE_CLUSTER"] = str(data.resolve())
        report_dir = ROOT / ".local-docs"
        report_dir.mkdir(exist_ok=True)
        report = report_dir / "historical-temperature-production-tests.log"
        print(f"Test output: {report.relative_to(ROOT)}", flush=True)
        try:
            return run_django_tests(
                ["manage.py", "test", *args.labels, "--settings=ht_disposable_settings", "--noinput", "-v", "2"],
                cwd=ROOT / "backend", env=env, report=report, launch=launch, timeout=1800,
            )
        finally:
            if report.is_file():
                print(report.read_text(encoding="utf-8", errors="replace"), flush=True)
    finally:
        original_error = sys.exc_info()[1]
        try:
            # Lifecycle operations are confined to the directory created above in this process.
            status = subprocess.run([str(binaries["pg_ctl"]), "-D", str(data), "status"],
                                    env=env, capture_output=True, timeout=15)
            if status.returncode == 0:
                run("pg_ctl", "-D", data, "-m", "immediate", "-w", "-t", "15", "stop", timeout=25)
                status = subprocess.run([str(binaries["pg_ctl"]), "-D", str(data), "status"],
                                        env=env, capture_output=True, timeout=15)
                if status.returncode != 3:
                    raise RuntimeError(
                        f"pg_ctl status after stop returned {status.returncode}; "
                        "expected 3 to verify the cluster is not running"
                    )
            elif status.returncode != 3:
                raise RuntimeError(
                    f"pg_ctl status returned {status.returncode}; "
                    "only 3 proves the cluster is not running"
                )
            shutil.rmtree(directory)
            print("Fresh synthetic cluster stopped and removed; no existing cluster changed.", flush=True)
        except Exception as error:
            print(f"Fresh synthetic cluster cleanup failed: {type(error).__name__}: {error}; "
                  f"retained directory: {directory}", flush=True)
            if original_error is None:
                raise


if __name__ == "__main__":
    raise SystemExit(main())
