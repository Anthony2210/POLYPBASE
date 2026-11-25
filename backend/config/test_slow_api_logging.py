import os
import sqlite3
from importlib.util import module_from_spec, spec_from_file_location
from unittest.mock import Mock, patch

from django.db import connection
from django.db.backends.utils import CursorWrapper
from django.http import HttpResponse
from django.test import Client, RequestFactory, SimpleTestCase, override_settings
from django.urls import path

from config import settings as app_settings
from config.middleware import SlowApiLoggingMiddleware


MIDDLEWARE_PATH = "config.middleware.SlowApiLoggingMiddleware"
LOGGER_NAME = "polypbase.slow_api"


def import_preview(request, item_id, token):
    cursor = CursorWrapper(Mock(), connection)
    cursor.execute("SELECT %s /* sql-marker */", ["query-parameter"])
    cursor.execute("SELECT 1")
    return HttpResponse(status=204)


def failing_view(request):
    driver_cursor = Mock()
    driver_cursor.execute.side_effect = sqlite3.OperationalError("private driver error")
    CursorWrapper(driver_cursor, connection).execute("SELECT 1")


def fast_view(request):
    return HttpResponse(status=204)


urlpatterns = [
    path("api/measurement-import/<int:item_id>/<str:token>/", import_preview, name="import-preview"),
    path("api/failing/", failing_view, name="failing"),
    path("api/fast/", fast_view, name="fast"),
]


class SlowApiSettingsTests(SimpleTestCase):
    def test_middleware_and_logger_are_registered_only_when_opted_in(self):
        for flag, expected in ((None, False), ("0", False), ("1", True)):
            with self.subTest(flag=flag):
                env = {"DJANGO_DEBUG": "0", "DJANGO_SECRET_KEY": "unused-test-key"}
                if flag is not None:
                    env["POLYPBASE_LOG_SLOW_API"] = flag
                spec = spec_from_file_location("config._isolated_settings", app_settings.__file__)
                settings = module_from_spec(spec)
                with patch.dict(os.environ, env, clear=True), patch("dotenv.load_dotenv"):
                    spec.loader.exec_module(settings)

                self.assertEqual(settings.POLYPBASE_LOG_SLOW_API, expected)
                self.assertEqual(MIDDLEWARE_PATH in settings.MIDDLEWARE, expected)
                self.assertEqual(LOGGER_NAME in getattr(settings, "LOGGING", {}).get("loggers", {}), expected)


@override_settings(
    ROOT_URLCONF=__name__,
    MIDDLEWARE=[MIDDLEWARE_PATH, "config.middleware.BrowserSecurityHeadersMiddleware"],
)
class SlowApiLoggingTests(SimpleTestCase):
    def clock(self, *milliseconds):
        return patch(
            "config.middleware.perf_counter_ns",
            side_effect=[ms * 1_000_000 for ms in milliseconds],
        )

    def test_slow_response_logs_only_route_and_aggregate_metrics(self):
        with self.clock(0, 10, 30, 40, 45, 510), self.assertLogs(LOGGER_NAME) as logged:
            response = self.client.get(
                "/api/measurement-import/987654/private-token/?secret=private-query",
                HTTP_AUTHORIZATION="Bearer private-credential",
            )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn("Content-Security-Policy", response)
        self.assertEqual(len(logged.records), 1)
        self.assertEqual(
            logged.records[0].getMessage(),
            "slow_api route=import-preview status=204 duration_ms=510.000 query_count=2 query_ms=25.000",
        )
        self.assertFalse(hasattr(logged.records[0], "request"))
        self.assertIsNone(logged.records[0].exc_info)
        self.assertFalse(connection.execute_wrappers)

    def test_requests_at_or_below_threshold_do_not_log(self):
        for duration in (499, 500):
            with self.subTest(duration=duration), self.clock(0, duration), self.assertNoLogs(LOGGER_NAME):
                self.assertEqual(self.client.get("/api/fast/").status_code, 204)

    def test_failed_query_is_counted_and_timed_in_500_response(self):
        with (
            self.clock(0, 100, 150, 600),
            self.assertLogs(LOGGER_NAME) as logged,
            self.assertLogs("django.request"),
        ):
            response = Client(raise_request_exception=False).get("/api/failing/")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            [record.getMessage() for record in logged.records],
            ["slow_api route=failing status=500 duration_ms=600.000 query_count=1 query_ms=50.000"],
        )
        self.assertIsNone(logged.records[0].exc_info)
        self.assertFalse(connection.execute_wrappers)

    def test_unresolved_api_path_logs_fixed_identifier(self):
        with self.clock(0, 501), self.assertLogs(LOGGER_NAME) as logged:
            self.assertEqual(self.client.get("/api/unknown-secret/").status_code, 404)

        self.assertEqual(
            [record.getMessage() for record in logged.records],
            ["slow_api route=unresolved status=404 duration_ms=501.000 query_count=0 query_ms=0.000"],
        )

    def test_non_api_request_skips_timing_and_database_wrapping(self):
        request = RequestFactory().get("/apiary/")
        expected = HttpResponse()
        middleware = SlowApiLoggingMiddleware(lambda request: expected)
        with self.clock() as clock, patch("config.middleware.connections") as connections:
            self.assertIs(middleware(request), expected)
        clock.assert_not_called()
        connections.all.assert_not_called()

    def test_escaping_exception_is_preserved_and_wrappers_removed(self):
        request = RequestFactory().get("/api/failing/")
        error = RuntimeError("private exception detail")
        middleware = SlowApiLoggingMiddleware(Mock(side_effect=error))
        with self.clock(0, 501), self.assertLogs(LOGGER_NAME) as logged:
            with self.assertRaises(RuntimeError) as captured:
                middleware(request)

        self.assertIs(captured.exception, error)
        self.assertFalse(connection.execute_wrappers)
        self.assertEqual(
            [record.getMessage() for record in logged.records],
            ["slow_api route=unresolved status=exception duration_ms=501.000 query_count=0 query_ms=0.000"],
        )
