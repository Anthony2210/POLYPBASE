"""Host-only QA protocol tests; no application imports or TARGET suite execution."""

import ast
import base64
import binascii
import errno
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


QA = Path(__file__).resolve().parent
ROOT = QA.parents[2]


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTAINER = load_file("qa_container_under_test", QA / "run_container.py")
PREPARE = load_file("qa_prepare_under_test", QA / "prepare.py")
PROOF = load_file("qa_proof_under_test", QA / "prove_installation.py")
# Authentic TARGET permission tool, loaded from TARGET bytes, never modified.
POLICY = load_file("qa_target_policy", ROOT / "deploy/scripts/cca_eob_runtime_permissions.py")
APP = "/srv/polypbase/app"
FR_MO = "backend/locale/fr/LC_MESSAGES/django.mo"
EN_MO = "backend/locale/en/LC_MESSAGES/django.mo"
JA_MO = "backend/locale/ja/LC_MESSAGES/django.mo"
UNCHANGED = "backend/apps/cultures/templates/registration/login.html"
# SHA-256 of the raw Git blobs (LF), never of a Windows working tree.
LIVE_FR_MO_SHA256 = "096e634c898ca0b72b4ab7199f31e79d67ed006ee243a4d0024d98a240184b82"
TARGET_FR_MO_SHA256 = "f640685fc0185867c28752a39572c9a38ae66d218bd6312221b3bbcc1500bba9"
LINUX = sys.platform == "linux"
linux_only = unittest.skipUnless(LINUX, "Linux-only; mandatory (zero skips) in the GitHub Linux QA")
EXTERNAL_ENTRIES = (
    {"path": "/usr/bin/python3.13", "sha256": "1" * 64, "access": "rx", "origin": "interpreter"},
    {"path": "/usr/lib/python3.13/os.py", "sha256": "2" * 64, "access": "r", "origin": "stdlib"},
    {"path": APP + "/.venv/lib/python3.13/site-packages/django/__init__.py", "sha256": "3" * 64,
     "access": "r", "origin": "dependency"},
)


def commit_files(commit, prefix="backend"):
    """Raw, identity-verified Git blob bytes of commit:prefix (no checkout conversion)."""
    records = []
    for record in PREPARE.git("ls-tree", "-rz", commit, "--", prefix).split(b"\0"):
        if record:
            header, name = record.split(b"\t", 1)
            mode, kind, oid = header.decode().split()
            assert kind == "blob" and mode in ("100644", "100755"), name
            records.append((name.decode(), oid))
    stream = io.BytesIO(PREPARE.git("cat-file", "--batch", data=(
        "\n".join(oid for _, oid in records) + "\n").encode()))
    files = {}
    for name, oid in records:
        actual, kind, size = stream.readline().decode().split()
        content = stream.read(int(size))
        assert actual == oid and stream.read(1) == b"\n"
        assert hashlib.sha1(b"blob " + size.encode() + b"\0" + content).hexdigest() == oid
        files[name] = content
    return files


def plain_tree(root, files):
    for relative, data in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(data)
    return root


def run_git(root, *args):
    return subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.symlinks=true", *args],
                          cwd=root, check=True, capture_output=True, timeout=120).stdout


def git_root(root, files):
    """Real Git working tree whose index tracks exactly ``files`` with exact bytes."""
    plain_tree(root, files)
    run_git(root, "init", "--quiet")
    run_git(root, "add", "--all", "--", ".")
    indexed = {}
    for record in run_git(root, "ls-files", "-s", "-z").split(b"\0"):
        if record:
            header, name = record.split(b"\t", 1)
            indexed[name.decode()] = header.decode().split()[1]
    expected = {name: hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                for name, data in files.items()}
    assert indexed == expected, "Temporary Git index does not carry the exact blob bytes"
    return root
REQUIRED_ARTIFACTS = (
    "trace-v2.json", "inventory.json", "report.txt", "qa-only-manifest.json",
    "offline-audit.json", "independent-tracked-sources.json",
    "uv-locked-environment-check.exit.json", "installed-lock-membership.json",
    "resource-closure.json", "startup-delta.json",
)


def frame(entries):
    """Build the real stdout protocol, including intentionally unsafe tar headers."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                member.linkname = "core-tests.json"
            member.size = len(data) if kind == tarfile.REGTYPE else 0
            archive.addfile(member, io.BytesIO(data))
    return CONTAINER.MARKER + base64.b64encode(buffer.getvalue()) + b"\n"


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


class EvidenceFrameTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name) / "evidence"
        self.output.mkdir()

    def test_valid_frame_extracts_exact_bytes_amid_logs(self):
        payloads = {
            "core-tests.json": b'{"status":"passed"}\n',
            "source-integrity.json": b"{}\n",
            "install-receipt.json": b"{}\n",
            "installation/report.txt": b"proof\x00\xff\n",
        }
        stdout = b"container log\r\n" + frame([
            (name, data, tarfile.REGTYPE) for name, data in payloads.items()
        ]) + b"final log\n"
        self.assertEqual(CONTAINER.unpack_evidence(stdout, self.output), set(payloads))
        self.assertEqual({name: (self.output / name).read_bytes() for name in payloads}, payloads)

    def test_missing_and_duplicate_frames_are_refused(self):
        valid = frame([("core-tests.json", b"{}", tarfile.REGTYPE)])
        for stdout in (b"ordinary logs\n", b"", valid + valid):
            with self.subTest(stdout=stdout), self.assertRaisesRegex(RuntimeError, "exactly one"):
                CONTAINER.unpack_evidence(stdout, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_invalid_base64_and_truncated_archive_are_refused(self):
        for encoded, error in ((b"not base64!", binascii.Error),
                               (base64.b64encode(b"not a gzip tar"), tarfile.ReadError)):
            with self.subTest(encoded=encoded), self.assertRaises(error):
                CONTAINER.unpack_evidence(CONTAINER.MARKER + encoded, self.output)

    def test_links_and_nonregular_members_are_refused(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE,
                     tarfile.FIFOTYPE, tarfile.CHRTYPE):
            with self.subTest(kind=kind), self.assertRaisesRegex(RuntimeError, "Unsafe"):
                CONTAINER.unpack_evidence(frame([("installation/proof", b"", kind)]), self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_traversal_absolute_and_noncanonical_paths_are_refused(self):
        for name in ("../escape", "/core-tests.json", "installation/../../escape",
                     "installation/../core-tests.json", "./core-tests.json",
                     "installation//proof", "installation/./proof", "C:/escape",
                     "installation\\proof", "installation/..\\escape"):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "Unsafe"):
                CONTAINER.unpack_evidence(frame([(name, b"bad", tarfile.REGTYPE)]), self.output)
        self.assertFalse((self.output.parent / "escape").exists())
        self.assertEqual(list(self.output.iterdir()), [])

    def test_unexpected_destinations_are_refused(self):
        for name in ("unexpected.json", "other/proof", "installation/nested/proof"):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "Unexpected"):
                CONTAINER.unpack_evidence(frame([(name, b"bad", tarfile.REGTYPE)]), self.output)

    def test_duplicate_members_are_refused_without_overwriting(self):
        stdout = frame([("core-tests.json", data, tarfile.REGTYPE) for data in (b"first", b"second")])
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            CONTAINER.unpack_evidence(stdout, self.output)
        self.assertEqual((self.output / "core-tests.json").read_bytes(), b"first")

    def test_existing_destination_is_not_overwritten(self):
        path = self.output / "core-tests.json"
        path.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            CONTAINER.unpack_evidence(frame([("core-tests.json", b"new", tarfile.REGTYPE)]), self.output)
        self.assertEqual(path.read_bytes(), b"original")


class VenvLockSecurityTests(unittest.TestCase):
    def check_lock(self, *, uid=0, mode=0o644, kind=stat.S_IFREG,
                   writable=False, ancestor_uid=0, ancestor_mode=0o755):
        # Model Linux metadata independently of the Windows host's ownership/ACLs.
        path = Path('/app/.venv/.lock')

        def metadata(candidate):
            if candidate == path:
                return SimpleNamespace(st_uid=uid, st_mode=kind | mode)
            return SimpleNamespace(st_uid=ancestor_uid, st_mode=stat.S_IFDIR | ancestor_mode)

        with patch.object(type(path), 'lstat', autospec=True, side_effect=metadata), \
                patch.object(PROOF.os, 'access', return_value=writable) as access:
            PROOF.controlled(path, regular=True)
        return path, access

    def test_root_owned_nonwritable_lock_and_all_ancestors_are_checked(self):
        path, access = self.check_lock()
        self.assertEqual([call.args for call in access.call_args_list],
                         [(candidate, PROOF.os.W_OK) for candidate in (path, *path.parents)])

    def test_non_root_lock_is_rejected_even_with_safe_permissions(self):
        for mode in (0o644, 0o666):
            with self.subTest(mode=oct(mode)), self.assertRaisesRegex(RuntimeError, 'Not root controlled'):
                self.check_lock(uid=10001, mode=mode)

    def test_group_or_world_writable_lock_is_rejected_even_on_read_only_filesystem(self):
        for mode in (0o666, 0o664, 0o646, 0o620, 0o602):
            with self.subTest(mode=oct(mode)), self.assertRaisesRegex(RuntimeError, 'Not root controlled'):
                self.check_lock(mode=mode, writable=False)

    def test_effectively_writable_lock_is_rejected_despite_safe_mode(self):
        with self.assertRaisesRegex(RuntimeError, 'Runtime-writable'):
            self.check_lock(mode=0o644, writable=True)

    def test_symlinked_lock_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'Unexpected alias'):
            self.check_lock(kind=stat.S_IFLNK)

    def test_unsafe_lock_ancestor_is_rejected(self):
        for uid, mode in ((10001, 0o755), (0, 0o775), (0, 0o777)):
            with self.subTest(uid=uid, mode=oct(mode)), self.assertRaisesRegex(RuntimeError, 'Not root controlled'):
                self.check_lock(ancestor_uid=uid, ancestor_mode=mode)

    def test_image_corrects_only_the_regular_lock_after_installation(self):
        dockerfile = (QA / 'Dockerfile').read_text(encoding='utf-8')
        sync = dockerfile.index('&& UV_OFFLINE=0 uv sync --locked')
        regular = dockerfile.index('&& test -f /app/.venv/.lock && test ! -L /app/.venv/.lock')
        before = dockerfile.index("&& stat -c 'QA venv lock before: uid=%u gid=%g mode=%a'")
        correction = dockerfile.index('&& chmod 0644 /app/.venv/.lock')
        after = dockerfile.index("&& stat -c 'QA venv lock after: uid=%u gid=%g mode=%a'")
        runtime = dockerfile.index('USER 10001:10001')
        self.assertLess(sync, regular)
        self.assertLess(regular, before)
        self.assertLess(before, correction)
        self.assertLess(correction, after)
        self.assertLess(after, runtime)
        self.assertEqual(dockerfile.count('chmod 0644'), 1)
        self.assertNotIn('chmod -R', dockerfile)
        self.assertNotIn('chown', dockerfile)


class ReviewedSystemResourceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = load_file('qa_system_inventory_fixture',
                                 ROOT / 'deploy/scripts/test_cca_eob_runtime_inventory.py')
        self.sample = self.fixture.InventoryCollectorTests('test_output_is_deterministic')
        self.sample.setUp()
        self.trace = json.loads(json.dumps(self.sample.document))
        self.files = dict(self.sample.files)
        for item in PROOF.REVIEWED_SYSTEM_RESOURCES:
            self.trace[item['event']].append(item['path'])
            self.files[item['path']] = ('synthetic system data: ' + item['path']).encode()
        self.extras = list(PROOF.REVIEWED_SYSTEM_EXTRAS)

    def collect(self, **kwargs):
        return self.sample.collect(self.trace, files=self.files, extras=self.extras, **kwargs)

    def test_scope_contains_exactly_three_observed_read_only_interpreter_decisions(self):
        expected = [
            ('/usr/lib/locale/C.utf8/LC_CTYPE', 'mapped'),
            ('/usr/lib/x86_64-linux-gnu/gconv/gconv-modules.cache', 'mapped'),
            ('/usr/share/zoneinfo/Europe/Paris', 'opened'),
        ]
        self.assertEqual([(item['path'], item['event']) for item in PROOF.REVIEWED_SYSTEM_RESOURCES], expected)
        scope = PROOF.reviewed_system_scope(self.trace, self.extras)
        self.assertIs(scope['qa_only'], True)
        self.assertEqual(scope['evidence_run'], '41611871224')
        self.assertIn('Bookworm', scope['environment'])
        for decision in scope['resources']:
            self.assertEqual((decision['origin'], decision['access'], decision['observed']),
                             ('interpreter', 'r', True))
            self.assertTrue(decision['purpose'])
            self.assertTrue(decision['expected_package'])

    def test_unreviewed_fourth_extra_duplicates_missing_or_widened_scope_are_refused(self):
        for extras in ([], self.extras[:-1], self.extras + [self.extras[0]],
                       self.extras + ['interpreter:r:/usr/share/zoneinfo/Europe/London'],
                       [self.extras[0].replace(':r:', ':rx:'), *self.extras[1:]],
                       [self.extras[0].replace('C.utf8/', 'C.utf8/../C.utf8/'), *self.extras[1:]]):
            with self.subTest(extras=extras), self.assertRaisesRegex(RuntimeError, 'exactly the three'):
                PROOF.reviewed_system_scope(self.trace, extras)

    def test_missing_observed_event_is_not_silently_accepted(self):
        for item in PROOF.REVIEWED_SYSTEM_RESOURCES:
            trace = json.loads(json.dumps(self.trace))
            trace[item['event']].remove(item['path'])
            with self.subTest(path=item['path']), self.assertRaisesRegex(RuntimeError, 'event missing'):
                PROOF.reviewed_system_scope(trace, self.extras)

    def test_runner_passes_exact_reviewed_extras_to_the_proof(self):
        tree = ast.parse((QA / 'run_tests.py').read_text(encoding='utf-8'))
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main')
        calls = [node for node in ast.walk(main) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                 and node.func.value.id == 'subprocess' and node.func.attr == 'run']
        self.assertEqual(len(calls), 1)
        values = [node.value for node in calls[0].args[0].elts
                  if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        proof_index = values.index('/qa/prove_installation.py')
        self.assertEqual(values[proof_index + 1:],
                         [value for extra in self.extras for value in ('--extra', extra)])

    def test_authentic_collector_refuses_all_three_without_scope(self):
        with self.assertRaises(ValueError) as refused:
            self.sample.collect(self.trace, files=self.files)
        self.assertEqual(str(refused.exception).count('unclassified runtime file needs --extra or --exclude:'), 3)
        for item in PROOF.REVIEWED_SYSTEM_RESOURCES:
            self.assertIn(item['path'] + ' (' + item['event'] + ')', str(refused.exception))

    def test_authentic_collector_hashes_entries_and_build_preserves_closure(self):
        before = json.loads(json.dumps(self.trace))
        inventory, report = self.collect()
        self.assertEqual(self.trace, before)
        files = PROOF.entries(inventory)
        PROOF.validate_reviewed_system_entries(files)
        for item in PROOF.REVIEWED_SYSTEM_RESOURCES:
            self.assertEqual(files[item['path']], {
                'path': item['path'], 'origin': 'interpreter', 'access': 'r',
                'sha256': hashlib.sha256(self.files[item['path']]).hexdigest(),
            })
            self.assertIn('interpreter:r:' + item['path'], report)
        _, baseline_report = self.sample.collect()
        # Fixture has a real missing-RECORD warning; the OS data must not hide it.
        line = lambda text: next(line for line in text.splitlines()
                                 if line.startswith('REVIEW: dependency files without a RECORD hash:'))
        self.assertEqual(line(report), line(baseline_report))
        # Resource bytes come from the build root (real-Git tests below); here only
        # the external closure and the reviewed resource shape must survive build.
        root_digest = 'b' * 64
        reviewed = json.loads(json.dumps(inventory))
        resources = [entry for entry in inventory['files'] if entry['origin'] == 'resource']
        self.assertTrue(resources)
        with patch.object(self.fixture.POLICY, 'tracked_sources', return_value=['backend/manage.py']), \
                patch.object(self.fixture.POLICY, 'tracked_files', return_value=set()), \
                patch.object(self.fixture.POLICY, 'resource_sha256', return_value=root_digest) as resource, \
                patch.object(self.fixture.POLICY.Path, 'read_bytes', return_value=b'# manage\n'), \
                patch.object(self.fixture.POLICY.Path, 'is_file', return_value=True), \
                patch.object(self.fixture.POLICY.Path, 'is_symlink', return_value=False):
            manifest = self.fixture.POLICY.build(Path('.'), self.fixture.APP, inventory)
        self.assertEqual(inventory, reviewed)
        self.assertEqual(resource.call_count, len(resources))
        self.assertEqual([entry for entry in manifest['files'] if entry['origin'] != 'tracked_code'],
                         [dict(entry, sha256=root_digest) if entry['origin'] == 'resource' else entry
                          for entry in reviewed['files']])

    def test_authentic_collector_still_refuses_an_unexpected_fourth_resource(self):
        for path, event in (('/usr/lib/locale/C.utf8/LC_TIME', 'mapped'),
                            ('/usr/lib/x86_64-linux-gnu/gconv/future.cache', 'mapped'),
                            ('/usr/share/zoneinfo/Europe/London', 'opened')):
            trace = json.loads(json.dumps(self.trace))
            trace[event].append(path)
            files = dict(self.files, **{path: b'unknown non-ELF system data'})
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'unclassified runtime file') as refused:
                self.sample.collect(trace, files=files, extras=self.extras)
            self.assertIn(path, str(refused.exception))

    def test_authentic_collector_still_requires_real_regular_extra_files(self):
        for item in PROOF.REVIEWED_SYSTEM_RESOURCES:
            path = item['path']
            files = dict(self.files)
            del files[path]
            with self.subTest(path=path, case='missing'), self.assertRaisesRegex(ValueError, 'real regular file'):
                self.sample.collect(self.trace, files=files, extras=self.extras)
            target = '/opt/qa/system-data'
            files[target] = b'synthetic alias target'
            links = dict(self.sample.links, **{path: target})
            with self.subTest(path=path, case='alias'), self.assertRaisesRegex(ValueError, 'real regular file'):
                self.sample.collect(self.trace, files=files, links=links, extras=self.extras)

    def test_proof_rejects_missing_or_reclassified_system_inventory_entries(self):
        inventory, _ = self.collect()
        entries = PROOF.entries(inventory)
        for item in PROOF.REVIEWED_SYSTEM_RESOURCES:
            for change in (None, {'origin': 'resource'}, {'access': 'rx'}):
                altered = dict(entries)
                if change is None:
                    del altered[item['path']]
                else:
                    altered[item['path']] = dict(altered[item['path']], **change)
                with self.subTest(path=item['path'], change=change), \
                        self.assertRaisesRegex(RuntimeError, 'missing or reclassified'):
                    PROOF.validate_reviewed_system_entries(altered)


class SuccessValidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name)
        self.core = {"status": "passed", "expected_counts": [59, 6, 14],
                     "zero_skips_required": True}
        self.write_json("core-tests.json", self.core)
        self.summary = {"status": "passed", "target": CONTAINER.TARGET,
                                "qa_only": True, "production": False,
                        "active_manifest_installed": False, "resource_closure": "passed",
                        "startup_delta": "passed", "tracked_runtime_sources": 249,
                        "manifest_files": 971, "expected_manifest_files": 971,
                        "inventory_files": 722,
                        "inventory_counts_by_origin": {"dependency": 465, "interpreter": 19,
                                                       "stdlib": 237, "resource": 1},
                        "manifest_counts_by_origin": {"dependency": 465, "interpreter": 19,
                                                      "stdlib": 237, "resource": 1, "tracked_code": 249},
                        "changed_resources": [FR_MO], "artifact_sha256": {}}

    def write_json(self, name, value):
        path = self.output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def complete_proof(self):
        for name in REQUIRED_ARTIFACTS:
            data = ("synthetic " + name + "\n").encode()
            path = self.output / "installation" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.summary["artifact_sha256"][name] = hashlib.sha256(data).hexdigest()
        self.write_json("installation/summary.json", self.summary)

    def test_complete_hash_verified_proof_passes(self):
        self.complete_proof()
        self.assertIsNone(CONTAINER.validate_success(self.output))

    def test_core_only_is_not_success(self):
        with self.assertRaises(FileNotFoundError):
            CONTAINER.validate_success(self.output)

    def test_each_required_proof_entry_is_mandatory(self):
        self.complete_proof()
        for name in REQUIRED_ARTIFACTS:
            summary = dict(self.summary, artifact_sha256={
                key: value for key, value in self.summary["artifact_sha256"].items() if key != name
            })
            self.write_json("installation/summary.json", summary)
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "evidence absent"):
                CONTAINER.validate_success(self.output)

    def test_missing_artifact_file_is_refused(self):
        self.complete_proof()
        (self.output / "installation" / REQUIRED_ARTIFACTS[0]).unlink()
        with self.assertRaises(FileNotFoundError):
            CONTAINER.validate_success(self.output)

    def test_tampered_artifact_is_refused(self):
        self.complete_proof()
        (self.output / "installation" / "report.txt").write_bytes(b"tampered")
        with self.assertRaisesRegex(RuntimeError, "integrity failure"):
            CONTAINER.validate_success(self.output)

    def test_failed_core_wrong_counts_and_skips_are_refused(self):
        self.complete_proof()
        for change in ({"status": "failed"}, {"expected_counts": [59, 6]},
                       {"expected_counts": [54, 6, 14]}, {"expected_counts": [44, 6, 14]},
                       {"zero_skips_required": False}):
            self.write_json("core-tests.json", dict(self.core, **change))
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, "test summary"):
                CONTAINER.validate_success(self.output)

    def test_failed_or_non_qa_installation_is_refused(self):
        self.complete_proof()
        for change in ({"status": "failed"}, {"target": "0" * 40}, {"qa_only": False},
                       {"production": True}, {"active_manifest_installed": True},
                       {"resource_closure": "failed"}, {"startup_delta": "failed"},
                       {"tracked_runtime_sources": 247}, {"manifest_files": 969},
                       {"expected_manifest_files": 969}, {"changed_resources": []},
                       {"changed_resources": [FR_MO, EN_MO]}):
            self.write_json("installation/summary.json", dict(self.summary, **change))
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, "installation proof"):
                CONTAINER.validate_success(self.output)

    def test_production_counts_and_same_total_origin_drift_are_refused(self):
        self.complete_proof()
        for change in (
            {"manifest_files": 955, "expected_manifest_files": 955, "inventory_files": 706},
            {"expected_manifest_files": 955}, {"inventory_files": 706},
            {"inventory_counts_by_origin": {**self.summary["inventory_counts_by_origin"],
                                            "dependency": 464, "stdlib": 238}},
            {"manifest_counts_by_origin": {**self.summary["manifest_counts_by_origin"],
                                           "dependency": 464, "stdlib": 238}},
            {"inventory_counts_by_origin": {}}, {"manifest_counts_by_origin": {}},
        ):
            self.write_json("installation/summary.json", dict(self.summary, **change))
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, "installation proof"):
                CONTAINER.validate_success(self.output)

    def test_artifact_path_traversal_is_refused(self):
        self.complete_proof()
        self.summary["artifact_sha256"]["../outside"] = "0" * 64
        self.write_json("installation/summary.json", self.summary)
        with self.assertRaisesRegex(RuntimeError, "integrity failure"):
            CONTAINER.validate_success(self.output)


class QaEnvironmentCountTests(unittest.TestCase):
    def documents(self):
        counts = {"dependency": 465, "interpreter": 19, "stdlib": 237, "resource": 1}
        inventory = {f"/{origin}/{number}": {"origin": origin}
                     for origin, count in counts.items() for number in range(count)}
        manifest = {**inventory, **{f"/tracked/{number}": {"origin": "tracked_code"}
                                  for number in range(249)}}
        return inventory, manifest

    def test_observed_bookworm_counts_are_accepted_and_match_host_pins(self):
        inventory, manifest = self.documents()
        inventory_counts, manifest_counts = PROOF.verify_qa_counts(inventory, manifest)
        self.assertEqual((len(inventory), len(manifest)), (722, 971))
        self.assertEqual(inventory_counts, CONTAINER.QA_INVENTORY_COUNTS)
        self.assertEqual(manifest_counts, CONTAINER.QA_MANIFEST_COUNTS)
        self.assertEqual(sum(inventory_counts.values()), PROOF.QA_INVENTORY_FILES)
        self.assertEqual(sum(manifest_counts.values()), PROOF.QA_MANIFEST_FILES)

    def test_production_sized_documents_are_not_qa_evidence(self):
        inventory, manifest = self.documents()
        for number in range(16):
            inventory.pop(f"/dependency/{number}")
            manifest.pop(f"/dependency/{number}")
        self.assertEqual((len(inventory), len(manifest)), (706, 955))
        with self.assertRaisesRegex(RuntimeError, "Debian 12 QA inventory"):
            PROOF.verify_qa_counts(inventory, manifest)
        with self.assertRaisesRegex(RuntimeError, "Debian 12 QA manifest"):
            PROOF.verify_qa_counts(self.documents()[0], manifest)

    def test_same_total_origin_drift_and_unknown_origins_are_refused(self):
        for document in (0, 1):
            for origin in ("stdlib", "unreviewed"):
                documents = self.documents()
                documents[document]["/dependency/0"] = {"origin": origin}
                with self.subTest(document=document, origin=origin), self.assertRaisesRegex(RuntimeError, "count/origin"):
                    PROOF.verify_qa_counts(*documents)


class SourceAndDiscoveryTests(unittest.TestCase):
    def test_prepare_pins_target_and_rejects_non_qa_delta_before_file_reads(self):
        self.assertEqual(PREPARE.TARGET, "cb07a2cea43e4af0bc9a8a61ed33abaa23eef1cc")
        self.assertEqual(CONTAINER.TARGET, PREPARE.TARGET)
        with patch.object(PREPARE, "git", side_effect=[b"backend/unapproved.py\0", b"", b""]):
            with self.assertRaisesRegex(RuntimeError, "Unauthorized delta.*backend/unapproved.py"):
                PREPARE.verify()

    def test_previous_target_delta_adds_exactly_two_eligible_sources(self):
        delta = PREPARE.target_delta()
        self.assertEqual(delta["tracked_code"], 249)
        self.assertEqual(delta["previous_tracked_code"], 247)
        self.assertEqual(delta["expected_production_candidate_entries"], 955)
        self.assertEqual(delta["conditional_on_unchanged_debian13_inventory_entries"], 706)
        self.assertNotIn("expected_candidate_entries", delta)
        self.assertEqual(delta["changes"], PREPARE.EXPECTED_DELTA)
        self.assertEqual(delta["additional_eligible_sources"], sorted(PROOF.UNEXPECTED_STARTUP_SOURCES))

    def test_unexpected_resource_dependency_or_test_delta_is_refused(self):
        original = PREPARE.git
        for name in ("uv.lock", FR_MO, "backend/config/settings.py", "backend/test_extra.py"):
            def changed(*args, **kwargs):
                data = original(*args, **kwargs)
                if args[:3] == ("diff", "--no-renames", "--name-status"):
                    data += ("M\t" + name + "\n").encode()
                return data
            with self.subTest(path=name), patch.object(PREPARE, "git", side_effect=changed):
                with self.assertRaisesRegex(RuntimeError, "Unexpected previous-TARGET delta"):
                    PREPARE.target_delta()

    def test_candidate_policy_sources_are_not_startup_imports(self):
        trace = {"modules": ["/app/" + name for name in PROOF.REQUIRED_RUNTIME_MODULES]}
        result = PROOF.verify_startup_delta(trace)
        self.assertEqual(result["status"], "passed")
        for name in PROOF.UNEXPECTED_STARTUP_SOURCES:
            for event in ("modules", "opened", "mapped"):
                changed = dict(trace, **{event: ["/app/" + name]})
                with self.subTest(path=name, event=event), self.assertRaisesRegex(RuntimeError, "startup import/read"):
                    PROOF.verify_startup_delta(changed)

    def check_discovery(self, directory, pattern, expected, class_name=None):
        paths = sorted((ROOT / directory).glob(pattern))
        self.assertTrue(paths)
        static_count = 0
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and (class_name is None or node.name == class_name):
                    static_count += sum(isinstance(method, ast.FunctionDef) and method.name.startswith("test_")
                                        for method in node.body)
        self.assertEqual(static_count, expected, "Static TARGET test count changed")
        loader = unittest.TestLoader()
        # Discovery must not run setUpClass, test methods, application commands or sockets.
        with patch.object(subprocess, "Popen", side_effect=AssertionError("No subprocess during discovery")), \
                patch.object(socket, "socket", side_effect=AssertionError("No socket during discovery")), \
                patch.object(sys, "path", list(sys.path)), patch.dict(sys.modules):
            suite = loader.discover(str(ROOT / directory), pattern=pattern)
            self.assertEqual(loader.errors, [])
            discovered = list(cases(suite))
            if class_name is not None:
                discovered = [case for case in discovered if type(case).__name__ == class_name]
            self.assertEqual(len(discovered), expected)
            self.assertEqual(len({case.id() for case in discovered}), expected)
            self.assertFalse(any(type(case).__name__ == "_FailedTest" for case in discovered))
            self.assertEqual(unittest.TestSuite(discovered).countTestCases(), expected)
        # Skip decorators affect execution only; they must never reduce discovery counts.

    def test_target_runtime_discovery_counts_59_without_execution(self):
        self.check_discovery("deploy/scripts", "test_cca_eob_runtime_*.py", 59)

    def test_target_preflight_discovery_counts_6_without_execution(self):
        self.check_discovery("deploy/tests", "test_preflight_settings.py", 6)

    def test_target_migration_guard_discovery_counts_14_without_execution(self):
        self.check_discovery("deploy/tests", "test_deploy_release.py", 14, "MigrationGuardTests")

    def test_target_collector_accepts_pyvenv_config_reported_as_opened(self):
        fixture = load_file("qa_inventory_fixture", ROOT / "deploy/scripts/test_cca_eob_runtime_inventory.py")
        sample = fixture.InventoryCollectorTests("test_output_is_deterministic")
        sample.setUp()  # Only constructs the existing FakeFS data; no TARGET test is run.
        baseline, _ = sample.collect()
        config = fixture.APP + "/.venv/pyvenv.cfg"
        document = dict(sample.document, opened=[*sample.document["opened"], config])
        before = json.loads(json.dumps(document))
        inventory, _ = sample.collect(document)
        self.assertEqual(document, before)
        self.assertEqual(inventory['files'], baseline['files'])
        entry = next(entry for entry in inventory['files'] if entry['path'] == config)
        self.assertEqual(entry, {'path': config, 'origin': 'interpreter', 'access': 'r',
                                 'sha256': hashlib.sha256(sample.files[config]).hexdigest()})
        trace_hash = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
        self.assertIn('trace_sha256=' + trace_hash, inventory['scope_review'])

    def test_target_collector_still_refuses_mapped_pyvenv_config(self):
        fixture = load_file("qa_inventory_fixture", ROOT / "deploy/scripts/test_cca_eob_runtime_inventory.py")
        sample = fixture.InventoryCollectorTests("test_output_is_deterministic")
        sample.setUp()
        config = fixture.APP + '/.venv/pyvenv.cfg'
        document = dict(sample.document, opened=[*sample.document['opened'], config],
                        mapped=[*sample.document['mapped'], config])
        before = json.loads(json.dumps(document))
        with self.assertRaisesRegex(ValueError, 'mapped.*pyvenv.cfg'):
            sample.collect(document)
        self.assertEqual(document, before)

    def test_canonical_target_archive_and_tool_identities(self):
        data = PREPARE.target_archive()
        self.assertEqual(hashlib.sha256(data).hexdigest(), PREPARE.TARGET_ARCHIVE_SHA256)
        expected = {
            'backend/cca_eob_runtime.py':
                '0d125888059a97a7e8641f041ee10422aebec91258e43306ebd135b5f2daea42',
            'deploy/scripts/cca_eob_runtime_inventory.py':
                'bf9f59d689036b38d47f5688a32d8faa903efab37b139f3c92c76ba6f1f517e4',
            'deploy/scripts/cca_eob_runtime_permissions.py':
                '8ce7fcb443978a8b32ddfdff4fa8b383f262ce2d89b41f2971c7dc2b86cd30c4',
            'pyproject.toml':
                '86036e55971c5acf56678bb135b0063bd2aff9b2059db8cd68632dd94f66bd80',
            'uv.lock':
                'ab2fac481c9ad93e16ab075ab7a072c437e3d46260de0fa60737dec90b48d154',
        }
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            self.assertEqual(len(archive.getmembers()), 845)
            for name, digest in expected.items():
                with self.subTest(path=name):
                    self.assertEqual(hashlib.sha256(archive.extractfile(name).read()).hexdigest(), digest)
        procedure = (ROOT / 'docs/cca-eob-runtime-manifest-procedure.md').read_text(encoding='utf-8')
        for name, digest in expected.items():
            if name.endswith('.py'):
                self.assertIn(digest + '  ' + name, procedure)
        # The previous permission tool is revoked: never pinned as a current identity.
        revoked = '831d9ef85b2e4a26266a0ba07bc556e0428cf393e85f17f0968af3dbbd637ac5'
        self.assertNotIn(revoked + '  deploy/scripts/cca_eob_runtime_permissions.py', procedure)
        self.assertIn('`' + revoked + '`\nest **révoquée**', procedure)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class LiveReferenceTests(unittest.TestCase):
    """LIVE resource bytes are pinned Git data, never a checkout or a live host."""

    def test_live_reference_is_pinned_and_lists_the_reviewed_catalog_delta(self):
        live = PREPARE.live_reference()
        self.assertEqual((live["commit"], live["backend_tree"]), (PREPARE.LIVE, PREPARE.LIVE_BACKEND_TREE))
        self.assertEqual(PROOF.LIVE, PREPARE.LIVE)
        self.assertEqual(PROOF.LIVE_BACKEND_TREE, PREPARE.LIVE_BACKEND_TREE)
        resources = live["backend_resource_sha256"]
        self.assertEqual(resources[FR_MO], LIVE_FR_MO_SHA256)
        self.assertIn(EN_MO, resources)
        self.assertNotIn(JA_MO, resources)
        self.assertFalse(any(name.endswith(".py") for name in resources))
        self.assertEqual(live["changed_backend_resources"], sorted(
            "backend/locale/%s/LC_MESSAGES/django.%s" % (language, kind)
            for language in ("en", "fr", "ja") for kind in ("mo", "po")))
        self.assertEqual(PROOF.validated_live_reference(
            {"target": PROOF.TARGET, "status": "passed", "live_reference": live}), live)

    def test_catalog_blob_identities_are_lf_git_bytes(self):
        live, target = commit_files(PREPARE.LIVE, FR_MO), commit_files(PREPARE.TARGET, FR_MO)
        self.assertEqual(sha256(live[FR_MO]), LIVE_FR_MO_SHA256)
        self.assertEqual(sha256(target[FR_MO]), TARGET_FR_MO_SHA256)
        self.assertNotEqual(LIVE_FR_MO_SHA256, TARGET_FR_MO_SHA256)

    def test_procedure_bundles_are_canonical_lf_archives(self):
        # Operator bundles of procedure step 0, with checkout conversion disabled
        # (a default Windows git archive applies core.autocrlf and is refused).
        bundles = (
            ("548c79a505d995164387ecda976dd9efa90de9cf407d0f9f88019f0e18d7d93b", PREPARE.TARGET,
             ("backend", "deploy/scripts/cca_eob_runtime_inventory.py",
              "deploy/scripts/cca_eob_runtime_permissions.py")),
            ("75f398cd7cdfe09030570d80383c143bc91374cf1e5b86ed9d851efa8c27e9d9", PREPARE.LIVE, ("backend",)),
        )
        for expected, commit, paths in bundles:
            data = PREPARE.git("-c", "core.autocrlf=false", "-c", "tar.umask=0002",
                               "archive", "--format=tar", commit, *paths)
            blobs = {}
            for path in paths:
                blobs.update(commit_files(commit, path))
            with self.subTest(commit=commit), tarfile.open(fileobj=io.BytesIO(data)) as archive:
                members = {member.name: archive.extractfile(member).read()
                           for member in archive.getmembers() if member.isfile()}
                self.assertFalse(any(member.issym() or member.islnk() for member in archive.getmembers()))
                self.assertEqual(members, blobs)
                self.assertEqual(sha256(data), expected)

    def test_live_reference_record_is_refused_when_altered(self):
        live = PREPARE.live_reference()
        integrity = {"target": PROOF.TARGET, "status": "passed", "live_reference": live}
        for change in (
            lambda d: d.update(target="0" * 40), lambda d: d.update(status="failed"),
            lambda d: d.pop("live_reference"),
            lambda d: d["live_reference"].update(commit="0" * 40),
            lambda d: d["live_reference"].update(backend_tree="0" * 40),
            lambda d: d["live_reference"].update(extra=True),
            lambda d: d["live_reference"]["backend_resource_sha256"].update({FR_MO: "bad"}),
            lambda d: d["live_reference"]["backend_resource_sha256"].update({"backend/x.py": "0" * 64}),
            lambda d: d["live_reference"]["backend_resource_sha256"].update({"deploy/x.mo": "0" * 64}),
            lambda d: d["live_reference"].update(changed_backend_resources=[FR_MO, EN_MO]),
        ):
            document = json.loads(json.dumps(integrity))
            change(document)
            with self.subTest(document=document), self.assertRaises(RuntimeError):
                PROOF.validated_live_reference(document)


class RealBlobResourceBuildTests(unittest.TestCase):
    """Authentic TARGET build with real LIVE/TARGET backend blobs in real Git roots."""

    @classmethod
    def setUpClass(cls):
        cls.live_files = commit_files(PREPARE.LIVE)
        cls.target_files = commit_files(PREPARE.TARGET)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)

    def inventory(self, scope_review="QA synthetic resource review"):
        """Reviewed inventory as collected on LIVE: resources carry LIVE hashes."""
        return {"files": [dict(entry) for entry in EXTERNAL_ENTRIES] + [
                    {"path": APP + "/" + name, "sha256": sha256(self.live_files[name]),
                     "access": "r", "origin": "resource"} for name in (FR_MO, EN_MO, UNCHANGED)],
                "deny_read": [APP + "/backend/.env"], "scope_review": scope_review}

    def builds(self, inventory):
        live = git_root(self.work / "live-app", self.live_files)
        clone = git_root(self.work / "target-clone", self.target_files)
        reviewed = json.loads(json.dumps(inventory))
        bridge, candidate = POLICY.build(live, APP, inventory), POLICY.build(clone, APP, inventory)
        self.assertEqual(inventory, reviewed, "build must not mutate the reviewed inventory")
        return clone, bridge, candidate

    @staticmethod
    def by_path(document):
        return {entry["path"]: entry for entry in document["files"]}

    def sources(self, files):
        return {APP + "/" + name: sha256(data) for name, data in files.items()
                if name.endswith(".py") and not any(part.startswith("test") for part in name.split("/"))}

    def test_bridge_carries_live_and_candidate_target_bytes_with_identical_external_closure(self):
        inventory = self.inventory()
        _, bridge, candidate = self.builds(inventory)
        bridge_files, candidate_files = self.by_path(bridge), self.by_path(candidate)
        self.assertEqual(bridge_files[APP + "/" + FR_MO]["sha256"], LIVE_FR_MO_SHA256)
        self.assertEqual(candidate_files[APP + "/" + FR_MO]["sha256"], TARGET_FR_MO_SHA256)
        for name in (EN_MO, UNCHANGED):
            self.assertEqual(bridge_files[APP + "/" + name]["sha256"], sha256(self.live_files[name]))
            self.assertEqual(candidate_files[APP + "/" + name]["sha256"], sha256(self.target_files[name]))
        self.assertEqual(sha256(self.live_files[UNCHANGED]), sha256(self.target_files[UNCHANGED]))
        for manifest in (bridge, candidate):
            # dependency/interpreter/stdlib are copied exactly and in order.
            self.assertEqual([entry for entry in manifest["files"] if entry["origin"] != "tracked_code"
                              and entry["origin"] != "resource"], list(EXTERNAL_ENTRIES))
            self.assertEqual([(entry["path"], entry["access"], entry["origin"]) for entry in manifest["files"]
                              if entry["origin"] == "resource"],
                             [(entry["path"], entry["access"], entry["origin"]) for entry in inventory["files"]
                              if entry["origin"] == "resource"])
            self.assertEqual((manifest["deny_read"], manifest["scope_review"]),
                             (inventory["deny_read"], inventory["scope_review"]))
        self.assertEqual({p: e["sha256"] for p, e in bridge_files.items() if e["origin"] == "tracked_code"},
                         self.sources(self.live_files))
        self.assertEqual({p: e["sha256"] for p, e in candidate_files.items() if e["origin"] == "tracked_code"},
                         self.sources(self.target_files))
        self.assertIn(APP + "/backend/cca_eob_runtime.py", candidate_files)
        self.assertNotIn(APP + "/backend/cca_eob_runtime.py", bridge_files)

    def test_procedure_data_check_proves_real_catalog_change_and_refuses_legacy_candidate(self):
        procedure = (ROOT / "docs/cca-eob-runtime-manifest-procedure.md").read_text(encoding="utf-8")
        code = procedure.split("# CCA_EOB_CANDIDATE_DATA_CHECK\n", 1)[1].split("\nPY\n", 1)[0]
        trace = {"schema": "cca_eob_runtime_trace_v2", "runtime": POLICY.RUNTIME, "app": APP,
                 "uid": 998, "no_new_privs": True, "readable_bytecode": [],
                 "deny_read": {APP + "/backend/.env": True}, "startup": dict(PROOF.STARTUP)}
        trace_hash = sha256(json.dumps(trace, sort_keys=True).encode())
        inventory = self.inventory(f"QA target={PREPARE.TARGET} | trace_sha256={trace_hash}")
        clone, bridge, candidate = self.builds(inventory)
        source = plain_tree(self.work / "source", self.target_files)
        live = plain_tree(self.work / "live-source", self.live_files)
        evidence = self.work / "evidence"
        evidence.mkdir()

        def verify(documents):
            for name, document in documents.items():
                (evidence / name).write_text(json.dumps(document), encoding="utf-8")
            return subprocess.run([sys.executable, "-I", "-S", "-B", "-X", "pycache_prefix=" + os.devnull,
                                   "-c", code, str(source), str(evidence), PREPARE.TARGET, str(clone), str(live)],
                                  capture_output=True, text=True, timeout=60)

        baseline = {"trace-v2.json": trace, "inventory.json": inventory,
                    "bridge.json": bridge, "candidate.json": candidate}
        result = verify(baseline)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[-1], "CCA_EOB_CANDIDATE_DATA_OK")
        self.assertEqual(lines[:-1], [
            f"CHANGED_RESOURCE {EN_MO} live={sha256(self.live_files[EN_MO])} target={sha256(self.target_files[EN_MO])}",
            f"CHANGED_RESOURCE {FR_MO} live={LIVE_FR_MO_SHA256} target={TARGET_FR_MO_SHA256}",
        ])
        # The defective pre-fix build copied inventory resources verbatim (LIVE bytes).
        legacy = json.loads(json.dumps(baseline))
        for entry in legacy["candidate.json"]["files"]:
            if entry["origin"] == "resource":
                entry["sha256"] = next(e["sha256"] for e in inventory["files"] if e["path"] == entry["path"])
        POLICY.validate(legacy["candidate.json"])
        swapped = json.loads(json.dumps(baseline))
        swapped["bridge.json"] = json.loads(json.dumps(candidate))
        for name, documents, message in (("legacy candidate", legacy, "Candidate resource differs from TARGET"),
                                         ("TARGET-rooted bridge", swapped, "differs from LIVE")):
            with self.subTest(case=name):
                refused = verify(documents)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn(message, refused.stderr)


class RealFilesystemResourceAliasTests(unittest.TestCase):
    """Real untracked files, Linux symlinks and O_NOFOLLOW against the authentic build."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        work = Path(temporary.name)
        self.target_bytes = commit_files(PREPARE.TARGET, "backend/locale")
        self.live_bytes = commit_files(PREPARE.LIVE, FR_MO)[FR_MO]
        files = {"backend/manage.py": b"# synthetic root\n", FR_MO: self.target_bytes[FR_MO],
                 EN_MO: self.target_bytes[EN_MO]}
        self.root = git_root(work / "root", files)
        self.leaf = Path(os.path.realpath(self.root)) / FR_MO
        self.outside = work / "outside"
        (self.outside / "LC_MESSAGES").mkdir(parents=True)
        self.outside_file = self.outside / "LC_MESSAGES/django.mo"
        self.outside_file.write_bytes(self.live_bytes)

    def inventory(self):
        return {"files": [dict(entry) for entry in EXTERNAL_ENTRIES] + [
                    {"path": APP + "/" + FR_MO, "sha256": LIVE_FR_MO_SHA256, "access": "r", "origin": "resource"}],
                "deny_read": [APP + "/backend/.env"], "scope_review": "QA synthetic alias review"}

    def refused(self, pattern):
        with self.assertRaisesRegex(ValueError, pattern) as refusal:
            POLICY.build(self.root, APP, self.inventory())
        return refusal.exception

    def test_regular_tracked_resource_is_hashed_from_root_bytes(self):
        manifest = POLICY.build(self.root, APP, self.inventory())
        entry = next(e for e in manifest["files"] if e["origin"] == "resource")
        self.assertEqual(entry["sha256"], TARGET_FR_MO_SHA256)

    def test_untracked_resource_present_on_disk_is_refused(self):
        run_git(self.root, "rm", "--cached", "--quiet", "--", FR_MO)
        self.assertTrue(self.leaf.is_file())
        self.refused("not tracked by Git")

    def assert_tracked_but_aliased(self, pattern="crosses a symlink"):
        self.assertIn(FR_MO, POLICY.tracked_files(self.root))
        self.refused(pattern)

    @linux_only
    def test_real_leaf_symlink_to_outside_live_bytes_is_refused(self):
        self.leaf.unlink()
        os.symlink(self.outside_file, self.leaf)
        self.assertEqual(self.leaf.read_bytes(), self.live_bytes)
        self.assert_tracked_but_aliased()

    @linux_only
    def test_real_ancestor_directory_symlink_is_refused(self):
        locale = self.leaf.parents[2]
        self.assertEqual(locale.name, "locale")
        shutil.move(str(locale / "fr"), str(self.outside / "fr"))
        os.symlink(self.outside / "fr", locale / "fr", target_is_directory=True)
        self.assertTrue(self.leaf.is_file())
        self.assert_tracked_but_aliased()

    @linux_only
    def test_real_in_root_symlink_to_another_tracked_resource_is_refused(self):
        self.leaf.unlink()
        os.symlink("../../en/LC_MESSAGES/django.mo", self.leaf)
        self.assertEqual(self.leaf.read_bytes(), self.target_bytes[EN_MO])
        self.assert_tracked_but_aliased()

    @linux_only
    def test_git_symlink_entry_with_real_link_is_refused_by_index_mode(self):
        self.leaf.unlink()
        os.symlink("../../en/LC_MESSAGES/django.mo", self.leaf)
        run_git(self.root, "add", "--", FR_MO)
        self.assertIn(b"120000", run_git(self.root, "ls-files", "-s", "--", FR_MO))
        self.assertNotIn(FR_MO, POLICY.tracked_files(self.root))
        self.refused("not tracked by Git")

    def swap_at_open(self, replace, *, strip_nofollow=False):
        """Replace the leaf after every path check, immediately before os.open."""
        real_open, seen = os.open, []

        def opener(path, flags, *args, **kwargs):
            if os.fspath(path) == str(self.leaf):
                seen.append(flags)
                replace()
                if strip_nofollow:
                    flags &= ~os.O_NOFOLLOW
            return real_open(path, flags, *args, **kwargs)

        with patch.object(POLICY.os, "open", opener):
            error = self.refused("changed while being hashed" if strip_nofollow else "missing or unreadable")
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0] & os.O_NOFOLLOW, "build must open resources with O_NOFOLLOW")
        return error

    def link_over_leaf(self):
        temporary = self.leaf.with_name("swap.tmp")
        os.symlink(self.outside_file, temporary)
        os.replace(temporary, self.leaf)

    @linux_only
    def test_o_nofollow_refuses_leaf_swapped_to_symlink_after_checks(self):
        self.assertTrue(POLICY.os.O_NOFOLLOW)
        error = self.swap_at_open(self.link_over_leaf)
        self.assertIsInstance(error.__cause__, OSError)
        self.assertEqual(error.__cause__.errno, errno.ELOOP)

    @linux_only
    def test_identity_check_refuses_swap_even_without_o_nofollow(self):
        # Defence in depth: following the link opens another inode, which fstat refuses.
        self.swap_at_open(self.link_over_leaf, strip_nofollow=True)

    @linux_only
    def test_regular_file_replaced_between_lstat_and_open_is_refused(self):
        def replace():
            other = self.leaf.with_name("other.tmp")
            other.write_bytes(self.live_bytes)
            os.replace(other, self.leaf)

        real_open, seen = os.open, []

        def opener(path, flags, *args, **kwargs):
            if os.fspath(path) == str(self.leaf):
                seen.append(flags)
                replace()
            return real_open(path, flags, *args, **kwargs)

        with patch.object(POLICY.os, "open", opener):
            self.refused("changed while being hashed")
        self.assertEqual(len(seen), 1)


class ResourceClosureProofTests(unittest.TestCase):
    """Fail-closed resource/runtime closure checks of the container proof."""

    @classmethod
    def setUpClass(cls):
        cls.live = PREPARE.live_reference()
        cls.target_resources = {name: sha256(data) for name, data in commit_files(PREPARE.TARGET).items()
                                if not name.endswith(".py")}

    def documents(self, resources=(FR_MO, UNCHANGED)):
        external = {entry["path"].replace(APP, "/app"): dict(entry, path=entry["path"].replace(APP, "/app"))
                    for entry in EXTERNAL_ENTRIES}
        found = {"/app/" + name: {"path": "/app/" + name, "access": "r", "origin": "resource",
                                  "sha256": self.target_resources.get(name, "0" * 64)} for name in resources}
        inventory = {**external, **found}
        manifest = json.loads(json.dumps(inventory))
        manifest["/app/backend/manage.py"] = {"path": "/app/backend/manage.py", "sha256": "4" * 64,
                                              "access": "r", "origin": "tracked_code"}
        trace = {"modules": sorted(["/app/" + name for name in PROOF.REQUIRED_RUNTIME_MODULES]
                                   + ["/usr/lib/python3.13/os.py"])}
        return trace, inventory, manifest

    def verify(self, trace, inventory, manifest):
        return PROOF.verify_resource_closure(trace, inventory, manifest, self.target_resources, self.live)

    def test_exact_target_resources_with_reviewed_catalog_change_pass(self):
        closure = self.verify(*self.documents())
        self.assertEqual(closure["changed_resources"], [FR_MO])
        rows = {row["path"]: row for row in closure["resources"]}
        self.assertEqual(rows["/app/" + FR_MO], {"path": "/app/" + FR_MO, "access": "r", "changed": True,
                                                 "live_sha256": LIVE_FR_MO_SHA256,
                                                 "target_sha256": TARGET_FR_MO_SHA256})
        self.assertFalse(rows["/app/" + UNCHANGED]["changed"])
        self.assertEqual(closure["loaded_backend_modules"], sorted(PROOF.REQUIRED_RUNTIME_MODULES))
        self.assertEqual(closure["manifest_counts_by_origin"], {
            "dependency": 1, "interpreter": 1, "stdlib": 1, "resource": 2, "tracked_code": 1})

    def test_drift_and_unreviewed_resources_are_refused(self):
        fr, dependency = "/app/" + FR_MO, "/app/.venv/lib/python3.13/site-packages/django/__init__.py"
        cases = {
            "legacy LIVE hash in manifest": (None, lambda t, i, m: m[fr].update(sha256=LIVE_FR_MO_SHA256),
                                             "does not carry TARGET bytes"),
            "inventory not TARGET bytes": (None, lambda t, i, m: i[fr].update(sha256=LIVE_FR_MO_SHA256),
                                           "Inventory resource differs"),
            "unreviewed changed catalog": ((FR_MO, EN_MO), None, "differ from the reviewed set"),
            "reviewed catalog not observed": ((UNCHANGED,), None, "differ from the reviewed set"),
            "new resource absent from LIVE": ((FR_MO, JA_MO), None, "absent from LIVE"),
            "untracked resource": ((FR_MO, "backend/untracked.mo"), None, "not a tracked regular TARGET blob"),
            "resource outside backend": (None, lambda t, i, m: [d.update({"/app/deploy/x.mo": dict(
                d[fr], path="/app/deploy/x.mo")}) for d in (i, m)], "outside backend"),
            "external hash drift": (None, lambda t, i, m: m[dependency].update(sha256="0" * 64), "External"),
            "external removed": (None, lambda t, i, m: m.pop(dependency), "External"),
            "resource relabelled": (None, lambda t, i, m: m[fr].update(origin="dependency"), "External"),
            "resource access widened": (None, lambda t, i, m: m[fr].update(access="rx"), "Resource path"),
            "resource added by build": (None, lambda t, i, m: m.update({"/app/" + EN_MO: dict(
                m[fr], path="/app/" + EN_MO)}), "Resource path"),
            "resource removed by build": (None, lambda t, i, m: m.pop(fr), "Resource path"),
            "inventory tracked_code": (None, lambda t, i, m: i.update({"/app/backend/x.py": dict(
                m["/app/backend/manage.py"], path="/app/backend/x.py")}), "unexpected origins"),
            "manifest unknown origin": (None, lambda t, i, m: m["/app/backend/manage.py"].update(origin="all"),
                                        "unexpected origins"),
            "settings not loaded": (None, lambda t, i, m: t.update(modules=["/usr/lib/python3.13/os.py"]),
                                    "was not loaded"),
        }
        for name, (resources, mutate, message) in cases.items():
            documents = self.documents(resources or (FR_MO, UNCHANGED))
            if mutate:
                mutate(*documents)
            with self.subTest(case=name), self.assertRaisesRegex(RuntimeError, message):
                self.verify(*documents)


def main():
    """Strict runner: on Linux every Linux-only protocol test must run (zero skips)."""
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    count = suite.countTestCases()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"QA protocol tests: discovered={count} run={result.testsRun} failures={len(result.failures)} "
          f"errors={len(result.errors)} skipped={len(result.skipped)} linux={LINUX}", flush=True)
    if count <= 0 or result.testsRun != count or not result.wasSuccessful():
        return 1
    if LINUX and result.skipped:
        print("STOP: skipped QA protocol tests are refused on Linux", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
