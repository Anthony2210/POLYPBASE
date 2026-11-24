"""Generate the French API route inventory without dispatching requests."""

import argparse
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/reference/api.md"
PREFLIGHT_SETTINGS = "config.preflight_settings"
API_PREFIX = "/api/"
PREFLIGHT_ENV = {
    "DJANGO_SETTINGS_MODULE": PREFLIGHT_SETTINGS,
    "PYTHON_DOTENV_DISABLED": "1",
    "POLYPBASE_TEST_POSTGRES": "0",
    "POLYPBASE_DEMO": "0",
    "DJANGO_DEBUG": "0",
    "DJANGO_SECRET_KEY": "unused-api-doc-key",
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "EMAIL_DELIVERY_ENABLED": "0",
}

HEADER = """<!-- Document g\u00e9n\u00e9r\u00e9 par scripts/gen_api_doc.py. Ne pas modifier \u00e0 la main. -->

# R\u00e9f\u00e9rence des routes API

Inventaire des routes `/api/` du r\u00e9solveur Django. Les routes web hors API
ne sont pas list\u00e9es. Les chevrons indiquent les param\u00e8tres de route Django.

Les m\u00e9thodes indiqu\u00e9es correspondent aux gestionnaires d\u00e9clar\u00e9s par la vue.
`HEAD` implicite (d\u00e9riv\u00e9 de `GET`) et `OPTIONS` fourni par Django ou DRF
sont omis. Cet inventaire ne d\u00e9crit ni les permissions, ni les corps des
requ\u00eates, ni les codes de r\u00e9ponse.

Depuis la racine du d\u00e9p\u00f4t, avec les d\u00e9pendances backend install\u00e9es :

```sh
python scripts/gen_api_doc.py
python scripts/gen_api_doc.py --check
```

La g\u00e9n\u00e9ration utilise `config.preflight_settings` dans un environnement
isol\u00e9, sans charger de fichier `.env` ni ouvrir de connexion \u00e0 une base
de donn\u00e9es. `--check` ne modifie pas le fichier et \u00e9choue s'il manque ou
si son contenu ne correspond pas au r\u00e9solveur.

| Route | M\u00e9thodes HTTP | Vue |
| --- | --- | --- |
"""


def api_patterns(patterns, prefix="/"):
    from django.urls import URLPattern, URLResolver

    for entry in patterns:
        route = prefix + str(entry.pattern)
        if isinstance(entry, URLResolver) and (
            route.startswith(API_PREFIX) or API_PREFIX.startswith(route)
        ):
            yield from api_patterns(entry.url_patterns, route)
        elif isinstance(entry, URLPattern) and route.startswith(API_PREFIX):
            yield route, entry.callback


def describe_view(route, callback):
    from django.views import View
    from rest_framework.views import APIView

    view = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
    if view is None:
        raise RuntimeError(f"Cannot infer HTTP methods for {route}")

    actions = getattr(callback, "actions", None)
    initkwargs = getattr(callback, "initkwargs", None)
    if initkwargs is None:
        initkwargs = getattr(callback, "view_initkwargs", {})
    allowed = initkwargs.get("http_method_names", view.http_method_names)
    if actions is not None:
        # DRF adds HEAD mapping to the GET action at runtime.
        methods = set(actions)
        if "get" in actions and actions.get("head") == actions["get"]:
            methods.discard("head")
    else:
        methods = {name for name in allowed if callable(getattr(view, name, None))}

    options = getattr(view, "options", None)
    if callable(options) and options not in (View.options, APIView.options):
        methods.add("options")
    elif actions is None:
        methods.discard("options")

    methods = sorted(name.upper() for name in methods.intersection(allowed))
    if not methods:
        raise RuntimeError(f"No explicit HTTP methods found for {route}")
    return route, ", ".join(f"`{name}`" for name in methods), f"{view.__module__}.{view.__qualname__}"


def forbid_connection(*args, **kwargs):
    raise RuntimeError("Database connections are forbidden during API doc generation")


def generate():
    with (
        patch.dict(os.environ, PREFLIGHT_ENV, clear=True),
        patch.object(sys, "path", [str(ROOT / "backend"), *sys.path]),
        patch.object(sys, "dont_write_bytecode", True),
    ):
        import django
        from django.conf import settings
        from django.db.backends.base.base import BaseDatabaseWrapper
        from django.urls import get_resolver

        with patch.object(BaseDatabaseWrapper, "connect", forbid_connection):
            databases = settings.DATABASES
            default_db = databases.get("default", {})
            if (
                settings.SETTINGS_MODULE != PREFLIGHT_SETTINGS
                or set(databases) != {"default"}
                or default_db.get("ENGINE") != "django.db.backends.sqlite3"
                or default_db.get("NAME") != ":memory:"
            ):
                raise RuntimeError("API doc generation requires isolated preflight settings")
            django.setup(set_prefix=False)
            rows = sorted(
                describe_view(route, callback)
                for route, callback in api_patterns(get_resolver().url_patterns)
            )
    if not rows:
        raise RuntimeError("No API routes found in the Django resolver")
    return HEADER + "".join(f"| `{route}` | {methods} | `{view}` |\n" for route, methods, view in rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check the reference without writing it")
    args = parser.parse_args()
    generated = generate()
    if args.check:
        try:
            matches = OUTPUT.read_text(encoding="utf-8") == generated
        except (FileNotFoundError, UnicodeDecodeError):
            matches = False
        if not matches:
            print("docs/reference/api.md is missing or stale; run python scripts/gen_api_doc.py", file=sys.stderr)
            return 1
        print("docs/reference/api.md is up to date")
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(generated, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
