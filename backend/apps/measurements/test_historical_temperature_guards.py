"""Migration control-flow proofs without opening a database or executing SQL."""

from importlib import import_module

from unittest.mock import MagicMock

from django.test import SimpleTestCase


class GuardMigrationContractTests(SimpleTestCase):
    def setUp(self):
        self.guards = import_module("apps.measurements.migrations.0012_historical_temperature_guards")
        self.receipt = import_module("apps.measurements.migrations.0013_historical_temperature_receipt")
        self.editor = MagicMock()
        self.editor.connection.vendor = "postgresql"
        self.cursor = self.editor.connection.cursor.return_value.__enter__.return_value
        self.cursor.fetchone.return_value = (False,)

    def test_forward_installs_only_on_empty_postgres_and_never_imports_data(self):
        self.guards.forwards(None, self.editor)
        self.assertEqual(self.cursor.execute.call_count, 2)
        self.assertIn("ACCESS EXCLUSIVE", self.cursor.execute.call_args_list[0].args[0])
        self.editor.execute.assert_called_once_with(self.guards.SQL)
        self.assertNotIn("INSERT INTO", self.guards.SQL)

    def test_nonempty_forward_and_reverse_refuse_before_changing_guards(self):
        self.cursor.fetchone.return_value = (True,)
        for operation in (self.guards.forwards, self.guards.backwards):
            with self.assertRaises(RuntimeError):
                operation(None, self.editor)
        self.editor.execute.assert_not_called()

    def test_empty_reverse_has_explicit_sql_not_a_silent_irreversible_operation(self):
        self.guards.backwards(None, self.editor)
        self.editor.execute.assert_called_once_with(self.guards.REVERSE_SQL)

    def test_receipt_reverse_locks_and_refuses_nonempty_custody(self):
        self.cursor.fetchone.return_value = (True,)
        with self.assertRaises(RuntimeError):
            self.receipt.remove_guards(None, self.editor)
        self.assertIn("ACCESS EXCLUSIVE", self.cursor.execute.call_args_list[0].args[0])
        self.editor.execute.assert_not_called()

    def test_sqlite_does_not_claim_or_install_postgres_guarantees(self):
        self.editor.connection.vendor = "sqlite"
        for operation in (self.guards.forwards, self.guards.backwards, self.receipt.guards, self.receipt.remove_guards):
            operation(None, self.editor)
        self.editor.execute.assert_not_called()
        self.editor.connection.cursor.assert_not_called()

    def test_guard_contract_includes_deferred_evidence_and_reverse_relationship_checks(self):
        sql = self.guards.SQL
        for fragment in ("BEFORE UPDATE OR DELETE", "BEFORE TRUNCATE", "DEFERRABLE INITIALLY DEFERRED",
                         "NEW.polyp_value", "NEW.ephyrae_value", "FOR SHARE", "NEW.organization_id",
                         "NEW.global_code", "SET search_path = pg_catalog, public, pg_temp"):
            self.assertIn(fragment, sql)
        # Contract checks are not PostgreSQL execution or syntax validation.

    def test_invalidation_is_status_only_monotone_and_pending_is_not_insertable(self):
        sql = self.guards.SQL
        for fragment in (
            "OLD.association_status = 'confirmed' AND NEW.association_status = 'pending_review'",
            "NEW.association_status IS DISTINCT FROM 'confirmed'",
            "r.association_status IS DISTINCT FROM 'confirmed'",
            "WHERE biological_measurement_id = OLD.id AND association_status = 'confirmed'",
            "NEW.biological_measurement_id, NEW.iso_year, NEW.iso_week, NEW.temperature_c, NEW.created_at",
            "NEW.strain_id, NEW.box_number",
            "Historical temperature biological Box cannot be reassigned",
            "Historical temperature institution cannot be reassigned",
        ):
            self.assertIn(fragment, sql)
        self.assertIn("DROP FUNCTION historical_temperature_reading_immutable()", self.guards.REVERSE_SQL)
