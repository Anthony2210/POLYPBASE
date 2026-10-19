"""SQLite-only integration tests without historical SQL migrations or dotenv."""

import os

import dotenv

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
dotenv.load_dotenv = lambda *args, **kwargs: False
# The base test module contains a legacy PostgreSQL opt-in. Never select it here.
if os.environ.get("POLYPBASE_TEST_POSTGRES") == "1":
    raise RuntimeError("Use the isolated PostgreSQL launcher, not SQLite settings")
os.environ["POSTGRES_DB"] = ""
os.environ.setdefault("DJANGO_SECRET_KEY", "django-insecure-disposable-temperature-qa-only")

from .test_settings import *  # noqa: E402,F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
MIGRATION_MODULES = {name: None for name in (
    "accounts", "organizations", "taxonomy", "cultures", "measurements", "exports", "audit",
    "admin", "auth", "contenttypes", "sessions",
)}
