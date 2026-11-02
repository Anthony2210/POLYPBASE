"""CLI-only CCA-EOB peer login. Never read the application .env or alter its service."""

import sys

_runtime = getattr(sys, "_cca_eob_runtime", None)
if _runtime is None:
    raise RuntimeError("STOP: use the dedicated cca_eob_runtime.py source-only launcher")
if not _runtime["evidence"]()["site_initialized"]:
    raise RuntimeError("STOP: the dedicated runtime must complete site initialization")

import os
import secrets

from django.core.exceptions import ImproperlyConfigured

if len(sys.argv) < 2 or sys.argv[1] != "cca_eob_production":
    raise ImproperlyConfigured("These settings are only for the dedicated cca_eob_production command.")
if any(name.upper().startswith(("PG", "POSTGRES_")) for name in os.environ):
    raise ImproperlyConfigured("Start the correction in a clean environment without PG*/POSTGRES_* overrides.")

# python-dotenv is pinned in the lockfile and honors this switch before opening .env.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["DJANGO_DEBUG"] = "0"
# No sessions, credentials or signatures from the normal service are needed.
os.environ["DJANGO_SECRET_KEY"] = secrets.token_urlsafe(64)

from .settings import *  # noqa: E402,F403

DEBUG = False
DATABASE_ROUTERS = []
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "polypbase",
        "USER": "polypbase_cca_eob",
        "PASSWORD": "",
        "HOST": "/var/run/postgresql",
        "PORT": "5433",
        "OPTIONS": {"sslmode": "disable"},
        "CONN_MAX_AGE": 0,
    }
}
