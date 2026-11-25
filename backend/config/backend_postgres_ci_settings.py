"""Django configuration reachable only from the active service runner."""

from .backend_postgres_ci import require_service

_service = require_service()

from .test_settings import *  # noqa: E402,F403

DATABASES = {"default": _service.django_config(_service.prefix + "bootstrap")}
DATABASE_ROUTERS = []
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
EMAIL_DELIVERY_ENABLED = False
TEST_RUNNER = "config.backend_postgres_ci_runner.ServiceTestRunner"
