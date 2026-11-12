"""Authenticated bundle source tests: disposable local Git repositories only."""

import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/export_migration_control.py"
spec = importlib.util.spec_from_file_location("migration_control_export", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "app"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Disposable test")
        self.git("config", "user.email", "test@example.invalid")
        (self.repo / "application").write_bytes(b"prepared release 8504f52 analogue\n")
        self.commit("Prepared release")
        self.prepared = self.git("rev-parse", "HEAD").decode().strip()
        for name in module.FILES:
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((name + "\n").encode())
        self.commit("Reviewed controls")
        self.reviewed = self.git("rev-parse", "HEAD").decode().strip()
        self.git("checkout", "--detach", self.prepared)

    def git(self, *args):
        return subprocess.check_output(["git", "--no-pager", "-C", str(self.repo), *args],
                                       stderr=subprocess.DEVNULL, timeout=10)

    def commit(self, message):
        self.git("add", ".")
        self.git("-c", "core.hooksPath=/dev/null", "commit", "-m", message)

    def export(self, commit=None, destination=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return module.export(self.repo, commit or self.reviewed, destination or self.root / "bundle")

    def test_exact_reviewed_blobs_without_advancing_partial_release(self):
        before = self.git("status", "--porcelain")
        digest = self.export()
        bundle = self.root / "bundle"
        manifest = (bundle / "SHA256SUMS").read_bytes()
        self.assertEqual(hashlib.sha256(manifest).hexdigest(), digest)
        self.assertEqual((bundle / "CONTROL_COMMIT").read_text().strip(), self.reviewed)
        for name in module.FILES:
            self.assertEqual((bundle / name).read_bytes(), self.git("show", f"{self.reviewed}:{name}"))
        self.assertEqual(self.git("rev-parse", "HEAD").decode().strip(), self.prepared)
        self.assertEqual(self.git("status", "--porcelain"), before)

    def test_untracked_runner_cannot_be_delivered(self):
        target = self.repo / module.FILES[0]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"untracked control\n")
        with self.assertRaises(ValueError):
            self.export(commit=self.prepared)
        self.assertFalse((self.root / "bundle").exists())

    def test_source_and_manifest_tampering_break_pins(self):
        digest = self.export()
        bundle = self.root / "bundle"
        path = bundle / module.FILES[0]
        original = hashlib.sha256(path.read_bytes()).hexdigest()
        path.write_bytes(b"tampered\n")
        self.assertNotEqual(hashlib.sha256(path.read_bytes()).hexdigest(), original)
        manifest = bundle / "SHA256SUMS"
        manifest.write_bytes(manifest.read_bytes().replace(original.encode(), b"0" * 64))
        self.assertNotEqual(hashlib.sha256(manifest.read_bytes()).hexdigest(), digest)

    def test_retry_never_overwrites_bundle_and_commit_must_be_exact(self):
        self.export()
        with self.assertRaises(FileExistsError):
            self.export()
        for commit in ("main", self.reviewed[:8], "A" * 40, self.reviewed + "\n"):
            with self.subTest(commit=commit), self.assertRaises(ValueError):
                self.export(commit=commit, destination=self.root / "other")


if __name__ == "__main__":
    unittest.main()
