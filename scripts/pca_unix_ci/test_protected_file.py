"""Native Linux protected-file proofs in the existing network-none CI container.

No Django, database credentials, archive subprocesses or filesystem mocks. Only
synthetic files beneath the private runtime-owned CI directory are modified.
"""

from contextlib import ExitStack, closing
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from config import pca_protected_artifacts as artifacts


@unittest.skipUnless(sys.platform == "linux", "Requires native Linux filesystem semantics")
class NativeProtectedFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if (ROOT != Path("/opt/pca") or Path.cwd() != ROOT
                or not Path("/.dockerenv").is_file()
                or set(os.listdir("/sys/class/net")) != {"lo"}
                or os.getuid() != 10001 or os.geteuid() != 10001):
            raise RuntimeError("Use the dedicated network-none CI container, not a host checkout")
        # Exercise the real procfs UID/capability/NoNewPrivs checks, never a stub.
        if artifacts.assert_unprivileged_runtime() != 10001:
            raise RuntimeError("Expected the dedicated unprivileged CI runtime")
        artifacts.validate_directory("/opt/pca/.local-docs")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native-file-", dir=ROOT / ".local-docs")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.branch = self.directory / "branch"
        self.branch.mkdir(mode=0o700)
        self.archive = self.branch / "backup.dump"
        self.archive.write_bytes(b"synthetic archive contents")
        self.archive.chmod(0o600)
        self.descriptor_count = len(os.listdir("/proc/self/fd"))

    def tearDown(self):
        self.assertEqual(len(os.listdir("/proc/self/fd")), self.descriptor_count,
                         "File and ancestor descriptors must close even on refusal")
        super().tearDown()

    def test_unchanged_archive_remains_pinned_through_final_check(self):
        expected = artifacts._signature(self.archive.stat())
        with artifacts.protected_file(str(self.archive)) as signature:
            self.assertEqual(signature, expected)
            self.assertEqual(hashlib.sha256(self.archive.read_bytes()).digest(),
                             hashlib.sha256(b"synthetic archive contents").digest())
            self.assertGreater(len(os.listdir("/proc/self/fd")), self.descriptor_count)
        self.assertEqual(artifacts._signature(self.archive.stat()), expected)

    def test_same_bytes_path_replacement_is_rejected(self):
        replacement = self.branch / "replacement.dump"
        replacement.write_bytes(self.archive.read_bytes())
        replacement.chmod(0o600)
        with self.assertRaises(artifacts.ArtifactSecurityError):
            with artifacts.protected_file(str(self.archive)):
                os.replace(replacement, self.archive)

    def test_rewrite_with_content_size_and_mtime_restored_is_rejected_by_ctime(self):
        original = self.archive.stat()
        content = self.archive.read_bytes()
        with self.assertRaisesRegex(artifacts.ArtifactSecurityError, "File changed"):
            with artifacts.protected_file(str(self.archive)):
                time.sleep(0.01)
                self.archive.write_bytes(b"x" * len(content))
                self.archive.write_bytes(content)
                os.utime(self.archive, ns=(original.st_atime_ns, original.st_mtime_ns))
                current = self.archive.stat()
                self.assertEqual(current.st_size, original.st_size)
                self.assertEqual(current.st_mtime_ns, original.st_mtime_ns)
                self.assertEqual(self.archive.read_bytes(), content)
                self.assertNotEqual(current.st_ctime_ns, original.st_ctime_ns)

    def test_replaced_ancestor_is_rejected_even_with_unchanged_open_file(self):
        original = artifacts._signature(self.archive.stat())
        with self.assertRaisesRegex(artifacts.ArtifactSecurityError, "Directory changed"):
            with artifacts.protected_file(str(self.archive)):
                moved = self.directory / "detached"
                self.branch.rename(moved)
                self.branch.mkdir(mode=0o700)
                self.assertEqual(artifacts._signature((moved / "backup.dump").stat()), original)

    def test_changed_ancestor_permissions_are_rejected(self):
        with self.assertRaisesRegex(artifacts.ArtifactSecurityError, "Directory changed"):
            with artifacts.protected_file(str(self.archive)):
                self.branch.chmod(0o750)

    def test_symlink_is_rejected_without_following_it(self):
        alias = self.branch / "alias.dump"
        alias.symlink_to(self.archive)
        with self.assertRaises(OSError):
            with artifacts.protected_file(str(alias)):
                self.fail("A symlink must never enter the guarded body")

    def test_guard_failure_rolls_back_before_commit_with_real_filesystem(self):
        # Match the M4 ExitStack ordering with a network-free in-memory SQL DB.
        with closing(sqlite3.connect(":memory:")) as database:
            database.execute("CREATE TABLE scientific_state (value INTEGER)")
            database.execute("INSERT INTO scientific_state VALUES (0)")
            database.commit()
            with self.assertRaises(artifacts.ArtifactSecurityError):
                with ExitStack() as guards:
                    guards.enter_context(artifacts.protected_file(str(self.archive)))
                    hashlib.sha256(self.archive.read_bytes()).hexdigest()
                    with database, guards:
                        database.execute("UPDATE scientific_state SET value = 1")
                        self.archive.write_bytes(b"changed after verification")
            self.assertEqual(database.execute("SELECT value FROM scientific_state").fetchall(), [(0,)])


if __name__ == "__main__":
    if sys.platform != "linux":
        raise SystemExit("Native Linux validation is required; a skipped suite is not CI evidence")
    unittest.main(verbosity=2)
