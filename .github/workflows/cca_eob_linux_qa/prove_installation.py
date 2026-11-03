"""Fail-closed, QA-only installation evidence for the fixed CCA-EOB target.

Run with a trusted CPython 3.13 outside the tracked /app tree, for example:
  python -I -S -B -X pycache_prefix=/dev/null /qa/prove_installation.py

Dockerfile/runner contract (this script never installs or repairs anything):
* /app is the exact TARGET checkout, including an image-build-populated .git;
  /app/.venv is a fresh, root-owned CPython 3.13 uv 0.13.6 locked install.
* Run as polypbase_cca_eob, uid/gid 10001, no supplementary groups other than
  10001, --read-only --network none --security-opt no-new-privileges, /tmp writable.
* Provide git, getfacl, uv on /usr/local/bin:/usr/bin:/bin, and readable /proc.
  The system Python and native library targets must be root controlled.
* Root-owned synthetic /app/backend/.env and /private/probe are regular 0600
  files; /private is a real root-owned 0755 directory. Never use real secrets.
* /qa/install-receipt.json is root-owned, non-writable by runtime, and records
  target, uv_version, uv_lock_sha256, fresh_venv=true, and command (argv list of
  the successful build-time uv sync --locked invocation). Record the real argv;
  do not manufacture a receipt after a failed/non-locked installation.
* Any required aliases/extras beyond the explicit venv Python entry point need
  reviewed --accept-alias PATH / --extra ORIGIN:r|rx:PATH arguments. No retries
  that automatically exclude files or include entire distributions are made.

Outputs are exclusively new files under /tmp/cca-eob-evidence. A generated
manifest is NOT root protected and is NEVER installed at an active policy path.
The authentic check() API tests content/access only, not protected_manifest().
The resulting evidence is not a production deployment or an apply-path proof.
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
import tomllib
import traceback

TARGET = "5461e7a5c07cf8893c2db78953316df78c9243a8"
APP = Path("/app")
EVIDENCE = Path("/tmp/cca-eob-evidence")
TOOLS = APP / "deploy/scripts"
PYTHON = APP / ".venv/bin/python"
FLAGS = ["-I", "-S", "-B", "-X", "pycache_prefix=/dev/null"]
PROBES = ["/app/backend/.env", "/private/probe"]
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C.UTF-8",
       "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory",
       "GIT_CONFIG_VALUE_0": "/app", "GIT_OPTIONAL_LOCKS": "0"}
STARTUP = {"contract": "cca_eob_source_only_v1", "isolated": 1, "no_site": 1,
           "dont_write_bytecode": True, "pycache_prefix": "/dev/null",
           "site_initialized": True, "implementation": "cpython", "version": [3, 13]}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON key: " + key)
        result[key] = value
    return result


def load(path):
    return json.loads(path.read_bytes(), object_pairs_hook=unique)


def save(name, data):
    if not isinstance(data, bytes):
        data = (json.dumps(data, sort_keys=True, indent=2) + "\n").encode()
    with (EVIDENCE / name).open("xb") as stream:
        stream.write(data)


def command(label, argv, *, source=None, env=None):
    """Preserve complete stdout/stderr even on nonzero exit; never invoke a shell."""
    save(label + ".command.json", {"argv": argv, "clean_environment": ENV if env is None else env})
    try:
        result = subprocess.run(argv, input=source, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, cwd="/", env=ENV if env is None else env,
                                timeout=180, check=False)
    except subprocess.TimeoutExpired as error:
        save(label + ".stdout", error.stdout or b"")
        save(label + ".stderr", error.stderr or b"")
        raise RuntimeError(label + " timed out after 180 seconds") from error
    save(label + ".stdout", result.stdout)
    save(label + ".stderr", result.stderr)
    save(label + ".exit.json", {"returncode": result.returncode})
    require(result.returncode == 0, label + " failed; see preserved stdout/stderr")
    return result.stdout


def git(*args):
    result = subprocess.run(["git", "--no-pager", "-C", str(APP), *args],
                            env=ENV, capture_output=True, timeout=60, check=True)
    return result.stdout


def controlled(path, *, regular=False):
    """Verify real root-owned objects/ancestors and effective runtime non-writability."""
    for candidate in (path, *path.parents):
        info = candidate.lstat()
        require(not stat.S_ISLNK(info.st_mode), "Unexpected alias: " + str(candidate))
        require(info.st_uid == 0 and not info.st_mode & 0o022,
                "Not root controlled: " + str(candidate))
        require(not os.access(candidate, os.W_OK), "Runtime-writable: " + str(candidate))
    if regular:
        require(stat.S_ISREG(path.lstat().st_mode), "Not regular: " + str(path))


def preflight(summary):
    import pwd

    require(sys.implementation.name == "cpython" and sys.version_info[:2] == (3, 13),
            "QA driver requires CPython 3.13")
    require(os.getuid() == os.geteuid() == 10001 and os.getgid() == os.getegid() == 10001,
            "Require real/effective uid/gid 10001")
    require(set(os.getgroups()) <= {10001}, "Unexpected supplementary groups")
    require(pwd.getpwuid(10001).pw_name == "polypbase_cca_eob", "Wrong runtime account")
    require(any(line.split() == ["NoNewPrivs:", "1"]
                for line in Path("/proc/self/status").read_text().splitlines()),
            "Require NoNewPrivs=1")
    for path in (Path("/"), APP, APP / ".venv", Path("/private")):
        require(os.statvfs(path).f_flag & os.ST_RDONLY, "Require read-only image: " + str(path))
        controlled(path)
    require(not os.statvfs(EVIDENCE).f_flag & os.ST_RDONLY, "Evidence volume is not writable")
    require(stat.S_IMODE(Path("/private").stat().st_mode) == 0o755, "Require /private mode0755")
    for name in PROBES:
        path = Path(name)
        controlled(path, regular=True)
        require(stat.S_IMODE(path.stat().st_mode) == 0o600, "Probe must be mode0600: " + name)
        require(not os.access(path, os.R_OK), "Probe is readable: " + name)
        try:
            descriptor = os.open(path, os.O_RDONLY)
        except PermissionError:
            pass
        else:
            os.close(descriptor)
            raise RuntimeError("Actual probe open was not denied: " + name)
    require(git("rev-parse", "HEAD").decode().strip() == TARGET, "HEAD differs from TARGET")
    require(not git("status", "--porcelain", "--untracked-files=no"), "Tracked tree is dirty")
    require((APP / ".git").is_dir() and not (APP / ".git").is_symlink(),
            "Require image-local .git repository")
    tree = git("ls-tree", "-rz", "--full-tree", TARGET)
    save("target-tree.z", tree)
    expected = {}
    tracked_names = set()
    for record in tree.split(b"\0"):
        if not record:
            continue
        header, raw_name = record.split(b"\t", 1)
        mode, kind, oid = header.decode().split()
        relative = raw_name.decode("utf-8")
        tracked_names.add(relative)
        require(kind == "blob" and mode in ("100644", "100755"),
                "Unsupported TARGET tree entry: " + relative)
        path = APP / relative
        controlled(path, regular=True)
        content = path.read_bytes()
        # Git object identity authenticates bytes independently of the builder.
        require(hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest() == oid,
                "File differs from TARGET blob: " + relative)
        require(bool(path.stat().st_mode & 0o111) == (mode == "100755"),
                "Executable mode differs from TARGET: " + relative)
        if relative.startswith("backend/") and relative.endswith(".py") and not any(
                part.startswith("test") for part in PurePosixPath(relative).parts):
            expected[str(path)] = {"path": str(path), "sha256": digest(content),
                                   "access": "r", "origin": "tracked_code"}
    require(str(APP / "backend/cca_eob_runtime.py") in expected, "Bootstrap missing from TARGET")
    save("independent-tracked-sources.json", expected)
    # No untracked application sources or fabricated virtualenv hooks are allowed.
    for path in (APP / "backend").rglob("*.py"):
        relative = path.relative_to(APP).as_posix()
        require(relative in tracked_names,
                "Untracked backend Python source: " + relative)
    for path in (APP / ".venv").rglob("*"):
        if path.is_symlink():
            controlled(path.resolve(), regular=path.is_file())
            require(path.lstat().st_uid == 0 and not os.access(path.parent, os.W_OK),
                    "Runtime-controlled venv alias: " + str(path))
        else:
            controlled(path)
    uv = command("uv-version", ["uv", "--version"]).decode().strip()
    require(uv.split()[:2] == ["uv", "0.13.6"], "Require uv 0.13.6")
    receipt_path = Path("/qa/install-receipt.json")
    controlled(receipt_path, regular=True)
    receipt = load(receipt_path)
    require(receipt["target"] == TARGET and receipt["uv_version"] == "0.13.6"
            and receipt["uv_lock_sha256"] == digest((APP / "uv.lock").read_bytes())
            and receipt["fresh_venv"] is True, "Build receipt identity/lock/freshness mismatch")
    argv = receipt["command"]
    require(isinstance(argv, list) and all(isinstance(value, str) for value in argv)
            and len(argv) >= 3 and Path(argv[0]).name == "uv" and argv[1] == "sync"
            and "--locked" in argv and not any(value in argv for value in
                ("--inexact", "--no-sync", "--dry-run")), "Require actual locked uv sync receipt")
    save("installation-receipt.json", receipt)
    command("uv-pip-check", ["uv", "--no-cache", "pip", "check", "--python", str(PYTHON), "--offline"])
    installed_bytes = command("uv-installed", ["uv", "--no-cache", "pip", "list", "--python",
                                               str(PYTHON), "--format", "json", "--offline"])
    installed = json.loads(installed_bytes, object_pairs_hook=unique)
    lock = tomllib.loads((APP / "uv.lock").read_text())
    canonical = lambda name: re.sub(r"[-_.]+", "-", name).lower()
    locked = {(canonical(package["name"]), package["version"]) for package in lock["package"]}
    actual_packages = {(canonical(package["name"]), package["version"]) for package in installed}
    require(len(actual_packages) == len(installed) and actual_packages <= locked,
            "Installed distributions include duplicate or non-locked names/versions")
    # Check the complete selected runtime resolution without installing anything.
    # --check is read-only; --offline and the read-only image forbid preparation.
    command('uv-locked-environment-check', [
        'uv', '--no-cache', 'sync', '--locked', '--check', '--offline',
        '--no-install-project', '--no-default-groups', '--project', '/app',
        '--python', str(PYTHON),
    ])
    # Check direct production requirements too; pip check alone accepts an empty venv.
    project = tomllib.loads((APP / "pyproject.toml").read_text())
    for requirement in project["project"]["dependencies"]:
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?(?:==|>=)([0-9.]+)", requirement)
        require(match is not None, "Unreviewed project requirement syntax: " + requirement)
        require(any(name == canonical(match[1]) for name, version in actual_packages),
                "Missing project dependency: " + match[1])
    save("installed-lock-membership.json", {"packages": sorted(actual_packages),
                                           "all_installed_versions_in_lock": True})
    summary.update(target=TARGET, uid=10001, gid=10001, no_new_privs=True, uv_version=uv,
                   tracked_runtime_sources=len(expected), build_receipt_verified=True)
    return expected


# Only sys and the built-in atexit are imported before authentic bootstrap.install().
# The collector itself is passed byte-for-byte on stdin, not patched or rewritten.
TRACE_WRAPPER = r'''
import sys
import atexit
attempts = []
def offline(event, args):
    if event.startswith('socket.') or event in ('sqlite3.connect', 'subprocess.Popen', 'os.system'):
        attempts.append(event)
        raise BaseException('STOP: QA forbids network, DNS, SQLite and subprocess activity')
sys.addaudithook(offline)
def finish():
    import json
    with open('/tmp/cca-eob-evidence/offline-audit.json', 'x') as stream:
        json.dump({'enforced_before_site': True, 'attempts': attempts}, stream, sort_keys=True)
atexit.register(finish)
path = '/app/backend/cca_eob_runtime.py'
sys.argv = [path, 'trace', '--deny-read', '/app/backend/.env', '--deny-read', '/private/probe']
exec(compile(open(path, 'rb').read(), path, 'exec'),
     {'__name__': '__main__', '__file__': path})
'''


def entries(document):
    result = {}
    for entry in document["files"]:
        require(set(entry) == {"path", "sha256", "access", "origin"}, "Invalid entry fields")
        require(entry["path"] not in result, "Duplicate inventory/manifest path")
        result[entry["path"]] = entry
    return result


def prove(args, summary):
    expected = preflight(summary)
    collector = TOOLS / "cca_eob_runtime_inventory.py"
    policy = TOOLS / "cca_eob_runtime_permissions.py"
    source = collector.read_bytes()
    save("authentic-tools.json", {str(path): digest(path.read_bytes()) for path in
                                  (collector, policy, APP / "backend/cca_eob_runtime.py")})
    output = command("trace", [str(PYTHON), *FLAGS, "-c", TRACE_WRAPPER], source=source, env={})
    trace = json.loads(output, object_pairs_hook=unique)
    save("trace-v2.json", trace)
    audit = load(EVIDENCE / "offline-audit.json")
    require(audit == {"enforced_before_site": True, "attempts": []}, "Forbidden activity attempted")
    require(trace["schema"] == "cca_eob_runtime_trace_v2" and trace["runtime"] == "polypbase_cca_eob"
            and trace["uid"] == 10001 and trace["app"] == "/app", "Trace identity mismatch")
    require(trace["startup"] == STARTUP and trace["no_new_privs"] is True,
            "Source-only startup contract mismatch")
    require(trace["deny_read"] == dict.fromkeys(PROBES, True), "Denial probes differ")
    require(not trace["readable_bytecode"] and not trace["executed"], "Bytecode or subprocess observed")
    require(trace["python"]["prefix"] == "/app/.venv"
            and trace["python"]["executable"] == str(PYTHON), "Unexpected venv interpreter")
    require(set(trace["directory_access"]) == set(trace["listed_directories"])
            and all(state in ("listable", "absent") for state in trace["directory_access"].values()),
            "Required runtime directory was not listable")
    hooks = []
    for site in trace["python"]["site_packages"]:
        for name in ("_virtualenv.py", "_virtualenv.pth"):
            path = Path(site) / name
            hooks.append({"path": str(path), "exists": os.path.lexists(path)})
    # This proof is deliberately for a fresh hook-free uv 0.13.6 installation.
    require(not any(item["exists"] for item in hooks),
            "Unexpected virtualenv hook; needs separate provenance review, never fabricate/delete it")
    save("uv-startup-hooks.json", {"actual_pair": hooks, "fabricated": False,
                                  "conclusion": "Real uv installation has no legacy hook pair"})
    aliases = [str(PYTHON), *args.accept_alias]
    require(len(aliases) == len(set(aliases)), "Duplicate alias review")
    review = "QA_ONLY_NONPRODUCTION source-only startup closure target=" + TARGET
    collect = [sys.executable, *FLAGS, str(collector), "collect", "--app", "/app",
               "--trace", str(EVIDENCE / "trace-v2.json"), "--output", str(EVIDENCE / "inventory.json"),
               "--report", str(EVIDENCE / "report.txt"), "--scope-review", review]
    for alias in aliases:
        collect.extend(["--accept-alias", alias])
    for extra in args.extra:
        collect.extend(["--extra", extra])
    save("explicit-scope-decisions.json", {"accept_alias": aliases, "extra": args.extra,
                                          "exclude": [], "include_distribution": []})
    command("collect", collect)
    manifest_bytes = command("manifest-build", [sys.executable, *FLAGS, str(policy), "build",
                              "--root", "/app", "--app", "/app", "--inventory",
                              str(EVIDENCE / "inventory.json")])
    manifest = json.loads(manifest_bytes, object_pairs_hook=unique)
    save("qa-only-manifest.json", manifest)
    inventory = load(EVIDENCE / "inventory.json")
    require(set(inventory) == {"files", "deny_read", "scope_review"}, "Inventory fields differ")
    require(set(manifest) == {"schema", "runtime", "app", "files", "deny_read", "scope_review"}
            and manifest["schema"] == "cca_eob_runtime_permissions_v1"
            and manifest["runtime"] == "polypbase_cca_eob" and manifest["app"] == "/app",
            "Manifest identity differs")
    actual, dependencies = entries(manifest), entries(inventory)
    config = '/app/.venv/pyvenv.cfg'
    require(config in trace['opened'], 'Real site initialization did not observe pyvenv.cfg')
    require(dependencies.get(config) == {
        'path': config, 'origin': 'interpreter', 'access': 'r',
        'sha256': digest(Path(config).read_bytes()),
    }, 'Corrected collector must retain the observed venv config as interpreter startup')
    require({p: e for p, e in actual.items() if e["origin"] == "tracked_code"} == expected,
            "Independent TARGET source/hash comparison failed")
    require({p: e for p, e in actual.items() if e["origin"] != "tracked_code"} == dependencies,
            "Dependency closure changed between inventory and manifest")
    trace_hash = digest(json.dumps(trace, sort_keys=True).encode())
    require(inventory["deny_read"] == manifest["deny_read"] == sorted(PROBES)
            and inventory["scope_review"] == manifest["scope_review"]
            and "trace_sha256=" + trace_hash in inventory["scope_review"]
            and review in inventory["scope_review"], "Scope/probe/trace identity drift")
    for path, entry in actual.items():
        controlled(Path(path), regular=True)
        require(digest(Path(path).read_bytes()) == entry["sha256"], "Independent file hash mismatch: " + path)
        require(os.access(path, os.R_OK) and not os.access(path, os.W_OK), "File access mismatch: " + path)
    # Invoke the authentic read-only content/ACL API, explicitly NOT the protected
    # production-manifest CLI gate: /tmp evidence is authored by the QA runtime.
    check_source = """
import importlib.util
import json
from pathlib import Path
spec = importlib.util.spec_from_file_location('qa_policy', '/app/deploy/scripts/cca_eob_runtime_permissions.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
policy.check(Path('/app'), json.loads(Path('/tmp/cca-eob-evidence/qa-only-manifest.json').read_bytes()))
print('QA_ONLY_AUTHENTIC_CONTENT_ACL_CHECK_OK')
"""
    command("qa-content-acl-check", [sys.executable, *FLAGS, "-c", check_source])
    report = (EVIDENCE / "report.txt").read_text()
    require("REVIEW: dependency files without a RECORD hash: 0" in report,
            "Dependency file lacks RECORD provenance; separate review required")
    require("INFO: excluded by the reviewer: 0" in report, "Unexpected exclusion")
    summary.update(status="passed", independent_source_hash_comparison="passed",
                   authentic_content_acl_check="passed", production_protected_manifest_gate="not run",
                   offline_audit=audit, trace_sha256=trace_hash, manifest_files=len(actual),
                   scope="QA-only observed source startup closure, not lazy apply/restore closure",
                   legacy_uv_hooks="actual absent pair; not synthesized")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accept-alias", action="append", default=[])
    parser.add_argument("--extra", action="append", default=[])
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Linux container only; no Linux execution can be inferred on this host")
    os.umask(0o077)
    EVIDENCE.mkdir(mode=0o700, exist_ok=True)
    require(not EVIDENCE.is_symlink() and EVIDENCE.stat().st_uid == os.getuid()
            and stat.S_IMODE(EVIDENCE.stat().st_mode) == 0o700 and not any(EVIDENCE.iterdir()),
            "Evidence directory must be real, empty, runtime-owned mode0700")
    summary = {"status": "failed", "qa_only": True, "production": False,
               "active_manifest_installed": False,
               "limitations": ["Build receipt attests install command/freshness; not an independent build replay",
                               "Network namespace isolation must also be enforced by the runner",
                               "No plan/apply, DB, pg_restore, production ACL install or deployment exercised",
                               "QA-authored manifest is not a root-protected active policy"]}
    exit_code = 1
    try:
        prove(args, summary)
        exit_code = 0
    except Exception as error:
        summary["error"] = str(error)
        save("failure.txt", traceback.format_exc().encode())
    finally:
        summary["artifact_sha256"] = {path.name: digest(path.read_bytes())
                                       for path in sorted(EVIDENCE.iterdir()) if path.is_file()}
        save("summary.json", summary)
    print(json.dumps({"status": summary["status"], "qa_only": True, "production": False,
                      "evidence": str(EVIDENCE)}, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
