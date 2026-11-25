"""In-process authority for the hosted PostgreSQL service regression runner."""

import os
from pathlib import Path
import secrets
import sys


ROOT = Path(__file__).resolve().parents[2]
SERVICE_ENV = {
    "GITHUB_ACTIONS": "true",
    "RUNNER_ENVIRONMENT": "github-hosted",
    "RUNNER_OS": "Linux",
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_PORT": "5432",
    "POSTGRES_USER": "postgres",
    "POSTGRES_PASSWORD": "ci-only",
    "POSTGRES_DB": "postgres",
}
ADMIN = "postgres"
MAINTENANCE = "postgres"
OPTIONS = {"sslmode": "disable", "connect_timeout": 5, "passfile": os.devnull,
           "options": "-c lock_timeout=10000 -c statement_timeout=120000"}
_active = None


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def check_environment():
    require(sys.platform == "linux", "Backend PostgreSQL CI requires hosted Linux.")
    for name, value in SERVICE_ENV.items():
        require(os.environ.get(name) == value, f"Backend PostgreSQL CI requires the workflow value for {name}.")
    for path in (ROOT / ".env", ROOT / "backend" / ".env"):
        require(not path.exists() and not path.is_symlink(), "Backend PostgreSQL CI refuses dotenv files.")
    require(not any(key.upper().startswith(("PG", "DB_")) or key.upper() == "DATABASE_URL"
                    or (key.upper().startswith("POSTGRES_") and key not in SERVICE_ENV)
                    for key in os.environ), "Backend PostgreSQL CI refuses extra database configuration.")


def active():
    return _active


def require_service():
    require(_active is not None, "Use scripts/run_backend_postgres_ci.py with its fresh service.")
    check_environment()
    return _active


class Service:
    def __init__(self):
        check_environment()
        self.prefix = "test_ppci_" + secrets.token_hex(8) + "_"
        self.databases = {}
        self.roles = {}
        self.identity = None
        self.maintenance = None
        self.test_template = None
        self.test_database = None

    def _raw(self, database, user, role=None):
        import psycopg

        options = dict(OPTIONS)
        if role is not None:
            require(role == "box1902_owner" and user == "box1902_migrator", "Unexpected startup role.")
            options["options"] += " -c role=box1902_owner"
        return psycopg.connect(host="127.0.0.1", port=5432, dbname=database, user=user,
                               password=self.password(user), autocommit=True, **options)

    def password(self, user):
        return SERVICE_ENV["POSTGRES_PASSWORD"] if user == ADMIN else self.roles[user][1]

    @staticmethod
    def server_key(conn):
        row = conn.execute("SELECT current_setting('server_version_num')::int, "
                           "(pg_catalog.pg_control_system()).system_identifier::text, "
                           "pg_catalog.pg_postmaster_start_time(), current_database(), "
                           "session_user, current_user, inet_server_port()").fetchone()
        require(180000 <= row[0] < 190000 and row[3:] == (MAINTENANCE, ADMIN, ADMIN, 5432),
                "Unexpected PostgreSQL 18 service identity.")
        return row[:3]

    def __enter__(self):
        global _active
        require(_active is None, "PostgreSQL service already active.")
        conn = self._raw(MAINTENANCE, ADMIN)
        try:
            self.identity = self.server_key(conn)
            require(conn.execute("SELECT pg_try_advisory_lock(185603127)").fetchone() == (True,),
                    "Another backend CI runner is using this service.")
            require(conn.execute("SELECT datname FROM pg_database ORDER BY datname").fetchall()
                    == [("postgres",), ("template0",), ("template1",)], "Expected fresh service databases.")
            require(conn.execute("SELECT rolname FROM pg_roles WHERE rolname !~ '^pg_' ORDER BY rolname").fetchall()
                    == [(ADMIN,)], "Expected fresh service roles.")
            require(conn.execute("SELECT nspname FROM pg_namespace WHERE nspname !~ '^pg_' "
                                 "AND nspname <> 'information_schema' ORDER BY nspname").fetchall()
                    == [("public",)], "Unexpected service schemas.")
            require(conn.execute("SELECT extname FROM pg_extension ORDER BY extname").fetchall()
                    == [("plpgsql",)], "Unexpected service extensions.")
            for catalog, column in (("pg_class", "relnamespace"), ("pg_proc", "pronamespace"),
                                    ("pg_type", "typnamespace")):
                require(conn.execute(f"SELECT 1 FROM pg_catalog.{catalog} WHERE {column} = "
                                     "'public'::regnamespace LIMIT 1").fetchone() is None,
                        "Expected an empty service database.")
        except BaseException:
            conn.close()
            raise
        self.maintenance = conn
        _active = self
        return self

    def __exit__(self, *exc):
        global _active
        try:
            for name in list(self.databases):
                self.drop_database(name)
            self.drop_roles()
        finally:
            _active = None
            self.maintenance.close()

    def verify(self, conn, database, user, role=None):
        # The live maintenance socket pins the postmaster; keep all writes on
        # the attested connection. Restricted roles cannot read pg_control_system.
        check_environment()
        require(self.server_key(self.maintenance) == self.identity, "Service postmaster changed.")
        oid = self.databases.get(database)
        require(oid is not None, "Database was not created by this invocation.")
        query = """SELECT oid, current_database(), session_user, current_user,
                          pg_postmaster_start_time(), inet_server_port()
                   FROM pg_catalog.pg_database WHERE datname=current_database()"""
        require(conn.execute(query).fetchone() == (oid, database, user, role or user, self.identity[2], 5432),
                "Unexpected disposable database OID or login.")

    def verify_test_database(self, suffix=None):
        from django.db import connections

        conn = connections["default"]
        name = self.test_database
        require(self.test_template is not None and name == conn.settings_dict["NAME"]
                and (suffix is None or name == self.prefix + suffix), "Unexpected service test database.")
        conn.ensure_connection()
        self.verify(conn.connection, name, ADMIN)

    def connect(self, database, user=ADMIN, *, role=None):
        check_environment()
        require(database in self.databases, "Refusing to connect to an unowned database.")
        conn = self._raw(database, user, role)
        try:
            self.verify(conn, database, user, role)
        except BaseException:
            conn.close()
            raise
        return conn

    def _catalog_oid(self, catalog, name):
        query = {"database": "SELECT oid FROM pg_database WHERE datname=%s",
                 "role": "SELECT oid FROM pg_roles WHERE rolname=%s"}[catalog]
        row = self.maintenance.execute(query, [name]).fetchone()
        return row[0] if row else None

    def create_database(self, name, *, owner=ADMIN, template=None):
        from psycopg import sql

        require(name.startswith(self.prefix) and name not in self.databases, "Invalid disposable database name.")
        self._verify_maintenance()
        if template is not None:
            require(template in self.databases and self._catalog_oid("database", template) == self.databases[template],
                    "Disposable template identity changed.")
        # No retry or automatic DROP on a collision.
        self.maintenance.execute(sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE {}").format(
            sql.Identifier(name), sql.Identifier(owner), sql.Identifier(template or "template0")))
        self.databases[name] = self._catalog_oid("database", name)
        require(self.databases[name] is not None, "Could not record created database OID.")

    def rename_database(self, name, target):
        from psycopg import sql

        self._verify_maintenance()
        require(target.startswith(self.prefix) and target not in self.databases, "Invalid disposable database target.")
        self._verify_owned("database", name, self.databases[name])
        self.maintenance.execute(sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
            sql.Identifier(name), sql.Identifier(target)))
        self.databases[target] = self.databases.pop(name)

    def _verify_maintenance(self):
        check_environment()
        require(self.server_key(self.maintenance) == self.identity, "Service postmaster changed.")

    def _verify_owned(self, catalog, name, oid):
        require(oid is not None and self._catalog_oid(catalog, name) == oid,
                f"Disposable {catalog} identity changed; refusing mutation.")

    def drop_database(self, name):
        from psycopg import sql

        self._verify_maintenance()
        self._verify_owned("database", name, self.databases[name])
        self.maintenance.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        del self.databases[name]

    def execute_maintenance(self, statement, params=None):
        self._verify_maintenance()
        cursor = self.maintenance.execute(statement, params)
        return cursor.fetchall() if cursor.description else []

    def create_runtime_role(self):
        self._verify_maintenance()
        self.maintenance.execute("CREATE ROLE polypbase LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                                 "NOINHERIT NOREPLICATION NOBYPASSRLS")
        self._record_roles(["polypbase"])

    def provision_box_roles(self, database):
        from apps.measurements import box_1902_sql as contract

        with self.connect(database) as conn:
            conn.execute(contract.provision_sql())
        self._record_roles(sorted(set(contract.ROLES) - {contract.RUNTIME}), nologin=contract.OWNER)

    def _record_roles(self, roles, *, nologin=None):
        from psycopg import sql

        self._verify_maintenance()
        for role in roles:
            oid = self._catalog_oid("role", role)
            require(oid is not None and role not in self.roles, "Unexpected newly provisioned role.")
            self.roles[role] = oid, secrets.token_urlsafe(32) if role != nologin else None
        for role in roles:
            if role != nologin:
                self.maintenance.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(self.password(role))))

    def drop_roles(self):
        from psycopg import sql

        self._verify_maintenance()
        for name, (oid, _) in self.roles.items():
            self._verify_owned("role", name, oid)
        if self.roles:
            self.maintenance.execute(sql.SQL("DROP ROLE {}").format(
                sql.SQL(", ").join(map(sql.Identifier, self.roles))))
            self.roles.clear()

    def django_config(self, database, user=ADMIN, *, role=None):
        require(database in self.databases, "Refusing unowned Django database.")
        options = dict(OPTIONS)
        if role is not None:
            require(role == "box1902_owner" and user == "box1902_migrator", "Unexpected Django startup role.")
            options["options"] += " -c role=box1902_owner"
        return {"ENGINE": "config.backend_postgres_ci_backend", "NAME": database,
                "HOST": "127.0.0.1", "PORT": "5432", "USER": user, "PASSWORD": self.password(user),
                "OPTIONS": options, "CONN_MAX_AGE": 0}

    def use_django_database(self, database, user=ADMIN, *, role=None):
        from django.db import connections

        conn = connections["default"]
        conn.close()
        conn.settings_dict.update(self.django_config(database, user, role=role))
