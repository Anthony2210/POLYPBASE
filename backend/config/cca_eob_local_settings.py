"""Explicit credentials and fixed loopback target for the reviewed local correction.

Not a test-runner configuration. No migration or import is authorized by these
settings. Session-level read-only is the default even when --apply is supplied.
"""

import os

from django.core.exceptions import ImproperlyConfigured

from .test_settings import *  # noqa: F403


LOCAL_USER = os.environ.get("POLYPBASE_CCA_LOCAL_USER", "")
if not LOCAL_USER:
    raise ImproperlyConfigured("Provide the verified LOCAL role via POLYPBASE_CCA_LOCAL_USER.")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "polypbase_anomalies_test_20261016",
        "HOST": "127.0.0.1",
        "PORT": "55438",
        "USER": LOCAL_USER,
        # An operator-supplied process variable or libpq's private password file;
        # never fall back to the application's POSTGRES_PASSWORD or shared .env.
        "PASSWORD": os.environ.get("POLYPBASE_CCA_LOCAL_PASSWORD", ""),
        "OPTIONS": {
            "connect_timeout": 5,
            "sslmode": "disable",
            "options": (
                "-c lock_timeout=10000 -c statement_timeout=30000 "
                "-c default_transaction_read_only="
                + ("off" if os.environ.get("POLYPBASE_CCA_LOCAL_ALLOW_WRITE") == "1" else "on")
            ),
        },
    },
}
