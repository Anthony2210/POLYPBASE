"""Mock-only checks for cleanup on the fixed, leased PCA target."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase


class PgqaCleanupTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Import configuration without inheriting any real QA target.
        spec = spec_from_file_location("config._pgqa_cleanup_unit",
                                       Path(__file__).with_name("historical_temperature_pgqa.py"))
        cls.qa = module_from_spec(spec)
        with patch("config.temperature_pgqa_backend.runtime_config.load_target", return_value=(
                "127.0.0.1", "65432", "synthetic_unit", "123", "unit_owner", "unit_runtime")):
            spec.loader.exec_module(cls.qa)
        cls._real_lease_gate = staticmethod(cls.qa.require_run_lease)

    def setUp(self):
        self.connection = MagicMock()
        self.cursor = self.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.return_value = ("O",)
        self.enterContext(patch("django.db.connection", self.connection))
        self.atomic = self.enterContext(patch("django.db.transaction.atomic"))
        self.owner = self.enterContext(patch.object(self.qa, "require_owner"))
        self.lease = self.enterContext(patch.object(self.qa, "require_run_lease"))
        self.schema = self.enterContext(patch.object(self.qa, "verify_schema"))
        self.grant = self.enterContext(patch.object(self.qa, "grant_runtime"))
        modules = self.enterContext(patch.object(self.qa, "import_module"))
        modules.return_value.REVERSE_SQL = "Mock historical guard removal"
        self.enterContext(patch.object(self.qa, "OWNER_ROLE", None))
        self.flush = self.enterContext(patch("django.core.management.call_command"))

    def statements(self):
        return [call.args[0] for call in self.cursor.execute.call_args_list]

    def test_unverified_owner_or_lease_never_mutates(self):
        for gate in (self.owner, self.lease, self.schema):
            with self.subTest(gate=gate):
                gate.side_effect = RuntimeError("Not attested")
                with self.assertRaisesMessage(RuntimeError, "Not attested"):
                    self.qa.cleanup_test_data()
                gate.side_effect = None
                self.cursor.execute.assert_not_called()
                self.atomic.assert_not_called()
                self.flush.assert_not_called()

    def test_real_lease_gate_refuses_missing_closed_or_unsafe_lease(self):
        # The remaining gates are mocked; this gate is the production implementation.
        with patch.object(self.qa, "require_run_lease", self._real_lease_gate):
            for lease, empty_start, unsafe in ((None, True, False),
                                              (MagicMock(), True, False),
                                              (MagicMock(), False, False),
                                              (MagicMock(), True, True)):
                with self.subTest(empty_start=empty_start, unsafe=unsafe), \
                        patch.object(self.qa, "RUN_LEASE", lease), \
                        patch.object(self.qa, "EMPTY_START_VERIFIED", empty_start), \
                        patch.object(self.qa, "UNSAFE_WORKERS", unsafe):
                    with self.assertRaisesMessage(RuntimeError, "live exclusive lease"):
                        self.qa.cleanup_test_data()
        self.cursor.execute.assert_not_called()
        self.flush.assert_not_called()

    def test_only_alias_truncate_guard_is_suspended_inside_atomic_cleanup(self):
        def flush(*args, **kwargs):
            self.atomic.return_value.__enter__.assert_called_once()
            self.atomic.return_value.__exit__.assert_not_called()
            self.assertIn(self.disable, self.statements())
            self.assertNotIn(self.enable, self.statements())

        self.flush.side_effect = flush
        self.qa.cleanup_test_data()
        statements = self.statements()
        self.assertLess(statements.index(self.disable), statements.index(self.enable))
        self.assertEqual([sql for sql in statements if "ALTER TABLE" in sql],
                         [self.disable, self.enable])
        self.assertFalse(any("session_replication_role" in sql for sql in statements))
        self.flush.assert_called_once_with("flush", database="default", interactive=False,
                                          verbosity=0, reset_sequences=False, inhibit_post_migrate=True)
        self.atomic.return_value.__exit__.assert_called_once_with(None, None, None)
        self.assertEqual(self.schema.call_count, 2)

    def test_missing_or_disabled_alias_guard_refuses_cleanup(self):
        for row in (None, ("D",), ("R",), ("A",)):
            with self.subTest(row=row):
                self.cursor.fetchone.return_value = row
                with self.assertRaisesMessage(RuntimeError, "alias TRUNCATE guard"):
                    self.qa.cleanup_test_data()
                self.flush.assert_not_called()
                self.assertNotIn(self.disable, self.statements())

    def test_failed_flush_exits_atomic_with_error_for_ddl_rollback(self):
        failure = ValueError("Flush failed")
        self.flush.side_effect = failure
        with self.assertRaisesMessage(ValueError, "Flush failed"):
            self.qa.cleanup_test_data()
        self.assertIn(self.disable, self.statements())
        self.atomic.return_value.__exit__.assert_called_once()
        self.assertIs(self.atomic.return_value.__exit__.call_args.args[1], failure)
        self.grant.assert_not_called()

    disable = ("ALTER TABLE public.cultures_boxlocalidalias "
               "DISABLE TRIGGER no_truncate_box_local_id_aliases")
    enable = ("ALTER TABLE public.cultures_boxlocalidalias "
              "ENABLE TRIGGER no_truncate_box_local_id_aliases")


