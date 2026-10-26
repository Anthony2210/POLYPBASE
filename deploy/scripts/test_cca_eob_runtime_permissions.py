"""Offline permission-policy tests; no ACLs, service accounts or databases changed."""

import copy
import hashlib
import importlib.util
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "cca_eob_runtime_permissions", Path(__file__).with_name("cca_eob_runtime_permissions.py"))
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)


class RuntimeManifestTests(unittest.TestCase):
    def manifest(self):
        files = [{"path": path, "sha256": "a" * 64, "access": access, "origin": origin}
                 for path, access, origin in (
                     ("/srv/polypbase/app/backend/manage.py", "r", "tracked_code"),
                     ("/usr/bin/python3.13", "rx", "interpreter"),
                     ("/usr/lib/python3.13/os.py", "r", "stdlib"),
                     ("/srv/polypbase/app/.venv/lib/python3.13/site-packages/django/__init__.py", "r", "dependency"))]
        return {"schema": POLICY.SCHEMA, "runtime": POLICY.RUNTIME, "app": "/srv/polypbase/app",
                "scope_review": "synthetic offline closure review", "files": files,
                "deny_read": ["/srv/polypbase/app/backend/.env"]}

    def test_requires_exact_dependency_inventory_and_explicit_denial_probes(self):
        POLICY.validate(self.manifest())
        for origin in ("interpreter", "stdlib", "dependency"):
            manifest = self.manifest()
            manifest["files"] = [entry for entry in manifest["files"] if entry["origin"] != origin]
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                POLICY.validate(manifest)
        for field, value in (("deny_read", []), ("scope_review", ""), ("runtime", "root")):
            manifest = self.manifest()
            manifest[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                POLICY.validate(manifest)

    def test_rejects_private_paths_writes_aliases_unknown_scope_and_duplicates(self):
        for changes in (
            {"path": "/srv/polypbase/app/backend/.env"},
            {"path": "/srv/polypbase/app/.git/config"},
            {"path": "/srv/polypbase/app/backend/../private.py"},
            {"path": "/srv/polypbase/app/backend//settings.py"},
            {"path": "/etc/private.py"}, {"access": "rwx"}, {"origin": "all"},
            {"sha256": "bad"}, {"extra": "unexpected"},
        ):
            manifest = self.manifest()
            manifest["files"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                POLICY.validate(manifest)
        manifest = self.manifest()
        manifest["files"].append(copy.deepcopy(manifest["files"][0]))
        with self.assertRaises(ValueError):
            POLICY.validate(manifest)

    def test_build_hashes_only_tracked_code_and_requires_complete_external_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "backend").mkdir()
            (root / "backend/manage.py").write_bytes(b"# synthetic\n")
            inventory = {"files": self.manifest()["files"][1:],
                         "deny_read": self.manifest()["deny_read"], "scope_review": "offline synthetic"}
            with patch.object(POLICY, "tracked_sources", return_value=["backend/manage.py"]):
                manifest = POLICY.build(root, "/srv/polypbase/app", inventory)
            self.assertEqual(manifest["files"][0]["sha256"], hashlib.sha256(b"# synthetic\n").hexdigest())

    def test_acl_evaluator_respects_named_user_mask_and_matching_group_precedence(self):
        acl = "user::rw-\nuser:1001:r-x\ngroup::r--\ngroup:1002:rw-\nmask::r--\nother::rwx\n"
        file = SimpleNamespace(lstat=lambda: SimpleNamespace(st_mode=0o100640, st_uid=1000, st_gid=1002))
        with patch.object(POLICY.subprocess, "run", return_value=SimpleNamespace(stdout=acl)):
            self.assertEqual(POLICY.acl_permissions(file, 1001, {1002}), {"r"})
            self.assertEqual(POLICY.acl_permissions(file, 1003, {1002}), {"r"})
            self.assertEqual(POLICY.acl_permissions(file, 1003, {1004}), set("rwx"))
            self.assertEqual(POLICY.acl_permissions(file, 1000, set()), set("rw"))
        with patch.object(POLICY.subprocess, "run", return_value=SimpleNamespace(stdout=acl + "default:user::rwx\n")):
            with self.assertRaises(ValueError):
                POLICY.acl_permissions(file, 1001, {1002})

    def check_with_owner(self, owned_path=None, *, executable=False):
        """Exercise the full gate with read-only POSIX metadata, also on Windows."""
        manifest = self.manifest()
        manifest["files"].append({
            "path": "/srv/polypbase/app/backend/resource.json", "sha256": "a" * 64,
            "access": "r", "origin": "resource"})
        paths = {entry["path"] for entry in manifest["files"]}
        uid = 1001

        class InventoryPath(PurePosixPath):
            def lstat(self):
                is_file = str(self) in paths or str(self) in manifest["deny_read"]
                mode = (stat.S_IFREG if is_file else stat.S_IFDIR)
                mode |= (0o500 if executable else 0o400) if is_file else 0o500
                return SimpleNamespace(st_mode=mode, st_uid=uid if str(self) == owned_path else 0)

            def read_bytes(self):
                return b"synthetic reviewed file"

            def exists(self):
                return True

            def is_symlink(self):
                return False

        digest = hashlib.sha256(b"synthetic reviewed file").hexdigest()
        for entry in manifest["files"]:
            entry["sha256"] = digest
        account = SimpleNamespace(pw_uid=uid, pw_gid=1002)
        with patch.dict(sys.modules, {"pwd": SimpleNamespace(getpwnam=lambda name: account)}), \
                patch.object(POLICY.os, "getgrouplist", return_value=[1002], create=True), \
                patch.object(POLICY, "Path", InventoryPath), \
                patch.object(POLICY, "tracked_sources", return_value=["backend/manage.py"]), \
                patch.object(POLICY, "acl_permissions", side_effect=lambda path, *_:
                             set() if str(path) in manifest["deny_read"] else set("rx")):
            POLICY.check(PurePosixPath(manifest["app"]), manifest)

    def test_gate_accepts_read_only_inventory_owned_by_another_uid(self):
        self.check_with_owner()

    def test_gate_refuses_runtime_owned_files_even_without_write_permission(self):
        files = [(entry["path"], entry["origin"]) for entry in self.manifest()["files"]]
        files.append(("/srv/polypbase/app/backend/resource.json", "resource"))
        for path, origin in files:
            for executable in (False, True):
                with self.subTest(origin=origin, mode="0500" if executable else "0400"):
                    with self.assertRaisesRegex(ValueError, "Runtime must not own an inventoried file"):
                        self.check_with_owner(path, executable=executable)

    def test_gate_refuses_every_runtime_owned_ancestor_even_mode_0500(self):
        ancestors = {str(parent) for entry in self.manifest()["files"]
                     for parent in PurePosixPath(entry["path"]).parents}
        for ancestor in sorted(ancestors):
            with self.subTest(ancestor=ancestor):
                with self.assertRaisesRegex(ValueError, "Runtime must not own an inventoried ancestor"):
                    self.check_with_owner(ancestor)

    def test_deploy_checks_after_sync_without_automatic_acl_mutation(self):
        source = Path(__file__).with_name("deploy_release.sh").read_text(encoding="utf-8")
        gate = source.index('CURRENT_STEP="CCA-EOB runtime read permissions"')
        self.assertLess(source.index("uv sync --frozen"), gate)
        self.assertLess(gate, source.index('CURRENT_STEP="Django validation"'))
        self.assertIn('check --root "$APP_DIR" --manifest "$CCA_RUNTIME_MANIFEST"', source)
        self.assertNotIn("setfacl", source)


if __name__ == "__main__":
    unittest.main()
