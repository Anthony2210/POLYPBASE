"""Run the reviewed Box 1902 first-install transition as the cluster administrator.

Container-only. Connects over the disposable peer socket as the SQL ``postgres``
role and executes the exact reviewed ``provision_sql()``. No application settings,
database lifecycle or application credentials are used. This is the separately
authorized provisioning step, never a migration or a backup shortcut.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path("/opt/pca")
DATABASE = "synthetic_pca_ci"
PORT = 65432


def main():
    credentials = json.load(sys.stdin)
    if (type(credentials) is not dict
            or set(credentials) != {"owner_password", "runtime_password"}
            or any(not isinstance(value, str) or not value for value in credentials.values())):
        raise ValueError("Missing disposable role credentials")
    sys.path.insert(0, str(ROOT / "backend"))
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            INSTALLED_APPS=[], DATABASES={}, USE_TZ=True,
            DEFAULT_AUTO_FIELD="django.db.models.AutoField",
            SECRET_KEY="django-insecure-disposable-pca-unix-ci-provisioning",
        )
    import django

    django.setup()
    from apps.measurements.box_1902_sql import provision_sql

    import psycopg
    from psycopg import sql

    with psycopg.connect(host="/var/run/postgresql", port=PORT, dbname=DATABASE,
                         user="postgres", password=None, connect_timeout=5,
                         passfile="/dev/null", sslmode="disable", autocommit=True) as connection:
        connection.execute(provision_sql())
        connection.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
            sql.Identifier("box1902_migrator"), sql.Literal(credentials["owner_password"])))
    print("Box 1902 V2 topology provisioned", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
