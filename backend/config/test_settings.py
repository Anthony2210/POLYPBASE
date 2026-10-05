"""Settings used by automated tests.

Tests must never connect to the shared PostgreSQL database configured in the
local .env file. SQLite keeps the test run isolated and reproducible.
"""

import os

from .settings import *  # noqa: F403


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Explicit opt-in for the disposable empty QA container, never inherited DB
# credentials/hosts from .env. Its loopback port is not the shared local copy.
if os.getenv("POLYPBASE_TEST_POSTGRES") == "1":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "subculture_qa",
            "USER": "subculture_qa",
            "PASSWORD": "",
            "HOST": "127.0.0.1",
            "PORT": "55439",
            "TEST": {"NAME": "test_subculture_qa"},
            "OPTIONS": {"options": "-c lock_timeout=10000 -c statement_timeout=30000"},
        }
    }

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]
