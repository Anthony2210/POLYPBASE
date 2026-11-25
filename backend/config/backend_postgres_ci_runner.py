"""Run a selected suite on its pre-migrated disposable DB without Django flush."""

import unittest
from unittest.mock import patch

from django.db import connections
from django.test import TransactionTestCase
from django.test.runner import DiscoverRunner

from .backend_postgres_ci import require, require_service


def test_cases(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_cases(test)
        else:
            yield test


class ServiceTestRunner(DiscoverRunner):
    def build_suite(self, test_labels=None, **kwargs):
        service = require_service()
        suite = super().build_suite(test_labels, **kwargs)
        self.collected = {test.id() for test in test_cases(suite)}
        missing = service.required_tests - self.collected
        require(bool(self.collected) and not missing and not self.test_loader.errors,
                "Missing PostgreSQL coverage: " + ", ".join(sorted(missing)))
        return suite

    def setup_databases(self, *, aliases, serialized_aliases, **kwargs):
        service = require_service()
        require(not self.keepdb and self.parallel == 1 and not serialized_aliases,
                "Service suites require serial execution and fresh database clones.")
        require(set(aliases) == ({"default"} if service.test_template else set()),
                "Unexpected Django database use in service suite.")

    def teardown_databases(self, old_config, **kwargs):
        connections.close_all()

    def run_suite(self, suite, **kwargs):
        service = require_service()
        if service.test_template is None:
            return super().run_suite(suite, **kwargs)

        def reset(test):
            require(test._databases_names(include_mirrors=False) == ["default"],
                    "Service can only reset its default database.")
            service.verify_test_database()
            connections.close_all()
            service.drop_database(service.test_database)
            service.create_database(service.test_database, template=service.test_template)

        with patch.object(TransactionTestCase, "_fixture_teardown", reset):
            return super().run_suite(suite, **kwargs)

    def suite_result(self, suite, result, **kwargs):
        service = require_service()
        skipped = {test.id() for test, _ in result.skipped}
        unexpected = skipped - service.allowed_skips
        if unexpected:
            self.log("Unexpected PostgreSQL skips: " + ", ".join(sorted(unexpected)))
        return (super().suite_result(suite, result, **kwargs) + len(unexpected)
                + len(result.expectedFailures) + int(result.testsRun != len(self.collected)))
