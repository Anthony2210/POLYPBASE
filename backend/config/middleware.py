import logging
import re
from contextlib import ExitStack
from time import perf_counter_ns

from django.db import connections


slow_api_logger = logging.getLogger("polypbase.slow_api")


class SlowApiLoggingMiddleware:
    """Log request and database timings without recording request or SQL data."""

    THRESHOLD_NS = 500_000_000

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path_info != "/api" and not request.path_info.startswith("/api/"):
            return self.get_response(request)

        started_ns = perf_counter_ns()
        query_count = query_ns = 0
        response = None

        def measure_query(execute, sql, params, many, context):
            nonlocal query_count, query_ns
            query_started_ns = perf_counter_ns()
            try:
                return execute(sql, params, many, context)
            finally:
                query_count += 1
                query_ns += perf_counter_ns() - query_started_ns

        try:
            with ExitStack() as stack:
                for connection in connections.all():
                    stack.enter_context(connection.execute_wrapper(measure_query))
                response = self.get_response(request)
        finally:
            elapsed_ns = perf_counter_ns() - started_ns
            if elapsed_ns > self.THRESHOLD_NS:
                match = request.resolver_match
                slow_api_logger.warning(
                    "slow_api route=%s status=%s duration_ms=%.3f query_count=%d query_ms=%.3f",
                    match.view_name if match else "unresolved",
                    response.status_code if response is not None else "exception",
                    elapsed_ns / 1_000_000,
                    query_count,
                    query_ns / 1_000_000,
                )
        return response


class BrowserSecurityHeadersMiddleware:
    """Add browser protections to Django responses, including API errors."""

    CONTENT_SECURITY_POLICY = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    )
    PERMISSIONS_POLICY = "camera=(self), microphone=(), geolocation=()"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Content-Security-Policy", self.CONTENT_SECURITY_POLICY)
        response.setdefault("Permissions-Policy", self.PERMISSIONS_POLICY)
        if request.path.startswith("/api/measurement-import/"):
            # Include errors produced outside the diagnostic API view.
            response["Cache-Control"] = "no-store"
        if re.fullmatch(r"/api/boxes/[0-9]+/history/preview/", request.path):
            # Cover native CSRF refusals that never reach APIView finalization.
            response["Cache-Control"] = "private, no-store"
        return response
