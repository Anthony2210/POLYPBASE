"""Execution boundary for reviewed CLA/SME reconciliation.

Production apply requires an operator-arranged maintenance window, an independently
restored fresh backup, and independent root authorization. This module creates no
backup, credentials, attestation or restore. Callers must include the yielded
binding in their plan fingerprint and keep all business SQL inside the session.
"""

import datetime as dt
import hashlib
import json
import os
import re
import stat
import subprocess
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from importlib import import_module
from pathlib import Path
from typing import cast

from apps.cultures.pca_production import IDENTITY_SQL, TARGET_KEYS
from config import database_backup, pca_protected_artifacts as artifacts
from django.conf import settings
from django.db import connection, transaction

TARGET_PATH = "/etc/polypbase/cla-sme-production-target.json"
AUTHORIZATION_PATH = "/etc/polypbase/cla-sme-production-authorization.json"
TARGET_SCHEMA = "cla_sme_target_v1"
AUTHORIZATION_SCHEMA = "cla_sme_authorization_v1"
OPERATION = "cla_sme_identity_20261027_v2"
PREPARATION_OPERATION = "cla_sme_references_20261027_v1"
AUTHORIZATION_KEYS = {
    "schema", "operation", "organization_id", "family", "target_strain_id",
    "actor_id", "plan_hash", "target_hash", "authorized_at", "expires_at", "backup",
}
DISPOSABLE_OPTIONS = {"options": "-c lock_timeout=10000 -c statement_timeout=120000"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def _root_document(path):
    artifacts.assert_unprivileged_runtime()
    return artifacts._read_json(path, {0}, False)


def _port(value):
    return (isinstance(value, str) and value.isascii() and value.isdigit()
            and 1 <= int(value) <= 65535 and str(int(value)) == value)


def _target():
    target = _root_document(TARGET_PATH)
    if (type(target) is not dict or set(target) != TARGET_KEYS
            or target["schema"] != TARGET_SCHEMA):
        raise ValueError("A complete root-authenticated CLA/SME target is required.")
    if (not isinstance(target["machine_id"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", target["machine_id"])
            or target["machine_id"] != Path("/etc/machine-id").read_text(encoding="ascii").strip()
            or target["connection_mode"] != "unix"
            or target["configured_host"] not in ("/run/postgresql", "/var/run/postgresql")
            or not _port(target["configured_port"])
            or target["server_address"] is not None or target["server_port"] is not None
            or any(type(target[k]) is not int or target[k] <= 0 for k in
                   ("database_oid", "role_oid", "socket_uid", "socket_gid", "server_version_num"))
            or not 180000 <= target["server_version_num"] < 190000
            or not isinstance(target["postmaster_started_at"], str)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", target["postmaster_started_at"])
            or any(not isinstance(target[k], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", target[k])
                   for k in ("database", "role"))
            or target["database"] in ("postgres", "template0", "template1")):
        raise ValueError("An exact local PostgreSQL 18 peer target is required.")
    config = connection.settings_dict
    if (config.get("HOST"), str(config.get("PORT")), config.get("NAME"), config.get("USER")) != (
            target["configured_host"], target["configured_port"], target["database"], target["role"]):
        raise ValueError("Configured database differs from the root target.")
    import pwd
    if pwd.getpwuid(os.getuid()).pw_name != target["role"]:
        raise ValueError("The OS account must match the attested peer role.")
    return target


def socket_guard(target):
    return artifacts.protected_postgresql_socket(
        target["configured_host"], target["configured_port"], target["socket_uid"], target["socket_gid"])


def _disposable_identity():
    """Trust the launcher's owned fresh directory, never the answering server."""
    raw = os.environ.get("POLYPBASE_HISTORICAL_TEMPERATURE_DISPOSABLE_CLUSTER", "")
    binaries = os.environ.get("POLYPBASE_PCA_QA_PG_BIN", "")
    if not raw or not binaries or os.environ.get("PYTHON_DOTENV_DISABLED") != "1":
        raise ValueError("The fresh-cluster launcher is required.")
    data = Path(raw)
    if (not data.is_absolute() or data.resolve() != data or data.name != "data"
            or not data.parent.name.startswith("polypbase-ht-production-synthetic-")):
        raise ValueError("Not a launcher-created disposable directory.")
    for path in (data.parent, data):
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or (hasattr(os, "getuid") and info.st_uid != os.getuid())
                or (os.name == "posix" and info.st_mode & 0o022)
                or not 0 <= dt.datetime.now().timestamp() - info.st_mtime <= 3600):
            raise ValueError("Disposable directory is not fresh and privately owned.")
    module = import_module("ht_disposable_settings")
    if Path(module.__file__ or "").resolve() != data.parent / "ht_disposable_settings.py":
        raise ValueError("Settings must originate in the fresh launcher directory.")
    binary = Path(binaries) / ("pg_controldata.exe" if os.name == "nt" else "pg_controldata")
    if not binary.is_absolute() or not binary.is_file():
        raise ValueError("An explicit pg_controldata binary is required.")
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith("PG")}
    environment.update(LC_ALL="C", LANG="C")
    try:
        output = subprocess.run([str(binary), str(data)], check=True, capture_output=True,
                                text=True, env=environment, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        raise ValueError("Offline disposable cluster identification failed.") from None
    matches = re.findall(r"^Database system identifier:\s*(\d+)\s*$", output, re.MULTILINE)
    if len(matches) != 1 or (data / "PG_VERSION").read_text(encoding="ascii").strip() != "18":
        raise ValueError("Expected one offline PostgreSQL 18 cluster identity.")
    return {"system_identifier": matches[0], "data_directory": str(data),
            "port": str(connection.settings_dict["PORT"])}


def require_database():
    """Validate configuration without opening a Django database connection."""
    config = connection.settings_dict
    if (settings.DATABASE_ROUTERS or set(settings.DATABASES) != {"default"}
                or config.get("AUTOCOMMIT", True) is not True):
        raise ValueError("Only the default database without routers is supported.")
    module = settings.SETTINGS_MODULE
    if (module == "config.test_settings" and config.get("ENGINE") == "django.db.backends.sqlite3"
            and (config.get("NAME") == ":memory:"
                 or str(config.get("NAME", "")).startswith("file:memorydb_default?mode=memory&"))):
        return "sqlite"
    if config.get("ENGINE") != "django.db.backends.postgresql":
        raise ValueError("Unsupported database execution configuration.")
    if any(key.upper().startswith("PG") for key in os.environ):
        raise ValueError("Remove inherited PG* libpq variables.")
    if module == "ht_disposable_settings" and getattr(settings, "RUNNING_TESTS", False):
        if ((config.get("HOST"), config.get("NAME"), config.get("USER"), config.get("PASSWORD"))
                != ("127.0.0.1", "test_subculture_qa", "subculture_qa", "")
                or not _port(str(config.get("PORT"))) or config.get("OPTIONS") != DISPOSABLE_OPTIONS):
            raise ValueError("Disposable execution requires the exact fresh-cluster test configuration.")
        _disposable_identity()
        return "disposable"
    if (module != "config.settings" or settings.DEBUG or config.get("PASSWORD")
            or config.get("OPTIONS") not in ({"sslmode": "prefer"}, {"sslmode": "disable"},
                                              {"sslmode": "require"}, {"sslmode": "verify-full"})):
        raise ValueError("Production requires existing config.settings, DEBUG off and peer authentication.")
    target = _target()
    with socket_guard(target):
        pass
    return "production"


def _utc(value):
    try:
        parsed = dt.datetime.fromisoformat(value)
        if parsed.utcoffset() != dt.timedelta(0):
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        raise ValueError("Authorization timestamps must be aware UTC instants.") from None


def _authorization(target, scope, *, operation=OPERATION):
    _require_operation(operation)
    document = _root_document(AUTHORIZATION_PATH)
    if (type(document) is not dict or set(document) != AUTHORIZATION_KEYS
            or document["schema"] != AUTHORIZATION_SCHEMA or document["operation"] != operation
            or any(document[k] != v or type(document[k]) is not type(v) for k, v in scope.items())
            or document["target_hash"] != fingerprint(target)):
        raise ValueError("Root authorization does not match this exact plan, target and scope.")
    now = dt.datetime.now(dt.UTC)
    authorized, expires = _utc(document["authorized_at"]), _utc(document["expires_at"])
    hour = dt.timedelta(hours=1)
    if not (dt.timedelta(0) <= now - authorized <= hour and authorized <= now < expires <= authorized + hour):
        raise ValueError("Root authorization is future-dated, expired or exceeds one hour.")
    backup = document["backup"]
    if (type(backup) is not dict or set(backup) != {"archive", "created_at", "restoration_tested"}
            or backup["restoration_tested"] is not True
            or not dt.timedelta(0) <= now - _utc(backup["created_at"]) <= hour):
        raise ValueError("A fresh independently restored backup is required.")
    archive = backup["archive"]
    if (type(archive) is not dict or set(archive) != {"path", "sha256", "size_bytes"}
            or not isinstance(archive["path"], str)
            or not isinstance(archive["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", archive["sha256"])
            or type(archive["size_bytes"]) is not int or archive["size_bytes"] <= 0):
        raise ValueError("Complete archive metadata is required.")
    with artifacts.protected_file(archive["path"]):
        database_backup.verify_archive(archive, client_major=18)
    # External archive verification can consume most of the approval lifetime.
    now = dt.datetime.now(dt.UTC)
    if (not authorized <= now < expires or now - authorized > hour
            or not dt.timedelta(0) <= now - _utc(backup["created_at"]) <= hour):
        raise ValueError("Authorization or backup expired during archive verification.")
    if (_root_document(AUTHORIZATION_PATH) != document
            or _root_document(TARGET_PATH) != target):
        raise ValueError("Root execution documents changed during archive verification.")
    return document


def _verify_production(target):
    info = connection.connection.info
    if ((info.host, str(info.port), info.dbname, info.user) !=
            (target["configured_host"], target["configured_port"], target["database"], target["role"])
            or info.hostaddr not in (None, "")):
        raise ValueError("Effective connection differs from the attested peer socket.")
    with connection.cursor() as cursor:
        cursor.execute(IDENTITY_SQL)
        expected = (target["database"], target["database_oid"], target["role"], target["role"],
                    target["role_oid"], None, None, target["postmaster_started_at"],
                    target["server_version_num"], "origin", ["public"], False, int(target["configured_port"]))
        if tuple(cursor.fetchone()) != expected:
            raise ValueError("Live PostgreSQL identity or session differs from the root target.")
        cursor.execute("""SELECT rolname FROM pg_roles WHERE pg_has_role(current_user, oid, 'MEMBER')
            AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls
                 OR (left(rolname, 3) = 'pg_' AND rolname <> 'pg_database_owner'))""")
        if cursor.fetchall():
            raise ValueError("Privileged PostgreSQL execution roles are forbidden.")


def _verify_disposable(identity):
    info = connection.connection.info
    effective = {"host": (info.host, "127.0.0.1"), "port": (str(info.port), identity["port"]),
                 "database": (info.dbname, "test_subculture_qa"), "user": (info.user, "subculture_qa"),
                 "hostaddr": (info.hostaddr, "127.0.0.1")}
    differing = [name for name, (actual, expected) in effective.items() if actual != expected]
    if differing:
        raise ValueError("Effective disposable connection differs from the launcher: " + ", ".join(differing) + ".")
    with connection.cursor() as cursor:
        cursor.execute("""SELECT system_identifier::text, current_setting('data_directory'),
            current_setting('port'), current_setting('server_version_num')::integer,
            current_user::text, session_user::text, current_schemas(false),
            current_setting('session_replication_role'), pg_is_in_recovery() FROM pg_control_system()""")
        row = cursor.fetchone()
    if row is None or len(row) != 9:
        raise ValueError("Live disposable cluster differs from its offline identity: metadata_shape.")
    # PostgreSQL reports forward slashes on Windows; compare filesystem identities,
    # not spelling. Only paths are normalized, never other attested fields.
    actual_path = os.path.normcase(str(Path(row[1]).resolve()))
    expected_path = os.path.normcase(str(Path(identity["data_directory"]).resolve()))
    checks = {"system_identifier": row[0] == identity["system_identifier"],
              "data_directory": actual_path == expected_path,
              "port": row[2] == identity["port"],
              "server_version_num": type(row[3]) is int and 180000 <= row[3] < 190000,
              "current_user": row[4] == "subculture_qa", "session_user": row[5] == "subculture_qa",
              "schemas": row[6] == ["public"], "replication_role": row[7] == "origin",
              "in_recovery": row[8] is False}
    differing = [name for name, matches in checks.items() if not matches]
    if differing:
        raise ValueError("Live disposable cluster differs from its offline identity: " + ", ".join(differing) + ".")


def _read_only(execute, sql, params, many, context):
    # Deliberately restrictive SQLite test guard, not an emulation of PostgreSQL RO.
    # Reject WITH/PRAGMA/EXPLAIN too: each can conceal writes on SQLite.
    if not re.match(r"^\s*(SELECT\b|SAVEPOINT\b|RELEASE\b|ROLLBACK\s+TO\b)", sql, re.IGNORECASE) or ";" in sql.rstrip().rstrip(";"):
        raise ValueError("Planning sessions forbid SQL writes.")
    return execute(sql, params, many, context)


def _require_operation(operation):
    if type(operation) is not str or operation not in (OPERATION, PREPARATION_OPERATION):
        raise ValueError("Only the exact CLA/SME identity or reference preparation operation is supported.")


@contextmanager
def session(*, apply=False, organization_id=None, family=None, target_strain_id=None,
            actor_id=None, expected_plan_hash=None, operation=OPERATION):
    """Yield only {'mode', 'target_hash'}; revalidate before the owned commit.

    PostgreSQL callers must enter in autocommit, never inside an atomic block.
    SQLite TestCase nesting is allowed. Plan hashes must bind this dictionary,
    the operation and explicit source references. Only reference preparation may
    authorize a null target strain. Callers must enforce administrator permission;
    the production boundary requires a positive actor ID for either operation.
    """
    _require_operation(operation)
    mode = require_database()
    if mode == "sqlite":
        binding = {"mode": mode, "target_hash": fingerprint({"database": "isolated_test_sqlite_v1"})}
        with cast(AbstractContextManager, transaction.atomic(using="default")), (nullcontext() if apply else connection.execute_wrapper(_read_only)):
            yield binding
        return
    if connection.in_atomic_block or (connection.connection is not None and not connection.autocommit):
        raise ValueError("PostgreSQL execution requires an owned outer transaction in autocommit.")
    target = _target() if mode == "production" else _disposable_identity()
    scope = {"organization_id": organization_id, "family": family, "target_strain_id": target_strain_id,
             "actor_id": actor_id, "plan_hash": expected_plan_hash}
    authorization = None
    if mode == "production" and apply:
        valid_target = ((type(target_strain_id) is int and target_strain_id > 0)
                        or (operation == PREPARATION_OPERATION and target_strain_id is None))
        if (any(type(value) is not int or value <= 0 for value in (organization_id, actor_id))
                or not valid_target
                or not isinstance(family, str) or not family.strip()
                or not isinstance(expected_plan_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_plan_hash)):
            raise ValueError("Apply requires explicit scope and an approved plan SHA256.")
        authorization = _authorization(target, scope, operation=operation)
    guard = socket_guard(target) if mode == "production" else nullcontext()
    with ExitStack() as guards:
        # Acquire before atomic can open a connection, but close before commit.
        guards.enter_context(guard)
        with cast(AbstractContextManager, transaction.atomic(using="default")), guards:
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE" if apply else
                               "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cursor.execute("SET LOCAL lock_timeout = '10s'")
                cursor.execute("SET LOCAL statement_timeout = '30s'")
                cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '60s'")
                cursor.execute("SET LOCAL transaction_timeout = '900s'")
            verify = _verify_production if mode == "production" else _verify_disposable
            verify(target)
            yield {"mode": mode, "target_hash": fingerprint(target)}
            if require_database() != mode:
                raise ValueError("Execution mode changed during the transaction.")
            if mode == "production":
                if _target() != target:
                    raise ValueError("Root target changed during execution.")
                verify(target)
                if apply and _authorization(target, scope, operation=operation) != authorization:
                    raise ValueError("Root authorization changed during execution.")
            else:
                if _disposable_identity() != target:
                    raise ValueError("Offline disposable identity changed during execution.")
                verify(target)
