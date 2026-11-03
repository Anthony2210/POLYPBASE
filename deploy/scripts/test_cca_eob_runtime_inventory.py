"""Offline inventory-collector tests on a synthetic filesystem; nothing live is read."""

import ast
import base64
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INVENTORY = load("cca_eob_runtime_inventory")
POLICY = load("cca_eob_runtime_permissions")
APP = "/srv/polypbase/app"
SITE = f"{APP}/.venv/lib/python3.13/site-packages"
LOADER = "/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"


def elf(interpreter=None):
    """Minimal little-endian ELF64 with an optional PT_INTERP program header."""
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    if interpreter is None:
        return bytes(header)
    payload = interpreter.encode() + b"\0"
    struct.pack_into("<Q", header, 0x20, 64)
    struct.pack_into("<HH", header, 0x36, 56, 1)
    program = bytearray(56)
    struct.pack_into("<I", program, 0, 3)
    struct.pack_into("<Q", program, 8, 120)
    struct.pack_into("<Q", program, 32, len(payload))
    return bytes(header + program + payload)


def record_hash(data):
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


class FakeFS:
    """Regular files, symlinks and owners; every unlisted ancestor is a root directory."""

    def __init__(self, files, links=None, owners=None):
        self.files, self.links, self.owners = dict(files), dict(links or {}), dict(owners or {})

    def lstat(self, path):
        kind = ("link" if path in self.links else "file" if path in self.files else "dir"
                if any(PurePosixPath(name).is_relative_to(path) for name in (*self.files, *self.links))
                else "missing")
        return SimpleNamespace(kind=kind, uid=self.owners.get(path, 0))

    def readlink(self, path):
        return self.links[path]

    def read(self, path):
        return self.files[path]

    def listdir(self, path):
        return sorted({PurePosixPath(name).relative_to(path).parts[0]
                       for name in (*self.files, *self.links)
                       if PurePosixPath(name).is_relative_to(path) and name != path})


class InventoryCollectorTests(unittest.TestCase):
    def setUp(self):
        django = b"# synthetic django\n"
        self.files = {
            "/usr/bin/python3.13": elf(LOADER),
            LOADER: elf(),
            "/usr/lib/x86_64-linux-gnu/libz.so.1": elf(),
            "/usr/lib/python3.13/os.py": b"# os\n",
            "/usr/lib/python3.13/lib-dynload/_json.cpython-313-x86_64-linux-gnu.so": elf(),
            f"{APP}/.venv/pyvenv.cfg": b"home = /usr/bin\n",
            f"{SITE}/_virtualenv.pth": b"import _virtualenv\n",
            f"{SITE}/django/__init__.py": django,
            f"{SITE}/django/conf/locale/fr/LC_MESSAGES/django.mo": b"mo",
            f"{SITE}/django-5.2.17.dist-info/RECORD": (
                f"django/__init__.py,{record_hash(django)},19\n"
                f"django/conf/locale/fr/LC_MESSAGES/django.mo,{record_hash(b'mo')},2\n"
                "django/unused.py,,0\n"
                "django-5.2.17.dist-info/RECORD,,\n").encode(),
            f"{SITE}/django/unused.py": b"",
            f"{SITE}/sqlparse-0.6.0.dist-info/RECORD": b"sqlparse/__init__.py,,\n",
            f"{SITE}/sqlparse/__init__.py": b"",
            f"{APP}/backend/manage.py": b"# manage\n",
            f"{APP}/backend/locale/fr/LC_MESSAGES/django.mo": b"app mo",
            f"{APP}/backend/__pycache__/manage.cpython-313.pyc": b"pyc",
        }
        self.links = {f"{APP}/.venv/bin/python": "python3", f"{APP}/.venv/bin/python3": "/usr/bin/python3.13"}
        self.document = {
            "schema": INVENTORY.TRACE_SCHEMA, "runtime": INVENTORY.RUNTIME, "uid": 998, "app": APP,
            "no_new_privs": True, "startup": dict(INVENTORY.STARTUP),
            "python": {"executable": f"{APP}/.venv/bin/python", "prefix": f"{APP}/.venv",
                       "base_prefix": "/usr", "version": "3.13.5 (main)",
                       "stdlib": [f"{APP}/.venv/lib/python3.13", "/usr/lib/python3.13"],
                       "site_packages": [f"{APP}/.venv/lib/python3.13/dist-packages", SITE]},
            "modules": [f"{APP}/backend/manage.py", "/usr/lib/python3.13/os.py", f"{SITE}/django/__init__.py",
                        "/usr/lib/python3.13/lib-dynload/_json.cpython-313-x86_64-linux-gnu.so"],
            "opened": [f"{SITE}/django/conf/locale/fr/LC_MESSAGES/django.mo",
                       f"{APP}/backend/locale/fr/LC_MESSAGES/django.mo",
                       f"{APP}/backend/locale/en/LC_MESSAGES/django.mo",
                       "/proc/self/maps"],
            "mapped": ["/usr/bin/python3.13", LOADER, "/usr/lib/x86_64-linux-gnu/libz.so.1"],
            "listed_directories": [SITE, f"{APP}/backend/missing"], "executed": [],
            "directory_access": {SITE: "listable", f"{APP}/backend/missing": "absent"},
            "deny_read": {f"{APP}/backend/.env": True}, "readable_bytecode": [],
        }

    def collect(self, document=None, files=None, links=None, owners=None, **options):
        options.setdefault("accept_aliases", [f"{APP}/.venv/bin/python"])
        fs = FakeFS(self.files if files is None else files, self.links if links is None else links, owners)
        return INVENTORY.collect(fs, self.document if document is None else document, APP,
                                 {"backend/manage.py"}, POLICY.safe_path, "reviewed synthetic trace", **options)

    def entries(self, inventory):
        return {entry["path"]: (entry["origin"], entry["access"]) for entry in inventory["files"]}

    def test_classifies_exact_closure_and_feeds_the_existing_build_contract(self):
        self.document["opened"].append(f"{APP}/.venv/pyvenv.cfg")
        inventory, text = self.collect()
        self.assertEqual(self.entries(inventory), {
            "/usr/bin/python3.13": ("interpreter", "rx"),
            LOADER: ("interpreter", "rx"),
            "/usr/lib/x86_64-linux-gnu/libz.so.1": ("interpreter", "r"),
            f"{APP}/.venv/pyvenv.cfg": ("interpreter", "r"),
            "/usr/lib/python3.13/os.py": ("stdlib", "r"),
            "/usr/lib/python3.13/lib-dynload/_json.cpython-313-x86_64-linux-gnu.so": ("stdlib", "r"),
            f"{SITE}/_virtualenv.pth": ("dependency", "r"),
            f"{SITE}/django/__init__.py": ("dependency", "r"),
            f"{SITE}/django/conf/locale/fr/LC_MESSAGES/django.mo": ("dependency", "r"),
            f"{APP}/backend/locale/fr/LC_MESSAGES/django.mo": ("resource", "r"),
        })
        self.assertEqual(inventory["deny_read"], [f"{APP}/backend/.env"])
        self.assertIn("trace_sha256=", inventory["scope_review"])
        for needle in ("django: 2/4", "sqlparse", "enforced startup:",
                       "backend/locale/en/LC_MESSAGES/django.mo", f"{SITE}/_virtualenv.pth"):
            self.assertIn(needle, text)
        # Resource bytes come from the build root (covered by the permissions tests);
        # here only the collector output must satisfy build's reviewed contract.
        root_digest = "b" * 64
        with patch.object(POLICY, "tracked_sources", return_value=["backend/manage.py"]), \
                patch.object(POLICY, "tracked_files", return_value={"backend/locale/fr/LC_MESSAGES/django.mo"}), \
                patch.object(POLICY, "resource_sha256", return_value=root_digest) as resource, \
                patch.object(POLICY.Path, "read_bytes", return_value=b"# manage\n"), \
                patch.object(POLICY.Path, "is_file", return_value=True), \
                patch.object(POLICY.Path, "is_symlink", return_value=False):
            manifest = POLICY.build(Path("."), APP, json.loads(json.dumps(inventory)))
        self.assertEqual(len(manifest["files"]), len(inventory["files"]) + 1)
        self.assertEqual(resource.call_count, 1)
        built = {entry["path"]: entry for entry in manifest["files"]}
        for entry in inventory["files"]:
            expected = dict(entry, sha256=root_digest) if entry["origin"] == "resource" else entry
            self.assertEqual(built[entry["path"]], expected)

    def test_expected_pyvenv_config_is_startup_whether_observed_or_not(self):
        path = f"{APP}/.venv/pyvenv.cfg"
        expected, _ = self.collect()
        for observed in (False, True):
            with self.subTest(observed=observed):
                document = json.loads(json.dumps(self.document))
                if observed:
                    document["opened"].append(path)  # Captured by the bootstrap during site.main().
                before = json.loads(json.dumps(document))
                inventory, _ = self.collect(document)
                self.assertEqual(document, before)
                self.assertEqual(inventory["files"], expected["files"])
                entry = next(entry for entry in inventory["files"] if entry["path"] == path)
                self.assertEqual(entry, {"path": path, "origin": "interpreter", "access": "r",
                                         "sha256": hashlib.sha256(self.files[path]).hexdigest()})
                trace_hash = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
                self.assertIn(f"trace_sha256={trace_hash}", inventory["scope_review"])

    def test_forged_external_prefix_is_refused(self):
        document = json.loads(json.dumps(self.document))
        document["python"]["prefix"] = "/opt/other"
        path = "/opt/other/pyvenv.cfg"
        # Without an opened event, the forged prefix alone used to admit this file.
        with self.assertRaisesRegex(ValueError, "Python prefix"):
            self.collect(document, files=dict(self.files, **{path: b"home = /usr/bin\n"}))

    def test_mapped_pyvenv_config_is_refused_before_event_classification_collapses(self):
        path = f"{APP}/.venv/pyvenv.cfg"
        for other_events in ((), ("opened",), ("modules",), ("opened", "modules")):
            for options in ({}, {"excludes": [path]}, {"extras": [f"interpreter:r:{path}"]}):
                with self.subTest(other_events=other_events, options=options):
                    document = json.loads(json.dumps(self.document))
                    document["mapped"].append(path)
                    for field in other_events:
                        document[field].append(path)
                    before = json.loads(json.dumps(document))
                    with self.assertRaisesRegex(ValueError, "mapped.*pyvenv.cfg"):
                        self.collect(document, **options)
                    self.assertEqual(document, before)

    def test_invalid_python_prefixes_are_refused_without_normalizing(self):
        expected = f"{APP}/.venv"
        for prefix in (None, 42, [], {}, "", ".venv", expected + "/", expected + "/.",
                       expected + "/../.venv", f"{APP}//.venv", "//srv/polypbase/app/.venv",
                       expected.replace("/", "\\"), "/usr", f"{APP}/venv-alias"):
            with self.subTest(prefix=prefix):
                document = json.loads(json.dumps(self.document))
                document["python"]["prefix"] = prefix
                links = dict(self.links, **{f"{APP}/venv-alias": expected})
                with self.assertRaises(ValueError):
                    self.collect(document, links=links)
        document = json.loads(json.dumps(self.document))
        del document["python"]["prefix"]
        with self.assertRaises(ValueError):
            self.collect(document)
        document = json.loads(json.dumps(self.document))
        document["python"]["base_prefix"] = expected
        with self.assertRaisesRegex(ValueError, "Python prefix"):
            self.collect(document)

    def test_expected_python_prefix_cannot_be_a_symlink_even_if_reviewed(self):
        prefix, target = f"{APP}/.venv", "/opt/environment"
        files = {path.replace(prefix, target): data for path, data in self.files.items()}
        links = {path.replace(prefix, target): value for path, value in self.links.items()}
        links[prefix] = target
        with self.assertRaisesRegex(ValueError, "Python prefix"):
            self.collect(files=files, links=links,
                         accept_aliases=[f"{prefix}/bin/python", f"{prefix}/pyvenv.cfg"])

    def test_mapped_alias_to_expected_pyvenv_config_is_refused(self):
        path, alias = f"{APP}/.venv/pyvenv.cfg", "/opt/runtime/pyvenv.cfg"
        document = json.loads(json.dumps(self.document))
        document["opened"].append(path)
        document["mapped"].append(alias)
        with self.assertRaisesRegex(ValueError, "mapped.*pyvenv.cfg"):
            self.collect(document, links=dict(self.links, **{alias: path}),
                         accept_aliases=[f"{APP}/.venv/bin/python", alias], excludes=[alias])

    def test_unrelated_unclassified_mapped_file_is_still_refused(self):
        path = "/opt/runtime/unclassified.dat"
        for opened in (False, True):
            with self.subTest(opened=opened):
                document = json.loads(json.dumps(self.document))
                document["opened"].append(f"{APP}/.venv/pyvenv.cfg")
                document["mapped"].append(path)
                if opened:
                    document["opened"].append(path)
                with self.assertRaisesRegex(ValueError, "unclassified runtime file"):
                    self.collect(document, files=dict(self.files, **{path: b"not ELF"}))

    def test_observed_pyvenv_config_does_not_allow_unexpected_files(self):
        for path, message in (
            (f"{APP}/.venv/bin/pyvenv.cfg", "outside backend and site-packages"),
            (f"{APP}/other/pyvenv.cfg", "outside backend and site-packages"),
            ("/opt/other/pyvenv.cfg", "unclassified runtime file"),
            ("/opt/other/runtime.dat", "unclassified runtime file"),
        ):
            with self.subTest(path=path):
                document = json.loads(json.dumps(self.document))
                document["opened"].extend([f"{APP}/.venv/pyvenv.cfg", path])
                with self.assertRaisesRegex(ValueError, message):
                    self.collect(document, files=dict(self.files, **{path: b"unexpected"}))

    def test_observed_pyvenv_config_keeps_symlink_restrictions(self):
        path = f"{APP}/.venv/pyvenv.cfg"
        document = json.loads(json.dumps(self.document))
        document["opened"].append(path)
        for target, owners, aliases, message in (
            ("/opt/runtime/pyvenv.cfg", {}, [], "needs --accept-alias"),
            ("/opt/runtime/pyvenv.cfg", {path: 998}, [path], "runtime owns a symlink"),
            ("/opt/runtime/pyvenv.cfg", {f"{APP}/.venv": 998}, [path], "runtime owns a symlink"),
            (f"{APP}/backend/.env", {}, [path], "private or mixed path"),
            (f"{APP}/other/pyvenv.cfg", {}, [path], "outside backend and site-packages"),
            ("/opt/runtime/startup.pyc", {}, [path], "unsupported bytecode or ZIP"),
            ("/opt/runtime/startup.zip", {}, [path], "unsupported bytecode or ZIP"),
        ):
            with self.subTest(target=target, owners=owners):
                files = dict(self.files)
                del files[path]
                files[target] = b"synthetic startup"
                links = dict(self.links, **{path: target})
                with self.assertRaisesRegex(ValueError, message):
                    self.collect(document, files=files, links=links, owners=owners,
                                 accept_aliases=[f"{APP}/.venv/bin/python", *aliases])
        alias = "/opt/runtime/pyvenv.cfg"
        document["opened"].append(alias)
        with self.assertRaisesRegex(ValueError, "unclassified runtime file"):
            self.collect(document, links=dict(self.links, **{alias: path}),
                         accept_aliases=[f"{APP}/.venv/bin/python", alias])

    def test_bytecode_is_a_stop_regardless_of_owner_or_exclusion(self):
        cache = f"{APP}/backend/__pycache__/manage.cpython-313.pyc"
        for uid in (0, 1000):
            with self.subTest(uid=uid), self.assertRaisesRegex(ValueError, "attempted readable bytecode"):
                self.collect(dict(self.document, readable_bytecode=[cache]), owners={cache: uid})
        document = dict(self.document, opened=[cache])
        with self.assertRaisesRegex(ValueError, "unsupported bytecode"):
            self.collect(document, excludes=[cache])

    def test_effective_startup_contract_is_mandatory(self):
        for startup in (None, {}, dict(INVENTORY.STARTUP, pycache_prefix=None),
                        dict(INVENTORY.STARTUP, no_site=0),
                        dict(INVENTORY.STARTUP, site_initialized=False)):
            with self.subTest(startup=startup), self.assertRaisesRegex(ValueError, "startup contract"):
                self.collect(dict(self.document, startup=startup))

    def test_output_is_deterministic(self):
        self.assertEqual(self.collect(), self.collect())

    def test_record_mismatch_is_refused(self):
        files = dict(self.files, **{f"{SITE}/django/__init__.py": b"# tampered\n"})
        with self.assertRaisesRegex(ValueError, "differs from its distribution RECORD"):
            self.collect(files=files)

    def test_unreviewed_alias_and_runtime_owned_link_are_refused(self):
        with self.assertRaisesRegex(ValueError, "needs --accept-alias"):
            self.collect(accept_aliases=[])
        with self.assertRaisesRegex(ValueError, "runtime owns a symlink"):
            self.collect(owners={f"{APP}/.venv/bin/python3": 998})
        with self.assertRaisesRegex(ValueError, "runtime owns a symlink"):
            self.collect(owners={f"{APP}/.venv/bin": 998})

    def test_private_untracked_and_unclassified_files_are_refused(self):
        cases = (
            ("opened", f"{APP}/backend/.env.production", "private or mixed path"),
            ("modules", f"{APP}/backend/local_patch.py", "outside the tracked_code inventory"),
            ("opened", f"{APP}/frontend/dist/index.html", "outside backend and site-packages"),
            ("opened", "/usr/share/zoneinfo/Europe/Paris", "unclassified runtime file"),
            ("mapped", "/usr/lib/locale/locale-archive", "unclassified runtime file"),
        )
        for field, path, message in cases:
            with self.subTest(path=path):
                document = json.loads(json.dumps(self.document))
                document[field].append(path)
                with self.assertRaisesRegex(ValueError, message):
                    self.collect(document, files=dict(self.files, **{path: b"synthetic"}))

    def test_unclassified_file_needs_an_explicit_reviewer_decision(self):
        zone = "/usr/share/zoneinfo/Europe/Paris"
        document = json.loads(json.dumps(self.document))
        document["opened"].append(zone)
        files = dict(self.files, **{zone: b"TZif"})
        inventory, _ = self.collect(document, files=files, extras=[f"stdlib:r:{zone}"])
        self.assertEqual(self.entries(inventory)[zone], ("stdlib", "r"))
        inventory, text = self.collect(document, files=files, excludes=[zone])
        self.assertNotIn(zone, self.entries(inventory))
        self.assertIn(zone, text)
        for extra, message in (
            (f"tracked_code:r:{zone}", "Invalid --extra"), (f"stdlib:rw:{zone}", "Invalid --extra"),
            ("stdlib:r:/usr/share/zoneinfo/missing", "existing real regular file"),
            (f"stdlib:r:{APP}/.venv/bin/python", "existing real regular file"),
            (f"interpreter:r:{APP}/backend/.env", "Secret or mixed private paths"),
            (f"stdlib:r:{SITE}/django/__init__.py", "conflicts with the traced classification"),
            (f"dependency:r:{APP}/frontend/dist/index.html", "outside backend/site-packages"),
        ):
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, message):
                self.collect(document, files=dict(files, **{f"{APP}/backend/.env": b"secret",
                                                            f"{APP}/frontend/dist/index.html": b"html"}),
                             extras=[f"stdlib:r:{zone}", extra])

    def test_include_distribution_adds_its_whole_record(self):
        inventory, _ = self.collect(include_distributions=["Django"])
        self.assertIn(f"{SITE}/django/unused.py", self.entries(inventory))
        with self.assertRaisesRegex(ValueError, "Unknown installed distribution"):
            self.collect(include_distributions=["numpy"])

    def test_trace_identity_and_denial_probes_are_mandatory(self):
        for field, value, message in (
            ("uid", 0, "non-root runtime"), ("runtime", "polypbase", "Unexpected trace"),
            ("app", "/tmp/other", "another checkout"), ("no_new_privs", False, "NoNewPrivs"),
            ("deny_read", {}, "no private read-denial probe"),
            ("deny_read", {f"{APP}/backend/.env": False}, "can read private probes"),
        ):
            document = dict(self.document, **{field: value})
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, message):
                self.collect(document)

    def test_directories_listed_by_the_runtime_must_be_effectively_listable(self):
        _, text = self.collect()
        self.assertIn(f"must list (r+x), verified listable as the runtime; "
                      f"CCA_EOB_RUNTIME_PERMISSIONS_OK checks x only: 1\n  {SITE}\n", text)
        self.assertIn(f"INFO: absent directories probed by the runtime: 1\n  {APP}/backend/missing", text)
        for state in ("denied", "error"):
            document = dict(self.document, directory_access={SITE: state, f"{APP}/backend/missing": "absent"})
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, "STOP: the runtime cannot list"):
                self.collect(document)
        for access in (None, {SITE: "listable"}, []):
            document = dict(self.document, directory_access=access)
            with self.subTest(access=access), self.assertRaisesRegex(ValueError, "lacks the effective"):
                self.collect(document)

    def test_trace_relists_directories_as_the_runtime_and_stops_on_denial(self):
        source = Path(INVENTORY.__file__).read_text(encoding="utf-8")
        trace = source[source.index("def trace("):source.index("class LinuxFS")]
        self.assertIn("os.listdir(path)", trace)
        self.assertIn('"denied" if isinstance(error, PermissionError)', trace)
        self.assertIn("raise SystemExit(3)", trace)
        self.assertLess(trace.index("sys.stdout.write"), trace.index("raise SystemExit(3)"))

    def test_elf_interpreter_parser(self):
        self.assertEqual(INVENTORY.elf_interpreter(elf(LOADER)), LOADER)
        self.assertIsNone(INVENTORY.elf_interpreter(elf()))
        self.assertIsNone(INVENTORY.elf_interpreter(b"#!/bin/sh\n"))

    def test_resolve_reports_each_link_with_owners(self):
        fs = FakeFS(self.files, self.links, {f"{APP}/.venv/bin": 1000})
        real, chain = INVENTORY.resolve(fs, f"{APP}/.venv/bin/python")
        self.assertEqual(real, "/usr/bin/python3.13")
        self.assertEqual(chain, [(f"{APP}/.venv/bin/python", 0, 1000), (f"{APP}/.venv/bin/python3", 0, 1000)])

    def test_lazy_runtime_imports_are_traced(self):
        """Function-level imports of the runtime path must be imported by trace."""
        backend = Path(__file__).resolve().parents[2] / "backend"
        sources = [backend / "apps/cultures/management/commands/cca_eob_production.py",
                   *sorted((backend / "apps/cultures/historical_2026").glob("*.py")),
                   *sorted((backend / "config").glob("cca_eob_*.py")),
                   backend / "config/pca_protected_artifacts.py", backend / "config/database_backup.py"]
        lazy = set()
        for source in sources:
            if source.name.startswith("test"):
                continue
            for function in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(function):
                    if isinstance(node, ast.Import):
                        lazy.update(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.level == 0:
                        lazy.update(f"{node.module}.{alias.name}" if node.module == "config"
                                    else node.module for alias in node.names)
        expected = set(INVENTORY.LAZY_MODULES) | set(INVENTORY.QA_ONLY_MODULES) | {"django.conf", "django.db"}
        self.assertLessEqual(lazy, expected)
        qa_guard = "if settings.SETTINGS_MODULE == \"config.historical_temperature_pgqa_settings\":"
        self.assertIn(qa_guard, (backend / "apps/cultures/historical_2026/cca_eob_production.py").read_text(
            encoding="utf-8"))

    def test_collector_never_changes_permissions_or_connects(self):
        source = Path(INVENTORY.__file__).read_text(encoding="utf-8")
        for forbidden in ("setfacl", "chmod", "chown", "ensure_connection", "cursor(", "subprocess.run"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
