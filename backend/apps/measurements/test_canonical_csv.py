"""Synthetic, database-free tests; also runnable with standard-library unittest."""

import csv
from decimal import localcontext
from io import StringIO
import json
import unittest

from .canonical import FIELDS, MAX_COUNT, Provenance, SourceCoordinate, validate_row
from .csv_import import CsvLimits, read_csv


class CanonicalCsvTests(unittest.TestCase):
    mapping = {"Box": "box_reference", "Date": "measured_on", "Polyps": "polyp_count", "Ephyrae": "ephyrae_count"}

    def parse(self, body="B-001,2021-01-01,0,0\n", *, mapping=None, **kwargs):
        mapping = self.mapping if mapping is None else mapping
        delimiter = kwargs.get("delimiter", ",")
        header = delimiter.join(mapping) + "\n"
        return read_csv((header + body).encode("utf-8"), source_id="synthetic.csv",
                        mapping=mapping, confirmed=kwargs.pop("confirmed", True), **kwargs)

    def values(self, row):
        return {value.field: value for value in row.values}

    def invalid_field(self, field, value, code=None):
        mapping = dict(self.mapping)
        if field not in mapping.values():
            mapping[field] = field
        cells = {"box_reference": "B-001", "measured_on": "2021-01-01", "polyp_count": "0", "ephyrae_count": "0", field: value}
        output = StringIO()
        csv.writer(output, lineterminator="\n").writerow([cells[target] for target in mapping.values()])
        result = self.parse(output.getvalue(), mapping=mapping)
        self.assertFalse(result.ready_for_planning)
        self.assertEqual(len(result.invalid_rows), 1)
        reasons = [issue for issue in result.invalid_rows[0].issues if issue.field == field]
        self.assertTrue(reasons)
        if code:
            self.assertIn(code, [issue.code for issue in reasons])
        return result

    def test_valid_ordinary_csv_and_zero(self):
        result = self.parse()
        self.assertTrue(result.ready_for_planning)
        self.assertEqual(len(result.valid_rows), 1)
        fields = self.values(result.valid_rows[0])
        self.assertEqual(fields["polyp_count"].value, 0)
        self.assertEqual(fields["ephyrae_count"].value, 0)
        self.assertEqual(fields["strobila_count"].state, "absent")

    def test_french_headers_explicit_semicolon_mapping(self):
        mapping = {"Boîte": "box_reference", "Jour": "measured_on", "Polypes": "polyp_count", "Éphyrules": "ephyrae_count"}
        result = self.parse("B-001;2026-09-14;10;2\n", mapping=mapping, delimiter=";")
        self.assertTrue(result.ready_for_planning)
        self.assertEqual(result.mapping, tuple(mapping.items()))

    def test_utf8_bom_quoting_and_multiline_provenance(self):
        mapping = dict(self.mapping, Note="notes")
        data = '\ufeffBox,Date,Polyps,Ephyrae,Note\r\nB-001,2021-01-01,0,0,"first, line\r\nsecond ""quote"""\r\n'
        result = read_csv(data.encode("utf-8"), source_id="quoted.csv", mapping=mapping, confirmed=True)
        row = result.valid_rows[0]
        self.assertEqual(row.provenance.coordinate, SourceCoordinate("quoted.csv", 2, 2, 3))
        self.assertEqual(self.values(row)["notes"].value, 'first, line\r\nsecond "quote"')
        self.assertEqual(row.provenance.original_values[-1], 'first, line\r\nsecond "quote"')

    def test_duplicate_and_ambiguous_headers(self):
        for headers in ("Box,Date,Polyps,Polyps", "Box,Date,Polyps, polyps ", "Box,Date,,Ephyrae"):
            with self.subTest(headers=headers):
                result = read_csv((headers + "\n").encode(), source_id="headers", mapping=self.mapping, confirmed=True)
                self.assertEqual(result.issues[0].code, "duplicate_or_ambiguous_header")
                self.assertFalse(result.complete)

    def test_mapping_confirmation_is_required(self):
        self.assertEqual(self.parse(confirmed=False).issues[0].code, "mapping_not_confirmed")

    def test_all_headers_must_be_explicitly_mapped_or_ignored(self):
        result = read_csv(b"Box,Date,Polyps,Ephyrae,Extra\n", source_id="extra", mapping=self.mapping, confirmed=True)
        self.assertEqual(result.issues[0].code, "incomplete_header_mapping")
        result = self.parse("B,2021-01-01,0,0,kept raw\n", mapping=dict(self.mapping, Extra=None))
        self.assertTrue(result.ready_for_planning)
        self.assertEqual(result.valid_rows[0].provenance.original_values[-1], "kept raw")

    def test_duplicate_targets_and_unknown_mapping_rejected(self):
        self.assertEqual(self.parse(mapping=dict(self.mapping, Another="polyp_count")).issues[0].code, "duplicate_target_field")
        self.assertEqual(self.parse(mapping=dict(self.mapping, Other="temperature")).issues[0].code, "invalid_mapping")

    def test_required_and_time_mapping(self):
        mapping = dict(self.mapping)
        del mapping["Polyps"]
        self.assertEqual(self.parse(mapping=mapping).issues[0].code, "missing_required_mapping")
        mapping = dict(self.mapping)
        del mapping["Date"]
        self.assertEqual(self.parse(mapping=mapping).issues[0].code, "missing_time_mapping")

    def test_malformed_row_width_does_not_discard_other_records(self):
        result = self.parse("B,2021-01-01,0\nB,2021-01-01,0,0,extra\nB,2021-01-01,0,0\n")
        self.assertTrue(result.complete)
        self.assertEqual(len(result.invalid_rows), 2)
        self.assertEqual(len(result.valid_rows), 1)
        self.assertFalse(result.ready_for_planning)
        self.assertEqual(result.invalid_rows[0].issues[0].code, "row_width")

    def test_malformed_quotes_stop_without_losing_source_identity(self):
        for body in ('B,2021-01-01,0,"unterminated', 'B,2021-01-01,0,0"x\n', 'B,2021-01-01,0,"0"x\n'):
            with self.subTest(body=body):
                result = self.parse(body)
                self.assertFalse(result.complete)
                self.assertEqual(result.issues[0].code, "malformed_csv")
                self.assertEqual(result.invalid_rows[0].provenance.coordinate.source_id, "synthetic.csv")
                self.assertEqual(result.invalid_rows[0].provenance.coordinate.record_number, 2)

    def test_blank_prefilled_rows_and_physical_lines_are_counted(self):
        result = self.parse("B,2021-01-01,,\n,,,\n\nB,2021-01-01,0,0\n")
        self.assertTrue(result.ready_for_planning)
        self.assertEqual(result.blank_count, 3)
        self.assertEqual(len(result.valid_rows), 1)
        self.assertEqual(result.blank_rows[0].provenance.original_values, ("B", "2021-01-01", "", ""))

    def test_quoted_empty_record_is_not_physical_blank(self):
        result = self.parse('""\n')
        self.assertEqual(result.blank_count, 0)
        self.assertEqual(result.invalid_rows[0].issues[0].code, "row_width")

    def test_partial_rows_require_counts_box_and_time(self):
        self.invalid_field("polyp_count", "", "required")
        self.invalid_field("ephyrae_count", "", "required")
        self.invalid_field("box_reference", "", "required")
        self.invalid_field("measured_on", "", "date_or_iso_week_required")
        result = self.parse("B,2021-01-01,,,note\n", mapping=dict(self.mapping, Note="notes"))
        self.assertEqual(result.invalid_rows[0].status, "invalid")

    def test_nullable_fields_and_unsupplied_are_distinct(self):
        result = self.parse("B,2021-01-01,0,0,,,\n", mapping=dict(self.mapping, Strobila="strobila_count", Salinity="salinity_psu", Observer="observer_reference"))
        fields = self.values(result.valid_rows[0])
        for field in ("strobila_count", "salinity_psu", "observer_reference"):
            self.assertEqual(fields[field].state, "null")
            self.assertIsNone(fields[field].value)
        result = self.parse("B,2021-01-01,0,0,0\n", mapping=dict(self.mapping, Strobila="strobila_count"))
        self.assertEqual(self.values(result.valid_rows[0])["strobila_count"].value, 0)

    def test_optional_nonnullable_blank_has_no_implicit_default(self):
        result = self.parse("B,2021-01-01,0,0,,\n", mapping=dict(self.mapping, Status="culture_status", Attention="needs_attention"))
        for field in ("culture_status", "needs_attention"):
            self.assertEqual(self.values(result.valid_rows[0])[field].state, "blank")

    def test_invalid_counts_never_rounded_or_coerced(self):
        for value in ("NaN", "Infinity", "-Infinity", "abc", "-0.1", "-0.49", "-1", "1.2", "0.0", "1,000", "1e3", "=1+1", "+SUM(A1)", "@A1", str(MAX_COUNT + 1)):
            with self.subTest(value=value):
                self.invalid_field("polyp_count", value)

    def test_count_max_and_long_leading_zeros(self):
        for value in (str(MAX_COUNT), "0" * 5000):
            result = self.parse(f"B,2021-01-01,{value},0\n")
            self.assertTrue(result.ready_for_planning)
            self.assertEqual(self.values(result.valid_rows[0])["polyp_count"].value, int(value.lstrip("0") or "0"))

    def test_numeric_formula_has_explicit_reason(self):
        self.invalid_field("strobila_count", "=SUM(A1)", "numeric_formula")
        self.invalid_field("salinity_psu", "=1+1", "numeric_formula")

    def test_formulas_in_text_are_only_data(self):
        result = self.parse('B,2021-01-01,0,0,=SUM(A1),unknown observer\n', mapping=dict(self.mapping, Note="notes", Observer="observer_reference"))
        self.assertEqual(self.values(result.valid_rows[0])["notes"].value, "=SUM(A1)")
        self.assertEqual(self.values(result.valid_rows[0])["observer_reference"].value, "unknown observer")

    def test_decimal_domain_precision_and_no_guessing(self):
        for value in ("35,5", "1,000", "1.234", "NaN", "inf", "1000", "1e1", "--1"):
            with self.subTest(value=value):
                self.invalid_field("salinity_psu", value)
        for source, normalized in (("0", "0.00"), ("35.5", "35.50"), ("999.99", "999.99"), ("-1.25", "-1.25")):
            result = self.parse(f"B,2021-01-01,0,0,{source}\n", mapping=dict(self.mapping, Salinity="salinity_psu"))
            self.assertEqual(self.values(result.valid_rows[0])["salinity_psu"].value, normalized)

    def test_decimal_validation_is_independent_of_ambient_context(self):
        mapping = dict(self.mapping, Salinity="salinity_psu")
        expected = self.parse("B,2021-01-01,0,0,999.99\n", mapping=mapping)
        for precision in (1, 2, 3):
            with self.subTest(precision=precision), localcontext() as context:
                context.prec = precision
                actual = self.parse("B,2021-01-01,0,0,999.99\n", mapping=mapping)
                self.assertEqual(actual, expected)
                self.assertTrue(self.parse("B,2021-01-01,0,0,1.23\n", mapping=mapping).ready_for_planning)

    def test_choices_and_boolean_not_guessed(self):
        self.invalid_field("culture_status", "Bon", "invalid_choice")
        self.invalid_field("needs_attention", "oui", "boolean_format")
        result = self.parse("B,2021-01-01,0,0,good,false\n", mapping=dict(self.mapping, Status="culture_status", Attention="needs_attention"))
        self.assertIs(self.values(result.valid_rows[0])["needs_attention"].value, False)

    def test_exact_date_iso_year_boundary(self):
        time = self.parse().valid_rows[0].time
        self.assertEqual((time.precision, time.measured_on, time.iso_year, time.iso_week), ("exact_date", "2021-01-01", 2020, 53))

    def test_ambiguous_or_invalid_dates_rejected(self):
        for value in ("01/02/2026", "2026/01/02", "2026-1-2", "2026-02-29", "20260102", "2026-01-02T00:00:00"):
            with self.subTest(value=value):
                self.invalid_field("measured_on", value, "iso_date_required")

    def test_week_only_no_fabricated_date(self):
        mapping = {"Box": "box_reference", "Year": "iso_year", "Week": "iso_week", "Polyps": "polyp_count", "Ephyrae": "ephyrae_count"}
        result = self.parse("B,2020,53,0,0\n", mapping=mapping)
        self.assertTrue(result.ready_for_planning)
        self.assertEqual(result.valid_rows[0].time.precision, "iso_week")
        self.assertIsNone(result.valid_rows[0].time.measured_on)

    def test_invalid_iso_weeks_not_clamped(self):
        mapping = {"Box": "box_reference", "Year": "iso_year", "Week": "iso_week", "Polyps": "polyp_count", "Ephyrae": "ephyrae_count"}
        for year, week in (("2021", "53"), ("2020", "0"), ("2020", "54"), ("0000", "1"), ("2020", "1.0"), ("", "1"), ("2020", "")):
            with self.subTest(year=year, week=week):
                result = self.parse(f"B,{year},{week},0,0\n", mapping=mapping)
                self.assertFalse(result.ready_for_planning)
                self.assertEqual(len(result.invalid_rows), 1)

    def test_exact_date_and_supplied_week_must_agree(self):
        mapping = dict(self.mapping, Year="iso_year", Week="iso_week")
        self.assertTrue(self.parse("B,2021-01-01,0,0,2020,53\n", mapping=mapping).ready_for_planning)
        result = self.parse("B,2021-01-01,0,0,2021,1\n", mapping=mapping)
        self.assertIn("date_week_mismatch", [issue.code for issue in result.invalid_rows[0].issues])

    def test_original_values_and_field_coordinates(self):
        result = self.invalid_field("polyp_count", " -0.1 ")
        invalid = result.invalid_rows[0]
        self.assertEqual(invalid.provenance.original_values[2], " -0.1 ")
        self.assertEqual(invalid.provenance.coordinate.line_start, 2)
        self.assertEqual(invalid.issues[0].column, 3)
        self.assertNotIn("-0.1", invalid.issues[0].code)

    def test_deterministic_representation_and_fingerprint(self):
        first = self.parse()
        second = read_csv(b"Box,Date,Polyps,Ephyrae\nB-001,2021-01-01,0,0\n", source_id="synthetic.csv", mapping=dict(reversed(tuple(self.mapping.items()))), confirmed=True)
        self.assertEqual(first.canonical_json(), second.canonical_json())
        self.assertEqual(first.fingerprint, second.fingerprint)
        document = json.loads(first.valid_rows[0].canonical_json())
        self.assertEqual(document["schema_version"], "1.0")
        self.assertEqual(document["schema"], "polypbase.biological-measurement-entry")

    def test_source_bytes_and_configuration_change_fingerprint(self):
        result = self.parse()
        changed = self.parse(limits=CsvLimits(max_rows=20))
        self.assertNotEqual(result.fingerprint, changed.fingerprint)
        self.assertNotEqual(result.source_sha256, self.parse("B-001,2021-01-01,0,0\r\n").source_sha256)

    def test_byte_limit_before_decode(self):
        result = self.parse(limits=CsvLimits(max_bytes=4))
        self.assertEqual(result.issues[0].code, "byte_limit")
        self.assertEqual(result.records, ())

    def test_row_limit_includes_blank_and_has_coordinates(self):
        result = self.parse("\nB,2021-01-01,0,0\n", limits=CsvLimits(max_rows=1))
        self.assertFalse(result.complete)
        self.assertEqual(result.issues[0].code, "row_limit")
        self.assertEqual(result.invalid_rows[0].provenance.coordinate.line_start, 3)

    def test_field_and_column_limits(self):
        result = self.parse("B" * 31 + ",2021-01-01,0,0\n", limits=CsvLimits(max_field_chars=30))
        self.assertEqual(result.issues[0].code, "field_limit")
        self.assertLessEqual(len(result.invalid_rows[0].provenance.original_values[0]), 30)
        result = self.parse("B,2021-01-01,0,0,x\n", limits=CsvLimits(max_columns=4))
        self.assertEqual(result.issues[0].code, "column_limit")
        self.assertEqual(self.parse(limits=CsvLimits(max_columns=3)).issues[0].code, "invalid_mapping")

    def test_empty_invalid_encoding_and_nul(self):
        for data, code in ((b"", "missing_header"), (b"\xff", "invalid_utf8"), (b"x\x00", "nul_character")):
            result = read_csv(data, source_id="synthetic", mapping=self.mapping, confirmed=True)
            self.assertEqual(result.issues[0].code, code)

    def test_configuration_errors(self):
        with self.assertRaises(ValueError):
            CsvLimits(max_rows=0)
        with self.assertRaises(ValueError):
            self.parse(delimiter="|")
        with self.assertRaises(TypeError):
            read_csv("not bytes", source_id="synthetic", mapping=self.mapping, confirmed=True)
        with self.assertRaises(ValueError):
            read_csv(b"", source_id="", mapping=self.mapping, confirmed=True)

    def test_pure_validation_without_csv_and_order_independence(self):
        provenance = Provenance(SourceCoordinate("future-adapter", 2, 2, 2), (), (), ())
        values = {"box_reference": "B", "measured_on": "2021-01-01", "polyp_count": "0", "ephyrae_count": "0"}
        first = validate_row(values, provenance)
        second = validate_row(dict(reversed(tuple(values.items()))), provenance)
        self.assertEqual(first, second)
        for field in ("polyp_count", "ephyrae_count"):
            values[field] = None
        self.assertEqual(validate_row(values, provenance), validate_row(dict(reversed(tuple(values.items()))), provenance))
        values["polyp_count"] = "0" * 16385
        self.assertIn("field_limit", [issue.code for issue in validate_row(values, provenance).issues])

    def test_tab_delimiter_and_no_final_newline(self):
        self.assertTrue(self.parse("B\t2021-01-01\t0\t0", delimiter="\t").ready_for_planning)

    def test_all_optional_values_and_whitespace_source(self):
        mapping = {field: field for field in FIELDS}
        values = [" B ", "2021-01-01", "", "", "0", "0", "0", "0.00", "good", "true", " note ", " observer "]
        result = self.parse(",".join(values) + "\n", mapping=mapping)
        fields = self.values(result.valid_rows[0])
        self.assertEqual(fields["box_reference"].value, "B")
        self.assertEqual(fields["notes"].value, " note ")
        self.assertEqual(fields["observer_reference"].value, "observer")
        self.assertEqual(result.valid_rows[0].provenance.original_values[0], " B ")


if __name__ == "__main__":
    unittest.main()
