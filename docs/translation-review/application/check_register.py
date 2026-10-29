"""Check the application translation register against its history and the current sources.

Usage (from the repository root):
    python docs/translation-review/application/check_register.py
    python docs/translation-review/application/check_register.py --show KEY

Standard library only. Read-only: it never modifies a file.
Regression tests: python -m unittest discover -s docs/translation-review/application -p "test_*.py"
"""
from __future__ import annotations

import ast
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Callable, NamedTuple

ROOT = Path(__file__).resolve().parents[3]
REGISTER = Path(__file__).with_name("register.csv")
COLUMNS = [
    "entry_id", "batch", "surface", "key_or_msgid", "language", "source_file", "initial_value", "final_value",
    "sheet_value", "decision_ref", "status", "reason", "baseline_git_head", "baseline_sheet_sha256",
]
STATUSES = {"APPLIED", "ALREADY_MATCHING", "PRESERVED", "DEFERRED", "NOT_FOUND"}
SURFACES = {"react", "django", "inline"}
LANGUAGES = {"FR", "EN"}
# Fields that may legitimately be empty; initial/final values are checked per status.
OPTIONAL_FIELDS = {"sheet_value", "initial_value", "final_value"}
BATCH_ID = re.compile(r"^B(\d{2,})-(\d{4}-\d{2}-\d{2})$")
ENTRY_ID = re.compile(r"^T(\d{3,})$")
GIT_HEAD = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PLACEHOLDER = re.compile(r"\{(\w+)\}")
CATALOG_ENTRY = re.compile(r"^\s+(\w+): ('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"),?\s*$")


class SourceState(NamedTuple):
    """What the application shows today for one register identity."""

    present: bool
    value: str | None


def identity(row: dict[str, str]) -> tuple[str, str, str, str]:
    """A translation event targets one key or msgid, in one language, on one surface and file."""
    return row["surface"], row["key_or_msgid"], row["language"], row["source_file"]


def describe(row: dict[str, str]) -> str:
    return f"{row['entry_id']} [{row['batch']}] {row['surface']}:{row['key_or_msgid']} {row['language']}"


def read_catalog(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = CATALOG_ENTRY.match(line)
        if match:
            values[match.group(1)] = ast.literal_eval(match.group(2))
    return values


def read_po(path: Path) -> dict[str, str]:
    messages: dict[str, str] = {}
    section = msgid = msgstr = None
    for raw in path.read_text(encoding="utf-8").splitlines() + ["msgid \"\""]:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("msgid "):
            if msgid is not None and msgstr is not None:
                messages[msgid] = msgstr
            section, msgid, msgstr = "msgid", ast.literal_eval(line[6:]), None
        elif line.startswith("msgstr "):
            section, msgstr = "msgstr", ast.literal_eval(line[7:])
        elif line.startswith('"') and section == "msgid":
            msgid += ast.literal_eval(line)
        elif line.startswith('"') and section == "msgstr":
            msgstr += ast.literal_eval(line)
    messages.pop("", None)
    return messages


def read_source_state(row: dict[str, str]) -> SourceState:
    path = ROOT / row["source_file"]
    if not path.exists():
        return SourceState(False, None)
    key = row["key_or_msgid"]
    if row["surface"] == "react":
        catalog = read_catalog(path)
        return SourceState(key in catalog, catalog.get(key))
    if row["surface"] == "django":
        # gettext shows the msgid itself when no non-empty translation exists.
        msgstr = read_po(path).get(key)
        return SourceState(bool(msgstr), msgstr or key)
    # Inline strings can only be checked for presence as a quoted literal.
    found = bool(row["final_value"]) and f"'{row['final_value']}'" in path.read_text(encoding="utf-8")
    return SourceState(found, row["final_value"] if found else None)


def check_row(row: dict[str, str]) -> list[str]:
    errors = []
    label = describe(row)
    for name in COLUMNS:
        if name not in OPTIONAL_FIELDS and not row[name].strip():
            errors.append(f"{label}: empty {name}")
    if row["status"] not in STATUSES:
        errors.append(f"{label}: unknown status {row['status']!r}")
    if row["surface"] not in SURFACES:
        errors.append(f"{label}: unknown surface {row['surface']!r}")
    if row["language"] not in LANGUAGES:
        errors.append(f"{label}: unsupported language {row['language']!r}")
    if not GIT_HEAD.match(row["baseline_git_head"]):
        errors.append(f"{label}: baseline_git_head is not a full Git commit hash")
    if not SHA256.match(row["baseline_sheet_sha256"]):
        errors.append(f"{label}: baseline_sheet_sha256 is not a SHA-256 digest")

    initial, final = row["initial_value"], row["final_value"]
    if row["status"] == "NOT_FOUND":
        if row["surface"] == "inline":
            errors.append(f"{label}: NOT_FOUND is not supported for inline translations: the checker cannot"
                          " reliably prove that an inline string is absent from the source. Record the"
                          " inline string with another status, or wait for source-level presence detection")
        if initial or final:
            errors.append(f"{label}: NOT_FOUND must leave initial_value and final_value empty")
        return errors
    if not initial or not final:
        errors.append(f"{label}: {row['status']} requires initial_value and final_value")
    if row["status"] == "APPLIED" and initial == final:
        errors.append(f"{label}: APPLIED without a change of value")
    if row["status"] != "APPLIED" and initial != final:
        errors.append(f"{label}: {row['status']} must not change the value")
    if set(PLACEHOLDER.findall(initial)) != set(PLACEHOLDER.findall(final)):
        errors.append(f"{label}: placeholders differ between initial and final values")
    return errors


def check_chronology(rows: list[dict[str, str]]) -> list[str]:
    """Batches must appear as contiguous blocks, in increasing number and non-decreasing date order."""
    errors = []
    previous_entry = None
    blocks: list[str] = []
    for row in rows:
        entry = ENTRY_ID.match(row["entry_id"])
        if not entry:
            errors.append(f"{describe(row)}: malformed entry_id")
        else:
            number = int(entry.group(1))
            if previous_entry is not None and number <= previous_entry:
                errors.append(f"{describe(row)}: entry_id not strictly increasing in file order")
            previous_entry = number
        if not BATCH_ID.match(row["batch"]):
            errors.append(f"{describe(row)}: malformed batch id (expected Bnn-YYYY-MM-DD)")
            continue
        if not blocks or blocks[-1] != row["batch"]:
            if row["batch"] in blocks:
                errors.append(f"{describe(row)}: batch {row['batch']} is split by another batch")
            blocks.append(row["batch"])

    seen_numbers: dict[int, str] = {}
    previous = None
    for batch in dict.fromkeys(blocks):
        match = BATCH_ID.match(batch)
        number, date = int(match.group(1)), match.group(2)
        if number in seen_numbers and seen_numbers[number] != batch:
            errors.append(f"batch number {number} is used by {seen_numbers[number]} and {batch}")
        seen_numbers.setdefault(number, batch)
        if previous is not None:
            if number <= previous[0]:
                errors.append(f"batch {batch} appears after {previous[2]} but is not numbered after it")
            if date < previous[1]:
                errors.append(f"batch {batch} is dated before the preceding batch {previous[2]}")
        previous = (number, date, batch)

    heads: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        heads[row["batch"]].add(row["baseline_git_head"])
    for batch, values in heads.items():
        if len(values) > 1:
            errors.append(f"batch {batch} declares several baseline_git_head values")
    return errors


def check_history(rows: list[dict[str, str]]) -> tuple[list[str], dict[tuple, dict[str, str]]]:
    """Validate one event per identity and batch, and value continuity between successive batches."""
    errors = []
    history: dict[tuple, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        events = history[identity(row)]
        if events and events[-1]["batch"] == row["batch"]:
            errors.append(f"{describe(row)}: duplicate event for this identity in batch {row['batch']}"
                          f" (already {events[-1]['entry_id']})")
            continue
        if events and events[-1]["final_value"] != row["initial_value"]:
            errors.append(f"{describe(row)}: initial_value {row['initial_value']!r} does not continue"
                          f" {events[-1]['entry_id']} final_value {events[-1]['final_value']!r}")
        events.append(row)
    return errors, {key: events[-1] for key, events in history.items()}


def check_current(latest: dict[tuple, dict[str, str]],
                  source_state: Callable[[dict[str, str]], SourceState]) -> list[str]:
    """Only the latest event of each identity has to describe today's application."""
    errors = []
    for row in latest.values():
        state = source_state(row)
        if row["status"] == "NOT_FOUND":
            # Inline NOT_FOUND is already rejected by check_row.
            if state.present:
                errors.append(f"{describe(row)}: marked NOT_FOUND but present in {row['source_file']}")
        elif state.value != row["final_value"]:
            errors.append(f"{describe(row)}: source has {state.value!r}, latest event expects {row['final_value']!r}")
    return errors


def validate(rows: list[dict[str, str]],
             source_state: Callable[[dict[str, str]], SourceState] = read_source_state) -> list[str]:
    errors = [error for row in rows for error in check_row(row)]
    ids = [row["entry_id"] for row in rows]
    if len(ids) != len(set(ids)):
        errors.append("duplicate entry_id")
    chronology = check_chronology(rows)
    errors += chronology
    if chronology:
        # Without a trustworthy order, continuity and current state would be ambiguous.
        return errors
    history_errors, latest = check_history(rows)
    errors += history_errors
    if history_errors:
        return errors
    return errors + check_current(latest, source_state)


def check_catalog_parity() -> list[str]:
    errors = []
    fr = read_catalog(ROOT / "frontend/src/i18n/fr.ts")
    en = read_catalog(ROOT / "frontend/src/i18n/en.ts")
    if set(fr) != set(en):
        errors.append(f"FR/EN catalog keys differ: {sorted(set(fr) ^ set(en))[:10]}")
    for key in sorted(set(fr) & set(en)):
        if set(PLACEHOLDER.findall(fr[key])) != set(PLACEHOLDER.findall(en[key])):
            errors.append(f"catalog placeholders differ for {key}")
    return errors


def load_register(path: Path = REGISTER) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != COLUMNS:
            raise ValueError(f"Unexpected columns: {reader.fieldnames}")
        return list(reader)


def main(argv: list[str]) -> int:
    try:
        rows = load_register()
    except ValueError as error:
        print(error)
        return 1

    if "--show" in argv:
        key = argv[argv.index("--show") + 1]
        for row in rows:
            if row["key_or_msgid"] == key:
                print({name: row[name] for name in COLUMNS})
        return 0

    errors = validate(rows) + check_catalog_parity()
    for error in errors:
        print(f"ERROR {error}")
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row["status"]] += 1
    batches = list(dict.fromkeys(row["batch"] for row in rows))
    print(f"{len(rows)} register entries in {len(batches)} batch(es) {batches}; {dict(sorted(counts.items()))}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
