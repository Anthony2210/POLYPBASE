"""Real CPython subprocess fixtures; no dependencies, network, database or live ACLs."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "backend/cca_eob_runtime.py"
FLAGS = ["-I", "-S", "-B", "-X", "pycache_prefix=/dev/null"]


@unittest.skipUnless(sys.implementation.name == "cpython" and sys.version_info[:2] == (3, 13),
                     "requires real CPython 3.13")
class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_python(self, code, *, secure=True, executable=None, flags=None):
        if secure:
            code = (f"scope={{'__name__':'cca_bootstrap'}}; "
                    f"exec(compile(open({str(BOOTSTRAP)!r}).read(), 'bootstrap', 'exec'), scope); "
                    "evidence=scope['install'](); " + code)
        return subprocess.run([str(executable or sys.executable), *(flags if flags is not None else
                               FLAGS if secure else ["-I", "-B"]), "-c", code],
                              capture_output=True, text=True, timeout=30, cwd=self.root)

    def forged_cache(self, mode):
        source = self.root / "victim.py"
        source.write_text("value = 'SOURCE'\n", encoding="utf-8")
        # Losing this old mtime during a fixture copy must fail deterministically.
        os.utime(source, (1_700_000_000, 1_700_000_000))
        cache = Path(importlib.util.cache_from_source(str(source)))
        py_compile.compile(str(source), cfile=str(cache), doraise=True, invalidation_mode=mode)
        # Preserve the entire real header (timestamp/size or source hash).
        import marshal
        cache.write_bytes(cache.read_bytes()[:16] + marshal.dumps(compile("value = 'CACHE'", str(source), "exec")))
        return source, cache

    def test_old_flags_consume_forged_timestamp_and_both_hash_caches(self):
        for mode in py_compile.PycInvalidationMode:
            with self.subTest(mode=mode):
                self.forged_cache(mode)
                code = f"import sys; sys.path.insert(0,{str(self.root)!r}); import victim; print(victim.value)"
                old = self.run_python(code, secure=False)
                self.assertEqual((old.returncode, old.stdout.strip()), (0, "CACHE"), old.stderr)
                new = self.run_python(code)
                self.assertEqual((new.returncode, new.stdout.strip()), (0, "SOURCE"), new.stderr)

    def test_prefix_alone_redirects_standard_cache_reads(self):
        self.forged_cache(py_compile.PycInvalidationMode.TIMESTAMP)
        result = self.run_python(
            f"import sys; sys.path.insert(0,{str(self.root)!r}); import victim; print(victim.value)",
            secure=False, flags=FLAGS)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "SOURCE"), result.stderr)

    def test_sourceless_zip_and_direct_bytecode_loaders_stop(self):
        source, cache = self.forged_cache(py_compile.PycInvalidationMode.TIMESTAMP)
        legacy = self.root / "legacy.pyc"
        shutil.copyfile(cache, legacy)
        archive = self.root / "modules.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.write(legacy, "packed.pyc")
        source.unlink()
        cases = [
            f"import sys; sys.path.insert(0,{str(self.root)!r}); import legacy",
            f"import sys; sys.path.insert(0,{str(archive)!r}); import packed",
            f"import importlib.machinery as m; m.SourcelessFileLoader('legacy',{str(legacy)!r}).get_code('legacy')",
            f"import zipimport; zipimport.zipimporter({str(archive)!r}).get_code('packed')",
            f"import marshal; marshal.loads(open({str(legacy)!r},'rb').read()[16:])",
        ]
        for code in cases:
            with self.subTest(code=code):
                result = self.run_python(code + "; print('DISPATCHED')")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("STOP:", result.stderr)
                self.assertNotIn("DISPATCHED", result.stdout)

    def test_custom_loader_and_source_subclass_are_rejected(self):
        code = """
import sys, importlib.machinery as m, importlib.util as u
class Loader:
    def create_module(self, spec): return None
    def exec_module(self, module): print('UNTRUSTED')
class Finder:
    def find_spec(self, name, path=None, target=None):
        if name == 'custom': return u.spec_from_loader(name, Loader())
sys.meta_path.insert(0, Finder())
import custom
"""
        result = self.run_python("exec(" + repr(code) + ")")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported import loader", result.stderr)
        self.assertNotIn("UNTRUSTED", result.stdout)
        result = self.run_python("exec(" + repr("import importlib.machinery as m\n"
                                 "class Sub(m.SourceFileLoader): pass\n"
                                 "Sub('x','missing.py').get_code('x')") + ")")
        self.assertIn("unsupported source loader subclass", result.stderr)

    def make_venv(self):
        venv.EnvBuilder(with_pip=False).create(self.root / ".venv")
        executable = self.root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        site = (self.root / ".venv/Lib/site-packages" if os.name == "nt" else
                self.root / ".venv/lib/python3.13/site-packages")
        return executable, site

    def test_real_venv_pth_startup_is_source_only_and_denial_is_not_swallowed(self):
        executable, site = self.make_venv()
        initialize = f"import sys; sys._cca_eob_runtime['initialize_site']({str(self.root / '.venv')!r}); "
        with self.subTest(startup="hook-free"):
            self.assertFalse((site / "_virtualenv.py").exists())
            self.assertFalse((site / "_virtualenv.pth").exists())
            plain = self.run_python(initialize + "assert '_virtualenv' not in sys.modules; "
                                    "print(evidence()['site_initialized'])", executable=executable)
            self.assertEqual((plain.returncode, plain.stdout.strip()), (0, "True"), plain.stderr)
        source, cache = self.forged_cache(py_compile.PycInvalidationMode.TIMESTAMP)
        installed_source = site / "victim.py"
        shutil.copy2(source, installed_source)
        (site / "__pycache__").mkdir(exist_ok=True)
        installed_cache = Path(importlib.util.cache_from_source(str(installed_source)))
        shutil.copyfile(cache, installed_cache)
        header = installed_cache.read_bytes()[:16]
        self.assertEqual(header[:4], importlib.util.MAGIC_NUMBER)
        self.assertEqual(int.from_bytes(header[4:8], "little"), 0)
        self.assertEqual(int.from_bytes(header[8:12], "little"), int(installed_source.stat().st_mtime))
        self.assertEqual(int.from_bytes(header[12:16], "little"), installed_source.stat().st_size)
        (site / "fixture.pth").write_text("import victim; print('PTH:' + victim.value)\n", encoding="utf-8")
        observe_source = "import victim; print(victim.__file__)"
        old = self.run_python(observe_source, secure=False, executable=executable)
        self.assertEqual(old.returncode, 0, old.stderr)
        self.assertIn("PTH:CACHE", old.stdout)
        self.assertNotIn("PTH:SOURCE", old.stdout)
        self.assertIn(str(installed_source), old.stdout)
        new = self.run_python(initialize + "print(evidence()); " + observe_source, executable=executable)
        self.assertEqual(new.returncode, 0, new.stderr)
        self.assertIn("PTH:SOURCE", new.stdout)
        self.assertNotIn("PTH:CACHE", new.stdout)
        self.assertIn(str(installed_source), new.stdout)
        self.assertEqual(installed_cache.read_bytes(), cache.read_bytes())
        self.assertIn(str(self.root / ".venv"), self.run_python(
            initialize + "print(sys.prefix)", executable=executable).stdout)
        shutil.copyfile(cache, site / "legacy.pyc")
        (site / "fixture.pth").write_text("import legacy\n", encoding="utf-8")
        blocked = self.run_python(initialize + "print('DISPATCHED')", executable=executable)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("STOP:", blocked.stderr)
        self.assertNotIn("DISPATCHED", blocked.stdout)

    def test_source_loader_never_requests_cache_even_without_prefix_defense(self):
        source, _ = self.forged_cache(py_compile.PycInvalidationMode.TIMESTAMP)
        code = f"""
import sys
loader_type = sys.modules['_frozen_importlib_external'].SourceFileLoader
loader = loader_type('victim', {str(source)!r})
paths = []
original = loader.get_data
def read(path):
    paths.append(path)
    return original(path)
loader.get_data = read
sys.pycache_prefix = None
namespace = {{}}
exec(loader.get_code('victim'), namespace)
assert namespace['value'] == 'SOURCE'
assert paths == [{str(source)!r}], paths
print('SOURCE_ONLY')
"""
        result = self.run_python("exec(" + repr(code) + ")")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "SOURCE_ONLY"), result.stderr)

    def test_frozen_imports_allow_only_builtin_data(self):
        result = self.run_python("import site, importlib.machinery; print('FROZEN_OK')")
        self.assertEqual(result.returncode, 0, result.stderr)
        code = """
import sys
b = sys.modules['_frozen_importlib']
b._call_with_frames_removed(b._imp.get_frozen_object, '__hello__', b'untrusted')
print('DISPATCHED')
"""
        result = self.run_python("exec(" + repr(code) + ")")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("DISPATCHED", result.stdout)

    def test_importing_site_is_not_initialization_evidence(self):
        result = self.run_python("import site; print(evidence()['site_initialized'])")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "False"), result.stderr)

    def test_pth_source_compile_failure_is_fatal(self):
        executable, site = self.make_venv()
        (site / 'broken.py').write_text('invalid syntax !!!', encoding='utf-8')
        (site / 'fixture.pth').write_text('import broken\n', encoding='utf-8')
        result = self.run_python("import site; site.main(); print('DISPATCHED')", executable=executable)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('STOP: cannot compile source', result.stderr)
        self.assertNotIn('DISPATCHED', result.stdout)

    def test_missing_flags_and_policy_drift_stop(self):
        for flags in (["-I", "-B"], ["-I", "-S", "-B"], ["-S", "-B", "-X", "pycache_prefix=/dev/null"]):
            result = self.run_python("print('DISPATCHED')", flags=flags)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("STOP:", result.stderr)
        result = self.run_python("import sys; sys.pycache_prefix = None; evidence()")
        self.assertIn("policy drift", result.stderr)

    def make_launcher_app(self):
        executable, _ = self.make_venv()
        backend = self.root / "backend"
        backend.mkdir()
        shutil.copyfile(BOOTSTRAP, backend / BOOTSTRAP.name)
        management = backend / "django/core/management"
        management.mkdir(parents=True)
        for directory in (backend / "django", backend / "django/core", management):
            (directory / "__init__.py").write_text("", encoding="utf-8")
        (management / "__init__.py").write_text(
            "def execute_from_command_line(argv):\n"
            " import sys, json\n"
            " print(json.dumps(sys._cca_eob_runtime['evidence'](), sort_keys=True))\n"
            " print(argv[1:])\n", encoding="utf-8")
        return executable, backend

    @unittest.skipUnless(sys.platform == "linux", "full launcher requires Linux")
    def test_full_trace_plan_apply_launcher_equivalence_without_django_or_database(self):
        executable, backend = self.make_launcher_app()
        outputs = []
        for action in ("trace", "plan", "apply"):
            result = subprocess.run([str(executable), *FLAGS, str(backend / BOOTSTRAP.name), action],
                                    input="import sys,json; print(json.dumps(sys._cca_eob_runtime['evidence'](),sort_keys=True))",
                                    text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs.append(json.loads(result.stdout.splitlines()[0]))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], outputs[2])

    def test_abbreviated_django_settings_and_pythonpath_are_forbidden(self):
        for option in ("--settings", "--sett", "--settings=config.settings", "--sett=config.settings",
                       "--pythonpath", "--pythonpat", "--pythonpat=/unreviewed"):
            result = self.run_python(f"scope['check_command_arguments']([{option!r}]); print('DISPATCHED')")
            with self.subTest(option=option):
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("cannot be overridden", result.stderr)
                self.assertNotIn("DISPATCHED", result.stdout)
        result = self.run_python("scope['check_command_arguments'](['--production','--plan-file','plan.json']); print('OK')")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "OK"), result.stderr)

    @unittest.skipUnless(sys.platform == "linux", "staged bootstrap requires Linux")
    def test_external_reviewed_bootstrap_uses_live_venv_and_trace_only_app_override(self):
        executable, backend = self.make_launcher_app()
        staging = self.root / "reviewed-bootstrap"
        staging.mkdir()
        bootstrap = staging / BOOTSTRAP.name
        shutil.copyfile(BOOTSTRAP, bootstrap)
        # Keep Django available at the external launcher's effective backend too.
        shutil.copytree(backend / "django", staging / "django")
        dispatcher = staging / "django/core/management/__init__.py"
        dispatcher.write_text(
            "def execute_from_command_line(argv):\n"
            " print('SYNTHETIC_DJANGO_DISPATCH', argv[2])\n", encoding="utf-8")
        shutil.copyfile(dispatcher, backend / "django/core/management/__init__.py")
        code = ("import sys,json; print(json.dumps({'startup':sys._cca_eob_runtime['evidence'](),"
                "'argv':sys.argv,'bootstrap':sys.modules['__main__'].__file__}))")
        result = subprocess.run([str(executable), *FLAGS, str(bootstrap), "trace", "--app", str(self.root)],
                                input=code, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(document["argv"], ["<cca-eob-trace>", "trace", "--app", str(self.root)])
        self.assertEqual(document["bootstrap"], str(bootstrap))
        self.assertTrue(document["startup"]["site_initialized"])
        self.assertNotIn("SYNTHETIC_DJANGO_DISPATCH", result.stdout)
        # Positive controls prove that an unavailable Django cannot mask a missing guard.
        # Plan/apply controls use the normal launcher; the external launcher is trace-only.
        for action in ("plan", "apply"):
            with self.subTest(control=action):
                result = subprocess.run([str(executable), *FLAGS, str(backend / BOOTSTRAP.name), action],
                                        text=True, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("SYNTHETIC_DJANGO_DISPATCH " + action, result.stdout)

        site = self.root / ".venv/lib/python3.13/site-packages"
        (site / "site-initialization-probe.pth").write_text(
            "import sys; sys.stderr.write('SITE_INITIALIZED\\n')\n", encoding="utf-8")
        result = subprocess.run([str(executable), *FLAGS, str(bootstrap), "trace", "--app", str(self.root)],
                                input=code, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SITE_INITIALIZED", result.stderr)
        self.assertTrue(json.loads(result.stdout)["startup"]["site_initialized"])
        for launcher in (bootstrap, backend / BOOTSTRAP.name):
            for action in ("plan", "apply"):
                for override in (["--app", str(self.root)], ["--app=" + str(self.root)]):
                    for preceding in ([], ["--production", "--organization-id", "1"]):
                        with self.subTest(launcher=launcher, action=action,
                                          override=override, preceding=preceding):
                            result = subprocess.run(
                                [str(executable), *FLAGS, str(launcher), action, *preceding, *override],
                                text=True, capture_output=True, timeout=30)
                            self.assertEqual(result.returncode, 4, result.stderr)
                            self.assertIn("STOP: --app is only permitted for trace", result.stderr)
                            self.assertNotIn("SITE_INITIALIZED", result.stdout + result.stderr)
                            self.assertNotIn("SYNTHETIC_DJANGO_DISPATCH", result.stdout + result.stderr)
                            self.assertNotIn("cca_eob_production", result.stdout)
                            self.assertNotIn("ModuleNotFoundError", result.stderr)


class ProjectVenvTests(unittest.TestCase):
    def test_real_django_and_virtualenv_hook_without_network_or_database(self):
        executable = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not executable.is_file():
            self.skipTest("requires the isolated project uv venv and locked dependencies")
        code = f"""
import sys
scope = {{'__name__': 'cca_bootstrap'}}
exec(compile(open({str(BOOTSTRAP)!r}).read(), 'bootstrap', 'exec'), scope)
scope['install']()
def offline(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'sqlite3.connect'):
        raise BaseException('STOP: test forbids network/database access')
sys.addaudithook(offline)
import os
site_packages = os.path.join({str(ROOT / '.venv')!r},
                             'Lib/site-packages' if os.name == 'nt' else 'lib/python3.13/site-packages')
hook_source = os.path.join(site_packages, '_virtualenv.py')
hook_pth = os.path.join(site_packages, '_virtualenv.pth')
has_legacy_hook = os.path.lexists(hook_source) or os.path.lexists(hook_pth)
assert '_virtualenv' not in sys.modules, 'uv hook loaded before initialize_site'
if has_legacy_hook:
    assert os.path.isfile(hook_source) and os.path.isfile(hook_pth), 'incomplete uv startup hook pair'
    with open(hook_pth, encoding='utf-8') as stream:
        assert stream.read().strip() == 'import _virtualenv', 'unexpected uv startup hook pth'
sys._cca_eob_runtime['initialize_site']({str(ROOT / '.venv')!r})
if has_legacy_hook:
    hook = sys.modules.get('_virtualenv')
    assert hook is not None, 'uv startup hook was not loaded'
    assert os.path.realpath(hook.__file__) == os.path.realpath(hook_source), 'unexpected uv hook origin'
    assert any(type(finder) is hook._Finder for finder in sys.meta_path), 'uv hook finder was not installed'
    from types import SimpleNamespace
    class Distribution:
        def parse_config_files(self):
            self.options = {{'prefix': ('fixture', '/outside'), 'unrelated': ('fixture', 'keep')}}
            for key in ('purelib', 'platlib', 'headers', 'scripts', 'data'):
                self.options['install_' + key] = ('fixture', '/outside')
            return 'parsed'
        def get_option_dict(self, name):
            assert name == 'install'
            return self.options
    hook.patch_dist(SimpleNamespace(Distribution=Distribution))
    distribution = Distribution()
    assert distribution.parse_config_files() == 'parsed'
    assert distribution.options == {{
        'prefix': (hook.VIRTUALENV_PATCH_FILE, os.path.abspath(sys.prefix)),
        'unrelated': ('fixture', 'keep'),
    }}, 'uv hook did not constrain installation paths to the venv'
else:
    assert '_virtualenv' not in sys.modules, 'unexpected uv startup hook in hook-free venv'
sys.path.insert(0, {str(ROOT / 'backend')!r})
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.cca_eob_production_settings'
sys.argv = ['manage.py', 'cca_eob_production']
import django
django.setup()
from django.core.management import load_command_class
from django.db import connections
parser = load_command_class('apps.cultures', 'cca_eob_production').create_parser('manage.py', 'cca_eob_production')
arguments = ['plan', '--organization-id', '1', '--actor-id', '1', '--target', 'fixture.json',
             '--workbook', 'fixture.xlsx', '--plan-file', 'plan.json']
for option, attribute, value in (('--sett', 'settings', 'config.settings'),
                                  ('--pythonpat', 'pythonpath', '/unreviewed')):
    assert getattr(parser.parse_args(arguments + [option + '=' + value]), attribute) == value
    try:
        scope['check_command_arguments']([option + '=' + value])
    except scope['RuntimeStop']:
        pass
    else:
        raise AssertionError('Django override abbreviation was accepted by the launcher')
assert connections['default'].connection is None
assert os.environ['PYTHON_DOTENV_DISABLED'] == '1'
import json
print(json.dumps(sys._cca_eob_runtime['evidence'](), sort_keys=True))
"""
        environment = {name: os.environ[name] for name in ("SystemRoot", "WINDIR") if name in os.environ}
        environment["PYTHON_DOTENV_DISABLED"] = "1"
        result = subprocess.run([str(executable), *FLAGS, "-c", code], env=environment,
                                text=True, capture_output=True, timeout=60, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(document["contract"], "cca_eob_source_only_v1")
        self.assertTrue(document["site_initialized"])


class ScopeTests(unittest.TestCase):
    def test_bootstrap_procedure_pins_the_exact_reviewed_lf_tool_sources(self):
        procedure = (ROOT / 'docs/cca-eob-runtime-manifest-procedure.md').read_text(encoding='utf-8')
        for relative in ('backend/cca_eob_runtime.py', 'deploy/scripts/cca_eob_runtime_inventory.py',
                         'deploy/scripts/cca_eob_runtime_permissions.py'):
            digest = hashlib.sha256((ROOT / relative).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            self.assertIn(digest + '  ' + relative, procedure)

    def test_ordinary_entry_points_are_not_bootstrapped(self):
        self.assertNotIn("cca_eob_runtime", (ROOT / "backend/manage.py").read_text())
        self.assertNotIn("cca_eob_runtime", (ROOT / "backend/config/wsgi.py").read_text())

    def test_dedicated_settings_refuse_old_entry_point(self):
        code = (ROOT / "backend/config/cca_eob_production_settings.py").read_text()
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code],
                                text=True, capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dedicated cca_eob_runtime.py", result.stderr)


if __name__ == "__main__":
    unittest.main()
