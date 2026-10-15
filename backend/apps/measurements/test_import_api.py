"""Isolated transport tests for diagnostic-only measurement CSV uploads."""

import csv
from datetime import date, datetime, timezone
from io import StringIO
import json
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.uploadhandler import StopFutureHandlers, StopUpload
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import OrganizationMembership
from apps.cultures.models import Box, SubcultureEvent
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .canonical import FIELDS
from .csv_import import CsvLimits, inspect_csv, read_csv
from .import_api import DiagnosticUploadHandler, LIMITS, MAX_REQUEST_BYTES
from .models import BiologicalMeasurement

INSPECT = "/api/measurement-import/inspect/"
PREVIEW = "/api/measurement-import/preview/"
BASE_FIELDS = ("box_reference", "measured_on", "polyp_count", "ephyrae_count")


class CsvInspectionTests(SimpleTestCase):
    def test_exact_headers_bom_delimiters_and_header_only_scope(self):
        for delimiter in (",", ";", "\t"):
            with self.subTest(delimiter=delimiter):
                data = ("\ufeff Box " + delimiter + '"Day\nrecord"\n"unterminated').encode()
                result = inspect_csv(data, delimiter=delimiter)
                self.assertEqual(result.headers, (" Box ", "Day\nrecord"))
                self.assertEqual(result.issues, ())

    def test_same_header_contract_as_preparation(self):
        for data, code in (
            (b"", "missing_header"), (b"a,A\n", "duplicate_or_ambiguous_header"),
            (b"a, \n", "duplicate_or_ambiguous_header"), (b'"open', "malformed_csv"),
            (b"a\xff", "invalid_utf8"), (b"a\n\x00", "nul_character"),
        ):
            with self.subTest(code=code):
                self.assertEqual(inspect_csv(data).issues[0].code, code)
                self.assertEqual(read_csv(data, source_id="test", mapping={}, confirmed=True).issues[0].code, code)

    def test_header_limits(self):
        for data, limits, code in (
            (b"abcd", CsvLimits(max_bytes=3), "byte_limit"),
            (b"abcd", CsvLimits(max_field_chars=3), "field_limit"),
            (b"a,b,c", CsvLimits(max_columns=2), "column_limit"),
        ):
            self.assertEqual(inspect_csv(data, limits=limits).issues[0].code, code)

    def test_handler_caps_actual_chunks_and_never_falls_back(self):
        handler = DiagnosticUploadHandler(None)
        handler.handle_raw_input(None, {}, 100 * LIMITS.max_bytes, b"boundary")
        with self.assertRaises(StopFutureHandlers):
            handler.new_file("file", "test.csv", "text/csv", None)
        handler.receive_data_chunk(b"a" * LIMITS.max_bytes, 0)
        with self.assertRaises(StopUpload):
            handler.receive_data_chunk(b"b", LIMITS.max_bytes)
        self.assertEqual(handler.file.getbuffer().nbytes, LIMITS.max_bytes)
        self.assertEqual(handler.error_code, "file_too_large")
        handler.file.close()


class MeasurementImportAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Diagnostic laboratory")
        cls.other_org = Organization.objects.create(name="Other diagnostic laboratory")
        users = get_user_model()
        cls.admin = users.objects.create_user(username="diagnostic-admin", email="diagnostic-admin@example.org")
        cls.tech = users.objects.create_user(username="diagnostic-tech", email="diagnostic-tech@example.org")
        cls.viewer = users.objects.create_user(username="diagnostic-viewer", email="diagnostic-viewer@example.org")
        cls.outsider = users.objects.create_user(username="diagnostic-outsider", email="diagnostic-outsider@example.org")
        cls.superuser = users.objects.create_user(username="diagnostic-root", email="diagnostic-root@example.org", is_superuser=True)
        for user, org, role in (
            (cls.admin, cls.org, "admin"), (cls.tech, cls.org, "lab_technician"),
            (cls.viewer, cls.org, "viewer"), (cls.outsider, cls.other_org, "admin"),
        ):
            OrganizationMembership.objects.create(user=user, organization=org, role=role)
        species = Species.objects.create(scientific_name="Diagnostic synthetic", genus_species_code="DIA")
        strain = Strain.objects.create(organization=cls.org, species=species, code="DIA-QA-1")
        foreign_strain = Strain.objects.create(organization=cls.other_org, species=species, code="DIA-OTHER-1")
        cls.box = Box.objects.create(
            organization=cls.org, strain=strain, global_code="DIA-QA-1.001", box_number="001",
            status=Box.Status.ACTIVE,
        )
        cls.foreign_box = Box.objects.create(
            organization=cls.other_org, strain=foreign_strain, global_code="DIA-OTHER-1.001", box_number="001",
            status=Box.Status.ACTIVE,
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def csv_bytes(self, rows=None, fields=BASE_FIELDS, delimiter=","):
        output = StringIO()
        writer = csv.writer(output, delimiter=delimiter, lineterminator="\n")
        writer.writerow(fields)
        for row in rows if rows is not None else [{}]:
            values = {
                "box_reference": self.box.global_code, "measured_on": "2026-09-14",
                "polyp_count": "0", "ephyrae_count": "0",
            } | row
            writer.writerow([values.get(field, "") for field in fields])
        return output.getvalue().encode()

    def no_writes(self, execute, sql, params, many, context):
        self.assertEqual(sql.lstrip().split(None, 1)[0].upper(), "SELECT", sql)
        return execute(sql, params, many, context)

    def post(self, url=PREVIEW, *, source=None, fields=BASE_FIELDS, rows=None, delimiter=",", extra=None, org=True):
        payload = {
            "file": SimpleUploadedFile("private-source-name.csv", self.csv_bytes(rows, fields, delimiter) if source is None else source),
            "delimiter": delimiter,
        }
        if url == PREVIEW:
            payload.update(mapping=json.dumps({field: field for field in fields}), confirmed="true")
        payload.update(extra or {})
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.org.pk)} if org else {}
        with connection.execute_wrapper(self.no_writes):
            response = self.client.post(url, payload, format="multipart", **headers)
        self.assertEqual(response["Cache-Control"], "no-store")
        return response

    def assert_error(self, response, code, status=400):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.json(), {"error_code": code})

    def preview(self, **kwargs):
        response = self.post(**kwargs)
        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()
        self.assertIs(result["application_available"], False)
        return result

    def test_inspection_and_exact_delimiters(self):
        for user in (self.admin, self.tech):
            self.client.force_authenticate(user)
            for delimiter in (",", ";", "\t"):
                response = self.post(INSPECT, delimiter=delimiter)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {
                    "headers": list(BASE_FIELDS), "fields": list(FIELDS), "issues": [],
                    "application_available": False,
                })

    def test_permissions_precede_any_multipart_parsing(self):
        for user in (None, self.viewer, self.outsider, self.superuser):
            self.client.force_authenticate(user)
            with patch("rest_framework.parsers.MultiPartParser.parse", side_effect=AssertionError("Parsed forbidden upload")):
                for url in (INSPECT, PREVIEW):
                    self.assert_error(self.post(url), "measurement_import_forbidden", 403)
        self.client.force_authenticate(self.admin)
        self.assert_error(self.post(org=False), "measurement_import_forbidden", 403)
        for selected in ("", "bad", "0", "-1", "9" * 100, str(self.other_org.pk), "99999999"):
            response = self.client.post(PREVIEW, b"not multipart", content_type="multipart/form-data", HTTP_X_ORGANIZATION_ID=selected)
            self.assert_error(response, "measurement_import_forbidden", 403)
            self.assertEqual(response["Cache-Control"], "no-store")

    def test_inactive_security_state_reloaded(self):
        for model, filters in (
            (get_user_model(), {"pk": self.admin.pk}),
            (Organization, {"pk": self.org.pk}),
            (OrganizationMembership, {"user": self.admin, "organization": self.org}),
        ):
            model.objects.filter(**filters).update(is_active=False)
            self.assert_error(self.post(), "measurement_import_forbidden", 403)
            model.objects.filter(**filters).update(is_active=True)

    def test_session_csrf_is_preserved_and_authority_checked_before_csrf_parses(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.client.force_login(self.viewer)
        with patch("django.http.multipartparser.MultiPartParser.parse", side_effect=AssertionError("Parsed before authority")):
            self.assert_error(self.post(), "measurement_import_forbidden", 403)
        self.client.force_login(self.admin)
        self.assert_error(self.post(), "measurement_import_forbidden", 403)
        token = "a" * 32
        self.client.cookies["csrftoken"] = token
        self.client.credentials(HTTP_X_CSRFTOKEN=token)
        before = {model._meta.label: list(model.objects.order_by("pk").values()) for model in apps.get_models()}
        self.assertEqual(self.post(INSPECT).status_code, 200)
        self.preview()
        after = {model._meta.label: list(model.objects.order_by("pk").values()) for model in apps.get_models()}
        self.assertEqual(after, before)

    def test_exact_zero_and_minimized_contract(self):
        fields = (*BASE_FIELDS, "strobila_count", "salinity_psu", "notes", "observer_reference")
        result = self.preview(fields=fields, rows=[{
            "strobila_count": "0", "salinity_psu": "0", "notes": "private source note",
            "observer_reference": "private observer name",
        }])
        row = result["rows"][0]
        self.assertEqual(row["outcome"], "CREATE")
        self.assertEqual((row["record_number"], row["line_start"], row["line_end"]), (2, 2, 2))
        self.assertEqual((row["measured_on"], row["precision"], row["iso_year"], row["iso_week"]), ("2026-09-14", "exact_date", 2026, 38))
        values = {value["field"]: value for value in row["values"]}
        self.assertEqual(values["polyp_count"], {"field": "polyp_count", "state": "value", "value": 0})
        self.assertEqual(values["strobila_count"]["value"], 0)
        self.assertEqual(values["salinity_psu"]["value"], "0.00")
        self.assertEqual(row["issues"], [{"code": "observer_resolution_unsupported", "field": "observer_reference", "column": 8}])
        self.assertFalse(result["summary"]["ready"])
        serialized = json.dumps(result)
        for private in ("private source note", "private observer name", "private-source-name", "original_values", "existing", "organization_id"):
            self.assertNotIn(private, serialized)
        self.assertNotIn("notes", values)
        self.assertNotIn("observer_reference", values)
        self.assertEqual(set(result), {
            "application_available", "preparation_fingerprint", "plan_fingerprint", "source_complete", "issues", "summary", "rows",
        })
        self.assertEqual(set(row), {
            "record_number", "line_start", "line_end", "outcome", "box_reference", "measured_on", "precision", "iso_year", "iso_week", "values", "issues",
        })

    def test_fresh_preview_detects_new_match_then_conflict(self):
        first = self.preview()
        self.assertTrue(first["summary"]["ready"])
        self.assertEqual(first["summary"]["create_candidates"], 1)
        measurement = BiologicalMeasurement.objects.create(box=self.box, measured_on=date(2026, 9, 14), polyp_count=0, ephyrae_count=0)
        second = self.preview()
        self.assertEqual(second["rows"][0]["outcome"], "ALREADY_MATCHES")
        self.assertEqual(second["preparation_fingerprint"], first["preparation_fingerprint"])
        self.assertNotEqual(second["plan_fingerprint"], first["plan_fingerprint"])
        measurement.polyp_count = 4
        measurement.save(update_fields=["polyp_count"])
        third = self.preview()
        self.assertEqual(third["rows"][0]["outcome"], "CONFLICT")
        self.assertNotEqual(third["plan_fingerprint"], second["plan_fingerprint"])
        self.assertFalse(third["summary"]["ready"])

    def test_duplicates_unknown_foreign_and_inactive(self):
        result = self.preview(rows=[{}, {}, {"box_reference": self.foreign_box.global_code}, {"box_reference": "unknown"}])
        self.assertEqual([row["outcome"] for row in result["rows"]], ["CONFLICT", "CONFLICT", "UNKNOWN_BOX", "UNKNOWN_BOX"])
        self.assertEqual(result["rows"][0]["issues"][0]["code"], "duplicate_source_slot")
        self.assertEqual(result["rows"][2]["issues"], result["rows"][3]["issues"])
        Box.objects.filter(pk=self.box.pk).update(status=Box.Status.INACTIVE)
        result = self.preview()
        self.assertEqual(result["rows"][0]["issues"][0]["code"], "inactive_box")
        self.assertFalse(result["summary"]["ready"])

    def test_week_only_does_not_invent_measurement_date(self):
        fields = ("box_reference", "iso_year", "iso_week", "polyp_count", "ephyrae_count")
        result = self.preview(fields=fields, rows=[{"iso_year": "2026", "iso_week": "38"}])
        row = result["rows"][0]
        self.assertIsNone(row["measured_on"])
        self.assertEqual(row["precision"], "iso_week")
        self.assertEqual(row["issues"][0]["code"], "week_precision_persistence_required")
        self.assertFalse(result["summary"]["ready"])

    def test_week_subculture_chronology_is_preserved(self):
        SubcultureEvent.objects.create(
            parent_box=self.box, event_date=date(2026, 9, 14),
            occurred_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
            parent_state_sequence=1, parent_polyp_count_before=10,
            allocated_polyp_count=2, parent_polyp_count_after=8,
            parent_state_snapshot={"synthetic": True},
        )
        result = self.preview(
            fields=("box_reference", "iso_year", "iso_week", "polyp_count", "ephyrae_count"),
            rows=[{"iso_year": "2026", "iso_week": "38"}],
        )
        self.assertEqual(result["rows"][0]["outcome"], "CONFLICT")
        self.assertIn("week_subculture_chronology_conflict", [issue["code"] for issue in result["rows"][0]["issues"]])
        self.assertIsNone(result["rows"][0]["measured_on"])
        self.assertFalse(result["summary"]["ready"])

    def test_nullable_blank_is_not_zero(self):
        fields = (*BASE_FIELDS, "strobila_count", "salinity_psu")
        BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 9, 14), polyp_count=0, ephyrae_count=0,
            strobila_count=None, salinity_psu=None,
        )
        result = self.preview(fields=fields)
        self.assertEqual(result["rows"][0]["outcome"], "ALREADY_MATCHES")
        values = {value["field"]: value for value in result["rows"][0]["values"]}
        self.assertEqual(values["strobila_count"], {"field": "strobila_count", "state": "null", "value": None})
        result = self.preview(fields=fields, rows=[{"strobila_count": "0"}])
        self.assertEqual(result["rows"][0]["outcome"], "CONFLICT")

    def test_invalid_ignored_and_multiline_coordinates(self):
        result = self.preview(fields=(*BASE_FIELDS, "notes"), rows=[
            {"notes": "first\nsecond"}, {"polyp_count": ""}, {"polyp_count": "", "ephyrae_count": ""},
        ])
        self.assertEqual([row["outcome"] for row in result["rows"]], ["CREATE", "INVALID", "IGNORED"])
        self.assertEqual((result["rows"][0]["line_start"], result["rows"][0]["line_end"]), (2, 3))
        self.assertEqual(result["rows"][1]["values"], [])
        self.assertIsNone(result["rows"][1]["box_reference"])
        self.assertEqual(result["summary"]["ignored_rows"], 1)

    def test_preparation_issues_are_diagnostics_not_transport_errors(self):
        for source, code in (
            (b"", "missing_header"), (b"a,A\n", "duplicate_or_ambiguous_header"),
            (b"a\xff", "invalid_utf8"), (b"a\x00", "nul_character"),
            (b"a" * 16385, "field_limit"), (b",".join([b"a"] * 65), "column_limit"),
        ):
            for url in (INSPECT, PREVIEW):
                response = self.post(url, source=source)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertIn(code, [issue["code"] for issue in response.json()["issues"]])
        result = self.preview(source=self.csv_bytes() + b'"unterminated')
        self.assertFalse(result["source_complete"])
        self.assertEqual(result["rows"][-1]["outcome"], "INVALID")
        self.assertIn("malformed_csv", [issue["code"] for issue in result["issues"]])

    def test_explicit_mapping_no_guess_or_implicit_confirmation(self):
        for confirmed in ("", "false", "True", "1"):
            self.assert_error(self.post(extra={"confirmed": confirmed}), "mapping_not_confirmed")
        response = self.post(extra={"mapping": "{}"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("incomplete_header_mapping", [issue["code"] for issue in response.json()["issues"]])
        for mapping in ("bad json", "[]", "null", '{"a":null,"a":null}', json.dumps({"a": "wrong"}), json.dumps({"a" * 16385: None}), json.dumps({str(i): None for i in range(65)}), '{"\\ud800":null}', '[' * 1100):
            self.assert_error(self.post(extra={"mapping": mapping}), "invalid_mapping")
        mapping = {field: field for field in BASE_FIELDS} | {"ignore": None}
        result = self.preview(fields=(*BASE_FIELDS, "ignore"), rows=[{"ignore": "private ignored cell"}], extra={"mapping": json.dumps(mapping)})
        self.assertEqual(result["rows"][0]["outcome"], "CREATE")
        self.assertNotIn("private ignored cell", json.dumps(result))

    def test_row_limit_remains_incomplete_and_diagnostic(self):
        with patch("apps.measurements.import_api.LIMITS", CsvLimits(max_rows=2)):
            result = self.preview(rows=[{}, {}, {}])
        self.assertFalse(result["source_complete"])
        self.assertFalse(result["summary"]["ready"])
        self.assertIn("row_limit", [issue["code"] for issue in result["issues"]])
        self.assertEqual(result["rows"][-1]["outcome"], "INVALID")

    def test_invalid_transport_configuration(self):
        for delimiter in ("|", "tab", "\\t", "", ",;"):
            self.assert_error(self.post(extra={"delimiter": delimiter}), "invalid_delimiter")
        self.assert_error(self.post(extra={"unexpected": "value"}), "invalid_configuration")
        self.assert_error(self.post(extra={"delimiter": [",", ";"]}), "invalid_configuration")
        self.assert_error(self.post(extra={"file": [SimpleUploadedFile("a", b"a"), SimpleUploadedFile("b", b"b")]}), "invalid_file")
        self.assert_error(self.post(extra={"file": "not a file"}), "invalid_configuration")
        for content_type, body, code in (
            ("application/json", b"{}", "multipart_required"),
            ("multipart/form-data", b"no boundary", "malformed_multipart"),
            ("multipart/form-data; boundary=abc", b'--abc\r\nContent-Disposition: form-data; name="file"; filename="a.csv"\r\n\r\ntruncated', "invalid_file"),
        ):
            response = self.client.post(PREVIEW, body, content_type=content_type, HTTP_X_ORGANIZATION_ID=str(self.org.pk))
            self.assert_error(response, code)
            self.assertEqual(response["Cache-Control"], "no-store")

    @override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=1)
    def test_large_uploads_never_use_temporary_file_handler(self):
        with patch("django.core.files.uploadhandler.TemporaryFileUploadHandler.new_file", side_effect=AssertionError("Disk upload")):
            for url in (INSPECT, PREVIEW):
                self.assert_error(self.post(url, source=b"x" * (LIMITS.max_bytes + 1)), "file_too_large", 413)
            response = self.post(INSPECT, source=b"x" * LIMITS.max_bytes)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["issues"][0]["code"], "field_limit")
            self.assertEqual(self.post(INSPECT).status_code, 200)

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=None)
    def test_metadata_is_bounded_even_if_global_limit_disabled(self):
        self.assert_error(self.post(extra={"mapping": "x" * (MAX_REQUEST_BYTES + 1)}), "request_too_large", 413)

    def test_no_model_audit_or_revision_changes_and_no_apply_route(self):
        def snapshot():
            return {model._meta.label: list(model.objects.order_by("pk").values()) for model in apps.get_models()}
        before = snapshot()
        self.post(INSPECT)
        self.preview()
        self.preview(rows=[{}, {}])
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.client.post("/api/measurement-import/apply/").status_code, 404)
        for method in (self.client.get, self.client.put, self.client.delete):
            response = method(PREVIEW, HTTP_X_ORGANIZATION_ID=str(self.org.pk))
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response["Cache-Control"], "no-store")
