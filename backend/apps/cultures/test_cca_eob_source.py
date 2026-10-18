"""Database-free CCA-EOB v2 extraction tests, with synthetic source values."""

import copy
import hashlib
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from pathlib import Path
from unittest import mock

from .historical_2026 import cca_eob_source as source
from .historical_2026 import workbook as workbook_module
from .historical_2026.workbook import (
    Sheet,
    Workbook,
    WorkbookError,
    column_letter,
    file_sha256,
    read_verified_workbook,
    read_workbook,
)

REAL_WORKBOOK = (
    Path.home() / "POLYPBASE-ANALYSES" / "data" / "raw" / "Suivi_2026_actualisé_v2.xlsx"
)


def synthetic_sheet():
    cells = {
        (1, 5): 2025,
        (2, 1): "Espèce",
        (2, 2): "N° boîte",
        (2, 3): "Température (°C)",
        (121, 1): "Cyanea capillata",
        (121, 2): "CCA-EOB-2.04",
        (125, 2): "CCA-EOB-2.05",
    }
    for week in range(1, 53):
        cells[(2, week + 5)] = " -" if week == 20 else week
    for row, temperature in ((121, 10), (123, 15), (125, 10), (127, 15)):
        cells[(row, 3)] = temperature
        cells[(row, 4)] = "Nb polypes"
        cells[(row + 1, 4)] = "Nb éphyrules"
    for week in range(1, 41):
        row4 = 121 if 10 <= week <= 31 else 123
        row5 = 127 if 10 <= week <= 31 else 125
        for index, row in enumerate((row4, row5)):
            cells[(row, week + 5)] = 1000 + index * 100 + week
            cells[(row + 1, week + 5)] = week % 3
    cells[(123, 6)] = 0
    cells[(124, 6)] = 0
    cells[(123, 5)], cells[(124, 5)] = 901, 0
    cells[(125, 5)], cells[(126, 5)] = 902, 903
    return Sheet(name="Semaestomeae", state="visible", cells=cells)


def write_xlsx(path, sheet):
    """Write only a temporary, synthetic workbook using the standard library."""
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package = "http://schemas.openxmlformats.org/package/2006/relationships"
    book = ET.Element("workbook", xmlns=main)
    sheets = ET.SubElement(book, "sheets")
    ET.SubElement(sheets, "sheet", name=sheet.name, sheetId="1", attrib={f"{{{rel}}}id": "rId1"})
    relationships = ET.Element("Relationships", xmlns=package)
    ET.SubElement(relationships, "Relationship", Id="rId1", Target="worksheets/sheet1.xml")
    worksheet = ET.Element("worksheet", xmlns=main)
    data = ET.SubElement(worksheet, "sheetData")
    rows = {}
    for (row, column), value in sorted(sheet.cells.items()):
        if value is None:
            continue
        if row not in rows:
            rows[row] = ET.SubElement(data, "row", r=str(row))
        cell = ET.SubElement(rows[row], "c", r=f"{column_letter(column)}{row}")
        if isinstance(value, str):
            cell.set("t", "inlineStr")
            inline = ET.SubElement(cell, "is")
            ET.SubElement(inline, "t").text = value
        else:
            ET.SubElement(cell, "v").text = str(value)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", ET.tostring(book))
        archive.writestr("xl/_rels/workbook.xml.rels", ET.tostring(relationships))
        archive.writestr("xl/worksheets/sheet1.xml", ET.tostring(worksheet))


class VerifiedWorkbookTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "synthetic.xlsx"
        write_xlsx(self.path, synthetic_sheet())
        self.data = self.path.read_bytes()
        self.sha256 = hashlib.sha256(self.data).hexdigest()

    def test_mismatched_valid_zip_is_rejected_before_zip_or_xml_interpretation(self):
        with (
            mock.patch.object(workbook_module.zipfile, "ZipFile") as archive,
            mock.patch.object(workbook_module, "_read_archive") as parser,
            mock.patch.object(workbook_module.ET, "fromstring") as xml,
        ):
            with self.assertRaisesRegex(WorkbookError, "Unexpected workbook SHA-256"):
                read_verified_workbook(self.path, "0" * 64)
        archive.assert_not_called()
        parser.assert_not_called()
        xml.assert_not_called()

    def test_verified_invalid_zip_reaches_zip_validation(self):
        self.path.write_bytes(b"Not a ZIP archive")
        sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with mock.patch.object(workbook_module, "_read_archive") as parser:
            with self.assertRaises(zipfile.BadZipFile):
                read_verified_workbook(self.path, sha256)
        parser.assert_not_called()

    def test_source_is_opened_and_read_once_in_binary_mode(self):
        binary_open = mock.mock_open(read_data=self.data)
        with (
            mock.patch.object(workbook_module, "open", binary_open, create=True),
            mock.patch.object(workbook_module, "file_sha256") as legacy_hash,
        ):
            book = read_verified_workbook(self.path, self.sha256)
        binary_open.assert_called_once_with(self.path, "rb")
        binary_open.return_value.read.assert_called_once_with()
        legacy_hash.assert_not_called()
        self.assertEqual(book.sha256, self.sha256)
        self.assertEqual(book.sheets[0].value(123, 6), 0)

    def test_source_parses_hashed_bytes_even_if_path_is_replaced_before_zip_open(self):
        replacement_path = self.path.with_name("replacement.xlsx")
        replacement_sheet = synthetic_sheet()
        replacement_sheet.cells[(123, 6)] = 7777
        write_xlsx(replacement_path, replacement_sheet)
        replacement = replacement_path.read_bytes()
        real_hash = hashlib.sha256
        real_zip = zipfile.ZipFile
        hashed = []
        order = []

        def hash_and_replace(data):
            hashed.append(data)
            digest = real_hash(data)
            self.path.write_bytes(replacement)
            order.append("sha256")
            return digest

        def open_zip(buffer):
            order.append("zip")
            self.assertEqual(order, ["sha256", "zip"])
            self.assertIsInstance(buffer, BytesIO)
            self.assertEqual(buffer.getvalue(), hashed[0])
            self.assertEqual(buffer.getvalue(), self.data)
            return real_zip(buffer)

        with (
            mock.patch.object(source, "V2_SHA256", self.sha256),
            mock.patch.object(workbook_module, "open", wraps=open, create=True) as binary_open,
            mock.patch.object(workbook_module.hashlib, "sha256", side_effect=hash_and_replace) as digest,
            mock.patch.object(workbook_module.zipfile, "ZipFile", side_effect=open_zip) as archive,
        ):
            result = source.load_source(self.path)
        binary_open.assert_called_once_with(self.path, "rb")
        digest.assert_called_once_with(self.data)
        archive.assert_called_once()
        self.assertEqual(order, ["sha256", "zip"])
        self.assertEqual(result["sha256"], self.sha256)
        self.assertEqual(result["entries"][0]["polyps"], 0)
        self.assertEqual(self.path.read_bytes(), replacement)
        self.assertNotEqual(file_sha256(self.path), self.sha256)

    def test_legacy_and_verified_readers_share_parser_and_return_same_workbook(self):
        with mock.patch.object(workbook_module, "_read_archive", wraps=workbook_module._read_archive) as parser:
            legacy = read_workbook(self.path)
            verified = read_verified_workbook(self.path, self.sha256)
        self.assertEqual(parser.call_count, 2)
        self.assertEqual(legacy, verified)
        self.assertEqual(verified.sha256, self.sha256)
        self.assertEqual(verified.sheets, [synthetic_sheet()])


class CcaEobSourceTests(unittest.TestCase):
    def setUp(self):
        self.sheet = synthetic_sheet()
        self.book = Workbook(sha256=source.V2_SHA256, sheets=[self.sheet])

    def load(self):
        with mock.patch.object(source, "read_verified_workbook", return_value=self.book):
            return source.load_source("synthetic.xlsx")

    def test_reviewed_sha_constant(self):
        self.assertEqual(
            source.V2_SHA256,
            "0d5cd16eee88845ea51fbd4f122be15c12e5896b5237458573fc432ebda8b160",
        )

    def test_wrong_sha_rejected_before_zip_or_xml_interpretation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "not-a-workbook.xlsx"
            path.write_bytes(b"Not even a ZIP archive")
            with (
                mock.patch.object(workbook_module.zipfile, "ZipFile") as archive,
                mock.patch.object(workbook_module, "_read_archive") as parser,
            ):
                with self.assertRaisesRegex(source.SourceError, "Unexpected workbook SHA-256") as error:
                    source.load_source(path)
            self.assertIsInstance(error.exception.__cause__, WorkbookError)
            archive.assert_not_called()
            parser.assert_not_called()

    def test_source_passes_expected_sha_to_verified_reader(self):
        with mock.patch.object(source, "read_verified_workbook", return_value=self.book) as reader:
            result = source.load_source("synthetic.xlsx")
        reader.assert_called_once_with("synthetic.xlsx", source.V2_SHA256)
        self.assertEqual(result["sha256"], source.V2_SHA256)

    def test_reader_sha_is_checked_before_layout(self):
        self.book.sha256 = "1" * 64
        self.book.sheets = []
        with mock.patch.object(source, "_check_layout") as layout:
            with self.assertRaisesRegex(source.SourceError, "Unexpected workbook SHA-256"):
                self.load()
        layout.assert_not_called()

    def test_exact_entries_and_block_switches(self):
        result = self.load()
        self.assertEqual(set(result), {"sha256", "entries", "uncovered", "excluded_2025"})
        self.assertEqual(result["sha256"], source.V2_SHA256)
        self.assertEqual(len(result["entries"]), 80)
        for entry in result["entries"]:
            self.assertEqual(set(entry), {
                "sheet", "polyp_cell", "ephyrae_cell", "source_identifier", "target_code",
                "iso_year", "iso_week", "polyps", "ephyrae",
            })
            self.assertEqual(entry["sheet"], "Semaestomeae")
            self.assertEqual(entry["iso_year"], 2026)
            week = entry["iso_week"]
            is_four = entry["target_code"] == "CCA-EOB-2.004"
            self.assertEqual(entry["target_code"], "CCA-EOB-2.004" if is_four else "CCA-EOB-2.005")
            self.assertEqual(entry["source_identifier"], "CCA-EOB-2.04" if is_four else "CCA-EOB-2.05")
            row = (121 if 10 <= week <= 31 else 123) if is_four else (127 if 10 <= week <= 31 else 125)
            column = week + 5
            self.assertEqual(entry["polyp_cell"], f"{column_letter(column)}{row}")
            self.assertEqual(entry["ephyrae_cell"], f"{column_letter(column)}{row + 1}")
            self.assertEqual(entry["polyps"], self.sheet.value(row, column))
            self.assertEqual(entry["ephyrae"], self.sheet.value(row + 1, column))
        self.assertEqual(
            [(e["target_code"], e["iso_week"]) for e in result["entries"]],
            [(code, week) for code in ("CCA-EOB-2.004", "CCA-EOB-2.005") for week in range(1, 41)],
        )

    def test_explicit_zero_is_an_entry_not_uncovered(self):
        result = self.load()
        entry = result["entries"][0]
        self.assertEqual((entry["polyps"], entry["ephyrae"]), (0, 0))
        self.assertEqual((entry["polyp_cell"], entry["ephyrae_cell"]), ("F123", "F124"))
        self.assertEqual(len(result["entries"]), 80)

    def test_empty_weeks_are_uncovered_without_counts_or_dates(self):
        result = self.load()
        self.assertEqual(len(result["uncovered"]), 24)
        for entry in result["uncovered"]:
            self.assertEqual(set(entry), {"sheet", "source_identifier", "target_code", "iso_year", "iso_week"})
            self.assertEqual(entry["iso_year"], 2026)
        self.assertEqual(
            [(e["target_code"], e["iso_week"]) for e in result["uncovered"]],
            [(code, week) for code in ("CCA-EOB-2.004", "CCA-EOB-2.005") for week in range(41, 53)],
        )

    def test_missing_pair_in_earlier_week_is_uncovered_not_zero(self):
        del self.sheet.cells[(123, 6)]
        del self.sheet.cells[(124, 6)]
        result = self.load()
        self.assertEqual(len(result["entries"]), 79)
        self.assertEqual(len(result["uncovered"]), 25)
        self.assertEqual(result["uncovered"][0]["iso_week"], 1)
        self.assertEqual(result["uncovered"][0]["target_code"], "CCA-EOB-2.004")

    def test_recap_is_excluded_with_provenance_and_no_invented_week(self):
        excluded = self.load()["excluded_2025"]
        self.assertEqual(excluded, [
            {"sheet": "Semaestomeae", "source_identifier": "CCA-EOB-2.04", "target_code": "CCA-EOB-2.004",
             "polyp_cell": "E123", "ephyrae_cell": "E124", "polyps": 901, "ephyrae": 0,
             "iso_year": 2025, "reason": "RECAP_2025_COLUMN_E"},
            {"sheet": "Semaestomeae", "source_identifier": "CCA-EOB-2.05", "target_code": "CCA-EOB-2.005",
             "polyp_cell": "E125", "ephyrae_cell": "E126", "polyps": 902, "ephyrae": 903,
             "iso_year": 2025, "reason": "RECAP_2025_COLUMN_E"},
        ])

    def test_zero_recap_is_kept_and_empty_recap_is_omitted(self):
        self.sheet.cells[(123, 5)] = 0
        result = self.load()
        self.assertEqual(len(result["excluded_2025"]), 2)
        self.assertEqual(result["excluded_2025"][0]["polyps"], 0)
        self.assertNotIn("E121", [e["polyp_cell"] for e in result["excluded_2025"]])

    def test_reviewed_week_20_uses_y_cells(self):
        entries = [e for e in self.load()["entries"] if e["iso_week"] == 20]
        self.assertEqual([e["polyp_cell"] for e in entries], ["Y121", "Y127"])

    def test_output_is_json_primitives_deterministic_and_does_not_mutate_book(self):
        before = copy.deepcopy(self.book)
        first = self.load()
        self.assertEqual(first, self.load())
        self.assertEqual(json.loads(json.dumps(first, allow_nan=False)), first)
        self.assertEqual(self.book, before)

    def test_both_partial_pair_directions_rejected_in_both_cultures_and_recap(self):
        original = copy.deepcopy(self.sheet.cells)
        for row, column in ((123, 6), (125, 6), (123, 5), (125, 5)):
            for offset in (0, 1):
                with self.subTest(row=row, column=column, missing=offset):
                    self.sheet.cells = original.copy()
                    del self.sheet.cells[(row + offset, column)]
                    with self.assertRaisesRegex(source.SourceError, "Partial pair"):
                        self.load()

    def test_invalid_counts_rejected_in_each_measurement_field_and_recap(self):
        for cell in ((123, 6), (124, 6), (125, 6), (126, 6), (123, 5), (124, 5)):
            original = self.sheet.cells[cell]
            for value in (-1, 1.5, 1.0, "0", "", " -", True, False, float("nan"), float("inf")):
                with self.subTest(cell=cell, value=value):
                    self.sheet.cells[cell] = value
                    with self.assertRaisesRegex(source.SourceError, "non-negative integer"):
                        self.load()
            self.sheet.cells[cell] = original

    def test_duplicate_week_rejected_even_for_identical_zero_pairs(self):
        for first, second in ((121, 123), (127, 125)):
            with self.subTest(first=first):
                sheet = synthetic_sheet()
                self.book.sheets = [sheet]
                for row in (first, second):
                    sheet.cells[(row, 6)] = 0
                    sheet.cells[(row + 1, 6)] = 0
                with self.assertRaisesRegex(source.SourceError, "Duplicate week 1 payload"):
                    self.load()

    def test_unexpected_identifiers_are_not_repaired_or_inherited(self):
        for cell, value in (
            ((121, 2), "CCA-EOB-2.004"), ((125, 2), "CCA-EOB-2.06"),
            ((123, 2), "CCA-EOB-2.04"), ((126, 2), "CCA-EOB-2.05"), ((121, 2), None),
        ):
            with self.subTest(cell=cell, value=value):
                self.sheet.cells = synthetic_sheet().cells
                self.sheet.cells[cell] = value
                with self.assertRaisesRegex(source.SourceError, "Unexpected identifier"):
                    self.load()

    def test_malformed_labels_context_and_headers_rejected(self):
        for cell, value in (
            ((121, 1), "Cyanea lamarckii"), ((125, 1), "Other species"),
            ((122, 1), "Cyanea capillata"), ((121, 3), 15), ((123, 3), None),
            ((126, 3), 10), ((127, 3), 15.0), ((121, 4), "Nb éphyrules"),
            ((124, 4), "Nb polypes"), ((128, 4), None), ((1, 5), 2026),
            ((2, 5), 53), ((2, 1), "Species"), ((2, 6), None), ((2, 6), True),
            ((2, 7), " -"), ((2, 25), 20), ((2, 57), 53), ((2, 58), 53),
            ((121, 58), 0),
        ):
            with self.subTest(cell=cell, value=value):
                self.sheet.cells = synthetic_sheet().cells
                self.sheet.cells[cell] = value
                with self.assertRaises(source.SourceError):
                    self.load()

    def test_duplicate_and_misplaced_week_headers_rejected(self):
        self.sheet.cells[(2, 7)] = 1
        with self.assertRaisesRegex(source.SourceError, "Duplicate week 1 header"):
            self.load()
        self.sheet.cells[(2, 6)], self.sheet.cells[(2, 7)] = 2, 1
        with self.assertRaisesRegex(source.SourceError, "Misplaced week header"):
            self.load()

    def test_missing_duplicate_and_hidden_target_sheet_rejected(self):
        for sheets in ([], [self.sheet, self.sheet], [Sheet(name="Other", state="visible")]):
            with self.subTest(sheets=len(sheets)):
                self.book.sheets = sheets
                with self.assertRaisesRegex(source.SourceError, "exactly one"):
                    self.load()
        self.book.sheets = [self.sheet]
        self.sheet.state = "hidden"
        with self.assertRaisesRegex(source.SourceError, "visible sheet"):
            self.load()

    def test_hidden_merged_source_values_rejected_including_zero(self):
        for cell in ((121, 6), (128, 57), (121, 2), (2, 25)):
            with self.subTest(cell=cell):
                self.sheet.ignored_merged_values = {cell: 0}
                with self.assertRaisesRegex(source.SourceError, "Hidden merged value"):
                    self.load()

    def test_other_cultures_and_sheets_are_outside_scope(self):
        self.sheet.cells[(129, 2)] = "OTHER-BOX"
        self.sheet.cells[(129, 6)] = -1
        self.book.sheets.append(Sheet(name="Other", state="visible", cells={(1, 1): "ignored"}))
        self.assertEqual(len(self.load()["entries"]), 80)

    def test_actual_reader_on_synthetic_xlsx_without_source_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.xlsx"
            write_xlsx(path, self.sheet)
            before = path.read_bytes()
            digest = file_sha256(path)
            with mock.patch.object(source, "V2_SHA256", digest):
                result = source.load_source(path)
            self.assertEqual(result["sha256"], digest)
            self.assertEqual(len(result["entries"]), 80)
            self.assertEqual(result["entries"][0]["polyps"], 0)
            self.assertEqual(len(result["uncovered"]), 24)
            self.assertEqual(path.read_bytes(), before)


@unittest.skipUnless(REAL_WORKBOOK.is_file(), "Reviewed v2 workbook is not available locally.")
class RealCcaEobSourceTests(unittest.TestCase):
    def test_exact_real_source_cells_sha_coverage_and_reviewed_old_mapping(self):
        before = file_sha256(REAL_WORKBOOK)
        self.assertEqual(before, source.V2_SHA256)
        result = source.load_source(REAL_WORKBOOK)
        book = read_verified_workbook(REAL_WORKBOOK, source.V2_SHA256)
        sheet = next(sheet for sheet in book.sheets if sheet.name == "Semaestomeae")
        self.assertEqual(result["sha256"], before)
        self.assertEqual(book.sha256, before)
        expected = []
        for identifier, target in (("CCA-EOB-2.04", "CCA-EOB-2.004"), ("CCA-EOB-2.05", "CCA-EOB-2.005")):
            for week in range(1, 41):
                if target.endswith("004"):
                    row = 121 if 10 <= week <= 31 else 123
                else:
                    row = 127 if 10 <= week <= 31 else 125
                column = week + 5
                expected.append({
                    "sheet": "Semaestomeae", "source_identifier": identifier, "target_code": target,
                    "polyp_cell": f"{column_letter(column)}{row}",
                    "ephyrae_cell": f"{column_letter(column)}{row + 1}",
                    "iso_year": 2026, "iso_week": week,
                    "polyps": sheet.value(row, column), "ephyrae": sheet.value(row + 1, column),
                })
        self.assertEqual(result["entries"], expected)
        self.assertEqual(result["uncovered"], [
            {"sheet": "Semaestomeae", "source_identifier": identifier, "target_code": target,
             "iso_year": 2026, "iso_week": week}
            for identifier, target in (("CCA-EOB-2.04", "CCA-EOB-2.004"), ("CCA-EOB-2.05", "CCA-EOB-2.005"))
            for week in range(41, 53)
        ])
        for row in range(121, 129):
            for column in range(46, 58):
                self.assertIsNone(sheet.value(row, column))
        excluded = result["excluded_2025"]
        self.assertEqual([entry["polyp_cell"] for entry in excluded], ["E123", "E125"])
        for entry, row in zip(excluded, (123, 125)):
            self.assertEqual((entry["polyps"], entry["ephyrae"]), (sheet.value(row, 5), sheet.value(row + 1, 5)))
            self.assertEqual(entry["ephyrae_cell"], f"E{row + 1}")
            self.assertEqual(entry["iso_year"], 2025)
            self.assertNotIn("iso_week", entry)

        # Reuse the already-versioned manifest; never embed real counts in tests.
        manifest_path = Path(source.__file__).with_name("manifest_2026.json")
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
        old_by_key = {(e["box"], e["iso_week"]): e for e in old["measurements"]}
        for entry in result["entries"]:
            if 33 <= entry["iso_week"] <= 40:
                old_code = "CCA-EOB-2.006" if entry["target_code"].endswith("004") else "CCA-EOB-2.005"
                prior = old_by_key[(old_code, entry["iso_week"])]
                self.assertEqual((entry["polyps"], entry["ephyrae"]), (prior["polyps"], prior["ephyrae"]))
        self.assertEqual(file_sha256(REAL_WORKBOOK), before)
