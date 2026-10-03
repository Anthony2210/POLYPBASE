"""Disposable local Docker PostgreSQL only, for portable-lineage race tests.

Run postgres:17 with --rm, a tmpfs data directory, POSTGRES_DB=phase5qa,
POSTGRES_HOST_AUTH_METHOD=trust and port 127.0.0.1:55435:5432. These settings
never inherit a database address or credentials from the environment.
Do not use this unauthenticated container outside local isolated QA.
"""

from .test_settings import *  # noqa: F403


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "phase5qa",
        "USER": "postgres",
        "PASSWORD": "",
        "HOST": "127.0.0.1",
        "PORT": "55435",
        "CONN_MAX_AGE": 0,
        "TEST": {"NAME": "test_phase5qa"},
    }
}
