"""Connection-free archive helpers; callers own target and snapshot provenance.

Evidence hashes detect changes, not authenticity. Readability is not a restore test.
No Django settings, database connections, or manifest writes occur here.
"""

import datetime
import hashlib
import ipaddress
import json
import os
import re
import stat
import subprocess
from pathlib import Path

TIMEOUT_SECONDS = 600
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_EVIDENCE_KEYS = {
    "schema", "target", "plan_hash", "created_at", "archive", "verification",
    "restoration_tested", "evidence_hash",
}


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _require_hash(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError("Expected a lowercase SHA256 digest.")


def _environment(database=None, *, hostaddr=None):
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith("PG")
    }
    if database is not None:
        environment["PGPASSWORD"] = str(database.get("PASSWORD", ""))
        environment["PGSSLMODE"] = str(
            database.get("OPTIONS", {}).get("sslmode", "prefer")
        )
    if hostaddr is not None:
        environment["PGHOSTADDR"] = hostaddr
    return environment


def _run(command, environment):
    try:
        subprocess.run(
            command, check=True, env=environment, timeout=TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        # Do not expose connection arguments or PostgreSQL error output.
        raise ValueError("PostgreSQL archive command failed or timed out.") from None


def verify_archive(archive):
    """Rehash and check readability, optionally matching recorded size and hash."""
    if not isinstance(archive, dict) or not isinstance(archive.get("path"), str):
        raise ValueError("Expected an archive dictionary with an absolute path.")
    path = Path(archive["path"])
    if not path.is_absolute():
        raise ValueError("Archive path must be absolute.")
    if "sha256" in archive:
        _require_hash(archive["sha256"])
    if "size_bytes" in archive and (
        type(archive["size_bytes"]) is not int or archive["size_bytes"] <= 0
    ):
        raise ValueError("Archive size must be a positive integer.")
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
            raise ValueError("Archive must be a nonempty regular, nonsymlink file.")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError("Archive changed during verification.")
            digest = hashlib.sha256()
            size = 0
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        result = {"path": str(path), "sha256": digest.hexdigest(), "size_bytes": size}
        if size != before.st_size:
            raise ValueError("Archive changed during verification.")
        for key in ("sha256", "size_bytes"):
            if key in archive and archive[key] != result[key]:
                raise ValueError("Archive checksum or size mismatch.")
        _run(["/usr/bin/pg_restore", "--list", str(path)], _environment())
        after = path.lstat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise ValueError("Archive changed during verification.")
        return result
    except OSError:
        raise ValueError("Archive is missing or inaccessible.") from None


def create_archive(database, output_dir, label, *, snapshot=None, hostaddr=None):
    """Create an exclusively owned, private custom-format PostgreSQL archive."""
    if not isinstance(label, str) or not _LABEL.fullmatch(label):
        raise ValueError("Unsafe backup label.")
    if database.get("ENGINE") != "django.db.backends.postgresql":
        raise ValueError("The configured database is not PostgreSQL.")
    if snapshot is not None and (not isinstance(snapshot, str) or not snapshot.strip()):
        raise ValueError("Snapshot must be a nonempty string.")
    if hostaddr is not None:
        try:
            if (not isinstance(hostaddr, str) or "%" in hostaddr
                    or str(ipaddress.ip_address(hostaddr)) != hostaddr):
                raise ValueError
        except ValueError:
            raise ValueError("Host address must be a canonical numeric IP address.") from None
    for key in ("HOST", "PORT", "USER", "NAME"):
        if database.get(key) is None or not str(database.get(key, "")).strip():
            raise ValueError("Explicit database connection parameters are required.")
    name = str(database["NAME"])
    # libpq otherwise interprets the name as a connection string overriding flags.
    if "=" in name or name.startswith(("postgresql://", "postgres://")):
        raise ValueError("Database name must not be a connection string.")
    directory = Path(output_dir).resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{label}-{timestamp}.dump"
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        command = [
            "/usr/bin/pg_dump", "--format=custom", "--no-owner", "--no-acl",
            "--no-password", "--file", str(path), "--host", str(database["HOST"]),
            "--port", str(database["PORT"]), "--username", str(database["USER"]),
            "--dbname", name,
        ]
        if snapshot is not None:
            command.extend(["--snapshot", snapshot])
        # Pin the address without changing the hostname used for TLS verification.
        _run(command, _environment(database, hostaddr=hostaddr))
        return verify_archive({"path": str(path)})
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _evidence_hash(evidence):
    return hashlib.sha256(_canonical({
        key: value for key, value in evidence.items() if key != "evidence_hash"
    })).hexdigest()


def create_evidence(database, target, plan_hash, output_dir, *, snapshot, hostaddr=None):
    """Bind an archive to a caller-verified target and exported snapshot.

    The caller must keep its REPEATABLE READ READ ONLY exporting transaction open
    and supply the same database/cluster. This helper grants no new privileges.
    """
    if type(target) is not dict:
        raise ValueError("Target must be a dictionary.")
    _require_hash(plan_hash)
    if not isinstance(snapshot, str) or not snapshot.strip():
        raise ValueError("Evidence requires an exported snapshot.")
    target_copy = json.loads(_canonical(target))
    archive = create_archive(
        database, output_dir, "pca", snapshot=snapshot, hostaddr=hostaddr,
    )
    evidence = {
        "schema": "pca_backup_v1", "target": target_copy, "plan_hash": plan_hash,
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "archive": archive, "verification": "archive_readability_only",
        "restoration_tested": False,
    }
    evidence["evidence_hash"] = _evidence_hash(evidence)
    return evidence


def validate_evidence(evidence, target, plan_hash, *, now=None):
    """Validate a complete manifest and its on-disk archive, never a reference."""
    if type(evidence) is not dict or set(evidence) != _EVIDENCE_KEYS:
        raise ValueError("Expected a complete backup evidence dictionary.")
    if evidence["schema"] != "pca_backup_v1":
        raise ValueError("Unsupported backup evidence schema.")
    _require_hash(plan_hash)
    _require_hash(evidence["plan_hash"])
    _require_hash(evidence["evidence_hash"])
    if (type(target) is not dict or type(evidence["target"]) is not dict
            or _canonical(evidence["target"]) != _canonical(target)
            or evidence["plan_hash"] != plan_hash):
        raise ValueError("Backup evidence target or plan does not match.")
    if (evidence["verification"] != "archive_readability_only"
            or evidence["restoration_tested"] is not False):
        raise ValueError("Invalid backup verification markers.")
    archive = evidence["archive"]
    if type(archive) is not dict or set(archive) != {"path", "sha256", "size_bytes"}:
        raise ValueError("Expected complete archive metadata.")
    if _evidence_hash(evidence) != evidence["evidence_hash"]:
        raise ValueError("Backup evidence checksum mismatch.")
    try:
        created = datetime.datetime.fromisoformat(evidence["created_at"])
    except (TypeError, ValueError):
        raise ValueError("Invalid backup evidence timestamp.") from None
    now = datetime.datetime.now(datetime.UTC) if now is None else now
    if (created.utcoffset() != datetime.timedelta(0) or now.utcoffset() is None):
        raise ValueError("Backup evidence requires a UTC timestamp and aware clock.")
    age = now - created
    if not datetime.timedelta(0) <= age <= datetime.timedelta(hours=1):
        raise ValueError("Backup evidence is future-dated or older than one hour.")
    verify_archive(archive)
    return evidence
