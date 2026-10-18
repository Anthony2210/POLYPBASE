"""The local correction command must remain read-only unless explicitly applied."""

import json
import os
import runpy
from io import StringIO
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from apps.measurements.models import BiologicalMeasurement

from .historical_2026.cca_eob_correction import fingerprint
from .management.commands.correct_cca_eob_v2 import Command
from .test_cca_eob_correction import CcaEobFixtureMixin


class CcaEobLocalSettingsTests(SimpleTestCase):
    def load_settings(self, **environment):
        with patch.dict(os.environ, {"POLYPBASE_CCA_LOCAL_USER": "",
                                     "POLYPBASE_CCA_LOCAL_PASSWORD": "",
                                     "POLYPBASE_CCA_LOCAL_ALLOW_WRITE": "", **environment}):
            return runpy.run_module("config.cca_eob_local_settings")["DATABASES"]["default"]

    def test_local_role_is_explicit_without_fallback_to_application_credentials(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "verified LOCAL role"):
            self.load_settings()

    def test_fixed_test_copy_is_read_only_by_default(self):
        config = self.load_settings(POLYPBASE_CCA_LOCAL_USER="synthetic-local-role")
        self.assertEqual((config["HOST"], config["PORT"], config["NAME"]),
                         ("127.0.0.1", "55438", "polypbase_anomalies_test_20261016"))
        self.assertEqual(config["USER"], "synthetic-local-role")
        self.assertEqual(config["PASSWORD"], "")
        self.assertIn("default_transaction_read_only=on", config["OPTIONS"]["options"])

    def test_write_capability_requires_exact_separate_opt_in(self):
        for flag in ("", "true", "0", "1"):
            with self.subTest(flag=flag):
                config = self.load_settings(POLYPBASE_CCA_LOCAL_USER="synthetic-local-role",
                                            POLYPBASE_CCA_LOCAL_ALLOW_WRITE=flag)
                self.assertIn("default_transaction_read_only=" + ("off" if flag == "1" else "on"),
                              config["OPTIONS"]["options"])


class CcaEobCommandTests(CcaEobFixtureMixin, TestCase):
    def command(self, **overrides):
        output, errors = StringIO(), StringIO()
        options = {"organization_id": self.org.pk, "workbook": self.workbook_path,
                   "stdout": output, "stderr": errors}
        options.update(overrides)
        call_command("correct_cca_eob_v2", **options)
        return json.loads(output.getvalue()), errors.getvalue()

    def test_default_dry_run_prints_exact_json_and_hash_without_writes(self):
        plan = self.plan()
        before = self.database_snapshot()
        payload, errors = self.command()
        self.assertEqual(payload, {"plan_hash": fingerprint(plan), "plan": plan})
        self.assertEqual(payload["plan_hash"], fingerprint(payload["plan"]))
        self.assertIn("Dry-run only: no writes", errors)
        self.assertIn("Ready=True", errors)
        self.assertEqual(self.database_snapshot(), before)

    def test_actor_and_hash_without_apply_do_not_enable_writes(self):
        before = self.database_snapshot()
        payload, errors = self.command(actor=self.admin.username, expected_plan_hash=fingerprint(self.plan()))
        self.assertTrue(payload["plan"]["ready"])
        self.assertIn("Dry-run only", errors)
        self.assertEqual(self.database_snapshot(), before)

    def test_apply_requires_both_actor_and_reviewed_hash(self):
        for overrides in ({}, {"actor": self.admin.username}, {"expected_plan_hash": fingerprint(self.plan())}):
            with self.subTest(overrides=overrides):
                before = self.database_snapshot()
                with self.assertRaisesMessage(CommandError, "--apply requires --actor and --expected-plan-hash"):
                    self.command(apply=True, **overrides)
                self.assertEqual(self.database_snapshot(), before)

    def test_blocked_dry_run_prints_json_exits_nonzero_and_does_not_write(self):
        BiologicalMeasurement.objects.filter(pk=self.measurements[0].pk).update(polyp_count=999)
        plan = self.plan()
        self.assertFalse(plan["ready"])
        before = self.database_snapshot()
        output, errors = StringIO(), StringIO()
        command = Command(stdout=output, stderr=errors)
        # A real CLI exits and closes all connections; keep the test transaction.
        with patch("django.core.management.base.connections.close_all"):
            with self.assertRaises(SystemExit) as raised:
                command.run_from_argv([
                    "manage.py", "correct_cca_eob_v2", "--organization-id", str(self.org.pk),
                    "--workbook", self.workbook_path,
                ])
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(json.loads(output.getvalue()), {"plan_hash": fingerprint(plan), "plan": plan})
        self.assertIn("Ready=False", errors.getvalue())
        self.assertIn("Blocked: inspect the JSON matrix", errors.getvalue())
        self.assertEqual(self.database_snapshot(), before)

    def test_apply_prints_reviewed_json_and_rejects_reusing_old_hash(self):
        reviewed = self.plan()
        reviewed_hash = fingerprint(reviewed)
        payload, errors = self.command(apply=True, actor=self.admin.username, expected_plan_hash=reviewed_hash)
        self.assertEqual(payload, {"plan_hash": reviewed_hash, "plan": reviewed})
        self.assertIn("Applied 3 reassignment(s)", errors)
        before = self.database_snapshot()
        with self.assertRaisesMessage(CommandError, "Stale plan"):
            self.command(apply=True, actor=self.admin.username, expected_plan_hash=reviewed_hash)
        self.assertEqual(self.database_snapshot(), before)
        refreshed, _ = self.command()
        self.assertNotEqual(refreshed["plan_hash"], reviewed_hash)
        payload, errors = self.command(
            apply=True, actor=self.admin.username, expected_plan_hash=refreshed["plan_hash"],
        )
        self.assertEqual(payload, refreshed)
        self.assertIn("Applied 0 reassignment(s)", errors)
        self.assertEqual(self.database_snapshot(), before)

    def test_effective_connection_is_checked_before_actor_lookup(self):
        reviewed_hash = fingerprint(self.plan())
        with patch("apps.cultures.management.commands.correct_cca_eob_v2.correction._verify_connection",
                   side_effect=ValueError("effective PostgreSQL connection rejected")), patch(
                       "apps.cultures.management.commands.correct_cca_eob_v2.get_user_model",
                   ) as user_model:
            with self.assertRaisesMessage(CommandError, "effective PostgreSQL connection rejected"):
                self.command(apply=True, actor=self.admin.username, expected_plan_hash=reviewed_hash)
            user_model.assert_not_called()

    def test_unknown_or_unauthorized_actor_is_a_command_error_without_writes(self):
        for actor in ("missing-cca-account", self.writer.username):
            with self.subTest(actor=actor):
                before = self.database_snapshot()
                with self.assertRaises(CommandError):
                    self.command(apply=True, actor=actor, expected_plan_hash=fingerprint(self.plan()))
                self.assertEqual(self.database_snapshot(), before)
