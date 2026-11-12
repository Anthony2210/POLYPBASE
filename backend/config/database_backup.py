"""Archive helpers without Django settings or manifest writes.

Evidence hashes detect changes, not authenticity. Readability is not a restore test.
Only create_complete_archive opens a database connection, for deployment backups.
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
CLIENT_VERSION_TIMEOUT_SECONDS = 30
# Debian's /usr/bin entries are pg_wrapper: without --cluster/PG* hints (which
# _environment strips) it selects a version from local cluster configuration,
# such as the default-port cluster, never from the archive or target server.
# Callers needing a specific major pass client_major.
PG_RESTORE = "/usr/bin/pg_restore"
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
        # Historical peer backups explicitly disable the default password file.
        # Existing callers without this option keep their original environment.
        if "passfile" in database.get("OPTIONS", {}):
            environment["PGPASSFILE"] = str(database["OPTIONS"]["passfile"])
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


def require_client(name, major):
    """Return the versioned Debian client path, refusing anything not root-controlled.

    The binary and every ancestor must be root-owned real files/directories without
    group/other write access, and ``--version`` must report exactly this major.
    """
    if (name not in ("pg_dump", "pg_restore") or type(major) is not int
            or not 10 <= major <= 99):
        raise ValueError("Unsupported PostgreSQL client request.")
    path = f"/usr/lib/postgresql/{major}/bin/{name}"
    try:
        parts = path.split("/")
        for depth in range(1, len(parts)):
            # lstat never follows a link: a symlinked ancestor is not a directory.
            info = os.lstat("/".join(parts[:depth]) or "/")
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                    or info.st_mode & 0o022):
                raise ValueError
        info = os.lstat(path)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o022 or not info.st_mode & 0o111):
            raise ValueError
        output = subprocess.run(
            [path, "--version"], check=True, env=_environment(),
            timeout=CLIENT_VERSION_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        ).stdout
        reported = re.fullmatch(
            rf"{name} \(PostgreSQL\) (\d+)\.\d+(?: \([^\n]*\))?\n?",
            output.decode("ascii"),
        )
        if reported is None or int(reported.group(1)) != major:
            raise ValueError
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError, ValueError):
        raise ValueError(
            f"PostgreSQL {major} {name} is unavailable, unsafe or reports another version."
        ) from None
    return path


def verify_archive(archive, *, client_major=None):
    """Rehash and check readability, optionally matching recorded size and hash.

    Without client_major the historical /usr/bin/pg_restore is used unchanged.
    """
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
    pg_restore = PG_RESTORE if client_major is None else require_client("pg_restore", client_major)
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
        _run([pg_restore, "--list", str(path)], _environment())
        after = path.lstat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise ValueError("Archive changed during verification.")
        return result
    except OSError:
        raise ValueError("Archive is missing or inaccessible.") from None


def create_archive(database, output_dir, label, *, snapshot=None, hostaddr=None,
                   client_major=None, preserve_authority=False):
    """Create a private custom archive, optionally preserving ownership and ACLs.

    Historical callers retain --no-owner/--no-acl unless preserve_authority is True.
    Without client_major the historical wrapper clients are used unchanged.
    """
    if not isinstance(label, str) or not _LABEL.fullmatch(label):
        raise ValueError("Unsafe backup label.")
    if database.get("ENGINE") != "django.db.backends.postgresql":
        raise ValueError("The configured database is not PostgreSQL.")
    if snapshot is not None and (not isinstance(snapshot, str) or not snapshot.strip()):
        raise ValueError("Snapshot must be a nonempty string.")
    if hostaddr is not None:
        if str(database.get("HOST", "")).startswith("/"):
            raise ValueError("Host address cannot be supplied for a Unix socket host.")
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
    # Refuse either unavailable client before creating a directory or dumping data.
    pg_dump = "/usr/bin/pg_dump"
    if client_major is not None:
        pg_dump = require_client("pg_dump", client_major)
        require_client("pg_restore", client_major)
    directory = Path(output_dir).resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{label}-{timestamp}.dump"
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        command = [pg_dump, "--format=custom"]
        if not preserve_authority:
            command.extend(["--no-owner", "--no-acl"])
        command.extend([
            "--no-password", "--file", str(path), "--host", str(database["HOST"]),
            "--port", str(database["PORT"]), "--username", str(database["USER"]),
            "--dbname", name,
        ])
        if snapshot is not None:
            command.extend(["--snapshot", snapshot])
        # Pin the address without changing the hostname used for TLS verification.
        _run(command, _environment(database, hostaddr=hostaddr))
        if client_major is None:
            return verify_archive({"path": str(path)})
        return verify_archive({"path": str(path)}, client_major=client_major)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _connect_backup(database):
    """Use explicit libpq routing, without service, role or password-file fallback."""
    import psycopg

    if any(key.upper().startswith("PG") for key in os.environ):
        raise ValueError("Ambient PostgreSQL connection overrides are not permitted.")
    for key in ("HOST", "PORT", "USER", "NAME"):
        if database.get(key) is None or not str(database.get(key, "")).strip():
            raise ValueError("Explicit database connection parameters are required.")
    name = str(database["NAME"])
    if "=" in name or name.startswith(("postgresql://", "postgres://")):
        raise ValueError("Database name must not be a connection string.")
    options = database.get("OPTIONS", {})
    return psycopg.connect(
        host=str(database["HOST"]), port=str(database["PORT"]),
        dbname=name, user=str(database["USER"]),
        password=str(database.get("PASSWORD", "")),
        sslmode=str(options.get("sslmode", "prefer")),
        passfile=str(options.get("passfile", os.devnull)),
        hostaddr="", options="-c row_security=off",
        connect_timeout=10, autocommit=True,
    )


def _complete_inventory(cursor):
    """Reject unreadable/filtered data before pg_dump can silently omit a schema."""
    cursor.execute("""SELECT current_setting('server_version_num')::int,
        session_user = current_user""")
    version, direct_login = cursor.fetchone()
    if not 180000 <= version < 190000 or not direct_login:
        raise ValueError("Complete deployment backup requires a direct PostgreSQL 18 login.")
    cursor.execute("""SELECT oid, has_schema_privilege(oid, 'USAGE')
        FROM pg_catalog.pg_namespace
        WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'""")
    if any(not readable for _, readable in cursor.fetchall()):
        raise ValueError("Incomplete backup authority: unreadable schema.")
    cursor.execute("""SELECT c.oid, c.relkind, c.relispopulated,
        CASE WHEN c.relkind = 'S' THEN has_sequence_privilege(c.oid, 'SELECT')
             ELSE has_table_privilege(c.oid, 'SELECT') END,
        CASE WHEN c.relkind IN ('r','p') THEN row_security_active(c.oid) ELSE false END,
        n.nspname, c.relname, pg_get_userbyid(c.relowner)
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
          AND c.relkind IN ('r','p','m','S','f')""")
    expected = set()
    for oid, kind, populated, readable, filtered, schema, name, owner in cursor.fetchall():
        if not readable or filtered or kind == 'f':
            raise ValueError("Incomplete backup authority: unreadable, RLS or foreign relation.")
        if kind == 'r':
            expected.add((oid, "TABLE DATA"))
        elif kind == 'S' or (kind == 'm' and populated):
            # PG18 emits synthetic data entries with OID 0 for these objects.
            # Refuse ambiguous TOC names rather than splitting quoted identifiers.
            if any(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", value) is None
                   for value in (schema, name, owner)):
                raise ValueError("Ambiguous sequence or materialized view backup identity.")
            entry = "SEQUENCE SET" if kind == 'S' else "MATERIALIZED VIEW DATA"
            expected.add((f"{schema} {name} {owner}", entry))
    cursor.execute("""SELECT oid, has_largeobject_privilege(oid, 'SELECT')
        FROM pg_catalog.pg_largeobject_metadata""")
    # Large objects need a separately reviewed completeness verifier. Do not claim
    # a complete archive merely because their metadata is visible.
    if cursor.fetchall():
        raise ValueError("Complete deployment backup does not yet support large objects.")
    return expected


def _verify_inventory(archive, expected, client_major):
    client = require_client("pg_restore", client_major)
    try:
        output = subprocess.run(
            [client, "--list", archive["path"]], check=True, env=_environment(),
            timeout=TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        ).stdout.decode("utf-8")
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        raise ValueError("PostgreSQL archive inventory verification failed.") from None
    entries = set()
    for line in output.splitlines():
        match = re.match(
            r"^\d+; \d+ (\d+) (TABLE DATA|SEQUENCE SET|MATERIALIZED VIEW DATA) (.*)$", line,
        )
        if match:
            identity = int(match[1]) if match[2] == "TABLE DATA" else match[3]
            entries.add((identity, match[2]))
    if not expected <= entries:
        raise ValueError("Incomplete backup archive: expected data entries are missing.")


def create_complete_archive(database, output_dir, label, *, client_major=18):
    """Fail closed on missing data authority and verify same-snapshot TOC coverage.

    Does not grant privileges or test restoration. The normal deployment caller
    must use this function, not the historical readability-only PCA helper.
    """
    if database.get("ENGINE") != "django.db.backends.postgresql":
        raise ValueError("The configured database is not PostgreSQL.")
    require_client("pg_dump", client_major)
    require_client("pg_restore", client_major)
    archive = None
    try:
        with _connect_backup(database) as connection:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cursor.execute("SET LOCAL statement_timeout = '60s'")
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                expected = _complete_inventory(cursor)
                cursor.execute("SELECT pg_export_snapshot()")
                snapshot = cursor.fetchone()[0]
                archive = create_archive(
                    database, output_dir, label, snapshot=snapshot, client_major=client_major,
                    preserve_authority=True,
                )
                _verify_inventory(archive, expected, client_major)
        return archive
    except BaseException as error:
        if archive is not None:
            Path(archive["path"]).unlink(missing_ok=True)
        if isinstance(error, (ValueError, KeyboardInterrupt, SystemExit)):
            raise
        # Database errors can contain connection identity or application values.
        raise ValueError("Complete deployment backup verification failed.") from None


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
