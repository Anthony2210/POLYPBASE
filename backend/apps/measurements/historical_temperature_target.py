"""Dedicated, pinned historical import identity and cross-role snapshot backup."""

import os
import re
from contextlib import contextmanager

import psycopg
from psycopg import sql
from config import pca_protected_artifacts as artifacts
from django.conf import settings
from django.db import connection

from apps.cultures import pca_production as pca
from .historical_temperature_backup_timeouts import check_backup_timeouts
from .historical_temperature_update_guard import check_update_guards

PINNED_ATTESTATION = "/etc/polypbase/historical-temperature-production-target.json"
TARGET_KEYS = pca.TARGET_KEYS | {"backup_role", "backup_role_oid"}
_is_disposable_qa = pca._is_disposable_qa
socket_guard = pca.socket_guard
read_artifact = pca.read_artifact
write_artifact = pca.write_artifact
_validate_backup = pca._validate_backup


def read_target(path):
    """Production authorization never comes from a QA artifact or the PCA target."""
    if os.fspath(path) != PINNED_ATTESTATION:
        raise ValueError("Historical target must use its dedicated pinned root-owned path.")
    return artifacts.read_historical_temperature_attestation()


def require_target(target):
    """Refuse before connecting unless the dedicated native production target matches."""
    if (type(target) is not dict or set(target) != TARGET_KEYS
            or target["schema"] != "historical_temperature_target_v1"):
        raise ValueError("A complete historical_temperature_target_v1 attestation is required.")
    if (any(not isinstance(target[key], str) or not target[key].strip() for key in
            ("machine_id", "configured_host", "configured_port", "database", "role",
             "backup_role", "postmaster_started_at"))
            or not re.fullmatch(r"[0-9a-f]{32}", target["machine_id"])
            or any(type(target[key]) is not int or target[key] <= 0 for key in
                   ("database_oid", "role_oid", "backup_role_oid", "server_version_num",
                    "socket_uid", "socket_gid"))):
        raise ValueError("Incomplete or invalid historical target identity.")
    if (target["connection_mode"] != "unix"
            or target["configured_host"] not in ("/var/run/postgresql", "/run/postgresql")
            or target["server_address"] is not None or target["server_port"] is not None
            or not target["configured_port"].isascii() or not target["configured_port"].isdigit()
            or not 1 <= int(target["configured_port"]) <= 65535
            or not 180000 <= target["server_version_num"] < 190000
            or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", target[key]) for key in
                   ("database", "role", "backup_role"))
            or target["database"] in ("postgres", "template0", "template1")
            or target["role"] == target["backup_role"]
            or target["role_oid"] == target["backup_role_oid"]):
        raise ValueError("Distinct import/backup roles and an exact PostgreSQL 18 Unix endpoint are required.")
    if any(key.upper().startswith("PG") for key in os.environ):
        raise ValueError("Remove inherited PG* libpq variables before production execution.")
    if target != artifacts.read_historical_temperature_attestation():
        raise ValueError("Target differs from the dedicated pinned root-authenticated attestation.")
    config = connection.settings_dict
    if (settings.SETTINGS_MODULE != "config.settings" or settings.DEBUG
            or getattr(settings, "HISTORICAL_TEMPERATURE_PGQA", False)
            or settings.DATABASE_ROUTERS or set(settings.DATABASES) != {"default"}
            or config.get("ENGINE") != "django.db.backends.postgresql" or config.get("PASSWORD")
            or config.get("OPTIONS") not in ({"sslmode": "prefer"}, {"sslmode": "require"},
                                            {"sslmode": "verify-full"}, {"sslmode": "disable"})
            or (config.get("HOST"), str(config.get("PORT")), config.get("NAME"), config.get("USER")) !=
               (target["configured_host"], target["configured_port"], target["database"], target["role"])
            or pca.machine_id() != target["machine_id"]):
        raise ValueError("Production settings, import role or machine differ from the independent attestation.")
    with socket_guard(target):
        pass
    return False


def _verify_live_target(target, database, *, backup=False):
    """Read the same session/server identity for either direct or Django connections."""
    role = target["backup_role" if backup else "role"]
    role_oid = target["backup_role_oid" if backup else "role_oid"]
    if database.pgconn.used_password:
        raise ValueError("Historical execution requires independently verified peer authentication, not a password.")
    info = database.info
    if ((info.host, info.hostaddr, str(info.port), info.dbname, info.user) !=
            (target["configured_host"], "", target["configured_port"], target["database"], role)):
        raise ValueError("Effective connection differs from the attested target.")
    with database.cursor() as cursor:
        cursor.execute(pca.IDENTITY_SQL)
        expected = (target["database"], target["database_oid"], role, role, role_oid, None, None,
                    target["postmaster_started_at"], target["server_version_num"], "origin", ["public"],
                    False, int(target["configured_port"]))
        if tuple(cursor.fetchone()) != expected:
            raise ValueError("Live database identity/session state differs from the independent attestation.")
        cursor.execute("""
            SELECT rolname FROM pg_roles
            WHERE pg_has_role(current_user, oid, 'MEMBER') AND
              (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls
               OR (left(rolname, 3) = 'pg_' AND rolname <> 'pg_database_owner'))
        """)
        if cursor.fetchall():
            raise ValueError("Unsafe PostgreSQL role privileges or privileged role membership.")
        if not backup:
            cursor.execute("""
                SELECT 1 FROM pg_database
                WHERE datname = current_database() AND pg_has_role(current_user, datdba, 'MEMBER')
                UNION ALL
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND pg_has_role(current_user, c.relowner, 'MEMBER')
                LIMIT 1
            """)
            if cursor.fetchall():
                raise ValueError("The import role must not own the database or public relations.")
    if not backup:
        check_update_guards(database, target["role"], target["backup_role_oid"])


def verify_target(target, *, in_snapshot=False):
    require_target(target)
    with socket_guard(target):
        connection.ensure_connection()
        _verify_live_target(target, connection.connection)


@contextmanager
def backup_database(target, snapshot):
    """Hold a peer-authenticated backup role and validate the caller's exported snapshot."""
    require_target(target)
    if not isinstance(snapshot, str) or not re.fullmatch(r"[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+", snapshot):
        raise ValueError("A PostgreSQL exported snapshot identifier is required.")
    config = {"ENGINE": "django.db.backends.postgresql", "HOST": target["configured_host"],
              "PORT": target["configured_port"], "NAME": target["database"],
              "USER": target["backup_role"], "PASSWORD": "",
                            "OPTIONS": {"sslmode": "disable", "passfile": "/dev/null"}}
    with socket_guard(target):
        database = psycopg.connect(host=config["HOST"], port=config["PORT"], dbname=config["NAME"],
                                   user=config["USER"], password="", passfile="/dev/null", sslmode="disable",
                                   connect_timeout=5, autocommit=True)
        try:
            _verify_live_target(target, database, backup=True)
            check_backup_timeouts(database)
            with database.cursor() as cursor:
                cursor.execute("SET default_transaction_read_only = on")
                cursor.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
                # Import must precede every snapshot-taking read in this transaction.
                cursor.execute(sql.SQL("SET TRANSACTION SNAPSHOT {}").format(sql.Literal(snapshot)))
            _verify_live_target(target, database, backup=True)
            yield config
            database.rollback()
            _verify_live_target(target, database, backup=True)
            require_target(target)
        finally:
            database.close()
