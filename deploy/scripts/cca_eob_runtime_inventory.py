"""Read-only evidence collector for the reviewed CCA-EOB runtime inventory.

trace   runs as polypbase_cca_eob (never root), without any database connection,
        and records the files the runtime interpreter actually loads.
collect runs as the deployment account (never root, never imports application
        code), hashes those exact files and writes the reviewed-inventory input
        of ``cca_eob_runtime_permissions.py build`` plus a review report.

Neither action changes permissions, installs a manifest or connects to PostgreSQL.
Anything the collector cannot classify stops it; the reviewer must decide
explicitly with --extra, --exclude, --accept-alias or --include-distribution.
"""

import argparse
import base64
import csv
import hashlib
import importlib
import importlib.util
import io
import json
import os
import posixpath
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys
from types import SimpleNamespace

TRACE_SCHEMA = "cca_eob_runtime_trace_v1"
RUNTIME = "polypbase_cca_eob"
COMMAND = "cca_eob_production"
SETTINGS = "config.cca_eob_production_settings"
# Function-level imports reached by the production command; a test keeps this
# list aligned with the reviewed command path. Extend only after code review.
LAZY_MODULES = ("pwd",)
# Imported only under the synthetic PG18 QA settings, never by production.
QA_ONLY_MODULES = ("config.historical_temperature_pgqa",)
PSEUDO = ("/proc/", "/sys/", "/dev/")
ORIGINS = ("resource", "dependency", "interpreter", "stdlib")


def trace(app, deny_read):
    """Load the runtime command path like manage.py does, then report files used."""
    import pwd

    if sys.platform != "linux" or os.getuid() == 0 or os.geteuid() == 0:
        raise SystemExit("trace runs only on Linux and never as root")
    if pwd.getpwuid(os.getuid()).pw_name != RUNTIME:
        raise SystemExit(f"trace must run as {RUNTIME}")
    if not deny_read:
        raise SystemExit("trace requires at least one --deny-read private file probe")
    opened, listed, executed = set(), set(), set()

    def hook(event, args):
        path = os.fspath(args[0]) if args and isinstance(args[0], os.PathLike) else args[0] if args else None
        if event == "open" and isinstance(path, (str, bytes)):
            opened.add(os.path.abspath(os.fsdecode(path)))
        elif event in ("os.listdir", "os.scandir") and isinstance(path, (str, bytes)):
            listed.add(os.path.abspath(os.fsdecode(path)))
        elif event == "subprocess.Popen":
            executed.add(os.fsdecode(args[0]) if args[0] is not None else repr(args[1]))

    sys.addaudithook(hook)
    backend = os.path.join(app, "backend")
    os.chdir(backend)
    sys.path.insert(0, backend)
    sys.argv = [os.path.join(backend, "manage.py"), COMMAND]
    os.environ["DJANGO_SETTINGS_MODULE"] = SETTINGS
    import django

    django.setup()
    from django.conf import settings
    from django.core.management import load_command_class
    from django.db import connections
    from django.utils import timezone, translation

    load_command_class("apps.cultures", COMMAND).create_parser("manage.py", COMMAND)
    for name in LAZY_MODULES:
        importlib.import_module(name)
    connection = connections["default"]  # imports the PostgreSQL backend only
    timezone.get_default_timezone()
    translation.activate(settings.LANGUAGE_CODE)
    translation.deactivate()
    if connection.connection is not None:
        raise SystemExit("trace must never open a database connection")

    import site
    import sysconfig

    modules = {os.path.abspath(module.__file__) for module in list(sys.modules.values())
               if isinstance(getattr(module, "__file__", None), str)}
    with open("/proc/self/maps", encoding="utf-8") as maps:
        mapped = {fields[5] for fields in (line.rstrip("\n").split(None, 5) for line in maps)
                  if len(fields) == 6 and fields[5].startswith("/")}
    with open("/proc/self/status", encoding="utf-8") as status:
        no_new_privs = any(line.split() == ["NoNewPrivs:", "1"] for line in status)
    # Imports and site.py list these directories and may silently tolerate a
    # refusal: re-list each one as the runtime to prove effective r+x access.
    directory_access = {}
    for path in sorted(listed):
        try:
            os.listdir(path)
            directory_access[path] = "listable"
        except FileNotFoundError:
            directory_access[path] = "absent"
        except OSError as error:
            directory_access[path] = "denied" if isinstance(error, PermissionError) else "error"
    document = {
        "schema": TRACE_SCHEMA, "runtime": RUNTIME, "uid": os.getuid(), "app": app,
        "no_new_privs": no_new_privs,
        "python": {"executable": sys.executable, "prefix": sys.prefix, "base_prefix": sys.base_prefix,
                   "version": sys.version,
                   "stdlib": sorted({sysconfig.get_path("stdlib"), sysconfig.get_path("platstdlib")}),
                   "site_packages": sorted(set(site.getsitepackages()))},
        "modules": sorted(modules), "opened": sorted(opened), "mapped": sorted(mapped),
        "listed_directories": sorted(listed), "directory_access": directory_access,
        "executed": sorted(executed),
        # os.access uses the real UID: the runtime itself. Probe content is never opened.
        "deny_read": {path: not os.access(path, os.R_OK) for path in deny_read},
        "readable_bytecode": sorted(path for path in opened
                                    if "__pycache__" in path and os.access(path, os.R_OK)),
    }
    sys.stdout.write(json.dumps(document, sort_keys=True, indent=2) + "\n")
    unlistable = [path for path, state in directory_access.items() if state not in ("listable", "absent")]
    if unlistable:
        sys.stderr.write("STOP: the runtime cannot list required directories (r+x): "
                         + ", ".join(unlistable) + "\n")
        raise SystemExit(3)


class LinuxFS:
    """Read-only filesystem view used by collect; tests substitute a fake."""

    def lstat(self, path):
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return SimpleNamespace(kind="missing", uid=None)
        kind = ("link" if stat.S_ISLNK(info.st_mode) else "file" if stat.S_ISREG(info.st_mode)
                else "dir" if stat.S_ISDIR(info.st_mode) else "other")
        return SimpleNamespace(kind=kind, uid=info.st_uid)

    def readlink(self, path):
        return os.readlink(path)

    def read(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def listdir(self, path):
        return sorted(os.listdir(path))


def resolve(fs, path):
    """Return the real path and every symlink crossed, with link/directory owners."""
    pending = list(PurePosixPath(path).parts[1:])
    current, chain = "/", []
    while pending:
        part = pending.pop(0)
        if part == "..":
            current = posixpath.dirname(current)
            continue
        candidate = posixpath.join(current, part)
        info = fs.lstat(candidate)
        if info.kind != "link":
            current = candidate
            continue
        if len(chain) >= 40:
            raise ValueError(f"Too many symbolic links: {path}")
        target = fs.readlink(candidate)
        chain.append((candidate, info.uid, fs.lstat(current).uid))
        if target.startswith("/"):
            current = "/"
        pending = [part for part in PurePosixPath(target).parts if part != "/"] + pending
    return current, chain


def elf_interpreter(data):
    """PT_INTERP of an ELF executable: the kernel needs execute access to it."""
    if data[:4] != b"\x7fELF":
        return None
    wide, order = data[4] == 2, "<" if data[5] == 1 else ">"
    phoff = struct.unpack_from(order + ("Q" if wide else "I"), data, 0x20 if wide else 0x1C)[0]
    size, count = struct.unpack_from(order + "HH", data, 0x36 if wide else 0x2A)
    if not size:
        return None
    for offset in range(phoff, phoff + size * count, size):
        if struct.unpack_from(order + "I", data, offset)[0] == 3:
            start, length = (struct.unpack_from(order + "Q", data, offset + delta)[0] for delta in (8, 32)) \
                if wide else (struct.unpack_from(order + "I", data, offset + delta)[0] for delta in (4, 16))
            return data[start:start + length].split(b"\0", 1)[0].decode()
    return None


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def record_index(fs, sites):
    """Map installed files to (distribution, expected hex SHA-256 or None) from RECORD."""
    index, distributions = {}, {}
    for site in sites:
        for entry in fs.listdir(site):
            if not entry.endswith(".dist-info"):
                continue
            name = canonical(entry[:-len(".dist-info")].rsplit("-", 1)[0])
            record = posixpath.join(site, entry, "RECORD")
            if fs.lstat(record).kind != "file":
                raise ValueError(f"Installed distribution without RECORD: {entry}")
            members = distributions.setdefault(name, [])
            for row in csv.reader(io.StringIO(fs.read(record).decode("utf-8"))):
                if not row:
                    continue
                path = posixpath.normpath(posixpath.join(site, row[0]))
                digest = None
                if len(row) > 1 and row[1].startswith("sha256="):
                    value = row[1][len("sha256="):]
                    digest = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).hex()
                index[path] = (name, digest)
                members.append(path)
    return index, distributions


def under(path, directory):
    return PurePosixPath(path).is_relative_to(directory)


def collect(fs, document, app, tracked, safe_path, scope_review, *, extras=(), excludes=(),
            accept_aliases=(), include_distributions=()):
    """Turn a runtime trace into an exact inventory, or refuse with the reasons."""
    if document.get("schema") != TRACE_SCHEMA or document.get("runtime") != RUNTIME:
        raise ValueError("Unexpected trace document.")
    if not isinstance(document.get("uid"), int) or document["uid"] == 0:
        raise ValueError("The trace must come from the non-root runtime account.")
    if document.get("app") != app:
        raise ValueError("The trace was produced for another checkout.")
    if not document.get("no_new_privs"):
        raise ValueError("The trace must run with NoNewPrivs=1, like the runtime command.")
    if not scope_review.strip():
        raise ValueError("A non-empty scope review reference is required.")
    probes = document.get("deny_read") or {}
    if not probes:
        raise ValueError("The trace carries no private read-denial probe.")
    readable = sorted(path for path, denied in probes.items() if denied is not True)
    if readable:
        raise ValueError(f"The runtime can read private probes: {readable}")
    directory_access = document.get("directory_access")
    if not isinstance(directory_access, dict) or set(directory_access) != set(document["listed_directories"]):
        raise ValueError("The trace lacks the effective runtime directory listing check.")
    unlistable = sorted(path for path, state in directory_access.items() if state not in ("listable", "absent"))
    if unlistable:
        raise ValueError(f"STOP: the runtime cannot list required directories (r+x): {unlistable}")
    runtime_uid, app = document["uid"], safe_path(app)
    backend, python = app / "backend", document["python"]
    # Debian reports absent dist-packages and a venv-local platstdlib: keep real
    # site directories only, and never treat the application tree as stdlib.
    sites = [real for real in (resolve(fs, path)[0] for path in python["site_packages"])
             if fs.lstat(real).kind == "dir"]
    stdlibs = [real for real in (resolve(fs, path)[0] for path in python["stdlib"])
               if fs.lstat(real).kind == "dir" and not under(real, app)]
    if not sites or not stdlibs:
        raise ValueError("The trace names no existing site-packages or stdlib directory.")
    interpreter = resolve(fs, python["executable"])[0]
    loader = elf_interpreter(fs.read(interpreter))
    loader = resolve(fs, loader)[0] if loader else None
    index, distributions = record_index(fs, sites)

    raw = {path: "module" for path in document["modules"]}
    raw.update({path: "opened" for path in document["opened"] if path not in raw})
    raw.update({path: "mapped" for path in document["mapped"] if path not in raw})
    raw.setdefault(python["executable"], "interpreter")
    if python["prefix"] != python["base_prefix"]:
        raw.setdefault(posixpath.join(python["prefix"], "pyvenv.cfg"), "startup")
    for site in sites:  # site.py reads every .pth file at each interpreter start.
        raw.update({posixpath.join(site, name): "startup" for name in fs.listdir(site)
                    if name.endswith(".pth") and posixpath.join(site, name) not in raw})
    for name in include_distributions:
        if canonical(name) not in distributions:
            raise ValueError(f"Unknown installed distribution: {name}")
        raw.update({path: "distribution" for path in distributions[canonical(name)]
                    if any(under(path, site) for site in sites)})

    excludes, accept_aliases = set(excludes), set(accept_aliases)
    files, notes = {}, {key: [] for key in (
        "ignored", "absent", "bytecode", "aliases", "excluded", "unverified", "native")}
    # Root-owned caches cannot be rewritten by the service; others bypass the hashes.
    notes["writable_bytecode"] = sorted(path for path in document["readable_bytecode"]
                                        if fs.lstat(path).uid != 0)
    errors = []
    for path, source in sorted(raw.items()):
        if path.startswith(PSEUDO):
            notes["ignored"].append(path)
            continue
        if path.endswith(" (deleted)"):
            errors.append(f"runtime mapped a deleted file: {path}")
            continue
        real, chain = resolve(fs, path)
        kind = fs.lstat(real).kind
        if kind == "missing":
            notes["absent"].append(path)
            continue
        if kind == "dir":
            continue
        if "__pycache__" in PurePosixPath(real).parts or real.endswith(".pyc"):
            notes["bytecode"].append(real)
            continue
        if kind != "file":
            errors.append(f"not a regular file: {path}")
            continue
        excluded = path in excludes or real in excludes
        if chain:
            notes["aliases"].append(f"{path} -> {real} via {[link for link, _, _ in chain]}")
            if any(runtime_uid in (owner, parent) for _, owner, parent in chain):
                errors.append(f"runtime owns a symlink or its directory on: {path}")
            elif path not in accept_aliases and not excluded:
                errors.append(f"symlinked entry point needs --accept-alias after review: {path}")
        if excluded:
            notes["excluded"].append(real)
            continue
        try:
            safe_path(real)
        except ValueError:
            errors.append(f"runtime loaded a private or mixed path: {real}")
            continue
        relative = PurePosixPath(real).relative_to(app).as_posix() if under(real, app) else None
        if any(under(real, site) for site in sites):
            origin, access = "dependency", "r"
        elif under(real, backend) and real.endswith(".py"):
            if relative not in tracked:
                errors.append(f"backend source outside the tracked_code inventory: {real}")
            continue  # build adds and hashes every tracked backend source itself.
        elif under(real, backend):
            origin, access = "resource", "r"
        elif relative is not None and real != posixpath.join(python["prefix"], "pyvenv.cfg"):
            errors.append(f"application path outside backend and site-packages: {real}")
            continue
        elif any(under(real, directory) for directory in stdlibs):
            origin, access = "stdlib", "r"
        elif real in (interpreter, loader):
            origin, access = "interpreter", "rx"
        elif source in ("startup", "interpreter"):
            origin, access = "interpreter", "r"
        elif source == "mapped" and fs.read(real)[:4] == b"\x7fELF":
            origin, access = "interpreter", "r"
            notes["native"].append(real)
        else:
            if not any(extra.split(":", 2)[-1] == real for extra in extras):
                errors.append(f"unclassified runtime file needs --extra or --exclude: {real} ({source})")
            continue
        files[real] = (origin, access)
    for extra in extras:
        origin, access, path = extra.split(":", 2)
        if origin not in ORIGINS or access not in ("r", "rx"):
            raise ValueError(f"Invalid --extra {extra}; expected ORIGIN:r|rx:/real/path")
        if resolve(fs, path) != (path, []) or fs.lstat(path).kind != "file":
            raise ValueError(f"--extra must name an existing real regular file: {path}")
        if under(path, app) and not (under(path, backend) or any(under(path, site) for site in sites)):
            raise ValueError(f"--extra cannot reach application paths outside backend/site-packages: {path}")
        if files.get(path, (origin, access)) != (origin, access):
            raise ValueError(f"--extra conflicts with the traced classification: {path}")
        safe_path(path)
        files[path] = (origin, access)
    if errors:
        raise ValueError("Runtime inventory refused:\n  " + "\n  ".join(errors))

    entries, coverage = [], {}
    for path, (origin, access) in sorted(files.items()):
        digest = hashlib.sha256(fs.read(path)).hexdigest()
        owner, expected = index.get(path, (None, None))
        if expected is not None and expected != digest:
            raise ValueError(f"Installed file differs from its distribution RECORD: {path}")
        if origin == "dependency" and expected is None:
            notes["unverified"].append(path)
        if owner:
            coverage[owner] = coverage.get(owner, 0) + 1
        entries.append({"path": path, "sha256": digest, "access": access, "origin": origin})
    missing = {"interpreter", "stdlib", "dependency"} - {entry["origin"] for entry in entries}
    if missing:
        raise ValueError(f"Inventory lacks mandatory origins: {sorted(missing)}")
    trace_hash = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
    inventory = {"files": entries, "deny_read": sorted(probes),
                 "scope_review": f"{scope_review.strip()} | trace_sha256={trace_hash}"}
    return inventory, report(document, inventory, notes, coverage, distributions,
                             interpreter, loader, extras, include_distributions)


def report(document, inventory, notes, coverage, distributions, interpreter, loader,
           extras, include_distributions):
    counts = {}
    for entry in inventory["files"]:
        counts[entry["origin"]] = counts.get(entry["origin"], 0) + 1
    lines = [
        "CCA-EOB runtime inventory review (no permission was changed)",
        f"python: {document['python']['version'].split()[0]} executable={document['python']['executable']}",
        f"interpreter: {interpreter}", f"loader: {loader}",
        f"files by origin (tracked backend sources are added by build): {json.dumps(counts, sort_keys=True)}",
        f"deny_read probes refused to the runtime: {inventory['deny_read']}",
        f"scope_review: {inventory['scope_review']}",
        "", "REVIEW: distributions used (inventoried / RECORD files):",
    ]
    lines += [f"  {name}: {coverage[name]}/{len(distributions[name])}" for name in sorted(coverage)]
    unused = sorted(set(distributions) - set(coverage))
    sections = (
        ("REVIEW: distributions installed but not loaded by the trace", unused),
        ("REVIEW: explicitly included distributions", sorted(include_distributions)),
        ("REVIEW: explicit extras", sorted(extras)),
        ("REVIEW: accepted symlinked entry points (the gate checks targets only)", notes["aliases"]),
        ("REVIEW: dependency files without a RECORD hash", notes["unverified"]),
        ("REVIEW: native system libraries mapped into the interpreter", notes["native"]),
        ("REVIEW: subprocesses started during the trace (not inventoried)", document["executed"]),
        ("WARNING: non-root bytecode caches the runtime can read (not covered by hashes)",
         notes["writable_bytecode"]),
        ("INFO: root-owned bytecode caches the runtime can read",
         sorted(set(document["readable_bytecode"]) - set(notes["writable_bytecode"]))),
        ("INFO: excluded by the reviewer", notes["excluded"]),
        ("INFO: bytecode caches attempted (not inventoried)", notes["bytecode"]),
        ("REVIEW: directories the runtime must list (r+x), verified listable as the runtime;"
         " CCA_EOB_RUNTIME_PERMISSIONS_OK checks x only",
         sorted(path for path, state in document["directory_access"].items() if state == "listable")),
        ("INFO: absent directories probed by the runtime",
         sorted(path for path, state in document["directory_access"].items() if state == "absent")),
        ("INFO: absent files probed", notes["absent"]),
        ("INFO: pseudo files ignored", notes["ignored"]),
    )
    for title, values in sections:
        lines += ["", f"{title}: {len(values)}"] + [f"  {value}" for value in values]
    return "\n".join(lines) + "\n"


def write_private(path, text):
    """Exclusive 0600 output: never overwrite a reviewed artifact."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def load_policy():
    spec = importlib.util.spec_from_file_location(
        "cca_eob_runtime_permissions", Path(__file__).with_name("cca_eob_runtime_permissions.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("trace", "collect"))
    parser.add_argument("--app", default="/srv/polypbase/app")
    parser.add_argument("--deny-read", action="append", default=[])
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--scope-review")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--extra", action="append", default=[], help="ORIGIN:r|rx:/real/path")
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--accept-alias", action="append", default=[])
    parser.add_argument("--include-distribution", action="append", default=[])
    args = parser.parse_args()
    if args.action == "trace":
        if args.trace or args.output or args.report or args.extra or args.exclude or args.accept_alias:
            parser.error("trace accepts only --app and --deny-read")
        trace(args.app, args.deny_read)
        return
    if not (args.trace and args.scope_review and args.output and args.report) or args.deny_read:
        parser.error("collect requires --trace, --scope-review, --output and --report; probes come from the trace")
    if sys.platform != "linux" or os.geteuid() == 0:
        parser.error("collect runs on Linux as the deployment account, never as root")
    policy = load_policy()
    inventory, text = collect(
        LinuxFS(), json.loads(args.trace.read_text(encoding="utf-8")), args.app,
        set(policy.tracked_sources(Path(args.app))), policy.safe_path, args.scope_review,
        extras=args.extra, excludes=args.exclude, accept_aliases=args.accept_alias,
        include_distributions=args.include_distribution)
    write_private(args.output, json.dumps(inventory, sort_keys=True, indent=2) + "\n")
    write_private(args.report, text)
    print(f"CCA_EOB_RUNTIME_INVENTORY_WRITTEN files={len(inventory['files'])}")


if __name__ == "__main__":
    main()
