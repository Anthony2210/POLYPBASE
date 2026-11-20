"""Demo configuration and outgoing-effect checks; no database is needed."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from django.core import mail
from django.core.management import call_command, CommandError
from django.test import Client, SimpleTestCase, override_settings

from apps.measurements.iminilide import IminilideClient, IminilideConfigurationError
from apps.taxonomy import worms


DEMO_ENV = {
    "POLYPBASE_DEMO": "1",
    "DEMO_PUBLIC_ORIGIN": "https://demo.example.test",
    "DEMO_SECRET_KEY": "s" * 64,
    "DEMO_DB_NAME": "demo_test",
    "DEMO_DB_USER": "demo_role",
    "DEMO_DB_PASSWORD": "p" * 48,
}


class DemoStartupTests(SimpleTestCase):
    def run_settings(self, env, assertions="", *, module="settings", success=True, error=None):
        # Stub dotenv, use only synthetic values and never call django.setup()
        # or the database driver in these child processes.
        code = (
            "import os\n"
            "from unittest.mock import patch\n"
            "with patch('dotenv.load_dotenv') as dotenv:\n"
            f"    from config import {module} as s\n"
            + "\n".join(f"    {line}" for line in assertions.splitlines())
        )
        child_env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
            env={**child_env, **env}, capture_output=True, text=True, timeout=30,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(f"ImproperlyConfigured: {error}", result.stderr)

    def test_pinned_demo_settings_refuse_an_empty_environment(self):
        self.run_settings({}, module="demo_settings", success=False,
                          error="config.demo_settings requires POLYPBASE_DEMO=1.")

    def test_demo_ignores_ordinary_database_mail_and_public_settings(self):
        self.run_settings({
            **DEMO_ENV,
            "DJANGO_DEBUG": "1", "DJANGO_SECRET_KEY": "ordinary-key",
            "DJANGO_ALLOWED_HOSTS": "*", "DJANGO_CSRF_TRUSTED_ORIGINS": "https://ordinary.invalid",
            "PUBLIC_BASE_URL": "https://ordinary.invalid", "DJANGO_SECURE_SSL_REDIRECT": "0",
            "POSTGRES_DB": "ordinary", "POSTGRES_HOST": "ordinary.invalid",
            "EMAIL_HOST": "ordinary.invalid", "EMAIL_PORT": "invalid",
            "EMAIL_HOST_USER": "ordinary", "EMAIL_HOST_PASSWORD": "ordinary",
            "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend", "EMAIL_DELIVERY_ENABLED": "1",
            "IMINILIDE_EMAIL": "ordinary", "IMINILIDE_PASSWORD": "ordinary",
            "BOX_SHARING_ACCESS_MAX_LIFETIME_SECONDS": "3600",
        }, '''dotenv.assert_not_called()
assert s.DEMO_MODE and not s.DEBUG and s.SECURE_SSL_REDIRECT
assert s.SESSION_COOKIE_SECURE and s.CSRF_COOKIE_SECURE
assert s.SECRET_KEY == os.environ['DEMO_SECRET_KEY']
assert s.ALLOWED_HOSTS == ['demo.example.test']
assert s.PUBLIC_BASE_URL == 'https://demo.example.test'
assert s.CSRF_TRUSTED_ORIGINS == [s.PUBLIC_BASE_URL]
assert s.DATABASES['default'] == {
    'ENGINE': 'django.db.backends.postgresql', 'HOST': '/run/polypbase-demo/postgresql',
    'PORT': '5432', 'NAME': 'demo_test', 'USER': 'demo_role', 'PASSWORD': os.environ['DEMO_DB_PASSWORD'],
}
assert not s.EMAIL_DELIVERY_ENABLED and s.EMAIL_BACKEND.endswith('.dummy.EmailBackend')
assert not hasattr(s, 'EMAIL_HOST_PASSWORD') and not hasattr(s, 'EMAIL_HOST_USER')
assert not s.IMINILIDE_EMAIL and not s.IMINILIDE_PASSWORD and not s.IMINILIDE_BASE_URL
assert s.BOX_SHARING_ACCESS_MAX_LIFETIME_SECONDS is None''', module="demo_settings")

    def test_missing_demo_values_never_fall_back(self):
        for name in DEMO_ENV:
            with self.subTest(name=name):
                env = {key: value for key, value in DEMO_ENV.items() if key != name}
                error = "Demo settings require POLYPBASE_DEMO=1." if name == "POLYPBASE_DEMO" else f"Missing or invalid {name} for demo mode."
                self.run_settings(env, success=False, error=error)

    def test_invalid_values_are_rejected(self):
        for name, value in [
            ("DEMO_PUBLIC_ORIGIN", "http://demo.example.test"),
            ("DEMO_PUBLIC_ORIGIN", "https://ordinary.example.test"),
            ("DEMO_PUBLIC_ORIGIN", "https://demo.example.test@ordinary.invalid"),
            ("DEMO_PUBLIC_ORIGIN", "https://demo.example.test/path"),
            ("DEMO_SECRET_KEY", "short"), ("DEMO_DB_USER", "postgres"),
            ("DEMO_DB_NAME", "postgresql://ordinary.invalid/test"),
            ("DEMO_DB_PASSWORD", "short"),
        ]:
            with self.subTest(name=name, value=value):
                self.run_settings({**DEMO_ENV, name: value}, success=False,
                                  error=f"Missing or invalid {name} for demo mode.")
        for name in ("PGHOSTADDR", "PGSERVICE", "PGOPTIONS"):
            with self.subTest(name=name):
                self.run_settings({**DEMO_ENV, name: "ignored"}, success=False,
                                  error="Remove all PG* environment variables in demo mode.")
        self.run_settings({**DEMO_ENV, "POLYPBASE_DEMO": "true"}, success=False,
                          error="POLYPBASE_DEMO must be 0 or 1.")

    def test_ordinary_settings_remain_compatible(self):
        self.run_settings({
            "DJANGO_SECRET_KEY": "ordinary-key", "POSTGRES_DB": "ordinary",
            "POSTGRES_HOST": "ordinary.invalid", "PUBLIC_BASE_URL": "https://ordinary.invalid/",
            "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        }, '''dotenv.assert_called_once()
assert not s.DEMO_MODE and s.SECRET_KEY == 'ordinary-key'
assert s.DATABASES['default']['HOST'] == 'ordinary.invalid'
assert s.PUBLIC_BASE_URL == 'https://ordinary.invalid'
assert s.EMAIL_DELIVERY_ENABLED and s.EMAIL_BACKEND.endswith('.smtp.EmailBackend')''')
        self.run_settings({"DJANGO_DEBUG": "1", "POLYPBASE_DEMO": "0"}, '''dotenv.assert_called_once()
assert not s.DEMO_MODE and s.DEBUG
assert s.DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3' ''')


class DemoRuntimeTests(SimpleTestCase):
    def test_session_marker_is_demo_only_and_not_cacheable(self):
        client = Client()
        with override_settings(DEMO_MODE=False):
            self.assertEqual(client.get("/api/auth/session/").json(), {"detail": "CSRF cookie set."})
        with override_settings(DEMO_MODE=True):
            response = client.get("/api/auth/session/")
            self.assertIs(response.json()["demo_mode"], True)
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertIn("csrftoken", response.cookies)

    @override_settings(DEMO_MODE=True, PUBLIC_BASE_URL=DEMO_ENV["DEMO_PUBLIC_ORIGIN"])
    def test_new_qr_uses_demo_origin(self):
        from apps.cultures.models import Box
        from apps.cultures.qr import box_qr_image_url, box_scan_url

        box = Box(id=15)
        self.assertEqual(box_scan_url(box), "https://demo.example.test/bac/15/")
        self.assertTrue(box_qr_image_url(box).startswith("https://demo.example.test/"))

    @override_settings(DEMO_MODE=True)
    def test_external_ingestion_refuses_before_side_effects(self):
        with patch("apps.taxonomy.worms.urlopen") as http:
            with self.assertRaises(worms.WormsUnavailable):
                worms.search_by_name("Aurelia aurita")
            with self.assertRaises(worms.WormsUnavailable):
                worms.get_record(123)
            http.assert_not_called()
        with self.assertRaises(IminilideConfigurationError):
            IminilideClient(email="demo@example.test", password="synthetic")
        command = "apps.measurements.management.commands.collect_iminilide"
        with patch(f"{command}.collect_iminilide") as collect, patch(f"{command}.condense_probe_readings") as condense:
            with self.assertRaisesMessage(CommandError, "Probe collection is disabled in demo mode."):
                call_command("collect_iminilide")
            collect.assert_not_called()
            condense.assert_not_called()

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.dummy.EmailBackend")
    def test_dummy_mail_does_not_open_smtp(self):
        with patch("smtplib.SMTP") as smtp, patch("smtplib.SMTP_SSL") as smtp_ssl:
            mail.send_mail("Demo", "Demo", "sender@example.test", ["recipient@example.test"])
            smtp.assert_not_called()
            smtp_ssl.assert_not_called()
