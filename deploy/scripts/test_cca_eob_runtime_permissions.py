"""Offline permission-policy tests; no ACLs, service accounts or databases changed."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess
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
            {"path": "/usr/lib/python3.13/legacy.pyc"},
            {"path": "/usr/lib/python313.zip"},
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

    def test_operator_candidate_data_check_accepts_exact_sources_and_rejects_drift(self):
        root = Path(__file__).resolve().parents[2]
        procedure = (root / 'docs/cca-eob-runtime-manifest-procedure.md').read_text(encoding='utf-8')
        code = procedure.split('# CCA_EOB_CANDIDATE_DATA_CHECK\n', 1)[1].split('\nPY\n', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source, clone, evidence = work / 'source', work / 'clone', work / 'evidence'
            for location in (source, clone):
                (location / 'backend').mkdir(parents=True)
                (location / 'backend/manage.py').write_bytes(b'# synthetic source\n')
                (location / 'backend/cca_eob_runtime.py').write_bytes((root / 'backend/cca_eob_runtime.py').read_bytes())
            evidence.mkdir()
            target = 'f' * 40
            trace = {'schema': 'cca_eob_runtime_trace_v2', 'runtime': POLICY.RUNTIME,
                     'app': self.manifest()['app'], 'uid': 998, 'no_new_privs': True,
                     'readable_bytecode': [], 'deny_read': {self.manifest()['deny_read'][0]: True},
                     'startup': {'contract': 'cca_eob_source_only_v1', 'isolated': 1, 'no_site': 1,
                                 'dont_write_bytecode': True, 'pycache_prefix': '/dev/null',
                                 'site_initialized': True, 'implementation': 'cpython', 'version': [3, 13]}}
            trace_hash = hashlib.sha256(json.dumps(trace, sort_keys=True).encode()).hexdigest()
            inventory = {'files': self.manifest()['files'][1:], 'deny_read': sorted(trace['deny_read']),
                         'scope_review': f'synthetic target={target} | trace_sha256={trace_hash}'}
            with patch.object(POLICY, 'tracked_sources', return_value=['backend/manage.py']):
                bridge = POLICY.build(clone, self.manifest()['app'], inventory)
            with patch.object(POLICY, 'tracked_sources', return_value=['backend/manage.py', 'backend/cca_eob_runtime.py']):
                candidate = POLICY.build(clone, self.manifest()['app'], inventory)
            # Separate serialized artifacts must not share build's in-memory entries.
            baseline = json.loads(json.dumps({'trace-v2.json': trace, 'inventory.json': inventory,
                                              'bridge.json': bridge, 'candidate.json': candidate}))

            def verify(documents):
                for name, document in documents.items():
                    (evidence / name).write_text(json.dumps(document), encoding='utf-8')
                return subprocess.run([sys.executable, '-I', '-S', '-B', '-X', 'pycache_prefix=/dev/null',
                                       '-c', code, str(source), str(evidence), target, str(clone)],
                                      text=True, capture_output=True, timeout=30)

            result = verify(baseline)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('CCA_EOB_CANDIDATE_DATA_OK', result.stdout)
            for mutation in ('source_hash', 'dependency_hash', 'private_probe', 'startup', 'trace_hash', 'duplicate'):
                documents = copy.deepcopy(baseline)
                if mutation == 'source_hash':
                    documents['candidate.json']['files'][0]['sha256'] = '0' * 64
                elif mutation == 'dependency_hash':
                    documents['candidate.json']['files'][-1]['sha256'] = '0' * 64
                elif mutation == 'private_probe':
                    documents['candidate.json']['deny_read'] = ['/different/private']
                elif mutation == 'startup':
                    documents['trace-v2.json']['startup']['pycache_prefix'] = None
                elif mutation == 'trace_hash':
                    documents['inventory.json']['scope_review'] = f'target={target} trace_sha256=wrong'
                else:
                    documents['candidate.json']['files'].append(copy.deepcopy(candidate['files'][0]))
                with self.subTest(mutation=mutation):
                    self.assertNotEqual(verify(documents).returncode, 0)
            (clone / 'backend/manage.py').write_bytes(b'# changed clone\n')
            self.assertNotEqual(verify(baseline).returncode, 0)

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

    def check_with_owner(self, owned_path=None, *, executable=False, tamper=False,
                         private_read=False, writable=False, target_inventory=False):
        """Exercise the full gate with read-only POSIX metadata, also on Windows."""
        manifest = self.manifest()
        manifest["files"].append({
            "path": "/srv/polypbase/app/backend/resource.json", "sha256": "a" * 64,
            "access": "r", "origin": "resource"})
        if target_inventory:
            manifest['files'].append({'path': manifest['app'] + '/backend/cca_eob_runtime.py',
                                      'sha256': 'a' * 64, 'access': 'r', 'origin': 'tracked_code'})
        paths = {entry["path"] for entry in manifest["files"]}
        uid = 1001

        class InventoryPath(PurePosixPath):
            def lstat(self):
                is_file = str(self) in paths or str(self) in manifest["deny_read"]
                mode = (stat.S_IFREG if is_file else stat.S_IFDIR)
                mode |= (0o500 if executable else 0o400) if is_file else 0o500
                return SimpleNamespace(st_mode=mode, st_uid=uid if str(self) == owned_path else 0)

            def read_bytes(self):
                return b"tampered" if tamper else b"synthetic reviewed file"

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
                             ({"r"} if private_read else set()) if str(path) in manifest["deny_read"]
                             else set("rwx" if writable else "rx")):
            POLICY.check(PurePosixPath(manifest["app"]), manifest)

    def test_target_manifest_cannot_validate_the_old_live_inventory(self):
        with self.assertRaisesRegex(ValueError, 'Tracked Python code inventory has drifted'):
            self.check_with_owner(target_inventory=True)
        self.check_with_owner()

    def test_gate_still_refuses_hash_drift_private_reads_and_runtime_writes(self):
        for options, message in (({"tamper": True}, "content drift"),
                                 ({"private_read": True}, "private denial probe"),
                                 ({"writable": True}, "permissions differ")):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, message):
                self.check_with_owner(**options)

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
