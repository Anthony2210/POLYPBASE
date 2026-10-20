"""Probe cross-login pg_dump snapshots using ONLY a newly initialized PG18 cluster.

Requires Python with psycopg 3 and existing PostgreSQL 18 binaries; installs nothing.
Example (Windows): python scripts/probe_pg18_exported_snapshot.py --bin-dir
"C:/Program Files/PostgreSQL/18/bin" --runs 2
On Linux, --peer proves Unix socket peer mapping of the current OS account to
both synthetic SQL logins (no OS users, sudo, or SET ROLE). No external DSN exists.
"""

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time


class Probe:
    def __init__(self, binaries, timeout, peer):
        self.binaries = binaries
        self.deadline = time.monotonic() + timeout
        self.peer = peer
        self.root = Path(tempfile.mkdtemp(prefix="pg18-snapshot-probe-"))
        self.data = self.root / "cluster"
        self.log = self.root / "server.log"
        self.started = False
        self.start_attempted = False
        self.connections = []
        self.env = {key: value for key, value in os.environ.items()
                    if not key.upper().startswith(("PG", "POSTGRES_"))}
        self.env.update(PGCONNECT_TIMEOUT="5", PGPASSFILE=str(self.root / "no-password-file"),
                        PGSYSCONFDIR=str(self.root))
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.host = str(self.root) if peer else "127.0.0.1"

    def remaining(self):
        seconds = self.deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError("Disposable probe deadline exceeded")
        return seconds

    def tool(self, name, *args):
        # Windows server descendants can retain pipe handles after pg_ctl exits.
        # Regular files avoid an unbounded communicate() waiting for inherited EOF.
        with (self.root / "tools.log").open("ab") as output:
            return subprocess.run([str(self.binaries / name), *map(str, args)],
                                  env=self.env, stdin=subprocess.DEVNULL,
                                  stdout=output, stderr=output,
                                  creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
                                  timeout=min(30, self.remaining()), check=True)

    def connect(self, user, database="snapshot_source"):
        import psycopg
        # Pass every routing/authentication parameter explicitly, not inherited libpq defaults.
        connection = psycopg.connect(host=self.host, port=self.port, user=user,
                                     dbname=database, password="", passfile=str(self.root / "no-password-file"),
                                     sslmode="disable", connect_timeout=5,
                                     options="-c statement_timeout=10000 -c lock_timeout=5000",
                                     autocommit=True)
        self.connections.append(connection)
        return connection

    def start(self):
        self.tool("initdb", "-D", self.data, "-U", "probe_admin", "--no-locale",
                  "--encoding=UTF8", "--auth-local=trust", "--auth-host=trust")
        config = ["listen_addresses = '127.0.0.1'", f"port = {self.port}",
                  "logging_collector = off", "log_connections = on", "log_statement = 'all'",
                  "log_line_prefix = '%u|%d| '", "max_connections = 10", "shared_buffers = '16MB'"]
        if os.name != "nt":
            config.append("unix_socket_directories = '" + str(self.root).replace("'", "''") + "'")
        (self.data / "postgresql.conf").write_text("\n".join(config) + "\n", encoding="utf-8")
        if self.peer:
            username = getpass.getuser()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", username):
                raise RuntimeError("OS username cannot safely be encoded in the private peer map")
            (self.data / "pg_ident.conf").write_text(
                "".join(f"probe_map {username} {role}\n" for role in
                        ("probe_admin", "probe_exporter", "probe_owner")), encoding="utf-8")
            (self.data / "pg_hba.conf").write_text("local all all peer map=probe_map\n", encoding="utf-8")
        else:
            (self.data / "pg_hba.conf").write_text("host all all 127.0.0.1/32 trust\n", encoding="utf-8")
        self.start_attempted = True
        self.tool("pg_ctl", "-D", self.data, "-l", self.log, "-w", "-t", "15", "start")
        self.started = True

    def run(self):
        import psycopg
        self.start()
        admin = self.connect("probe_admin", "postgres")
        actual_data, version = admin.execute("SELECT current_setting('data_directory'), current_setting('server_version_num')::int").fetchone()
        if Path(actual_data).resolve() != self.data.resolve() or not 180000 <= version < 190000:
            raise RuntimeError("Fresh cluster identity or PostgreSQL 18 check failed")
        admin.execute("CREATE ROLE probe_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT")
        admin.execute("CREATE ROLE probe_exporter LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT")
        admin.execute("CREATE DATABASE snapshot_source OWNER probe_owner")
        admin.execute("CREATE DATABASE snapshot_restored OWNER probe_owner")
        source_admin = self.connect("probe_admin")
        source_admin.execute("REVOKE ALL ON DATABASE snapshot_source FROM PUBLIC")
        source_admin.execute("GRANT CONNECT ON DATABASE snapshot_source TO probe_owner, probe_exporter")
        owner = self.connect("probe_owner")
        owner.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        owner.execute("CREATE TABLE public.probe_measurements (id integer PRIMARY KEY, value integer NOT NULL)")
        owner.execute("CREATE TABLE public.unrelated_private (id integer PRIMARY KEY)")
        owner.execute("INSERT INTO public.probe_measurements VALUES (1, 0)")
        owner.execute("INSERT INTO public.unrelated_private VALUES (99)")
        owner.execute("GRANT USAGE ON SCHEMA public TO probe_exporter")
        owner.execute("GRANT SELECT ON public.probe_measurements TO probe_exporter")
        exporter = self.connect("probe_exporter")
        for connection, role in ((exporter, "probe_exporter"), (owner, "probe_owner")):
            row = connection.execute("SELECT session_user, current_user, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
            assert row == (role, role, False, False, False, False, False), row
        assert exporter.execute("SELECT pg_has_role(current_user, 'probe_owner', 'MEMBER'), (SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user)), has_database_privilege(current_user, current_database(), 'CREATE'), has_schema_privilege(current_user, 'public', 'CREATE'), (SELECT tableowner=current_user FROM pg_tables WHERE schemaname='public' AND tablename='probe_measurements')").fetchone() == (False, 0, False, False, False)
        exporter.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        exporter.execute("SAVEPOINT permission_probe")
        try:
            exporter.execute("SELECT * FROM public.unrelated_private")
        except psycopg.errors.InsufficientPrivilege:
            exporter.execute("ROLLBACK TO SAVEPOINT permission_probe")
        else:
            raise AssertionError("Restricted exporter read an unrelated private table")
        exporter.execute("RELEASE SAVEPOINT permission_probe")
        snapshot = exporter.execute("SELECT pg_export_snapshot()").fetchone()[0]
        assert exporter.execute("SELECT id,value FROM public.probe_measurements ORDER BY id").fetchall() == [(1, 0)]
        owner.execute("INSERT INTO public.probe_measurements VALUES (2, 42)")
        assert owner.execute("SELECT count(*) FROM public.probe_measurements").fetchone() == (2,)
        archive = self.root / "snapshot.dump"
        common = ["--host", self.host, "--port", self.port, "--username", "probe_owner", "--no-password"]
        self.tool("pg_dump", *common, "--dbname", "snapshot_source", "--format=custom",
                  "--snapshot", snapshot, "--file", archive)
        assert exporter.execute("SELECT current_setting('transaction_isolation'), current_setting('transaction_read_only'), session_user, current_user").fetchone() == ("repeatable read", "on", "probe_exporter", "probe_exporter")
        assert exporter.execute("SELECT id,value FROM public.probe_measurements ORDER BY id").fetchall() == [(1, 0)]
        assert exporter.info.transaction_status == psycopg.pq.TransactionStatus.INTRANS
        print("GATE PASS: PG18 cross-login pg_dump consumed the live restricted RR READ ONLY snapshot without SET ROLE", flush=True)
        self.tool("pg_restore", *common, "--dbname", "snapshot_restored", "--exit-on-error", archive)
        restored = self.connect("probe_owner", "snapshot_restored")
        assert restored.execute("SELECT id,value FROM public.probe_measurements ORDER BY id").fetchall() == [(1, 0)]
        assert restored.execute("SELECT id FROM public.unrelated_private").fetchall() == [(99,)]
        exporter.execute("ROLLBACK")
        statements = self.log.read_text(encoding="utf-8", errors="replace")
        assert "probe_owner|snapshot_source|" in statements
        assert f"SET TRANSACTION SNAPSHOT '{snapshot}'" in statements
        assert not re.search(r"\bSET\s+(?:LOCAL\s+|SESSION\s+)?ROLE\b", statements, re.I)
        if self.peer:
            assert 'method=peer' in statements
        return {"status": "passed", "postgresql_version_num": version,
                "transport": "unix-peer" if self.peer else "loopback-trust-private-cluster",
                "exporter": "nonowner, no role memberships, restricted SELECT, repeatable read read only",
                "dump_login": "different non-superuser database/table owner; no SET ROLE",
                "unrelated_table_read": "denied with SQLSTATE 42501",
                "post_export_committed_row": "present in source, absent in restored database",
                "zero_measurement": "preserved", "real_pg_restore": "passed"}

    def cleanup(self):
        for connection in reversed(self.connections):
            connection.close()
        if self.start_attempted:
            # pg_ctl acts only on the PID/data directory created by this instance.
            with (self.root / "cleanup.log").open("ab") as output:
                result = subprocess.run([str(self.binaries / "pg_ctl"), "-D", str(self.data),
                                         "-m", "immediate", "-w", "-t", "15", "stop"],
                                        env=self.env, stdin=subprocess.DEVNULL,
                                        stdout=output, stderr=output, timeout=20)
            if result.returncode and (self.data / "postmaster.pid").exists():
                raise RuntimeError(f"Own cluster shutdown failed; retained at {self.root}")
        shutil.rmtree(self.root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", required=True, type=Path, help="Existing local PostgreSQL 18 binaries")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=90, help="Per-run work deadline; cleanup has a separate 20s bound")
    parser.add_argument("--peer", action="store_true", help="Linux Unix-socket peer authentication with a private identity map")
    args = parser.parse_args()
    if not 1 <= args.runs <= 5 or not 15 <= args.timeout_seconds <= 300:
        parser.error("--runs must be 1..5 and --timeout-seconds 15..300")
    if args.peer and os.name == "nt":
        parser.error("--peer requires Unix")
    if os.name != "nt" and os.geteuid() == 0:
        parser.error("initdb must run as an unprivileged OS account")
    # libpq used by psycopg also reads the current process environment.
    # Discard inherited routes without inspecting or displaying their values.
    for key in list(os.environ):
        if key.upper().startswith(("PG", "POSTGRES_")):
            del os.environ[key]
    binaries = args.bin_dir.resolve()
    for name in ("initdb", "pg_ctl", "pg_dump", "pg_restore"):
        executable = binaries / (name + (".exe" if os.name == "nt" else ""))
        if not executable.is_file():
            parser.error(f"Missing existing executable: {name}")
    # Python/Windows subprocess resolves .exe for absolute executable paths.
    for run in range(1, args.runs + 1):
        probe = Probe(binaries, args.timeout_seconds, args.peer)
        try:
            report = probe.run()
        finally:
            probe.cleanup()
        report.update(run=run, cleanup="own cluster stopped and temporary directory removed")
        print(json.dumps(report, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
