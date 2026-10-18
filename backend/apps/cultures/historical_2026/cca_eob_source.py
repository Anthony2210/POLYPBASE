"""Read the two reviewed CCA-EOB cultures from the fingerprinted v2 workbook.

Pure Python, read-only, and independent of Django. No observation date is inferred.
Only Semaestomeae rows 121:128 and their headers are in scope.
"""

from . import decisions
from .source import SourceError
from .workbook import WorkbookError, column_letter, read_verified_workbook

V2_SHA256 = "0d5cd16eee88845ea51fbd4f122be15c12e5896b5237458573fc432ebda8b160"

_SHEET = "Semaestomeae"
# Exact source identifiers and row pairs, not a general identifier repair rule.
_CULTURES = (
    ("CCA-EOB-2.04", "CCA-EOB-2.004", (121, 123)),
    ("CCA-EOB-2.05", "CCA-EOB-2.005", (125, 127)),
)
_FIRST_COLUMN = 6
_LAST_COLUMN = 57


def _check_layout(sheet):
    if sheet.state != "visible":
        raise SourceError(f"Expected visible sheet {_SHEET!r}.")
    for column, expected in decisions.EXPECTED_HEADER_LABELS.items():
        if sheet.value(2, column) != expected:
            raise SourceError(f"Unexpected header at {_SHEET}!{column_letter(column)}2.")
    if type(sheet.value(1, 5)) is not int or sheet.value(1, 5) != 2025:
        raise SourceError(f"Expected 2025 recap at {_SHEET}!E1.")
    if sheet.value(2, 5) is not None:
        raise SourceError(f"Unexpected week header at {_SHEET}!E2.")

    seen = set()
    for column in range(_FIRST_COLUMN, _LAST_COLUMN + 1):
        raw = sheet.value(2, column)
        reviewed = decisions.REVIEWED_WEEK_HEADERS.get((_SHEET, column))
        if reviewed is not None:
            if raw != reviewed["raw"]:
                raise SourceError(f"Reviewed week header changed at {_SHEET}!{column_letter(column)}2.")
            week = reviewed["iso_week"]
        else:
            week = raw
        if type(week) is not int or not 1 <= week <= 52:
            raise SourceError(f"Unexpected week header at {_SHEET}!{column_letter(column)}2: {raw!r}.")
        if week in seen:
            raise SourceError(f"Duplicate week {week} header in {_SHEET}.")
        seen.add(week)
        if week != column - 5:
            raise SourceError(f"Misplaced week header at {_SHEET}!{column_letter(column)}2.")

    expected_context = {121: {1: "Cyanea capillata"}}
    for identifier, _, rows in _CULTURES:
        expected_context.setdefault(rows[0], {})[2] = identifier
        for row, temperature in zip(rows, (10, 15)):
            expected_context.setdefault(row, {})[3] = temperature
            for offset, label in enumerate((decisions.POLYP_LABEL, decisions.EPHYRAE_LABEL)):
                current = row + offset
                if sheet.value(current, 4) != label:
                    raise SourceError(f"Unexpected row label at {_SHEET}!D{current}.")
                for column in range(1, 4):
                    value = sheet.value(current, column)
                    expected = expected_context.get(current, {}).get(column)
                    if value != expected or (column == 3 and expected is not None and type(value) is not int):
                        kind = "identifier" if column == 2 else "context"
                        raise SourceError(
                            f"Unexpected {kind} at {_SHEET}!{column_letter(column)}{current}: {value!r}."
                        )

    for row, column in sheet.cells:
        if column > _LAST_COLUMN and (row == 2 or 121 <= row <= 128):
            raise SourceError(f"Unexpected source column at {_SHEET}!{column_letter(column)}{row}.")
    for row, column in sheet.ignored_merged_values:
        if 121 <= row <= 128 or (row == 2 and column >= _FIRST_COLUMN):
            raise SourceError(f"Hidden merged value at {_SHEET}!{column_letter(column)}{row}.")


def _pair(sheet, row, column):
    polyps, ephyrae = sheet.value(row, column), sheet.value(row + 1, column)
    if polyps is None and ephyrae is None:
        return None
    letter = column_letter(column)
    if polyps is None or ephyrae is None:
        raise SourceError(f"Partial pair at {_SHEET}!{letter}{row}/{letter}{row + 1}.")
    for value, current in ((polyps, row), (ephyrae, row + 1)):
        if type(value) is not int or value < 0:
            raise SourceError(
                f"Expected non-negative integer at {_SHEET}!{letter}{current}: {value!r}."
            )
    return {
        "polyp_cell": f"{letter}{row}",
        "ephyrae_cell": f"{letter}{row + 1}",
        "polyps": polyps,
        "ephyrae": ephyrae,
    }


def load_source(workbook_path):
    """Return JSON primitives: sha256, entries, uncovered, excluded_2025.

    Entries carry exact source cells, identifiers, counts and ISO year/week.
    Uncovered records carry sheet, identifiers and ISO year/week, but no counts:
    both temperature blocks are empty. Nonempty column E pairs are returned in
    excluded_2025 with cells, counts, identifiers, iso_year=2025 and a reason;
    no week is assigned to a yearly recap. Empty recap pairs are omitted.

    Any fingerprint, layout, count or duplicate violation raises SourceError.
    The verified reader hashes and parses the same in-memory bytes, without
    reopening the source path between verification and parsing.
    """
    try:
        book = read_verified_workbook(workbook_path, V2_SHA256)
    except WorkbookError as error:
        raise SourceError(str(error)) from error
    sha256 = book.sha256
    if sha256 != V2_SHA256:
        raise SourceError(f"Unexpected workbook SHA-256 {sha256}; expected {V2_SHA256}.")
    sheets = [sheet for sheet in book.sheets if sheet.name == _SHEET]
    if len(sheets) != 1:
        raise SourceError(f"Expected exactly one {_SHEET!r} sheet, found {len(sheets)}.")
    sheet = sheets[0]
    _check_layout(sheet)

    entries, uncovered, excluded = [], [], []
    for identifier, target, rows in _CULTURES:
        identity = {"sheet": _SHEET, "source_identifier": identifier, "target_code": target}
        for row in rows:
            pair = _pair(sheet, row, 5)
            if pair is not None:
                excluded.append({
                    **identity,
                    **pair,
                    "iso_year": 2025,
                    "reason": "RECAP_2025_COLUMN_E",
                })
        for week in range(1, 53):
            key = {**identity, "iso_year": 2026, "iso_week": week}
            pairs = []
            for row in rows:
                pair = _pair(sheet, row, week + 5)
                if pair is not None:
                    pairs.append(pair)
            if len(pairs) > 1:
                raise SourceError(f"Duplicate week {week} payload for {identifier} in {_SHEET}.")
            if pairs:
                entries.append({**key, **pairs[0]})
            else:
                uncovered.append(key)
    return {
        "sha256": sha256,
        "entries": entries,
        "uncovered": uncovered,
        "excluded_2025": excluded,
    }
