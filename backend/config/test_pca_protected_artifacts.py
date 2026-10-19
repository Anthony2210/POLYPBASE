"""Connection-free simulated Linux syscall tests, including on Windows.

The fake filesystem models descriptors separately from directory entries. These
are policy/race-detection tests, not evidence of Linux kernel syscall behavior.
"""

import copy
import errno
import inspect
import io
import json
import stat
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

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
    """Minimal syscall model: an open FD survives replacement of its name."""

    def __init__(self):
        self.nodes = {}
        self.handles = {}
        self.open_calls = []
        self.next_fd = 20
        self.next_inode = 1
        self.hook = lambda event, path: None
        self.status = STATUS
        self.uid_map = "         0          0 4294967295\n"
        self.add("/", stat.S_IFDIR | 0o755)
        self.add("/etc", stat.S_IFDIR | 0o755)
        self.add("/etc/polypbase", stat.S_IFDIR | 0o755)
        self.add(artifacts.PINNED_ATTESTATION, stat.S_IFREG | 0o644, data=b'{"target": "pinned"}')
        self.add("/srv", stat.S_IFDIR | 0o755)
        self.add("/srv/pca", stat.S_IFDIR | 0o700, uid=1000)
        self.add("/srv/pca/plan.json", stat.S_IFREG | 0o600, uid=1000, data=b'{"zero": 0}')

    def add(self, path, mode, uid=0, data=b"", gid=0):
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
        if flags & artifacts.os.O_CREAT:
            if node is not None and flags & artifacts.os.O_EXCL:
                raise FileExistsError(path)
            if node is None:
                node = self.add(path, stat.S_IFREG | mode, uid=1000)
        if node is None:
            raise FileNotFoundError(path)
        if stat.S_ISLNK(node.st_mode) and flags & artifacts.os.O_NOFOLLOW:
            raise OSError(errno.ELOOP, "Symlink")
        if flags & artifacts.os.O_DIRECTORY and not stat.S_ISDIR(node.st_mode):
            raise NotADirectoryError(path)
        self.next_fd += 1
        self.handles[self.next_fd] = [path, node, 0]
        return self.next_fd

    def fstat(self, fd):
        path, node, _ = self.handles[fd]
        self.hook("fstat", path)
        return copy.copy(node)

    def entry_stat(self, name, *, dir_fd=None, follow_symlinks=True):
        if follow_symlinks:
            raise AssertionError("Entry checks must not follow symlinks")
        path = self.path(name, dir_fd)
        self.hook("stat", path)
        return copy.copy(self.nodes[path])

    def read(self, fd, count):
        path, node, offset = self.handles[fd]
        self.hook("read", path)
        result = node.data[offset:offset + count]
        self.handles[fd][2] += len(result)
        return result

    def write(self, fd, data):
        path, node, offset = self.handles[fd]
        self.hook("write", path)
        # Exercise partial-write handling on every successful output.
        data = data[:7]
        node.data = node.data[:offset] + data + node.data[offset + len(data):]
        node.st_size = len(node.data)
        node.st_mtime_ns += 1
        node.st_ctime_ns += 1
        self.handles[fd][2] += len(data)
        return len(data)

    def lseek(self, fd, offset, whence):
        if whence != artifacts.os.SEEK_SET:
            raise AssertionError("Unexpected seek")
        self.handles[fd][2] = offset
        return offset

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
        stack.enter_context(patch.object(artifacts.os, "geteuid", return_value=1000, create=True))
        stack.enter_context(patch.object(artifacts.os, "getegid", return_value=1000, create=True))
        self.access = stack.enter_context(patch.object(artifacts.os, "access", return_value=False))
        # Windows lacks these Linux flags; distinct simulated values are deliberate.
        for index, name in enumerate(("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK"), 20):
            stack.enter_context(patch.object(artifacts.os, name, 1 << index, create=True))
        for name, function in (
            ("open", self.open), ("fstat", self.fstat), ("stat", self.entry_stat),
            ("read", self.read), ("write", self.write), ("lseek", self.lseek),
            ("close", self.close), ("fsync", lambda fd: self.hook("fsync", self.handles[fd][0])),
        ):
            stack.enter_context(patch.object(artifacts.os, name, side_effect=function))


class SimulatedLinuxArtifactTests(unittest.TestCase):
    def setUp(self):
        self.fs = SimulatedLinux()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.fs.install(self.stack)

    def tearDown(self):
        self.assertEqual(self.fs.handles, {}, "Every descriptor must be closed")

    def reject_attestation(self):
        with self.assertRaises((artifacts.ArtifactSecurityError, OSError)):
            artifacts.read_attestation()

    def once(self, event, path, action):
        def hook(current_event, current_path):
            if (current_event, current_path) == (event, path):
                self.fs.hook = lambda event, path: None
                action()
        self.fs.hook = hook

    def test_pinned_positive_and_no_override_api(self):
        self.assertEqual(artifacts.PINNED_ATTESTATION, "/etc/polypbase/pca-production-target.json")
        self.assertEqual(str(inspect.signature(artifacts.read_attestation)), "()")
        self.assertEqual(artifacts.read_attestation(), {"target": "pinned"})
        for args, kwargs in ((("/srv/pca/plan.json",), {}), ((), {"owner": 1000}), ((), {"path": "/fake"})):
            with self.subTest(args=args, kwargs=kwargs), self.assertRaises(TypeError):
                artifacts.read_attestation(*args, **kwargs)

    def test_attestation_runtime_owned_file_rejected(self):
        self.fs.nodes[artifacts.PINNED_ATTESTATION].st_uid = 1000
        self.reject_attestation()

    def test_attestation_all_ancestors_root_only(self):
        for path in ("/", "/etc", "/etc/polypbase"):
            with self.subTest(path=path):
                self.fs.nodes[path].st_uid = 1000
                self.reject_attestation()
                self.fs.nodes[path].st_uid = 0

    def test_unsafe_ancestor_permissions_including_root(self):
        for path in ("/", "/etc", "/etc/polypbase"):
            for mode in (0o775, 0o757, 0o1777):
                with self.subTest(path=path, mode=mode):
                    self.fs.nodes[path].st_mode = stat.S_IFDIR | mode
                    self.reject_attestation()
            self.fs.nodes[path].st_mode = stat.S_IFDIR | 0o755

    def test_symlink_ancestors_and_leaf(self):
        for path in ("/", "/etc", "/etc/polypbase", artifacts.PINNED_ATTESTATION):
            with self.subTest(path=path):
                original = self.fs.nodes[path].st_mode
                self.fs.nodes[path].st_mode = stat.S_IFLNK | 0o777
                self.reject_attestation()
                self.fs.nodes[path].st_mode = original

    def test_unsafe_file_type_permissions_and_links(self):
        node = self.fs.nodes[artifacts.PINNED_ATTESTATION]
        for mode in (stat.S_IFIFO | 0o600, stat.S_IFDIR | 0o700, stat.S_IFREG | 0o664, stat.S_IFREG | 0o606):
            with self.subTest(mode=mode):
                node.st_mode = mode
                self.reject_attestation()
        node.st_mode = stat.S_IFREG | 0o644
        node.st_nlink = 2
        self.reject_attestation()

    def test_read_uses_nofollow_nonblock_and_relative_descriptors(self):
        artifacts.read_attestation()
        for path, flags, _, parent in self.fs.open_calls:
            self.assertTrue(flags & artifacts.os.O_NOFOLLOW)
            if path == artifacts.PINNED_ATTESTATION:
                self.assertTrue(flags & artifacts.os.O_NONBLOCK)
            else:
                self.assertTrue(flags & artifacts.os.O_DIRECTORY)
            self.assertEqual(parent is None, path == "/")

    def test_every_file_signature_field_mutation_detected(self):
        path = artifacts.PINNED_ATTESTATION
        for field in ("mode", "uid", "gid", "dev", "ino", "nlink", "size", "mtime_ns", "ctime_ns"):
            with self.subTest(field=field):
                node = self.fs.nodes[path]
                attr = "st_" + field
                original = getattr(node, attr)
                self.once("read", path, lambda: setattr(node, attr, original + 1))
                self.reject_attestation()
                setattr(node, attr, original)

    def test_file_replacement_during_read(self):
        path = artifacts.PINNED_ATTESTATION
        self.once("read", path, lambda: self.fs.add(path, stat.S_IFREG | 0o644, data=b'{}'))
        self.reject_attestation()

    def test_symlink_replacement_during_read(self):
        path = artifacts.PINNED_ATTESTATION
        self.once("read", path, lambda: self.fs.add(path, stat.S_IFLNK | 0o777))
        self.reject_attestation()

    def test_all_ancestor_entry_replacements_detected_at_end(self):
        for path in ("/", "/etc", "/etc/polypbase"):
            with self.subTest(path=path):
                self.once("read", artifacts.PINNED_ATTESTATION, lambda: self.fs.add(path, stat.S_IFDIR | 0o755))
                self.reject_attestation()

    def test_ancestor_metadata_mutation_detected(self):
        self.once("read", artifacts.PINNED_ATTESTATION, lambda: setattr(self.fs.nodes["/etc"], "st_mode", stat.S_IFDIR | 0o777))
        self.reject_attestation()

    def test_json_and_manifest_validation_positive(self):
        self.assertEqual(artifacts.read_json("/srv/pca/plan.json"), {"zero": 0})
        artifacts.validate_directory("/srv/pca")
        self.assertEqual(len(artifacts.validate_file("/srv/pca/plan.json")), 9)
        self.fs.nodes["/srv/pca"].st_uid = 0
        self.fs.nodes["/srv/pca/plan.json"].st_uid = 0
        self.assertEqual(artifacts.read_json("/srv/pca/plan.json"), {"zero": 0})

    def test_manifest_rejects_unrelated_owner_at_every_level(self):
        for path in ("/", "/srv", "/srv/pca", "/srv/pca/plan.json"):
            with self.subTest(path=path):
                original = self.fs.nodes[path].st_uid
                self.fs.nodes[path].st_uid = 2000
                with self.assertRaises(artifacts.ArtifactSecurityError):
                    artifacts.read_json("/srv/pca/plan.json")
                self.fs.nodes[path].st_uid = original

    def test_private_directory_requires_exact_0700(self):
        for mode in (0o750, 0o710, 0o704, 0o755, 0o770, 0o1700, 0o600):
            with self.subTest(mode=mode):
                self.fs.nodes["/srv/pca"].st_mode = stat.S_IFDIR | mode
                for function, path in ((artifacts.validate_directory, "/srv/pca"), (artifacts.validate_file, "/srv/pca/plan.json"), (artifacts.read_json, "/srv/pca/plan.json")):
                    with self.assertRaises(artifacts.ArtifactSecurityError):
                        function(path)
                with self.assertRaises(artifacts.ArtifactSecurityError):
                    artifacts.write_json("/srv/pca/new.json", {})

    def test_noncanonical_paths_rejected_before_any_filesystem_open(self):
        for path in ("relative", "", "//srv/pca", "/srv//pca", "/srv/./pca", "/srv/../pca", "/srv/pca/", "/srv/pca/..", "/srv/\x00pca", "/srv/\\pca", b"/srv/pca"):
            for function in (artifacts.read_json, artifacts.validate_file, artifacts.validate_directory):
                with self.subTest(path=path, function=function.__name__), self.assertRaises(artifacts.ArtifactSecurityError):
                    function(path)
            with self.assertRaises(artifacts.ArtifactSecurityError):
                artifacts.write_json(path, {})
        self.assertEqual(self.fs.open_calls, [])

    def test_write_exclusive_private_and_roundtrip(self):
        path = "/srv/pca/new.json"
        value = {"zero": 0, "text": "é", "missing": None}
        artifacts.write_json(path, value)
        self.assertEqual(json.loads(self.fs.nodes[path].data), value)
        self.assertEqual(stat.S_IMODE(self.fs.nodes[path].st_mode), 0o600)
        self.assertEqual(artifacts.read_json(path), value)
        flags = next(call[1] for call in self.fs.open_calls if call[0] == path)
        for flag in (artifacts.os.O_CREAT, artifacts.os.O_EXCL, artifacts.os.O_NOFOLLOW, artifacts.os.O_NONBLOCK):
            self.assertTrue(flags & flag)

    def test_write_never_overwrites_existing_file_or_symlink(self):
        for mode in (stat.S_IFREG | 0o600, stat.S_IFLNK | 0o777):
            self.fs.nodes["/srv/pca/plan.json"].st_mode = mode
            with self.assertRaises(FileExistsError):
                artifacts.write_json("/srv/pca/plan.json", {})
            self.assertEqual(self.fs.nodes["/srv/pca/plan.json"].data, b'{"zero": 0}')

    def test_missing_parent_not_created(self):
        with self.assertRaises(FileNotFoundError):
            artifacts.write_json("/srv/missing/new.json", {})
        self.assertNotIn("/srv/missing", self.fs.nodes)

    def test_write_replacement_detected_without_unlinking(self):
        path = "/srv/pca/new.json"
        self.once("write", path, lambda: self.fs.add(path, stat.S_IFREG | 0o600, uid=1000, data=b'replacement'))
        with self.assertRaises(artifacts.ArtifactSecurityError):
            artifacts.write_json(path, {})
        self.assertEqual(self.fs.nodes[path].data, b'replacement')

    def test_write_ancestor_replacement_detected(self):
        self.once("write", "/srv/pca/new.json", lambda: self.fs.add("/srv/pca", stat.S_IFDIR | 0o700, uid=1000))
        with self.assertRaises(artifacts.ArtifactSecurityError):
            artifacts.write_json("/srv/pca/new.json", {})

    def test_write_metadata_mutation_detected(self):
        path = "/srv/pca/new.json"
        self.once("write", path, lambda: setattr(self.fs.nodes[path], "st_gid", 99))
        with self.assertRaises(artifacts.ArtifactSecurityError):
            artifacts.write_json(path, {})

    def test_write_readback_detects_content_tampering(self):
        path = "/srv/pca/new.json"
        self.once("fsync", path, lambda: setattr(self.fs.nodes[path], "data", b"xxx"))
        with self.assertRaises(artifacts.ArtifactSecurityError):
            artifacts.write_json(path, {})

    def test_readback_mutation_during_write_detected(self):
        path = "/srv/pca/new.json"
        self.once("read", path, lambda: setattr(self.fs.nodes[path], "st_ctime_ns", 999))
        with self.assertRaises(artifacts.ArtifactSecurityError):
            artifacts.write_json(path, {})

    def test_parent_fsync_file_mutation_detected(self):
        path = "/srv/pca/new.json"
        self.once("fsync", "/srv/pca", lambda: setattr(self.fs.nodes[path], "st_ctime_ns", 999))
        with self.assertRaises(artifacts.ArtifactSecurityError):
            artifacts.write_json(path, {})

    def test_first_fstat_failure_closes_every_open_descriptor(self):
        for path in ("/", "/etc", "/etc/polypbase", artifacts.PINNED_ATTESTATION):
            def fail():
                raise OSError("Simulated fstat failure")
            with self.subTest(path=path):
                self.once("fstat", path, fail)
                with self.assertRaises(OSError):
                    artifacts.read_attestation()
                self.assertEqual(self.fs.handles, {})

    def test_protected_file_safe_body_holds_all_descriptors(self):
        path = "/srv/pca/plan.json"
        expected = artifacts.validate_file(path)
        with artifacts.protected_file(path) as signature:
            self.assertEqual(signature, expected)
            self.assertEqual({handle[0] for handle in self.fs.handles.values()}, {"/", "/srv", "/srv/pca", path})
            self.assertEqual(artifacts.read_json(path), {"zero": 0})
        self.assertEqual(artifacts.validate_file(path), expected)

    def test_protected_file_rejects_replaced_ancestors_with_same_file_inode(self):
        path = "/srv/pca/plan.json"
        original_file = self.fs.nodes[path]
        for ancestor, mode in (("/", 0o755), ("/srv", 0o755), ("/srv/pca", 0o700)):
            with self.subTest(ancestor=ancestor):
                with self.assertRaisesRegex(artifacts.ArtifactSecurityError, "Directory changed"):
                    with artifacts.protected_file(path) as signature:
                        self.fs.add(ancestor, stat.S_IFDIR | mode)
                        self.assertIs(self.fs.nodes[path], original_file)
                        # Separate point-in-time validation cannot detect this gap.
                        self.assertEqual(artifacts.validate_file(path), signature)

    def test_protected_file_rejects_file_replaced_during_body(self):
        path = "/srv/pca/plan.json"
        with self.assertRaisesRegex(artifacts.ArtifactSecurityError, "File changed"):
            with artifacts.protected_file(path):
                self.fs.add(path, stat.S_IFREG | 0o600, uid=1000, data=b'{"zero": 0}')

    def test_protected_file_rejects_mutation_during_body(self):
        path = "/srv/pca/plan.json"
        with self.assertRaisesRegex(artifacts.ArtifactSecurityError, "File changed"):
            with artifacts.protected_file(path):
                self.fs.nodes[path].st_mtime_ns += 1

    def test_protected_directory_safe_creation_body(self):
        with artifacts.protected_directory("/srv/pca"):
            self.assertEqual({handle[0] for handle in self.fs.handles.values()}, {"/", "/srv", "/srv/pca"})
            artifacts.write_json("/srv/pca/new.json", {"zero": 0})
            self.fs.nodes["/srv/pca"].st_mtime_ns += 1
            self.fs.nodes["/srv/pca"].st_size += 10
        self.assertEqual(artifacts.read_json("/srv/pca/new.json"), {"zero": 0})

    def test_protected_directory_rejects_replacement_during_body(self):
        for ancestor, mode in (("/", 0o755), ("/srv", 0o755), ("/srv/pca", 0o700)):
            with self.subTest(ancestor=ancestor):
                with self.assertRaisesRegex(artifacts.ArtifactSecurityError, "Directory changed"):
                    with artifacts.protected_directory("/srv/pca"):
                        self.fs.add(ancestor, stat.S_IFDIR | mode)

    def test_protected_contexts_preserve_body_errors_and_close_descriptors(self):
        for function, path in ((artifacts.protected_file, "/srv/pca/plan.json"), (artifacts.protected_directory, "/srv/pca")):
            with self.subTest(function=function.__name__):
                with self.assertRaisesRegex(RuntimeError, "caller failed"):
                    with function(path):
                        raise RuntimeError("caller failed")
                self.assertEqual(self.fs.handles, {})

    def test_protected_contexts_enforce_runtime_on_entry(self):
        for status, uid_map in (
            (STATUS.replace("NoNewPrivs:\t1", "NoNewPrivs:\t0"), "0 0 4294967295\n"),
            (STATUS, "0 1000 1\n"),
        ):
            self.fs.status = status
            self.fs.uid_map = uid_map
            for function, path in ((artifacts.protected_file, "/srv/pca/plan.json"), (artifacts.protected_directory, "/srv/pca")):
                with self.subTest(function=function.__name__, uid_map=uid_map):
                    with self.assertRaises(artifacts.ArtifactSecurityError):
                        with function(path):
                            self.fail("Unsafe runtime entered caller body")
        self.assertEqual(self.fs.open_calls, [])

    def test_protected_contexts_reject_unsafe_private_directory_before_body(self):
        self.fs.nodes["/srv/pca"].st_mode = stat.S_IFDIR | 0o750
        for function, path in ((artifacts.protected_file, "/srv/pca/plan.json"), (artifacts.protected_directory, "/srv/pca")):
            with self.subTest(function=function.__name__):
                with self.assertRaises(artifacts.ArtifactSecurityError):
                    with function(path):
                        self.fail("Unsafe directory entered caller body")

    def test_protected_file_rejects_symlink_before_body(self):
        self.fs.nodes["/srv/pca/plan.json"].st_mode = stat.S_IFLNK | 0o777
        with self.assertRaises(OSError):
            with artifacts.protected_file("/srv/pca/plan.json"):
                self.fail("Symlink entered caller body")

    def test_validate_file_detects_replacement(self):
        path = "/srv/pca/plan.json"
        self.once("stat", path, lambda: self.fs.add(path, stat.S_IFREG | 0o600, uid=1000))
        with self.assertRaises(artifacts.ArtifactSecurityError):
            artifacts.validate_file(path)

    def test_validate_directory_detects_replacement(self):
        path = "/srv/pca"
        self.once("stat", path, lambda: self.fs.add(path, stat.S_IFDIR | 0o700, uid=1000))
        with self.assertRaises(artifacts.ArtifactSecurityError):
            artifacts.validate_directory(path)

    def test_write_requires_new_file_metadata_0600(self):
        path = "/srv/pca/new.json"
        self.once("fstat", path, lambda: setattr(self.fs.nodes[path], "st_mode", stat.S_IFREG | 0o640))
        with self.assertRaises(artifacts.ArtifactSecurityError):
            artifacts.write_json(path, {})

    def test_zero_length_write_fails_closed(self):
        with patch.object(artifacts.os, "write", return_value=0):
            with self.assertRaises(artifacts.ArtifactSecurityError):
                artifacts.write_json("/srv/pca/new.json", {})

    def test_invalid_json_and_serialization_failure_close_fds(self):
        self.fs.nodes[artifacts.PINNED_ATTESTATION].data = b"invalid json"
        self.fs.nodes[artifacts.PINNED_ATTESTATION].st_size = 12
        with self.assertRaises(ValueError):
            artifacts.read_attestation()
        with self.assertRaises(ValueError):
            artifacts.write_json("/srv/pca/new.json", float("nan"))
        self.assertNotIn("/srv/pca/new.json", self.fs.nodes)

    def test_runtime_positive_and_independent_status_read(self):
        self.assertEqual(artifacts.assert_unprivileged_runtime(), 1000)
        self.fs.status = self.fs.status.replace("NoNewPrivs:\t1", "NoNewPrivs:\t0")
        with self.assertRaises(artifacts.ArtifactSecurityError):
            artifacts.assert_unprivileged_runtime()

    def test_runtime_initial_uid_map_parsed_with_kernel_whitespace(self):
        for value in ("         0          0 4294967295\n", "0\t0\t4294967295", " 0 0 4294967295 \n"):
            with self.subTest(value=value):
                self.fs.uid_map = value
                self.assertEqual(artifacts.assert_unprivileged_runtime(), 1000)

    def test_runtime_rejects_unprivileged_namespace_uid_maps(self):
        for value in ("0 1000 1\n", "1000 1000 1\n", "0 100000 65536\n", "0 0 1\n", "0 0 4294967294\n", "1 1 4294967295\n", "0 0 1000\n1000 100000 65536\n"):
            with self.subTest(value=value):
                self.fs.uid_map = value
                with self.assertRaisesRegex(artifacts.ArtifactSecurityError, "initial user namespace"):
                    artifacts.read_attestation()
        self.assertEqual(self.fs.open_calls, [])

    def test_runtime_rejects_malformed_or_multiple_uid_map_rows(self):
        for value in ("", "\n", "0 0\n", "0 0 4294967295 extra\n", "0 0 nope\n", "0 0 -1\n", "+0 0 4294967295\n", "0 0 4294967295\n0 0 4294967295\n", "0 0 4294967295\n\n"):
            with self.subTest(value=value):
                self.fs.uid_map = value
                with self.assertRaises(artifacts.ArtifactSecurityError):
                    artifacts.assert_unprivileged_runtime()

    def test_runtime_missing_uid_map_fails_closed(self):
        self.fs.uid_map = None
        with self.assertRaises(FileNotFoundError):
            artifacts.read_attestation()
        self.assertEqual(self.fs.open_calls, [])

    def test_runtime_uid_map_read_independently_each_time(self):
        self.assertEqual(artifacts.assert_unprivileged_runtime(), 1000)
        self.fs.uid_map = "0 1000 1\n"
        with self.assertRaises(artifacts.ArtifactSecurityError):
            artifacts.assert_unprivileged_runtime()

    def test_all_public_helpers_reject_user_namespace_without_env_bypass(self):
        self.fs.uid_map = "0 1000 1\n"
        with patch.dict(artifacts.os.environ, {"PCA_SKIP_SECURITY": "1", "PCA_UID_MAP": "0 0 4294967295"}):
            for function, args in (
                (artifacts.read_attestation, ()), (artifacts.read_json, ("/srv/pca/plan.json",)),
                (artifacts.validate_file, ("/srv/pca/plan.json",)), (artifacts.validate_directory, ("/srv/pca",)),
                (artifacts.write_json, ("/srv/pca/new.json", {})),
            ):
                with self.subTest(function=function.__name__), self.assertRaises(artifacts.ArtifactSecurityError):
                    function(*args)
        self.assertEqual(self.fs.open_calls, [])

    def test_runtime_requires_linux(self):
        with patch.object(artifacts.sys, "platform", "win32"), self.assertRaises(artifacts.ArtifactSecurityError):
            artifacts.assert_unprivileged_runtime()

    def test_runtime_requires_four_equal_nonzero_uids(self):
        for value in ("0 0 0 0", "1000 0 1000 1000", "1000 1000 0 1000", "1000 1000 1000 0", "1000 1000 1000", "2000 2000 2000 2000", "x x x x"):
            with self.subTest(value=value):
                self.fs.status = STATUS.replace("Uid:\t1000 1000 1000 1000", "Uid:\t" + value)
                with self.assertRaises(artifacts.ArtifactSecurityError):
                    artifacts.assert_unprivileged_runtime()

    def test_runtime_requires_four_equal_decimal_gids(self):
        for value in (
            "", "1000 1000 1000", "1000 1000 1000 1000 1000", "x x x x",
            "-1 -1 -1 -1", "+1000 +1000 +1000 +1000", "1000.0 1000 1000 1000",
            "110 1000 1000 1000", "1000 110 1000 1000",
            "1000 1000 110 1000", "1000 1000 1000 110",
        ):
            with self.subTest(gids=value):
                self.fs.status = STATUS.replace("Gid:\t1000 1000 1000 1000", "Gid:\t" + value)
                with self.assertRaisesRegex(artifacts.ArtifactSecurityError, "equal GIDs"):
                    artifacts.read_attestation()
                self.assertEqual(self.fs.open_calls, [])

    def test_runtime_gids_must_match_effective_gid(self):
        with patch.object(artifacts.os, "getegid", return_value=110):
            with self.assertRaisesRegex(artifacts.ArtifactSecurityError, "equal GIDs"):
                artifacts.read_attestation()
            self.assertEqual(self.fs.open_calls, [])
            self.fs.status = STATUS.replace("Gid:\t1000 1000 1000 1000", "Gid:\t110 110 110 110")
            self.assertEqual(artifacts.assert_unprivileged_runtime(), 1000)

    def test_runtime_rejects_each_capability(self):
        for field in ("CapInh", "CapPrm", "CapEff", "CapAmb"):
            for value in ("0000000000000001", "8000000000000000", "garbage", "-0", "0 0"):
                with self.subTest(field=field, value=value):
                    self.fs.status = STATUS.replace(field + ":\t0000000000000000", field + ":\t" + value)
                    with self.assertRaises(artifacts.ArtifactSecurityError):
                        artifacts.assert_unprivileged_runtime()

    def test_runtime_missing_or_duplicate_required_fields(self):
        for field in ("Uid", "Gid", "CapInh", "CapPrm", "CapEff", "CapAmb", "NoNewPrivs"):
            with self.subTest(field=field):
                self.fs.status = "\n".join(line for line in STATUS.splitlines() if not line.startswith(field + ":"))
                with self.assertRaises(artifacts.ArtifactSecurityError):
                    artifacts.assert_unprivileged_runtime()
                self.fs.status = STATUS + next(line for line in STATUS.splitlines() if line.startswith(field + ":")) + "\n"
                with self.assertRaises(artifacts.ArtifactSecurityError):
                    artifacts.assert_unprivileged_runtime()

    def test_runtime_no_new_privileges_exactly_one(self):
        for value in ("0", "", "2", "1 1", "01"):
            self.fs.status = STATUS.replace("NoNewPrivs:\t1", "NoNewPrivs:\t" + value)
            with self.assertRaises(artifacts.ArtifactSecurityError):
                artifacts.assert_unprivileged_runtime()

    def test_proc_failure_fails_closed_before_artifact_access(self):
        with patch("builtins.open", side_effect=PermissionError("proc unavailable")), self.assertRaises(PermissionError):
            artifacts.read_attestation()
        self.assertEqual(self.fs.open_calls, [])

    def test_all_public_helpers_enforce_runtime_without_env_bypass(self):
        self.fs.status = STATUS.replace("NoNewPrivs:\t1", "NoNewPrivs:\t0")
        with patch.dict(artifacts.os.environ, {"PCA_SKIP_SECURITY": "1", "PCA_ATTESTATION_PATH": "/srv/pca/plan.json"}):
            for function, args in (
                (artifacts.read_attestation, ()), (artifacts.read_json, ("/srv/pca/plan.json",)),
                (artifacts.validate_file, ("/srv/pca/plan.json",)), (artifacts.validate_directory, ("/srv/pca",)),
                (artifacts.write_json, ("/srv/pca/new.json", {})),
            ):
                with self.subTest(function=function.__name__), self.assertRaises(artifacts.ArtifactSecurityError):
                    function(*args)
        self.assertEqual(self.fs.open_calls, [])


class SimulatedLinuxPostgresqlSocketTests(unittest.TestCase):
    def setUp(self):
        self.fs = SimulatedLinux()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.fs.install(self.stack)
        for name, value in (("getgid", 1000), ("getegid", 1000), ("getgroups", [1000, 1001])):
            self.stack.enter_context(patch.object(artifacts.os, name, return_value=value, create=True))
        self.readlink = self.stack.enter_context(patch.object(
            artifacts.os, "readlink", side_effect=self.read_alias, create=True,
        ))
        self.fs.add("/run", stat.S_IFDIR | 0o755)
        self.fs.add("/var", stat.S_IFDIR | 0o755)
        self.fs.add("/var/run", stat.S_IFLNK | 0o777, data=b"/run")
        self.directory = self.fs.add("/run/postgresql", stat.S_IFDIR | 0o2775, uid=105, gid=110)
        self.socket_path = "/run/postgresql/.s.PGSQL.5432"
        self.socket = self.fs.add(self.socket_path, stat.S_IFSOCK | 0o777, uid=105, gid=110)

    def tearDown(self):
        self.assertEqual(self.fs.handles, {}, "Every descriptor must be closed")

    def read_alias(self, name, *, dir_fd):
        self.assertEqual(name, "run")
        self.assertEqual(self.fs.handles[dir_fd][0], "/var")
        return self.fs.nodes[self.fs.path(name, dir_fd)].data.decode("ascii")

    def endpoint(self, host="/run/postgresql", port="5432", owner_uid=105, owner_gid=110):
        return artifacts.protected_postgresql_socket(host, port, owner_uid, owner_gid)

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
                        ".", artifacts.os.W_OK, dir_fd=directory_fd, effective_ids=True,
                    )
                    paths = [handle[0] for handle in self.fs.handles.values()]
                    self.assertCountEqual(paths, (
                        ["/", "/run", "/run/postgresql"] if host == "/run/postgresql"
                        else ["/", "/var", "/", "/run", "/run/postgresql"]
                    ))
                self.assertEqual(self.fs.handles, {})
                for path, flags, _, parent in self.fs.open_calls:
                    for flag in (artifacts.os.O_DIRECTORY, artifacts.os.O_NOFOLLOW, artifacts.os.O_CLOEXEC):
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
        for port in (None, 5432, True, "", "0", "65536", "-1", "+5432", " 5432", "5432/other", "５４３２"):
            with self.subTest(port=port):
                self.reject_endpoint(port=port)
        for field in ("owner_uid", "owner_gid"):
            for value in (None, 0, -1, True, 105.0, "105"):
                with self.subTest(field=field, value=value):
                    self.reject_endpoint(**{field: value})
        self.assertEqual(self.fs.open_calls, [])

    def test_runtime_owner_and_each_group_membership_rejected_before_open(self):
        self.directory.st_uid = self.socket.st_uid = 1000
        self.reject_endpoint(owner_uid=1000)
        self.directory.st_uid = self.socket.st_uid = 105
        for name, value in (("getgid", 110), ("getegid", 110), ("getgroups", [1000, 110])):
            with self.subTest(identity=name):
                self.fs.status = STATUS
                if name == "getegid":
                    self.fs.status = STATUS.replace("Gid:\t1000 1000 1000 1000", "Gid:\t110 110 110 110")
                with patch.object(artifacts.os, name, return_value=value):
                    self.reject_endpoint()
        self.assertEqual(self.fs.open_calls, [])

    def test_acl_write_grant_rejected_before_body_and_descriptors_closed(self):
        def writable(name, mode, *, dir_fd, effective_ids):
            self.assertEqual(name, ".")
            self.assertEqual(mode, artifacts.os.W_OK)
            self.assertIs(effective_ids, True)
            self.assertIs(self.fs.handles[dir_fd][1], self.directory)
            return True

        self.fs.access.side_effect = writable
        for host in ("/run/postgresql", "/var/run/postgresql"):
            with self.subTest(host=host):
                self.fs.access.reset_mock()
                with self.assertRaisesRegex(artifacts.ArtifactSecurityError, "must not be able to write"):
                    with self.endpoint(host=host):
                        self.fail("ACL-writable directory entered caller body")
                self.fs.access.assert_called_once()
                self.assertEqual(self.fs.handles, {})

    def test_runtime_security_checked_before_open(self):
        for status, uid_map in (
            (STATUS.replace("NoNewPrivs:\t1", "NoNewPrivs:\t0"), "0 0 4294967295\n"),
            (STATUS, "0 1000 1\n"),
        ):
            with self.subTest(status=status, uid_map=uid_map):
                self.fs.status, self.fs.uid_map = status, uid_map
                self.reject_endpoint()
        self.assertEqual(self.fs.open_calls, [])

    def test_ancestors_require_root_owner_and_no_group_or_world_write(self):
        for path in ("/", "/run", "/var"):
            node = self.fs.nodes[path]
            host = "/var/run/postgresql" if path == "/var" else "/run/postgresql"
            for field, value in (("st_uid", 1000), ("st_mode", stat.S_IFDIR | 0o775), ("st_mode", stat.S_IFDIR | 0o1777)):
                with self.subTest(path=path, field=field, value=value):
                    original = getattr(node, field)
                    setattr(node, field, value)
                    self.reject_endpoint(host=host)
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
        for path in ("/run", "/run/postgresql", self.socket_path):
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
                self.reject_endpoint(host="/var/run/postgresql")
                setattr(alias, field, original)

    def test_socket_and_directory_name_replacements_rejected_on_exit(self):
        for path in (self.socket_path, "/run/postgresql", "/run", "/var"):
            for symlink in (False, True):
                with self.subTest(path=path, symlink=symlink):
                    original = self.fs.nodes[path]
                    with self.assertRaises(artifacts.ArtifactSecurityError):
                        with self.endpoint(host="/var/run/postgresql"):
                            self.fs.add(
                                path, stat.S_IFLNK | 0o777 if symlink else original.st_mode,
                                uid=original.st_uid, gid=original.st_gid,
                            )
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
            with self.endpoint(host="/var/run/postgresql"):
                self.fs.add("/var/run", alias.st_mode, data=b"/run")
        self.assertEqual(self.fs.handles, {})
        self.fs.nodes["/var/run"] = alias
        with self.assertRaisesRegex(artifacts.ArtifactSecurityError, "alias changed"):
            with self.endpoint(host="/var/run/postgresql"):
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
                    with self.endpoint(host="/var/run/postgresql"):
                        self.fail("Failed fstat entered caller body")
                self.assertEqual(self.fs.handles, {})
        self.fs.hook = lambda event, path: None

    def test_body_errors_preserved_and_all_descriptors_closed(self):
        for host in ("/run/postgresql", "/var/run/postgresql"):
            with self.subTest(host=host):
                with self.assertRaisesRegex(RuntimeError, "caller failed"):
                    with self.endpoint(host=host):
                        self.directory.st_mtime_ns += 1
                        raise RuntimeError("caller failed")
                self.assertEqual(self.fs.handles, {})


if __name__ == "__main__":
    unittest.main()
