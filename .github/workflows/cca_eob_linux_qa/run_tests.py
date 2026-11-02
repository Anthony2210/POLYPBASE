#!/usr/bin/env python3
"""
Polypbase CCA-EOB Linux CPython 3.13 Integration QA Runner.

Runs inside an isolated Docker container with --network none.
Validates environment invariants, network isolation, uv virtual environment,
mandatory CCA-EOB runtime tests, and deployment preflight guards.
"""

import os
import platform
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

# Resolve repository/app root whether located at /app/run_tests.py or .github/workflows/cca_eob_linux_qa/run_tests.py
_CURRENT_DIR = Path(__file__).resolve().parent
if (_CURRENT_DIR / "backend").is_dir():
    APP_DIR = _CURRENT_DIR
else:
    APP_DIR = Path(__file__).resolve().parents[3]

ROOT = APP_DIR


def print_banner(title: str) -> None:
    print("\n" + "=" * 78)
    print(f" {title}")
    print("=" * 78, flush=True)


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
        proc = subprocess.run(["uv", "--version"], capture_output=True, text=True, check=True)
        uv_version = proc.stdout.strip()
        print(f"uv Version:           {uv_version}")
        if not uv_version:
            raise RuntimeError("STOP: uv version output is empty")
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

    site_packages_dirs = list((APP_DIR / ".venv/lib").glob("python3.*/site-packages")) if os.name != "nt" else [APP_DIR / ".venv/Lib/site-packages"]
    if not site_packages_dirs:
        raise RuntimeError("STOP: No Python 3.x site-packages directory found in virtualenv!")
    site_packages = site_packages_dirs[0]
    if not site_packages.is_dir():
        raise RuntimeError(f"STOP: Expected site-packages directory not found or not a directory: {site_packages}")
    print(f"site-packages:        {site_packages}")

    v_py = site_packages / "_virtualenv.py"
    v_pth = site_packages / "_virtualenv.pth"
    print(f"_virtualenv.py:       {v_py.is_file()}")
    print(f"_virtualenv.pth:      {v_pth.is_file()}")
    if not (v_py.is_file() and v_pth.is_file()):
        raise RuntimeError("STOP: uv virtualenv startup hooks (_virtualenv.py/.pth) not found in site-packages!")
    print("PASS: uv virtualenv hooks verified.")

    # Verify effective network isolation
    print_banner("2. EFFECTIVE NETWORK ISOLATION VERIFICATION")
    is_isolated = False
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.0)
        sock.connect(("1.1.1.1", 53))
        sock.close()
    except OSError as err:
        is_isolated = True
        print(f"PASS: Outbound network connection rejected as expected: {err}")
    except Exception as err:
        is_isolated = True
        print(f"PASS: Connection error: {err}")

    if not is_isolated:
        if os.environ.get("REQUIRE_NETWORK_ISOLATION") == "1":
            raise RuntimeError("STOP: Network access is available! Container is NOT isolated.")
        print("NOTICE: Network is reachable (expected during local preparation outside container).")
    else:
        print("PASS: Network isolation verified (--network none confirmed).")


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
    module_path = start_dir.replace('/', '.').replace('\\', '.')
    module_name = f"{module_path}.{pattern[:-3]}"
    module = __import__(module_name, fromlist=[test_class])
    test_class_obj = getattr(module, test_class)
    suite = loader.loadTestsFromTestCase(test_class_obj)
else:
    suite = loader.discover(start_dir=start_dir, pattern=pattern)
count = suite.countTestCases()
print(f"Discovered test cases: {{count}}", flush=True)

if expected_count is not None and count != expected_count:
    print(f"ERROR: Expected {{expected_count}} tests, but discovered {{count}}", file=sys.stderr)
    sys.exit(3)

start_time = time.time()
runner = unittest.TextTestRunner(verbosity=2)
result = runner.run(suite)
duration = time.time() - start_time

print("-" * 78)
print(f"RESULT SUMMARY: {{count}} tests | run={{result.testsRun}} failures={{len(result.failures)}} errors={{len(result.errors)}} skipped={{len(result.skipped)}} duration={{duration:.2f}}s")

if not result.wasSuccessful():
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

    disallow_skips_runtime = (sys.platform == "linux")

    suites = [
        (
            "CCA-EOB Runtime Tests (Startup, Inventory, Permissions)",
            "deploy/scripts",
            "test_cca_eob_runtime_*.py",
            44,
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
            12,
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

    total_duration = time.time() - overall_start
    print_banner("FINAL EXECUTION SUMMARY")
    all_passed = all(succ for _, succ in results) and len(results) == len(suites)
    for title, succ in results:
        status_str = "PASSED" if succ else "FAILED"
        print(f"  [{status_str}] {title}")

    print(f"\nTotal Validation Duration: {total_duration:.2f}s")
    if all_passed:
        print("\nALL INTEGRATION AND DEPLOYMENT SUITES PASSED.")
        print("Exit Status: 0")
        return 0
    else:
        print("\nONE OR MORE SUITES FAILED OR WERE SKIPPED UNEXPECTEDLY.", file=sys.stderr)
        print("Exit Status: 1", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
