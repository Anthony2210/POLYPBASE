"""Settings used by automated tests.

Tests must never connect to the shared PostgreSQL database configured in the
local .env file. SQLite keeps the test run isolated and reproducible.
"""

from .settings import *  # noqa: F403


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]
