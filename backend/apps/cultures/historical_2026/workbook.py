"""Minimal read-only XLSX reader built on the standard library.

It returns cell values exactly as stored (cached values, never formulas) so
extraction can be reproduced without openpyxl or any other dependency.
"""

import hashlib
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CELL_REF = re.compile(r"^([A-Z]+)([0-9]+)$")
_INTEGER = re.compile(r"^-?[0-9]+$")


class WorkbookError(Exception):
    """The workbook cannot be read or is not the expected file."""


@dataclass
class Sheet:
    name: str
    state: str
    cells: dict = field(default_factory=dict)  # (row, column) -> value
    # Stored values hidden inside merged ranges (Excel shows only the anchor).
    ignored_merged_values: dict = field(default_factory=dict)

    def value(self, row, column):
        return self.cells.get((row, column))

    @property
    def max_row(self):
        return max((row for row, _ in self.cells), default=0)

    @property
    def max_column(self):
        return max((column for _, column in self.cells), default=0)


@dataclass
class Workbook:
    sha256: str
    sheets: list


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def column_number(letters):
    number = 0
    for letter in letters:
        number = number * 26 + (ord(letter) - 64)
    return number


def column_letter(number):
    letters = ""
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _text(element):
    """Concatenate every text run, including rich text fragments."""
    return "".join(node.text or "" for node in element.iter(f"{_MAIN}t"))


def _shared_strings(archive):
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [_text(item) for item in root.findall(f"{_MAIN}si")]


def _number(raw):
    if _INTEGER.match(raw):
        return int(raw)
    value = float(raw)
    return int(value) if value.is_integer() else value


def _read_sheet(archive, path, shared, name, state):
    sheet = Sheet(name=name, state=state)
    root = ET.fromstring(archive.read(path))
    for cell in root.iter(f"{_MAIN}c"):
        match = _CELL_REF.match(cell.get("r", ""))
        if match is None:
            raise WorkbookError(f"Unreadable cell reference in sheet {name!r}.")
        kind = cell.get("t", "n")
        if kind == "inlineStr":
            inline = cell.find(f"{_MAIN}is")
            value = _text(inline) if inline is not None else None
        else:
            node = cell.find(f"{_MAIN}v")
            if node is None or node.text is None:
                continue
            raw = node.text
            if kind == "s":
                value = shared[int(raw)]
            elif kind == "str":
                value = raw
            elif kind == "b":
                value = raw == "1"
            elif kind == "e":
                raise WorkbookError(
                    f"Error value {raw!r} at {match.group(0)} in sheet {name!r}."
                )
            else:
                value = _number(raw)
        if value is None or value == "":
            continue
        sheet.cells[(int(match.group(2)), column_number(match.group(1)))] = value
    _drop_merged_non_anchor_values(sheet, root)
    return sheet


def _drop_merged_non_anchor_values(sheet, root):
    """Only the top-left cell of a merged range carries a visible value."""
    merges = root.find(f"{_MAIN}mergeCells")
    if merges is None:
        return
    for merge in merges.findall(f"{_MAIN}mergeCell"):
        start, _, end = merge.get("ref", "").partition(":")
        first, last = _CELL_REF.match(start), _CELL_REF.match(end)
        if first is None or last is None:
            raise WorkbookError(f"Unreadable merged range in sheet {sheet.name!r}.")
        top, left = int(first.group(2)), column_number(first.group(1))
        bottom, right = int(last.group(2)), column_number(last.group(1))
        for row in range(top, bottom + 1):
            for column in range(left, right + 1):
                if (row, column) != (top, left) and (row, column) in sheet.cells:
                    sheet.ignored_merged_values[(row, column)] = sheet.cells.pop((row, column))


def read_workbook(path):
    sha256 = file_sha256(path)
    with zipfile.ZipFile(path) as archive:
        shared = _shared_strings(archive)
        book = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            rel.get("Id"): rel.get("Target") for rel in rels.findall(f"{_PKG_REL}Relationship")
        }
        sheets = []
        for node in book.find(f"{_MAIN}sheets").findall(f"{_MAIN}sheet"):
            target = targets[node.get(f"{_REL}id")]
            target = target.lstrip("/")
            if not target.startswith("xl/"):
                target = f"xl/{target}"
            sheets.append(
                _read_sheet(
                    archive,
                    target,
                    shared,
                    node.get("name"),
                    node.get("state", "visible"),
                )
            )
    return Workbook(sha256=sha256, sheets=sheets)
