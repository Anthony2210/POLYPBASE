from copy import deepcopy
from datetime import date
import json

from django.test import SimpleTestCase

from .scientific_snapshot import (
    SCHEMA,
    SELECTORS,
    VERSION,
    Diagnostic,
    Disclosure,
    FieldValue,
    ObservationRecord,
    SnapshotError,
    build_snapshot,
    canonical_json,
    select_disclosure,
    validate_snapshot,
)
from .scientific_time import ObservationPrecision, ObservationTime

ALL = sorted(SELECTORS)
UUID_TEXT = "1b4e28ba-2fa1-11d2-883f-0016d3cca427"


def exact(day):
    return ObservationTime(ObservationPrecision.EXACT_DATE, day, day.fromordinal(day.toordinal() - day.weekday()))


def codes(diagnostics):
    return {item.code for item in diagnostics}


def full_snapshot(observations=None):
    disclosure = select_disclosure(ALL)
    if observations is None:
        observations = [ObservationRecord(exact(date(2026, 4, 15)), {
            "observation.polyp_count": FieldValue.of(0),
            "observation.ephyrae_count": FieldValue.of(0),
            "observation.strobila_count": FieldValue.of(None),
            "observation.origin": {"origin": FieldValue.of("direct_entry"), "import_format": FieldValue.of(None)},
            "observation.observer_status": {"observer_status": FieldValue.of("known")},
        })]
    return build_snapshot(
        disclosure,
        source={
            "source.institution_portable_id": FieldValue.of(UUID_TEXT),
            "source.institution_name": FieldValue.of("Synthetic lab"),
            "source.box_code": FieldValue.of("SYN-AAA-1.001"),
        },
        identity={
            "identity.species_scientific_name": FieldValue.of("Aurelia synthetica"),
            "identity.strain_code": FieldValue.of("SYN-AAA-1"),
            "identity.global_strain_id": FieldValue.of(None),
        },
        observations=observations,
    )


class DisclosureSelectionTests(SimpleTestCase):
    def assert_rejected(self, requested, code, **kwargs):
        with self.assertRaises(SnapshotError) as raised:
            select_disclosure(requested, **kwargs)
        self.assertIn(code, codes(raised.exception.diagnostics))

    def test_unknown_selectors_orm_paths_and_json_keys_are_rejected(self):
        for selector in (
            "box__organization__name", "box.notes", "observation.notes", "observation.user",
            "observations", "source", "observation.temperature", "polyp_count", "Observation.polyp_count",
        ):
            with self.subTest(selector=selector):
                self.assert_rejected([selector], "unknown_selector")

    def test_selection_shape_is_strict(self):
        self.assert_rejected("observation.time", "selection_list_required")
        self.assert_rejected({"observation.time": True}, "selection_list_required")
        self.assert_rejected(None, "selection_list_required")
        self.assert_rejected([], "empty_selection")
        self.assert_rejected([1], "selector_string_required")
        self.assert_rejected(["observation.time", "observation.time"], "duplicate_selector")

    def test_observation_details_require_time(self):
        self.assert_rejected(["observation.polyp_count"], "observation_time_required")
        self.assert_rejected(["observation.origin"], "observation_time_required")
        self.assertEqual(
            select_disclosure(["observation.time", "observation.polyp_count"]).selectors,
            frozenset({"observation.time", "observation.polyp_count"}),
        )

    def test_workflow_requirements_are_enforced_but_never_added(self):
        self.assert_rejected(["observation.time"], "required_selector_missing", required={"source.box_code"})
        selection = select_disclosure(["source.box_code"], required={"source.box_code"})
        self.assertEqual(selection.selectors, frozenset({"source.box_code"}))
        with self.assertRaises(ValueError):
            select_disclosure(["source.box_code"], required={"box.notes"})


class SnapshotBuildTests(SimpleTestCase):
    def test_zero_null_blank_absent_are_distinct_states(self):
        record = ObservationRecord(exact(date(2026, 4, 13)), {
            "observation.polyp_count": FieldValue.of(0),
            "observation.ephyrae_count": FieldValue("blank"),
            "observation.strobila_count": FieldValue.of(None),
        })
        second = ObservationRecord(exact(date(2026, 4, 20)), {
            "observation.polyp_count": FieldValue("absent"),
            "observation.ephyrae_count": FieldValue.of(0),
            "observation.strobila_count": FieldValue.of(0),
        })
        snapshot = build_snapshot(
            select_disclosure([
                "observation.time", "observation.polyp_count", "observation.ephyrae_count",
                "observation.strobila_count",
            ]),
            observations=[record, second],
        )
        first_values, second_values = (item["values"] for item in snapshot["observations"])
        self.assertEqual(first_values["polyp_count"], {"state": "value", "value": 0})
        self.assertEqual(first_values["ephyrae_count"], {"state": "blank"})
        self.assertEqual(first_values["strobila_count"], {"state": "null"})
        self.assertEqual(second_values["polyp_count"], {"state": "absent"})
        self.assertEqual(second_values["strobila_count"], {"state": "value", "value": 0})
        # The JSON round trip keeps zero as an integer and NULL distinct.
        decoded = json.loads(canonical_json(snapshot))
        self.assertEqual(decoded, snapshot)
        self.assertEqual(validate_snapshot(decoded), ())

    def test_no_observation_and_undisclosed_observations_differ(self):
        empty = build_snapshot(select_disclosure(["observation.time"]), observations=[])
        self.assertEqual(empty["observations"], [])
        undisclosed = build_snapshot(
            select_disclosure(["source.box_code"]), source={"source.box_code": FieldValue.of("SYN.1")},
        )
        self.assertNotIn("observations", undisclosed)
        self.assertNotIn("identity", undisclosed)

    def test_excluded_values_cannot_reach_the_payload(self):
        disclosure = select_disclosure(["observation.time", "observation.polyp_count"])
        record = ObservationRecord(exact(date(2026, 4, 13)), {
            "observation.polyp_count": FieldValue.of(4),
            "observation.ephyrae_count": FieldValue.of(9),
        })
        with self.assertRaises(ValueError):
            build_snapshot(disclosure, observations=[record])
        with self.assertRaises(ValueError):
            build_snapshot(
                select_disclosure(["source.box_code"]),
                source={"source.box_code": FieldValue.of("SYN.1"), "source.institution_name": FieldValue.of("X")},
            )
        with self.assertRaises(ValueError):
            build_snapshot(select_disclosure(["source.box_code"]), source={})
        with self.assertRaises(ValueError):
            build_snapshot(
                select_disclosure(["source.box_code"]),
                source={"source.box_code": FieldValue.of("SYN.1")}, observations=[],
            )

    def test_only_disclosed_keys_are_serialized(self):
        disclosure = select_disclosure(["observation.time", "observation.ephyrae_count"])
        snapshot = build_snapshot(disclosure, observations=[ObservationRecord(
            exact(date(2026, 4, 13)), {"observation.ephyrae_count": FieldValue.of(3)},
        )])
        self.assertEqual(set(snapshot), {"schema", "schema_version", "disclosed", "observations"})
        self.assertEqual(snapshot["disclosed"], ["observation.ephyrae_count", "observation.time"])
        self.assertEqual(set(snapshot["observations"][0]), {"time", "values"})
        self.assertEqual(snapshot["observations"][0]["values"], {"ephyrae_count": {"state": "value", "value": 3}})

    def test_serialization_is_deterministic(self):
        first = full_snapshot()
        reordered = json.loads(json.dumps(first, sort_keys=False))
        reordered = dict(reversed(list(reordered.items())))
        self.assertEqual(canonical_json(first), canonical_json(reordered))
        self.assertEqual(canonical_json(first), canonical_json(full_snapshot()))
        self.assertNotIn("generated", canonical_json(first))

    def test_week_only_time_never_fabricates_a_day(self):
        snapshot = build_snapshot(
            select_disclosure(["observation.time"]),
            observations=[ObservationRecord(ObservationTime.for_iso_week(2020, 53), {})],
        )
        self.assertEqual(snapshot["observations"][0]["time"], {
            "precision": "iso_week", "iso_year": 2020, "iso_week": 53, "measured_on": None,
        })
        self.assertNotIn("2020-12-28", canonical_json(snapshot))
        self.assertNotIn("week_start", canonical_json(snapshot))

    def test_legacy_precision_is_kept_and_not_upgraded(self):
        legacy = ObservationTime(ObservationPrecision.LEGACY_UNVERIFIED, date(2021, 1, 4), date(2021, 1, 4))
        snapshot = build_snapshot(
            select_disclosure(["observation.time"]), observations=[ObservationRecord(legacy, {})],
        )
        self.assertEqual(snapshot["observations"][0]["time"]["precision"], "legacy_unverified")
        self.assertEqual(snapshot["observations"][0]["time"]["measured_on"], "2021-01-04")


class SnapshotValidationTests(SimpleTestCase):
    def setUp(self):
        self.snapshot = full_snapshot()

    def assert_invalid(self, payload, code):
        self.assertIn(code, codes(validate_snapshot(payload)), validate_snapshot(payload))

    def test_valid_snapshot_has_no_diagnostics(self):
        self.assertEqual(validate_snapshot(self.snapshot), ())
        self.assertEqual(self.snapshot["schema"], SCHEMA)
        self.assertEqual(self.snapshot["schema_version"], VERSION)

    def test_schema_and_version_must_match_exactly(self):
        for version in ("1.1", "2.0", "1", 1.0, None):
            with self.subTest(version=version):
                payload = deepcopy(self.snapshot)
                payload["schema_version"] = version
                self.assertEqual(validate_snapshot(payload), (
                    Diagnostic("unsupported_schema_version", "schema_version"),
                ))
        payload = deepcopy(self.snapshot)
        payload["schema"] = "polypbase.transfer"
        self.assert_invalid(payload, "unsupported_schema")
        self.assertEqual(codes(validate_snapshot([])), {"snapshot_object_required"})

    def test_unknown_and_undisclosed_keys_are_rejected(self):
        payload = deepcopy(self.snapshot)
        payload["notes"] = "free text"
        self.assert_invalid(payload, "unknown_key")

        payload = deepcopy(self.snapshot)
        payload["disclosed"].remove("identity.global_strain_id")
        self.assert_invalid(payload, "undisclosed_field")

        payload = deepcopy(self.snapshot)
        payload["observations"][0]["values"]["salinity_psu"] = {"state": "value", "value": "31.00"}
        self.assert_invalid(payload, "undisclosed_field")

        payload = deepcopy(self.snapshot)
        payload["observations"][0]["observer"] = {"state": "value", "value": "someone"}
        self.assert_invalid(payload, "undisclosed_field")

        payload = deepcopy(self.snapshot)
        payload["disclosed"] = ["observation.time"]
        self.assert_invalid(payload, "undisclosed_section")

    def test_disclosed_content_must_be_present(self):
        payload = deepcopy(self.snapshot)
        del payload["identity"]["strain_code"]
        self.assert_invalid(payload, "disclosed_field_missing")
        payload = deepcopy(self.snapshot)
        del payload["observations"]
        self.assert_invalid(payload, "disclosed_section_missing")

    def test_disclosed_selection_is_validated_and_canonical(self):
        payload = deepcopy(self.snapshot)
        payload["disclosed"].append("box__notes")
        self.assert_invalid(payload, "unknown_selector")
        payload = deepcopy(self.snapshot)
        payload["disclosed"] = list(reversed(payload["disclosed"]))
        self.assert_invalid(payload, "disclosed_not_canonical")

    def test_field_values_are_strictly_typed(self):
        for value in (True, -1, 1.0, "3", 2147483648):
            with self.subTest(value=value):
                payload = deepcopy(self.snapshot)
                payload["observations"][0]["values"]["polyp_count"] = {"state": "value", "value": value}
                self.assert_invalid(payload, "invalid_value")
        payload = deepcopy(self.snapshot)
        payload["observations"][0]["values"]["polyp_count"] = {"state": "null", "value": 0}
        self.assert_invalid(payload, "field_shape")
        payload = deepcopy(self.snapshot)
        payload["observations"][0]["values"]["polyp_count"] = {"state": "zero"}
        self.assert_invalid(payload, "unknown_field_state")
        payload = deepcopy(self.snapshot)
        payload["observations"][0]["values"]["polyp_count"] = 0
        self.assert_invalid(payload, "field_state_required")
        payload = deepcopy(self.snapshot)
        payload["identity"]["global_strain_id"] = {"state": "value", "value": "not-a-uuid"}
        self.assert_invalid(payload, "invalid_value")
        payload = deepcopy(self.snapshot)
        payload["source"]["box_code"] = {"state": "value", "value": ""}
        self.assert_invalid(payload, "invalid_value")

    def test_import_format_follows_origin(self):
        payload = deepcopy(self.snapshot)
        payload["observations"][0]["provenance"]["origin"] = {"state": "value", "value": "file_import"}
        self.assert_invalid(payload, "import_format_required")
        payload["observations"][0]["provenance"]["import_format"] = {"state": "value", "value": "csv"}
        self.assertEqual(validate_snapshot(payload), ())
        payload["observations"][0]["provenance"]["origin"] = {"state": "value", "value": "direct_entry"}
        self.assert_invalid(payload, "import_format_not_applicable")

    def test_temporal_precision_rules(self):
        def with_time(**time):
            payload = deepcopy(self.snapshot)
            payload["observations"][0]["time"] = {
                "precision": "exact_date", "iso_year": 2026, "iso_week": 16, "measured_on": "2026-04-15", **time,
            }
            return payload

        self.assertEqual(validate_snapshot(with_time()), ())
        self.assert_invalid(with_time(iso_week=17), "date_week_mismatch")
        self.assert_invalid(with_time(measured_on=None), "iso_date_required")
        self.assert_invalid(with_time(measured_on="2026-02-30"), "iso_date_required")
        self.assert_invalid(with_time(measured_on="15/04/2026"), "iso_date_required")
        self.assert_invalid(with_time(precision="approximate"), "unknown_precision")
        self.assert_invalid(with_time(iso_week=True), "iso_week_required")
        self.assert_invalid(with_time(precision="iso_week"), "week_only_has_no_day")
        self.assertEqual(validate_snapshot(with_time(precision="iso_week", measured_on=None)), ())
        # 2020 has an ISO week 53; 2021 does not.
        self.assertEqual(validate_snapshot(with_time(
            precision="iso_week", measured_on=None, iso_year=2020, iso_week=53,
        )), ())
        self.assert_invalid(with_time(
            precision="iso_week", measured_on=None, iso_year=2021, iso_week=53,
        ), "invalid_iso_week")
        payload = with_time()
        payload["observations"][0]["time"]["week_start"] = "2026-04-13"
        self.assert_invalid(payload, "time_shape")

    def test_observations_are_ordered_and_unique_per_iso_week(self):
        def record(day):
            return ObservationRecord(exact(day), {
                "observation.polyp_count": FieldValue.of(1),
                "observation.ephyrae_count": FieldValue.of(1),
                "observation.strobila_count": FieldValue.of(1),
                "observation.origin": {"origin": FieldValue.of("legacy_unclassified"), "import_format": FieldValue.of(None)},
                "observation.observer_status": {"observer_status": FieldValue.of("legacy_unestablished")},
            })

        with self.assertRaises(SnapshotError) as raised:
            full_snapshot([record(date(2026, 4, 13)), record(date(2026, 4, 17))])
        self.assertIn("duplicate_observation_week", codes(raised.exception.diagnostics))
        with self.assertRaises(SnapshotError) as raised:
            full_snapshot([record(date(2026, 4, 20)), record(date(2026, 4, 13))])
        self.assertIn("observations_not_ordered", codes(raised.exception.diagnostics))
        # ISO year boundary: 2021-01-03 is in ISO week 2020-W53.
        snapshot = full_snapshot([record(date(2021, 1, 3)), record(date(2021, 1, 4))])
        self.assertEqual(
            [(item["time"]["iso_year"], item["time"]["iso_week"]) for item in snapshot["observations"]],
            [(2020, 53), (2021, 1)],
        )

    def test_field_value_rejects_ambiguous_states(self):
        with self.assertRaises(ValueError):
            FieldValue("null", 0)
        with self.assertRaises(ValueError):
            FieldValue("missing")
        self.assertEqual(FieldValue.of(0), FieldValue("value", 0))
        self.assertEqual(FieldValue.of_text(""), FieldValue("blank"))
        self.assertEqual(FieldValue.of_text(None), FieldValue("null"))
        self.assertIsInstance(select_disclosure(["observation.time"]), Disclosure)


class MalformedJsonValidationTests(SimpleTestCase):
    """Unhashable JSON values must yield diagnostics, never an uncaught TypeError."""

    def setUp(self):
        self.snapshot = full_snapshot()

    def assert_diagnosed(self, payload, code):
        diagnostics = validate_snapshot(payload)
        self.assertIn(code, codes(diagnostics), diagnostics)

    def test_list_or_object_in_enum_positions_is_diagnosed(self):
        observation = ("observations", 0)
        cases = (
            (observation + ("values", "polyp_count", "state"), "unknown_field_state"),
            (observation + ("provenance", "origin", "state"), "unknown_field_state"),
            (observation + ("provenance", "origin", "value"), "invalid_value"),
            (observation + ("provenance", "import_format", "state"), "unknown_field_state"),
            (observation + ("provenance", "observer_status", "value"), "invalid_value"),
            (observation + ("time", "precision"), "unknown_precision"),
            (("source", "box_code", "state"), "unknown_field_state"),
            (("identity", "global_strain_id", "value"), "field_shape"),
        )
        for malformed in ([], ["value"], {}, {"value": "known"}):
            for path, code in cases:
                with self.subTest(path=path, malformed=malformed):
                    payload = deepcopy(self.snapshot)
                    target = payload
                    for key in path[:-1]:
                        target = target[key]
                    target[path[-1]] = malformed
                    # global_strain_id is in the null state: an added value is a shape error.
                    self.assert_diagnosed(payload, code)

    def test_valid_origin_with_malformed_import_format_value(self):
        payload = deepcopy(self.snapshot)
        payload["observations"][0]["provenance"]["origin"] = {"state": "value", "value": "file_import"}
        for malformed in ([], ["csv"], {"csv": True}):
            with self.subTest(malformed=malformed):
                payload["observations"][0]["provenance"]["import_format"] = {"state": "value", "value": malformed}
                self.assert_diagnosed(payload, "invalid_value")

    def test_list_or_object_in_header_positions_is_diagnosed(self):
        for malformed in ([], ["1.0"], {}, {"1.0": True}):
            with self.subTest(malformed=malformed):
                payload = deepcopy(self.snapshot)
                payload["schema_version"] = malformed
                self.assert_diagnosed(payload, "unsupported_schema_version")
                payload = deepcopy(self.snapshot)
                payload["schema"] = malformed
                self.assert_diagnosed(payload, "unsupported_schema")
                payload = deepcopy(self.snapshot)
                payload["disclosed"] = [malformed, *payload["disclosed"]]
                self.assert_diagnosed(payload, "selector_string_required")
                payload = deepcopy(self.snapshot)
                payload["disclosed"] = malformed
                self.assert_diagnosed(payload, "selection_list_required" if isinstance(malformed, dict)
                                      else ("empty_selection" if not malformed else "unknown_selector"))

    def test_every_position_rejects_list_and_object_without_exception(self):
        def positions(node, path=()):
            yield path
            if isinstance(node, dict):
                for key, child in node.items():
                    yield from positions(child, path + (key,))
            elif isinstance(node, list):
                for index, child in enumerate(node):
                    yield from positions(child, path + (index,))

        for path in list(positions(self.snapshot))[1:]:
            for malformed in ([], [[]], {}, {"state": []}):
                with self.subTest(path=path, malformed=malformed):
                    payload = deepcopy(self.snapshot)
                    target = payload
                    for key in path[:-1]:
                        target = target[key]
                    target[path[-1]] = deepcopy(malformed)
                    diagnostics = validate_snapshot(payload)  # Must not raise.
                    if path == ("observations",) and malformed == []:
                        self.assertEqual(diagnostics, ())  # A valid "no observation" list.
                    else:
                        self.assertNotEqual(diagnostics, ())

    def test_valid_zero_null_blank_absent_still_accepted(self):
        payload = deepcopy(self.snapshot)
        payload["observations"][0]["values"] = {
            "polyp_count": {"state": "value", "value": 0},
            "ephyrae_count": {"state": "blank"},
            "strobila_count": {"state": "null"},
        }
        payload["source"]["institution_name"] = {"state": "absent"}
        self.assertEqual(validate_snapshot(payload), ())
