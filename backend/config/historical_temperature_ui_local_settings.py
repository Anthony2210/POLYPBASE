"""Explicit staged local-UI configuration; never a test or server fallback.

Each stage runs in a fresh process. Application-side target, role, system-identity
and approval checks remain mandatory; configuration is not import authorization.
"""

import os

from .temperature_pgqa_backend.runtime_config import disable_dotenv, load_target, required

disable_dotenv()
_unexpected = sorted(name for name in os.environ if name.upper().startswith("PG") and name.upper() != "PGPASSFILE")
if _unexpected:
    raise RuntimeError("Clear libpq environment variables before using the staged UI configuration")
if os.getenv("POSTGRES_DB") or os.getenv("POLYPBASE_TEST_POSTGRES") == "1":
    raise RuntimeError("Clear inherited application and test database configuration")
os.environ["POSTGRES_DB"] = ""
os.environ.setdefault("DJANGO_SECRET_KEY", "django-insecure-disposable-temperature-ui-only")

from .test_settings import *  # noqa: E402,F403

HOST, PORT, DATABASE, SYSTEM_IDENTIFIER, OWNER, RUNTIME = load_target("POLYPBASE_UI_TEMP_DB_")
LOCAL_USER = required("POLYPBASE_UI_TEMP_DB_USER")
STAGE = required("POLYPBASE_UI_TEMP_STAGE")
if LOCAL_USER not in {OWNER, RUNTIME} or STAGE not in {"A", "B", "C", "D"}:
    raise RuntimeError("Explicit staged UI role and stage A, B, C or D are required")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": DATABASE, "HOST": HOST, "PORT": PORT, "USER": LOCAL_USER,
        "PASSWORD": os.environ.get("POLYPBASE_UI_TEMP_DB_PASSWORD", ""),
        "OPTIONS": {"sslmode": "disable", "connect_timeout": 5},
        "CONN_MAX_AGE": 0,
    },
}
HISTORICAL_TEMPERATURE_UI_STAGE = STAGE
HISTORICAL_TEMPERATURE_UI_MIGRATE_ENABLED = STAGE == "B" and os.environ.get("POLYPBASE_UI_TEMP_ALLOW_MIGRATE") == "1"
HISTORICAL_TEMPERATURE_LOCAL_UI_APPLY_ENABLED = STAGE == "C" and os.environ.get("POLYPBASE_UI_TEMP_ENABLE_APPLY") == "1"
DATABASE_ROUTERS = []
