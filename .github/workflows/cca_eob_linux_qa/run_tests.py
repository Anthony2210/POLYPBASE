#!/usr/bin/env python3
"""
Polypbase CCA-EOB TARGET cb07a2c Linux CPython 3.13 Integration QA Runner.

Runs inside an isolated Docker container with --network none.
Validates environment invariants, network isolation, uv virtual environment,
mandatory CCA-EOB runtime tests, and deployment preflight guards.
"""

import base64
import importlib
import io
import json
import os
import platform
import shutil

import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path

# QA tooling is outside the authentic TARGET application tree.
APP_DIR = Path('/app')

ROOT = APP_DIR


def print_banner(title: str) -> None:
    print("\n" + "=" * 78)
    print(f" {title}")
    print("=" * 78, flush=True)


def validate_venv_layout(venv: Path) -> None:
    config = venv / "pyvenv.cfg"
    values = dict(line.split("=", 1) for line in config.read_text().splitlines() if "=" in line)
    values = {key.strip().lower(): value.strip() for key, value in values.items()}
    if (
        not values.get("home")
        or not Path(values["home"]).is_dir()
        or values.get("include-system-site-packages", "").lower() != "false"
        or not values.get("version_info", values.get("version", "")).startswith("3.13.")
    ):
        raise RuntimeError("STOP: Invalid Python 3.13 pyvenv.cfg")
    site_packages = venv / "lib/python3.13/site-packages"
    if not site_packages.is_dir():
        raise RuntimeError(f"STOP: Expected site-packages directory missing: {site_packages}")
    hook = site_packages / "_virtualenv.py"
    pth = site_packages / "_virtualenv.pth"
    if os.path.lexists(hook) or os.path.lexists(pth):
        if not (hook.is_file() and pth.is_file()):
            raise RuntimeError("STOP: Incomplete legacy virtualenv startup-hook pair")
        if pth.read_text().strip() != "import _virtualenv":
            raise RuntimeError("STOP: Unexpected legacy virtualenv .pth content")
        compile(hook.read_text(), str(hook), "exec")
        module = importlib.import_module("_virtualenv")
        if Path(module.__file__).resolve() != hook.resolve():
            raise RuntimeError("STOP: Legacy virtualenv hook loaded from unexpected location")
        if not any(type(finder).__module__ == "_virtualenv" for finder in sys.meta_path):
            raise RuntimeError("STOP: Legacy virtualenv patch finder is not active")
        print("Legacy virtualenv hook pair present; effective patch is checked by ProjectVenvTests.")
    else:
        if "_virtualenv" in sys.modules:
            raise RuntimeError("STOP: Unexpected virtualenv startup hook loaded")
        print("Hook-free virtualenv (documented uv >= 0.13 behavior on Python >= 3.10).")
    unexpected = {path.name for path in site_packages.glob("*.pth")} - {"_virtualenv.pth"}
    if unexpected:
        raise RuntimeError(f"STOP: Unreviewed startup .pth files: {sorted(unexpected)}")


def audit_environment() -> None:
    print_banner("1. ENVIRONMENT & PROVENANCE DIAGNOSTICS")
    print(f"Host OS / Kernel:     {platform.system()} {platform.release()} ({platform.platform()})")
    print(f"Architecture:         {platform.machine()}")
    print(f"CPython Impl:         {sys.implementation.name}")
    print(f"CPython Version:      {platform.python_version()} ({sys.version})")
    print(f"sys.executable:       {sys.executable}")
    print(f"sys.prefix:           {sys.prefix}")
    print(f"sys.base_prefix:      {sys.base_prefix}")
    print(f"Effective UID / GID:  {os.geteuid() if hasattr(os, 'geteuid') else 'N/A'} / "
          f"{os.getegid() if hasattr(os, 'getegid') else 'N/A'}")
    print(f"Repository Root:      {APP_DIR}")
    print(f"PYTHON_DOTENV_DISABLED: {os.environ.get('PYTHON_DOTENV_DISABLED')}")
    print(f"POSTGRES_DB:          {os.environ.get('POSTGRES_DB')}")
    print(f"DJANGO_SETTINGS_MODULE: {os.environ.get('DJANGO_SETTINGS_MODULE')}")

    if (
        sys.platform != "linux"
        or sys.implementation.name != "cpython"
        or sys.version_info[:2] != (3, 13)
    ):
        raise RuntimeError(
            "STOP: Linux CPython 3.13 is mandatory"
        )

    if APP_DIR != Path("/app") or Path(sys.executable) != Path("/app/.venv/bin/python"):
        raise RuntimeError("STOP: Expected /app/.venv/bin/python interpreter")
    if Path(sys.prefix) != APP_DIR / ".venv":
        raise RuntimeError(
            "STOP: expected uv virtual environment is not active"
        )

    if sys.prefix == sys.base_prefix:
        raise RuntimeError(
            "STOP: Python is not running inside a virtual environment"
        )
    # Log and verify uv version
    try:
        proc = subprocess.run(["uv", "--version"], capture_output=True, text=True, check=True, timeout=10)
        uv_version = proc.stdout.strip()
        print(f"uv Version:           {uv_version}")
        if uv_version.split()[:2] != ["uv", "0.13.6"]:
            raise RuntimeError(f"STOP: Expected uv 0.13.6, received {uv_version!r}")
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"STOP: uv version check failed (exit={exc.returncode}): {exc.stderr.strip()}")
    except FileNotFoundError:
        raise RuntimeError("STOP: uv executable not found in PATH")
    except Exception as exc:
        raise RuntimeError(f"STOP: uv version check failed: {exc}")

    # Verify Linux null device requirements
    if sys.platform == "linux":
        null = os.lstat("/dev/null")
        is_chr = stat.S_ISCHR(null.st_mode)
        major = os.major(null.st_rdev)
        minor = os.minor(null.st_rdev)
        print(f"/dev/null:            uid={null.st_uid}, is_chr={is_chr}, major={major}, minor={minor}")
        if null.st_uid != 0 or not is_chr or major != 1 or minor != 3:
            raise RuntimeError(f"STOP: /dev/null is not the root-owned Linux null device (uid={null.st_uid}, major={major}, minor={minor})")
        print("PASS: Linux /dev/null character device verified (root-owned, major=1, minor=3).")
    else:
        print(f"INFO: Local platform is {sys.platform} (Linux null device check deferred to container).")

    # Verify real uv-created virtual environment structure
    venv_python = APP_DIR / (".venv/bin/python" if os.name != "nt" else ".venv/Scripts/python.exe")
    if not venv_python.is_file():
        raise RuntimeError(f"STOP: Virtualenv python missing at {venv_python}")
    print(f"Virtualenv Python:    {venv_python} (exists=True)")

    validate_venv_layout(APP_DIR / ".venv")
    for name in ("django", "dotenv", "psycopg"):
        module = importlib.import_module(name)
        if not Path(module.__file__).resolve().is_relative_to(APP_DIR / ".venv"):
            raise RuntimeError(f"STOP: Dependency outside expected venv: {name}")
    for executable in ("bash", "grep", "sha256sum"):
        if shutil.which(executable) is None:
            raise RuntimeError(f"STOP: Required executable missing: {executable}")

    print_banner("2. EFFECTIVE ISOLATION VERIFICATION")
    interfaces = {line.split(':', 1)[0].strip() for line in
                  Path('/proc/net/dev').read_text().splitlines()[2:]}
    if interfaces != {'lo'}:
        raise RuntimeError(f'STOP: Unexpected network interfaces: {interfaces}')
    if os.getuid() != 10001 or os.geteuid() != 10001:
        raise RuntimeError('STOP: Dedicated non-root identity required')
    if not any(line.split() == ['NoNewPrivs:', '1'] for line in
               Path('/proc/self/status').read_text().splitlines()):
        raise RuntimeError('STOP: NoNewPrivs is not enabled')
    if not os.statvfs('/app').f_flag & os.ST_RDONLY:
        raise RuntimeError('STOP: Read-only application filesystem required')
    print('PASS: Loopback only, uid10001, NoNewPrivs=1, read-only /app.')


def run_suite_in_subprocess(
    title: str,
    start_dir: str,
    pattern: str,
    *,
    expected_count: int | None = None,
    disallow_skips: bool = False,
    timeout: int = 120,
    test_class: str | None = None,
) -> bool:
    print_banner(f"RUNNING TEST SUITE: {title}")
    print(f"Working Directory:    {APP_DIR}")
    print(f"Start Directory:      {start_dir}")
    print(f"Pattern:              {pattern}")
    print(f"Disallow Skips:       {disallow_skips}")
    if expected_count is not None:
        print(f"Expected Test Count:  {expected_count}")

    runner_code = f"""
import sys, time, unittest
from pathlib import Path

start_dir = {repr(str(start_dir))}
pattern = {repr(pattern)}
expected_count = {repr(expected_count)}
disallow_skips = {repr(disallow_skips)}
test_class = {repr(test_class)}

loader = unittest.defaultTestLoader
if test_class:
    # Load specific test class from the module
    # Convert start_dir to module path (e.g., 'deploy/tests' -> 'deploy.tests')
    module_path = '.'.join(Path(start_dir).parts)
    module_name = module_path + '.' + pattern[:-3]
    module = __import__(module_name, fromlist=[test_class])
    test_class_obj = getattr(module, test_class)
    suite = loader.loadTestsFromTestCase(test_class_obj)
else:
    suite = loader.discover(start_dir=start_dir, pattern=pattern)
count = suite.countTestCases()
print(f"Discovered test cases: {{count}}", flush=True)

if count <= 0 or (expected_count is not None and count != expected_count):
    print(f"ERROR: Expected {{expected_count}} tests, but discovered {{count}}", file=sys.stderr)
    sys.exit(3)

start_time = time.time()
runner = unittest.TextTestRunner(verbosity=2)
result = runner.run(suite)
duration = time.time() - start_time

print("-" * 78)
print(f"RESULT SUMMARY: {{count}} tests | run={{result.testsRun}} failures={{len(result.failures)}} errors={{len(result.errors)}} skipped={{len(result.skipped)}} duration={{duration:.2f}}s")

if result.testsRun <= 0 or result.testsRun != count or not result.wasSuccessful():
    print("STATUS: FAILED (failures or errors encountered)", file=sys.stderr)
    sys.exit(1)

if disallow_skips and len(result.skipped) > 0:
    print(f"STATUS: FAILED ({{len(result.skipped)}} tests skipped; ZERO skips allowed!)", file=sys.stderr)
    for test, reason in result.skipped:
        print(f"  SKIPPED: {{test}} -> {{reason}}", file=sys.stderr)
    sys.exit(2)

print("STATUS: PASSED", flush=True)
sys.exit(0)
"""

    compile(runner_code, "<qa-suite-runner>", "exec")
    python_bin = sys.executable
    cmd = [python_bin, "-B", "-c", runner_code]
    print(f"Executing: {' '.join(cmd[:3])} '<runner_code>'", flush=True)

    start = time.time()
    proc = subprocess.run(cmd, cwd=str(APP_DIR), timeout=timeout)
    elapsed = time.time() - start

    if proc.returncode == 0:
        print(f"PASSED: {title} in {elapsed:.2f}s (exit=0)\n")
        return True
    else:
        print(f"FAILED: {title} in {elapsed:.2f}s (exit={proc.returncode})\n", file=sys.stderr)
        return False


def main() -> int:
    overall_start = time.time()
    try:
        audit_environment()
    except Exception as exc:
        print(f"\nAUDIT FAILURE: {exc}", file=sys.stderr)
        return 1

    disallow_skips_runtime = True

    suites = [
        (
            "CCA-EOB Runtime Tests (Startup, Inventory, Permissions)",
            "deploy/scripts",
            "test_cca_eob_runtime_*.py",
            59,
            disallow_skips_runtime,
        ),
        (
            "Deployment Preflight Settings Isolation Tests",
            "deploy/tests",
            "test_preflight_settings.py",
            6,
            True,
        ),
    ]

    # Under Linux, also run MigrationGuardTests from test_deploy_release.py
    # (tests deploy_release.sh bash migration guards; excludes PowerShellTests which require pwsh)
    if sys.platform == "linux":
        suites.append((
            "Deployment Release Guard Tests (Bash Migration Guards)",
            "deploy/tests",
            "test_deploy_release.py",
            14,
            True,
            "MigrationGuardTests",
        ))

    results = []
    for suite_config in suites:
        title = suite_config[0]
        sdir = suite_config[1]
        pat = suite_config[2]
        exp_cnt = suite_config[3]
        no_skip = suite_config[4]
        test_class = suite_config[5] if len(suite_config) > 5 else None
        success = run_suite_in_subprocess(
            title,
            sdir,
            pat,
            expected_count=exp_cnt,
            disallow_skips=no_skip,
            timeout=180,
            test_class=test_class,
        )
        results.append((title, success))
        if not success:
            print(f"STOPPING ON FAILURE IN SUITE: {title}", file=sys.stderr)
            break

    core_passed = all(succ for _, succ in results) and len(results) == len(suites)
    Path('/tmp/core-tests.json').write_text(json.dumps({
        'expected_counts': [59, 6, 14], 'zero_skips_required': True,
        'status': 'passed' if core_passed else 'failed', 'suites': results,
    }, sort_keys=True, indent=2) + '\n')
    # Unit tests alone never establish installation acceptance.
    proof = subprocess.run([
        sys.executable, '-I', '-S', '-B', '-X', 'pycache_prefix=/dev/null',
        '/qa/prove_installation.py',
        '--extra', 'interpreter:r:/usr/lib/locale/C.utf8/LC_CTYPE',
        '--extra', 'interpreter:r:/usr/lib/x86_64-linux-gnu/gconv/gconv-modules.cache',
        '--extra', 'interpreter:r:/usr/share/zoneinfo/Europe/Paris',
    ], cwd='/', env={}, timeout=600, check=False)
    results.append(('Full isolated installation evidence', proof.returncode == 0))
    total_duration = time.time() - overall_start
    print_banner("FINAL EXECUTION SUMMARY")
    all_passed = core_passed and proof.returncode == 0
    for title, succ in results:
        status_str = "PASSED" if succ else "FAILED"
        print(f"  [{status_str}] {title}")

    print(f"\nTotal Validation Duration: {total_duration:.2f}s")
    if all_passed:
        print("\nCORE TESTS AND QA-ONLY INSTALLATION PROOF PASSED. INDEPENDENT REVIEW REQUIRED.")
        print("Exit Status: 0")
        return 0
    else:
        print("\nONE OR MORE SUITES FAILED OR WERE SKIPPED UNEXPECTEDLY.", file=sys.stderr)
        print("Exit Status: 1", file=sys.stderr)
        return 1


def export_evidence() -> None:
    # /tmp is a disposable tmpfs: stream evidence before the container exits.
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        sources = [(Path('/tmp/core-tests.json'), 'core-tests.json'),
                   (Path('/qa/source-integrity.json'), 'source-integrity.json'),
                   (Path('/qa/install-receipt.json'), 'install-receipt.json')]
        directory = Path('/tmp/cca-eob-evidence')
        if directory.is_dir() and not directory.is_symlink():
            sources.extend((path, 'installation/' + path.name) for path in sorted(directory.iterdir()))
        for path, name in sources:
            if path.is_file() and not path.is_symlink():
                archive.add(path, arcname=name, recursive=False)
    print('CCA_EOB_QA_EVIDENCE_V1:' + base64.b64encode(buffer.getvalue()).decode(), flush=True)


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        export_evidence()
