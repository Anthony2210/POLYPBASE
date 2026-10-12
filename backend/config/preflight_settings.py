"""Local deployment checks: in-memory SQLite only, without a PostgreSQL opt-in."""

from .test_settings import *  # noqa: F403


# Override even the disposable PostgreSQL opt-in inherited from test settings.
# Neither environment variables nor dotenv may select a database for preflight.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
