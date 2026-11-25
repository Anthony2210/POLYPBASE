"""Offline checks for the service boundary; never connect to PostgreSQL."""

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from config import backend_postgres_ci as ci


class ServiceBoundaryTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, ci.SERVICE_ENV, clear=True)
        platform = patch.object(ci.sys, "platform", "linux")
        path = patch.object(ci, "ROOT", Path(__file__).resolve().parent / "missing-test-checkout")
        for guard in (environment, platform, path):
            guard.start()
            self.addCleanup(guard.stop)

    def test_exact_hosted_runner_and_fixed_endpoint(self):
        ci.check_environment()
        for var, value in (("GITHUB_ACTIONS", "false"), ("RUNNER_ENVIRONMENT", "self-hosted"),
                           ("POSTGRES_HOST", "localhost"), ("POSTGRES_PORT", "5433"),
                           ("POSTGRES_DB", "polypbase"), ("POSTGRES_USER", "developer"),
                           ("POSTGRES_PASSWORD", "not-the-fixture-password"),
                           ("PGHOSTADDR", "192.0.2.1"), ("PGSERVICE", "override"),
                           ("DATABASE_URL", "postgresql://not-the-service")):
            with self.subTest(var=var), patch.dict(os.environ, {var: value}), self.assertRaises(RuntimeError):
                ci.check_environment()
        with self.assertRaises(RuntimeError):
            ci.require_service()  # Environment alone does not enable Django settings.

    def test_initial_connection_refuses_wrong_version_or_existing_inventory(self):
        for version, names in ((170000, []), (180000, [("existing_app",), ("postgres",)])):
            with self.subTest(version=version), patch.object(ci.Service, "_raw") as raw:
                conn = raw.return_value
                conn.execute.return_value.fetchone.side_effect = [
                    (version, "cluster", "startup", "postgres", "postgres", "postgres", 5432), (True,),
                ]
                conn.execute.return_value.fetchall.return_value = names
                with self.assertRaises(RuntimeError), ci.Service():
                    self.fail("Unexpected service accepted")
                conn.close.assert_called_once()
                self.assertIsNone(ci.active())
                self.assertTrue(all(call.args[0].startswith("SELECT") for call in conn.execute.call_args_list))

    def test_ignored_or_replaced_database_is_never_dropped(self):
        service = ci.Service()
        self.assertNotEqual(service.prefix, ci.Service().prefix)
        name = service.prefix + "species"
        service.databases[name] = 100
        service.maintenance = Mock()
        with patch.object(service, "_verify_maintenance"), patch.object(service, "_catalog_oid", return_value=101):
            with self.assertRaisesRegex(RuntimeError, "identity changed"):
                service.drop_database(name)
        service.maintenance.execute.assert_not_called()
        with patch.object(service, "_raw") as raw, self.assertRaisesRegex(RuntimeError, "unowned"):
            service.connect("postgres")
        raw.assert_not_called()

    def test_restricted_login_requires_matching_live_database_identity(self):
        service = ci.Service()
        name = service.prefix + "box"
        service.databases[name] = 100
        service.identity = (180000, "cluster", "startup")
        conn = Mock()
        with patch.object(service, "server_key", return_value=service.identity):
            conn.execute.return_value.fetchone.return_value = (
                100, name, "box1902_migrator", "box1902_owner", "startup", 5432,
            )
            service.verify(conn, name, "box1902_migrator", "box1902_owner")
            with self.assertRaisesRegex(RuntimeError, "OID or login"):
                service.verify(conn, name, "box1902_migrator")


if __name__ == "__main__":
    unittest.main()
