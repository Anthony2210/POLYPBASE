"""Deterministic workbook-to-manifest preparation for the 2026 import.

Layout-level behaviour is tested on small synthetic workbooks (the reviewed
decisions are re-pointed at synthetic coordinates). The real workbook is only
read when it is available next to this repository.
"""

import os
import tempfile
import zipfile
from pathlib import Path
from unittest import SkipTest, mock
from xml.sax.saxutils import escape

from django.test import SimpleTestCase

from .historical_2026 import decisions, manifest as manifest_module
from .historical_2026.source import SourceError, build_manifest, canonical_box_code
from .historical_2026.workbook import column_letter, file_sha256, read_workbook

REAL_WORKBOOK = Path(
    os.environ.get(
        "POLYPBASE_2026_WORKBOOK",
        Path.home()
        / "POLYPBASE-ANALYSES"
        / "data"
        / "raw"
        / decisions.SOURCE_FILENAME,
    )
)


def write_xlsx(path, sheets, *, merges=(), hidden=()):
    """Write a minimal XLSX. ``sheets`` maps name -> {(row, col): value}."""
    names = list(sheets)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'/>")
        entries = "".join(
            f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"'
            f'{" state=\"hidden\"" if name in hidden else ""}/>'
            for i, name in enumerate(names, 1)
        )
        archive.writestr(
            "xl/workbook.xml",
            "<workbook xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main' "
            "xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'>"
            f"<sheets>{entries}</sheets></workbook>",
        )
        rels = "".join(
            f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(names) + 1)
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            f"<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>{rels}</Relationships>",
        )
        for i, name in enumerate(names, 1):
            rows = {}
            for (row, col), value in sheets[name].items():
                rows.setdefault(row, []).append((col, value))
            body = ""
            for row in sorted(rows):
                cells = ""
                for col, value in sorted(rows[row]):
                    ref = f"{column_letter(col)}{row}"
                    if isinstance(value, str):
                        cells += f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'
                    else:
                        cells += f'<c r="{ref}"><v>{value}</v></c>'
                body += f'<row r="{row}">{cells}</row>'
            merge_xml = ""
            if merges:
                merge_xml = "<mergeCells>" + "".join(f'<mergeCell ref="{ref}"/>' for ref in merges) + "</mergeCells>"
            archive.writestr(
                f"xl/worksheets/sheet{i}.xml",
                "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
                f"<sheetData>{body}</sheetData>{merge_xml}</worksheet>",
            )


def block(rows, first_row, *, species=None, box=None, temp=None, polyp, ephyrae, polyp_label=None, ephyrae_label=None):
    """Return cells for one polyp/ephyrae pair. polyp/ephyrae map column -> value."""
    cells = {}
    if species is not None:
        cells[(first_row, 1)] = species
    if box is not None:
        cells[(first_row, 2)] = box
    if temp is not None:
        cells[(first_row, 3)] = temp
    cells[(first_row, 4)] = polyp_label or decisions.POLYP_LABEL
    cells[(first_row + 1, 4)] = ephyrae_label or decisions.EPHYRAE_LABEL
    for col, value in polyp.items():
        cells[(first_row, col)] = value
    for col, value in ephyrae.items():
        cells[(first_row + 1, col)] = value
    rows.update(cells)


def header(cells, *, weeks=None):
    cells[(1, 5)] = 2025
    cells[(2, 1)], cells[(2, 2)], cells[(2, 3)] = "Espèce", "N° boîte", "Température (°C)"
    for col, week in (weeks or {6: 1, 7: 2, 8: " -"}).items():
        cells[(2, col)] = week


SYNTHETIC_DECISIONS = {
    "REVIEWED_WEEK_HEADERS": {("S", 8): {"raw": " -", "iso_week": 3}},
    "ORIENTATION_CORRECTIONS": (
        {"sheet": "S", "source_box": "TTH-AVI-1.09", "polyp_row": 13, "ephyrae_row": 14},
    ),
    "EXCLUDED_BLOCKS": (
        {
            "reason": "CCO_ISOLATED_10C_BLOCK",
            "sheet": "S",
            "source_box": "CCO-JKA-1.04",
            "polyp_row": 11,
            "ephyrae_row": 12,
            "block_temperature_c": 10,
            "kept_block_temperature_c": 15,
        },
    ),
    "MEASUREMENT_CORRECTIONS": (
        {
            "box": "LDR-JAP-1.001",
            "iso_year": 2026,
            "iso_week": 1,
            "expected_current": {"polyps": 80, "ephyrae": 8},
            "target": {"polyps": 80, "ephyrae": 0},
            "reason": "TEST_LDR",
        },
    ),
}


def synthetic_sheet(*, extra=None):
    cells = {}
    header(cells)
    # Plain block with an explicit zero, a recap value and a recovered week.
    block(cells, 3, species="Species one", box="AAA-BBB-1.01", temp=15,
          polyp={5: 9, 6: 5, 7: 6, 8: 7}, ephyrae={5: 9, 6: 0, 7: 1, 8: 2})
    # The two approved exact malformed identifiers.
    block(cells, 5, species="Species two", box="ASP-EVA1.01", temp=15, polyp={6: 1}, ephyrae={6: 1})
    block(cells, 7, species="Species three", box="CLA-JKA1.10", temp=15, polyp={6: 2}, ephyrae={6: 0})
    # CCO: continuous 15 block kept, isolated 10 block excluded.
    block(cells, 9, species="Species four", box="CCO-JKA-1.04", temp=15, polyp={6: 40}, ephyrae={6: 100})
    block(cells, 11, box="CCO-JKA-1.04", temp=10, polyp={6: 7}, ephyrae={6: 0})
    # TTH: labels inverted in the reviewed rows only.
    block(cells, 13, species="Species five", box="TTH-AVI-1.09", temp=25, polyp={6: 300}, ephyrae={6: 20},
          polyp_label=decisions.EPHYRAE_LABEL, ephyrae_label=decisions.POLYP_LABEL)
    # Explicit correction target.
    block(cells, 15, species="Species six", box="LDR-JAP-1.01", temp=15, polyp={6: 80}, ephyrae={6: 0})
    cells.update(extra or {})
    return {"S": cells}


class WorkbookPreparationTests(SimpleTestCase):
    def build(self, sheets, **kwargs):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "synthetic.xlsx"
        write_xlsx(path, sheets, **kwargs)
        overrides = dict(SYNTHETIC_DECISIONS, EXPECTED_WORKBOOK_SHA256=file_sha256(path))
        with mock.patch.multiple(decisions, **overrides):
            return build_manifest(path), path

    def measurements(self, manifest):
        return {(m["box"], m["iso_week"]): m for m in manifest["measurements"]}

    def test_expected_workbook_sha_is_accepted(self):
        manifest, path = self.build(synthetic_sheet())
        self.assertEqual(manifest["source"]["sha256"], file_sha256(path))

    def test_wrong_workbook_sha_is_rejected(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "other.xlsx"
        write_xlsx(path, synthetic_sheet())
        with mock.patch.multiple(decisions, **SYNTHETIC_DECISIONS):
            with self.assertRaisesMessage(SourceError, "Unexpected workbook SHA-256"):
                build_manifest(path)

    def test_column_e_recap_is_excluded_not_imported(self):
        manifest, _ = self.build(synthetic_sheet())
        self.assertNotIn(("AAA-BBB-1.001", 0), self.measurements(manifest))
        recap = [e for e in manifest["excluded"] if e["reason"] == "RECAP_2025_COLUMN_E"]
        self.assertEqual([(e["polyp_cell"], e["polyp_value"]) for e in recap], [("E3", 9)])
        self.assertEqual(len([m for m in manifest["measurements"] if m["box"] == "AAA-BBB-1.001"]), 3)

    def test_w20_style_recovery_only_for_the_reviewed_column(self):
        manifest, _ = self.build(synthetic_sheet())
        recovered = self.measurements(manifest)[("AAA-BBB-1.001", 3)]
        self.assertEqual((recovered["polyps"], recovered["ephyrae"]), (7, 2))
        self.assertEqual(recovered["corrections"][0]["code"], "REVIEWED_WEEK_HEADER")
        self.assertEqual(manifest["counts"]["corrections_applied"]["REVIEWED_WEEK_HEADER"], 1)

    def test_other_unusual_week_header_is_not_recovered(self):
        sheets = synthetic_sheet()
        sheets["S"][(2, 9)] = " -"
        sheets["S"][(3, 9)] = 1
        sheets["S"][(4, 9)] = 1
        with self.assertRaisesMessage(SourceError, "Unexpected week header"):
            self.build(sheets)

    def test_exact_malformed_aliases_are_applied(self):
        manifest, _ = self.build(synthetic_sheet())
        by_box = self.measurements(manifest)
        self.assertEqual(by_box[("ASP-EVA-1.001", 1)]["source_box"], "ASP-EVA1.01")
        self.assertEqual(by_box[("CLA-JKA-1.010", 1)]["corrections"][0]["to"], "CLA-JKA-1.10")
        self.assertEqual(
            {box["code"]: box["local_code"] for box in manifest["boxes"]}["ASP-EVA-1.001"], "ASP-EVA-1.01"
        )

    def test_unrelated_malformed_identifier_is_not_repaired(self):
        sheets = synthetic_sheet()
        block(sheets["S"], 17, species="Species seven", box="XYZ-ABC1.01", temp=15, polyp={6: 1}, ephyrae={6: 1})
        with self.assertRaisesMessage(SourceError, "Unexpected box identifier: 'XYZ-ABC1.01'"):
            self.build(sheets)
        self.assertEqual(canonical_box_code("AAU-NBE-1.01")[0], "AAU-NBE-1.001")

    def test_cco_continuous_15_block_kept_and_10_block_excluded_visibly(self):
        manifest, _ = self.build(synthetic_sheet())
        kept = self.measurements(manifest)[("CCO-JKA-1.004", 1)]
        self.assertEqual((kept["polyps"], kept["ephyrae"], kept["block_temperature_c"]), (40, 100, 15))
        rejected = [e for e in manifest["excluded"] if e["reason"] == "CCO_ISOLATED_10C_BLOCK"]
        self.assertEqual([(e["polyp_cell"], e["polyp_value"], e["iso_week"]) for e in rejected], [("F11", 7, 1)])
        self.assertEqual(manifest["counts"]["excluded"]["CCO_ISOLATED_10C_BLOCK"], 1)
        self.assertEqual({b["code"] for b in manifest["boxes"]} & {"TEST-CCO-1.001"}, set())

    def test_cco_exclusion_requires_the_reviewed_temperature(self):
        sheets = synthetic_sheet()
        sheets["S"][(11, 3)] = 12
        with self.assertRaisesMessage(SourceError, "Excluded block temperature changed"):
            self.build(sheets)

    def test_tth_orientation_corrected_only_for_reviewed_rows(self):
        manifest, _ = self.build(synthetic_sheet())
        tth = self.measurements(manifest)[("TTH-AVI-1.009", 1)]
        self.assertEqual((tth["polyps"], tth["ephyrae"]), (300, 20))
        self.assertEqual((tth["polyp_cell"], tth["ephyrae_cell"]), ("F13", "F14"))
        self.assertEqual(tth["corrections"][0]["code"], "ANTHONY_TTH_LABEL_INVERSION")

    def test_inverted_labels_elsewhere_are_rejected(self):
        sheets = synthetic_sheet()
        sheets["S"][(3, 4)], sheets["S"][(4, 4)] = decisions.EPHYRAE_LABEL, decisions.POLYP_LABEL
        with self.assertRaisesMessage(SourceError, "Unexpected row labels"):
            self.build(sheets)

    def test_explicit_zero_is_preserved_and_blank_is_not_zero(self):
        manifest, _ = self.build(synthetic_sheet())
        self.assertEqual(self.measurements(manifest)[("AAA-BBB-1.001", 1)]["ephyrae"], 0)
        self.assertEqual(self.measurements(manifest)[("AAA-BBB-1.001", 1)]["ephyrae_value"], 0)
        sheets = synthetic_sheet()
        del sheets["S"][(4, 7)]  # blank ephyrae cell opposite a polyp count
        with self.assertRaisesMessage(SourceError, "Partial observation"):
            self.build(sheets)
        sheets = synthetic_sheet()
        del sheets["S"][(3, 7)], sheets["S"][(4, 7)]  # both blank: no observation at all
        manifest, _ = self.build(sheets)
        self.assertNotIn(("AAA-BBB-1.001", 2), self.measurements(manifest))

    def test_strobila_is_null_and_provenance_is_kept(self):
        manifest, _ = self.build(synthetic_sheet())
        for item in manifest["measurements"]:
            self.assertIsNone(item["strobila"])
        item = self.measurements(manifest)[("AAA-BBB-1.001", 2)]
        self.assertEqual(
            (item["sheet"], item["source_box"], item["polyp_cell"], item["polyp_value"],
             item["ephyrae_cell"], item["ephyrae_value"], item["year"], item["iso_week"],
             item["measured_on"]),
            ("S", "AAA-BBB-1.01", "G3", 6, "G4", 1, 2026, 2, "2026-01-05"),
        )

    def test_explicit_correction_row_carries_the_expected_old_state(self):
        manifest, _ = self.build(synthetic_sheet())
        ldr = self.measurements(manifest)[("LDR-JAP-1.001", 1)]
        self.assertEqual(ldr["operation"], "EXPLICIT_CORRECTION")
        self.assertEqual(ldr["expected_current"], {"polyps": 80, "ephyrae": 8})
        self.assertEqual((ldr["polyps"], ldr["ephyrae"]), (80, 0))

    def test_correction_target_must_match_the_workbook(self):
        sheets = synthetic_sheet()
        sheets["S"][(16, 6)] = 5
        with self.assertRaisesMessage(SourceError, "Correction target"):
            self.build(sheets)

    def test_duplicate_week_for_a_box_without_a_decision_is_rejected(self):
        sheets = synthetic_sheet()
        block(sheets["S"], 17, species="Species one", box="AAA-BBB-1.01", temp=20, polyp={6: 1}, ephyrae={6: 1})
        with self.assertRaisesMessage(SourceError, "Duplicate source observation"):
            self.build(sheets)

    def test_hidden_values_in_merged_cells_are_ignored_not_imported(self):
        sheets = synthetic_sheet()
        sheets["S"][(4, 2)] = 3  # stale value under the merged anchor B3:B4
        manifest, _ = self.build(sheets, merges=["B3:B4"])
        self.assertEqual(manifest["counts"]["ignored_merged_non_anchor_values"], 1)

    def test_manifest_is_deterministic_and_fingerprinted(self):
        first, _ = self.build(synthetic_sheet())
        second, _ = self.build(synthetic_sheet())
        sealed = manifest_module.with_fingerprint(first)
        self.assertEqual(sealed["fingerprint"], manifest_module.with_fingerprint(second)["fingerprint"])
        self.assertEqual(manifest_module.dumps(sealed), manifest_module.dumps(manifest_module.with_fingerprint(second)))
        altered = dict(sealed, counts=dict(sealed["counts"], observations=0))
        self.assertNotEqual(manifest_module.compute_fingerprint(altered), sealed["fingerprint"])

    def test_load_rejects_an_altered_or_foreign_manifest(self):
        manifest, _ = self.build(synthetic_sheet())
        sealed = manifest_module.with_fingerprint(manifest)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "manifest.json"
        # Synthetic source hash differs from the pinned workbook.
        manifest_module.write_manifest(sealed, path)
        with self.assertRaisesMessage(manifest_module.ManifestError, "expected workbook"):
            manifest_module.load_manifest(path)
        tampered = manifest_module.dumps(sealed).replace('"observations":', '"observationz":', 1)
        path.write_text(tampered, encoding="utf-8")
        with self.assertRaisesMessage(manifest_module.ManifestError, "fingerprint"):
            manifest_module.load_manifest(path)


class CommittedManifestTests(SimpleTestCase):
    def test_committed_manifest_is_internally_consistent(self):
        manifest = manifest_module.load_manifest()
        self.assertEqual(manifest["source"]["sha256"], decisions.EXPECTED_WORKBOOK_SHA256)
        self.assertEqual(manifest["counts"]["observations"], len(manifest["measurements"]))
        self.assertEqual(len({(m["box"], m["iso_week"]) for m in manifest["measurements"]}), len(manifest["measurements"]))
        self.assertTrue(all(m["strobila"] is None for m in manifest["measurements"]))

    def test_committed_manifest_encodes_the_reviewed_special_cases(self):
        manifest = manifest_module.load_manifest()
        counts = manifest["counts"]
        self.assertEqual(counts["excluded"]["CCO_ISOLATED_10C_BLOCK"], 6)
        self.assertEqual(counts["corrections_applied"]["REVIEWED_WEEK_HEADER"], 39)
        self.assertEqual(counts["corrections_applied"]["ANTHONY_TTH_LABEL_INVERSION"], 32)
        by_key = {(m["box"], m["iso_week"]): m for m in manifest["measurements"]}
        cco = [by_key[("CCO-JKA-1.004", week)] for week in range(1, 7)]
        self.assertEqual(
            [(m["polyps"], m["ephyrae"]) for m in cco],
            [(40, 100), (41, 100), (35, 50), (36, 50), (40, 80), (42, 50)],
        )
        self.assertEqual(counts["alias_observations"], {"ASP-EVA1.01": 40, "CLA-JKA1.10": 22})
        self.assertEqual(counts["corrections_applied"]["ANTHONY_EXACT_IDENTIFIER_ALIAS"], 62)
        cla_w20 = by_key[("CLA-JKA-1.010", 20)]  # the 22nd CLA row is the reviewed W20 column
        self.assertEqual((cla_w20["polyp_cell"], [c["code"] for c in cla_w20["corrections"]]),
                         ("Y83", ["ANTHONY_EXACT_IDENTIFIER_ALIAS", "REVIEWED_WEEK_HEADER"]))
        tth = by_key[("TTH-AVI-1.009", 1)]
        self.assertEqual(tth["legacy_reading"], {"polyps": 20, "ephyrae": 300})
        ldr = by_key[("LDR-JAP-1.001", 18)]
        self.assertEqual((ldr["polyps"], ldr["ephyrae"], ldr["operation"]), (80, 0, "EXPLICIT_CORRECTION"))
        self.assertEqual(by_key[("TTH-AVI-1.009", 1)]["polyps"], 300)

    def test_real_workbook_regenerates_the_committed_manifest(self):
        if not REAL_WORKBOOK.exists():
            raise SkipTest("Authoritative workbook is not available next to this repository.")
        rebuilt = manifest_module.with_fingerprint(build_manifest(REAL_WORKBOOK))
        self.assertEqual(manifest_module.dumps(rebuilt), manifest_module.DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(read_workbook(REAL_WORKBOOK).sheets), 7)
