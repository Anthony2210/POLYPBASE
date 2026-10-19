"""Explicit partial acceptance, independent of Django and database access."""

import hashlib
import json
from collections import Counter
from copy import deepcopy

from .historical_temperature_dry_run import build_report
from .historical_temperature_source import fingerprint

APPROVED_SNAPSHOT_SHA256 = "c23ca0375aadaff9db3f6789684609abe1d518fda1f9dfab475e90857787de99"
APPROVED_PLAN_FINGERPRINT = "34a7333f6b97f70936827d4b73bd35e7d5de607c4bd0e005f72a980a4d3712bf"
POLICY = "anthony-create-reading-only-v1"


class PartialImportError(ValueError):
    pass


def snapshot_baseline(snapshot):
    """Project the reviewed richer snapshot to the existing planner contract."""
    return {
        "organization_id": snapshot["identity"]["organization_id"],
        "boxes": [{k: row[k] for k in (
                    "id", "organization_id", "global_code", "merged_into_id", "previous_global_code",
                ) if k in row} for row in snapshot["boxes"]],
        "biological_measurements": [{k: row[k] for k in (
            "id", "box_id", "week_start", "polyp_count", "ephyrae_count", "duplicate_of_id",
        ) if k in row} for row in snapshot["biological_measurements"]],
        "historical_temperatures": deepcopy(snapshot["historical_temperatures"]),
    }


def select_partial(report):
    """Never interpret global ready=false as permission to execute all proposals.

    Input must be a freshly generated report, not an untrusted uploaded plan.
    Both the full classification and all source exclusions are sealed in the result.
    """
    plan = report["association_plan"]
    if plan["baseline_status"] != "VALID" or plan["baseline_errors"]:
        raise PartialImportError("invalid_baseline")
    accepted, excluded, payloads = [], [], []
    proposals = {p["payload"]["biological_measurement_id"]: p for p in plan["proposed_changes"]}
    for row in plan["associations"]:
        if row["status"] != "CREATE_READING":
            excluded.append(deepcopy(row))
            continue
        proposal = proposals.get(row["biological_measurement_id"])
        if (row["issues"] or not row["evidences"] or proposal is None
                or proposal["operation"] != "CREATE_READING"
                or proposal["payload"]["evidences"] != row["evidences"]):
            raise PartialImportError("invalid_create_association")
        accepted.append(deepcopy(row))
        payloads.append(deepcopy(proposal["payload"]))
    if len({p["biological_measurement_id"] for p in payloads}) != len(payloads):
        raise PartialImportError("duplicate_measurement")
    body = {
        "policy": POLICY, "organization_id": report["organization_id"],
        "global_ready": report["summary"]["ready"],
        "report_fingerprint": report["report_fingerprint"],
        "baseline_fingerprint": report["baseline_fingerprint"],
        "accepted": accepted, "excluded": excluded, "payloads": payloads,
        "source_exclusions": deepcopy(report["source_extraction"]["exclusions"]),
        "header_issues": deepcopy(report["source_extraction"]["header_issues"]),
        "summary": {
            "accepted_readings": len(payloads),
            "accepted_evidences": sum(len(p["evidences"]) for p in payloads),
            "excluded_groups": len(excluded),
            "excluded_status_counts": dict(sorted(Counter(r["status"] for r in excluded).items())),
        },
    }
    body["plan_fingerprint"] = fingerprint(body)
    return body


def prepare_approved_partial(raw_directory, snapshot_bytes):
    """Regenerate the exact approved snapshot subset; no DB fallback or expansion."""
    if hashlib.sha256(snapshot_bytes).hexdigest() != APPROVED_SNAPSHOT_SHA256:
        raise PartialImportError("approved_snapshot_changed")
    snapshot = json.loads(snapshot_bytes)
    baseline = snapshot_baseline(snapshot)
    report = build_report(raw_directory, organization_id=baseline["organization_id"], baseline=baseline)
    result = select_partial(report)
    result["snapshot_sha256"] = APPROVED_SNAPSHOT_SHA256
    # The executable fingerprint covers the approval binding as well as every exclusion.
    result["plan_fingerprint"] = fingerprint({k: v for k, v in result.items() if k != "plan_fingerprint"})
    compare_approval(result, APPROVED_PLAN_FINGERPRINT)
    return snapshot, result


def compare_approval(actual, expected_fingerprint):
    if actual["plan_fingerprint"] != expected_fingerprint:
        raise PartialImportError(
            "approved_plan_changed: " + json.dumps({
                "expected_plan_fingerprint": expected_fingerprint,
                "actual_plan_fingerprint": actual["plan_fingerprint"],
                "actual_summary": actual["summary"],
                "action": "STOP; compare accepted/excluded groups with the sealed approved private plan; no expansion is authorized",
            }, sort_keys=True)
        )


def main(argv=None):
    import argparse
    from pathlib import Path
    from .historical_temperature_source import canonical_json

    parser = argparse.ArgumentParser(description="Offline approved partial temperature plan; no application action.")
    parser.add_argument("--raw-directory", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    repository = Path(__file__).resolve().parents[3]
    output = args.output.resolve()
    if (not output.is_relative_to(repository / ".local-docs") or output.suffix != ".json"
            or output == args.snapshot.resolve() or output.is_relative_to(args.raw_directory.resolve())):
        parser.error("Output must be a separate private .local-docs JSON file.")
    try:
        _snapshot, partial = prepare_approved_partial(args.raw_directory, args.snapshot.read_bytes())
        output.write_text(canonical_json(partial) + "\n", encoding="utf-8", newline="\n")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Partial plan refused: {exc}\n")
    print(canonical_json({"summary": partial["summary"], "global_ready": partial["global_ready"],
                          "plan_fingerprint": partial["plan_fingerprint"]}))


if __name__ == "__main__":
    main()
