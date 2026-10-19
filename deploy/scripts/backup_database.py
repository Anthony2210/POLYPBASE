"""Create and verify a PostgreSQL custom-format backup from Django settings."""

import argparse
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
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.umask(0o077)
    sys.path.insert(0, str(Path.cwd()))

    import django

    django.setup()
    from django.conf import settings

    from config.database_backup import create_archive

    database = settings.DATABASES["default"]
    if database["ENGINE"] != "django.db.backends.postgresql":
        raise SystemExit("The configured database is not PostgreSQL.")

    archive = create_archive(database, args.output_dir, args.label)
    target = Path(archive["path"])
    print(
        f"BACKUP_OK {target.name} {archive['size_bytes']} bytes "
        f"sha256={archive['sha256']}"
    )

    if args.retention_days is not None:
        if args.retention_days < 1:
            raise SystemExit("--retention-days must be at least 1.")
        cutoff = datetime.datetime.now(datetime.UTC).timestamp() - (
            args.retention_days * 24 * 60 * 60
        )
        for backup in args.output_dir.resolve().glob(f"{args.label}-*.dump"):
            if backup != target and backup.stat().st_mtime < cutoff:
                backup.unlink()
                print(f"BACKUP_REMOVED {backup.name}")


if __name__ == "__main__":
    main()
