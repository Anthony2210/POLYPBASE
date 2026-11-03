"""Offline permission-policy tests; no ACLs, service accounts or databases changed."""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
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

APP = "/srv/polypbase/app"
RESOURCE = "backend/locale/fr/LC_MESSAGES/django.mo"
APP_RESOURCE = f"{APP}/{RESOURCE}"
EXTERNAL = ("dependency", "interpreter", "stdlib")
# Synthetic binary catalogs: LIVE and TARGET intentionally differ.
LIVE_MO = b"\xde\x12\x04\x95\0live catalog"
TARGET_MO = b"\xde\x12\x04\x95\0target catalog"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(root, *args):
    return subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=root, check=True,
                          capture_output=True, text=True, timeout=30).stdout


def git_add(root, relative, data):
    (root / relative).parent.mkdir(parents=True, exist_ok=True)
    (root / relative).write_bytes(data)
    git(root, "add", "--", relative)


def git_root(root, files):
    """Real Git working tree whose index tracks exactly ``files`` (no commit needed)."""
    root.mkdir(parents=True)
    git(root, "init", "--quiet")
    for relative, data in files.items():
        git_add(root, relative, data)
    return root


def plain_tree(root, files):
    """Authenticated bundle extracted as plain data, without Git metadata."""
    for relative, data in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(data)
    return root


def link(target, path, test, *, directory=False):
    """Create a real alias; Windows directories use a privilege-free junction."""
    try:
        if directory and sys.platform == "win32":
            import _winapi
            _winapi.CreateJunction(str(target), str(path))
        else:
            os.symlink(target, path, target_is_directory=directory)
    except OSError as error:
        test.skipTest(f"Cannot create a real symlink here: {error}")


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

    def test_build_hashes_resources_from_root_and_copies_external_entries_exactly(self):
        with tempfile.TemporaryDirectory() as directory:
            live = git_root(Path(directory) / "live", {"backend/manage.py": b"# live\n", RESOURCE: LIVE_MO})
            target = git_root(Path(directory) / "target", {
                "backend/manage.py": b"# target\n", "backend/cca_eob_runtime.py": b"# launcher\n",
                RESOURCE: TARGET_MO})
            inventory = self.inventory()
            reviewed = copy.deepcopy(inventory)
            bridge = POLICY.build(live, APP, inventory)
            candidate = POLICY.build(target, APP, inventory)
        self.assertEqual(inventory, reviewed, "build must not mutate the reviewed inventory")
        self.assertNotEqual(LIVE_MO, TARGET_MO)
        by_path = {name: {entry["path"]: entry for entry in manifest["files"]}
                   for name, manifest in (("bridge", bridge), ("candidate", candidate))}
        self.assertEqual(by_path["bridge"][APP_RESOURCE]["sha256"], digest(LIVE_MO))
        self.assertEqual(by_path["candidate"][APP_RESOURCE]["sha256"], digest(TARGET_MO))
        for manifest in (bridge, candidate):
            resource = {entry["path"]: entry for entry in manifest["files"]}[APP_RESOURCE]
            self.assertEqual((resource["access"], resource["origin"]), ("r", "resource"))
            # Every dependency/interpreter/stdlib entry is copied byte-for-byte, in order.
            self.assertEqual([entry for entry in manifest["files"] if entry["origin"] in EXTERNAL],
                             [entry for entry in reviewed["files"] if entry["origin"] in EXTERNAL])
            self.assertEqual([entry["path"] for entry in manifest["files"] if entry["origin"] != "tracked_code"],
                             [entry["path"] for entry in reviewed["files"]])
        self.assertEqual(by_path["bridge"][APP + "/backend/manage.py"]["sha256"], digest(b"# live\n"))
        self.assertEqual(by_path["candidate"][APP + "/backend/manage.py"]["sha256"], digest(b"# target\n"))
        self.assertNotIn(APP + "/backend/cca_eob_runtime.py", by_path["bridge"])
        self.assertIn(APP + "/backend/cca_eob_runtime.py", by_path["candidate"])

    def test_build_refuses_untracked_missing_or_git_symlink_resources(self):
        cases = {
            "untracked": lambda root: (root / RESOURCE).write_bytes(TARGET_MO),
            "missing": lambda root: (git_add(root, RESOURCE, TARGET_MO), (root / RESOURCE).unlink()),
            # core.symlinks=false checks a tracked link out as a plain file: the
            # index mode, not the working tree, must refuse it.
            "git_symlink_entry": lambda root: (
                (root / RESOURCE).write_bytes(b"../../../../outside.mo"),
                git(root, "update-index", "--add", "--cacheinfo",
                    "120000," + git(root, "hash-object", "-w", RESOURCE).strip() + "," + RESOURCE)),
        }
        for name, prepare in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as directory:
                root = git_root(Path(directory) / "root", {"backend/manage.py": b"# root\n"})
                (root / RESOURCE).parent.mkdir(parents=True, exist_ok=True)
                prepare(root)
                with self.assertRaisesRegex(ValueError, "not tracked|missing or unreadable"):
                    POLICY.build(root, APP, self.inventory())

    def test_build_refuses_invalid_outside_or_duplicate_resource_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = git_root(Path(directory) / "root", {
                "backend/manage.py": b"# root\n", RESOURCE: TARGET_MO, "deploy/outside.mo": b"outside",
                "backendx/resource.mo": b"prefix alias", "backend/.env": b"SECRET=1"})
            for path in (APP + "/deploy/outside.mo", APP + "/backendx/resource.mo", APP + "/backend",
                         APP + "/backend/../deploy/outside.mo", APP + "/backend/./locale/fr/LC_MESSAGES/django.mo",
                         APP + "/backend//locale/fr/LC_MESSAGES/django.mo", APP + "\\backend\\x.mo",
                         "/srv/polypbase/other/backend/locale/fr/LC_MESSAGES/django.mo",
                         "backend/locale/fr/LC_MESSAGES/django.mo", APP + "/backend/.env",
                         APP + "/backend/legacy.pyc", 7):
                inventory = self.inventory()
                inventory["files"][-1]["path"] = path
                with self.subTest(path=path), self.assertRaises(ValueError):
                    POLICY.build(root, APP, inventory)
            for mutate in (lambda files: files.append(copy.deepcopy(files[-1])),
                           lambda files: files.append(dict(files[-1], path=APP + "/backend/manage.py")),
                           lambda files: files[-1].pop("access"),
                           lambda files: files[-1].update(extra=True),
                           lambda files: files[-1].update(access="rwx")):
                inventory = self.inventory()
                mutate(inventory["files"])
                with self.subTest(mutation=inventory["files"][-1]), self.assertRaises(ValueError):
                    POLICY.build(root, APP, inventory)

    def test_build_refuses_resource_symlinks_on_any_component(self):
        with tempfile.TemporaryDirectory() as directory:
            root = git_root(Path(directory) / "root", {"backend/manage.py": b"# root\n", RESOURCE: TARGET_MO})
            real_lstat, leaf = Path.lstat, root / RESOURCE

            def lstat(path, *args, **kwargs):
                # Privilege-free stand-in for a leaf symlink (also runs on Windows).
                if os.path.realpath(path) == os.path.realpath(leaf):
                    return os.stat_result((stat.S_IFLNK | 0o777,) + (0,) * 9)
                return real_lstat(path, *args, **kwargs)

            with patch.object(POLICY.Path, "lstat", autospec=True, side_effect=lstat):
                with self.assertRaisesRegex(ValueError, "crosses a symlink"):
                    POLICY.build(root, APP, self.inventory())
            POLICY.build(root, APP, self.inventory())

    def test_build_refuses_real_symlink_or_directory_alias_to_outside_bytes(self):
        for kind in ("leaf", "ancestor"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = git_root(Path(directory) / "root", {"backend/manage.py": b"# root\n", RESOURCE: TARGET_MO})
                outside = Path(directory) / "outside"
                (outside / "fr/LC_MESSAGES").mkdir(parents=True)
                (outside / "fr/LC_MESSAGES/django.mo").write_bytes(LIVE_MO)
                if kind == "leaf":
                    (root / RESOURCE).unlink()
                    link(outside / "fr/LC_MESSAGES/django.mo", root / RESOURCE, self)
                else:
                    shutil.rmtree(root / "backend/locale")
                    link(outside, root / "backend/locale", self, directory=True)
                # The index still lists the path: only the filesystem check refuses it.
                self.assertIn(RESOURCE, POLICY.tracked_files(root))
                with self.assertRaisesRegex(ValueError, "symlink|inside the build root"):
                    POLICY.build(root, APP, self.inventory())

    def inventory(self, resource_hash=None):
        """Reviewed inventory as the collector writes it: LIVE-hashed resource."""
        return {"files": copy.deepcopy(self.manifest()["files"][1:]) + [
                    {"path": APP_RESOURCE, "sha256": resource_hash or digest(LIVE_MO),
                     "access": "r", "origin": "resource"}],
                "deny_read": self.manifest()["deny_read"], "scope_review": "offline synthetic"}

    def test_operator_candidate_data_check_accepts_exact_sources_and_rejects_drift(self):
        root = Path(__file__).resolve().parents[2]
        procedure = (root / 'docs/cca-eob-runtime-manifest-procedure.md').read_text(encoding='utf-8')
        code = procedure.split('# CCA_EOB_CANDIDATE_DATA_CHECK\n', 1)[1].split('\nPY\n', 1)[0]
        launcher = (root / 'backend/cca_eob_runtime.py').read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            evidence, target_files = work / 'evidence', {
                'backend/manage.py': b'# synthetic source\n', 'backend/cca_eob_runtime.py': launcher,
                RESOURCE: TARGET_MO}
            live_files = {'backend/manage.py': b'# synthetic source\n', RESOURCE: LIVE_MO}
            # Authenticated bundles (plain data) and the Git roots build actually reads.
            source, live_source = plain_tree(work / 'source', target_files), plain_tree(work / 'live', live_files)
            clone, app_root = git_root(work / 'clone', target_files), git_root(work / 'app', live_files)
            evidence.mkdir()
            target = 'f' * 40
            trace = {'schema': 'cca_eob_runtime_trace_v2', 'runtime': POLICY.RUNTIME,
                     'app': self.manifest()['app'], 'uid': 998, 'no_new_privs': True,
                     'readable_bytecode': [], 'deny_read': {self.manifest()['deny_read'][0]: True},
                     'startup': {'contract': 'cca_eob_source_only_v1', 'isolated': 1, 'no_site': 1,
                                 'dont_write_bytecode': True, 'pycache_prefix': '/dev/null',
                                 'site_initialized': True, 'implementation': 'cpython', 'version': [3, 13]}}
            trace_hash = hashlib.sha256(json.dumps(trace, sort_keys=True).encode()).hexdigest()
            inventory = self.inventory()
            inventory.update(deny_read=sorted(trace['deny_read']),
                             scope_review=f'synthetic target={target} | trace_sha256={trace_hash}')
            bridge = POLICY.build(app_root, self.manifest()['app'], inventory)
            candidate = POLICY.build(clone, self.manifest()['app'], inventory)
            # Separate serialized artifacts must not share build's in-memory entries.
            baseline = json.loads(json.dumps({'trace-v2.json': trace, 'inventory.json': inventory,
                                              'bridge.json': bridge, 'candidate.json': candidate}))

            def verify(documents):
                for name, document in documents.items():
                    (evidence / name).write_text(json.dumps(document), encoding='utf-8')
                return subprocess.run([sys.executable, '-I', '-S', '-B', '-X', 'pycache_prefix=/dev/null',
                                       '-c', code, str(source), str(evidence), target, str(clone),
                                       str(live_source)],
                                      text=True, capture_output=True, timeout=30)

            def resource(document):
                return next(entry for entry in document['files'] if entry['origin'] == 'resource')

            def dependency(document):
                return next(entry for entry in document['files'] if entry['origin'] == 'dependency')

            result = verify(baseline)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('CCA_EOB_CANDIDATE_DATA_OK', result.stdout)
            self.assertIn(f'CHANGED_RESOURCE {RESOURCE} live={digest(LIVE_MO)} target={digest(TARGET_MO)}',
                          result.stdout)
            # The defective build (inventory copied verbatim) yields a well-formed
            # candidate carrying the LIVE resource hash: it must be refused.
            legacy = copy.deepcopy(baseline)
            resource(legacy['candidate.json'])['sha256'] = digest(LIVE_MO)
            POLICY.validate(legacy['candidate.json'])
            mutations = {
                'source_hash': lambda d: d['candidate.json']['files'][0].update(sha256='0' * 64),
                'dependency_hash': lambda d: dependency(d['candidate.json']).update(sha256='0' * 64),
                'bridge_dependency_hash': lambda d: dependency(d['bridge.json']).update(sha256='0' * 64),
                'dependency_access': lambda d: dependency(d['candidate.json']).update(access='rx'),
                'private_probe': lambda d: d['candidate.json'].update(deny_read=['/different/private']),
                'bridge_scope_review': lambda d: d['bridge.json'].update(scope_review='other'),
                'startup': lambda d: d['trace-v2.json']['startup'].update(pycache_prefix=None),
                'trace_hash': lambda d: d['inventory.json'].update(scope_review=f'target={target} trace_sha256=wrong'),
                'duplicate': lambda d: d['candidate.json']['files'].append(copy.deepcopy(candidate['files'][0])),
                'candidate_live_resource_hash': lambda d: d.update(legacy),
                'candidate_wrong_resource_hash': lambda d: resource(d['candidate.json']).update(sha256='0' * 64),
                'bridge_target_resource_hash': lambda d: resource(d['bridge.json']).update(sha256=digest(TARGET_MO)),
                'inventory_resource_hash': lambda d: resource(d['inventory.json']).update(sha256=digest(TARGET_MO)),
                'resource_access': lambda d: resource(d['candidate.json']).update(access='rx'),
                'resource_removed': lambda d: d['candidate.json']['files'].remove(resource(d['candidate.json'])),
                'resource_added': lambda d: d['candidate.json']['files'].append(
                    dict(resource(d['candidate.json']), path=APP + '/backend/locale/en/LC_MESSAGES/django.mo')),
                'resource_relabelled': lambda d: resource(d['candidate.json']).update(origin='dependency'),
            }
            for name, mutate in mutations.items():
                documents = copy.deepcopy(baseline)
                mutate(documents)
                with self.subTest(mutation=name):
                    self.assertNotEqual(verify(documents).returncode, 0)
            for tree, relative, data in ((clone, 'backend/manage.py', b'# changed clone\n'),
                                         (clone, RESOURCE, LIVE_MO), (live_source, RESOURCE, TARGET_MO),
                                         (source, RESOURCE, b'\0other target bytes')):
                original = (tree / relative).read_bytes()
                (tree / relative).write_bytes(data)
                with self.subTest(tree=tree.name, relative=relative):
                    self.assertNotEqual(verify(baseline).returncode, 0)
                (tree / relative).write_bytes(original)
            self.assertEqual(verify(baseline).returncode, 0)
            (live_source / RESOURCE).unlink()
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
