"""CCA-only Debian PostgreSQL socket guard, without changing PCA artifacts.

Adapted from the vetted PCA guard at 74ac83e. Checks detect observed replacement,
not hostile root or a mutation after the final check. Caller work must remain
inside the context, including any transaction commit.
"""

import os
import stat
from contextlib import ExitStack, contextmanager

from . import pca_protected_artifacts as artifacts

ArtifactSecurityError = artifacts.ArtifactSecurityError


def _assert_unprivileged_runtime():
    uid = artifacts.assert_unprivileged_runtime()
    # The PCA primitive at d1e4e73 verifies UIDs/capabilities/NoNewPrivs and the
    # initial UID namespace, but does not yet verify saved/filesystem GIDs.
    with open("/proc/self/status", "r", encoding="ascii") as stream:
        fields = {}
        for line in stream:
            key, separator, value = line.partition(":")
            if separator:
                if key in fields:
                    raise ArtifactSecurityError("Duplicate proc status field")
                fields[key] = value.split()
    try:
        gids = fields["Gid"]
        if (len(gids) != 4 or any(not item.isdecimal() for item in gids)
                or len(set(int(item, 10) for item in gids)) != 1
                or int(gids[0], 10) != os.getegid()):
            raise ValueError
    except (KeyError, ValueError) as error:
        raise ArtifactSecurityError("Runtime must have four equal GIDs matching its effective GID") from error
    return uid


@contextmanager
def protected_postgresql_socket(host, port, owner_uid, owner_gid):
    """Hold and recheck the independently attested Debian socket namespace.

    Debian's postgres-owned 2775 directory is allowed, but the runtime must not
    own it or belong to its writable group. Only /var/run -> /run may be a link.
    """
    uid = _assert_unprivileged_runtime()
    if (host not in ("/run/postgresql", "/var/run/postgresql")
            or not isinstance(port, str) or not port.isascii() or not port.isdigit()
            or not 1 <= int(port) <= 65535
            or any(type(value) is not int or value <= 0 for value in (owner_uid, owner_gid))
            or uid == owner_uid or owner_gid in {os.getgid(), os.getegid(), *os.getgroups()}):
        raise ArtifactSecurityError("Untrusted PostgreSQL socket endpoint or runtime identity")
    with ExitStack() as stack:
        if host == "/var/run/postgresql":
            var = stack.enter_context(artifacts._directories(["var"], {0}, False))
            alias = os.stat("run", dir_fd=var, follow_symlinks=False)
            if (not stat.S_ISLNK(alias.st_mode) or alias.st_uid != 0
                    or os.readlink("run", dir_fd=var) != "/run"):
                raise ArtifactSecurityError("Only the root-owned /var/run -> /run alias is trusted")
        run = stack.enter_context(artifacts._directories(["run"], {0}, False))
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
        if (artifacts._directory_signature(os.fstat(fd)) != artifacts._directory_signature(directory)
                or artifacts._directory_signature(os.stat("postgresql", dir_fd=run, follow_symlinks=False))
                != artifacts._directory_signature(directory)
                or artifacts._signature(os.stat(name, dir_fd=fd, follow_symlinks=False)) != artifacts._signature(socket)):
            raise ArtifactSecurityError("PostgreSQL socket endpoint changed or was replaced")
        if host == "/var/run/postgresql" and (
                artifacts._signature(os.stat("run", dir_fd=var, follow_symlinks=False)) != artifacts._signature(alias)
                or os.readlink("run", dir_fd=var) != "/run"):
            raise ArtifactSecurityError("PostgreSQL socket alias changed")
