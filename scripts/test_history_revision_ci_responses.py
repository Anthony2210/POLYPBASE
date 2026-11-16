"""Offline regressions for CI transport and narrowly typed SQL rejection assertions."""

import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHILD = r'''
import os
import socket
import sys
sys.path.insert(0, "backend")

class ForbiddenSocket(socket.socket):
    def connect(self, *args, **kwargs):
        raise AssertionError("Offline regressions must never connect sockets")
    def connect_ex(self, *args, **kwargs):
        raise AssertionError("Offline regressions must never connect sockets")
socket.socket = ForbiddenSocket

import django
django.setup()
from django.conf import settings
from django.db import DataError, IntegrityError, connection
from django.test import Client, override_settings
from django.middleware.security import SecurityMiddleware
from django.http import HttpResponse
from types import SimpleNamespace
from unittest.mock import Mock, patch
from contextlib import nullcontext
from uuid import uuid4
from django.core.exceptions import ValidationError
from apps.cultures.history_preview_api import BoxHistoryPreviewAPIView
from apps.cultures.test_history_revision_codec import HistoryRevisionCodecStorageTests
from apps.cultures.test_history_revision_models import HistoryRevisionStorageTests

assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
assert settings.DATABASES["default"]["NAME"] == ":memory:"
assert not settings.DEBUG and not settings.RUNNING_TESTS
assert settings.SECURE_SSL_REDIRECT
assert settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE
assert settings.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")
'''


class HistoryRevisionCIResponseTests(unittest.TestCase):
    def run_offline(self, code):
        # Do not inherit database configuration, dotenv, or hosted-runner opt-ins.
        environment = {
            name: value for name, value in os.environ.items()
            if name.upper() in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "TEMP", "TMP", "PATH"}
        }
        environment.update({
            "DJANGO_SETTINGS_MODULE": "config.preflight_settings",
            "PYTHON_DOTENV_DISABLED": "1",
            "POLYPBASE_TEST_POSTGRES": "0",
            "DJANGO_DEBUG": "0",
            "DJANGO_SECRET_KEY": "offline-history-revision-response-tests",
            "DJANGO_SECURE_SSL_REDIRECT": "1",
            "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
            "EMAIL_DELIVERY_ENABLED": "0",
        })
        result = subprocess.run(
            [sys.executable, "-B", "-c", CHILD + code], cwd=ROOT,
            env=environment, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_http_redirect_is_from_security_middleware_before_preview(self):
        self.run_offline(r'''
original = SecurityMiddleware.process_request
with override_settings(ALLOWED_HOSTS=["testserver"]), patch.object(
    SecurityMiddleware, "process_request", autospec=True,
    side_effect=lambda self, request: original(self, request),
) as security, patch.object(BoxHistoryPreviewAPIView, "dispatch") as preview:
    response = Client().get("/api/boxes/1/history/preview/")
    assert response.status_code == 301, dict(response.headers)
    assert response["Location"] == "https://testserver/api/boxes/1/history/preview/"
    assert "Cache-Control" not in response and "Vary" not in response
    assert response["X-Content-Type-Options"] == "nosniff"
    assert security.call_count == 1
    preview.assert_not_called()
''')

    def test_codec_helper_uses_https_and_keeps_security_and_read_only_assertions(self):
        self.run_offline(r'''
case = HistoryRevisionCodecStorageTests(
    "test_real_source_r1_r2_r3_store_decode_compose_and_preview_never_reads_overlay",
)
case.org = SimpleNamespace(pk=7)
case.url = lambda: "/api/boxes/1/history/preview/"
case.window = lambda: {"start": "2026-01-01", "end": "2026-01-02"}
client = Mock()
case.api_client = lambda: client

def response(status=200, cache="private, no-store", vary="Cookie, X-Organization-Id"):
    result = HttpResponse(status=status)
    if cache is not None:
        result["Cache-Control"] = cache
    if vary is not None:
        result["Vary"] = vary
    return result

client.get.return_value = response()
assert case.request() is client.get.return_value
client.get.assert_called_once_with(
    case.url(), data=case.window(), secure=True, HTTP_X_ORGANIZATION_ID="7",
)
for invalid in (response(301, None, None), response(cache=None),
                response(cache="public"), response(vary=None), response(vary="Cookie")):
    client.get.return_value = invalid
    with case.assertRaises(AssertionError) as caught:
        case.request()
    assert "Preview GET status=" in str(caught.exception)
    assert "headers=" in str(caught.exception)

def write_during_request(*args, **kwargs):
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA user_version = 1")
    return response()
client.get.side_effect = write_during_request
with case.assertRaises(AssertionError) as caught:
    case.request()
assert "PRAGMA user_version" in str(caught.exception)
''')

    def test_only_overlength_key_accepts_data_error_including_subclasses(self):
        self.run_offline(r'''
class DriverDataError(DataError):
    pass
class DriverIntegrityError(IntegrityError):
    pass

def exercise(key, error):
    case = HistoryRevisionStorageTests("test_derived_identity_sql_uniqueness_and_key_shape")
    case.stream = SimpleNamespace(pk=uuid4())
    case.other = SimpleNamespace(pk=uuid4())
    identity = SimpleNamespace(derived_source_key="b" * 64)
    def sql(query, params):
        actual = params[3]
        if actual == key:
            raise error("Synthetic SQL rejection")
        if params[1] == case.other.pk.hex or actual == "c" * 64:
            return
        raise IntegrityError("Synthetic shape or uniqueness rejection")
    case.sql = sql
    with patch("apps.cultures.test_history_revision_models.transaction.atomic", side_effect=nullcontext), patch(
        "apps.cultures.test_history_revision_models.BoxHistoryEventIdentity.objects.create",
        side_effect=[identity, ValidationError("Invalid key"), ValidationError("Invalid key")],
    ):
        case.test_derived_identity_sql_uniqueness_and_key_shape()

for accepted in (DataError, DriverDataError, IntegrityError, DriverIntegrityError):
    exercise("a" * 65, accepted)
for strict_key in ("", "a" * 63, "A" * 64, "x" * 64):
    exercise(strict_key, DriverIntegrityError)
    try:
        exercise(strict_key, DriverDataError)
    except DriverDataError:
        pass
    else:
        raise AssertionError("DataError accepted outside the overlength case")
for rejected in (RuntimeError, TypeError):
    try:
        exercise("a" * 65, rejected)
    except rejected:
        pass
    else:
        raise AssertionError("Unrelated exception accepted for the overlength key")
''')


if __name__ == "__main__":
    unittest.main()
