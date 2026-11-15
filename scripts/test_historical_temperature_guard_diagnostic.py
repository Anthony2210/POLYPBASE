"""Bounded, settings-free tests; no database connections or launcher needed."""

from contextlib import redirect_stdout
from datetime import datetime, timezone
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import historical_temperature_guard_diagnostic as diagnostic


class GuardDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(
            diagnostic, "__file__", str(self.root / "scripts" / "diagnostic.py")))
        self.launch = self.enterContext(patch.object(
            diagnostic, "require_launch",
            return_value=SimpleNamespace(system_identifier="123456")))
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.functions = [self.function()]
        self.triggers = [{
            "name": "historical_reading_guard", "tgenabled": "O",
            "table_schema": "public", "table_name": "metadata_only",
            "function_oid": 42, "function_schema": "public",
            "function_name": "historical_temperature_guard",
        }]
        self.migrations = [{
            "app": "measurements", "name": "0018_box_1902_preservation",
            "applied": datetime(2026, 11, 15, tzinfo=timezone.utc),
        }]
        self.cursor.execute.side_effect = self.query
        self.ui = ModuleType("apps.measurements.historical_temperature_ui")
        self.ui.check_guards = Mock()
        apps = ModuleType("apps")
        measurements = ModuleType("apps.measurements")
        contract = ModuleType("apps.measurements.box_1902_sql")
        contract.EXPECTED_DEFINITIONS = {"public.historical_temperature_guard": "exact body"}
        measurements.box_1902_sql = contract
        apps.measurements = measurements
        self.enterContext(patch.dict(sys.modules, {
            "apps": apps, "apps.measurements": measurements,
            "apps.measurements.box_1902_sql": contract,
            "apps.measurements.historical_temperature_ui": self.ui,
        }))
        self.output = self.enterContext(redirect_stdout(io.StringIO()))

    @staticmethod
    def function(**overrides):
        row = {
            "oid": 42, "schema": "public", "name": "historical_temperature_guard",
            "arguments": "", "owner": "guard_owner", "prosecdef": False,
            "proconfig": ["search_path=pg_catalog, public, pg_temp"],
            "provolatile": "v", "proparallel": "u", "proisstrict": False,
            "proleakproof": False, "prosrc": "exact body",
            "definition": "CREATE OR REPLACE FUNCTION public.historical_temperature_guard() ...",
            "acl": "{guard_owner=X/guard_owner}",
        }
        row.update(overrides)
        return row

    def query(self, sql, params=None):
        self.assertTrue(sql.strip().startswith("SELECT"))
        self.assertNotIn("settings_dict", sql)
        if "FROM pg_catalog.pg_proc p" in sql:
            self.assertIn("'^historical_temperature_'", sql)
            rows = self.functions
        elif "FROM pg_catalog.pg_trigger t" in sql:
            rows = self.triggers
        else:
            self.assertIn("FROM public.django_migrations", sql)
            self.assertEqual(params, ["measurements"])
            rows = self.migrations
        columns = list(rows[0]) if rows else []
        self.cursor.description = [(name,) for name in columns]
        self.cursor.fetchall.return_value = [tuple(row[name] for name in columns) for row in rows]

    def report(self, stage):
        return self.root / ".local-docs" / f"historical-temperature-guards-{stage}.json"

    def test_launch_refusal_prevents_connection_access_and_file_output(self):
        self.launch.side_effect = RuntimeError("not an authorized child")
        with patch.object(diagnostic.Path, "mkdir") as mkdir, patch.object(
                diagnostic.Path, "write_text") as write:
            with self.assertRaisesRegex(RuntimeError, "authorized child"):
                diagnostic.record_guard_diagnostic(self.connection, "before_0018")
        self.assertEqual(self.connection.mock_calls, [])
        mkdir.assert_not_called()
        write.assert_not_called()
        self.ui.check_guards.assert_not_called()
        self.assertEqual(self.output.getvalue(), "")

    def test_invalid_stages_refused_before_queries_or_output(self):
        for stage in ("BEFORE0018", "after_0018/other", "before_0018 ", "", None, []):
            with self.subTest(stage=stage):
                with self.assertRaises(ValueError):
                    diagnostic.record_guard_diagnostic(self.connection, stage)
        self.assertEqual(self.connection.mock_calls, [])
        self.assertFalse((self.root / ".local-docs").exists())
        self.ui.check_guards.assert_not_called()
        self.assertEqual(self.output.getvalue(), "")

    def test_before_stage_preserves_metadata_and_uses_fixed_repo_path(self):
        result = diagnostic.record_guard_diagnostic(self.connection, "before_0018")
        self.assertEqual(result, json.loads(self.report("before_0018").read_text(encoding="utf-8")))
        self.assertEqual(result["system_identifier"], "123456")
        function = result["functions"][0]
        for name in ("owner", "prosecdef", "proconfig", "provolatile", "proparallel",
                     "proisstrict", "proleakproof", "prosrc", "definition", "acl"):
            self.assertEqual(function[name], self.functions[0][name])
        self.assertEqual(function["legacy_predicate"], {"fails": False, "reasons": []})
        self.assertNotIn("contract_definition_matches", function)
        self.assertEqual(result["triggers"], self.triggers)
        self.assertEqual(result["measurements_migrations"][0]["applied"], "2026-11-15T00:00:00+00:00")
        self.assertEqual(result["check_guards"]["outcome"], "passed")
        self.ui.check_guards.assert_called_once_with()
        self.assertEqual(self.cursor.execute.call_count, 3)
        self.assertEqual([call[0] for call in self.connection.mock_calls],
                         ["cursor", "cursor().__enter__", "cursor().__enter__().execute",
                          "cursor().__enter__().fetchall", "cursor().__enter__().execute",
                          "cursor().__enter__().fetchall", "cursor().__enter__().execute",
                          "cursor().__enter__().fetchall", "cursor().__exit__"])
        self.assertIn(str(self.report("before_0018")), self.output.getvalue())

    def test_after_stage_captures_failure_and_exact_contract_body_matches(self):
        self.functions = [
            self.function(prosecdef=True, proconfig=["search_path=pg_catalog, pg_temp"]),
            self.function(oid=43, prosrc="changed body", proconfig=None),
            self.function(oid=44, name="historical_temperature_other"),
        ]
        self.ui.check_guards.side_effect = RuntimeError("guard_functions_unsafe")
        result = diagnostic.record_guard_diagnostic(self.connection, "after_0018")
        first, second, third = result["functions"]
        self.assertEqual(first["legacy_predicate"], {
            "fails": True, "reasons": ["security_definer", "missing_legacy_search_path"],
        })
        self.assertEqual(second["legacy_predicate"]["reasons"], ["missing_legacy_search_path"])
        self.assertIs(first["contract_definition_matches"], True)
        self.assertIs(second["contract_definition_matches"], False)
        self.assertIsNone(third["contract_definition_matches"])
        check = result["check_guards"]
        self.assertEqual(check["outcome"], "failed")
        self.assertEqual(check["error_type"], "RuntimeError")
        self.assertEqual(check["error"], "guard_functions_unsafe")
        self.assertIn("Traceback (most recent call last)", check["traceback"])
        self.assertIn("RuntimeError: guard_functions_unsafe", check["traceback"])
        self.assertEqual(result, json.loads(self.report("after_0018").read_text(encoding="utf-8")))

    def test_existing_output_directory_and_empty_catalogs(self):
        self.functions = []
        self.triggers = []
        self.migrations = []
        (self.root / ".local-docs").mkdir()
        for _ in range(2):
            result = diagnostic.record_guard_diagnostic(self.connection, "before_0018")
            self.assertEqual(result["functions"], [])
            self.assertEqual(result["triggers"], [])
            self.assertEqual(result["measurements_migrations"], [])

    def test_module_import_does_not_import_django_or_application_settings(self):
        original_import = __import__

        def guarded_import(name, *args, **kwargs):
            if name == "django" or name.startswith(("django.", "apps.", "config.")):
                self.fail(f"Unexpected settings/application import: {name}")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import):
            importlib.reload(diagnostic)
        self.assertEqual(self.connection.mock_calls, [])
        self.assertFalse((self.root / ".local-docs").exists())


if __name__ == "__main__":
    unittest.main()
