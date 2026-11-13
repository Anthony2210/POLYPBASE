"""Pure preview input tests: synthetic typed drafts, no database access."""

from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from .effective_history import (
    UNSET,
    AdjustEventKind,
    AdjustEventTemporal,
    AdjustEventZones,
    EventKind,
    EventTarget,
    InsertEvent,
    ModifyEvent,
    ReconstructionAnchor,
    RejectedOperation,
    RejectionCode,
    TemporalPosition,
    TemporalPrecision,
    anchor_key,
    draft_key,
    evidence_fingerprint,
)
from .history_preview_validation import (
    MAX_ANCHOR_ID_LENGTH,
    MAX_ANCHORS,
    MAX_DRAFT_ID_LENGTH,
    MAX_JUSTIFICATION_LENGTH,
    MAX_JUSTIFICATIONS,
    MAX_KEY_LENGTH,
    MAX_LOCATOR_ITEMS,
    MAX_OPERATIONS,
    OperationJustification,
    PreviewInputError,
    validate_draft,
    validate_scope_ids,
    validate_window,
)

PARIS = ZoneInfo("Europe/Paris")
START = datetime(2026, 3, 1, tzinfo=UTC)
FINGERPRINT = evidence_fingerprint({"reading_id": 12})
TARGET = EventTarget("movement:12")


def insertion(**changes):
    return replace(InsertEvent("new-1", EventKind.MOVEMENT, TemporalPosition.exact(START), 1, 2), **changes)


def anchor(**changes):
    return replace(ReconstructionAnchor(
        "reconstruction-1", 1, 10, FINGERPRINT,
        (("policy", "synthetic-v1"), ("origin_reading_id", 12), ("destination_reading_id", 13)),
    ), **changes)


def unchecked_temporal(**changes):
    """Model malformed typed values without relying on constructor checks."""
    value = TemporalPosition.unknown()
    for name, item in changes.items():
        object.__setattr__(value, name, item)
    return value


def draft(operations=(), anchors=(), justifications=(), **scope):
    return validate_draft(operations, anchors, justifications, **({"organization_id": 1, "box_id": 10} | scope))


class PreviewValidationAssertions(SimpleTestCase):
    def assert_input_error(self, code, function, *args, index=None, **kwargs):
        with self.assertRaises(PreviewInputError) as caught:
            function(*args, **kwargs)
        error = caught.exception
        self.assertEqual(error.code, code)
        self.assertEqual(str(error), code)
        self.assertEqual(error.operation_index, index)
        self.assertIsInstance(error.rejections, tuple)
        return error


class PreviewErrorTests(PreviewValidationAssertions):
    def test_error_has_safe_code_and_preserves_structured_rejections(self):
        rejection = RejectedOperation(3, RejectionCode.INVALID_SHAPE, {"reason": "private source detail"})
        error = PreviewInputError("draft_rejected", operation_index=3, rejections=[rejection])
        self.assertIsInstance(error, ValueError)
        self.assertEqual((error.code, error.operation_index, error.rejections), ("draft_rejected", 3, (rejection,)))
        self.assertEqual(str(error), "draft_rejected")
        self.assertNotIn("private", repr(error))
        self.assertEqual(PreviewInputError("invalid_window").rejections, ())
        self.assertIsNone(PreviewInputError("invalid_window").operation_index)

    def test_arbitrary_error_text_is_never_exposed_as_a_message(self):
        for code in (None, 12, {}, "", "Sensitive payload\nsecret", "invalid<script>", "a" * 129):
            with self.subTest(code=code):
                error = PreviewInputError(code)
                self.assertEqual(error.code, "invalid_preview_input")
                self.assertEqual(str(error), "invalid_preview_input")

    def test_justification_is_frozen(self):
        justification = OperationJustification(0, "Source record")
        with self.assertRaises(FrozenInstanceError):
            justification.__setattr__("reason", "Changed")


class PreviewWindowTests(PreviewValidationAssertions):
    def test_aware_window_is_normalized_to_utc(self):
        start = datetime(2026, 3, 1, tzinfo=PARIS)
        end = start + timedelta(days=1)
        result = validate_window(start, end)
        self.assertEqual(result, (start.astimezone(UTC), end.astimezone(UTC)))
        self.assertTrue(all(value.tzinfo is UTC for value in result))

    def test_window_uses_elapsed_time_across_both_dst_changes(self):
        for month, day, hours in ((3, 29, 23), (10, 25, 25)):
            with self.subTest(month=month):
                start = datetime(2026, month, day, tzinfo=PARIS)
                lower, upper = validate_window(start, start + timedelta(days=1))
                self.assertEqual(upper - lower, timedelta(hours=hours))

    def test_ambiguous_local_clock_window_uses_absolute_order(self):
        first = datetime(2026, 10, 25, 2, 30, tzinfo=PARIS, fold=0)
        second = first.replace(fold=1)
        lower, upper = validate_window(first, second)
        self.assertEqual(upper - lower, timedelta(hours=1))
        self.assert_input_error("invalid_window", validate_window, second, first)

    def test_nonexistent_paris_spring_time_is_rejected_at_either_window_boundary(self):
        lower = datetime(2026, 3, 28, tzinfo=UTC)
        upper = datetime(2026, 3, 30, tzinfo=UTC)
        for fold in (0, 1):
            gap = datetime(2026, 3, 29, 2, 30, tzinfo=PARIS, fold=fold)
            with self.subTest(fold=fold):
                self.assert_input_error("invalid_window", validate_window, lower, gap)
                self.assert_input_error("invalid_window", validate_window, gap, upper)

    def test_round_trip_preserves_utc_fixed_offsets_and_real_spring_times(self):
        for zone in (UTC, ZoneInfo("UTC"), timezone(timedelta(hours=2)),
                     timezone(-timedelta(hours=5, minutes=30)), timezone(timedelta(hours=5, minutes=45))):
            for fold in (0, 1):
                start = datetime(2026, 3, 29, 2, 30, tzinfo=zone, fold=fold)
                end = start + timedelta(hours=1)
                with self.subTest(zone=zone, fold=fold):
                    self.assertEqual(validate_window(start, end), (start.astimezone(UTC), end.astimezone(UTC)))
                    self.assertEqual(start.fold, fold)
        start = datetime(2026, 3, 29, 1, 59, 59, tzinfo=PARIS)
        end = datetime(2026, 3, 29, 3, tzinfo=PARIS)
        lower, upper = validate_window(start, end)
        self.assertEqual(upper - lower, timedelta(seconds=1))

    def test_exact_366_day_ceiling_and_microsecond_overflow(self):
        self.assertEqual(validate_window(START, START + timedelta(days=366)),
                         (START, START + timedelta(days=366)))
        self.assert_input_error("window_too_large", validate_window,
                                START, START + timedelta(days=366, microseconds=1))
        self.assert_input_error("window_too_large", validate_window, START, START + timedelta(days=367))

    def test_ceiling_is_checked_after_utc_conversion(self):
        start = datetime(2026, 7, 1, tzinfo=PARIS)
        end = (start + timedelta(days=366)).replace(hour=1)
        self.assert_input_error("window_too_large", validate_window, start, end)

    def test_bad_temporal_types_naive_empty_and_reversed_windows(self):
        invalid = (None, "2026-03-01", date(2026, 3, 1), 0, True, float("inf"), float("nan"),
                   datetime(2026, 3, 1), TemporalPosition.exact(START))
        for value in invalid:
            for pair in ((value, START), (START, value)):
                with self.subTest(pair=pair):
                    self.assert_input_error("invalid_window", validate_window, *pair)
        self.assert_input_error("invalid_window", validate_window, START, START)
        self.assert_input_error("invalid_window", validate_window, START, START - timedelta(microseconds=1))

    def test_timezone_conversion_overflow_and_invalid_offset_are_safe_errors(self):
        class InvalidOffset(tzinfo):
            def utcoffset(self, dt):
                return timedelta(days=2)

        for value in (
            datetime.min.replace(tzinfo=timezone(timedelta(hours=1))),
            datetime.max.replace(tzinfo=timezone(-timedelta(hours=1))),
            START.replace(tzinfo=InvalidOffset()),
        ):
            self.assert_input_error("invalid_window", validate_window, value, START)


class PreviewScopeAndSequenceTests(PreviewValidationAssertions):
    def test_scope_requires_positive_exact_integers(self):
        class IntSubclass(int):
            pass

        self.assertIsNone(validate_scope_ids(1, 10))
        for value in (None, 0, -1, True, False, 1.0, "1", IntSubclass(1), [], UNSET):
            for organization_id, box_id in ((value, 10), (1, value)):
                with self.subTest(organization_id=organization_id, box_id=box_id):
                    self.assert_input_error("invalid_scope", validate_scope_ids, organization_id, box_id)
                    self.assert_input_error("invalid_scope", draft, organization_id=organization_id, box_id=box_id)

    def test_empty_draft_and_mutable_outer_sequences_become_tuples(self):
        self.assertEqual(draft(), ((), (), ()))
        operation = ModifyEvent(TARGET)
        reason = OperationJustification(0, " Source record\n  original spacing ")
        operations, anchors, justifications = [operation], [anchor()], [reason]
        result = draft(operations, anchors, justifications)
        self.assertEqual(result, ((operation,), (anchor(),), (reason,)))
        self.assertIs(result[0][0], operation)
        self.assertIs(result[1][0], anchors[0])
        self.assertIs(result[2][0], reason)
        operations.clear()
        anchors.clear()
        justifications.clear()
        self.assertEqual(tuple(map(len, result)), (1, 1, 1))

    def test_only_exact_lists_and_tuples_not_mappings_or_generators(self):
        class ListSubclass(list):
            pass

        class TupleSubclass(tuple):
            pass

        def forbidden_generator():
            raise AssertionError("The validator must not consume a generator")
            yield None

        for name in ("operations", "anchors", "justifications"):
            for value in (None, {}, {"key": "value"}, "", set(), iter(()), forbidden_generator(),
                          ListSubclass(), TupleSubclass()):
                with self.subTest(name=name, value_type=type(value)):
                    self.assert_input_error(f"invalid_{name}", draft, **{name: value})

    def test_each_collection_has_a_technical_limit(self):
        operations = [ModifyEvent(TARGET)] * MAX_OPERATIONS
        anchors = [anchor(anchor_id=f"anchor-{index}") for index in range(MAX_ANCHORS)]
        justifications = [OperationJustification(index, "Source") for index in range(MAX_JUSTIFICATIONS)]
        result = draft(operations, anchors, justifications)
        self.assertEqual(tuple(map(len, result)), (MAX_OPERATIONS, MAX_ANCHORS, MAX_JUSTIFICATIONS))
        for name, values in (("operations", operations), ("anchors", anchors), ("justifications", justifications)):
            with self.subTest(name=name):
                self.assert_input_error(f"too_many_{name}", draft, **{name: values + [values[0]]})


class PreviewOperationTests(PreviewValidationAssertions):
    def test_all_five_typed_operations_are_preserved(self):
        operations = (
            ModifyEvent(TARGET), AdjustEventKind(TARGET, EventKind.DEACTIVATION),
            AdjustEventTemporal(TARGET, TemporalPosition.unknown()),
            AdjustEventZones(TARGET, destination_zone_id=None), insertion(),
        )
        reasons = (OperationJustification(4, "Paper laboratory record"),)
        self.assertEqual(draft(operations, justifications=reasons), (operations, (), reasons))

    def test_unknown_dataclasses_subclasses_and_removal_are_not_operations(self):
        @dataclass(frozen=True)
        class RemoveEvent:
            target: EventTarget

        class ModifySubclass(ModifyEvent):
            pass

        for operation in (None, {}, {"kind": "remove"}, RemoveEvent(TARGET), ModifySubclass(TARGET),
                          EventTarget("movement:1"), "remove", object()):
            with self.subTest(operation=operation):
                self.assert_input_error("invalid_operation", draft, [ModifyEvent(TARGET), operation], index=1)

    def test_target_requires_exact_dataclass_and_nonblank_bounded_key(self):
        class TargetSubclass(EventTarget):
            pass

        for target in (None, {}, "movement:12", TargetSubclass("movement:12"),
                       *(replace(TARGET, key=value) for value in (None, 12, "", " \n ", "x" * (MAX_KEY_LENGTH + 1)))):
            with self.subTest(target=target):
                self.assert_input_error("invalid_target", draft, [replace(ModifyEvent(TARGET), target=target)], index=0)
        self.assertEqual(draft([ModifyEvent(EventTarget("x" * MAX_KEY_LENGTH))])[0][0].target.key,
                         "x" * MAX_KEY_LENGTH)

    def test_optional_fingerprint_must_be_a_production_sha256_digest(self):
        for fingerprint in ("", " ", 12, [], UNSET, "f1", "g" * 64, "a" * 63, "a" * 65, "a" * 64 + "\n"):
            with self.subTest(fingerprint=fingerprint):
                operation = ModifyEvent(replace(EventTarget("derived:1"), expected_evidence_fingerprint=fingerprint))
                self.assert_input_error("invalid_fingerprint", draft, [operation], index=0)
        for fingerprint in (None, FINGERPRINT):
            operation = ModifyEvent(EventTarget("derived:1", fingerprint))
            self.assertEqual(draft([operation])[0], (operation,))

    def test_kind_requires_eventkind_not_its_string_or_none(self):
        for value in ("movement", "deactivation", "unknown", None, 1, True, object()):
            for operation in (replace(ModifyEvent(TARGET), kind=value),
                                          replace(AdjustEventKind(TARGET, EventKind.MOVEMENT), kind=value), insertion(kind=value)):
                with self.subTest(operation=operation):
                    self.assert_input_error("invalid_kind", draft, [operation], index=0)
        self.assert_input_error("invalid_kind", draft, [replace(AdjustEventKind(TARGET, EventKind.MOVEMENT), kind=UNSET)], index=0)
        self.assert_input_error("invalid_kind", draft, [insertion(kind=UNSET)], index=0)

    def test_zones_are_exact_positive_ids_or_none_and_unset_only_for_edits(self):
        class IntSubclass(int):
            pass

        for field in ("origin_zone_id", "destination_zone_id"):
            for value in (True, False, 0, -1, 2.0, "2", [], IntSubclass(2)):
                for operation in (replace(ModifyEvent(TARGET), **{field: value}),
                                  replace(AdjustEventZones(TARGET), **{field: value}), insertion(**{field: value})):
                    with self.subTest(field=field, value=value, operation=operation):
                        self.assert_input_error("invalid_zone_id", draft, [operation], index=0)
            self.assert_input_error("invalid_zone_id", draft, [insertion(**{field: UNSET})], index=0)
            for value in (None, 2, UNSET):
                for operation in (replace(ModifyEvent(TARGET), **{field: value}),
                                  replace(AdjustEventZones(TARGET), **{field: value})):
                    self.assertIs(draft([operation])[0][0], operation)

    def test_aliases_preserve_unset_and_explicit_none_distinctly(self):
        operations = [ModifyEvent(TARGET), AdjustEventZones(TARGET),
                      ModifyEvent(TARGET, origin_zone_id=None), AdjustEventZones(TARGET, destination_zone_id=None)]
        result = draft(operations)[0]
        self.assertIs(result[0].kind, UNSET)
        self.assertIs(result[0].temporal, UNSET)
        self.assertIs(result[0].origin_zone_id, UNSET)
        self.assertIs(result[1].destination_zone_id, UNSET)
        self.assertIsNone(result[2].origin_zone_id)
        self.assertIsNone(result[3].destination_zone_id)
        self.assert_input_error("invalid_temporal", draft, [replace(ModifyEvent(TARGET), temporal=None)], index=0)
        self.assert_input_error("invalid_temporal", draft, [replace(AdjustEventTemporal(TARGET, TemporalPosition.unknown()), temporal=UNSET)], index=0)
        self.assert_input_error("invalid_temporal", draft, [insertion(temporal=UNSET)], index=0)

    def test_insert_id_is_nonblank_bounded_text(self):
        for value in (None, 1, "", "\n ", "x" * (MAX_DRAFT_ID_LENGTH + 1)):
            self.assert_input_error("invalid_draft_id", draft, [insertion(draft_id=value)], index=0)
        operation = insertion(draft_id="x" * MAX_DRAFT_ID_LENGTH)
        self.assertEqual(draft([operation], justifications=[OperationJustification(0, "Source")])[0], (operation,))

    def test_maximum_draft_id_produces_a_targetable_key(self):
        maximum = MAX_KEY_LENGTH - len(draft_key(""))
        operation = insertion(draft_id="x" * maximum)
        target = ModifyEvent(EventTarget(draft_key(operation.draft_id)))
        self.assertEqual(len(target.target.key), MAX_KEY_LENGTH)
        self.assertEqual(draft([operation, target], justifications=[OperationJustification(0, "Source")])[0],
                         (operation, target))
        for length in (maximum + 1, MAX_KEY_LENGTH):
            self.assert_input_error("invalid_draft_id", draft, [insertion(draft_id="x" * length)], index=0)

    def test_business_checks_are_left_to_the_phase_one_engine(self):
        # No-op edits, unresolved targets, foreign zones, disallowed draft kinds,
        # impossible business shapes and duplicate draft ids are engine concerns.
        operations = [ModifyEvent(EventTarget("not-found")),
                      AdjustEventKind(TARGET, EventKind.RECONSTRUCTED_MOVEMENT),
                      AdjustEventZones(TARGET, origin_zone_id=999999),
                      insertion(origin_zone_id=None, destination_zone_id=None), insertion()]
        reasons = [OperationJustification(3, "Source"), OperationJustification(4, "Source")]
        self.assertEqual(draft(operations, justifications=reasons)[0], tuple(operations))


class PreviewTemporalTests(PreviewValidationAssertions):
    def check_position(self, position):
        operation = AdjustEventTemporal(TARGET, position)
        self.assertEqual(draft([operation])[0], (operation,))

    def reject_position(self, position):
        for operation in (AdjustEventTemporal(TARGET, position), ModifyEvent(TARGET, temporal=position),
                          insertion(temporal=position)):
            self.assert_input_error("invalid_temporal", draft, [operation], index=0)

    def test_valid_precision_forms_are_unchanged_without_invented_instants(self):
        for position in (
            TemporalPosition.exact(START), TemporalPosition.recorded_boundary(START),
            TemporalPosition.week(2026, 13, PARIS),
            TemporalPosition.observation_interval(START, START + timedelta(days=2)),
            TemporalPosition.observation_interval(START, START + timedelta(days=2),
                                                  ordering_anchor=START - timedelta(days=7)),
            TemporalPosition.unknown(), TemporalPosition.unknown(not_before=START),
            TemporalPosition.unknown(before=START),
            TemporalPosition.unknown(not_before=START, before=START + timedelta(days=1)),
        ):
            with self.subTest(position=position):
                self.check_position(position)
                if position.precision not in (TemporalPrecision.EXACT, TemporalPrecision.RECORDED_BOUNDARY):
                    self.assertIsNone(position.instant)

    def test_temporal_requires_exact_dataclass_and_precision_enum(self):
        class TemporalSubclass(TemporalPosition):
            pass

        for value in (None, {}, "2026-03-01", START, TemporalSubclass(TemporalPrecision.UNKNOWN),
                      unchecked_temporal(precision="unknown"), unchecked_temporal(precision="exact", instant=START),
                      unchecked_temporal(precision=None), unchecked_temporal(precision=UNSET)):
            self.reject_position(value)

    def test_temporal_fields_must_be_aware_and_utc_representable(self):
        for field in ("instant", "not_before", "before", "ordering_anchor"):
            for value in ("2026-03-01", date(2026, 3, 1), 0, True, float("inf"), float("nan"),
                          datetime(2026, 3, 1), datetime.min.replace(tzinfo=PARIS)):
                with self.subTest(field=field, value=value):
                    self.reject_position(unchecked_temporal(**{field: value}))

    def test_nonexistent_local_times_are_rejected_in_every_timestamp_field(self):
        lower = datetime(2026, 3, 28, tzinfo=PARIS)
        upper = datetime(2026, 3, 30, tzinfo=PARIS)
        for fold in (0, 1):
            gap = datetime(2026, 3, 29, 2, 30, tzinfo=PARIS, fold=fold)
            for position in (
                TemporalPosition.exact(gap), TemporalPosition.recorded_boundary(gap),
                TemporalPosition.unknown(not_before=gap), TemporalPosition.unknown(before=gap),
                TemporalPosition.observation_interval(lower, gap),
                TemporalPosition.observation_interval(gap, upper),
                TemporalPosition.observation_interval(lower, upper, ordering_anchor=gap),
            ):
                with self.subTest(fold=fold, position=position):
                    self.reject_position(position)

    def test_operation_timestamps_preserve_real_folds_utc_and_fixed_offsets(self):
        instants = [datetime(2026, 10, 25, 2, 30, tzinfo=PARIS, fold=fold) for fold in (0, 1)]
        instants += [datetime(2026, 3, 29, 2, 30, tzinfo=zone, fold=fold)
                     for zone in (UTC, timezone(timedelta(hours=2)), timezone(-timedelta(hours=5, minutes=30)))
                     for fold in (0, 1)]
        for instant in instants:
            for position in (
                TemporalPosition.exact(instant), TemporalPosition.recorded_boundary(instant),
                TemporalPosition.unknown(not_before=instant), TemporalPosition.unknown(before=instant),
                TemporalPosition.observation_interval(instant, instant + timedelta(days=1)),
                TemporalPosition.observation_interval(instant - timedelta(days=1), instant),
                TemporalPosition.observation_interval(instant - timedelta(days=1), instant + timedelta(days=1),
                                                      ordering_anchor=instant),
            ):
                for operation in (ModifyEvent(TARGET, temporal=position), AdjustEventTemporal(TARGET, position),
                                  insertion(temporal=position)):
                    with self.subTest(instant=instant, fold=instant.fold, position=position, operation=operation):
                        result = draft([operation], justifications=[OperationJustification(0, "Source")])
                        self.assertIs(result[0][0].temporal, position)

    def test_pointlike_positions_need_instant_without_bounds_or_ordering_anchor(self):
        for precision in (TemporalPrecision.EXACT, TemporalPrecision.RECORDED_BOUNDARY):
            self.reject_position(unchecked_temporal(precision=precision))
            for field in ("not_before", "before", "ordering_anchor"):
                self.reject_position(unchecked_temporal(precision=precision, instant=START, **{field: START}))
        for precision in (TemporalPrecision.UNKNOWN, TemporalPrecision.ISO_WEEK, TemporalPrecision.OBSERVATION_INTERVAL):
            self.reject_position(unchecked_temporal(precision=precision, instant=START))

    def test_interval_bounds_are_required_where_known_and_strictly_ordered_in_utc(self):
        for precision in (TemporalPrecision.ISO_WEEK, TemporalPrecision.OBSERVATION_INTERVAL):
            for bounds in ({}, {"not_before": START}, {"before": START}):
                self.reject_position(unchecked_temporal(precision=precision, **bounds))
        for precision in (TemporalPrecision.UNKNOWN, TemporalPrecision.ISO_WEEK, TemporalPrecision.OBSERVATION_INTERVAL):
            for upper in (START, START - timedelta(seconds=1)):
                self.reject_position(unchecked_temporal(precision=precision, not_before=START, before=upper))
        first = datetime(2026, 10, 25, 2, 30, tzinfo=PARIS, fold=0)
        second = first.replace(fold=1)
        self.check_position(unchecked_temporal(precision=TemporalPrecision.OBSERVATION_INTERVAL,
                                              not_before=first, before=second))
        self.reject_position(unchecked_temporal(precision=TemporalPrecision.OBSERVATION_INTERVAL,
                                               not_before=second, before=first))

    def test_week_year_and_number_are_exact_integers_and_valid_calendar_values(self):
        week = TemporalPosition.week(2026, 13, PARIS)
        for year, number in ((True, 13), (2026, True), (2026.0, 13), (2026, 13.0),
                             ("2026", 13), (2026, "13"), (None, 13), (2026, None),
                             (0, 13), (-1, 13), (10000, 1), (2026, 0), (2026, 54),
                             (2025, 53), (9999, 52)):
            self.reject_position(unchecked_temporal(precision=TemporalPrecision.ISO_WEEK, iso_year=year,
                                                   iso_week=number, not_before=week.not_before, before=week.before))
        self.check_position(TemporalPosition.week(2020, 53, PARIS))
        self.check_position(TemporalPosition.week(2020, 1, PARIS))

    def test_week_bounds_match_paris_calendar_across_dst_and_iso_year(self):
        for year, number, hours in ((2026, 13, 167), (2026, 43, 169), (2020, 1, 168), (2020, 53, 168)):
            with self.subTest(year=year, number=number):
                week = TemporalPosition.week(year, number, PARIS)
                assert week.not_before is not None and week.before is not None
                self.assertEqual(week.before.astimezone(UTC) - week.not_before.astimezone(UTC), timedelta(hours=hours))
                self.check_position(week)
                self.check_position(replace(week, not_before=week.not_before.astimezone(UTC),
                                            before=week.before.astimezone(UTC)))
                self.reject_position(replace(week, before=week.before + timedelta(microseconds=1)))
                self.reject_position(replace(week, not_before=week.not_before + timedelta(seconds=1)))
                self.reject_position(TemporalPosition.week(year, number, UTC))

    def test_only_iso_weeks_carry_year_or_week(self):
        for position in (TemporalPosition.exact(START), TemporalPosition.recorded_boundary(START),
                         TemporalPosition.unknown(), TemporalPosition.observation_interval(START, START + timedelta(days=1))):
            for field, value in (("iso_year", 2026), ("iso_week", 13)):
                self.reject_position(unchecked_temporal(**(position.__dict__ | {field: value})))


class PreviewAnchorTests(PreviewValidationAssertions):
    def test_anchor_requires_exact_dataclass_and_valid_typed_fields(self):
        class AnchorSubclass(ReconstructionAnchor):
            pass

        for value in (None, {}, AnchorSubclass("a", 1, 10, FINGERPRINT),
                      *(anchor(anchor_id=value) for value in (None, 1, "", " \n", "x" * (MAX_ANCHOR_ID_LENGTH + 1))),
                      *(anchor(**{field: value}) for field in ("organization_id", "box_id")
                        for value in (None, 0, -1, True, "1", 1.0))):
            with self.subTest(value=value):
                self.assert_input_error("invalid_anchor", draft, anchors=[value])
        self.assertEqual(draft(anchors=[anchor(anchor_id="a" * MAX_ANCHOR_ID_LENGTH)])[1][0].anchor_id,
                         "a" * MAX_ANCHOR_ID_LENGTH)

    def test_maximum_anchor_id_produces_a_targetable_key(self):
        maximum = MAX_KEY_LENGTH - len(anchor_key(""))
        value = anchor(anchor_id="x" * maximum)
        operation = ModifyEvent(EventTarget(anchor_key(value.anchor_id)))
        self.assertEqual(len(operation.target.key), MAX_KEY_LENGTH)
        self.assertEqual(draft([operation], anchors=[value])[:2], ((operation,), (value,)))
        for length in (maximum + 1, MAX_KEY_LENGTH):
            self.assert_input_error("invalid_anchor", draft, anchors=[anchor(anchor_id="x" * length)])

    def test_anchor_scope_must_match_not_be_silently_ignored(self):
        for value in (anchor(organization_id=2), anchor(box_id=11), anchor(organization_id=2, box_id=11)):
            self.assert_input_error("anchor_scope_mismatch", draft, anchors=[value])
        own = anchor()
        self.assertEqual(draft(anchors=[own])[1], (own,))

    def test_duplicate_anchor_ids_are_invalid_even_with_different_evidence(self):
        for second in (anchor(), anchor(evidence_fingerprint="a" * 64), anchor(locator=())):
            self.assert_input_error("duplicate_anchor_id", draft, anchors=[anchor(), second])
        self.assertEqual(len(draft(anchors=[anchor(), anchor(anchor_id="other")])[1]), 2)

    def test_anchor_fingerprint_is_required_and_valid(self):
        for value in (None, "", "bad", 12, [], "g" * 64, "a" * 63, "a" * 65):
            self.assert_input_error("invalid_fingerprint", draft, anchors=[anchor(evidence_fingerprint=value)])

    def test_empty_or_stable_reference_locator_tuples_are_supported(self):
        for locator in ((), (("starting_location", 10),), (("policy", "synthetic-v1"), ("reading_id", 12)),
                        (("reference", "uuid-like-reference"),),
                        tuple((f"source-{index}", index + 1) for index in range(MAX_LOCATOR_ITEMS))):
            value = anchor(locator=locator)
            self.assertIs(draft(anchors=[value])[1][0], value)

    def test_locator_rejects_mutable_nested_malformed_and_duplicate_references(self):
        invalid = (
            None, {}, [], iter(()), ("source",), (("source",),), (("source", 1, 2),), (["source", 1],),
            (("", 1),), (("  ", 1),), ((12, 1),), (("x" * (MAX_KEY_LENGTH + 1), 1),),
            (("source", None),), (("source", True),), (("source", 0),), (("source", -1),),
            (("source", 1.0),), (("source", []),), (("source", {}),), (("source", ("id", 1)),),
            (("source", ""),), (("source", " \n"),), (("source", "x" * (MAX_KEY_LENGTH + 1)),),
            (("source", 1), ("source", 2)),
            tuple((f"source-{index}", index + 1) for index in range(MAX_LOCATOR_ITEMS + 1)),
        )
        for locator in invalid:
            with self.subTest(locator=locator):
                self.assert_input_error("invalid_locator", draft, anchors=[anchor(locator=locator)])


class PreviewJustificationTests(PreviewValidationAssertions):
    def test_every_insertion_needs_its_own_matching_reason(self):
        operations = [ModifyEvent(TARGET), insertion(), insertion(draft_id="new-2")]
        self.assert_input_error("missing_insertion_justification", draft, operations, index=1)
        self.assert_input_error("missing_insertion_justification", draft, operations,
                                justifications=[OperationJustification(0, "Wrong operation")], index=1)
        self.assert_input_error("missing_insertion_justification", draft, operations,
                                justifications=[OperationJustification(1, "First source")], index=2)
        reasons = [OperationJustification(2, "Second source"), OperationJustification(1, "First source")]
        self.assertEqual(draft(operations, justifications=reasons)[2], tuple(reasons))

    def test_optional_edit_reason_preserves_original_text_without_ui_formatting(self):
        original = " \tOriginal record\n  Preserve spacing and punctuation.  "
        reason = OperationJustification(0, original)
        result = draft([ModifyEvent(TARGET)], justifications=[reason])
        self.assertIs(result[2][0], reason)
        self.assertEqual(result[2][0].reason, original)

    def test_reason_must_be_nonblank_bounded_text(self):
        for reason in (None, 12, True, "", " \n\t", "x" * (MAX_JUSTIFICATION_LENGTH + 1)):
            self.assert_input_error("invalid_justification", draft, [insertion()],
                                    justifications=[replace(OperationJustification(0, "Source"), reason=reason)], index=0)
        reason = OperationJustification(0, "x" * MAX_JUSTIFICATION_LENGTH)
        self.assertEqual(draft([insertion()], justifications=[reason])[2], (reason,))

    def test_justifications_require_exact_dataclass_and_in_range_exact_integer_index(self):
        class JustificationSubclass(OperationJustification):
            pass

        for reason in (None, {}, (0, "Source"), JustificationSubclass(0, "Source"),
                       *(replace(OperationJustification(0, "Source"), operation_index=index)
                         for index in (True, False, -1, 1, 0.0, "0", None))):
            with self.subTest(reason=reason):
                self.assert_input_error("invalid_justification", draft, [ModifyEvent(TARGET)], justifications=[reason])
        self.assert_input_error("invalid_justification", draft, justifications=[OperationJustification(0, "Source")])

    def test_duplicate_justifications_for_any_operation_are_invalid(self):
        for operation in (insertion(), ModifyEvent(TARGET)):
            self.assert_input_error("duplicate_justification", draft, [operation],
                                    justifications=[OperationJustification(0, "First"), OperationJustification(0, "Second")],
                                    index=0)
