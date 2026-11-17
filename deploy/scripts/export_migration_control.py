"""Export reviewed Git blobs without changing the application checkout.

The manifest digest must be carried over an authenticated operator channel;
shipping a manifest next to its files does not authenticate either of them.
"""

import argparse
import hashlib
from pathlib import Path
import re
import subprocess


FILES = (
    "deploy/scripts/run_dedicated_migrations.sh",
    "deploy/scripts/backup_database.py",
    "deploy/scripts/trusted_deployment_backup.py",
    "backend/config/database_backup.py",
    "deploy/scripts/box_1902_privileges.py",
    "backend/apps/measurements/box_1902_sql.py",
)


def git(repository, *args):
    return subprocess.check_output(
        ["git", "--no-pager", "-C", str(repository), *args],
        stdin=subprocess.DEVNULL, timeout=30,
    )


def export(repository, commit, destination):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Use the full reviewed lowercase commit SHA.")
    if git(repository, "rev-parse", "--verify", f"{commit}^{{commit}}").decode().strip() != commit:
        raise ValueError("Reviewed commit is unavailable.")
    # Read every required blob before creating output: untracked files cannot ship.
    blobs = {}
    for name in FILES:
        mode = git(repository, "ls-tree", commit, "--", name).split(b" ", 1)[0]
        if mode not in (b"100644", b"100755"):
            raise ValueError(f"Required reviewed regular Git file is missing: {name}")
        blobs[name] = git(repository, "show", f"{commit}:{name}")
    blobs["CONTROL_COMMIT"] = (commit + "\n").encode("ascii")
    destination = Path(destination).resolve()
    destination.mkdir(mode=0o700)  # Never overwrite an earlier bundle.
    lines = []
    for name, data in sorted(blobs.items()):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {name}\n")
    manifest = "".join(lines).encode("ascii")
    (destination / "SHA256SUMS").write_bytes(manifest)
    digest = hashlib.sha256(manifest).hexdigest()
    print(f"CONTROL_BUNDLE commit={commit} manifest_sha256={digest} directory={destination}")
    return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commit")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    export(args.repository, args.commit, args.destination)


if __name__ == "__main__":
    main()
