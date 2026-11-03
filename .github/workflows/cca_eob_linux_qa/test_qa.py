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
import subprocess
import sys
import tarfile
import tempfile
import unittest
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
