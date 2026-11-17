"""Back up the deployment database with strictly validated PostgreSQL 18 clients."""

import argparse
import copy
import datetime
import os
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/srv/polypbase/backups"),
    )
    parser.add_argument("--label", default="backup")
    parser.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help="Delete older backups with the same label after a successful backup.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.retention_days is not None and args.retention_days < 1:
        raise SystemExit("--retention-days must be at least 1.")
    # libpq routing/role overrides must not affect the completeness connection.
    for key in list(os.environ):
        if key.upper().startswith("PG"):
            del os.environ[key]
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.umask(0o077)
    sys.path.insert(0, str(Path.cwd()))

    import django

    django.setup()
    from django.conf import settings

    from config.database_backup import create_complete_archive

    database = copy.deepcopy(settings.DATABASES["default"])
    if database["ENGINE"] != "django.db.backends.postgresql":
        raise SystemExit("The configured database is not PostgreSQL.")

    backup_user = os.environ.get("POLYPBASE_BACKUP_USER")
    if backup_user is not None:
        if not backup_user.strip():
            raise SystemExit("POLYPBASE_BACKUP_USER must not be empty.")
        database["USER"] = backup_user
        # Never reuse the runtime password or its password file for another login.
        database["PASSWORD"] = os.environ.get("POLYPBASE_BACKUP_PASSWORD", "")
        database.setdefault("OPTIONS", {})["passfile"] = os.devnull
    elif "POLYPBASE_BACKUP_PASSWORD" in os.environ:
        raise SystemExit("POLYPBASE_BACKUP_PASSWORD requires POLYPBASE_BACKUP_USER.")

    archive = create_complete_archive(database, args.output_dir, args.label, client_major=18)
    target = Path(archive["path"])
    print(
        f"BACKUP_OK {target.name} {archive['size_bytes']} bytes "
        f"sha256={archive['sha256']}"
    )

    if args.retention_days is not None:
        cutoff = datetime.datetime.now(datetime.UTC).timestamp() - (
            args.retention_days * 24 * 60 * 60
        )
        for backup in args.output_dir.resolve().glob(f"{args.label}-*.dump"):
            if backup != target and backup.stat().st_mtime < cutoff:
                backup.unlink()
                print(f"BACKUP_REMOVED {backup.name}")


if __name__ == "__main__":
    main()
