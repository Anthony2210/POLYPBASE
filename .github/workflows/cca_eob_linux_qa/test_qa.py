"""Host-only QA protocol tests; no application imports or TARGET suite execution."""

import ast
import base64
import binascii
import hashlib
import importlib.util
import io
import json
from pathlib import Path
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
REQUIRED_ARTIFACTS = (
    "trace-v2.json", "inventory.json", "report.txt", "qa-only-manifest.json",
    "offline-audit.json", "independent-tracked-sources.json",
    "uv-locked-environment-check.exit.json", "installed-lock-membership.json",
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
        with patch.object(self.fixture.POLICY, 'tracked_sources', return_value=['backend/manage.py']), \
                patch.object(self.fixture.POLICY.Path, 'read_bytes', return_value=b'# manage\n'), \
                patch.object(self.fixture.POLICY.Path, 'is_file', return_value=True), \
                patch.object(self.fixture.POLICY.Path, 'is_symlink', return_value=False):
            manifest = self.fixture.POLICY.build(Path('.'), self.fixture.APP, inventory)
        self.assertEqual([entry for entry in manifest['files'] if entry['origin'] != 'tracked_code'],
                         inventory['files'])

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
        self.core = {"status": "passed", "expected_counts": [54, 6, 14],
                     "zero_skips_required": True}
        self.write_json("core-tests.json", self.core)
        self.summary = {"status": "passed", "target": CONTAINER.TARGET,
                                "qa_only": True, "production": False,
                        "active_manifest_installed": False, "artifact_sha256": {}}

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
        for change in ({"status": "failed"}, {"expected_counts": [54, 6]},
                               {"expected_counts": [44, 6, 14]},
                       {"zero_skips_required": False}):
            self.write_json("core-tests.json", dict(self.core, **change))
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, "test summary"):
                CONTAINER.validate_success(self.output)

    def test_failed_or_non_qa_installation_is_refused(self):
        self.complete_proof()
        for change in ({"status": "failed"}, {"target": "0" * 40}, {"qa_only": False},
                       {"production": True}, {"active_manifest_installed": True}):
            self.write_json("installation/summary.json", dict(self.summary, **change))
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, "installation proof"):
                CONTAINER.validate_success(self.output)

    def test_artifact_path_traversal_is_refused(self):
        self.complete_proof()
        self.summary["artifact_sha256"]["../outside"] = "0" * 64
        self.write_json("installation/summary.json", self.summary)
        with self.assertRaisesRegex(RuntimeError, "integrity failure"):
            CONTAINER.validate_success(self.output)


class SourceAndDiscoveryTests(unittest.TestCase):
    def test_prepare_pins_target_and_rejects_non_qa_delta_before_file_reads(self):
        self.assertEqual(PREPARE.TARGET, "5461e7a5c07cf8893c2db78953316df78c9243a8")
        self.assertEqual(CONTAINER.TARGET, PREPARE.TARGET)
        with patch.object(PREPARE, "git", side_effect=[b"backend/unapproved.py\0", b"", b""]):
            with self.assertRaisesRegex(RuntimeError, "Unauthorized delta.*backend/unapproved.py"):
                PREPARE.verify()

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

    def test_target_runtime_discovery_counts_54_without_execution(self):
        self.check_discovery("deploy/scripts", "test_cca_eob_runtime_*.py", 54)

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
                '831d9ef85b2e4a26266a0ba07bc556e0428cf393e85f17f0968af3dbbd637ac5',
            'pyproject.toml':
                '86036e55971c5acf56678bb135b0063bd2aff9b2059db8cd68632dd94f66bd80',
            'uv.lock':
                'ab2fac481c9ad93e16ab075ab7a072c437e3d46260de0fa60737dec90b48d154',
        }
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            self.assertEqual(len(archive.getmembers()), 841)
            for name, digest in expected.items():
                with self.subTest(path=name):
                    self.assertEqual(hashlib.sha256(archive.extractfile(name).read()).hexdigest(), digest)
        procedure = (ROOT / 'docs/cca-eob-runtime-manifest-procedure.md').read_text(encoding='utf-8')
        for name, digest in expected.items():
            if name.endswith('.py'):
                self.assertIn(digest + '  ' + name, procedure)


if __name__ == "__main__":
    unittest.main()
