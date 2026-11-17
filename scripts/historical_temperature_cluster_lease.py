"""In-memory launch provenance, handed only to the launcher's spawned child.

This is an accidental-target / independent-invocation guard, not a sandbox against
arbitrary code running as the same OS user. No environment variable or file grants
provisioning authority. The launcher and this bootstrap are trusted code.
"""

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
import multiprocessing
import os
from pathlib import Path
import runpy
import subprocess
import sys
import traceback


@dataclass(frozen=True)
class Launch:
    launcher_pid: int
    data: str
    port: int
    system_identifier: str
    postmaster_pid: int
    started_at: int


_active_launch = None


def capture_launch(data, port, system_identifier):
    """Called after this invocation's initdb and pg_ctl, never from settings."""
    lines = (data / "postmaster.pid").read_text(encoding="utf-8").splitlines()
    if (Path(lines[1]).resolve() != data.resolve() or int(lines[3]) != port
            or not system_identifier.isdecimal() or int(lines[0]) <= 0):
        raise RuntimeError("Fresh cluster startup identity mismatch")
    return Launch(os.getpid(), str(data.resolve()), port, system_identifier,
                  int(lines[0]), int(lines[2]))


def require_launch():
    """Fail before any database access, including alternative runner entrypoints."""
    launch = _active_launch
    parent = multiprocessing.parent_process()
    if (launch is None or parent is None or parent.pid != launch.launcher_pid
            or os.getppid() != launch.launcher_pid or not parent.is_alive()):
        raise RuntimeError("A live disposable launcher child is required; environment markers grant no authority")
    # A restart or directory substitution invalidates the current invocation.
    if capture_launch(Path(launch.data), launch.port, launch.system_identifier) != Launch(
            os.getpid(), launch.data, launch.port, launch.system_identifier,
            launch.postmaster_pid, launch.started_at):
        raise RuntimeError("Stale disposable cluster startup identity")
    return launch


def _backend_identity(connection):
    # These reads remain available after SET ROLE to the protected NOLOGIN owner;
    # access to pg_stat_activity details can depend on the active role.
    return connection.execute(
        "SELECT pg_catalog.current_setting('server_version_num')::int, "
        "pg_catalog.pg_postmaster_start_time(), "
        "session_user, current_user, pg_catalog.pg_backend_pid(), pg_catalog.current_database(), "
        "pg_catalog.host(pg_catalog.inet_client_addr()), pg_catalog.inet_client_port(), "
        "pg_catalog.host(pg_catalog.inet_server_addr()), pg_catalog.inet_server_port()"
    ).fetchone()


def verify_server(connection, *, expected_user="subculture_qa", expected_role=None):
    launch = require_launch()
    expected_role = expected_user if expected_role is None else expected_role
    if expected_user != "subculture_qa":
        before = _backend_identity(connection)
        if (before is None or not 180000 <= before[0] < 190000
                or before[1] is None or before[2] != expected_user
                or before[3] != expected_role or before[4] != connection.info.backend_pid
                or before[6] != "127.0.0.1" or not isinstance(before[7], int) or before[7] <= 0
                or before[8:] != ("127.0.0.1", launch.port)):
            raise RuntimeError("Disposable QA session identity mismatch")
        # Bind this exact, still-open target backend to the independently attested
        # instance. The backend PID AND live TCP endpoint must match; neither a
        # PID reused by another session nor a connection to another instance is enough.
        with connect_leased(
            host="127.0.0.1", port=launch.port, dbname="postgres", user="subculture_qa",
            password="", sslmode="disable", connect_timeout=5, autocommit=True,
            options="-c lock_timeout=10000 -c statement_timeout=120000",
        ) as administrator:
            peer = administrator.execute(
                "SELECT backend_start, datname, usename, pg_catalog.host(client_addr), client_port, "
                                "pg_catalog.pg_postmaster_start_time() "
                "FROM pg_catalog.pg_stat_activity WHERE pid=%s AND backend_type='client backend'",
                [before[4]],
            ).fetchone()
            if (peer is None or peer[0] is None
                    or peer[1:] != (before[5], expected_user, before[6], before[7], before[1])):
                raise RuntimeError("Target backend does not belong to the attested disposable instance")
        # Psycopg does not reconnect transparently. A restart loses this socket;
        # a changed role/session/backend during verification must also fail closed.
        if _backend_identity(connection) != before:
            raise RuntimeError("Disposable QA connection changed during attestation")
        require_launch()
        return
    row = connection.execute(
        "SELECT current_setting('server_version_num')::int, "
        "current_setting('data_directory'), session_user, "
        "(pg_control_system()).system_identifier::text, "
        # Compare the recorded PID-file start with the same recorded value, not
        # pg_postmaster_start_time(), which is a separately captured timestamp.
        "split_part(pg_read_file('postmaster.pid'), chr(10), 3)::bigint, "
        "split_part(pg_read_file('postmaster.pid'), chr(10), 1)::int, current_user"
    ).fetchone()
    version, directory, login, identifier, started_at, pid, role = row
    checks = {
        "version": 180000 <= version < 190000,
        "directory": Path(directory).resolve() == Path(launch.data),
        "session_user": login == expected_user, "current_user": role == expected_role,
        "system_identifier": identifier == launch.system_identifier,
        "started_at": started_at == launch.started_at, "postmaster_pid": pid == launch.postmaster_pid,
    }
    if not all(checks.values()):
        raise RuntimeError("Fresh PostgreSQL 18 instance identity mismatch: "
                           + ", ".join(name for name, matches in checks.items() if not matches))
    require_launch()


def connect_leased(*, expected_role=None, **kwargs):
    """Shared gate for QA suites that open psycopg connections outside Django."""
    launch = require_launch()
    if (kwargs.get("host") != "127.0.0.1" or int(kwargs.get("port", 0)) != launch.port
            or kwargs.get("password") != "" or not isinstance(kwargs.get("user"), str)
            or not kwargs["user"]):
        raise RuntimeError("Connection is outside the current disposable launch")
    import psycopg

    connection = psycopg.connect(**kwargs)
    try:
        verify_server(connection, expected_user=kwargs["user"], expected_role=expected_role)
    except BaseException:
        connection.close()
        raise
    return connection


def initialize_database():
    """First SQL mutation uses the very same connection whose identity passed."""
    launch = require_launch()
    with connect_leased(
        host="127.0.0.1", port=launch.port, dbname="postgres", user="subculture_qa",
        password="", sslmode="disable", connect_timeout=5, autocommit=True,
        options="-c lock_timeout=10000 -c statement_timeout=120000",
    ) as connection:
        connection.execute("CREATE DATABASE subculture_qa")


def _django_main(argv, cwd, env, report, launch):
    global _active_launch
    # Spawn imports only stdlib/bootstrap modules before inherited configuration
    # is replaced. Django and application settings are imported afterwards.
    os.environ.clear()
    os.environ.update(env)
    os.chdir(cwd)
    sys.path[:0] = env["PYTHONPATH"].split(os.pathsep)
    sys.argv = argv
    _active_launch = launch
    with open(report, "w", encoding="utf-8") as log, redirect_stdout(log), redirect_stderr(log):
        try:
            require_launch()
            runpy.run_path(str(Path(cwd) / "manage.py"), run_name="__main__")
        except Exception:
            traceback.print_exc()
            raise SystemExit(1) from None


def run_django_tests(argv, *, cwd, env, report, launch, timeout=1800):
    """Use spawn on every OS: no lease is accepted from argv, environment or disk."""
    process = multiprocessing.get_context("spawn").Process(
        target=_django_main, args=(argv, str(cwd), env, str(report), launch),
    )
    try:
        process.start()
        process.join(timeout)
        if process.is_alive():
            raise subprocess.TimeoutExpired(["manage.py", "test"], timeout)
        return process.exitcode
    finally:
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
                process.join(10)
                if process.is_alive():
                    process.kill()
                    process.join(10)
                if process.is_alive():
                    raise RuntimeError("Disposable Django worker could not be stopped")
            process.close()
