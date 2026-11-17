"""Offline regressions for the disposable per-test database reset.

The real reset needs PostgreSQL and is exercised only by the hosted QA. These
tests use a fake database catalog and cursor, never a socket or a real database.
"""

import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHILD = r'''
import os
import re
import socket
import sys

class ForbiddenSocket(socket.socket):
    def connect(self, *args, **kwargs):
        raise AssertionError("Offline isolation tests must never connect sockets")
    def connect_ex(self, *args, **kwargs):
        raise AssertionError("Offline isolation tests must never connect sockets")
socket.socket = ForbiddenSocket

sys.path.insert(0, "backend")
import django
from django.conf import settings
settings.configure(SECRET_KEY="offline-isolation", DATABASES={}, INSTALLED_APPS=[])
django.setup()

from config import history_revision_ci_isolation as iso

BASE = iso.BASE_DATABASE_NAME
TEST = iso.TEST_DATABASE_NAME
TEMPLATE = iso.TEMPLATE_DATABASE_NAME
ROLE = BASE


class FakeCatalog:
    def __init__(self):
        self.rows = {BASE: [1, ROLE], TEST: [2, ROLE]}
        self.next_oid = 3
        self.sql = []

    def execute(self, cursor, sql, params):
        normalized = " ".join(sql.split())
        self.sql.append((normalized, params))
        if "current_database()" in normalized:
            cursor.result = (BASE,)
        elif normalized == "SELECT current_user":
            cursor.result = (ROLE,)
        elif normalized.startswith("SELECT oid, pg_get_userbyid"):
            cursor.result = tuple(self.rows[params[0]]) if params[0] in self.rows else None
        elif normalized.startswith("CREATE DATABASE"):
            match = re.fullmatch(
                r'CREATE DATABASE "([^"]+)" WITH TEMPLATE "([^"]+)" OWNER "([^"]+)"',
                normalized,
            )
            assert match, normalized
            name, template, owner = match.groups()
            assert template in self.rows, template
            assert owner == ROLE
            assert name not in self.rows
            self.rows[name] = [self.next_oid, owner]
            self.next_oid += 1
            cursor.result = None
        elif normalized.startswith("DROP DATABASE"):
            match = re.fullmatch(r'DROP DATABASE "([^"]+)"', normalized)
            assert match, normalized
            name = match.group(1)
            assert name in self.rows, name
            del self.rows[name]
            cursor.result = None
        else:
            raise AssertionError("Unexpected SQL: " + normalized)
        return cursor.result


class FakeCursor:
    def __init__(self, catalog):
        self.catalog = catalog
        self.result = None
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def execute(self, sql, params=None):
        return self.catalog.execute(self, sql, params)
    def fetchone(self):
        return self.result


class FakeMaintenance:
    vendor = "postgresql"
    def __init__(self, catalog):
        self.catalog = catalog
        self.settings_dict = {"NAME": BASE}
    def cursor(self):
        return FakeCursor(self.catalog)


class FakeConnection:
    vendor = "postgresql"
    def __init__(self):
        self.settings_dict = {"NAME": TEST}
        self.connected = False
    def connect(self):
        self.connected = True
    def close(self):
        self.connected = False


class FakeConnections:
    def __init__(self, connection):
        self.connection = connection
        self.closed = 0
    def __getitem__(self, alias):
        assert alias == "default"
        return self.connection
    def close_all(self):
        self.closed += 1
        self.connection.close()


def setup_environment():
    catalog = FakeCatalog()
    connection = FakeConnection()
    iso.connections = FakeConnections(connection)
    maintenance = FakeMaintenance(catalog)
    calls = {"base": 0, "test": 0}
    def verify_base():
        calls["base"] += 1
        assert maintenance.settings_dict["NAME"] == BASE
    def verify_test():
        calls["test"] += 1
        assert connection.settings_dict["NAME"] in {BASE, TEST}
    return catalog, connection, maintenance, verify_base, verify_test, calls


def prepare(catalog, connection, maintenance, verify_base, verify_test):
    iso.prepare(
        maintenance, base_name=BASE, test_name=TEST, template_name=TEMPLATE,
        quote=lambda name: '"' + name + '"', verify_base=verify_base,
        verify_test=verify_test,
    )


class DummyTransactionTestCase:
    def __init__(self, names=("default",)):
        self.names = list(names)
        self.flushes = 0
    def _databases_names(self, include_mirrors=False):
        return self.names
    def _fixture_teardown(self):
        self.flushes += 1


def reset_globals():
    iso._STATE = None
    iso._ORIGINAL_TEARDOWN = None
    iso.TransactionTestCase = DummyTransactionTestCase


# --- prepare and reset -----------------------------------------------------

reset_globals()
catalog, connection, maintenance, verify_base, verify_test, calls = setup_environment()
original = DummyTransactionTestCase._fixture_teardown
prepare(catalog, connection, maintenance, verify_base, verify_test)
assert iso._STATE is not None
assert TEMPLATE in catalog.rows
assert iso.TransactionTestCase._fixture_teardown is not original
assert calls["base"] >= 1 and calls["test"] >= 1
assert any(sql.startswith("CREATE DATABASE") for sql, _ in catalog.sql)

case = DummyTransactionTestCase()
test_oid_before = catalog.rows[TEST][0]
iso.TransactionTestCase._fixture_teardown(case)
assert case.flushes == 0, "Django TRUNCATE flush must not run"
assert iso._STATE.resets == 1
assert catalog.rows[TEST][0] != test_oid_before
assert [sql for sql, _ in catalog.sql if sql.startswith(("DROP DATABASE", "CREATE DATABASE"))][-2:] == [
    'DROP DATABASE "test_history_revision_ci"',
    'CREATE DATABASE "test_history_revision_ci" WITH TEMPLATE "test_history_revision_ci_template" OWNER "history_revision_ci"',
]
assert all("TRUNCATE" not in sql.upper() for sql, _ in catalog.sql)
assert all("SESSION_REPLICATION_ROLE" not in sql.upper() for sql, _ in catalog.sql)
assert all("DISABLE TRIGGER" not in sql.upper() for sql, _ in catalog.sql)
assert all("WITH (FORCE)" not in sql.upper() for sql, _ in catalog.sql)

evidence = iso.finish()
assert evidence == {"template": TEMPLATE, "template_oid": 3, "resets": 1}
assert TEMPLATE not in catalog.rows
assert TEST in catalog.rows
assert iso.TransactionTestCase._fixture_teardown is original
assert iso._STATE is None


# --- refusal paths ---------------------------------------------------------

def refused(action):
    try:
        action()
    except Exception:
        return True
    return False


reset_globals()
catalog, connection, maintenance, verify_base, verify_test, calls = setup_environment()
catalog.rows[TEMPLATE] = [99, ROLE]
assert refused(lambda: prepare(catalog, connection, maintenance, verify_base, verify_test))
assert not any(sql.startswith("CREATE DATABASE") for sql, _ in catalog.sql)
assert iso._STATE is None

reset_globals()
catalog, connection, maintenance, verify_base, verify_test, calls = setup_environment()
catalog.rows[TEST][1] = "other_role"
assert refused(lambda: prepare(catalog, connection, maintenance, verify_base, verify_test))
assert not any(sql.startswith("CREATE DATABASE") for sql, _ in catalog.sql)

reset_globals()
catalog, connection, maintenance, verify_base, verify_test, calls = setup_environment()
maintenance.settings_dict["NAME"] = TEST
assert refused(lambda: prepare(catalog, connection, maintenance, verify_base, verify_test))

reset_globals()
catalog, connection, maintenance, verify_base, verify_test, calls = setup_environment()
prepare(catalog, connection, maintenance, verify_base, verify_test)
case = DummyTransactionTestCase(("other",))
assert refused(case._fixture_teardown)
assert iso._STATE.resets == 0
connection.settings_dict["NAME"] = BASE
case = DummyTransactionTestCase()
assert refused(case._fixture_teardown)
assert iso._STATE.resets == 0
connection.settings_dict["NAME"] = TEST
iso.finish()

reset_globals()
catalog, connection, maintenance, verify_base, verify_test, calls = setup_environment()
prepare(catalog, connection, maintenance, verify_base, verify_test)
catalog.rows[TEST][0] = 777
case = DummyTransactionTestCase()
assert refused(case._fixture_teardown)
assert iso._STATE.resets == 0
catalog.rows[TEST][0] = iso._STATE.test_oid
iso.finish()

reset_globals()
catalog, connection, maintenance, verify_base, verify_test, calls = setup_environment()
prepare(catalog, connection, maintenance, verify_base, verify_test)
catalog.rows[TEMPLATE][0] = 999
assert refused(iso.finish)
assert iso.TransactionTestCase._fixture_teardown is original
assert iso._STATE is None
assert TEMPLATE in catalog.rows

reset_globals()
catalog, connection, maintenance, verify_base, verify_test, calls = setup_environment()
prepare(catalog, connection, maintenance, verify_base, verify_test)
catalog.rows[TEMPLATE][1] = "other_role"
assert refused(iso.finish)
assert TEMPLATE in catalog.rows

assert iso.finish() is None
'''

STATIC = r'''
"""The isolated reset replaces flush; concurrency classes stay TransactionTestCase."""
import ast
from pathlib import Path

source = Path("backend/apps/cultures/test_history_revision_postgres_ci.py").read_text(encoding="utf-8")
tree = ast.parse(source)
bases = {
    node.name: [base.id if isinstance(base, ast.Name) else ast.unparse(base) for base in node.bases]
    for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
}
assert bases["HistoryRevisionPostgresCITests"] == ["TransactionTestCase"]
assert bases["HistoryRevisionSourcePreservationPostgresCITests"] == ["TransactionTestCase"]
assert bases["HistoryRevisionFunctionCatalogPostgresCITests"] == ["TestCase"]

models = Path("backend/apps/cultures/test_history_revision_models.py").read_text(encoding="utf-8")
assert "from django.db import DataError, IntegrityError" in models
assert '"a" * 65' in models
assert "assertRaises((DataError, IntegrityError))" in models

codec = Path("backend/apps/cultures/test_history_revision_codec.py").read_text(encoding="utf-8")
assert "secure=True" in codec
assert 'response.get("Cache-Control")' in codec
assert 'response.get("Vary", "")' in codec

isolation = Path("backend/config/history_revision_ci_isolation.py").read_text(encoding="utf-8")
assert "DROP DATABASE" in isolation
assert "WITH TEMPLATE" in isolation
assert "template_oid" in isolation
'''


class HistoryRevisionCIIsolationTests(unittest.TestCase):
    def run_offline(self, code):
        environment = {
            name: value for name, value in os.environ.items()
            if name.upper() in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "TEMP", "TMP", "PATH"}
        }
        environment.update({"PYTHON_DOTENV_DISABLED": "1", "POLYPBASE_TEST_POSTGRES": "0"})
        result = subprocess.run(
            [sys.executable, "-B", "-c", CHILD + code], cwd=ROOT,
            env=environment, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_reset_refuses_unknown_databases_and_never_uses_truncate_or_force(self):
        self.run_offline("")

    def test_static_contract_preserves_transaction_tests_and_https_assertions(self):
        result = subprocess.run(
            [sys.executable, "-B", "-c", STATIC], cwd=ROOT,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()