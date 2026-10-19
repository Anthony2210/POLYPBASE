"""Synthetic XLSX and pure pipeline tests; no settings or database required."""

from copy import deepcopy
import hashlib
import io

from pathlib import Path

import unittest
from unittest.mock import patch
import zipfile

from .historical_temperature_dry_run import build_report, compare_2026_manifest, main
from .historical_temperature_plan import plan
from .historical_temperature_source import (
    SOURCES, Cell, Sheet, SourceError, _read_sheets, canonical_box,
    canonical_json, extract_sheet, extract_sources, fingerprint, numeric_state, read_source,
)


FILENAME = "Suivi_2026_actualisé_v2.xlsx"
SHA = SOURCES[FILENAME][1]


def sheet_fixture(*, year=2026, temperature="15.000", polyps="0", ephyrae="0"):
    first = 6 if year >= 2025 else 5
    cells = {(2, 1): Cell("Espèce", "s"), (2, 2): Cell("N° boîte", "s"),
             (2, 3): Cell("Température (°C)", "s"),
             (3, 1): Cell("Synthetic species", "s"), (3, 2): Cell("ABC-DEF-1.01", "s"),
             (3, 3): Cell(temperature, "n"),
             (3, 4): Cell("Nb polypes", "s"), (4, 4): Cell("Nb éphyrules", "s"),
             (2, first): Cell("1", "n"), (2, first + 1): Cell("2", "n"),
             (3, first): Cell(polyps, "n"), (4, first): Cell(ephyrae, "n")}
    if year >= 2025:
        cells[(1, 5)] = Cell(str(year - 1), "n")
    return Sheet("Synthetic", cells=cells, merges=["B3:B4", "C3:C4"])


def extract(sheet=None, *, year=2026):
    return extract_sheet(sheet or sheet_fixture(year=year), filename=FILENAME, year=year, sha256=SHA)


def workbook_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Synthetic" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="3"><c r="C3"><v>15.123456789012345678900</v></c><c r="F3"><v>0</v></c><c r="G3" t="b"><v>0</v></c><c r="H3"><f>1+1</f><v>2</v></c></row><row r="4"><c r="C4"><v>999</v></c></row></sheetData><mergeCells><mergeCell ref="C3:C4"/></mergeCells></worksheet>')
    return buffer.getvalue()


class HistoricalTemperatureSourceTests(unittest.TestCase):
    def test_exact_allowlist_and_v2_hash(self):
        self.assertEqual(set(SOURCES), {f"Suivi_{year}.xlsx" for year in range(2020, 2026)} | {FILENAME})
        self.assertEqual(SHA, "0d5cd16eee88845ea51fbd4f122be15c12e5896b5237458573fc432ebda8b160")
        self.assertEqual(len({digest for _, digest in SOURCES.values()}), 7)

    def test_excluded_filenames_are_refused_before_reading(self):
        for name in ("Suivi_2019.xlsx", "Suivi_2026.xlsx", "Suivi_2026_actualisé.xlsx", "Suivi_2026_actualise_v2.xlsx"):
            with self.subTest(name=name), patch.object(Path, "read_bytes", side_effect=AssertionError("read forbidden")):
                with self.assertRaisesRegex(SourceError, "not allowlisted"):
                    read_source(Path(name))

    def test_changed_source_is_refused_before_parsing(self):
        with patch.object(Path, "read_bytes", return_value=b"not the authoritative file"), patch(
            "apps.measurements.historical_temperature_source._read_sheets", side_effect=AssertionError("parse forbidden"),
        ):
            with self.assertRaisesRegex(SourceError, "SHA-256 mismatch"):
                read_source(Path(FILENAME))

    def test_only_verified_bytes_are_parsed_without_second_read(self):
        content = workbook_bytes()
        with patch.dict(SOURCES, {FILENAME: (2026, hashlib.sha256(content).hexdigest())}), patch.object(
            Path, "read_bytes", return_value=content,
        ) as reader:
            year, digest, sheets = read_source(Path(FILENAME))
        self.assertEqual(year, 2026)
        self.assertEqual(digest, hashlib.sha256(content).hexdigest())
        self.assertEqual(sheets[0].name, "Synthetic")
        reader.assert_called_once()

    def test_xml_numeric_precision_and_merged_anchor_preserved(self):
        sheet = _read_sheets(workbook_bytes())[0]
        self.assertEqual(sheet.cell(3, 3).value, "15.123456789012345678900")
        self.assertEqual(sheet.merged_range(3, 3), "C3:C4")
        self.assertEqual(sheet.cell(4, 3).kind, "missing")
        self.assertEqual(sheet.ignored[0]["value"], "999")
        self.assertEqual(numeric_state(sheet.cell(3, 7)), "malformed")
        self.assertEqual(numeric_state(sheet.cell(3, 8)), "malformed")

    def test_value_and_full_provenance_survive_without_float(self):
        result = extract(sheet_fixture(temperature="15.123456789012345678900"))
        candidate = result["candidates"][0]
        evidence = candidate["evidence"]
        self.assertEqual(candidate["temperature_c"], "15.123456789012345678900")
        self.assertEqual(evidence, {
            "source_file": FILENAME, "source_sha256": SHA, "sheet": "Synthetic",
            "temperature_cell": "C3", "temperature_merged_range": "C3:C4",
            "source_value": "15.123456789012345678900", "source_box": "ABC-DEF-1.01",
            "polyp_cell": "F3", "polyp_value": "0", "ephyrae_cell": "F4", "ephyrae_value": "0",
            "week_cell": "F2", "week_value": "1", "source_iso_year": 2026, "source_iso_week": 1,
        })
        self.assertEqual(candidate["candidate_id"], fingerprint(evidence))
        self.assertNotIn("measured_on", candidate)
        self.assertNotIn("measured_at", candidate)

    def test_zero_is_measurement_empty_week_is_not(self):
        result = extract(sheet_fixture(temperature="0.000"))
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["polyps"], 0)
        self.assertEqual(result["candidates"][0]["temperature_c"], "0.000")
        self.assertEqual(result["blocks"][0]["empty_weeks"], 1)

    def test_missing_and_malformed_temperatures_are_distinct(self):
        for value, reason in (("", "TEMPERATURE_MISSING"), ("not measured", "TEMPERATURE_MALFORMED")):
            with self.subTest(value=value):
                result = extract(sheet_fixture(temperature=value))
                self.assertEqual(result["candidates"], [])
                self.assertIn(reason, [e["reason"] for e in result["exclusions"]])

    def test_absent_temperature_cell_is_not_inherited(self):
        sheet = sheet_fixture()
        del sheet.cells[(3, 3)]
        result = extract(sheet)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["blocks"][0]["temperature_state"], "missing")

    def test_empty_blocks_are_preserved_without_observations(self):
        result = extract(sheet_fixture(polyps="", ephyrae=""))
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(result["blocks"]), 1)
        self.assertEqual(result["exclusions"][0]["reason"], "NO_CURRENT_BIOLOGICAL_OCCURRENCES")

    def test_two_biological_rows_create_one_candidate(self):
        self.assertEqual(len(extract()["candidates"]), 1)

    def test_repeated_temperature_across_observed_weeks_is_allowed(self):
        sheet = sheet_fixture()
        sheet.cells[(3, 7)] = Cell("0", "n")
        sheet.cells[(4, 7)] = Cell("0", "n")
        candidates = extract(sheet)["candidates"]
        self.assertEqual([c["iso_week"] for c in candidates], [1, 2])
        self.assertEqual([c["temperature_c"] for c in candidates], ["15.000", "15.000"])

    def test_retrospective_columns_never_become_current_weeks(self):
        for year in (2025, 2026):
            sheet = sheet_fixture(year=year)
            sheet.cells[(3, 5)] = Cell("9", "n")
            sheet.cells[(4, 5)] = Cell("0", "n")
            result = extract(sheet, year=year)
            self.assertEqual(len(result["candidates"]), 1)
            self.assertEqual(result["exclusions"][0]["reason"], "RETROSPECTIVE_COLUMN_NO_WEEK")

    def test_pre_2025_column_e_is_a_real_week(self):
        candidate = extract(sheet_fixture(year=2024), year=2024)["candidates"][0]
        self.assertEqual(candidate["evidence"]["polyp_cell"], "E3")

    def test_duplicate_week_headers_block_even_nonoverlapping_biology(self):
        sheet = sheet_fixture()
        sheet.cells[(2, 7)] = Cell("1", "n")
        result = extract(sheet)
        self.assertEqual(result["candidates"][0]["biological_state"], "malformed")
        self.assertEqual(result["header_issues"][0]["reason"], "DUPLICATE_WEEK_HEADER")
        self.assertEqual(plan(result["candidates"], organization_id=7)["associations"][0]["status"], "BLOCKED")

    def test_2026_dash_is_not_silently_week_20(self):
        sheet = sheet_fixture()
        sheet.cells[(2, 6)] = Cell(" -", "s")
        result = extract(sheet)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["exclusions"][0]["reason"], "UNRESOLVED_WEEK_HEADER")
        self.assertEqual(result["exclusions"][0]["week_value"], " -")

    def test_week_53_must_exist_in_source_iso_year(self):
        sheet = sheet_fixture(year=2021)
        sheet.cells[(2, 5)] = Cell("53", "n")
        self.assertEqual(extract(sheet, year=2021)["candidates"], [])

    def test_unreviewed_row_orientation_is_blocked_not_repaired(self):
        sheet = sheet_fixture()
        sheet.cells[(3, 4)], sheet.cells[(4, 4)] = sheet.cells[(4, 4)], sheet.cells[(3, 4)]
        candidate = extract(sheet)["candidates"][0]
        self.assertEqual(candidate["biological_state"], "malformed")
        self.assertEqual(candidate["evidence"]["polyp_cell"], "F3")

    def test_partial_malformed_and_boolean_biology_are_not_zero(self):
        for cell, state in ((Cell(), "partial"), (Cell("?", "s"), "malformed"), (Cell("0", "b"), "malformed")):
            sheet = sheet_fixture()
            sheet.cells[(4, 6)] = cell
            candidate = extract(sheet)["candidates"][0]
            self.assertIsNone(candidate["ephyrae"])
            self.assertEqual(candidate["biological_state"], state)

    def test_new_species_without_box_clears_old_identity(self):
        sheet = sheet_fixture()
        sheet.cells.update({(5, 1): Cell("Different species", "s"), (5, 3): Cell("20", "n"),
                            (5, 4): Cell("Nb polypes", "s"), (6, 4): Cell("Nb éphyrules", "s"),
                            (5, 6): Cell("0", "n"), (6, 6): Cell("0", "n")})
        candidate = extract(sheet)["candidates"][1]
        self.assertEqual(candidate["source_box"], "")
        self.assertIsNone(candidate["canonical_box"])

    def test_no_identity_aliases_are_inherited_from_older_2026_sources(self):
        for label in ("ASP-EVA1.01", "CLC-JKA1.10", "ROC-1.01", "Station n°1"):
            self.assertIsNone(canonical_box(label))
        self.assertEqual(canonical_box("COR-JIS-1.01"), "COR-JIS-1.001")
        self.assertEqual(canonical_box("CLA-JKA-1.01"), "CLA-JKA-1.001")

    def test_extraction_reads_exactly_seven_sources(self):
        with patch("apps.measurements.historical_temperature_source.read_source", return_value=(2026, SHA, [])) as reader:
            extract_sources(Path("synthetic"))
        self.assertEqual([call.args[0].name for call in reader.call_args_list], list(SOURCES))

    def test_source_structure_is_not_mutated_and_results_are_deterministic(self):
        sheet = sheet_fixture()
        original = deepcopy(sheet)
        self.assertEqual(canonical_json(extract(sheet)), canonical_json(extract(sheet)))
        self.assertEqual(sheet, original)

    def test_report_is_deterministic_and_no_baseline_means_no_db_proposal(self):
        extraction = {"sources": [], **extract(), "ignored_merged_values": []}
        with patch("apps.measurements.historical_temperature_dry_run.extract_sources", return_value=extraction):
            first = build_report("unused", organization_id=7)
            second = build_report("unused", organization_id=7)
        self.assertEqual(canonical_json(first), canonical_json(second))
        self.assertFalse(first["database_access"])
        self.assertEqual(first["database_writes"], 0)
        self.assertEqual(first["association_plan"]["proposed_changes"], [])
        self.assertEqual(first["report_fingerprint"], fingerprint({k: v for k, v in first.items() if k != "report_fingerprint"}))

    def test_cli_refuses_report_outside_ignored_private_directory_before_reading_sources(self):
        with patch("apps.measurements.historical_temperature_dry_run.build_report", side_effect=AssertionError("source access forbidden")):
            with self.assertRaises(SystemExit) as exc:
                main(["--raw-directory", "synthetic", "--organization-id", "7", "--output", "tracked-report.json"])
        self.assertEqual(exc.exception.code, 2)

    def test_report_blocks_manifest_identity_disagreement_despite_matching_db_counts(self):
        extraction = {"sources": [], **extract(), "ignored_merged_values": []}
        candidate = extraction["candidates"][0]
        manifest = {"source": {"sha256": "14befa67441f289153576b5c53de462eaa05b4218df6d5439a44ee432f62cb67"},
                    "boxes": [], "measurements": [{"sheet": "Synthetic", "polyp_cell": "F3", "ephyrae_cell": "F4",
                        "box": "XYZ-DEF-1.001", "iso_week": 1, "polyps": 0, "ephyrae": 0}]}
        manifest["fingerprint"] = fingerprint(manifest)
        baseline = {"organization_id": 7, "boxes": [{"id": 11, "organization_id": 7, "global_code": "ABC-DEF-1.001"}],
                    "biological_measurements": [{"id": 21, "box_id": 11, "week_start": "2025-12-29",
                                                 "polyp_count": 0, "ephyrae_count": 0}], "historical_temperatures": []}
        with patch("apps.measurements.historical_temperature_dry_run.extract_sources", return_value=extraction):
            report = build_report("unused", organization_id=7, baseline=baseline, manifest=manifest)
        self.assertFalse(report["summary"]["ready"])
        self.assertEqual(report["association_plan"]["proposed_changes"], [])
        self.assertIn("V2_IDENTITY_DIFFERS_REVIEW_REQUIRED", report["association_plan"]["associations"][0]["issues"])
        self.assertEqual(candidate["canonical_box"], "ABC-DEF-1.001")

    def test_manifest_comparison_is_not_a_db_resolution_or_identity_mapping(self):
        candidate = extract()["candidates"][0]
        manifest = {"source": {"sha256": "14befa67441f289153576b5c53de462eaa05b4218df6d5439a44ee432f62cb67"},
                    "boxes": [], "measurements": [{"sheet": "Synthetic", "polyp_cell": "F3", "ephyrae_cell": "F4",
                        "box": "XYZ-DEF-1.001", "iso_week": 1, "polyps": 0, "ephyrae": 0}]}
        manifest["fingerprint"] = fingerprint(manifest)
        result = compare_2026_manifest({"candidates": [candidate], "blocks": []}, manifest)
        self.assertEqual(result["comparisons"][0]["issues"], ["IDENTITY_DIFFERS"])
        self.assertFalse(result["corrections_transferred"])
        self.assertEqual(candidate["canonical_box"], "ABC-DEF-1.001")
        manifest["measurements"][0]["box"] = "ABC-DEF-1.001"
        with self.assertRaises(SourceError):
            compare_2026_manifest({"candidates": [], "blocks": []}, manifest)
