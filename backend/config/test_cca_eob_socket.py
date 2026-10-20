"""Connection-free simulated Linux socket tests, including on Windows.

Adapted from the vetted main 74ac83e socket tests. Descriptor identities are
separate from directory entries. These test policy and race detection, not Linux
kernel behavior, PostgreSQL connections, or the production peer configuration.
"""

import copy
import errno
import io
import stat
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from config import cca_eob_socket as sockets
from config import pca_protected_artifacts as artifacts

STATUS = """Name:\ttest
Uid:\t1000 1000 1000 1000
Gid:\t1000 1000 1000 1000
CapInh:\t0000000000000000
CapPrm:\t0000000000000000
CapEff:\t0000000000000000
CapAmb:\t0000000000000000
NoNewPrivs:\t1
"""


class SimulatedLinux:
    """Scoped syscall model: an open directory survives name replacement."""

    def __init__(self):
        self.nodes = {}
        self.handles = {}
        self.open_calls = []
        self.next_fd = 20
        self.next_inode = 1
        self.hook = lambda event, path: None
        self.status = STATUS
        self.uid_map = "0 0 4294967295\n"
        self.add("/", stat.S_IFDIR | 0o755)

    def add(self, path, mode, uid=0, gid=0, data=b""):
        node = SimpleNamespace(
            st_mode=mode, st_uid=uid, st_gid=gid, st_dev=1,
            st_ino=self.next_inode, st_nlink=1, st_size=len(data),
            st_mtime_ns=10, st_ctime_ns=10, data=data,
        )
        self.next_inode += 1
        self.nodes[path] = node
        return node

    def path(self, name, parent):
        if parent is None:
            return name
        return self.handles[parent][0].rstrip("/") + "/" + name

    def open(self, name, flags, mode=0o777, *, dir_fd=None):
        path = self.path(name, dir_fd)
        self.open_calls.append((path, flags, mode, dir_fd))
        self.hook("open", path)
        node = self.nodes.get(path)
        if node is None:
            raise FileNotFoundError(path)
        if stat.S_ISLNK(node.st_mode) and flags & sockets.os.O_NOFOLLOW:
            raise OSError(errno.ELOOP, "Symlink")
        if flags & sockets.os.O_DIRECTORY and not stat.S_ISDIR(node.st_mode):
            raise NotADirectoryError(path)
        self.next_fd += 1
        self.handles[self.next_fd] = [path, node]
        return self.next_fd

    def fstat(self, fd):
        path, node = self.handles[fd]
        self.hook("fstat", path)
        return copy.copy(node)

    def entry_stat(self, name, *, dir_fd=None, follow_symlinks=True):
        if follow_symlinks:
            raise AssertionError("Entry checks must not follow symlinks")
        path = self.path(name, dir_fd)
        self.hook("stat", path)
        if path not in self.nodes:
            raise FileNotFoundError(path)
        return copy.copy(self.nodes[path])

    def close(self, fd):
        del self.handles[fd]

    def proc_open(self, path, *args, **kwargs):
        if path == "/proc/self/status":
            return io.StringIO(self.status)
        if path == "/proc/self/uid_map":
            if self.uid_map is None:
                raise FileNotFoundError(path)
            return io.StringIO(self.uid_map)
        raise AssertionError("Unexpected high-level file access")

    def install(self, stack):
        stack.enter_context(patch.object(artifacts.sys, "platform", "linux"))
        stack.enter_context(patch("builtins.open", side_effect=self.proc_open))
        for name, value in (("geteuid", 1000), ("getgid", 1000), ("getegid", 1000), ("getgroups", [1000, 1001])):
            stack.enter_context(patch.object(sockets.os, name, return_value=value, create=True))
        # Windows lacks these Linux flags; distinct simulated values are deliberate.
        for index, name in enumerate(("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC"), 20):
            stack.enter_context(patch.object(sockets.os, name, 1 << index, create=True))
        for name, function in (("open", self.open), ("fstat", self.fstat), ("stat", self.entry_stat), ("close", self.close)):
            stack.enter_context(patch.object(sockets.os, name, side_effect=function))
        self.access = stack.enter_context(patch.object(sockets.os, "access", return_value=False))


class ProtectedPostgresqlSocketTests(SimpleTestCase):
    def setUp(self):
        self.fs = SimulatedLinux()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.fs.install(self.stack)
        self.readlink = self.stack.enter_context(patch.object(
            sockets.os, "readlink", side_effect=self.read_alias, create=True,
        ))
        self.fs.add("/run", stat.S_IFDIR | 0o755)
        self.fs.add("/var", stat.S_IFDIR | 0o755)
        self.fs.add("/var/run", stat.S_IFLNK | 0o777, data=b"/run")
        self.directory = self.fs.add("/run/postgresql", stat.S_IFDIR | 0o2775, uid=105, gid=110)
        self.socket_path = "/run/postgresql/.s.PGSQL.5433"
        self.socket = self.fs.add(self.socket_path, stat.S_IFSOCK | 0o777, uid=105, gid=110)

    def tearDown(self):
        self.assertEqual(self.fs.handles, {}, "Every descriptor must be closed")

    def read_alias(self, name, *, dir_fd):
        self.assertEqual(name, "run")
        self.assertEqual(self.fs.handles[dir_fd][0], "/var")
        return self.fs.nodes[self.fs.path(name, dir_fd)].data.decode("ascii")

    def endpoint(self, host="/var/run/postgresql", port="5433", owner_uid=105, owner_gid=110):
        return sockets.protected_postgresql_socket(host, port, owner_uid, owner_gid)

    def reject_endpoint(self, **kwargs):
        with self.assertRaises((artifacts.ArtifactSecurityError, OSError)):
            with self.endpoint(**kwargs):
                self.fail("Unsafe endpoint entered caller body")
        self.assertEqual(self.fs.handles, {})

    def test_run_and_debian_alias_allow_group_writable_postgres_directory(self):
        for host in ("/run/postgresql", "/var/run/postgresql"):
            with self.subTest(host=host):
                self.fs.open_calls.clear()
                self.fs.access.reset_mock()
                with self.endpoint(host=host) as result:
                    self.assertIsNone(result)
                    directory_fd = next(fd for fd, handle in self.fs.handles.items()
                                        if handle[0] == "/run/postgresql")
                    self.fs.access.assert_called_once_with(
                        ".", sockets.os.W_OK, dir_fd=directory_fd, effective_ids=True,
                    )
                    paths = [handle[0] for handle in self.fs.handles.values()]
                    self.assertCountEqual(paths, (
                        ["/", "/run", "/run/postgresql"] if host == "/run/postgresql"
                        else ["/", "/var", "/", "/run", "/run/postgresql"]
                    ))
                self.assertEqual(self.fs.handles, {})
                for path, flags, _, parent in self.fs.open_calls:
                    for flag in (sockets.os.O_DIRECTORY, sockets.os.O_NOFOLLOW, sockets.os.O_CLOEXEC):
                        self.assertTrue(flags & flag)
                    self.assertEqual(parent is None, path == "/")
                self.assertNotIn(self.socket_path, [call[0] for call in self.fs.open_calls])
        self.assertEqual(self.readlink.call_count, 2)

    def test_untrusted_and_noncanonical_hosts_rejected_before_open(self):
        for host in (
            "postgresql", "run/postgresql", "/tmp/postgresql", "/run", "/run/postgresql/",
            "/run//postgresql", "/run/./postgresql", "/var/run/../run/postgresql",
            "localhost", "", None, b"/run/postgresql", "C:\\run\\postgresql",
        ):
            with self.subTest(host=host):
                self.reject_endpoint(host=host)
        self.assertEqual(self.fs.open_calls, [])

    def test_invalid_ports_and_attested_owner_ids_rejected_before_open(self):
        for port in (None, 5433, True, "", "0", "65536", "-1", "+5433", " 5433", "5433/other", "５４３３"):
            with self.subTest(port=port):
                self.reject_endpoint(port=port)
        for field in ("owner_uid", "owner_gid"):
            for value in (None, 0, -1, True, 105.0, "105"):
                with self.subTest(field=field, value=value):
                    self.reject_endpoint(**{field: value})
        self.assertEqual(self.fs.open_calls, [])

    def test_runtime_owner_and_each_group_membership_rejected_before_open(self):
        self.reject_endpoint(owner_uid=1000)
        for name, value in (("getgid", 110), ("getegid", 110), ("getgroups", [1000, 110])):
            with self.subTest(identity=name):
                self.fs.status = STATUS
                if name == "getegid":
                    self.fs.status = STATUS.replace("Gid:\t1000 1000 1000 1000", "Gid:\t110 110 110 110")
                with patch.object(sockets.os, name, return_value=value):
                    self.reject_endpoint()
        self.assertEqual(self.fs.open_calls, [])

    def test_acl_write_grant_rejected_before_body_and_descriptors_closed(self):
        def writable(name, mode, *, dir_fd, effective_ids):
            self.assertEqual(name, ".")
            self.assertEqual(mode, sockets.os.W_OK)
            self.assertIs(effective_ids, True)
            self.assertIs(self.fs.handles[dir_fd][1], self.directory)
            return True

        self.fs.access.side_effect = writable
        for host in ("/run/postgresql", "/var/run/postgresql"):
            with self.subTest(host=host):
                self.fs.access.reset_mock()
                self.reject_endpoint(host=host)
                self.fs.access.assert_called_once()

    def test_runtime_requires_four_equal_decimal_gids(self):
        for value in (
            "", "1000 1000 1000", "1000 1000 1000 1000 1000", "x x x x",
            "-1 -1 -1 -1", "+1000 +1000 +1000 +1000", "1000.0 1000 1000 1000",
            "110 1000 1000 1000", "1000 110 1000 1000",
            "1000 1000 110 1000", "1000 1000 1000 110", "110 110 110 110",
        ):
            with self.subTest(gids=value):
                self.fs.status = STATUS.replace("Gid:\t1000 1000 1000 1000", "Gid:\t" + value)
                with self.assertRaisesRegex(artifacts.ArtifactSecurityError, "equal GIDs"):
                    with self.endpoint():
                        self.fail("Invalid GIDs entered caller body")
        self.assertEqual(self.fs.open_calls, [])

    def test_runtime_requires_all_proc_security_fields_without_duplicates(self):
        for field in ("Uid", "Gid", "CapInh", "CapPrm", "CapEff", "CapAmb", "NoNewPrivs"):
            line = next(line for line in STATUS.splitlines() if line.startswith(field + ":"))
            for status in (STATUS.replace(line + "\n", ""), STATUS + line + "\n"):
                with self.subTest(field=field, status=status):
                    self.fs.status = status
                    self.reject_endpoint()
        self.assertEqual(self.fs.open_calls, [])

    def test_runtime_security_checked_before_open(self):
        for value in ("0 0 0 0", "1000 0 1000 1000", "1000 1000 0 1000", "1000 1000 1000 0", "2000 2000 2000 2000"):
            with self.subTest(uids=value):
                self.fs.status = STATUS.replace("Uid:\t1000 1000 1000 1000", "Uid:\t" + value)
                self.reject_endpoint()
        for field in ("CapInh", "CapPrm", "CapEff", "CapAmb"):
            for value in ("0000000000000001", "garbage", "-0", "0 0"):
                with self.subTest(field=field, value=value):
                    self.fs.status = STATUS.replace(field + ":\t0000000000000000", field + ":\t" + value)
                    self.reject_endpoint()
        self.fs.status = STATUS.replace("NoNewPrivs:\t1", "NoNewPrivs:\t0")
        self.reject_endpoint()
        self.fs.status = STATUS
        for uid_map in ("0 1000 1\n", "0 0 4294967295\n1 1 1\n", "", None):
            with self.subTest(uid_map=uid_map):
                self.fs.uid_map = uid_map
                self.reject_endpoint()
        self.assertEqual(self.fs.open_calls, [])

    def test_windows_and_other_actual_platforms_refused_before_proc_or_socket_access(self):
        for platform in ("win32", "darwin"):
            with self.subTest(platform=platform), patch.object(artifacts.sys, "platform", platform):
                with patch("builtins.open") as proc_open:
                    with self.assertRaisesRegex(artifacts.ArtifactSecurityError, "require Linux"):
                        with self.endpoint():
                            self.fail("Non-Linux runtime entered caller body")
                    proc_open.assert_not_called()
        self.assertEqual(self.fs.open_calls, [])

    def test_ancestors_require_root_owner_and_no_group_or_world_write(self):
        for path in ("/", "/run", "/var"):
            node = self.fs.nodes[path]
            for field, value in (("st_uid", 1000), ("st_mode", stat.S_IFDIR | 0o775), ("st_mode", stat.S_IFDIR | 0o1777)):
                with self.subTest(path=path, field=field, value=value):
                    original = getattr(node, field)
                    setattr(node, field, value)
                    self.reject_endpoint()
                    setattr(node, field, original)

    def test_postgres_directory_world_write_or_wrong_owner_and_gid_rejected(self):
        for field, value in (
            ("st_mode", stat.S_IFDIR | 0o2777), ("st_uid", 0), ("st_uid", 999),
            ("st_gid", 0), ("st_gid", 999),
        ):
            with self.subTest(field=field, value=value):
                original = getattr(self.directory, field)
                setattr(self.directory, field, value)
                self.reject_endpoint()
                setattr(self.directory, field, original)

    def test_symlink_directories_and_socket_or_non_socket_leaf_rejected(self):
        for path in ("/", "/var", "/run", "/run/postgresql", self.socket_path):
            node = self.fs.nodes[path]
            with self.subTest(symlink=path):
                original = node.st_mode
                node.st_mode = stat.S_IFLNK | 0o777
                self.reject_endpoint()
                node.st_mode = original
        for kind in (stat.S_IFREG, stat.S_IFDIR, stat.S_IFIFO):
            with self.subTest(kind=kind):
                self.socket.st_mode = kind | 0o777
                self.reject_endpoint()

    def test_socket_wrong_owner_or_gid_rejected(self):
        for field in ("st_uid", "st_gid"):
            for value in (0, 1000, 999):
                with self.subTest(field=field, value=value):
                    original = getattr(self.socket, field)
                    setattr(self.socket, field, value)
                    self.reject_endpoint()
                    setattr(self.socket, field, original)

    def test_alias_must_be_root_owned_symlink_to_absolute_run(self):
        alias = self.fs.nodes["/var/run"]
        for field, value in (
            ("st_uid", 1000), ("st_mode", stat.S_IFDIR | 0o755),
            ("data", b"../run"), ("data", b"/tmp/run"), ("data", b"/run/"),
        ):
            with self.subTest(field=field, value=value):
                original = getattr(alias, field)
                setattr(alias, field, value)
                self.reject_endpoint()
                setattr(alias, field, original)

    def test_socket_and_directory_name_replacements_rejected_on_exit(self):
        for path in (self.socket_path, "/run/postgresql", "/run", "/var", "/"):
            for symlink in (False, True):
                with self.subTest(path=path, symlink=symlink):
                    original = self.fs.nodes[path]
                    with self.assertRaises(artifacts.ArtifactSecurityError):
                        with self.endpoint():
                            self.fs.add(path, stat.S_IFLNK | 0o777 if symlink else original.st_mode,
                                        uid=original.st_uid, gid=original.st_gid)
                    self.assertEqual(self.fs.handles, {})
                    self.fs.nodes[path] = original

    def test_open_directory_metadata_mutations_rejected_on_exit(self):
        for field in ("st_mode", "st_uid", "st_gid", "st_dev", "st_ino", "st_nlink"):
            with self.subTest(field=field):
                original = getattr(self.directory, field)
                with self.assertRaisesRegex(artifacts.ArtifactSecurityError, "endpoint changed"):
                    with self.endpoint():
                        setattr(self.directory, field, original + 1)
                self.assertEqual(self.fs.handles, {})
                setattr(self.directory, field, original)

    def test_socket_metadata_mutations_rejected_on_exit(self):
        for field in ("mode", "uid", "gid", "dev", "ino", "nlink", "size", "mtime_ns", "ctime_ns"):
            with self.subTest(field=field):
                attr = "st_" + field
                original = getattr(self.socket, attr)
                with self.assertRaisesRegex(artifacts.ArtifactSecurityError, "endpoint changed"):
                    with self.endpoint():
                        setattr(self.socket, attr, original + 1)
                self.assertEqual(self.fs.handles, {})
                setattr(self.socket, attr, original)

    def test_alias_replacement_and_retargeting_rejected_on_exit(self):
        alias = self.fs.nodes["/var/run"]
        with self.assertRaisesRegex(artifacts.ArtifactSecurityError, "alias changed"):
            with self.endpoint():
                self.fs.add("/var/run", alias.st_mode, data=b"/run")
        self.assertEqual(self.fs.handles, {})
        self.fs.nodes["/var/run"] = alias
        with self.assertRaisesRegex(artifacts.ArtifactSecurityError, "alias changed"):
            with self.endpoint():
                alias.data = b"/tmp/run"
        self.assertEqual(self.fs.handles, {})

    def test_first_fstat_failures_close_all_descriptors(self):
        for path in ("/", "/var", "/run", "/run/postgresql"):
            with self.subTest(path=path):
                def fail(event, current_path):
                    if (event, current_path) == ("fstat", path):
                        raise OSError("Simulated fstat failure")
                self.fs.hook = fail
                with self.assertRaisesRegex(OSError, "Simulated fstat failure"):
                    with self.endpoint():
                        self.fail("Failed fstat entered caller body")
                self.assertEqual(self.fs.handles, {})
        self.fs.hook = lambda event, path: None

    def test_missing_socket_alias_or_directory_closes_all_descriptors(self):
        for path in ("/var/run", "/run", "/run/postgresql", self.socket_path):
            with self.subTest(path=path):
                node = self.fs.nodes.pop(path)
                self.reject_endpoint()
                self.fs.nodes[path] = node

    def test_body_errors_preserved_and_all_descriptors_closed(self):
        for host in ("/run/postgresql", "/var/run/postgresql"):
            with self.subTest(host=host):
                with self.assertRaisesRegex(RuntimeError, "caller failed"):
                    with self.endpoint(host=host):
                        self.directory.st_mtime_ns += 1
                        raise RuntimeError("caller failed")
                self.assertEqual(self.fs.handles, {})

    def test_directory_timestamp_changes_do_not_reject_a_stable_endpoint(self):
        with self.endpoint():
            self.directory.st_mtime_ns += 1
            self.directory.st_ctime_ns += 1
            self.directory.st_size += 1
        self.assertEqual(self.fs.handles, {})
