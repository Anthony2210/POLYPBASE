"""Create and verify a PostgreSQL custom-format backup from Django settings."""

import argparse
import datetime
import hashlib
import os
import subprocess
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

    database = settings.DATABASES["default"]
    if database["ENGINE"] != "django.db.backends.postgresql":
        raise SystemExit("The configured database is not PostgreSQL.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    target = args.output_dir / f"{args.label}-{timestamp}.dump"
    partial = target.with_suffix(".dump.part")

    environment = os.environ.copy()
    environment["PGPASSWORD"] = database["PASSWORD"]
    environment["PGSSLMODE"] = database.get("OPTIONS", {}).get("sslmode", "prefer")

    command = [
        "/usr/bin/pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-acl",
        "--no-password",
        "--file",
        str(partial),
        "--host",
        database["HOST"],
        "--port",
        str(database["PORT"]),
        "--username",
        database["USER"],
        database["NAME"],
    ]

    try:
        subprocess.run(command, check=True, env=environment)
        subprocess.run(
            ["/usr/bin/pg_restore", "--list", str(partial)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        partial.replace(target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        print(f"BACKUP_OK {target.name} {target.stat().st_size} bytes sha256={digest}")

        if args.retention_days is not None:
            if args.retention_days < 1:
                raise SystemExit("--retention-days must be at least 1.")
            cutoff = datetime.datetime.now(datetime.UTC).timestamp() - (
                args.retention_days * 24 * 60 * 60
            )
            for backup in args.output_dir.glob(f"{args.label}-*.dump"):
                if backup != target and backup.stat().st_mtime < cutoff:
                    backup.unlink()
                    print(f"BACKUP_REMOVED {backup.name}")
    finally:
        partial.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
