"""Pure codec contracts and inert-storage replay against isolated real sources."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
import re
from uuid import UUID

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from . import test_history_preview as fixtures
from . import test_history_preview_api as api_fixtures
from . import test_history_preview_service as service_fixtures
from .effective_history import (
    UNSET, AdjustEventKind, AdjustEventTemporal, AdjustEventZones, EffectiveStatus,
    EventKind, EventTarget, InsertEvent, ModifyEvent, SourceRef, TemporalPosition,
    ThermalSourceKind,
)
from .effective_history_sources import load_box_source_history
from .effective_temperature import ThermalEvidence
from .history_preview_validation import OperationJustification, PreviewInputError
from .history_revision_codec import decode_revision, encode_revision
from .history_revision_composition import (
    HistoricalRevision, ReviewedEvent, compose_revisions, review_event,
    source_event_fingerprint,
)
from .models import BoxHistoryEventIdentity, BoxHistoryRevision, BoxHistoryStream


_LEADING_SELECT = re.compile(r"^\(*\s*SELECT\b", re.IGNORECASE)
_SET_OPERAND = re.compile(
    r"\b(?:UNION(?:\s+ALL)?|INTERSECT|EXCEPT)\s*\(*\s*([A-Za-z]+)",
    re.IGNORECASE,
)


def is_read_only_preview_sql(statement):
    """Accept SELECT and set operations whose every operand is SELECT.

    PostgreSQL may emit ``(SELECT ...) UNION ALL (SELECT ...)`` for the
    preview. CTEs and other read-only shapes are deliberately not accepted
    here, so adding one requires an explicit review of the zero-write check.
    """
    text = statement.lstrip()
    if _LEADING_SELECT.match(text) is None:
        return False
    return all(
        match.group(1).upper() == "SELECT"
        for match in _SET_OPERAND.finditer(text)
    )


def identity(number):
    return str(UUID(int=number))


def effective(history, key):
    return next(item for item in (*history.events, *history.unplaced_events) if item.key == key)


def decode_fields(revision, payload):
    return decode_revision(
        revision_id=revision.revision_id, organization_id=revision.organization_id,
        box_id=revision.box_id, sequence=revision.sequence,
        predecessor_id=revision.predecessor_id, **payload,
    )


class HistoryRevisionCodecTests(SimpleTestCase):
    databases = frozenset()

    def revision(self, operations=None, *, temporal=None, refs=None, justifications=()):
        if operations is None:
            operations = (AdjustEventZones(EventTarget("movement:7"), destination_zone_id=None),)
        targets = dict.fromkeys(item.target.key for item in operations if type(item) is not InsertEvent)
        return HistoricalRevision(
            identity(1), 1, 10, 1, None, operations,
            tuple(ReviewedEvent(
                key, EventKind.MOVEMENT, temporal or TemporalPosition.exact(fixtures.at(5)),
                1, 2, "a" * 64,
                refs if refs is not None else (SourceRef("box_movement", 7, "movement"),),
            ) for key in targets), justifications,
        )

    def roundtrip(self, revision):
        before = repr(revision)
        encoded = encode_revision(revision)
        wire = json.loads(json.dumps(encoded, allow_nan=False))
        decoded = decode_fields(revision, wire)
        self.assertEqual(decoded, revision)
        self.assertEqual(encode_revision(decoded), wire)
        self.assertEqual(repr(revision), before)
        return encoded, decoded

    def rejects(self, payload):
        before = deepcopy(payload)
        with self.assertRaises(PreviewInputError):
            decode_fields(self.revision(), payload)
        self.assertEqual(payload, before)

    def test_all_five_operations_roundtrip_without_collapsing_unset_and_null(self):
        target = EventTarget("movement:7", "b" * 64)
        operations = (
            ModifyEvent(target, temporal=TemporalPosition.exact(fixtures.at(6)), origin_zone_id=None),
            AdjustEventKind(target, EventKind.DEACTIVATION),
            AdjustEventTemporal(target, TemporalPosition.unknown()),
            AdjustEventZones(target, destination_zone_id=None),
            InsertEvent(identity(100), EventKind.REACTIVATION,
                        TemporalPosition.exact(fixtures.at(8)), destination_zone_id=3),
        )
        revision = self.revision(operations, justifications=(OperationJustification(4, "  Reviewed evidence\nkept verbatim  "),))
        encoded, decoded = self.roundtrip(revision)
        self.assertEqual(set(encoded), {"operations", "review_values", "justifications"})
        self.assertNotIn("kind", encoded["operations"][0])
        self.assertIsNone(encoded["operations"][0]["origin_zone_id"])
        self.assertNotIn("origin_zone_id", encoded["operations"][3])
        self.assertIs(decoded.operations[0].kind, UNSET)
        self.assertIs(decoded.operations[3].origin_zone_id, UNSET)
        self.assertIsNone(decoded.operations[3].destination_zone_id)
        self.assertEqual(decoded.justifications[0].reason, revision.justifications[0].reason)

    def test_all_temporal_precisions_and_event_kinds_roundtrip(self):
        positions = (
            TemporalPosition.exact(fixtures.at(5)),
            TemporalPosition.recorded_boundary(fixtures.at(5)),
            TemporalPosition.week(2026, 13, fixtures.PARIS),
            TemporalPosition.observation_interval(fixtures.at(5), fixtures.at(8), ordering_anchor=fixtures.at(9)),
            TemporalPosition.unknown(),
            TemporalPosition.unknown(not_before=fixtures.at(5)),
        )
        for position in positions:
            for kind in EventKind:
                with self.subTest(precision=position.precision, kind=kind):
                    revision = self.revision((ModifyEvent(EventTarget("movement:7"), kind=kind, temporal=position),), temporal=position)
                    self.roundtrip(revision)

    def test_minimal_refs_preserve_source_roles_and_categories_not_thermal_values(self):
        refs = tuple(SourceRef(kind.value, f"ref-{index}", "evidence", "reconstructed", False)
                     for index, kind in enumerate(ThermalSourceKind))
        encoded, _ = self.roundtrip(self.revision(refs=refs))
        for ref in encoded["review_values"]["events"][0]["sources"]:
            self.assertEqual(set(ref), {"source", "source_id", "role", "basis", "exclusive"})
        self.assertNotIn("temperature", json.dumps(encoded))
        encoded["review_values"]["events"][0]["sources"][0]["role"] = "changed"
        self.assertEqual(refs[0].role, "evidence")

    def test_codec_and_composition_leave_zero_negative_null_and_all_raw_evidence_intact(self):
        evidence = [
            fixtures.raw("zero", 1, fixtures.at(2), "0"),
            fixtures.raw("negative", 1, fixtures.at(3), "-3.25", 12),
            replace(fixtures.raw("null", 1, fixtures.at(4), probe=13), temperature_c=None),
            ThermalEvidence("setpoint", 1, 1, ThermalSourceKind.HISTORICAL_SETPOINT,
                            TemporalPosition.exact(fixtures.at(2)), temperature_c=Decimal(0), box_id=10),
            ThermalEvidence("bucket", 1, 1, ThermalSourceKind.CONDENSED_BUCKET,
                            TemporalPosition.observation_interval(fixtures.at(2), fixtures.at(3)),
                            temperature_c=Decimal("-1.01"), temperature_sum_c=Decimal("-3.02"),
                            measurement_count=3, probe_id=11),
            ThermalEvidence("daily", 1, 1, ThermalSourceKind.MIXED_DAILY_AGGREGATE,
                            TemporalPosition.observation_interval(fixtures.at(3), fixtures.at(4)),
                            temperature_c=Decimal(0), measurement_count=2),
        ]
        source, thermal = fixtures.sources([
            fixtures.event("open", EventKind.LOCATION_ASSIGNMENT, fixtures.at(1), destination=1),
            fixtures.event("movement:7", EventKind.MOVEMENT, fixtures.at(5), 1, 2,
                           sources=(SourceRef("box_movement", 7, "movement"),)),
        ], evidence)
        before = repr((source, thermal))
        baseline = compose_revisions(source).published_history
        revision = replace(self.revision((AdjustEventZones(EventTarget("movement:7"), destination_zone_id=3),)),
                           reviewed_events=(review_event(effective(baseline, "movement:7")),))
        encoded, decoded = self.roundtrip(revision)
        result = compose_revisions(source, (decoded,), thermal_source=thermal)
        segments = {item.evidence.key: item.evidence for item in
                    (*result.published_thermal.segments, *result.published_thermal.unassigned)}
        for item in evidence:
            self.assertIs(segments[item.key], item)
        self.assertEqual([segments[key].temperature_c for key in ("zero", "negative", "null")],
                         [Decimal(0), Decimal("-3.25"), None])
        self.assertEqual({item.source_kind for item in segments.values()}, set(ThermalSourceKind))
        self.assertEqual(repr((source, thermal)), before)
        self.assertNotIn("temperature_c", json.dumps(encoded))

    def test_missing_optional_snapshot_and_ref_fields_decode_to_typed_defaults(self):
        payload = encode_revision(self.revision())
        event = payload["review_values"]["events"][0]
        for key in ("source_fingerprint", "origin_zone_id", "destination_zone_id"):
            event.pop(key)
        event["sources"] = [{"source": "box_movement", "source_id": 7, "role": "movement"}]
        decoded = decode_fields(self.revision(), payload)
        self.assertEqual(decoded.reviewed_events[0].sources, (SourceRef("box_movement", 7, "movement"),))
        self.assertIsNone(decoded.reviewed_events[0].source_fingerprint)

    def test_unknown_fields_are_rejected_at_every_boundary(self):
        for path in ((), ("operations", 0), ("operations", 0, "target"),
                     ("review_values",), ("review_values", "events", 0),
                     ("review_values", "events", 0, "temporal"),
                     ("review_values", "events", 0, "sources", 0)):
            payload = encode_revision(self.revision())
            if not path:
                payload["operations"][0]["type"] = "delete_event"
            else:
                node = payload
                for key in path:
                    node = node[key]
                node["raw_payload"] = {"temperature_c": 0}
            with self.subTest(path=path):
                self.rejects(payload)
        payload = encode_revision(self.revision(justifications=(OperationJustification(0, "Reviewed"),)))
        payload["justifications"][0]["extra"] = True
        self.rejects(payload)

    def test_malformed_shapes_scalars_kinds_dates_fingerprints_and_refs_are_rejected(self):
        cases = [
            (("operations",), {}), (("review_values",), []), (("justifications",), {}),
            (("review_values", "events"), None), (("operations", 0), None),
            (("operations", 0, "target"), "movement:7"),
            (("operations", 0, "destination_zone_id"), 0),
            (("operations", 0, "destination_zone_id"), -1),
            (("operations", 0, "destination_zone_id"), False),
            (("review_values", "events", 0, "kind"), "bad"),
            (("review_values", "events", 0, "source_fingerprint"), "A" * 64),
            (("review_values", "events", 0, "temporal", "precision"), "day"),
            (("review_values", "events", 0, "temporal", "instant"), "2026-03-05T00:00:00"),
            (("review_values", "events", 0, "temporal", "instant"), "2026-02-30T00:00:00Z"),
            (("review_values", "events", 0, "temporal", "instant"), "2026-03-05 00:00:00+00:00"),
            (("review_values", "events", 0, "temporal", "instant"), None),
            (("review_values", "events", 0, "sources", 0, "source_id"), 0),
            (("review_values", "events", 0, "sources", 0, "source_id"), True),
            (("review_values", "events", 0, "sources", 0, "exclusive"), 1),
            (("review_values", "events", 0, "sources", 0, "basis"), " "),
        ]
        for path, value in cases:
            payload = encode_revision(self.revision())
            node = payload
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = value
            with self.subTest(path=path, value=value):
                self.rejects(payload)

    def test_uncertain_temporal_shapes_are_validated_not_coerced(self):
        for temporal in (
            {"precision": "iso_week", "iso_year": 2026, "iso_week": 54},
            {"precision": "iso_week", "iso_year": 2026, "iso_week": 13,
             "not_before": "2026-03-23T00:00:00Z", "before": "2026-03-30T00:00:00Z"},
            {"precision": "observation_interval", "not_before": "2026-03-05T00:00:00Z"},
            {"precision": "observation_interval", "not_before": "2026-03-06T00:00:00Z", "before": "2026-03-05T00:00:00Z"},
        ):
            payload = encode_revision(self.revision())
            payload["review_values"]["events"][0]["temporal"] = temporal
            self.rejects(payload)

    def test_reasons_must_reference_unique_operation_indices_and_insertion_requires_reason(self):
        for reasons in ([{"operation_index": 0, "reason": " "}],
                        [{"operation_index": True, "reason": "Review"}],
                        [{"operation_index": 1, "reason": "Review"}],
                        [{"operation_index": 0, "reason": "x" * 4001}],
                        [{"operation_index": 0, "reason": "Review"}] * 2):
            payload = encode_revision(self.revision())
            payload["justifications"] = reasons
            self.rejects(payload)
        insertion = InsertEvent(identity(100), EventKind.REACTIVATION, TemporalPosition.exact(fixtures.at(8)))
        revision = self.revision((insertion,), justifications=(OperationJustification(0, "Evidence"),))
        payload = encode_revision(revision)
        payload["justifications"] = []
        self.rejects(payload)

    def test_durable_identifiers_require_canonical_uuid_strings(self):
        invalid = ("browser-draft", identity(100).replace("-", ""), "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA", UUID(int=100))
        for value in invalid:
            for field in ("revision_id", "predecessor_id"):
                revision = replace(self.revision(), **{field: value})
                with self.subTest(field=field, value=value), self.assertRaises(PreviewInputError):
                    encode_revision(revision)
            insertion = InsertEvent(value, EventKind.REACTIVATION, TemporalPosition.exact(fixtures.at(8)))
            with self.subTest(insertion=value), self.assertRaises(PreviewInputError):
                encode_revision(self.revision((insertion,), justifications=(OperationJustification(0, "Evidence"),)))
        for prefix in ("anchor:", "draft:"):
            with self.assertRaises(PreviewInputError):
                encode_revision(self.revision((AdjustEventZones(EventTarget(prefix + "browser"), destination_zone_id=3),)))

    def test_review_targets_and_local_chain_shape_are_strict(self):
        for field, value in (("sequence", True), ("sequence", 0), ("sequence", 2),
                             ("organization_id", 0), ("box_id", False)):
            with self.subTest(field=field), self.assertRaises(PreviewInputError):
                encode_revision(replace(self.revision(), **{field: value}))
        for events in ([], encode_revision(self.revision())["review_values"]["events"] * 2):
            payload = encode_revision(self.revision())
            payload["review_values"]["events"] = events
            self.rejects(payload)

    def test_decode_and_reencode_do_not_share_mutable_json_with_caller(self):
        revision = self.revision()
        payload = encode_revision(revision)
        before = deepcopy(payload)
        decoded = decode_fields(revision, payload)
        fresh = encode_revision(decoded)
        fresh["review_values"]["events"][0]["sources"][0]["role"] = "changed"
        self.assertEqual(payload, before)
        self.assertEqual(decoded, revision)


class HistoryRevisionCodecStorageTests(TestCase):
    # Borrow fixture builders only, not another TestCase's discovered tests.
    setUp = service_fixtures.HistoryPreviewServiceTests.setUp
    make_box = service_fixtures.HistoryPreviewServiceTests.make_box
    probe = service_fixtures.HistoryPreviewServiceTests.probe
    raw = service_fixtures.HistoryPreviewServiceTests.raw
    bucket = service_fixtures.HistoryPreviewServiceTests.bucket
    observe = service_fixtures.HistoryPreviewServiceTests.observe
    snapshot = service_fixtures.HistoryPreviewServiceTests.snapshot
    url = api_fixtures.HistoryPreviewAPITests.url
    window = api_fixtures.HistoryPreviewAPITests.window
    api_client = api_fixtures.HistoryPreviewAPITests.api_client
    success = api_fixtures.HistoryPreviewAPITests.success

    def request(self):
        # Script-based CI keeps production HTTPS middleware enabled with DEBUG off.
        try:
            with CaptureQueriesContext(connection) as queries:
                response = self.api_client().get(
                    self.url(), data=self.window(), secure=True,
                    HTTP_X_ORGANIZATION_ID=str(self.org.pk),
                )
        finally:
            self.assertTrue(all(is_read_only_preview_sql(row["sql"]) for row in queries),
                            [row["sql"] for row in queries
                             if not is_read_only_preview_sql(row["sql"])])
        diagnostic = f"Preview GET status={response.status_code}, headers={dict(response.headers)!r}"
        self.assertEqual(response.status_code, 200, diagnostic)
        self.assertEqual(response.get("Cache-Control"), "private, no-store", diagnostic)
        self.assertTrue({"cookie", "x-organization-id"}.issubset(
            {item.strip().lower() for item in response.get("Vary", "").split(",")},
        ), diagnostic)
        return response

    def load(self):
        return load_box_source_history(organization_id=self.org.pk, box_id=self.box.pk)

    def persist(self, stream, revision):
        with self.assertNumQueries(0):
            payload = encode_revision(revision)
        stored = BoxHistoryRevision.objects.create(
            id=revision.revision_id, stream=stream, sequence=revision.sequence,
            predecessor_id=revision.predecessor_id, author=self.admin,
            reason="Reviewed stored decision", source_fingerprint="a" * 64,
            request_fingerprint="b" * 64, **payload,
        )
        # Fetch scalar fields explicitly. The codec never receives a model.
        values = BoxHistoryRevision.objects.filter(pk=stored.pk).values(
            "id", "sequence", "predecessor_id", "operations", "review_values", "justifications",
        ).get()
        values["revision_id"] = str(values.pop("id"))
        if values["predecessor_id"] is not None:
            values["predecessor_id"] = str(values["predecessor_id"])
        with self.assertNumQueries(0):
            decoded = decode_revision(organization_id=self.org.pk, box_id=self.box.pk, **values)
        self.assertEqual(decoded, revision)
        stream.head = stored
        stream.save()
        return decoded

    def test_real_source_r1_r2_r3_store_decode_compose_and_preview_never_reads_overlay(self):
        source = self.load()
        source_before = repr(source)
        original = next(item for item in source.events if item.identity.key == self.target.key)
        original_fingerprint = source_event_fingerprint(original)
        rows_before = self.snapshot()
        preview_before = self.success(self.request())
        stream = BoxHistoryStream.objects.create(organization=self.org, box=self.box)
        chain = []
        for sequence, operation in enumerate((
            AdjustEventZones(self.target, destination_zone_id=self.c.pk),
            AdjustEventTemporal(self.target, TemporalPosition.exact(self.later)),
            ModifyEvent(self.target, temporal=original.temporal, destination_zone_id=self.b.pk),
        ), 1):
            previous = compose_revisions(source, chain).published_history
            reviewed = review_event(effective(previous, self.target.key))
            self.assertEqual(reviewed.sources, original.sources)
            self.assertEqual(reviewed.source_fingerprint, original_fingerprint)
            revision = HistoricalRevision(identity(sequence), self.org.pk, self.box.pk, sequence,
                                          chain[-1].revision_id if chain else None,
                                          (operation,), (reviewed,))
            chain.append(self.persist(stream, revision))
            with self.assertNumQueries(0):
                result = compose_revisions(source, chain)
            projected = effective(result.published_history, self.target.key)
            self.assertIs(projected.original, original)
            self.assertEqual(projected.sources, original.sources)
            if sequence == 1:
                self.assertEqual(projected.destination_zone_id, self.c.pk)
            elif sequence == 2:
                self.assertEqual(projected.temporal, TemporalPosition.exact(self.later))
            # Even while the stored chain differs, GET remains request-local simulation.
            self.assertEqual(self.success(self.request()), preview_before)
        self.assertEqual(projected.temporal, original.temporal)
        self.assertEqual(projected.destination_zone_id, original.destination_zone_id)
        self.assertEqual(projected.status, EffectiveStatus.MODIFIED)
        self.assertFalse(projected.established)
        self.assertEqual(len(result.decisions), 3)
        self.assertEqual(self.load(), source)
        self.assertEqual(repr(source), source_before)
        after = self.snapshot()
        storage = {"cultures.BoxHistoryStream", "cultures.BoxHistoryRevision"}
        self.assertEqual({key: value for key, value in after.items() if key not in storage},
                         {key: value for key, value in rows_before.items() if key not in storage})

    def test_registry_assigned_insert_identity_survives_storage_and_later_review(self):
        source = self.load()
        original_rows = self.snapshot()
        stream = BoxHistoryStream.objects.create(organization=self.org, box=self.box)
        registry = BoxHistoryEventIdentity.objects.create(stream=stream, kind="inserted")
        insertion = InsertEvent(str(registry.pk), EventKind.MOVEMENT,
                                TemporalPosition.exact(self.later), self.b.pk, self.c.pk)
        r1 = self.persist(stream, HistoricalRevision(identity(10), self.org.pk, self.box.pk, 1,
                          None, (insertion,), (), (OperationJustification(0, "Reviewed insertion evidence"),)))
        one = compose_revisions(source, (r1,))
        key = f"draft:{registry.pk}"
        reviewed = review_event(effective(one.published_history, key))
        self.assertIsNone(reviewed.source_fingerprint)
        r2 = self.persist(stream, HistoricalRevision(identity(11), self.org.pk, self.box.pk, 2,
                          r1.revision_id, (AdjustEventTemporal(EventTarget(key),
                          TemporalPosition.exact(self.later + timedelta(hours=1))),), (reviewed,)))
        result = compose_revisions(source, (r1, r2))
        item = effective(result.published_history, key)
        self.assertEqual(item.key, key)
        self.assertEqual(item.status, EffectiveStatus.INSERTED)
        self.assertIsNone(item.original)
        storage = {"cultures.BoxHistoryStream", "cultures.BoxHistoryRevision", "cultures.BoxHistoryEventIdentity"}
        self.assertEqual({key: value for key, value in self.snapshot().items() if key not in storage},
                         {key: value for key, value in original_rows.items() if key not in storage})
