"""Pinned settings entry point for the isolated demo process."""

import os

from django.core.exceptions import ImproperlyConfigured

if os.getenv("POLYPBASE_DEMO") != "1":
    raise ImproperlyConfigured("config.demo_settings requires POLYPBASE_DEMO=1.")

from .settings import *  # noqa: E402,F403
