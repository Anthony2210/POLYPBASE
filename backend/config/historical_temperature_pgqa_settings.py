"""Opt-in settings for the single authorized PostgreSQL 18 rehearsal target."""

import os

from . import historical_temperature_pgqa as qa

qa.require_opt_in()
if os.getenv("POSTGRES_DB"):
    raise RuntimeError("Inherited application database configuration must be cleared")

from .temperature_pgqa_backend.runtime_config import disable_dotenv

disable_dotenv()
from .settings import *  # noqa: E402,F403

HISTORICAL_TEMPERATURE_PGQA = True
HISTORICAL_TEMPERATURE_LOCAL_UI_APPLY_ENABLED = False
TEST_RUNNER = "config.historical_temperature_pgqa.IsolatedTemperatureRunner"
DATABASES = {
    "default": {
        "ENGINE": qa.ENGINE, "NAME": qa.DATABASE, "HOST": qa.HOST, "PORT": qa.PORT,
        "USER": qa.OWNER, "PASSWORD": qa.CREDENTIALS[qa.OWNER],
        "OPTIONS": {
            "sslmode": "disable", "connect_timeout": 5, "options": qa.OPTIONS,
            "application_name": "polypbase-temperature-isolated-qa", "passfile": os.devnull,
        },
        "TEST": {"NAME": qa.DATABASE},
        "CONN_MAX_AGE": 0,
    },
}
DATABASE_ROUTERS = []
MIGRATION_MODULES = {}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
DEBUG = False
SECRET_KEY = "django-insecure-disposable-temperature-qa-only"
SECURE_SSL_REDIRECT = False
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
