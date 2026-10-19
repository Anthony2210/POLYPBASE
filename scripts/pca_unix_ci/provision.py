"""Root-only provisioning for an ephemeral, network-none Linux CI container.

Never run on a host. Local trust authentication is ONLY for this disposable,
network-isolated container, not a production authentication recommendation.

Child protocol: run_tests.py receives one JSON object on stdin, containing exactly
owner_password, runtime_password and system_identifier. No arguments means the
full synthetic suite. --probe CASE runs real production guards without a fixture;
exit 0 means the expected success/refusal was asserted (not merely caught).
Cases and required refusal fragments:
  success             no refusal
  socket-owner        Unsafe PostgreSQL socket ownership or type
  socket-group        Untrusted PostgreSQL socket endpoint or runtime identity
  socket-acl          Runtime must not be able to write
  socket-world-write  Unsafe PostgreSQL socket directory
  server-identity     Live database identity/session state differs
  role-privileges     Unsafe PostgreSQL role privileges
All children use the same clean environment; only socket-group adds postgres GID.
The root marker /etc/polypbase/pca-unix-ci.json is independent of child input.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys

ROOT = Path("/opt/pca")
DATA = Path("/var/lib/postgresql/pca-unix-ci")
SOCKET_DIRECTORY = Path("/run/postgresql")
PORT = 65432
DATABASE = "synthetic_pca_ci"
OWNER = "synthetic_pca_owner"
RUNTIME = "synthetic_pca_runtime"
RUNTIME_UID = RUNTIME_GID = 10001
ATTESTATION = Path("/etc/polypbase/pca-production-target.json")
MARKER = Path("/etc/polypbase/pca-unix-ci.json")

# Explicitly mirror production.IDENTITY_SQL without importing Django/settings.
IDENTITY_SQL = """
SELECT current_database(), d.oid::bigint, session_user::text, current_user::text,
       r.oid::bigint, host(inet_server_addr()), inet_server_port(),
       to_char(pg_postmaster_start_time() AT TIME ZONE 'UTC',
               'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'), current_setting('server_version_num')::integer,
       current_setting('session_replication_role'), current_schemas(false),
       pg_is_in_recovery(), current_setting('port')::integer
FROM pg_database d, pg_roles r
WHERE d.datname = current_database() AND r.rolname = current_user
"""


def clean_environment():
    """Do not inherit image PGDATA, host credentials, Python paths or libpq hints."""
    return {
        "PATH": "/opt/venv/bin:/usr/lib/postgresql/18/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/opt/pca/.local-docs",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHON_DOTENV_DISABLED": "1",
    }


def run(command, *, timeout=30, **kwargs):
    return subprocess.run(command, check=True, timeout=timeout,
                          cwd=ROOT, env=clean_environment(), **kwargs)


def postgres_command(*arguments, timeout=30, **kwargs):
    return run(["runuser", "--user", "postgres", "--", *arguments],
               timeout=timeout, **kwargs)


def trusted_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError(f"Expected a root-owned, non-writable directory: {path}")


def preflight():
    if sys.platform != "linux" or os.getuid() != 0 or os.geteuid() != 0:
        raise RuntimeError("Provisioning requires Linux container root")
    import pwd

    if not Path("/.dockerenv").is_file() or set(os.listdir("/sys/class/net")) != {"lo"}:
        raise RuntimeError("Provisioning requires a Docker container with --network none")
    if Path(__file__).resolve() != ROOT / "scripts/pca_unix_ci/provision.py" or Path.cwd() != ROOT:
        raise RuntimeError("Provisioning is allowed only under /opt/pca, never /root or a host checkout")
    for path in (Path("/"), Path("/opt"), ROOT, ROOT / "scripts", ROOT / "scripts/pca_unix_ci",
                 ROOT / "backend", Path("/etc"), Path("/var"), Path("/run")):
        trusted_directory(path)
    alias = Path("/var/run").lstat()
    if not stat.S_ISLNK(alias.st_mode) or alias.st_uid != 0 or os.readlink("/var/run") != "/run":
        raise RuntimeError("Expected the root-owned /var/run -> /run alias")
    if os.path.lexists(DATA) or os.path.lexists(ATTESTATION) or os.path.lexists(MARKER):
        raise RuntimeError("Refusing an existing cluster or CI attestation; use a fresh container")
    if any(ROOT.rglob(".env")):
        raise RuntimeError("Host .env files must never be present in the CI image")
    runtime = pwd.getpwnam("pca-runtime")
    postgres = pwd.getpwnam("postgres")
    if ((runtime.pw_uid, runtime.pw_gid) != (RUNTIME_UID, RUNTIME_GID)
            or postgres.pw_uid in (0, RUNTIME_UID) or postgres.pw_gid in (0, RUNTIME_GID)
            or postgres.pw_gid in os.getgrouplist("pca-runtime", RUNTIME_GID)):
        raise RuntimeError("Expected distinct dedicated postgres and unprivileged runtime OS accounts")
    artifacts = ROOT / ".local-docs"
    info = artifacts.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != RUNTIME_UID
            or info.st_gid != RUNTIME_GID or stat.S_IMODE(info.st_mode) != 0o700):
        raise RuntimeError("Expected the private runtime-owned artifact directory")
    return postgres


def machine_id():
    path = Path("/etc/machine-id")
    if not os.path.lexists(path):
        with path.open("x", encoding="ascii") as stream:
            stream.write(secrets.token_hex(16) + "\n")
        path.chmod(0o444)
    else:
        info = path.lstat()
        # Minimal Debian images may ship an empty machine-id. It is safe to
        # initialize only that root-owned image placeholder inside this container.
        if stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_size == 0:
            path.write_text(secrets.token_hex(16) + "\n", encoding="ascii")
            path.chmod(0o444)
    info = path.lstat()
    value = path.read_text(encoding="ascii").strip()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
            or not re.fullmatch(r"[0-9a-f]{32}", value) or value == "0" * 32):
        raise RuntimeError("Expected a valid root-owned machine-id")
    return value


def write_json(path, value, *, exclusive=False):
    with path.open("x" if exclusive else "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    path.chmod(0o644)


def connect(*, database=DATABASE, role="postgres", password=None):
    import psycopg

    # Explicit parameters and a clean process environment prevent libpq discovery.
    return psycopg.connect(host="/var/run/postgresql", port=PORT, dbname=database,
                           user=role, password=password, connect_timeout=5,
                           passfile="/dev/null", sslmode="disable", autocommit=True,
                           options="-c statement_timeout=10000 -c lock_timeout=5000")


def initialize_roles(owner_password, runtime_password):
    from psycopg import sql

    with connect(database="postgres") as connection:
        for role, password in ((OWNER, owner_password), (RUNTIME, runtime_password)):
            # Literal composition is needed for CREATE ROLE's PASSWORD grammar.
            # Never emit the composed SQL or exceptions containing credentials.
            connection.execute(sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(sql.Identifier(role), sql.Literal(password)))
        connection.execute(sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0").format(
            sql.Identifier(DATABASE), sql.Identifier(OWNER)))
    with connect() as connection:
        connection.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(DATABASE)))
        connection.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
            sql.Identifier(DATABASE), sql.Identifier(OWNER), sql.Identifier(RUNTIME)))
        connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(RUNTIME)))
        connection.execute("REVOKE ALL ON FUNCTION pg_catalog.pg_control_system() FROM PUBLIC")
        connection.execute(sql.SQL("GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO {}, {}").format(
            sql.Identifier(OWNER), sql.Identifier(RUNTIME)))


def attest(postgres, identity):
    # Read control data as the cluster OS owner, independently of the SQL channel.
    control = postgres_command("pg_controldata", str(DATA), capture_output=True, text=True).stdout
    matches = re.findall(r"^Database system identifier:\s*([0-9]+)\s*$", control, re.MULTILINE)
    if len(matches) != 1:
        raise RuntimeError("Cannot independently attest the new cluster system identifier")
    system_identifier = matches[0]
    with connect() as connection:
        if connection.execute("SELECT system_identifier::text FROM pg_catalog.pg_control_system()").fetchone()[0] != system_identifier:
            raise RuntimeError("Control-file and SQL system identifiers differ")
    with connect(role=RUNTIME) as connection:
        row = connection.execute(IDENTITY_SQL).fetchone()
    if (row[0] != DATABASE or row[2:4] != (RUNTIME, RUNTIME)
            or row[5:7] != (None, None) or row[9:] != ("origin", ["public"], False, PORT)
            or not 180000 <= row[8] < 190000):
        raise RuntimeError("Fresh PostgreSQL identity does not match the synthetic target")
    target = {
        "schema": "pca_target_v3", "machine_id": identity,
        "configured_host": "/var/run/postgresql", "configured_port": str(PORT),
        "database": DATABASE, "role": RUNTIME, "database_oid": row[1], "role_oid": row[4],
        "server_address": None, "server_port": None,
        "postmaster_started_at": row[7], "server_version_num": row[8],
        "connection_mode": "unix", "socket_uid": postgres.pw_uid, "socket_gid": postgres.pw_gid,
    }
    ATTESTATION.parent.mkdir(mode=0o755, exist_ok=True)
    trusted_directory(ATTESTATION.parent)
    write_json(ATTESTATION, target, exclusive=True)
    write_json(MARKER, {
        "schema": "pca_unix_ci_v1", "container_only": True, "network": "none",
        "system_identifier": system_identifier, "database": DATABASE, "port": PORT,
        "owner": OWNER, "runtime": RUNTIME,
    }, exclusive=True)
    return system_identifier, target


def child(payload, *, probe=None, postgres_gid=None):
    environment = clean_environment()
    environment.update({
        "DJANGO_SETTINGS_MODULE": "config.historical_temperature_pgqa_settings",
        "DJANGO_DEBUG": "0",
        "DJANGO_SECRET_KEY": "django-insecure-disposable-pca-unix-ci-only",
        "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
        "EMAIL_DELIVERY_ENABLED": "0",
        "POLYPBASE_TEST_POSTGRES": "1",
        "POLYPBASE_HISTORICAL_POSTGRES_QA": "1",
        "POLYPBASE_QA_HOST": "127.0.0.1", "POLYPBASE_QA_PORT": str(PORT),
        "POLYPBASE_QA_DATABASE": DATABASE, "POLYPBASE_QA_OWNER": OWNER,
        "POLYPBASE_QA_RUNTIME": RUNTIME,
        "POLYPBASE_QA_SYSTEM_IDENTIFIER": payload["system_identifier"],
    })
    command = ["setpriv", f"--reuid={RUNTIME_UID}", f"--regid={RUNTIME_GID}",
               "--clear-groups" if postgres_gid is None else f"--groups={postgres_gid}",
               "--no-new-privs", "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all",
               "/opt/venv/bin/python", "-B", "/opt/pca/scripts/pca_unix_ci/run_tests.py"]
    if probe is not None:
        command.extend(["--probe", probe])
    print(f"CI child: {probe or 'full suite'}", flush=True)
    subprocess.run(command, cwd=ROOT, env=environment, input=json.dumps(payload) + "\n",
                   text=True, check=True, timeout=600 if probe is None else 30)


def probes(payload, target, postgres):
    socket = SOCKET_DIRECTORY / f".s.PGSQL.{PORT}"
    child(payload, probe="success")
    for case in ("socket-owner", "socket-group", "socket-acl", "socket-world-write",
                 "server-identity", "role-privileges"):
        try:
            if case == "socket-owner":
                os.chown(socket, 0, postgres.pw_gid)
            elif case == "socket-acl":
                run(["setfacl", "-m", f"u:{RUNTIME_UID}:rwx", str(SOCKET_DIRECTORY)])
            elif case == "socket-world-write":
                SOCKET_DIRECTORY.chmod(0o2777)
            elif case == "server-identity":
                write_json(ATTESTATION, {**target, "postmaster_started_at": "2000-01-01T00:00:00.000000Z"})
            elif case == "role-privileges":
                with connect() as connection:
                    connection.execute(f"ALTER ROLE {RUNTIME} CREATEDB")
            child(payload, probe=case,
                  postgres_gid=postgres.pw_gid if case == "socket-group" else None)
        finally:
            # Restore even when the child times out or fails its assertion.
            if case == "socket-owner":
                os.chown(socket, postgres.pw_uid, postgres.pw_gid)
            elif case == "socket-acl":
                run(["setfacl", "-b", str(SOCKET_DIRECTORY)])
                SOCKET_DIRECTORY.chmod(0o2775)
            elif case == "socket-world-write":
                SOCKET_DIRECTORY.chmod(0o2775)
            elif case == "server-identity":
                write_json(ATTESTATION, target)
            elif case == "role-privileges":
                with connect() as connection:
                    connection.execute(f"ALTER ROLE {RUNTIME} NOCREATEDB")
            child(payload, probe="success")


def interrupted(signum, frame):
    raise SystemExit(128 + signum)


def main():
    postgres = preflight()
    # Discard all inherited configuration before even opening a root SQL session.
    os.environ.clear()
    os.environ.update(clean_environment())
    os.umask(0o022)
    identity = machine_id()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, interrupted)
    DATA.mkdir(mode=0o700)
    os.chown(DATA, postgres.pw_uid, postgres.pw_gid)
    # The base image supplies this directory, but its default mode is too open.
    if SOCKET_DIRECTORY.is_symlink():
        raise RuntimeError("Socket directory must not be a symbolic link")
    SOCKET_DIRECTORY.mkdir(mode=0o2775, exist_ok=True)
    run(["setfacl", "-b", "-k", str(SOCKET_DIRECTORY)])
    os.chown(SOCKET_DIRECTORY, postgres.pw_uid, postgres.pw_gid)
    SOCKET_DIRECTORY.chmod(0o2775)
    owner_password, runtime_password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    # Once initdb has been attempted, only this fresh path can be stopped below.
    try:
        postgres_command("initdb", "-D", str(DATA), "--auth-local=trust",
                         "--auth-host=scram-sha-256", "--encoding=UTF8", "--locale=C.UTF-8", timeout=60)
        with (DATA / "postgresql.conf").open("a", encoding="ascii") as stream:
            stream.write("\nlisten_addresses = '127.0.0.1'\nport = 65432\n"
                         "unix_socket_directories = '/run/postgresql'\n"
                         "unix_socket_permissions = 0777\npassword_encryption = 'scram-sha-256'\n")
        postgres_command("pg_ctl", "-D", str(DATA), "-l", str(DATA / "server.log"),
                         "-w", "-t", "30", "start", timeout=40)
        initialize_roles(owner_password, runtime_password)
        system_identifier, target = attest(postgres, identity)
        payload = {"owner_password": owner_password, "runtime_password": runtime_password,
                   "system_identifier": system_identifier}
        child(payload)
        probes(payload, target, postgres)
    finally:
        if (DATA / "postmaster.pid").exists():
            postgres_command("pg_ctl", "-D", str(DATA), "-m", "immediate", "-w", "-t", "5", "stop", timeout=10)
    print("Isolated PCA Unix socket suite and production-guard probes passed", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        # SQL errors can include CREATE ROLE statements: do not leak credentials.
        print(f"STOP: isolated PCA CI failed ({type(error).__name__}); no target is authorized", file=sys.stderr)
        raise SystemExit(1) from None
