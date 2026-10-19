"""Offline report CLI. Deliberately has no Django setup or database code.

Run from backend with ``python -m apps.measurements.historical_temperature_dry_run``.
Reports contain private scientific data and are restricted to ignored .local-docs.
"""

import argparse
import json
from collections import Counter
from pathlib import Path

from .historical_temperature_plan import plan
from .historical_temperature_source import (
    SOURCES,
    SourceError,
    canonical_json,
    extract_sources,
    fingerprint,
)

_OLD_SOURCE_HASH = "14befa67441f289153576b5c53de462eaa05b4218df6d5439a44ee432f62cb67"


def compare_2026_manifest(extraction, manifest):
    """Compare source coordinates, never treat a manifest as a database snapshot."""
    if (manifest.get("source", {}).get("sha256") != _OLD_SOURCE_HASH
            or manifest.get("fingerprint") != fingerprint({k: v for k, v in manifest.items() if k != "fingerprint"})):
        raise SourceError("The older 2026 manifest fingerprint or source hash is invalid.")
    old = {}
    for item in manifest["measurements"]:
        key = (item["sheet"], item["polyp_cell"], item["ephyrae_cell"])
        if key in old:
            raise SourceError("Duplicate source coordinate in the older manifest.")
        old[key] = item
    comparisons = []
    present = set()
    for candidate in extraction["candidates"]:
        if candidate["iso_year"] != 2026:
            continue
        evidence = candidate["evidence"]
        key = (evidence["sheet"], evidence["polyp_cell"], evidence["ephyrae_cell"])
        item = old.get(key)
        present.add(key)
        issues = []
        if item is None:
            issues.append("NOT_IN_OLDER_MANIFEST")
        else:
            if item["box"] != candidate["canonical_box"]:
                issues.append("IDENTITY_DIFFERS")
            if item["iso_week"] != candidate["iso_week"]:
                issues.append("WEEK_DIFFERS")
            if (item["polyps"], item["ephyrae"]) != (candidate["polyps"], candidate["ephyrae"]):
                issues.append("BIOLOGY_DIFFERS")
            if candidate["biological_state"] != "complete":
                issues.append("SOURCE_BIOLOGY_UNREVIEWED")
        comparisons.append({"candidate_id": candidate["candidate_id"], "source_box": candidate["source_box"],
                            "canonical_box": candidate["canonical_box"], "iso_week": candidate["iso_week"],
                            "sheet": key[0], "polyp_cell": key[1], "ephyrae_cell": key[2],
                            "older_manifest_box": item["box"] if item else None,
                            "older_manifest_counts": {"polyps": item["polyps"], "ephyrae": item["ephyrae"]} if item else None,
                            "status": "SOURCE_COORDINATE_AGREES" if not issues else "REVIEW_REQUIRED", "issues": issues})
    identities = []
    for box in manifest["boxes"]:
        blocks = [block for block in extraction["blocks"] if block["source_file"] == "Suivi_2026_actualisé_v2.xlsx"
                  and block["sheet"] == box["sheet"] and block["temperature_cell"] == f"C{box['first_source_row']}"]
        if len(blocks) == 1 and blocks[0]["source_box"] != box["source_identifier"]:
            identities.append({"sheet": box["sheet"], "cell": f"B{box['first_source_row']}",
                               "older_source_label": box["source_identifier"],
                               "older_manifest_box": box["code"], "v2_source_label": blocks[0]["source_box"]})
    return {
        "kind": "SOURCE_COMPARISON_ONLY_NOT_DATABASE_BASELINE",
        "older_manifest_fingerprint": manifest["fingerprint"],
        "older_workbook_read": False, "corrections_transferred": False,
        "summary": {"compared_candidates": len(comparisons),
                    "coordinate_agreements": sum(not row["issues"] for row in comparisons),
                    "review_required": sum(bool(row["issues"]) for row in comparisons),
                    "issue_counts": dict(sorted(Counter(issue for row in comparisons for issue in row["issues"]).items())),
                    "changed_identity_labels": len(identities),
                    "older_coordinates_without_candidate": len(set(old) - present)},
        "identity_changes": identities, "comparisons": comparisons,
        "older_coordinates_without_candidate": [list(key) for key in sorted(set(old) - present)],
    }


def build_report(raw_directory, *, organization_id, baseline=None, manifest=None):
    extraction = extract_sources(raw_directory)
    if manifest is None:
        manifest_path = Path(__file__).resolve().parents[1] / "cultures/historical_2026/manifest_2026.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    comparison = compare_2026_manifest(extraction, manifest)
    blockers = {row["candidate_id"]: [f"V2_{issue}_REVIEW_REQUIRED" for issue in row["issues"]
                                      if issue in {"IDENTITY_DIFFERS", "WEEK_DIFFERS", "BIOLOGY_DIFFERS"}]
                for row in comparison["comparisons"]}
    blockers = {key: issues for key, issues in blockers.items() if issues}
    association_plan = plan(extraction["candidates"], organization_id=organization_id,
                            baseline=baseline, source_blockers=blockers)
    by_week = Counter((c["iso_year"], c["iso_week"]) for c in extraction["candidates"])
    body = {
        "schema": "polypbase.historical_measured_temperature.dry_run", "schema_version": 1,
        "database_access": False, "database_writes": 0, "apply_available": False,
        "organization_id": organization_id,
        "baseline_fingerprint": fingerprint(baseline) if baseline is not None else None,
        "source_extraction": extraction, "association_plan": association_plan,
        "candidates_by_week": [{"iso_year": year, "iso_week": week, "count": count}
                               for (year, week), count in sorted(by_week.items())],
        "summary": {
            "sources_verified": len(extraction["sources"]),
            "numeric_anchors": sum(s["numeric_anchors"] for s in extraction["sources"]),
            "blocks_with_biology": sum(s["blocks_with_biology"] for s in extraction["sources"]),
            "blocks_without_biology": sum(s["blocks_without_biology"] for s in extraction["sources"]),
            "candidate_associations": len(extraction["candidates"]),
            "biological_source_states": dict(sorted(Counter(c["biological_state"] for c in extraction["candidates"]).items())),
            "exclusion_counts": dict(sorted(Counter(e["reason"] for e in extraction["exclusions"]).items())),
            "proposed_db_changes": len(association_plan["proposed_changes"]),
            "resolved_db_associations": sum(a["status"] in {"CREATE_READING", "ADD_EVIDENCE", "ALREADY_PRESENT"}
                                            for a in association_plan["associations"]),
            "ready": (association_plan["summary"]["ready"] and not extraction["header_issues"]
                      and not any(e["reason"] in {"TEMPERATURE_MALFORMED", "UNRESOLVED_WEEK_HEADER"}
                                  for e in extraction["exclusions"])),
        },
    }
    body["older_2026_source_comparison"] = comparison
    body["report_fingerprint"] = fingerprint(body)
    return body


def main(argv=None):
    parser = argparse.ArgumentParser(description="Extract the seven fingerprinted workbooks without loading Django or connecting to any database.")
    parser.add_argument("--raw-directory", required=True, type=Path)
    parser.add_argument("--organization-id", required=True, help="Explicit target identifier; not an authorization grant.")
    parser.add_argument("--baseline-json", type=Path, help="Optional separately authorized offline snapshot. Never opens a database.")

    parser.add_argument("--output", required=True, type=Path, help="Private JSON report under the repository's ignored .local-docs directory.")
    args = parser.parse_args(argv)
    repository = Path(__file__).resolve().parents[3]
    output = args.output.resolve()
    if not output.is_relative_to(repository / ".local-docs") or output.suffix.lower() != ".json":
        parser.error("Report output must be a JSON file under the repository's ignored .local-docs directory.")
    inputs = [(args.raw_directory / filename).resolve() for filename in SOURCES]
    if args.baseline_json:
        inputs.append(args.baseline_json.resolve())
    if output in inputs:
        parser.error("Report output cannot replace an input.")
    try:
        organization_id = int(args.organization_id) if args.organization_id.isdecimal() else args.organization_id
        baseline = json.loads(args.baseline_json.read_text(encoding="utf-8")) if args.baseline_json else None
        report = build_report(args.raw_directory, organization_id=organization_id, baseline=baseline)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(canonical_json(report) + "\n", encoding="utf-8", newline="\n")
    except (SourceError, OSError, ValueError) as exc:
        parser.exit(2, f"Dry-run refused: {exc}\n")
    print(json.dumps({"summary": report["summary"], "plan_summary": report["association_plan"]["summary"],
                      "report_fingerprint": report["report_fingerprint"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
