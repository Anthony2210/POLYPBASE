"""Run bounded PCA QA on an independently provisioned disposable PostgreSQL 18.

Uses the historical-temperature fail-closed backend, restricted owner/runtime
logins and exclusive lease. Never creates, starts, stops or deletes a cluster.
The runtime environment and both opt-ins are mandatory; passwords are prompted.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LABELS = (
    "apps.measurements.test_canonical_evidence",
    "apps.exports.test_canonical_environment",
    "apps.cultures.test_pca_identity_unification",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Authorize only the configured disposable QA target")
    parser.add_argument("labels", nargs="*", help="Django labels; apps. prefix optional")
    arguments = parser.parse_args()
    if not arguments.run:
        parser.error("--run is required; provision and authorize the disposable target separately")
    labels = arguments.labels or list(DEFAULT_LABELS)
    for index, label in enumerate(labels):
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", label):
            parser.error("Invalid module/class/method label")
        if label.startswith(("measurements.", "exports.", "cultures.")):
            labels[index] = f"apps.{label}"
    # Keep the source runner's bounded execution, but never own cluster lifecycle.
    # The child requires an interactive terminal; no password is forwarded here.
    try:
        result = subprocess.run([
            sys.executable, "-B", str(ROOT / "scripts" / "validate_historical_temperature_postgres.py"),
            "--run", "--tests-only", "--labels", *labels,
        ], cwd=ROOT, check=False, timeout=900)
        return result.returncode
    except subprocess.TimeoutExpired:
        print("STOP: PCA QA exceeded 900 seconds; inspect the disposable target before reuse", file=sys.stderr)
        return 124
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
