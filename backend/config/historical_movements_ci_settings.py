"""Fixed ephemeral PostgreSQL settings for the dedicated GitHub-hosted CI job.

These settings never consume database credentials or endpoints from the caller.
They do not enable historical acceptance, which still requires in-memory SQLite.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


_REQUIRED_ENVIRONMENT = {
    "GITHUB_ACTIONS": "true",
    "RUNNER_ENVIRONMENT": "github-hosted",
    "RUNNER_OS": "Linux",
    "HM_CI_POSTGRES": "1",
    "POSTGRES_DB": "",
    "POLYPBASE_TEST_POSTGRES": "0",
}
for _name, _expected in _REQUIRED_ENVIRONMENT.items():
    if os.environ.get(_name) != _expected:
        raise ImproperlyConfigured(
            f"Historical movement CI requires {_name} to match the dedicated workflow configuration."
        )
if (Path(__file__).resolve().parent.parent / ".env").exists():
    raise ImproperlyConfigured("Historical movement CI refuses a backend/.env file.")
if os.environ.get("DATABASE_URL") or any(
    value for key, value in os.environ.items() if key.startswith("PG")
):
    raise ImproperlyConfigured("Historical movement CI refuses external libpq/database configuration.")

from .test_settings import *  # noqa: E402,F403


# Port, names and role belong exclusively to the service declared in the workflow.
# Empty password is intentional: trust auth is confined to the disposable runner.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "historical_movements_ci",
        "USER": "historical_movements_ci",
        "PASSWORD": "",
        "HOST": "127.0.0.1",
        "PORT": "55439",
        "TEST": {"NAME": "test_historical_movements_ci"},
        "OPTIONS": {"options": "-c lock_timeout=10000 -c statement_timeout=60000"},
        "CONN_MAX_AGE": 0,
    }
}
DATABASE_ROUTERS = []
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
EMAIL_DELIVERY_ENABLED = False
