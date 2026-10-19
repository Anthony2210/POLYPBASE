"""Exact, offline XLSX extraction. No Django, database, or legacy repair imports."""

import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path, PurePosixPath

from .temperature_series import temperature_decimal

SOURCES = {
    "Suivi_2020.xlsx": (2020, "42f5bebd82917de9fdea51f823b158c0b3869d701ac714c7e8d743c45efc0250"),
    "Suivi_2021.xlsx": (2021, "57b3513a962f5f0667646c3f6bd26376c215da9b6c13fe33ddad2bd4895f9dee"),
    "Suivi_2022.xlsx": (2022, "4761e642eb1a882a23114bbae267ce92c3f31db2cd64c2e76ae4e4191db861e9"),
    "Suivi_2023.xlsx": (2023, "f30e00a2ae97112a6969dae78d4c5541b9e8cd7f98e850d36e2e1094be6a33ce"),
    "Suivi_2024.xlsx": (2024, "8f60d451ad2c7f2c37796bb7adf4f509cac5561b9bc9e0b3469235e1324ca330"),
    "Suivi_2025.xlsx": (2025, "0776396ad12b6479e53755496b0e6c51ec3a2e1ff581c983f84f82fe943b84fa"),
    "Suivi_2026_actualisé_v2.xlsx": (2026, "0d5cd16eee88845ea51fbd4f122be15c12e5896b5237458573fc432ebda8b160"),
}
_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_REF = re.compile(r"([A-Z]+)([1-9][0-9]*)")
_BOX = re.compile(r"([A-Z]{3}-[A-Z]{3}-[0-9]+)\.([0-9]{1,3})")
_LABELS = ("Nb polypes", "Nb éphyrules")


class SourceError(ValueError):
    pass


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def coordinates(ref):
    match = _REF.fullmatch(ref)
    if not match:
        raise SourceError(f"Invalid source coordinate: {ref!r}")
    column = 0
    for letter in match[1]:
        column = column * 26 + ord(letter) - 64
    return int(match[2]), column


def column_letter(column):
    result = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        result = chr(65 + remainder) + result
    return result


def canonical_box(label):
    match = _BOX.fullmatch(label)
    return f"{match[1]}.{int(match[2]):03d}" if match else None


@dataclass(frozen=True)
class Cell:
    value: str = ""
    kind: str = "missing"
    formula: bool = False

    @property
    def present(self):
        return self.kind != "missing" and bool(self.value.strip())


@dataclass
class Sheet:
    name: str
    state: str = "visible"
    cells: dict = field(default_factory=dict)
    merges: list = field(default_factory=list)
    ignored: list = field(default_factory=list)

    def cell(self, row, column):
        return self.cells.get((row, column), Cell())

    def merged_range(self, row, column):
        matches = [ref for ref in self.merges if coordinates(ref.split(":")[0]) == (row, column)]
        if len(matches) > 1:
            raise SourceError(f"Overlapping source merges in {self.name}")
        return matches[0] if matches else ""


def _read_sheets(content):
    """Keep numeric XML lexemes, types and formulas; never calculate a formula."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(node.text or "" for node in item.iter(f"{_MAIN}t"))
                      for item in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        book = ET.fromstring(archive.read("xl/workbook.xml"))
        targets = {}
        for node in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels")).findall(f"{_PKG}Relationship"):
            relationship_id, target = node.get("Id"), node.get("Target")
            if not relationship_id or not target:
                raise SourceError("A workbook relationship has no ID or target.")
            targets[relationship_id] = target
        sheet_nodes = book.find(f"{_MAIN}sheets")
        if sheet_nodes is None:
            raise SourceError("The workbook has no sheet list.")
        sheets = []
        for item in sheet_nodes:
            relationship_id, name = item.get(f"{_REL}id"), item.get("name")
            if not relationship_id or relationship_id not in targets or not name:
                raise SourceError("A workbook sheet has no name or valid relationship.")
            target = targets[relationship_id]
            path = target.lstrip("/") if target.startswith("/") else str(PurePosixPath("xl") / target)
            root = ET.fromstring(archive.read(path))
            sheet = Sheet(name, item.get("state", "visible"))
            for node in root.iter(f"{_MAIN}c"):
                row, column = coordinates(node.get("r", ""))
                kind = node.get("t", "n")
                value = node.find(f"{_MAIN}v")
                if kind == "inlineStr":
                    raw = "".join(t.text or "" for t in node.iter(f"{_MAIN}t"))
                elif value is not None:
                    raw = value.text or ""
                    if kind == "s":
                        raw = shared[int(raw)]
                else:
                    raw = ""
                formula = node.find(f"{_MAIN}f") is not None
                if raw or formula:
                    sheet.cells[(row, column)] = Cell(raw, kind, formula)
            for node in root.iter(f"{_MAIN}mergeCell"):
                ref = node.get("ref", "")
                first, last = ref.split(":")
                top, left = coordinates(first)
                bottom, right = coordinates(last)
                sheet.merges.append(ref)
                # Iterate stored cells only, never a potentially full-sheet range.
                covered = [key for key in sheet.cells if top <= key[0] <= bottom
                           and left <= key[1] <= right and key != (top, left)]
                for key in covered:
                    cell = sheet.cells.pop(key)
                    sheet.ignored.append({"cell": f"{column_letter(key[1])}{key[0]}",
                                          "value": cell.value, "kind": cell.kind,
                                          "formula": cell.formula, "merged_range": ref})
            sheets.append(sheet)
        return sheets


def read_source(path):
    path = Path(path)
    if path.name not in SOURCES:
        raise SourceError(f"Source is not allowlisted: {path.name}")
    # Parse exactly the bytes whose fingerprint was checked (no second read).
    content = path.read_bytes()
    year, expected = SOURCES[path.name]
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise SourceError(f"SHA-256 mismatch for {path.name}: expected {expected}, got {actual}")
    return year, actual, _read_sheets(content)


def numeric_state(cell):
    if not cell.present and not cell.formula:
        return "missing"
    if cell.kind not in {"n", "s", "str", "inlineStr"} or cell.formula:
        return "malformed"
    try:
        temperature_decimal(cell.value)
    except ValueError:
        return "malformed"
    return "numeric"


def count_value(cell):
    if numeric_state(cell) != "numeric":
        return None
    value = Decimal(cell.value.strip())
    if value < 0 or value != value.to_integral_value() or value > 2147483647:
        return None
    return int(value)


def _week(cell, year):
    if numeric_state(cell) != "numeric":
        return None
    value = Decimal(cell.value.strip())
    if value != value.to_integral_value() or not 1 <= value <= 53:
        return None
    week = int(value)
    try:
        date.fromisocalendar(year, week, 1)
    except ValueError:
        return None
    return week


def extract_sheet(sheet, *, filename, year, sha256):
    """Extract literal row pairs, without inheriting any older 2026 decisions."""
    result = {"candidates": [], "blocks": [], "exclusions": [], "header_issues": [],
              "ignored_merged_values": sheet.ignored}
    anchor_rows = {row for row, col in sheet.cells if col == 3 and row >= 3}
    consumed = set()
    for (row, col), cell in sorted(sheet.cells.items()):
        if row >= 3 and col == 4 and cell.value in _LABELS and row not in consumed:
            anchor_rows.add(row)
            consumed.add(row + 1)
    anchors = [(row, sheet.cell(row, 3)) for row in sorted(anchor_rows)]
    if not anchors:
        result["exclusions"].append({"reason": "NO_TEMPERATURE_ANCHORS", "sheet": sheet.name})
        return result
    expected = {1: "Espèce", 2: "N° boîte", 3: "Température (°C)"}
    if any(sheet.cell(2, col).value != value for col, value in expected.items()):
        raise SourceError(f"Unrecognized tracking layout: {filename}/{sheet.name}")
    first_week_column = 6 if year >= 2025 else 5
    if year >= 2025 and (sheet.cell(1, 5).value != str(year - 1) or sheet.cell(2, 5).present):
        raise SourceError(f"Unexpected retrospective column: {filename}/{sheet.name}")
    max_column = max((col for _, col in sheet.cells), default=first_week_column)
    columns = {}
    for column in range(first_week_column, max_column + 1):
        header = sheet.cell(2, column)
        week = _week(header, year)
        columns[column] = week
        if week is None:
            result["header_issues"].append({"sheet": sheet.name,
                "cell": f"{column_letter(column)}2", "source_value": header.value,
                "reason": "UNRESOLVED_WEEK_HEADER"})
    ambiguous_columns = set()
    for week in sorted({week for week in columns.values() if week is not None}):
        duplicates = [column for column, value in columns.items() if value == week]
        if len(duplicates) > 1:
            ambiguous_columns.update(duplicates)
            result["header_issues"].append({"sheet": sheet.name, "iso_week": week,
                "cells": [f"{column_letter(col)}2" for col in duplicates],
                "reason": "DUPLICATE_WEEK_HEADER"})
    # Context follows visible source boundaries, not old identity corrections.
    context = {}
    active_box = ""
    max_row = max((row for row, _ in sheet.cells), default=2)
    for row in range(3, max_row + 1):
        species, box = sheet.cell(row, 1), sheet.cell(row, 2)
        if species.present:
            active_box = box.value if box.present else ""
        elif box.present:
            active_box = box.value
        context[row] = active_box
    for row, temperature in anchors:
        source_box = context[row]
        base = {"source_file": filename, "source_sha256": sha256, "sheet": sheet.name,
                "temperature_cell": f"C{row}", "temperature_merged_range": sheet.merged_range(row, 3),
                "source_value": temperature.value, "source_box": source_box}
        state = numeric_state(temperature)
        labels = (sheet.cell(row, 4).value, sheet.cell(row + 1, 4).value)
        block = {**base, "temperature_state": state, "source_kind": temperature.kind,
                 "source_formula": temperature.formula, "row_labels": list(labels),
                 "biological_occurrences": 0, "empty_weeks": 0, "retrospective_entries": 0}
        result["blocks"].append(block)
        if labels != _LABELS:
            block["issue"] = "UNREVIEWED_ROW_ORIENTATION"
        for column in range(5, max_column + 1):
            polyp, ephyra = sheet.cell(row, column), sheet.cell(row + 1, column)
            if not polyp.present and not ephyra.present and not polyp.formula and not ephyra.formula:
                if column >= first_week_column and columns[column] is not None:
                    block["empty_weeks"] += 1
                continue
            raw = {**base, "polyp_cell": f"{column_letter(column)}{row}", "polyp_value": polyp.value,
                   "ephyrae_cell": f"{column_letter(column)}{row+1}", "ephyrae_value": ephyra.value,
                   "week_cell": f"{column_letter(column)}2", "week_value": sheet.cell(2, column).value}
            if column < first_week_column:
                block["retrospective_entries"] += 1
                result["exclusions"].append({**raw, "reason": "RETROSPECTIVE_COLUMN_NO_WEEK"})
                continue
            block["biological_occurrences"] += 1
            week = columns[column]
            if state != "numeric" or week is None:
                reason = "UNRESOLVED_WEEK_HEADER" if week is None else f"TEMPERATURE_{state.upper()}"
                result["exclusions"].append({**raw, "reason": reason})
                continue
            polyps, ephyrae = count_value(polyp), count_value(ephyra)
            bio_state = "complete"
            if not polyp.present or not ephyra.present:
                bio_state = "partial"
            if (labels != _LABELS or column in ambiguous_columns
                    or (polyp.present and polyps is None) or (ephyra.present and ephyrae is None)):
                bio_state = "malformed"
            evidence = {**raw, "source_iso_year": year, "source_iso_week": week}
            candidate = {"candidate_id": fingerprint(evidence), "iso_year": year, "iso_week": week,
                         "source_box": source_box, "canonical_box": canonical_box(source_box),
                         "temperature_c": temperature.value, "biological_state": bio_state,
                         "polyps": polyps, "ephyrae": ephyrae, "evidence": evidence}
            result["candidates"].append(candidate)
        if not block["biological_occurrences"]:
            result["exclusions"].append({**base, "reason": "NO_CURRENT_BIOLOGICAL_OCCURRENCES"})
    return result


def extract_sources(raw_directory):
    combined = {"sources": [], "candidates": [], "blocks": [], "exclusions": [],
                "header_issues": [], "ignored_merged_values": []}
    for filename in SOURCES:
        year, sha256, sheets = read_source(Path(raw_directory) / filename)
        source = {"filename": filename, "iso_year": year, "sha256": sha256, "hash_verified": True,
                  "numeric_anchors": 0, "blocks_with_biology": 0, "blocks_without_biology": 0,
                  "blocks_with_any_entries": 0, "candidate_associations": 0}
        for sheet in sheets:
            part = extract_sheet(sheet, filename=filename, year=year, sha256=sha256)
            for key in ("candidates", "blocks", "exclusions", "header_issues"):
                if key == "header_issues":
                    combined[key].extend({"source_file": filename, **item} for item in part[key])
                else:
                    combined[key].extend(part[key])
            combined["ignored_merged_values"].extend(
                {"source_file": filename, "sheet": sheet.name, **item}
                for item in part["ignored_merged_values"])
            numeric = [block for block in part["blocks"] if block["temperature_state"] == "numeric"]
            source["numeric_anchors"] += len(numeric)
            source["blocks_with_biology"] += sum(bool(b["biological_occurrences"]) for b in numeric)
            source["blocks_without_biology"] += sum(not b["biological_occurrences"] for b in numeric)
            source["blocks_with_any_entries"] += sum(bool(b["biological_occurrences"] or b["retrospective_entries"]) for b in numeric)
            source["candidate_associations"] += len(part["candidates"])
        combined["sources"].append(source)
    return combined
