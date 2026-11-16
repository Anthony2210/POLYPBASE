"""Offline, subprocess-only checks for the history revision PostgreSQL boundary.

The settings are copied to an isolated package with stub application settings and
Django connections. No application settings, dotenv, database driver or socket is
used, even when this suite runs on a developer machine without Django installed.
"""

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "backend/config/history_revision_ci_settings.py"
WORKFLOW_PATH = ROOT / ".github/workflows/history-revision-postgres.yml"
ENVIRONMENT = {
    "GITHUB_ACTIONS": "true",
    "RUNNER_ENVIRONMENT": "github-hosted",
    "RUNNER_OS": "Linux",
    "GITHUB_EVENT_NAME": "push",
    "GITHUB_REF": "refs/heads/test/history-revision-postgres-qa",
    "HR_CI_POSTGRES": "1",
    "PYTHON_DOTENV_DISABLED": "1",
    "POLYPBASE_TEST_POSTGRES": "0",
    "POSTGRES_DB": "",
}

# All database behavior below is a test double; a socket tripwire adds a second
# boundary if an import or implementation accidentally attempts network access.
CHILD = r'''
import importlib
import json
import os
from pathlib import Path
import socket
import sys
import types
import unittest
import unittest.mock
import io
from contextlib import contextmanager
from copy import deepcopy

platform_environment = {name: value for name, value in os.environ.items()
                        if name.upper() in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "TEMP", "TMP"}}
os.environ.clear()
os.environ.update(platform_environment)
os.environ.update(payload["environment"])
sys.path.insert(0, payload["directory"])
class ForbiddenSocket(socket.socket):
    def __init__(self, *args, **kwargs):
        raise AssertionError("Offline settings tests must never open sockets")
socket.socket = ForbiddenSocket

def module(name):
    value = types.ModuleType(name)
    sys.modules[name] = value
    return value

class ImproperlyConfigured(Exception):
    pass

# Database guard tests keep their exact import stubs; collection tests can also
# exercise the installed Django runner, with no application settings or DB setup.
if payload.get("real_django"):
    from django.conf import settings as django_settings
    django_settings.configure(SECRET_KEY="offline", DATABASES={}, INSTALLED_APPS=[])
    from django.core.exceptions import ImproperlyConfigured
    from django.test.runner import DiscoverRunner as RealDiscoverRunner
else:
    module("django")
    module("django.core")
    module("django.core.exceptions").ImproperlyConfigured = ImproperlyConfigured
    module("django.db").connections = {}
    module("django.test")
class DiscoverRunner:
    test_runner = unittest.TextTestRunner
    test_loader = unittest.TestLoader()
    def build_suite(self, test_labels=None, **kwargs):
        return self.test_loader.loadTestsFromNames(test_labels or [])
    def get_test_runner_kwargs(self):
        return {"stream": io.StringIO(), "verbosity": 0, "resultclass": None,
                        "failfast": getattr(self, "failfast", False)}
    def run_suite(self, suite, **kwargs):
        return self.test_runner(**self.get_test_runner_kwargs()).run(suite)
    def __init__(self, *, keepdb=False, parallel=0, interactive=False, verbosity=0):
        self.keepdb, self.parallel, self.interactive = keepdb, parallel, interactive
    def suite_result(self, suite, result, **kwargs):
        return len(result.failures) + len(result.errors) + len(result.unexpectedSuccesses)
    def setup_databases(self, **kwargs):
        connection.creation._create_test_db(2, not self.interactive, keepdb=self.keepdb)
        connection.settings_dict["NAME"] = settings.TEST_DATABASE_NAME
        connection.close()
        connection.connect()
        events.append("migrate")
        return "old-config"
    def teardown_databases(self, old_config, **kwargs):
        assert old_config == "old-config"
        connection.close()
        connection.settings_dict["NAME"] = settings.DATABASE_NAME
        with connection.creation._nodb_cursor():
            events.append("drop")
if payload.get("real_django"):
    DiscoverRunner = RealDiscoverRunner
else:
    module("django.test.runner").DiscoverRunner = DiscoverRunner

try:
    settings = importlib.import_module("config.history_revision_ci_settings")
except ImproperlyConfigured as error:
    print(json.dumps({"rejected": True, "error": str(error),
                      "base_imported": Path(payload["directory"], "base-imported").exists()}))
    sys.exit(0)

# Exercise runner and identity guards with in-memory cursor/connection doubles.
if payload.get("real_django"):
    settings.connections = {}
events = []
rows = {}
existing_test_database = False
create_error = None
create_parameters = []
class Cursor:
    def __init__(self, connection):
        self.connection = connection
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def execute(self, sql, parameters=None):
        assert sql.lstrip().startswith("SELECT "), "Offline cursor permits read-only checks only"
        self.existence_check = parameters is not None
        if self.existence_check:
            assert sql == "SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s"
            assert parameters == [settings.TEST_DATABASE_NAME]
            assert self.connection.settings_dict["NAME"] == settings.DATABASE_NAME
            events.append("check_test_absent")
        else:
            assert "shobj_description" in sql and "current_database()" in sql
            events.append("identity:" + self.connection.settings_dict["NAME"])
    def fetchone(self):
        if self.existence_check:
            return (1,) if existing_test_database else None
        name = self.connection.settings_dict["NAME"]
        return rows.get(name, (name, settings.DATABASE_NAME, settings.DATABASE_NAME, 5432,
                              180001, settings.SERVICE_MARKER if name == settings.DATABASE_NAME else None))

class Creation:
    def __init__(self, connection):
        self.connection = connection
    def _nodb_cursor(self):
        raise AssertionError("Django's unmarked postgres maintenance connection is forbidden")
    def _get_test_db_name(self):
        return self.connection.settings_dict["TEST"]["NAME"]
    def sql_table_creation_suffix(self):
        events.append("creation_suffix")
        return "WITH ENCODING 'UTF8'"
    def _execute_create_test_db(self, cursor, parameters, keepdb=False):
        assert not keepdb
        events.append("create")
        create_parameters.append(parameters)
        if create_error is not None:
            raise create_error
    def _create_test_db(self, verbosity, autoclobber, keepdb=False):
        # Model Django's dangerous fallback so the regression catches delegation.
        with self._nodb_cursor() as cursor:
            try:
                self._execute_create_test_db(cursor, {}, keepdb)
            except Exception:
                if not autoclobber:
                    input("Django fallback confirmation")
                events.append("fallback_drop")
                cursor.execute("DROP DATABASE test_history_revision_ci")
                self._execute_create_test_db(cursor, {}, keepdb)

class Connection:
    vendor = "postgresql"
    def __init__(self, config):
        self.settings_dict = deepcopy(config)
        self.connected = False
        self.creation = Creation(self)
        self.ops = types.SimpleNamespace(quote_name=lambda name: '"' + name + '"')
    def copy(self, alias):
        return Connection(self.settings_dict)
    def connect(self):
        events.append("connect:" + self.settings_dict["NAME"])
        self.connected = True
    def close(self):
        self.connected = False
    def cursor(self):
        if not self.connected:
            self.connect()
        return Cursor(self)

connection = Connection(settings.DATABASES["default"])
settings.connections["default"] = connection
exec(payload["assertions"])
print(json.dumps({"rejected": False, "events": events,
                  "base_imported": Path(payload["directory"], "base-imported").exists()}))
'''


# Synthetic cases retain the frozen identities but do no business or DB work.
COLLECTION_FIXTURE = r'''
required = settings.REQUIRED_TEST_IDS
assert isinstance(required, frozenset) and len(required) == 103
assert {suffix: sum(identity.startswith("apps.cultures.test_history_revision_" + suffix + ".")
                    for identity in required)
        for suffix in ("models", "codec", "composition", "postgres_ci")} == {
    "models": 33, "codec": 14, "composition": 47, "postgres_ci": 9,
}
classes = {}
labels = set()
for identity in sorted(required):
    class_path, method_name = identity.rsplit(".", 1)
    module_name, class_name = class_path.rsplit(".", 1)
    labels.add(module_name)
    if class_path not in classes:
        parts = module_name.split(".")
        for index in range(1, len(parts) + 1):
            name = ".".join(parts[:index])
            if name not in sys.modules:
                value = module(name)
                value.__path__ = []
                if index > 1:
                    setattr(sys.modules[".".join(parts[:index - 1])], parts[index - 1], value)
        cls = type(class_name, (unittest.TestCase,), {"__module__": module_name})
        setattr(sys.modules[module_name], class_name, cls)
        classes[class_path] = cls
    setattr(classes[class_path], method_name, lambda self: None)
labels = sorted(labels)

def runner():
    value = settings.HistoryRevisionCIRunner(verbosity=0)
    value.test_loader = unittest.TestLoader()
    return value

def cases(identities=required):
    return [classes[identity.rsplit(".", 1)[0]](identity.rsplit(".", 1)[1])
            for identity in sorted(identities)]

def extra_case():
    return unittest.FunctionTestCase(lambda: None)

def rejected(value, labels=labels):
    try:
        value.build_suite(labels)
    except ImproperlyConfigured:
        return
    raise AssertionError("Incomplete or broken collection accepted")
'''


class CISettingsTests(unittest.TestCase):
    def child(self, environment=None, *, dotenv=False, assertions="", real_django=False):
        with tempfile.TemporaryDirectory(prefix="history-revision-offline-") as directory:
            temporary = Path(directory)
            package = temporary / "config"
            package.mkdir()
            (package / "__init__.py").touch()
            shutil.copyfile(SETTINGS_PATH, package / SETTINGS_PATH.name)
            (package / "test_settings.py").write_text(
                "from pathlib import Path\n"
                "Path(__file__).resolve().parents[1].joinpath('base-imported').touch()\n"
                "DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3'}}\n"
                "DATABASE_ROUTERS = ['forbidden.router']\n",
                encoding="utf-8",
            )
            if dotenv:
                (temporary / ".env").touch()
            environments = environment if isinstance(environment, list) else [ENVIRONMENT if environment is None else environment]
            payloads = [{"directory": directory, "environment": item, "assertions": assertions,
                         "real_django": real_django} for item in environments]
            launcher = (
                "import json, sys\nfrom pathlib import Path\n"
                "payloads = json.loads(sys.stdin.read())\n"
                "for payload in payloads:\n"
                "    for name in list(sys.modules):\n"
                "        if name == 'config' or name.startswith('config.'):\n"
                "            del sys.modules[name]\n"
                "    Path(payload['directory'], 'base-imported').unlink(missing_ok=True)\n"
                "    try:\n"
                "        exec(" + repr(CHILD) + ", {'payload': payload})\n"
                "    except SystemExit as error:\n"
                "        if error.code != 0: raise\n"
            )
            result = subprocess.run(
                [sys.executable, "-I", "-c", launcher], input=json.dumps(payloads),
                capture_output=True, text=True, timeout=30, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            results = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(len(results), len(environments))
            return results if isinstance(environment, list) else results[0]

    def assert_rejected(self, environment, *, dotenv=False):
        results = self.child(environment, dotenv=dotenv)
        if not isinstance(results, list):
            results = [results]
        for index, result in enumerate(results):
            with self.subTest(case=index):
                self.assertTrue(result["rejected"])
                self.assertFalse(result["base_imported"], "Safety gates must precede application settings import")
                self.assertNotIn("synthetic-secret", result["error"])

    def test_exact_configuration_and_runner(self):
        result = self.child(assertions='''
assert settings.DATABASES == {"default": {
    "ENGINE": "django.db.backends.postgresql", "NAME": "history_revision_ci",
    "USER": "history_revision_ci", "PASSWORD": "", "HOST": "127.0.0.1", "PORT": "55440",
    "TEST": {"NAME": "test_history_revision_ci"}, "CONN_MAX_AGE": 0,
    "OPTIONS": {"connect_timeout": 5, "options": "-c lock_timeout=5000 -c statement_timeout=20000 -c idle_in_transaction_session_timeout=20000"},
}}
assert settings.DATABASE_ROUTERS == []
assert settings.TEST_RUNNER == "config.history_revision_ci_settings.HistoryRevisionCIRunner"
assert settings.EMAIL_DELIVERY_ENABLED is False
settings.check_database_config(connection)
assert events == []
''')
        self.assertFalse(result["rejected"])
        self.assertTrue(result["base_imported"])

    def test_every_gate_is_required_and_exact(self):
        cases = []
        for name in ENVIRONMENT:
            for replacement in (None, "wrong", " " + ENVIRONMENT[name]):
                environment = dict(ENVIRONMENT)
                if replacement is None:
                    del environment[name]
                else:
                    environment[name] = replacement
                cases.append(environment)
        self.assert_rejected(cases)

    def test_untrusted_execution_contexts(self):
        for name, value in (("RUNNER_ENVIRONMENT", "self-hosted"), ("RUNNER_OS", "Windows"),
                            ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_EVENT_NAME", "workflow_dispatch"),
                            ("GITHUB_REF", "refs/heads/main"), ("POLYPBASE_TEST_POSTGRES", "1")):
            with self.subTest(name=name, value=value):
                self.assert_rejected({**ENVIRONMENT, name: value})

    def test_dotenv_is_refused_before_application_import(self):
        self.assert_rejected(ENVIRONMENT, dotenv=True)

    def test_inherited_database_configuration_is_refused_without_leaking_values(self):
        names = (
            "DATABASE_URL", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_HOST",
            "POSTGRES_PORT", "POSTGRES_SSLMODE", "POSTGRES_HOST_AUTH_METHOD", "POSTGRES_ANY_FUTURE_OPTION",
            "PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGPASSFILE",
            "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGSSLMODE", "PGSYSCONFDIR", "PGAPPNAME",
            "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_OPTIONS",
        )
        self.assert_rejected([
            {**ENVIRONMENT, name: value} for name in names for value in ("synthetic-secret", " ")
        ])

    def test_empty_inherited_values_cannot_override_fixed_configuration(self):
        result = self.child({**ENVIRONMENT, "DATABASE_URL": "", "POSTGRES_HOST": "", "PGSERVICE": "", "DB_HOST": ""})
        self.assertFalse(result["rejected"])

    def test_preconnect_configuration_guard(self):
        self.child(assertions='''
for key, value in {"ENGINE": "django.db.backends.sqlite3", "NAME": "postgres", "HOST": "remote.invalid",
                   "PORT": "5432", "USER": "other", "PASSWORD": "synthetic-secret",
                   "OPTIONS": {"service": "other"}, "CONN_MAX_AGE": 60,
                   "TEST": {"NAME": "other"}}.items():
    candidate = Connection(settings.DATABASES["default"])
    candidate.settings_dict[key] = value
    try:
        settings.check_database_config(candidate)
    except ImproperlyConfigured:
        pass
    else:
        raise AssertionError(key)
assert events == []
connection.settings_dict["TEST"].update(CHARSET=None, COLLATION=None, MIGRATE=True, MIRROR=None)
settings.check_database_config(connection)
for key, value in (("TEMPLATE", "other"), ("MIGRATE", False), ("MIRROR", "other"), ("DEPENDENCIES", ["other"])):
    candidate = Connection(settings.DATABASES["default"])
    candidate.settings_dict["TEST"][key] = value
    try:
        settings.check_database_config(candidate)
    except ImproperlyConfigured:
        pass
    else:
        raise AssertionError(key)
''')

    def test_server_identity_rejects_wrong_database_roles_port_version_or_marker(self):
        self.child(assertions='''
good = (settings.DATABASE_NAME, settings.DATABASE_NAME, settings.DATABASE_NAME, 5432, 180001, settings.SERVICE_MARKER)
for index, value in ((0, "postgres"), (1, "other"), (2, "other"), (3, 55440),
                     (4, 170009), (4, 190000), (4, "180001"), (5, None), (5, "other")):
    row = list(good)
    row[index] = value
    rows[settings.DATABASE_NAME] = tuple(row)
    try:
        settings.server_identity(connection)
    except ImproperlyConfigured:
        pass
    else:
        raise AssertionError((index, value))
rows.clear()
settings.server_identity(connection)
connection.settings_dict["NAME"] = settings.TEST_DATABASE_NAME
settings.server_identity(connection, test_database=True)
rows[settings.TEST_DATABASE_NAME] = good
try:
    settings.server_identity(connection, test_database=True)
except ImproperlyConfigured:
    pass
else:
    raise AssertionError("Base identity cannot stand in for the test DB")
''')

    def test_runner_checks_base_before_creation_and_test_before_migrations(self):
        self.child(assertions='''
runner = settings.HistoryRevisionCIRunner()
old_config = runner.setup_databases()
assert events.index("identity:history_revision_ci") < events.index("create")
assert events.index("identity:test_history_revision_ci") < events.index("migrate")
runner.teardown_databases(old_config)
assert events[-1] == "drop"
''')

    def test_runner_refuses_preexisting_test_database_without_create_drop_or_prompt(self):
        self.child(assertions='''
import builtins
def forbidden_input(*args, **kwargs):
    events.append("prompt")
    raise AssertionError("Database creation must never prompt")
builtins.input = forbidden_input
existing_test_database = True
for interactive in (False, True):
    events.clear()
    try:
        settings.HistoryRevisionCIRunner(interactive=interactive).setup_databases()
    except ImproperlyConfigured as error:
        assert "pre-existing" in str(error)
    else:
        raise AssertionError("Existing test database accepted")
    assert "check_test_absent" in events
    assert events.index("identity:history_revision_ci") < events.index("check_test_absent")
    assert not {"create", "drop", "fallback_drop", "prompt", "migrate"}.intersection(events)
    assert connection.settings_dict["NAME"] == settings.DATABASE_NAME
assert create_parameters == []
''')

    def test_runner_duplicate_create_race_never_retries_drops_or_prompts(self):
        self.child(assertions='''
import builtins
class DuplicateDatabase(Exception):
    pass
def forbidden_input(*args, **kwargs):
    events.append("prompt")
    raise AssertionError("Database creation must never prompt")
builtins.input = forbidden_input
for failure in (DuplicateDatabase("synthetic-secret-duplicate"), RuntimeError("synthetic-secret-create")):
    for interactive in (False, True):
        events.clear()
        create_parameters.clear()
        create_error = failure
        try:
            settings.HistoryRevisionCIRunner(interactive=interactive).setup_databases()
        except ImproperlyConfigured as error:
            assert "no retry or DROP" in str(error)
            assert "synthetic-secret" not in str(error)
            assert error.__suppress_context__
        else:
            raise AssertionError("Failed creation accepted")
        assert events.count("create") == 1
        assert events.index("check_test_absent") < events.index("create")
        assert not {"drop", "fallback_drop", "prompt", "migrate"}.intersection(events)
        assert connection.settings_dict["NAME"] == settings.DATABASE_NAME
        assert create_parameters == [{"dbname": '"test_history_revision_ci"', "suffix": "WITH ENCODING 'UTF8'"}]
''')

    def test_runner_refuses_missing_base_marker_before_creation(self):
        self.child(assertions='''
rows[settings.DATABASE_NAME] = (settings.DATABASE_NAME, settings.DATABASE_NAME, settings.DATABASE_NAME, 5432, 180001, None)
try:
    settings.HistoryRevisionCIRunner().setup_databases()
except ImproperlyConfigured:
    pass
else:
    raise AssertionError("Missing base marker accepted")
assert "create" not in events
''')

    def test_runner_refuses_wrong_test_identity_before_migrations(self):
        self.child(assertions='''
rows[settings.TEST_DATABASE_NAME] = ("other", settings.DATABASE_NAME, settings.DATABASE_NAME, 5432, 180001, None)
try:
    settings.HistoryRevisionCIRunner().setup_databases()
except ImproperlyConfigured:
    pass
else:
    raise AssertionError("Wrong test identity accepted")
assert "migrate" not in events
assert not connection.connected
''')

    def test_runner_rechecks_settings_before_reconnect_and_before_drop(self):
        self.child(assertions='''
runner = settings.HistoryRevisionCIRunner()
old_config = runner.setup_databases()
connection.close()
connection.settings_dict["HOST"] = "remote.invalid"
before = list(events)
try:
    connection.connect()
except ImproperlyConfigured:
    pass
else:
    raise AssertionError("Unsafe reconnect accepted")
assert events == before
try:
    runner.teardown_databases(old_config)
except ImproperlyConfigured:
    pass
else:
    raise AssertionError("Unsafe teardown accepted")
assert "drop" not in events
''')

    def test_suite_result_writes_counts_and_ids_without_tracebacks_reasons_or_environment(self):
        self.child(assertions='''
import unittest
class Outcomes(unittest.TestCase):
    def test_pass(self):
        pass
    def test_fail(self):
        self.fail("synthetic-secret-failure-trace")
    def test_error(self):
        raise RuntimeError("synthetic-secret-error-trace")
    @unittest.skip("synthetic-secret-skip-reason")
    def test_skip(self):
        pass
    @unittest.expectedFailure
    def test_expected_failure(self):
        self.fail("synthetic-secret-expected-trace")
    @unittest.expectedFailure
    def test_unexpected_success(self):
        pass
suite = unittest.defaultTestLoader.loadTestsFromTestCase(Outcomes)
result = unittest.TestResult()
suite.run(result)
directory = Path(payload["directory"]) / "evidence" / "nested"
os.environ["HR_CI_EVIDENCE_DIR"] = str(directory)
os.environ["UNRELATED_SECRET"] = "synthetic-secret-environment"
runner = settings.HistoryRevisionCIRunner()
assert runner.suite_result(suite, result) == 6
raw = (directory / "tests.json").read_text(encoding="utf-8")
evidence = json.loads(raw)
assert evidence == {
    "run": 6, "passed": 1, "failed": 1, "errors": 1, "skipped": 1,
    "expectedFailures": 1, "unexpectedSuccesses": 1,
    "collected_test_ids": [], "started_test_ids": [], "stopped_test_ids": [],
    "successful_test_ids": [],
    "missing_collected_test_ids": sorted(settings.REQUIRED_TEST_IDS),
    "missing_executed_test_ids": sorted(settings.REQUIRED_TEST_IDS),
    "missing_successful_test_ids": sorted(settings.REQUIRED_TEST_IDS),
    "failing_test_ids": [result.failures[0][0].id(), result.errors[0][0].id()],
    "skipped_test_ids": [result.skipped[0][0].id()],
    "expected_failure_test_ids": [result.expectedFailures[0][0].id()],
    "unexpected_success_test_ids": [result.unexpectedSuccesses[0].id()],
}
assert "synthetic-secret" not in raw and "Traceback" not in raw
assert events == []
''')

    def test_suite_result_fails_each_disallowed_outcome_without_evidence_directory(self):
        self.child(assertions='''
import unittest
runner = settings.HistoryRevisionCIRunner()
case = unittest.FunctionTestCase(lambda: None)
assert "HR_CI_EVIDENCE_DIR" not in os.environ
for outcome in ("failures", "errors", "skipped", "expectedFailures", "unexpectedSuccesses"):
    result = unittest.TestResult()
    result.testsRun = 1
    getattr(result, outcome).append(case if outcome == "unexpectedSuccesses" else (case, "synthetic-secret"))
    assert runner.suite_result(None, result) == 2, outcome
assert not (Path(payload["directory"]) / "tests.json").exists()
assert events == []
''')

    def test_suite_result_rejects_empty_or_untracked_counts_and_evidence_write_failure(self):
        self.child(assertions='''
import unittest
runner = settings.HistoryRevisionCIRunner()
directory = Path(payload["directory"]) / "evidence"
os.environ["HR_CI_EVIDENCE_DIR"] = str(directory)
result = unittest.TestResult()
for count in (0, 3):
    result.testsRun = count
    assert runner.suite_result(None, result) == 1
    evidence = json.loads((directory / "tests.json").read_text(encoding="utf-8"))
    assert evidence["run"] == evidence["passed"] == count
    assert all(evidence[key] == 0 for key in ("failed", "errors", "skipped", "expectedFailures", "unexpectedSuccesses"))
    assert evidence["failing_test_ids"] == evidence["skipped_test_ids"] == []
    assert len(evidence["missing_collected_test_ids"]) == 103
    assert len(evidence["missing_executed_test_ids"]) == 103
    assert len(evidence["missing_successful_test_ids"]) == 103
os.environ["HR_CI_EVIDENCE_DIR"] = str(directory / "tests.json")
try:
    runner.suite_result(None, result)
except OSError:
    pass
else:
    raise AssertionError("Evidence write failure must not report success")
assert events == []
''')

    def test_collection_and_execution_accept_baseline_and_extras(self):
        self.child(assertions=COLLECTION_FIXTURE + r'''
for extras in (0, 4):
    cls = next(iter(classes.values()))
    for index in range(extras):
        setattr(cls, "test_extra_" + str(index), lambda self: None)
    value = runner()
    suite = value.build_suite(labels)
    assert len(value._history_revision_collected_ids) == 103 + extras
    result = value.run_suite(suite)
    assert result.testsRun == 103 + extras
    assert required <= result.history_revision_started_ids
    assert required <= result.history_revision_stopped_ids
    assert required <= result.history_revision_successful_ids
    assert value.suite_result(suite, result) == 0
assert events == []
''')

    def test_collection_rejects_empty_missing_module_and_renamed_required_method(self):
        self.child(assertions=COLLECTION_FIXTURE + r'''
rejected(runner(), [])
# Extras preserve the total, but cannot replace the required module or method.
for missing in (set(identity for identity in required if "_codec." in identity),
                {sorted(required)[0]}):
    value = runner()
    masked = unittest.TestSuite(cases(required - missing) + [extra_case() for _ in missing])
    assert masked.countTestCases() == 103
    with unittest.mock.patch.object(DiscoverRunner, "build_suite", return_value=masked):
        rejected(value)
identity = sorted(required)[0]
class_path, method = identity.rsplit(".", 1)
cls = classes[class_path]
setattr(cls, method + "_renamed", getattr(cls, method))
delattr(cls, method)
rejected(runner())
assert events == []
''')

    def test_failed_recollection_cannot_reuse_previous_collection_proof(self):
        self.child(assertions=COLLECTION_FIXTURE + r'''
value = runner()
suite = value.build_suite(labels)
result = value.run_suite(suite)
assert value.suite_result(suite, result) == 0
with unittest.mock.patch.object(DiscoverRunner, "build_suite", return_value=unittest.TestSuite()):
    rejected(value)
assert value._history_revision_collected_ids == frozenset()
assert value.suite_result(None, result) > 0
assert events == []
''')

    def test_collection_rejects_loader_errors_and_failed_test_even_with_full_baseline(self):
        self.child(assertions=COLLECTION_FIXTURE + r'''
value = runner()
# A missing module produces a real unittest loader error and _FailedTest.
rejected(value, labels + ["apps.cultures.missing_history_revision_tests"])
assert value.test_loader.errors
value = runner()
value.test_loader.errors.append("synthetic-secret-loader-error")
rejected(value)
failed = unittest.loader._FailedTest("broken_module", ImportError("synthetic-secret"))
value = runner()
with unittest.mock.patch.object(DiscoverRunner, "build_suite",
                               return_value=unittest.TestSuite(cases() + [failed])):
    rejected(value)
assert events == []
''')

    def test_execution_rejects_missing_required_identity_despite_masked_counts(self):
        self.child(assertions=COLLECTION_FIXTURE + r'''
value = runner()
value.build_suite(labels)
missing = sorted(required)[0]
# Run 103 tests successfully, but substitute an extra for one required test.
suite = unittest.TestSuite(cases(required - {missing}) + [extra_case()])
result = value.run_suite(suite)
assert result.testsRun == 103 and result.wasSuccessful()
assert value.suite_result(suite, result) > 0
# Neither a full count nor start/success without stop is sufficient.
for attribute in ("history_revision_started_ids", "history_revision_stopped_ids",
                  "history_revision_successful_ids"):
    result = value.run_suite(unittest.TestSuite(cases()))
    getattr(result, attribute).remove(missing)
    assert result.testsRun == 103 and value.suite_result(None, result) > 0
assert events == []
''')

    def test_execution_rejects_each_required_test_non_success_and_failfast(self):
        self.child(assertions=COLLECTION_FIXTURE + r'''
identity = sorted(required)[0]
class_path, method = identity.rsplit(".", 1)
cls = classes[class_path]
original = getattr(cls, method)
def fail(self):
    self.fail("synthetic-secret")
def error(self):
    raise RuntimeError("synthetic-secret")
for replacement in (fail, error, unittest.skip("offline skip")(original),
                    unittest.expectedFailure(lambda self: fail(self)),
                    unittest.expectedFailure(lambda self: None)):
    setattr(cls, method, replacement)
    value = runner()
    suite = value.build_suite(labels)
    result = value.run_suite(suite)
    assert result.testsRun == 103
    assert value.suite_result(suite, result) > 0
    setattr(cls, method, original)
setattr(cls, method, fail)
value = runner()
value.build_suite(labels)
value.failfast = True
result = value.run_suite(unittest.TestSuite(cases()))
assert result.testsRun == 1 and result.shouldStop
assert value.suite_result(None, result) > 0
assert events == []
''')

    def test_successful_identity_evidence_and_missing_execution_evidence(self):
        self.child(assertions=COLLECTION_FIXTURE + r'''
value = runner()
suite = value.build_suite(labels)
result = value.run_suite(suite)
directory = Path(payload["directory"]) / "evidence"
os.environ["HR_CI_EVIDENCE_DIR"] = str(directory)
assert value.suite_result(suite, result) == 0
evidence = json.loads((directory / "tests.json").read_text())
for key in ("collected_test_ids", "started_test_ids", "stopped_test_ids", "successful_test_ids"):
    assert evidence[key] == sorted(required)
for key in ("missing_collected_test_ids", "missing_executed_test_ids", "missing_successful_test_ids"):
    assert evidence[key] == []
missing = sorted(required)[0]
result.history_revision_successful_ids.remove(missing)
assert value.suite_result(None, result) > 0
evidence = json.loads((directory / "tests.json").read_text())
assert evidence["missing_successful_test_ids"] == [missing]
assert events == []
''')

    @unittest.skipUnless(importlib.util.find_spec("django"), "Django is not installed in this interpreter")
    def test_installed_django_collection_and_result_interfaces_offline(self):
        self.child(real_django=True, assertions=COLLECTION_FIXTURE + r'''
value = runner()
suite = value.build_suite(labels)
result = value.run_suite(suite)
assert result.testsRun == 103 and value.suite_result(suite, result) == 0
assert result.history_revision_successful_ids == required
rejected(runner(), labels[:-1])
rejected(runner(), labels + ["apps.cultures.missing_history_revision_tests"])
value = runner()
value.build_suite(labels)
result = value.run_suite(unittest.TestSuite(cases(required - {sorted(required)[0]}) + [extra_case()]))
assert result.testsRun == 103 and result.wasSuccessful()
failures = value.suite_result(None, result)
assert failures > 0
from django.core.management.commands import test as test_command
factory = unittest.mock.Mock(return_value=value)
with unittest.mock.patch.object(test_command, "get_runner", return_value=factory) as get_runner, \
     unittest.mock.patch.object(value, "run_tests", return_value=failures) as run_tests, \
     unittest.mock.patch.object(value, "setup_databases", side_effect=AssertionError("No DB setup")) as setup_databases:
    try:
        test_command.Command().handle(*labels, testrunner=settings.TEST_RUNNER)
    except SystemExit as error:
        assert isinstance(error.code, int) and error.code != 0
    else:
        raise AssertionError("Management test command accepted a rejected suite result")
    get_runner.assert_called_once_with(django_settings, settings.TEST_RUNNER)
    factory.assert_called_once_with(testrunner=settings.TEST_RUNNER)
    run_tests.assert_called_once_with(tuple(labels))
    setup_databases.assert_not_called()
assert events == []
''')

    def test_runner_forbids_reuse_clones_and_extra_aliases(self):
        self.child(assertions='''
for arguments in ({"keepdb": True}, {"parallel": 2}):
    try:
        settings.HistoryRevisionCIRunner(**arguments).setup_databases()
    except ImproperlyConfigured:
        pass
    else:
        raise AssertionError(arguments)
settings.connections["other"] = connection
try:
    settings.HistoryRevisionCIRunner().setup_databases()
except ImproperlyConfigured:
    pass
else:
    raise AssertionError("Extra database accepted")
assert events == []
''')


try:
    import yaml
except ImportError:
    yaml = None


@unittest.skipUnless(WORKFLOW_PATH.is_file(), "History revision workflow is not present in this checkout yet")
@unittest.skipIf(yaml is None, "PyYAML is optional offline tooling; no production dependency is added")
class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.workflow = yaml.load(self.text, Loader=yaml.BaseLoader)
        jobs = self.workflow["jobs"]
        candidates = [job for job in jobs.values() if "postgres" in job.get("services", {})]
        self.assertEqual(len(candidates), 1)
        self.job = candidates[0]

    def test_push_only_hosted_ubuntu_readonly_and_bounded(self):
        self.assertEqual(self.workflow["on"], {"push": {"branches": ["test/history-revision-postgres-qa"]}})
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertRegex(self.job["runs-on"], r"^ubuntu-(latest|\d{2}\.04)$")
        self.assertTrue(0 < int(self.job["timeout-minutes"]) <= 30)
        self.assertNotIn("secrets.", self.text)
        self.assertNotIn("environment", self.job)

    def test_disposable_postgres18_service(self):
        service = self.job["services"]["postgres"]
        self.assertEqual(service["image"], "postgres:18")
        self.assertEqual(service["ports"], ["55440:5432"])
        self.assertNotIn("volumes", service)
        self.assertEqual(service["env"], {
            "POSTGRES_DB": "history_revision_ci", "POSTGRES_USER": "history_revision_ci",
            "POSTGRES_HOST_AUTH_METHOD": "trust",
        })
        self.assertIn("--tmpfs /var/lib/postgresql:rw,nosuid", service["options"])
        self.assertIn("pg_isready -h 127.0.0.1", service["options"])

    def test_settings_gates_and_pinned_actions(self):
        environment = {**self.workflow.get("env", {}), **self.job.get("env", {})}
        # GITHUB_* and RUNNER_* are provided by Actions, not forged job variables.
        for name, expected in ENVIRONMENT.items():
            if not name.startswith(("GITHUB_", "RUNNER_")):
                self.assertEqual(environment.get(name), expected, name)
        self.assertEqual(environment["DJANGO_SETTINGS_MODULE"], "config.history_revision_ci_settings")
        allowed = {"github", "needs", "strategy", "matrix", "vars", "secrets", "inputs"}
        for value in environment.values():
            for expression in re.findall(r"\$\{\{(.*?)\}\}", value, re.DOTALL):
                roots = set(re.findall(r"(?<![\w.])([a-zA-Z_]\w*)\s*(?:\.|\[)", expression))
                self.assertLessEqual(roots, allowed)
        for step in self.job["steps"]:
            if "uses" in step:
                self.assertRegex(step["uses"], r"@[0-9a-f]{40}$")

    def test_preflight_precedes_database_commands_and_failure_evidence_is_retained(self):
        steps = self.job["steps"]
        commands = [step.get("run", "") for step in steps]
        joined = "\n".join(commands)
        self.assertIn("uv sync --frozen", joined)
        self.assertIn("test_history_revision_ci.py", joined)
        # The utility's run subcommand uses call_command('test', ...) internally;
        # labels and explicit CLI --settings therefore need not appear in YAML.
        preflight = next(i for i, command in enumerate(commands) if "preflight" in command and "history_revision" in command)
        runs = [i for i, command in enumerate(commands)
                if re.search(r"history_revision[^\s]*\.py\s+run\b", command)]
        self.assertTrue(runs, "The workflow must invoke the history revision utility's run subcommand")
        for i in runs:
            self.assertLess(preflight, i)
        for i, command in enumerate(commands):
            if "manage.py migrate" in command or "manage.py test" in command:
                self.assertLess(preflight, i)
        uploads = [step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")]
        self.assertTrue(uploads)
        for upload in uploads:
            self.assertEqual(upload["if"], "always()")
            self.assertTrue(0 < int(upload["with"]["retention-days"]) <= 7)


if __name__ == "__main__":
    unittest.main()
