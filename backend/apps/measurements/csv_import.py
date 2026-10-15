"""Bounded, explicit CSV adapter for the pure canonical measurement contract."""

from dataclasses import asdict, dataclass
import hashlib
import json
from collections.abc import Mapping

from .canonical import FIELDS, SCHEMA, VERSION, Issue, Provenance, RowResult, SourceCoordinate, validate_row


@dataclass(frozen=True)
class CsvLimits:
    max_bytes: int = 5 * 1024 * 1024
    max_rows: int = 10000  # Data records, including blank and invalid records.
    max_columns: int = 64
    max_field_chars: int = 16384

    def __post_init__(self):
        for value in asdict(self).values():
            if type(value) is not int or value < 1:
                raise ValueError("CSV limits must be positive integers.")


@dataclass(frozen=True)
class CsvResult:
    source_id: str
    source_sha256: str
    delimiter: str
    mapping: tuple[tuple[str, str | None], ...]
    limits: CsvLimits
    records: tuple[RowResult, ...] = ()
    issues: tuple[Issue, ...] = ()
    complete: bool = False
    schema: str = SCHEMA
    schema_version: str = VERSION

    @property
    def valid_rows(self):
        return tuple(record.row for record in self.records if record.status == "valid")

    @property
    def blank_rows(self):
        return tuple(record for record in self.records if record.status == "blank")

    @property
    def blank_count(self):
        return len(self.blank_rows)

    @property
    def invalid_rows(self):
        return tuple(record for record in self.records if record.status == "invalid")

    @property
    def ready_for_planning(self):
        return self.complete and not self.issues and not self.invalid_rows

    def canonical_json(self):
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def fingerprint(self):
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class _CsvFailure(Exception):
    def __init__(self, code, start, end, cells):
        self.code, self.start, self.end, self.cells = code, start, end, cells


def _records(text, delimiter, limits):
    """Strict quoted-field lexer, without process-global csv.field_size_limit.

    Accumulation stops at the field/column limits. CRLF is one physical line;
    embedded newlines remain verbatim in quoted cells.
    """
    cells, field = [], []
    state = "start"
    line = start = 1
    index = 0
    active = False
    while index < len(text):
        char = text[index]
        newline = char in "\r\n"
        token = "\r\n" if char == "\r" and text[index:index + 2] == "\r\n" else char
        physically_empty = not active and newline
        active = True
        if state == "quoted":
            if char == '"':
                state = "closed"
            else:
                field.extend(token)
        elif state == "closed" and char == '"':
            field.append('"')
            state = "quoted"
        elif char == delimiter or newline:
            cells.append("".join(field))
            field = []
            state = "start"
            if len(cells) > limits.max_columns:
                raise _CsvFailure("column_limit", start, line, tuple(cells[:limits.max_columns]))
            if newline:
                yield tuple(cells), start, line, physically_empty
                cells = []
                active = False
                start = line + 1
        elif state == "closed" or (char == '"' and state != "start"):
            raise _CsvFailure("malformed_csv", start, line, tuple(cells) + ("".join(field),))
        elif char == '"':
            state = "quoted"
        else:
            field.append(char)
            state = "unquoted"
        if len(field) > limits.max_field_chars:
            raise _CsvFailure("field_limit", start, line, tuple(cells) + ("".join(field[:limits.max_field_chars]),))
        if newline:
            line += 1
        index += len(token)
    if state == "quoted":
        raise _CsvFailure("malformed_csv", start, line, tuple(cells) + ("".join(field),))
    if active:
        cells.append("".join(field))
        if len(cells) > limits.max_columns:
            raise _CsvFailure("column_limit", start, line, tuple(cells[:limits.max_columns]))
        yield tuple(cells), start, line, False


def _header_issues(headers):
    normalized = [header.strip().casefold() for header in headers]
    if any(not header for header in normalized) or len(set(normalized)) != len(headers):
        return (Issue("duplicate_or_ambiguous_header"),)
    return ()


@dataclass(frozen=True)
class CsvInspection:
    """Header diagnostics only; this does not certify data records."""

    headers: tuple[str, ...] = ()
    issues: tuple[Issue, ...] = ()


def inspect_csv(data: bytes, *, delimiter: str = ",", limits: CsvLimits = CsvLimits()) -> CsvInspection:
    """Inspect an explicit UTF-8 CSV header using the preparation lexer/limits."""
    if not isinstance(data, bytes):
        raise TypeError("CSV input must be bytes.")
    if delimiter not in {",", ";", "\t"}:
        raise ValueError("Choose an explicit comma, semicolon or tab delimiter.")
    if len(data) > limits.max_bytes:
        return CsvInspection(issues=(Issue("byte_limit"),))
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return CsvInspection(issues=(Issue("invalid_utf8"),))
    if "\x00" in text:
        return CsvInspection(issues=(Issue("nul_character"),))
    try:
        header = next(_records(text, delimiter, limits), None)
    except _CsvFailure as failure:
        return CsvInspection(issues=(Issue(failure.code),))
    if header is None:
        return CsvInspection(issues=(Issue("missing_header"),))
    headers = header[0]
    return CsvInspection(headers, _header_issues(headers))


def read_csv(data: bytes, *, source_id: str, mapping: Mapping[str, str | None],
             confirmed: bool, delimiter: str = ",", limits: CsvLimits = CsvLimits()) -> CsvResult:
    """Prepare bounded UTF-8 bytes; no sniffing, I/O, model lookup or mutation.

    Every exact header must map to one canonical field or explicit None (ignore).
    A caller must explicitly confirm the mapping. Unusable/truncated input never
    yields a result ready for planning; preceding records remain diagnostic only.
    """
    if not isinstance(data, bytes):
        raise TypeError("CSV input must be bytes.")
    if not isinstance(source_id, str) or not source_id.strip() or len(source_id) > 512:
        raise ValueError("A bounded nonempty source identity is required.")
    if delimiter not in {",", ";", "\t"}:
        raise ValueError("Choose an explicit comma, semicolon or tab delimiter.")
    if not isinstance(mapping, Mapping):
        raise TypeError("Mapping must be a header-to-field mapping.")
    # Configuration is also bounded; do not echo arbitrary input in errors.
    if len(mapping) > limits.max_columns or any(
        not isinstance(header, str) or len(header) > limits.max_field_chars
        or (field is not None and (not isinstance(field, str) or field not in FIELDS))
        for header, field in mapping.items()
    ):
        return CsvResult(source_id, "", delimiter, (), limits, issues=(Issue("invalid_mapping"),))
    records = []
    ordered_mapping = tuple(sorted(mapping.items()))
    digest = ""

    def result(*, issues=(), complete=False):
        return CsvResult(source_id, digest, delimiter, ordered_mapping, limits,
                         tuple(records), tuple(issues), complete)

    if len(data) > limits.max_bytes:
        return result(issues=(Issue("byte_limit"),))
    digest = hashlib.sha256(data).hexdigest()
    if confirmed is not True:
        return result(issues=(Issue("mapping_not_confirmed"),))
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return result(issues=(Issue("invalid_utf8"),))
    if "\x00" in text:
        return result(issues=(Issue("nul_character"),))
    reader = _records(text, delimiter, limits)
    headers = ()
    record_number = 1
    try:
        header_record = next(reader, None)
        if header_record is None:
            return result(issues=(Issue("missing_header"),))
        headers, _, _, _ = header_record
        header_issues = _header_issues(headers)
        if header_issues:
            return result(issues=header_issues)
        if set(headers) != set(mapping):
            return result(issues=(Issue("incomplete_header_mapping"),))
        ordered_mapping = tuple((header, mapping[header]) for header in headers)
        targets = [field for _, field in ordered_mapping if field is not None]
        if len(set(targets)) != len(targets):
            return result(issues=(Issue("duplicate_target_field"),))
        if not {"box_reference", "polyp_count", "ephyrae_count"}.issubset(targets):
            return result(issues=(Issue("missing_required_mapping"),))
        if "measured_on" not in targets and not {"iso_year", "iso_week"}.issubset(targets):
            return result(issues=(Issue("missing_time_mapping"),))
        for cells, start, end, physically_empty in reader:
            record_number += 1
            provenance = Provenance(SourceCoordinate(source_id, record_number, start, end),
                                    headers, cells, ordered_mapping)
            if len(records) >= limits.max_rows:
                records.append(RowResult("invalid", provenance, issues=(Issue("row_limit"),)))
                return result(issues=(Issue("row_limit"),))
            # A physical empty line is an intentionally empty record.
            if physically_empty:
                records.append(RowResult("blank", provenance))
            elif len(cells) != len(headers):
                records.append(RowResult("invalid", provenance, issues=(Issue("row_width"),)))
            else:
                values = {field: cells[index] for index, (_, field) in enumerate(ordered_mapping)
                          if field is not None}
                records.append(validate_row(values, provenance))
        return result(complete=True)
    except _CsvFailure as failure:
        provenance = Provenance(SourceCoordinate(source_id, record_number + 1 if headers else 1,
                                                failure.start, failure.end),
                                headers, failure.cells, ordered_mapping)
        records.append(RowResult("invalid", provenance, issues=(Issue(failure.code),)))
        return result(issues=(Issue(failure.code),))
