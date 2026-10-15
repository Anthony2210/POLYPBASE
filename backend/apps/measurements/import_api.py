"""Ephemeral diagnostic uploads. No source storage or application capability."""

from dataclasses import asdict
import json
from types import SimpleNamespace

from django.core.exceptions import RequestDataTooBig, TooManyFieldsSent, TooManyFilesSent
from django.core.files.uploadhandler import MemoryFileUploadHandler, StopUpload
from django.http.multipartparser import MultiPartParserError
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import APIException, ParseError, PermissionDenied, UnsupportedMediaType
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import BasePermission
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from .canonical import FIELDS
from .csv_import import CsvLimits, inspect_csv, read_csv
from .import_planner import authorize_measurement_import, build_measurement_import_plan

LIMITS = CsvLimits()
# Includes worst-case JSON escaping of all bounded mapping headers plus framing.
MAX_REQUEST_BYTES = LIMITS.max_bytes + 64 * (16384 * 6 + 128) + 65536


class DiagnosticRequestError(APIException):
    def __init__(self, code, status=400):
        self.status_code = status
        super().__init__(detail=code, code=code)


class BoundedRequestStream:
    """Bound even multipart metadata and transfer-encoded wire input."""

    def __init__(self, stream):
        self.stream = stream
        self.remaining = MAX_REQUEST_BYTES

    def _read(self, method, size):
        data = method(min(size, self.remaining + 1) if size >= 0 else self.remaining + 1)
        self.remaining -= len(data)
        if self.remaining < 0:
            raise DiagnosticRequestError("request_too_large", 413)
        return data

    def read(self, size=-1):
        return self._read(self.stream.read, size)

    def readline(self, size=-1):
        return self._read(self.stream.readline, size)


class DiagnosticUploadHandler(MemoryFileUploadHandler):
    """Sole upload handler: cap bytes before buffering; never fall back to disk."""

    def __init__(self, request):
        super().__init__(request)
        self.total = 0
        self.files_started = 0
        self.error_code = None

    def handle_raw_input(self, *args, **kwargs):
        self.activated = True

    def new_file(self, *args, **kwargs):
        self.files_started += 1
        if self.files_started > 1:
            self.error_code = "invalid_file"
            raise StopUpload(connection_reset=True)
        super().new_file(*args, **kwargs)

    def receive_data_chunk(self, raw_data, start):
        self.total += len(raw_data)
        if self.total > LIMITS.max_bytes:
            self.error_code = "file_too_large"
            raise StopUpload(connection_reset=True)
        return super().receive_data_chunk(raw_data, start)


def _authorize_request(request):
    selected = request.headers.get("X-Organization-Id", "")
    if not selected.isascii() or not selected.isdecimal() or len(selected) > 18 or int(selected) < 1:
        raise PermissionDenied(code="measurement_import_forbidden")
    return authorize_measurement_import(request.user, SimpleNamespace(pk=int(selected)))


class DiagnosticSessionAuthentication(SessionAuthentication):
    def enforce_csrf(self, request):
        # Django's CSRF token check may parse POST. Authorize before that happens.
        _authorize_request(request._request)
        super().enforce_csrf(request)


class DiagnosticPermission(BasePermission):
    def has_permission(self, request, view):
        _, view.organization = _authorize_request(request)
        return True


def _row_payload(planned):
    source = planned.source
    coordinate = source.provenance.coordinate
    row = source.row
    values = tuple(value for value in row.values if value.field not in {"notes", "observer_reference"}) if row else ()
    reference = next((value.value for value in values if value.field == "box_reference"), None)
    return {
        "record_number": coordinate.record_number,
        "line_start": coordinate.line_start,
        "line_end": coordinate.line_end,
        "outcome": planned.outcome,
        "box_reference": reference,
        "measured_on": row.time.measured_on if row else None,
        "precision": row.time.precision if row else None,
        "iso_year": row.time.iso_year if row else None,
        "iso_week": row.time.iso_week if row else None,
        "values": [asdict(value) for value in values],
        "issues": [asdict(issue) for issue in planned.issues],
    }


class MeasurementImportDiagnosticAPIView(APIView):
    authentication_classes = [DiagnosticSessionAuthentication]
    permission_classes = [DiagnosticPermission]
    parser_classes = [MultiPartParser]
    renderer_classes = [JSONRenderer]
    http_method_names = ["post", "options"]
    preview = False

    def initialize_request(self, request, *args, **kwargs):
        # Install before session CSRF handling, which can access request.POST.
        request.upload_handlers = [DiagnosticUploadHandler(request)]
        request._stream = BoundedRequestStream(request._stream)
        return super().initialize_request(request, *args, **kwargs)

    def handle_exception(self, exc):
        if isinstance(exc, DiagnosticRequestError):
            return Response({"error_code": str(exc.detail)}, status=exc.status_code)
        if isinstance(exc, (RequestDataTooBig, TooManyFieldsSent, TooManyFilesSent)):
            return Response({"error_code": "request_too_large"}, status=413)
        if isinstance(exc, (ParseError, MultiPartParserError)):
            return Response({"error_code": "malformed_multipart"}, status=400)
        if isinstance(exc, UnsupportedMediaType):
            return Response({"error_code": "multipart_required"}, status=400)
        response = super().handle_exception(exc)
        if response.status_code in {401, 403}:
            response.data = {"error_code": "measurement_import_forbidden"}
        return response

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def post(self, request):
        data = request.data
        handler = request._request.upload_handlers[0]
        if handler.error_code:
            raise DiagnosticRequestError(handler.error_code, 413 if handler.error_code == "file_too_large" else 400)
        allowed = {"delimiter", "mapping", "confirmed"} if self.preview else {"delimiter"}
        if set(request.POST) - allowed or any(len(request.POST.getlist(key)) != 1 for key in request.POST):
            raise DiagnosticRequestError("invalid_configuration")
        if set(request.FILES) != {"file"} or len(request.FILES.getlist("file")) != 1:
            raise DiagnosticRequestError("invalid_file")
        delimiter = data.get("delimiter")
        if delimiter not in {",", ";", "\t"}:
            raise DiagnosticRequestError("invalid_delimiter")
        mapping = None
        if self.preview:
            if data.get("confirmed") != "true":
                raise DiagnosticRequestError("mapping_not_confirmed")
            try:
                mapping = json.loads(data.get("mapping", ""), object_pairs_hook=_unique_mapping)
                if isinstance(mapping, dict):
                    for header in mapping:
                        header.encode("utf-8")
            except (ValueError, TypeError, RecursionError):
                raise DiagnosticRequestError("invalid_mapping") from None
            if not isinstance(mapping, dict) or len(mapping) > LIMITS.max_columns or any(
                len(header) > LIMITS.max_field_chars
                or (field is not None and (not isinstance(field, str) or field not in FIELDS))
                for header, field in mapping.items()
            ):
                raise DiagnosticRequestError("invalid_mapping")
        upload = request.FILES["file"]
        try:
            source = upload.read(LIMITS.max_bytes + 1)
        finally:
            upload.close()
        if len(source) > LIMITS.max_bytes:
            raise DiagnosticRequestError("file_too_large", 413)
        if not self.preview:
            inspection = inspect_csv(source, delimiter=delimiter, limits=LIMITS)
            return Response({
                "headers": list(inspection.headers), "fields": list(FIELDS),
                "issues": [asdict(issue) for issue in inspection.issues],
                "application_available": False,
            })
        preparation = read_csv(
            source, source_id="diagnostic-upload", mapping=mapping,
            confirmed=True, delimiter=delimiter, limits=LIMITS,
        )
        plan = build_measurement_import_plan(preparation, organization=self.organization, actor=request.user)
        return Response({
            "application_available": False,
            "preparation_fingerprint": plan.preparation_fingerprint,
            "plan_fingerprint": plan.fingerprint,
            "source_complete": plan.source_complete,
            "issues": [asdict(issue) for issue in plan.issues],
            "summary": asdict(plan.summary),
            "rows": [_row_payload(row) for row in plan.rows],
        })


def _unique_mapping(pairs):
    result = {}
    for header, field in pairs:
        if header in result:
            raise ValueError("Duplicate mapping key.")
        result[header] = field
    return result


class MeasurementImportInspectAPIView(MeasurementImportDiagnosticAPIView):
    pass


class MeasurementImportPreviewAPIView(MeasurementImportDiagnosticAPIView):
    preview = True
