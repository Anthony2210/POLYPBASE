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

    def add(self, path, mode, uid=0, data=b""):
        node = SimpleNamespace(
            st_mode=mode, st_uid=uid, st_gid=0, st_dev=1,
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
                self.fs.status = STATUS.replace("1000 1000 1000 1000", value)
                with self.assertRaises(artifacts.ArtifactSecurityError):
                    artifacts.assert_unprivileged_runtime()

    def test_runtime_rejects_each_capability(self):
        for field in ("CapInh", "CapPrm", "CapEff", "CapAmb"):
            for value in ("0000000000000001", "8000000000000000", "garbage", "-0", "0 0"):
                with self.subTest(field=field, value=value):
                    self.fs.status = STATUS.replace(field + ":\t0000000000000000", field + ":\t" + value)
                    with self.assertRaises(artifacts.ArtifactSecurityError):
                        artifacts.assert_unprivileged_runtime()

    def test_runtime_missing_or_duplicate_required_fields(self):
        for field in ("Uid", "CapInh", "CapPrm", "CapEff", "CapAmb", "NoNewPrivs"):
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


if __name__ == "__main__":
    unittest.main()
