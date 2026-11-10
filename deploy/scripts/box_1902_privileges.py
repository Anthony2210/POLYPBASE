#!/usr/bin/env python3
"""Emit Box 1902 SQL for independent review. Never connect or execute SQL.

No Django settings, environment files, credentials or database drivers are loaded.
"""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from apps.measurements.box_1902_sql import (  # noqa: E402
    approval_sql, provision_sql, retirement_sql,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("provision", help="Emit separately authorized topology transition")
    approval = commands.add_parser("approve", help="Emit approval from exact reviewed artifacts")
    approval.add_argument("--plan", type=Path, required=True)
    approval.add_argument("--manifest", type=Path, required=True)
    approval.add_argument("--actor-id", type=int, required=True)
    approval.add_argument("--expires-at", required=True, help="Explicit ISO timestamp with UTC offset")
    commands.add_parser("retire", help="Emit retirement without provenance loss")
    args = parser.parse_args(argv)
    if args.command == "provision":
        output = provision_sql()
    elif args.command == "retire":
        output = retirement_sql()
    else:
        import json
        from datetime import datetime

        expiry = datetime.fromisoformat(args.expires_at)
        if expiry.tzinfo is None:
            parser.error("--expires-at must include a UTC offset")
        output = approval_sql(
            args.plan.read_text(encoding="utf-8"), args.actor_id,
            json.loads(args.manifest.read_text(encoding="utf-8")), args.expires_at,
        )
    sys.stdout.write(output)


if __name__ == "__main__":
    main()
