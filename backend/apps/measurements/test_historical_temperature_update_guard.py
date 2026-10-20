"""Connection-free catalog and infrastructure SQL contract tests."""

from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock

from . import historical_temperature_update_guard as guard


class HistoricalTemperatureUpdateGuardTests(TestCase):
    def setUp(self):
        self.database = MagicMock()
        self.cursor = self.database.cursor.return_value.__enter__.return_value
        self.identity = (guard.ROLE, guard.ROLE, False, False)
        self.function = (
            701, "public", guard.FUNCTION_NAME, "plpgsql", True, "f", 0, "", None, None, None,
            0, 0, False, False, False, False, "v", "u", guard.FUNCTION_BODY,
            ["search_path=pg_catalog, pg_temp"], 702, False,
        )
        self.triggers = [
            ("public", table, "r", False, False, guard.TRIGGER_NAME, 701, 18, "A", "",
             True, 0, b"", False, 0, 0, 0, False, False, 0)
            for table in guard.TABLES
        ]

    def check(self, *, functions=None, triggers=None, identity=None):
        self.cursor.fetchone.return_value = self.identity if identity is None else identity
        self.cursor.fetchall.side_effect = [
            [self.function] if functions is None else functions,
            self.triggers if triggers is None else triggers,
        ]
        return guard.check_update_guards(self.database, guard.ROLE, 702)

    def test_valid_catalog_uses_only_three_parameterized_selects(self):
        self.assertIsNone(self.check())
        self.assertEqual(self.cursor.execute.call_args_list[0].args, (guard.IDENTITY_SQL,))
        self.assertEqual(self.cursor.execute.call_args_list[1].args,
                         (guard.FUNCTION_SQL, (guard.FUNCTION_NAME,)))
        self.assertEqual(self.cursor.execute.call_args_list[2].args,
                         (guard.TRIGGERS_SQL, (guard.TRIGGER_NAME, 701)))
        for call in self.cursor.execute.call_args_list:
            self.assertTrue(call.args[0].lstrip().startswith("SELECT "))
        self.database.commit.assert_not_called()
        self.database.rollback.assert_not_called()

    def test_wrong_policy_or_owner_refused_before_cursor(self):
        for role, oid in (("other", 702), (guard.ROLE, None), (guard.ROLE, True),
                          (guard.ROLE, 0), (guard.ROLE, "702")):
            with self.subTest(role=role, oid=oid), self.assertRaises(ValueError):
                guard.check_update_guards(self.database, role, oid)
        self.database.cursor.assert_not_called()

    def test_session_identity_parameter_set_and_membership_fail_closed(self):
        for row in (None, ("other", guard.ROLE, False, False),
                    (guard.ROLE, "other", False, False),
                    (guard.ROLE, guard.ROLE, True, False),
                    (guard.ROLE, guard.ROLE, False, True)):
            with self.subTest(row=row), self.assertRaises(ValueError):
                # None represents an absent identity row here, not check()'s default.
                self.cursor.fetchone.return_value = row
                guard.check_update_guards(self.database, guard.ROLE, 702)

    def test_function_missing_or_overloaded_fails_closed(self):
        for rows in ([], [self.function, self.function]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.check(functions=rows)

    def test_every_function_signature_flag_body_path_and_owner_is_attested(self):
        changes = {
            0: 0, 1: "private", 2: "other", 3: "sql", 4: False, 5: "p", 6: 1,
            7: "23", 8: [23], 9: ["i"], 10: ["arg"], 11: 1, 12: 23,
            13: True, 14: True, 15: True, 16: True, 17: "s", 18: "s",
            19: guard.FUNCTION_BODY.replace("session_user", "current_user"),
            20: ["search_path=public"], 21: 999, 22: True,
        }
        for index, value in changes.items():
            row = list(self.function)
            row[index] = value
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.check(functions=[tuple(row)])
        for config in (None, [], ["search_path=pg_catalog, pg_temp", "role=other"]):
            row = list(self.function)
            row[20] = config
            with self.subTest(config=config), self.assertRaises(ValueError):
                self.check(functions=[tuple(row)])

    def test_missing_extra_duplicate_or_wrong_table_attachments_fail_closed(self):
        for rows in ([], self.triggers[:-1], self.triggers + [self.triggers[0]],
                     [self.triggers[0]] * 6):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.check(triggers=rows)

    def test_every_trigger_flag_and_relation_shape_is_attested(self):
        changes = {
            0: "private", 1: "wrong_table", 2: "p", 3: True, 4: True,
            5: "other", 6: 999, 7: 19, 8: "O", 9: "1", 10: False,
            11: 1, 12: b"arg\x00", 13: True, 14: 999, 15: 999, 16: 999,
            17: True, 18: True, 19: 999,
        }
        for index, value in changes.items():
            row = list(self.triggers[0])
            row[index] = value
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.check(triggers=[tuple(row)] + self.triggers[1:])
        for enabled in ("D", "R"):
            row = list(self.triggers[0])
            row[8] = enabled
            with self.subTest(enabled=enabled), self.assertRaises(ValueError):
                self.check(triggers=[tuple(row)] + self.triggers[1:])

    def test_attachment_order_and_bytea_memoryview_are_supported(self):
        rows = [row[:12] + (memoryview(b""),) + row[13:] for row in reversed(self.triggers)]
        self.assertIsNone(self.check(triggers=rows))

    def test_catalog_queries_include_extra_attachments_and_both_inheritance_directions(self):
        self.assertIn("t.tgname = %s OR t.tgfoid = %s", guard.TRIGGERS_SQL)
        self.assertIn("i.inhrelid = c.oid OR i.inhparent = c.oid", guard.TRIGGERS_SQL)
        self.assertIn("'MEMBER'", guard.FUNCTION_SQL)
        self.assertIn("'session_replication_role', 'SET'", guard.IDENTITY_SQL)

    def test_infrastructure_script_matches_literal_body_and_atomic_install_order(self):
        path = Path(__file__).resolve().parents[3] / "deploy/scripts/historical_temperature_import_grants.sql"
        sql = path.read_text(encoding="utf-8")
        self.assertEqual(sql.split("AS $guard$", 1)[1].split("$guard$;", 1)[0], guard.FUNCTION_BODY)
        self.assertIn(f"CREATE FUNCTION public.{guard.FUNCTION_NAME}()", sql)
        self.assertNotIn("CREATE OR REPLACE", sql)
        self.assertIn("SECURITY INVOKER\nSET search_path = pg_catalog, pg_temp", sql)
        self.assertIn(f"ALTER FUNCTION public.{guard.FUNCTION_NAME}() OWNER TO polypbase;", sql)
        self.assertIn(f"REVOKE ALL ON FUNCTION public.{guard.FUNCTION_NAME}() FROM PUBLIC;", sql)
        self.assertEqual(sql.count(f"CREATE TRIGGER {guard.TRIGGER_NAME}\n"), 6)
        self.assertEqual(sql.count("GRANT UPDATE ("), 6)
        for table in guard.TABLES:
            ddl = (f"CREATE TRIGGER {guard.TRIGGER_NAME}\nBEFORE UPDATE ON public.{table}\n"
                   f"FOR EACH STATEMENT EXECUTE FUNCTION public.{guard.FUNCTION_NAME}();")
            enable = f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {guard.TRIGGER_NAME};"
            self.assertIn(ddl, sql)
            self.assertIn(enable, sql)
            self.assertLess(sql.index(enable), sql.index("GRANT UPDATE ("))
        self.assertLess(sql.index("BEGIN;"), sql.index("CREATE FUNCTION"))
        self.assertTrue(sql.rstrip().endswith("COMMIT;"))
        for bypass in ("WHEN (", "UPDATE OF", "pg_trigger_depth", "current_setting", "SET ROLE"):
            self.assertNotIn(bypass, sql)
