"""Offline exact runtime manifest builder and read-only Linux deployment gate.

Never installs permissions. A missing dependency inventory is an error, not a
reason to scan a mixed checkout, venv, home, or interpreter installation.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys

SCHEMA = "cca_eob_runtime_permissions_v1"
RUNTIME = "polypbase_cca_eob"
FORBIDDEN = {".git", ".env", "backups", "logs", "deployments", "node_modules", "__pycache__"}


def safe_path(value):
    if not isinstance(value, str) or not value.startswith("/") or "\\" in value:
        raise ValueError("Expected an exact absolute POSIX path.")
    path = PurePosixPath(value)
    if str(path) != value or any(part in (".", "..") for part in value.split("/")):
        raise ValueError("Non-canonical paths are forbidden.")
    if any(part in FORBIDDEN or part.startswith(".env") for part in path.parts):
        raise ValueError("Secret or mixed private paths cannot receive runtime permissions.")
    return path


def tracked_sources(root):
    result = subprocess.run(
        ["git", "--no-pager", "ls-files", "-z", "--", "backend"], cwd=root,
        capture_output=True, check=True, timeout=30)
    return sorted(path for path in result.stdout.decode().split("\0") if path.endswith(".py")
                  and not any(part.startswith("test") for part in PurePosixPath(path).parts))


def build(root, app, inventory):
    """Build locally from tracked code and a separately reviewed deployment inventory."""
    app = safe_path(app)
    if set(inventory) != {"files", "deny_read", "scope_review"} or not inventory["scope_review"].strip():
        raise ValueError("An explicit reviewed files/deny_read/scope_review inventory is required.")
    files = []
    for relative in tracked_sources(root):
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError("Tracked sources must be regular files, not symlinks.")
        files.append({"path": str(app / relative), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "access": "r", "origin": "tracked_code"})
    # Include every real interpreter/library/package/resource target explicitly.
    # No implicit wildcard, directory read grant or dependency discovery is used.
    files.extend(inventory["files"])
    manifest = {"schema": SCHEMA, "runtime": RUNTIME, "app": str(app), "files": files,
                "deny_read": inventory["deny_read"], "scope_review": inventory["scope_review"]}
    validate(manifest)
    return manifest


def validate(manifest):
    if set(manifest) != {"schema", "runtime", "app", "files", "deny_read", "scope_review"}:
        raise ValueError("Unexpected runtime manifest fields.")
    if manifest["schema"] != SCHEMA or manifest["runtime"] != RUNTIME:
        raise ValueError("Unexpected manifest identity.")
    app = safe_path(manifest["app"])
    if not isinstance(manifest["scope_review"], str) or not manifest["scope_review"].strip():
        raise ValueError("Missing dependency closure review reference.")
    seen, kinds = set(), set()
    for entry in manifest["files"]:
        if set(entry) != {"path", "sha256", "access", "origin"}:
            raise ValueError("Unexpected manifest file fields.")
        path = safe_path(entry["path"])
        if str(path) in seen or not re.fullmatch("[0-9a-f]{64}", entry["sha256"]):
            raise ValueError("Duplicate file or invalid SHA-256.")
        if entry["access"] not in ("r", "rx"):
            raise ValueError("Only file read and explicit executable access are allowed.")
        if entry["origin"] not in ("tracked_code", "resource", "dependency", "interpreter", "stdlib"):
            raise ValueError("Unknown inventory scope.")
        if entry["origin"] in ("tracked_code", "resource") and not path.is_relative_to(app / "backend"):
            raise ValueError("Application code/resources must be scoped to backend.")
        if entry["origin"] == "tracked_code" and path.suffix != ".py":
            raise ValueError("Tracked code must be Python source.")
        seen.add(str(path))
        kinds.add(entry["origin"])
    if not {"tracked_code", "dependency", "interpreter", "stdlib"} <= kinds:
        raise ValueError("Explicit interpreter, stdlib and dependency inventories are mandatory.")
    if not isinstance(manifest["deny_read"], list) or not manifest["deny_read"]:
        raise ValueError("Explicit private read-denial probes are mandatory.")
    for path in manifest["deny_read"]:
        if not isinstance(path, str) or not path.startswith("/") or str(PurePosixPath(path)) != path:
            raise ValueError("Invalid denial probe path.")
        if path in seen:
            raise ValueError("Private paths cannot be granted access.")


def acl_permissions(path, uid, groups):
    """Evaluate POSIX ACLs without running any application code as the runtime."""
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise ValueError(f"Inventory must enumerate real targets without symlinks: {path}")
    result = subprocess.run(["getfacl", "-cpn", "--", str(path)], capture_output=True,
                            text=True, check=True, timeout=10)
    entries = {}
    for line in result.stdout.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("default:"):
            raise ValueError(f"Default ACL requires a separate inventory review: {path}")
        kind, qualifier, rights = line.split(":")
        entries[kind, qualifier] = set(rights) - {"-"}
    mask = entries.get(("mask", ""), set("rwx"))
    if uid == info.st_uid:
        return entries["user", ""]
    if ("user", str(uid)) in entries:
        return entries["user", str(uid)] & mask
    matching = [rights for (kind, qualifier), rights in entries.items() if kind == "group"
                and (int(qualifier) if qualifier else info.st_gid) in groups]
    if matching:
        return set().union(*matching) & mask
    return entries["other", ""]


def protected_manifest(path):
    """The service may read the policy but must not author or replace it."""
    for candidate in (path, *path.parents):
        info = candidate.lstat()
        if info.st_uid != 0 or stat.S_ISLNK(info.st_mode) or info.st_mode & 0o022:
            raise ValueError("Runtime manifest and ancestors must be root-controlled.")
        result = subprocess.run(["getfacl", "-cpn", "--", str(candidate)], capture_output=True,
                                text=True, check=True, timeout=10)
        for line in result.stdout.splitlines():
            line = line.split("#", 1)[0].strip()
            if line and (line.startswith("default:") or
                         ("w" in line.rsplit(":", 1)[-1] and not line.startswith("user::"))):
                raise ValueError("Runtime manifest has a writable or default ACL.")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Runtime manifest must be a regular file.")


def check(root, manifest):
    """Verify exact content, closure coverage and effective read-only access; never repair."""
    import pwd

    validate(manifest)
    account = pwd.getpwnam(RUNTIME)
    uid = account.pw_uid
    if uid == 0:
        raise ValueError("Runtime must not be root.")
    groups = set(os.getgrouplist(RUNTIME, account.pw_gid))
    if manifest["app"] != str(root):
        raise ValueError("Manifest does not match this deployed checkout.")
    expected = {str(root / relative) for relative in tracked_sources(root)}
    actual = {e["path"] for e in manifest["files"] if e["origin"] == "tracked_code"}
    if expected != actual:
        raise ValueError("Tracked Python code inventory has drifted; offline regeneration required.")
    directories = set()
    for entry in manifest["files"]:
        path = Path(entry["path"])
        directories.update(path.parents)
        # Check ancestors before reading, so an alias cannot hide a mixed target.
        for parent in path.parents:
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError(f"Ancestor is not a real directory: {parent}")
            if info.st_uid == uid:
                raise ValueError(f"Runtime must not own an inventoried ancestor: {parent}")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Inventory file is not regular: {path}")
        # Owners can chmod read-only files or directories back to writable.
        if info.st_uid == uid:
            raise ValueError(f"Runtime must not own an inventoried file: {path}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"Runtime file content drift: {path}")
        rights = acl_permissions(path, uid, groups)
        if "w" in rights or not set(entry["access"]) <= rights:
            raise ValueError(f"Runtime file permissions differ: {path}")
    for directory in directories:
        rights = acl_permissions(directory, uid, groups)
        if "x" not in rights or "w" in rights:
            raise ValueError(f"Runtime ancestor must be traversable and not writable: {directory}")
    for name in manifest["deny_read"]:
        path = Path(name)
        if not path.exists() or path.is_symlink():
            raise ValueError("Denial probes must name existing real private files.")
        if "r" in acl_permissions(path, uid, groups):
            raise ValueError("Runtime can read a private denial probe.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "check"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--app", default="/srv/polypbase/app")
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    if args.action == "build":
        if not args.inventory or args.manifest:
            parser.error("build requires only --inventory (reviewed offline dependency inventory)")
        manifest = build(args.root, args.app, json.loads(args.inventory.read_text(encoding="utf-8")))
        sys.stdout.buffer.write((json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode())
    else:
        if not args.manifest or args.inventory or sys.platform != "linux":
            parser.error("check requires --manifest and Linux; it never changes ACLs")
        protected_manifest(args.manifest)
        check(args.root, json.loads(args.manifest.read_text(encoding="utf-8")))
        print("CCA_EOB_RUNTIME_PERMISSIONS_OK")


if __name__ == "__main__":
    main()
