"""Validate explicit demo settings without opening connections."""

import re

from django.core.exceptions import ImproperlyConfigured


def required(env, name, pattern):
    value = env.get(name, "")
    if not re.fullmatch(pattern, value):
        raise ImproperlyConfigured(f"Missing or invalid {name} for demo mode.")
    return value


def demo_configuration(env):
    # libpq environment defaults (notably PGHOSTADDR and PGSERVICE) can
    # redirect a connection even when Django supplies a socket directory.
    if any(name.upper().startswith("PG") for name in env):
        raise ImproperlyConfigured("Remove all PG* environment variables in demo mode.")

    label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    origin = required(env, "DEMO_PUBLIC_ORIGIN", rf"https://demo(?:\.{label})+\.[a-z]{{2,63}}")
    if len(origin.removeprefix("https://")) > 253:
        raise ImproperlyConfigured("Invalid DEMO_PUBLIC_ORIGIN hostname length.")
    secret = required(env, "DEMO_SECRET_KEY", r"[A-Za-z0-9_-]{50,128}")
    # Fixed directory and no TCP, service file, URI, connection string or list
    # of hosts. Infrastructure must control what is mounted at this path.
    db = {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": "/run/polypbase-demo/postgresql",
        "PORT": "5432",
        "NAME": required(env, "DEMO_DB_NAME", r"demo_[a-z0-9_]{1,58}"),
        "USER": required(env, "DEMO_DB_USER", r"demo_[a-z0-9_]{1,58}"),
        "PASSWORD": required(env, "DEMO_DB_PASSWORD", r"[A-Za-z0-9_-]{32,128}"),
    }
    return origin, secret, {"default": db}
