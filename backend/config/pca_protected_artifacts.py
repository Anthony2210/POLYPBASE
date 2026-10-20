"""Linux-only protected production artifacts; no environment or trust-root overrides.

Checks detect observed replacement/mutation, not hostile root or another process
with the same UID after the final check. Artifact directories must be private.
Failed writes can leave an incomplete file: never unlink a possibly replaced name.
"""

import json
import os
import stat
import sys
from contextlib import ExitStack, contextmanager

PINNED_ATTESTATION = "/etc/polypbase/pca-production-target.json"


class ArtifactSecurityError(ValueError):
    """The runtime or artifact does not satisfy the production trust policy."""


def assert_unprivileged_runtime():
    """Read Linux procfs independently and return the verified nonzero UID."""
    if sys.platform != "linux":
        raise ArtifactSecurityError("Protected artifacts require Linux")
    with open("/proc/self/status", "r", encoding="ascii") as stream:
        fields = {}
        for line in stream:
            key, separator, value = line.partition(":")
            if separator:
                if key in fields:
                    raise ArtifactSecurityError("Duplicate proc status field")
                fields[key] = value.split()
    try:
        uids = fields["Uid"]
        if len(uids) != 4 or any(not item.isdecimal() for item in uids):
            raise ValueError
        uids = tuple(int(item, 10) for item in uids)
        if not uids[0] or len(set(uids)) != 1 or os.geteuid() != uids[0]:
            raise ValueError
        gids = fields["Gid"]
        if (len(gids) != 4 or any(not item.isdecimal() for item in gids)
                or len(set(int(item, 10) for item in gids)) != 1
                or int(gids[0], 10) != os.getegid()):
            raise ValueError
        for key in ("CapInh", "CapPrm", "CapEff", "CapAmb"):
            values = fields[key]
            if (len(values) != 1 or not values[0]
                    or any(c not in "0123456789abcdefABCDEF" for c in values[0])
                    or int(values[0], 16) != 0):
                raise ValueError
        if fields["NoNewPrivs"] != ["1"]:
            raise ValueError
    except (KeyError, ValueError) as error:
        raise ArtifactSecurityError("Runtime must have equal nonzero UIDs, equal GIDs, no capabilities and NoNewPrivs=1") from error
    # Namespace-local UID 0 must not stand in for host root ownership.
    with open("/proc/self/uid_map", "r", encoding="ascii") as stream:
        rows = [line.split() for line in stream]
    if (len(rows) != 1 or len(rows[0]) != 3
            or any(not item.isdecimal() for item in rows[0])
            or tuple(int(item, 10) for item in rows[0]) != (0, 0, 4294967295)):
        raise ArtifactSecurityError("Runtime must use the initial user namespace UID map")
    return uids[0]


def _parts(path):
    path = os.fspath(path)
    if (not isinstance(path, str) or not path.startswith("/")
            or "\x00" in path or "\\" in path
            or (path != "/" and any(p in ("", ".", "..") for p in path[1:].split("/")))):
        raise ArtifactSecurityError("Expected an absolute canonical lexical POSIX path")
    return [] if path == "/" else path[1:].split("/")


def _signature(info):
    return tuple(getattr(info, "st_" + field) for field in (
        "mode", "uid", "gid", "dev", "ino", "nlink", "size", "mtime_ns", "ctime_ns",
    ))


def _directory_signature(info):
    # Directory timestamps and size legitimately change when creating an artifact.
    return _signature(info)[:6]


def _check_directory(info, owners, private=False):
    mode = stat.S_IMODE(info.st_mode)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in owners
            or mode & 0o022 or (private and mode != 0o700)):
        raise ArtifactSecurityError("Unsafe directory ownership, type or permissions")


def _check_file(info, owners):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid not in owners
            or info.st_nlink != 1 or info.st_mode & 0o022):
        raise ArtifactSecurityError("Unsafe file ownership, type, links or permissions")


@contextmanager
def _directories(parts, owners, private):
    """Hold every ancestor FD and recheck both its metadata and its name."""
    descriptors = []
    opened = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open("/", flags)
        opened.append(fd)
        descriptors.append((fd, None, "/", os.fstat(fd)))
        _check_directory(descriptors[-1][3], owners, private and not parts)
        for index, name in enumerate(parts):
            parent = fd
            fd = os.open(name, flags, dir_fd=parent)
            opened.append(fd)
            info = os.fstat(fd)
            descriptors.append((fd, parent, name, info))
            _check_directory(info, owners, private and index == len(parts) - 1)
        yield fd
        for fd, parent, name, original in reversed(descriptors):
            current = os.fstat(fd)
            entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (_directory_signature(current) != _directory_signature(original)
                    or _directory_signature(entry) != _directory_signature(original)):
                raise ArtifactSecurityError("Directory changed or was replaced")
    finally:
        for fd in reversed(opened):
            os.close(fd)


def _verify_file(fd, parent, name, original, owners):
    current = os.fstat(fd)
    entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
    _check_file(current, owners)
    if _signature(current) != _signature(original) or _signature(entry) != _signature(original):
        raise ArtifactSecurityError("File changed or was replaced")


def _read_bytes(fd, parent, name, owners):
    original = os.fstat(fd)
    _check_file(original, owners)
    chunks = []
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    _verify_file(fd, parent, name, original, owners)
    data = b"".join(chunks)
    if len(data) != original.st_size:
        raise ArtifactSecurityError("File size does not match content")
    return data


def _read_json(path, owners, private):
    parts = _parts(path)
    if not parts:
        raise ArtifactSecurityError("Expected a file path")
    with _directories(parts[:-1], owners, private) as parent:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        try:
            data = _read_bytes(fd, parent, parts[-1], owners)
            value = json.loads(data)
        finally:
            os.close(fd)
    return value


def read_attestation():
    """Read only the pinned, root-owned attestation under root-owned ancestors."""
    assert_unprivileged_runtime()
    return _read_json(PINNED_ATTESTATION, {0}, False)


def read_historical_temperature_attestation():
    """Read the dedicated root-owned historical temperature target, without overrides."""
    assert_unprivileged_runtime()
    return _read_json("/etc/polypbase/historical-temperature-production-target.json", {0}, False)


def read_json(path):
    """Read a manifest beneath a private root/runtime-owned directory."""
    uid = assert_unprivileged_runtime()
    return _read_json(path, {0, uid}, True)


@contextmanager
def protected_directory(path):
    """Hold ancestors across caller work; revalidate bindings on normal exit."""
    uid = assert_unprivileged_runtime()
    with _directories(_parts(path), {0, uid}, True):
        yield


@contextmanager
def protected_file(path):
    """Hold file and ancestor FDs; yield its signature and verify on normal exit."""
    uid = assert_unprivileged_runtime()
    owners = {0, uid}
    parts = _parts(path)
    if not parts:
        raise ArtifactSecurityError("Expected a file path")
    with _directories(parts[:-1], owners, True) as parent:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        try:
            info = os.fstat(fd)
            _check_file(info, owners)
            yield _signature(info)
            _verify_file(fd, parent, parts[-1], info, owners)
        finally:
            os.close(fd)


@contextmanager
def protected_postgresql_socket(host, port, owner_uid, owner_gid):
    """Trust only the independently attested Debian PostgreSQL socket namespace.

    Debian's postgres-owned 2775 directory is allowed, but the runtime must not
    own it or belong to its writable group. Only /var/run -> /run may be a link.
    """
    uid = assert_unprivileged_runtime()
    if (host not in ("/run/postgresql", "/var/run/postgresql")
            or not isinstance(port, str) or not port.isascii() or not port.isdigit()
            or not 1 <= int(port) <= 65535
            or any(type(value) is not int or value <= 0 for value in (owner_uid, owner_gid))
            or uid == owner_uid or owner_gid in {os.getgid(), os.getegid(), *os.getgroups()}):
        raise ArtifactSecurityError("Untrusted PostgreSQL socket endpoint or runtime identity")
    with ExitStack() as stack:
        if host == "/var/run/postgresql":
            var = stack.enter_context(_directories(["var"], {0}, False))
            alias = os.stat("run", dir_fd=var, follow_symlinks=False)
            if (not stat.S_ISLNK(alias.st_mode) or alias.st_uid != 0
                    or os.readlink("run", dir_fd=var) != "/run"):
                raise ArtifactSecurityError("Only the root-owned /var/run -> /run alias is trusted")
        run = stack.enter_context(_directories(["run"], {0}, False))
        fd = os.open("postgresql", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=run)
        stack.callback(os.close, fd)
        directory = os.fstat(fd)
        if (not stat.S_ISDIR(directory.st_mode)
                or (directory.st_uid, directory.st_gid) != (owner_uid, owner_gid)
                or directory.st_mode & 0o002):
            raise ArtifactSecurityError("Unsafe PostgreSQL socket directory")
        # Mode bits alone miss named POSIX ACL entries granting runtime access.
        if os.access(".", os.W_OK, dir_fd=fd, effective_ids=True):
            raise ArtifactSecurityError("Runtime must not be able to write the PostgreSQL socket directory")
        name = f".s.PGSQL.{port}"
        socket = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if (not stat.S_ISSOCK(socket.st_mode)
                or (socket.st_uid, socket.st_gid) != (owner_uid, owner_gid)):
            raise ArtifactSecurityError("Unsafe PostgreSQL socket ownership or type")
        yield
        if (_directory_signature(os.fstat(fd)) != _directory_signature(directory)
                or _directory_signature(os.stat("postgresql", dir_fd=run, follow_symlinks=False))
                != _directory_signature(directory)
                or _signature(os.stat(name, dir_fd=fd, follow_symlinks=False)) != _signature(socket)):
            raise ArtifactSecurityError("PostgreSQL socket endpoint changed or was replaced")
        if host == "/var/run/postgresql" and (
                _signature(os.stat("run", dir_fd=var, follow_symlinks=False)) != _signature(alias)
                or os.readlink("run", dir_fd=var) != "/run"):
            raise ArtifactSecurityError("PostgreSQL socket alias changed")


def validate_directory(path):
    """Validate all ancestors and require the final directory to be mode 0700."""
    with protected_directory(path):
        pass


def validate_file(path):
    """Validate a manifest and return its stable metadata signature."""
    with protected_file(path) as signature:
        pass
    return signature


def write_json(path, value):
    """Exclusively create a 0600 manifest; never create parents or overwrite."""
    uid = assert_unprivileged_runtime()
    owners = {0, uid}
    parts = _parts(path)
    if not parts:
        raise ArtifactSecurityError("Expected a file path")
    data = (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    with _directories(parts[:-1], owners, True) as parent:
        fd = os.open(parts[-1], os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600, dir_fd=parent)
        try:
            original = os.fstat(fd)
            _check_file(original, owners)
            if stat.S_IMODE(original.st_mode) != 0o600 or original.st_size != 0:
                raise ArtifactSecurityError("New file must be empty and mode 0600")
            offset = 0
            while offset < len(data):
                count = os.write(fd, data[offset:])
                if count <= 0:
                    raise ArtifactSecurityError("Incomplete artifact write")
                offset += count
            os.fsync(fd)
            current = os.fstat(fd)
            if _signature(current)[:6] != _signature(original)[:6]:
                raise ArtifactSecurityError("New file metadata changed")
            os.lseek(fd, 0, os.SEEK_SET)
            if _read_bytes(fd, parent, parts[-1], owners) != data:
                raise ArtifactSecurityError("Written content changed")
            os.fsync(parent)
            _verify_file(fd, parent, parts[-1], current, owners)
        finally:
            os.close(fd)
