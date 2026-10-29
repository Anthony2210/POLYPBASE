"""Check the application translation register against its history and the current sources.

Usage (from the repository root):
    python docs/translation-review/application/check_register.py
    python docs/translation-review/application/check_register.py --show KEY

Standard library only. Read-only: it never modifies a file.
Regression tests: python -m unittest discover -s docs/translation-review/application -p "test_*.py"
"""
from __future__ import annotations

import csv
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Callable, NamedTuple

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
from compile_django_messages import parse_po_file  # noqa: E402

REGISTER = Path(__file__).with_name("register.csv")
COLUMNS = [
    "entry_id", "batch", "surface", "key_or_msgid", "language", "source_file", "initial_value", "final_value",
    "sheet_value", "decision_ref", "status", "reason", "baseline_git_head", "baseline_sheet_sha256",
]
STATUSES = {"APPLIED", "ALREADY_MATCHING", "PRESERVED", "DEFERRED", "NOT_FOUND", "REMOVED"}
SURFACES = {"react", "django", "inline"}
LANGUAGES = {"FR", "EN"}
# REMOVED is limited to the React catalogs: each language has exactly one catalog file.
REACT_CATALOGS = {"FR": "frontend/src/i18n/fr.ts", "EN": "frontend/src/i18n/en.ts"}
# After a removal, the identity may only be confirmed absent; any other status would contradict it.
AFTER_REMOVED = {"NOT_FOUND"}
# Fields that may legitimately be empty; initial/final values are checked per status.
OPTIONAL_FIELDS = {"sheet_value", "initial_value", "final_value"}
BATCH_ID = re.compile(r"^B(\d{2,})-(\d{4}-\d{2}-\d{2})$")
ENTRY_ID = re.compile(r"^T(\d{3,})$")
GIT_HEAD = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PLACEHOLDER = re.compile(r"\{(\w+)\}")
# Strict tokenizer for the React catalogs. Any character it cannot classify makes the catalog
# unreadable, so a key can never be reported absent because of unusual formatting.
CATALOG_TOKEN = re.compile(r"""
    (?P<space>\s+)
  | (?P<comment>//[^\n]*|/\*.*?\*/)
  | (?P<string>'(?:[^'\\\r\n]|\\.)*'|"(?:[^"\\\r\n]|\\.)*"|`(?:[^`\\$]|\\.|\$(?!\{))*`)
  | (?P<name>[A-Za-z_$][\w$]*)
  | (?P<punct>[{}<>:;,=.|\[\]])
""", re.VERBOSE | re.DOTALL)
SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v"}


class CatalogFormatError(ValueError):
    """The catalog uses a construct the register checker cannot read reliably."""


class SourceState(NamedTuple):
    """What the application shows today for one register identity."""

    present: bool
    value: str | None


def identity(row: dict[str, str]) -> tuple[str, str, str, str]:
    """A translation event targets one key or msgid, in one language, on one surface and file."""
    return row["surface"], row["key_or_msgid"], row["language"], row["source_file"]


def describe(row: dict[str, str]) -> str:
    return f"{row['entry_id']} [{row['batch']}] {row['surface']}:{row['key_or_msgid']} {row['language']}"


def decode_js_string(token: str) -> str:
    """Decode a JavaScript string or substitution-free template literal token."""
    body = token[1:-1]
    if token[0] == "`":
        body = body.replace("\r\n", "\n").replace("\r", "\n")
    result, index = [], 0
    while index < len(body):
        char = body[index]
        if char != "\\":
            result.append(char)
            index += 1
            continue
        escape = body[index + 1]
        index += 2
        if escape in SIMPLE_ESCAPES:
            result.append(SIMPLE_ESCAPES[escape])
        elif escape == "0" and not body[index:index + 1].isdigit():
            result.append("\0")
        elif escape == "x" and re.fullmatch(r"[0-9a-fA-F]{2}", body[index:index + 2]):
            result.append(chr(int(body[index:index + 2], 16)))
            index += 2
        elif escape == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", body[index:index + 4]):
            result.append(chr(int(body[index:index + 4], 16)))
            index += 4
        elif escape == "u" and (braced := re.match(r"\{([0-9a-fA-F]{1,6})\}", body[index:])):
            result.append(chr(int(braced.group(1), 16)))
            index += braced.end()
        elif escape in "\r\n  ":
            # Line continuation: the escaped line terminator is not part of the value.
            if escape == "\r" and body[index:index + 1] == "\n":
                index += 1
        elif escape in "123456789xu":
            raise CatalogFormatError(f"unsupported escape sequence \\{escape} in {token[:40]}")
        else:
            result.append(escape)
    return "".join(result)


def tokenize_catalog(text: str) -> list[tuple[str, str, int]]:
    tokens, position = [], 0
    while position < len(text):
        match = CATALOG_TOKEN.match(text, position)
        if not match:
            line = text.count("\n", 0, position) + 1
            raise CatalogFormatError(f"line {line}: unsupported catalog syntax {text[position:position + 20]!r}")
        if match.lastgroup not in ("space", "comment"):
            tokens.append((match.lastgroup, match.group(), text.count("\n", 0, position) + 1))
        position = match.end()
    return tokens


def parse_catalog(text: str, language: str) -> dict[str, str]:
    """Parse one React catalog, accepting only the exact shape of the application catalogs.

    Accepted: optional `import type { A, B } from '...';` lines, a single
    `export const <language>[: Type] = { key: 'value', ... } [as const];` and optional
    `export type Name = keyof typeof <language>;` lines. Keys are identifiers or string
    literals, values are string literals. Anything else fails closed with CatalogFormatError.
    """
    tokens = tokenize_catalog(text)
    position = 0

    def peek(offset: int = 0) -> tuple[str, str, int] | None:
        return tokens[position + offset] if position + offset < len(tokens) else None

    def fail(expected: str) -> CatalogFormatError:
        token = peek()
        found = f"{token[1]!r} on line {token[2]}" if token else "end of file"
        return CatalogFormatError(f"{language} catalog: expected {expected}, found {found}")

    def take(kind: str, value: str | None = None) -> str:
        nonlocal position
        token = peek()
        if not token or token[0] != kind or (value is not None and token[1] != value):
            raise fail(value or kind)
        position += 1
        return token[1]

    def at(value: str) -> bool:
        token = peek()
        return bool(token) and token[0] in ("name", "punct") and token[1] == value

    while at("import"):
        take("name", "import"), take("name", "type"), take("punct", "{")
        take("name")
        while at(","):
            take("punct", ",")
            if not at("}"):
                take("name")
        take("punct", "}"), take("name", "from"), take("string"), take("punct", ";")

    take("name", "export"), take("name", "const"), take("name", language)
    if at(":"):
        take("punct", ":")
        # Type annotation such as Record<TranslationKey, string>: names and type punctuation only.
        while peek() and not at("="):
            kind, value, _ = peek()
            if kind != "name" and value not in ("<", ">", ",", ".", "[", "]", "|"):
                raise fail("a type annotation")
            position += 1
    take("punct", "=")
    take("punct", "{")
    values: dict[str, str] = {}
    while not at("}"):
        token = peek()
        if token and token[0] == "name":
            key = take("name")
        elif token and token[0] == "string":
            key = decode_js_string(take("string"))
        else:
            raise fail("a catalog key")
        if not key.strip():
            raise CatalogFormatError(f"{language} catalog: empty key on line {token[2]}")
        if key in values:
            raise CatalogFormatError(f"{language} catalog: duplicate key {key!r} on line {token[2]}")
        take("punct", ":")
        values[key] = decode_js_string(take("string"))
        if not at("}"):
            take("punct", ",")
    take("punct", "}")
    if at("as"):
        take("name", "as"), take("name", "const")
    take("punct", ";")

    while at("export"):
        take("name", "export"), take("name", "type"), take("name"), take("punct", "=")
        take("name", "keyof"), take("name", "typeof"), take("name", language), take("punct", ";")
    if peek():
        raise fail("end of file")
    return values


@lru_cache(maxsize=8)
def _parse_catalog_cached(text: str, language: str) -> dict[str, str]:
    return parse_catalog(text, language)


def read_catalog(path: Path) -> dict[str, str]:
    # Cached on the exact file text: every register row re-reads the file, never a stale parse.
    return dict(_parse_catalog_cached(path.read_text(encoding="utf-8"), path.stem))


def read_po(path: Path) -> dict[str, str]:
    messages = parse_po_file(path)
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
        messages = read_po(path)
        return SourceState(key in messages, messages.get(key) or key)
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
    if row["status"] == "REMOVED":
        # final_value is empty: the catalog identity is intentionally absent after this event.
        if row["surface"] != "react":
            errors.append(f"{label}: REMOVED is only supported for the React catalog surface")
        elif row["source_file"] != REACT_CATALOGS.get(row["language"]):
            errors.append(f"{label}: REMOVED must name the {row['language']} React catalog"
                          f" {REACT_CATALOGS.get(row['language'])}")
        if not initial:
            errors.append(f"{label}: REMOVED requires the exact pre-removal value as initial_value")
        if final:
            errors.append(f"{label}: REMOVED must leave final_value empty")
        if row["sheet_value"]:
            errors.append(f"{label}: REMOVED must not carry a sheet_value")
        return errors
    if not initial or not final:
        errors.append(f"{label}: {row['status']} requires initial_value and final_value")
    if row["status"] == "APPLIED" and initial == final:
        errors.append(f"{label}: APPLIED without a change of value")
    if row["status"] != "APPLIED" and initial != final:
        errors.append(f"{label}: {row['status']} must not change the value")
    if Counter(PLACEHOLDER.findall(initial)) != Counter(PLACEHOLDER.findall(final)):
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
        batch_match = BATCH_ID.match(row["batch"])
        if not batch_match:
            errors.append(f"{describe(row)}: malformed batch id (expected Bnn-YYYY-MM-DD)")
            continue
        try:
            date.fromisoformat(batch_match.group(2))
        except ValueError:
            errors.append(f"{describe(row)}: invalid calendar date in batch")
            continue
        if not blocks or blocks[-1] != row["batch"]:
            if row["batch"] in blocks:
                errors.append(f"{describe(row)}: batch {row['batch']} is split by another batch")
            blocks.append(row["batch"])

    seen_numbers: dict[int, str] = {}
    previous = None
    for batch in dict.fromkeys(blocks):
        match = BATCH_ID.match(batch)
        number, batch_date = int(match.group(1)), match.group(2)
        if number in seen_numbers and seen_numbers[number] != batch:
            errors.append(f"batch number {number} is used by {seen_numbers[number]} and {batch}")
        seen_numbers.setdefault(number, batch)
        if previous is not None:
            if number <= previous[0]:
                errors.append(f"batch {batch} appears after {previous[2]} but is not numbered after it")
            if batch_date < previous[1]:
                errors.append(f"batch {batch} is dated before the preceding batch {previous[2]}")
        previous = (number, batch_date, batch)

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
        if events and events[-1]["status"] == "REMOVED" and row["status"] not in AFTER_REMOVED:
            errors.append(f"{describe(row)}: {row['status']} contradicts the removal recorded in"
                          f" {events[-1]['entry_id']}; the key must stay absent")
        elif events and events[-1]["final_value"] != row["initial_value"]:
            errors.append(f"{describe(row)}: initial_value {row['initial_value']!r} does not continue"
                          f" {events[-1]['entry_id']} final_value {events[-1]['final_value']!r}")
        events.append(row)
    return errors, {key: events[-1] for key, events in history.items()}


def check_removal_pairs(rows: list[dict[str, str]]) -> list[str]:
    """A catalog key is removed from FR and EN together, in the same batch."""
    languages: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for row in rows:
        if row["status"] == "REMOVED":
            languages[row["batch"], row["surface"], row["key_or_msgid"]].append(row["language"])
    return [f"batch {batch}: REMOVED {surface}:{key} must have exactly one FR and one EN event, found"
            f" {sorted(found)}"
            for (batch, surface, key), found in languages.items() if sorted(found) != ["EN", "FR"]]


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
        elif row["status"] == "REMOVED":
            if state.present:
                errors.append(f"{describe(row)}: marked REMOVED but present in {row['source_file']};"
                              " a removed key cannot reappear without a new register event")
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
    errors += history_errors + check_removal_pairs(rows)
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
        if Counter(PLACEHOLDER.findall(fr[key])) != Counter(PLACEHOLDER.findall(en[key])):
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

    try:
        errors = validate(rows) + check_catalog_parity()
    except (ValueError, OSError) as error:
        print(f"ERROR source validation: {error}")
        return 1
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
