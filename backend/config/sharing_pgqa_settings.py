"""Opt-in settings for PostgreSQL concurrency evidence of Box reader sharing.

Only for a disposable, empty, local cluster created for this purpose: fixed
loopback endpoint, fixed names, trust authentication, no credential or host
taken from the environment or from `backend/.env`.

The schema is created from the current models, not from the migration chain:
`measurements.0018` installs the Box 1902 capability and requires a
provisioned role topology that a throwaway cluster does not have. Row locks,
transaction isolation and the constraints declared on the models are what
these tests exercise; anything installed only by RunPython/RunSQL migrations
is absent here and is not covered by this evidence.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

if os.environ.get("POLYPBASE_SHARING_PGQA") != "1":
    raise ImproperlyConfigured("Set POLYPBASE_SHARING_PGQA=1 to use the disposable sharing QA cluster.")
if os.environ.get("POLYPBASE_TEST_POSTGRES") == "1":
    raise ImproperlyConfigured("POLYPBASE_TEST_POSTGRES must not be combined with the sharing QA settings.")
if (Path(__file__).resolve().parent.parent / ".env").exists():
    raise ImproperlyConfigured("Sharing QA refuses a backend/.env file.")
if os.environ.get("DATABASE_URL") or any(
    value for key, value in os.environ.items() if key.startswith("PG") or key.startswith("POSTGRES_")
):
    raise ImproperlyConfigured("Sharing QA refuses external libpq/database configuration.")

from .test_settings import *  # noqa: E402,F403
from .test_settings import LOCAL_APPS  # noqa: E402

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "sharing_pgqa",
        "USER": "sharing_pgqa",
        "PASSWORD": "",
        "HOST": "127.0.0.1",
        "PORT": "55441",
        "TEST": {"NAME": "test_sharing_pgqa"},
        "OPTIONS": {"options": "-c lock_timeout=10000 -c statement_timeout=30000"},
        "CONN_MAX_AGE": 0,
    }
}
DATABASE_ROUTERS = []
# Model-synced schema; see the module docstring.
MIGRATION_MODULES = {
    **{config.split(".")[1]: None for config in LOCAL_APPS},
    # Synced together so foreign keys to auth tables resolve in one pass.
    **{label: None for label in ("auth", "contenttypes", "sessions", "admin")},
}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
