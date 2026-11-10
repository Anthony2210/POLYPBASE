"""Versioned scientific snapshot of one source Box, with explicit disclosure.

Pure contract: no Django imports, no persistence, no transport or file format.
It separates scientific content from the transfer envelope, file encodings,
import planning and destination materialization, which build on top of it.

Semantics are frozen per version. A later version may add selectors or keys;
it must never change the meaning of an existing one. Validation is strict and
exact: unknown versions, keys and selectors are rejected, never ignored.
"""

from dataclasses import dataclass
from datetime import date
import json
import re
from typing import Mapping
import uuid

from .scientific_time import ObservationPrecision, ObservationTime

SCHEMA = "polypbase.scientific-snapshot"
VERSION = "1.0"
SUPPORTED_VERSIONS = frozenset({VERSION})
MAX_COUNT = 2147483647  # Portable PositiveIntegerField range.

# Server-owned catalogue for 1.0. Clients choose among these names only; they
# are contract names, never ORM paths or payload keys supplied by a client.
SOURCE_SELECTORS = {
    "source.institution_portable_id": "institution_portable_id",
    "source.institution_name": "institution_name",
    "source.box_code": "box_code",
}
IDENTITY_SELECTORS = {
    "identity.species_scientific_name": "species_scientific_name",
    "identity.strain_code": "strain_code",
    "identity.global_strain_id": "global_strain_id",
}
OBSERVATION_TIME = "observation.time"
OBSERVATION_VALUE_SELECTORS = {
    "observation.polyp_count": "polyp_count",
    "observation.ephyrae_count": "ephyrae_count",
    "observation.strobila_count": "strobila_count",
}
OBSERVATION_PROVENANCE_SELECTORS = {
    # origin also carries import_format, whose meaning depends on origin.
    "observation.origin": ("origin", "import_format"),
    "observation.observer_status": ("observer_status",),
}
SELECTORS = frozenset({
    *SOURCE_SELECTORS, *IDENTITY_SELECTORS, OBSERVATION_TIME,
    *OBSERVATION_VALUE_SELECTORS, *OBSERVATION_PROVENANCE_SELECTORS,
})

# value: a known value, including 0. null: explicitly unknown or not
# established (for example strobilae not measured). blank: an empty source
# cell, kept for file adapters. absent: the source did not supply the field.
# An undisclosed field is not a state: its key is missing from the payload.
FIELD_STATES = frozenset({"value", "null", "blank", "absent"})
ORIGINS = frozenset({"direct_entry", "file_import", "legacy_unclassified"})
IMPORT_FORMATS = frozenset({"csv"})
OBSERVER_STATUSES = frozenset({"known", "not_recorded", "legacy_unestablished"})
TOP_LEVEL_KEYS = frozenset({"schema", "schema_version", "disclosed", "source", "identity", "observations"})
TIME_KEYS = frozenset({"precision", "iso_year", "iso_week", "measured_on"})
ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


@dataclass(frozen=True)
class Diagnostic:
    code: str
    path: str = ""


class SnapshotError(ValueError):
    def __init__(self, diagnostics):
        self.diagnostics = tuple(diagnostics)
        super().__init__(", ".join(f"{item.path}:{item.code}" for item in self.diagnostics))


@dataclass(frozen=True)
class FieldValue:
    state: str
    value: object = None

    def __post_init__(self):
        if self.state not in FIELD_STATES:
            raise ValueError("Unsupported field state.")
        if self.state != "value" and self.value is not None:
            raise ValueError("Only the value state carries a value.")

    @classmethod
    def of(cls, value):
        """Map a stored scalar: None is an explicit NULL, never zero or blank."""
        return cls("null") if value is None else cls("value", value)

    @classmethod
    def of_text(cls, value):
        """Map stored text: an empty stored string is blank, not NULL."""
        if value == "":
            return cls("blank")
        return cls.of(value)

    def to_dict(self):
        return {"state": "value", "value": self.value} if self.state == "value" else {"state": self.state}


@dataclass(frozen=True)
class Disclosure:
    selectors: frozenset

    def __contains__(self, selector):
        return selector in self.selectors

    def any_of(self, selectors):
        return not self.selectors.isdisjoint(selectors)


def select_disclosure(requested, *, required=frozenset()):
    """Validate an explicit selection against the catalogue before any read.

    `required` is a server-side workflow requirement, never client input.
    Nothing is included implicitly, including future catalogue additions.
    """
    required = frozenset(required)
    if not required <= SELECTORS:
        raise ValueError("Workflow requirements must use catalogue selectors.")
    if isinstance(requested, (str, bytes, Mapping)) or not isinstance(requested, (list, tuple, set, frozenset)):
        raise SnapshotError([Diagnostic("selection_list_required", "disclosed")])
    diagnostics = []
    seen = set()
    for index, selector in enumerate(requested):
        path = f"disclosed[{index}]"
        if not isinstance(selector, str):
            diagnostics.append(Diagnostic("selector_string_required", path))
        elif selector not in SELECTORS:
            diagnostics.append(Diagnostic("unknown_selector", path))
        elif selector in seen:
            diagnostics.append(Diagnostic("duplicate_selector", path))
        else:
            seen.add(selector)
    if not requested:
        diagnostics.append(Diagnostic("empty_selection", "disclosed"))
    if diagnostics:
        raise SnapshotError(diagnostics)
    observation_details = set(OBSERVATION_VALUE_SELECTORS) | set(OBSERVATION_PROVENANCE_SELECTORS)
    if seen & observation_details and OBSERVATION_TIME not in seen:
        # An observation value without its time would be scientifically unusable.
        diagnostics.append(Diagnostic("observation_time_required", "disclosed"))
    for selector in sorted(required - seen):
        diagnostics.append(Diagnostic("required_selector_missing", selector))
    if diagnostics:
        raise SnapshotError(diagnostics)
    return Disclosure(frozenset(seen))


@dataclass(frozen=True)
class ObservationRecord:
    """One observation. `fields` holds disclosed entries only, by selector."""

    time: ObservationTime
    fields: Mapping[str, object]


def _time_to_dict(time):
    return {
        "precision": time.precision.value,
        "iso_year": time.iso_year,
        "iso_week": time.iso_week,
        # A week-only observation has no day; its Monday is never emitted.
        "measured_on": None if time.measured_on is None else time.measured_on.isoformat(),
    }


def _section(disclosure, catalogue, values, path):
    selected = {selector for selector in catalogue if selector in disclosure}
    supplied = set(values)
    if supplied - selected:
        raise ValueError(f"{path}: values were supplied for undisclosed selectors.")
    if selected - supplied:
        raise ValueError(f"{path}: disclosed selectors have no value.")
    return {catalogue[selector]: values[selector].to_dict() for selector in sorted(selected)}


def build_snapshot(disclosure, *, source=None, identity=None, observations=None):
    """Assemble only disclosed content, then validate the result.

    Producers must supply exactly the disclosed entries, so filtering happens
    before serialization and an undisclosed value can never reach the payload.
    """
    payload = {"schema": SCHEMA, "schema_version": VERSION, "disclosed": sorted(disclosure.selectors)}
    if disclosure.any_of(SOURCE_SELECTORS):
        payload["source"] = _section(disclosure, SOURCE_SELECTORS, source or {}, "source")
    elif source:
        raise ValueError("source: values were supplied for undisclosed selectors.")
    if disclosure.any_of(IDENTITY_SELECTORS):
        payload["identity"] = _section(disclosure, IDENTITY_SELECTORS, identity or {}, "identity")
    elif identity:
        raise ValueError("identity: values were supplied for undisclosed selectors.")
    if OBSERVATION_TIME in disclosure:
        if observations is None:
            raise ValueError("observations: the disclosed observation list is missing.")
        payload["observations"] = [_observation(disclosure, record) for record in observations]
    elif observations is not None:
        raise ValueError("observations: observations were supplied but not disclosed.")
    diagnostics = validate_snapshot(payload)
    if diagnostics:
        raise SnapshotError(diagnostics)
    return payload


def _observation(disclosure, record):
    entry = {"time": _time_to_dict(record.time)}
    values = {key: value for key, value in record.fields.items() if key in OBSERVATION_VALUE_SELECTORS}
    provenance = {key: value for key, value in record.fields.items() if key in OBSERVATION_PROVENANCE_SELECTORS}
    unknown = set(record.fields) - set(values) - set(provenance)
    if unknown:
        raise ValueError("observations: unknown observation selectors were supplied.")
    if disclosure.any_of(OBSERVATION_VALUE_SELECTORS):
        entry["values"] = _section(disclosure, OBSERVATION_VALUE_SELECTORS, values, "observation values")
    elif values:
        raise ValueError("observation values: values were supplied for undisclosed selectors.")
    selected = {selector for selector in OBSERVATION_PROVENANCE_SELECTORS if selector in disclosure}
    if set(provenance) != selected:
        raise ValueError("observation provenance: supplied entries must match the disclosure.")
    if selected:
        entry["provenance"] = {}
        for selector in sorted(selected):
            supplied = provenance[selector]
            keys = OBSERVATION_PROVENANCE_SELECTORS[selector]
            if set(supplied) != set(keys):
                raise ValueError("observation provenance: incomplete provenance entry.")
            for key in keys:
                entry["provenance"][key] = supplied[key].to_dict()
    return entry


def canonical_json(payload):
    """Deterministic encoding: identical content always yields identical text."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# Validation of a received or produced payload ---------------------------------


def _is_choice(choices):
    # Check the JSON type first: lists or objects are unhashable in a set test.
    return lambda value: isinstance(value, str) and value in choices


def _is_count(value):
    return type(value) is int and 0 <= value <= MAX_COUNT


def _is_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _text(limit):
    # Stored identifiers are kept exactly; an empty one is the blank state.
    return lambda value: isinstance(value, str) and 0 < len(value) <= limit


FIELD_VALIDATORS = {
    "institution_portable_id": _is_uuid,
    "institution_name": _text(150),
    "box_code": _text(100),
    "species_scientific_name": _text(150),
    "strain_code": _text(80),
    "global_strain_id": _is_uuid,
    "polyp_count": _is_count,
    "ephyrae_count": _is_count,
    "strobila_count": _is_count,
    "origin": _is_choice(ORIGINS),
    "import_format": _is_choice(IMPORT_FORMATS),
    "observer_status": _is_choice(OBSERVER_STATUSES),
}


def _validate_field(name, entry, path, diagnostics):
    if not isinstance(entry, dict) or "state" not in entry:
        diagnostics.append(Diagnostic("field_state_required", path))
        return None
    state = entry["state"]
    if not _is_choice(FIELD_STATES)(state):
        diagnostics.append(Diagnostic("unknown_field_state", path))
        return None
    expected = {"state", "value"} if state == "value" else {"state"}
    if set(entry) != expected:
        diagnostics.append(Diagnostic("field_shape", path))
        return None
    if state == "value" and not FIELD_VALIDATORS[name](entry["value"]):
        diagnostics.append(Diagnostic("invalid_value", path))
        return None
    return state


def _validate_section(payload, key, catalogue, selected, diagnostics):
    expected = {catalogue[selector] for selector in catalogue if selector in selected}
    if not expected:
        if key in payload:
            diagnostics.append(Diagnostic("undisclosed_section", key))
        return
    section = payload.get(key)
    if not isinstance(section, dict):
        diagnostics.append(Diagnostic("disclosed_section_missing", key))
        return
    for name in sorted(set(section) - expected):
        diagnostics.append(Diagnostic("undisclosed_field", f"{key}.{name}"))
    for name in sorted(expected - set(section)):
        diagnostics.append(Diagnostic("disclosed_field_missing", f"{key}.{name}"))
    for name in sorted(expected & set(section)):
        _validate_field(name, section[name], f"{key}.{name}", diagnostics)


def _validate_time(time, path, diagnostics):
    if not isinstance(time, dict) or set(time) != TIME_KEYS:
        diagnostics.append(Diagnostic("time_shape", path))
        return None
    year, week = time["iso_year"], time["iso_week"]
    if type(year) is not int or type(week) is not int:
        diagnostics.append(Diagnostic("iso_week_required", path))
        return None
    try:
        if not isinstance(time["precision"], str):
            raise ValueError
        precision = ObservationPrecision(time["precision"])
    except ValueError:
        diagnostics.append(Diagnostic("unknown_precision", f"{path}.precision"))
        return None
    if precision == ObservationPrecision.ISO_WEEK:
        if time["measured_on"] is not None:
            diagnostics.append(Diagnostic("week_only_has_no_day", f"{path}.measured_on"))
            return None
        try:
            return ObservationTime.for_iso_week(year, week)
        except ValueError:
            diagnostics.append(Diagnostic("invalid_iso_week", path))
            return None
    raw = time["measured_on"]
    if not isinstance(raw, str) or not ISO_DATE.fullmatch(raw):
        diagnostics.append(Diagnostic("iso_date_required", f"{path}.measured_on"))
        return None
    try:
        measured_on = date.fromisoformat(raw)
    except ValueError:
        diagnostics.append(Diagnostic("iso_date_required", f"{path}.measured_on"))
        return None
    iso = measured_on.isocalendar()
    if (iso.year, iso.week) != (year, week):
        diagnostics.append(Diagnostic("date_week_mismatch", path))
        return None
    return ObservationTime(precision, measured_on, date.fromisocalendar(year, week, 1))


def _validate_observation(entry, index, selected, diagnostics):
    path = f"observations[{index}]"
    value_names = {OBSERVATION_VALUE_SELECTORS[s] for s in OBSERVATION_VALUE_SELECTORS if s in selected}
    provenance_names = {
        name for selector, names in OBSERVATION_PROVENANCE_SELECTORS.items() if selector in selected
        for name in names
    }
    expected_keys = {"time"} | ({"values"} if value_names else set()) | ({"provenance"} if provenance_names else set())
    if not isinstance(entry, dict):
        diagnostics.append(Diagnostic("observation_shape", path))
        return None
    for key in sorted(set(entry) - expected_keys):
        diagnostics.append(Diagnostic("undisclosed_field", f"{path}.{key}"))
    for key in sorted(expected_keys - set(entry)):
        diagnostics.append(Diagnostic("disclosed_field_missing", f"{path}.{key}"))
    time = _validate_time(entry.get("time"), f"{path}.time", diagnostics) if "time" in entry else None
    for key, names in (("values", value_names), ("provenance", provenance_names)):
        if key not in entry or not names:
            continue
        group = entry[key]
        if not isinstance(group, dict):
            diagnostics.append(Diagnostic("observation_shape", f"{path}.{key}"))
            continue
        for name in sorted(set(group) - names):
            diagnostics.append(Diagnostic("undisclosed_field", f"{path}.{key}.{name}"))
        for name in sorted(names - set(group)):
            diagnostics.append(Diagnostic("disclosed_field_missing", f"{path}.{key}.{name}"))
        states = {
            name: _validate_field(name, group[name], f"{path}.{key}.{name}", diagnostics)
            for name in sorted(names & set(group))
        }
        if key == "provenance" and "origin" in states and "import_format" in states:
            # Mirrors the stored constraint: a format exists only for file imports.
            origin = group["origin"].get("value") if states["origin"] == "value" else None
            if origin == "file_import" and states["import_format"] != "value":
                diagnostics.append(Diagnostic("import_format_required", f"{path}.provenance.import_format"))
            if origin in {"direct_entry", "legacy_unclassified"} and states["import_format"] != "null":
                diagnostics.append(Diagnostic("import_format_not_applicable", f"{path}.provenance.import_format"))
    return time


def validate_snapshot(payload):
    """Return structured diagnostics; an empty tuple means a valid 1.0 snapshot."""
    if not isinstance(payload, dict):
        return (Diagnostic("snapshot_object_required"),)
    diagnostics = []
    if payload.get("schema") != SCHEMA:
        diagnostics.append(Diagnostic("unsupported_schema", "schema"))
    if not _is_choice(SUPPORTED_VERSIONS)(payload.get("schema_version")):
        diagnostics.append(Diagnostic("unsupported_schema_version", "schema_version"))
    if diagnostics:
        return tuple(diagnostics)  # Never interpret content of an unknown version.
    for key in sorted(set(payload) - TOP_LEVEL_KEYS):
        diagnostics.append(Diagnostic("unknown_key", key))
    disclosed = payload.get("disclosed")
    try:
        selection = select_disclosure(disclosed)
    except SnapshotError as error:
        return tuple(diagnostics) + error.diagnostics
    if disclosed != sorted(selection.selectors):
        diagnostics.append(Diagnostic("disclosed_not_canonical", "disclosed"))
    selected = selection.selectors
    _validate_section(payload, "source", SOURCE_SELECTORS, selected, diagnostics)
    _validate_section(payload, "identity", IDENTITY_SELECTORS, selected, diagnostics)
    if OBSERVATION_TIME not in selected:
        if "observations" in payload:
            diagnostics.append(Diagnostic("undisclosed_section", "observations"))
        return tuple(diagnostics)
    observations = payload.get("observations")
    if not isinstance(observations, list):
        diagnostics.append(Diagnostic("disclosed_section_missing", "observations"))
        return tuple(diagnostics)
    previous = None
    for index, entry in enumerate(observations):
        time = _validate_observation(entry, index, selected, diagnostics)
        if time is None:
            continue
        key = (time.iso_year, time.iso_week)
        # One snapshot describes one Box, which holds one canonical
        # observation per ISO week; order is by ISO week only.
        if previous is not None and key == previous:
            diagnostics.append(Diagnostic("duplicate_observation_week", f"observations[{index}]"))
        elif previous is not None and key < previous:
            diagnostics.append(Diagnostic("observations_not_ordered", f"observations[{index}]"))
        previous = key
    return tuple(diagnostics)
