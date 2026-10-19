"""Explicit production orchestration, separate from QA, migrations and imports.

The target is independently attested, not discovered and approved in one step.
A server boot identity plus exact endpoint/database/role OIDs needs no new grants.
"""

import ipaddress
import json
import os
import re
import stat
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import cast

from config import database_backup, pca_protected_artifacts
from django.conf import settings
from django.db import connection, transaction

from . import pca_identity_unification as merge

SCHEMA = "pca_production_plan_v1"
OUTCOME = {"canonical_box_id": 2135, "retired_box_id": 2141,
           "canonical_measurements": 94, "evidence_retained_duplicates": 18}
TARGET_KEYS = {"schema", "machine_id", "configured_host", "configured_port",
               "database", "role", "server_address", "server_port", "database_oid", "role_oid",
               "postmaster_started_at", "server_version_num"}
# All functions are public PostgreSQL metadata reads; no pg_control_system grant.
IDENTITY_SQL = """
SELECT current_database(), d.oid::bigint, session_user::text, current_user::text,
       r.oid::bigint, host(inet_server_addr()), inet_server_port(),
       to_char(pg_postmaster_start_time() AT TIME ZONE 'UTC',
               'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'), current_setting('server_version_num')::integer,
       current_setting('session_replication_role'), current_schemas(false),
       pg_is_in_recovery()
FROM pg_database d, pg_roles r
WHERE d.datname = current_database() AND r.rolname = current_user
"""


def machine_id():
    return Path("/etc/machine-id").read_text(encoding="ascii").strip()


def _is_disposable_qa():
    qa = (getattr(settings, "HISTORICAL_TEMPERATURE_PGQA", False)
          and settings.SETTINGS_MODULE == "config.historical_temperature_pgqa_settings")
    if qa:
        merge.require_local_database()
    return qa


def read_target(path):
    """Only the pinned root-authenticated source can authorize production."""
    if _is_disposable_qa():
        return read_artifact(path)
    if os.fspath(path) != pca_protected_artifacts.PINNED_ATTESTATION:
        raise ValueError("Production target must use the pinned root-owned attestation path.")
    return pca_protected_artifacts.read_attestation()


def read_artifact(path):
    """Production checks all ancestors; QA remains behind its disposable gate."""
    if not _is_disposable_qa():
        return pca_protected_artifacts.read_json(path)
    path = Path(path)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or (os.name == "posix" and info.st_mode & 0o022)
            or (hasattr(os, "getuid") and info.st_uid not in (0, os.getuid()))):
        raise ValueError("Evidence must be a regular, owned file, not group/world writable.")
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def write_artifact(path, value):
    if not _is_disposable_qa():
        return pca_protected_artifacts.write_json(path, value)
    descriptor = os.open(Path(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")


def require_target(target):
    """Preconnect refusal: no discovery, DNS lists, libpq redirection or QA bypass."""
    if type(target) is not dict or set(target) != TARGET_KEYS or target["schema"] != "pca_target_v2":
        raise ValueError("A complete authenticated pca_target_v2 attestation is required.")
    if (not all(isinstance(target[key], str) and target[key].strip() for key in
                ("machine_id", "configured_host", "configured_port", "database",
                 "role", "server_address", "postmaster_started_at"))
            or not re.fullmatch(r"[0-9a-f]{32}", target["machine_id"])
            or any(type(target[key]) is not int or target[key] <= 0 for key in
                   ("server_port", "database_oid", "role_oid", "server_version_num"))):
        raise ValueError("Incomplete or invalid target identity.")
    # The command runs locally on the production server, never through a tunnel.
    address = ipaddress.ip_address(target["server_address"])
    host = target["configured_host"]
    if (not address.is_loopback or host not in ("localhost", str(address))
            or not target["configured_port"].isdigit()
            or int(target["configured_port"]) != target["server_port"]
            or not 1 <= target["server_port"] <= 65535
            or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", target[key]) for key in ("database", "role"))
            or target["database"] in ("postgres", "template0", "template1")):
        raise ValueError("An exact local application database endpoint is required; no tunnels or remote hosts.")
    if any(key.upper().startswith("PG") for key in os.environ):
        raise ValueError("Remove inherited PG* libpq variables before production execution.")
    config = connection.settings_dict
    qa = _is_disposable_qa()
    if not qa and target != pca_protected_artifacts.read_attestation():
        raise ValueError("Target differs from the pinned root-authenticated attestation.")
    if not qa and (settings.SETTINGS_MODULE != "config.settings" or settings.DEBUG
          or getattr(settings, "HISTORICAL_TEMPERATURE_PGQA", False)
          or config.get("ENGINE") != "django.db.backends.postgresql"
          or config.get("OPTIONS") not in ({"sslmode": "prefer"}, {"sslmode": "require"},
                                          {"sslmode": "verify-full"}, {"sslmode": "disable"})):
        raise ValueError("Production requires config.settings, DEBUG off and unredirected PostgreSQL settings.")
    if (settings.DATABASE_ROUTERS or set(settings.DATABASES) != {"default"}
            or (config.get("HOST"), str(config.get("PORT")), config.get("NAME"), config.get("USER")) !=
            (host, target["configured_port"], target["database"], target["role"])
            or machine_id() != target["machine_id"]):
        raise ValueError("Configured database or local machine differs from the independent attestation.")
    return qa


def verify_target(target):
    qa = require_target(target)
    if qa:
        merge._verify_connection()
    connection.ensure_connection()
    info = connection.connection.info
    if (info.hostaddr, str(info.port), info.dbname, info.user) != (
            target["server_address"], target["configured_port"], target["database"], target["role"]):
        raise ValueError("Effective connection differs from the attested target.")
    with connection.cursor() as cursor:
        cursor.execute(IDENTITY_SQL)
        row = cursor.fetchone()
        expected = (target["database"], target["database_oid"], target["role"], target["role"],
                    target["role_oid"], target["server_address"], target["server_port"],
                    target["postmaster_started_at"], target["server_version_num"], "origin", ["public"], False)
        actual = tuple(row)
        if actual != expected:
            raise ValueError("Live database identity/session state differs from the independent attestation.")
        cursor.execute("""
            SELECT rolname FROM pg_roles
            WHERE pg_has_role(current_user, oid, 'MEMBER') AND
              (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls
               OR (left(rolname, 3) = 'pg_' AND rolname <> 'pg_database_owner'))
        """)
        if cursor.fetchall():
            raise ValueError("Unsafe PostgreSQL role privileges or privileged role membership.")


def envelope(target, plan):
    return {"schema": SCHEMA, "target": target, "plan": plan, "expected_outcome": OUTCOME}


def validate_plan(document, approved_hash):
    if (not isinstance(approved_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", approved_hash)
            or type(document) is not dict or set(document) != {"schema", "target", "plan", "expected_outcome"}
            or document["schema"] != SCHEMA or document["expected_outcome"] != OUTCOME
            or merge.fingerprint(document) != approved_hash):
        raise ValueError("The production plan artifact does not match the exact approved SHA-256 hash.")
    plan = document["plan"]
    if (not isinstance(plan, dict) or not plan.get("ready") or plan.get("blockers")
            or plan.get("operation") != merge.OPERATION or plan.get("organization_id") != 1
            or plan.get("counts") != {"duplicates": 18, "moved": 92, "untouched": 2}):
        raise ValueError("Only the conflict-free reviewed 94 canonical / 18 duplicate PCA plan can be applied.")
    return plan


def build_plan(organization_id, *, actor, target):
    require_target(target)
    actor_id = merge._require_scope(organization_id, actor)
    verify_target(target)
    return envelope(target, merge._build_read_only_plan(organization_id, actor_id))


def backup_plan(organization_id, *, actor, document, approved_hash, output_dir):
    plan = validate_plan(document, approved_hash)
    target = document["target"]
    require_target(target)
    actor_id = merge._require_scope(organization_id, actor)
    verify_target(target)
    if connection.in_atomic_block:
        raise ValueError("Backup must start outside an existing transaction.")
    directory_guard = (nullcontext() if _is_disposable_qa()
                       else pca_protected_artifacts.protected_directory(output_dir))
    with directory_guard, cast(AbstractContextManager[None], transaction.atomic(using="default")):
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            organization, _ = merge._authorize(organization_id, actor_id)
            if merge._make_plan(organization) != plan:
                raise ValueError("Stale plan: no backup evidence can be issued for a different live snapshot.")
            cursor.execute("SELECT pg_export_snapshot()")
            snapshot = cursor.fetchone()[0]
        database = {**connection.settings_dict, "ENGINE": "django.db.backends.postgresql"}
        evidence = database_backup.create_evidence(
            database, target, approved_hash, output_dir, snapshot=snapshot,
            hostaddr=target["server_address"],
        )
        if not _is_disposable_qa():
            pca_protected_artifacts.validate_file(evidence["archive"]["path"])
        return evidence


def _validate_backup(evidence, target, approved_hash):
    if _is_disposable_qa():
        return database_backup.validate_evidence(evidence, target, approved_hash)
    if (not isinstance(evidence, dict) or not isinstance(evidence.get("archive"), dict)
            or not isinstance(evidence["archive"].get("path"), str)):
        raise pca_protected_artifacts.ArtifactSecurityError("A protected backup archive path is required.")
    # Keep the original ancestor and file bindings across the external tools.
    with pca_protected_artifacts.protected_file(evidence["archive"]["path"]):
        return database_backup.validate_evidence(evidence, target, approved_hash)


def apply_plan(organization_id, *, actor, document, approved_hash, backup_evidence,
               review_ref, authorization):
    plan = validate_plan(document, approved_hash)
    target = document["target"]
    require_target(target)
    actor_id = merge._require_scope(organization_id, actor)
    if (authorization != f"APPLY_PCA_ORGANIZATION_1:{approved_hash}"
            or not isinstance(review_ref, str) or not review_ref.strip()):
        raise ValueError("Apply requires exact production authorization and an independent review reference.")
    if connection.in_atomic_block:
        raise ValueError("Production apply must start outside an existing transaction.")
    # Invalid/missing evidence must fail before any connection or ORM lookup.
    _validate_backup(backup_evidence, target, approved_hash)
    verify_target(target)

    def recheck():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '10s'")
            cursor.execute("SET LOCAL statement_timeout = '30s'")
        verify_target(target)
        _validate_backup(backup_evidence, target, approved_hash)

    return merge._apply_reviewed_plan(
        organization_id, actor_id=actor_id, expected_plan_hash=merge.fingerprint(plan),
        backup_ref=backup_evidence["archive"]["path"], review_ref=review_ref,
        execution_evidence={"target": target, "approved_plan_hash": approved_hash,
                            "backup": backup_evidence, "authorization": authorization},
        verify_execution=recheck,
    )
