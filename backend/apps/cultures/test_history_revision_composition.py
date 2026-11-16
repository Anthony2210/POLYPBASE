"""Database-free contracts for durable historical revision composition."""

# The public result annotates projections as object; invalid typed inputs are
# deliberate validation cases. Runtime assertions below check their contracts.
# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportArgumentType=false

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from unittest.mock import patch
from uuid import UUID

from django.test import SimpleTestCase

from . import history_revision_composition as composition
from . import test_history_preview as fixtures
from .effective_history import (
    AdjustEventTemporal,
    AdjustEventZones,
    AnchorStatus,
    ConflictCode,
    EffectiveStatus,
    EventIdentity,
    EventKind,
    EventOrigin,
    EventTarget,
    IdentityStability,
    InsertEvent,
    ModifyEvent,
    ReconstructionAnchor,
    SourceRef,
    TemporalPosition,
    ThermalSourceKind,
    _mark_provisional,
)
from .effective_temperature import ThermalEvidence, project_temperature, utc
from .history_preview import _project, _signature
from .history_preview_validation import OperationJustification, PreviewInputError

MODULE = "apps.cultures.history_revision_composition"
PREVIEW = "apps.cultures.history_preview"
A, B, C = fixtures.A, fixtures.B, fixtures.C


def identity(number):
    return str(UUID(int=number))


def effective(history, key):
    return next(item for item in (*history.events, *history.unplaced_events) if item.key == key)


class HistoryRevisionCompositionTests(SimpleTestCase):
    # SimpleTestCase deliberately forbids all database access.
    databases = frozenset()

    def source(self, evidence=()):
        return fixtures.sources([
            fixtures.event("open", EventKind.LOCATION_ASSIGNMENT, fixtures.at(1), destination=A),
            fixtures.event("move", EventKind.MOVEMENT, fixtures.at(5), A, B),
            fixtures.event("later", EventKind.MOVEMENT, fixtures.at(15), B, C),
        ], evidence)

    def revision(self, history, operations=(), *, sequence=1, predecessor=None,
                 number=None, justifications=()):
        targets = dict.fromkeys(operation.target.key for operation in operations
                                if not isinstance(operation, InsertEvent))
        return composition.HistoricalRevision(
            revision_id=identity(number if number is not None else sequence),
            organization_id=1, box_id=10, sequence=sequence,
            predecessor_id=predecessor, operations=tuple(operations),
            reviewed_events=tuple(composition.review_event(effective(history, key)) for key in targets),
            justifications=tuple(justifications),
        )

    def first(self, source, operations=None):
        if operations is None:
            operations = (AdjustEventZones(EventTarget("move"), destination_zone_id=C),)
        return self.revision(composition.compose_revisions(source).published_history, operations)

    def assert_error(self, code, source, revisions=(), **kwargs):
        # Malformed inputs intentionally include objects outside the JSON contract.
        before = repr((source, revisions, kwargs))
        with self.assertRaises(PreviewInputError) as caught:
            composition.compose_revisions(source, revisions, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(repr((source, revisions, kwargs)), before)
        return caught.exception

    def anchored(self):
        source, _ = self.source()
        derived = replace(source.events[1], identity=EventIdentity(
            "derived", IdentityStability.DERIVED,
            evidence_fingerprint="a" * 64, locator=(("reading", 7),)))
        source = replace(source, events=(source.events[0], derived, source.events[2]))
        anchor = ReconstructionAnchor(identity(500), 1, 10, "a" * 64, (("reading", 7),))
        return source, anchor

    def test_empty_composition_keeps_source_and_engine_baseline_without_publication_policy(self):
        source, _ = self.source()
        result = composition.compose_revisions(source)
        self.assertIs(result.source, source)
        self.assertEqual(result.source_history, _project(source))
        self.assertIs(result.source_history, result.published_history)
        self.assertEqual(result.revisions, ())
        self.assertEqual(result.decisions, ())
        self.assertIsNone(result.candidate_revision)
        self.assertIsNone(result.candidate_history)
        self.assertIsNone(result.published_thermal)
        self.assertIsNone(result.candidate_thermal)
        self.assertEqual(result.mode, "composition_only")
        self.assertIs(result.publication_evaluated, False)

    def test_r1_r2_r3_restore_is_a_new_attributed_decision_not_erasure(self):
        source, _ = self.source()
        r1 = self.first(source)
        one = composition.compose_revisions(source, (r1,))
        r2 = self.revision(one.published_history, (
            AdjustEventTemporal(EventTarget("move"), TemporalPosition.exact(fixtures.at(8))),
        ), sequence=2, predecessor=r1.revision_id)
        two = composition.compose_revisions(source, (r1, r2))
        r3 = self.revision(two.published_history, (ModifyEvent(
            EventTarget("move"), temporal=source.events[1].temporal, destination_zone_id=B),
        ), sequence=3, predecessor=r2.revision_id)
        result = composition.compose_revisions(source, (r1, r2, r3))
        restored = effective(result.published_history, "move")
        self.assertEqual((restored.temporal, restored.destination_zone_id), (source.events[1].temporal, B))
        self.assertIs(restored.original, source.events[1])
        self.assertEqual(restored.status, EffectiveStatus.MODIFIED)
        self.assertFalse(restored.established)
        self.assertEqual(result.revisions, (r1, r2, r3))
        self.assertEqual([(d.revision_id, d.operation_index, d.event_key) for d in result.decisions],
                         [(r.revision_id, 0, "move") for r in (r1, r2, r3)])
        self.assertEqual(effective(two.published_history, "move").destination_zone_id, C)
        self.assertEqual(effective(result.source_history, "move").destination_zone_id, B)

    def test_review_captures_previous_effective_values_and_original_content_fingerprint(self):
        source, _ = self.source()
        r1 = self.first(source)
        current = effective(composition.compose_revisions(source, (r1,)).published_history, "move")
        review = composition.review_event(current)
        self.assertIsInstance(review, composition.ReviewedEvent)
        self.assertEqual((review.event_key, review.kind, review.temporal,
                          review.origin_zone_id, review.destination_zone_id),
                         ("move", current.kind, current.temporal, A, C))
        self.assertEqual(review.source_fingerprint, composition.source_event_fingerprint(source.events[1]))
        self.assertNotEqual(review.source_fingerprint,
                            composition.source_event_fingerprint(replace(source.events[1], destination_zone_id=C)))

    def test_fingerprint_hashes_original_complete_content_deterministically(self):
        source, _ = self.source()
        original = replace(source.events[1], sources=(SourceRef("movement", 7, "proof"),),
                           details={"count": 0, "missing": None}, provenance="recorded")
        fingerprint = composition.source_event_fingerprint(original)
        self.assertEqual(fingerprint, sha256(_signature(original).encode("utf-8")).hexdigest())
        self.assertEqual(len(fingerprint), 64)
        variants = (
            replace(original, details={"count": None, "missing": None}),
            replace(original, provenance="different"), replace(original, established=False),
            replace(original, sources=(SourceRef("movement", 8, "proof"),)),
            replace(original, origin=EventOrigin.HISTORICAL_BOX_MOVEMENT),
            replace(original, temporal=TemporalPosition.exact(fixtures.at(6))),
        )
        for changed in variants:
            with self.subTest(changed=changed):
                self.assertNotEqual(composition.source_event_fingerprint(changed), fingerprint)
        with self.assertRaises(PreviewInputError) as caught:
            composition.source_event_fingerprint(object())
        self.assertEqual(caught.exception.code, "invalid_source_event")

    def test_candidate_is_separate_from_source_and_published_history_and_thermal(self):
        source, thermal = self.source((fixtures.raw("b", B, fixtures.at(8), probe=12),
                                       fixtures.raw("c", C, fixtures.at(8), "-2", 13)))
        r1 = self.first(source, (AdjustEventTemporal(EventTarget("move"), TemporalPosition.exact(fixtures.at(6))),))
        published = composition.compose_revisions(source, (r1,))
        candidate = self.revision(published.published_history, (
            AdjustEventZones(EventTarget("move"), destination_zone_id=C),
        ), sequence=2, predecessor=r1.revision_id)
        result = composition.compose_revisions(source, (r1,), candidate=candidate, thermal_source=thermal)
        self.assertEqual(result.revisions, (r1,))
        self.assertIs(result.candidate_revision, candidate)
        self.assertEqual(result.published_history, published.published_history)
        self.assertEqual(effective(result.source_history, "move").temporal.instant, fixtures.at(5))
        self.assertEqual(effective(result.published_history, "move").destination_zone_id, B)
        self.assertEqual(effective(result.candidate_history, "move").destination_zone_id, C)
        self.assertEqual({s.evidence.key for s in result.published_thermal.segments}, {"b"})
        self.assertEqual({s.evidence.key for s in result.candidate_thermal.segments}, {"c"})
        self.assertEqual(result.published_thermal, project_temperature(result.published_history, thermal))
        self.assertEqual(result.candidate_thermal, project_temperature(result.candidate_history, thermal))
        self.assertEqual([(d.revision_id, d.operation_index) for d in result.decisions],
                         [(r1.revision_id, 0), (candidate.revision_id, 0)])
        self.assertFalse(result.publication_evaluated)

    def test_candidate_can_be_first_revision(self):
        source, _ = self.source()
        candidate = self.first(source)
        result = composition.compose_revisions(source, candidate=candidate)
        self.assertEqual(result.revisions, ())
        self.assertEqual(result.source_history, result.published_history)
        self.assertEqual(effective(result.candidate_history, "move").destination_zone_id, C)
        self.assertEqual(len(result.decisions), 1)

    def test_inserted_backend_identity_can_be_reviewed_and_corrected_later(self):
        source, _ = fixtures.sources([self.source()[0].events[0]])
        draft_id = identity(700)
        operation = InsertEvent(draft_id, EventKind.MOVEMENT, TemporalPosition.exact(fixtures.at(5)), A, B)
        justification = OperationJustification(0, "Historical movement supported by reviewed evidence")
        r1 = self.revision(_project(source), (operation,), justifications=(justification,))
        one = composition.compose_revisions(source, (r1,))
        inserted = effective(one.published_history, f"draft:{draft_id}")
        self.assertIsNone(inserted.original)
        self.assertIsNone(composition.review_event(inserted).source_fingerprint)
        r2 = self.revision(one.published_history, (
            AdjustEventZones(EventTarget(inserted.key), destination_zone_id=C),
        ), sequence=2, predecessor=r1.revision_id)
        result = composition.compose_revisions(source, (r1, r2))
        changed = effective(result.published_history, inserted.key)
        self.assertEqual(changed.identity, inserted.identity)
        self.assertEqual(changed.status, EffectiveStatus.INSERTED)
        self.assertEqual(changed.destination_zone_id, C)
        self.assertIsNone(changed.original)
        self.assertFalse(changed.established)
        self.assertEqual([d.event_key for d in result.decisions], [inserted.key, inserted.key])
        self.assertEqual(result.revisions[0].justifications, (justification,))
        self.assertEqual(source.events, (source.events[0],))

    def test_browser_and_noncanonical_insertion_ids_are_rejected(self):
        source, _ = self.source()
        for bad in ("browser-new-1", "draft:browser", UUID(int=700), identity(700).replace("-", ""),
                    "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"):
            with self.subTest(identity=bad):
                operation = InsertEvent(bad, EventKind.MOVEMENT, TemporalPosition.exact(fixtures.at(10)), B, C)
                revision = self.revision(_project(source), (operation,),
                                         justifications=(OperationJustification(0, "Reviewed insertion"),))
                # Phase 3 rejects non-string IDs before durable-ID validation.
                code = "invalid_draft_id" if isinstance(bad, UUID) else "invalid_durable_identity"
                self.assert_error(code, source, (revision,))

    def test_insertions_require_phase3_justification(self):
        source, _ = self.source()
        operation = InsertEvent(identity(700), EventKind.MOVEMENT, TemporalPosition.exact(fixtures.at(10)), B, C)
        revision = self.revision(_project(source), (operation,))
        error = self.assert_error("missing_insertion_justification", source, (revision,))
        self.assertEqual(error.operation_index, 0)

    def test_phase3_rejects_malformed_operations_and_justifications(self):
        source, _ = self.source()
        revision = self.first(source)
        cases = (
            (replace(revision, operations=(object(),)), "invalid_operation"),
            (replace(revision, operations=(AdjustEventZones(EventTarget("move"), destination_zone_id=True),)),
             "invalid_zone_id"),
            (replace(revision, justifications=(OperationJustification(0, " "),)), "invalid_justification"),
            (replace(revision, justifications=(OperationJustification(1, "Wrong index"),)), "invalid_justification"),
            (replace(revision, justifications=(OperationJustification(0, "First"),
                                               OperationJustification(0, "Second"))), "duplicate_justification"),
        )
        for bad, code in cases:
            with self.subTest(code=code):
                self.assert_error(code, source, (bad,))

    def test_canonical_anchor_identity_survives_multiple_revisions_and_derived_key_changes(self):
        source, anchor = self.anchored()
        baseline = composition.compose_revisions(source, anchors=(anchor,))
        key = f"anchor:{anchor.anchor_id}"
        r1 = self.revision(baseline.published_history, (
            AdjustEventZones(EventTarget(key), destination_zone_id=C),))
        one = composition.compose_revisions(source, (r1,), anchors=(anchor,))
        r2 = self.revision(one.published_history, (
            AdjustEventTemporal(EventTarget(key), TemporalPosition.exact(fixtures.at(8))),
        ), sequence=2, predecessor=r1.revision_id)
        result = composition.compose_revisions(source, (r1, r2), anchors=(anchor,))
        changed = effective(result.published_history, key)
        self.assertEqual(changed.identity.stability, IdentityStability.ANCHORED)
        self.assertEqual(changed.identity.derived_key, "derived")
        self.assertEqual(changed.original, effective(baseline.published_history, key).original)
        self.assertEqual(result.published_history.anchor_resolutions, baseline.published_history.anchor_resolutions)
        self.assertEqual(result.published_history.anchor_resolutions[0].status, AnchorStatus.MATCHED)
        renamed = replace(source.events[1], identity=replace(source.events[1].identity, key="recomputed-key"))
        refreshed = replace(source, events=(source.events[0], renamed, source.events[2]))
        refreshed_result = composition.compose_revisions(refreshed, (r1, r2), anchors=(anchor,))
        replayed = effective(refreshed_result.published_history, key)
        self.assertEqual(replayed.key, changed.key)
        self.assertEqual(replayed.temporal, changed.temporal)
        self.assertEqual(replayed.destination_zone_id, changed.destination_zone_id)
        self.assertEqual(replayed.identity.derived_key, "recomputed-key")
        self.assertEqual(refreshed_result.decisions, result.decisions)
        self.assertEqual([d.event_key for d in result.decisions], [key, key])
        # Stable naming is not permission to replay against changed evidence.
        altered = replace(renamed, destination_zone_id=A)
        self.assert_error("revision_review_incompatible",
                          replace(refreshed, events=(source.events[0], altered, source.events[2])),
                          (r1, r2), anchors=(anchor,))

    def test_changed_anchor_evidence_rejects_composition(self):
        source, anchor = self.anchored()
        changed = replace(source.events[1], identity=replace(source.events[1].identity, evidence_fingerprint="b" * 64))
        self.assert_error("source_anchor_incompatible", replace(source, events=(source.events[0], changed)),
                          anchors=(anchor,))

    def test_orphan_anchor_rejects_composition(self):
        source, anchor = self.anchored()
        self.assert_error("source_anchor_incompatible", replace(source, events=(source.events[0],)), anchors=(anchor,))

    def test_ambiguous_derived_evidence_rejects_composition(self):
        source, anchor = self.anchored()
        duplicate = replace(source.events[1], identity=replace(source.events[1].identity, key="other-derived"))
        self.assert_error("source_anchor_incompatible", replace(source, events=(*source.events, duplicate)),
                          anchors=(anchor,))

    def test_two_anchors_cannot_claim_one_derived_event(self):
        source, anchor = self.anchored()
        self.assert_error("source_anchor_incompatible", source,
                          anchors=(anchor, replace(anchor, anchor_id=identity(501))))

    def test_anchor_locator_mismatch_rejects_composition(self):
        source, anchor = self.anchored()
        self.assert_error("source_anchor_incompatible", source,
                          anchors=(replace(anchor, locator=(("reading", 8),)),))

    def test_anchor_ids_types_duplicates_and_foreign_scopes_are_rejected(self):
        source, anchor = self.anchored()
        cases = (
            ((object(),), "invalid_anchor"),
            ((replace(anchor, anchor_id="browser-anchor"),), "invalid_durable_identity"),
            ((anchor, anchor), "duplicate_anchor_id"),
            ((replace(anchor, organization_id=2),), "anchor_scope_mismatch"),
            ((replace(anchor, box_id=11),), "anchor_scope_mismatch"),
        )
        for anchors, code in cases:
            with self.subTest(code=code):
                self.assert_error(code, source, anchors=anchors)

    def test_persisted_event_origin_sources_and_original_references_are_preserved(self):
        base, _ = self.source()
        for origin in EventOrigin:
            if origin == EventOrigin.DRAFT:
                continue
            with self.subTest(origin=origin):
                original = replace(base.events[1], origin=origin,
                                   sources=(SourceRef("movement", 7, "recorded"),),
                                   provenance="original evidence", details={"zero": 0, "missing": None})
                source = replace(base, events=(base.events[0], original, base.events[2]))
                r1 = self.first(source)
                result = composition.compose_revisions(source, (r1,))
                changed = effective(result.published_history, "move")
                self.assertIs(changed.original, original)
                self.assertEqual(changed.origin, origin)
                self.assertEqual(changed.sources, original.sources)
                self.assertEqual(changed.provenance, original.provenance)
                self.assertEqual(changed.original.details, original.details)
                self.assertEqual(changed.identity, original.identity)

    def test_invalid_source_and_scope_are_rejected(self):
        self.assert_error("invalid_revision_source", object())
        source, _ = self.source()
        for org, box in ((0, 10), (True, 10), (1, 0), (1, "10")):
            with self.subTest(org=org, box=box):
                self.assert_error("invalid_scope", replace(source, organization_id=org, box_id=box))

    def test_non_revision_objects_are_rejected(self):
        source, _ = self.source()
        self.assert_error("invalid_revision", source, (object(),))
        self.assert_error("invalid_revision", source, candidate=object())

    def test_revision_and_predecessor_ids_must_be_canonical_uuid_strings(self):
        source, _ = self.source()
        revision = self.first(source)
        for bad in ("browser-id", UUID(int=1), identity(1).replace("-", ""),
                    "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"):
            for field in ("revision_id", "predecessor_id"):
                with self.subTest(field=field, value=bad):
                    self.assert_error("invalid_durable_identity", source, (replace(revision, **{field: bad}),))

    def test_revision_chain_rejects_gaps_reordering_duplicate_ids_and_wrong_predecessors(self):
        source, _ = self.source()
        r1 = self.first(source)
        one = composition.compose_revisions(source, (r1,))
        r2 = self.revision(one.published_history, (
            AdjustEventTemporal(EventTarget("move"), TemporalPosition.exact(fixtures.at(8))),
        ), sequence=2, predecessor=r1.revision_id)
        cases = (
            (replace(r1, sequence=0),), (replace(r1, sequence=True),),
            (replace(r1, sequence="1"),), (replace(r1, predecessor_id=identity(99)),),
            (r2,), (r2, r1), (r1, replace(r2, sequence=3)),
            (r1, replace(r2, predecessor_id=None)), (r1, replace(r2, predecessor_id=identity(99))),
            (r1, replace(r2, revision_id=r1.revision_id)),
        )
        for chain in cases:
            with self.subTest(chain=chain):
                self.assert_error("invalid_revision_chain", source, chain)

    def test_candidate_must_be_the_next_revision_of_the_complete_chain(self):
        source, _ = self.source()
        r1 = self.first(source)
        self.assert_error("invalid_revision_chain", source, (r1,), candidate=r1)
        self.assert_error("invalid_revision_chain", source, candidate=replace(r1, sequence=2))

    def test_foreign_revision_and_candidate_scopes_are_rejected(self):
        source, _ = self.source()
        revision = self.first(source)
        for changed in (replace(revision, organization_id=2), replace(revision, box_id=11)):
            with self.subTest(scope=(changed.organization_id, changed.box_id)):
                self.assert_error("revision_scope_mismatch", source, (changed,))
                self.assert_error("revision_scope_mismatch", source, candidate=changed)

    def test_thermal_source_foreign_org_box_or_zone_scope_is_rejected(self):
        source, thermal = self.source()
        for changed in (replace(thermal, organization_id=2), replace(thermal, box_id=11),
                        replace(thermal, zone_ids=frozenset((A, B)))):
            with self.subTest(scope=changed):
                self.assert_error("source_scope_mismatch", source, thermal_source=changed)

    def test_each_noninsert_target_requires_exactly_one_review(self):
        source, _ = self.source()
        revision = self.first(source)
        review = revision.reviewed_events[0]
        for reviews, code in (
            ((), "review_target_mismatch"),
            ((review, replace(review, event_key="later")), "review_target_mismatch"),
            ((review, review), "invalid_reviewed_events"), ((object(),), "invalid_reviewed_events"),
        ):
            with self.subTest(code=code, reviews=reviews):
                self.assert_error(code, source, (replace(revision, reviewed_events=reviews),))

    def test_multiple_operations_on_one_target_share_review_and_keep_full_operation_indices(self):
        source, _ = self.source()
        revision = self.first(source, (
            AdjustEventZones(EventTarget("move"), destination_zone_id=C),
            AdjustEventTemporal(EventTarget("move"), TemporalPosition.exact(fixtures.at(8))),
        ))
        self.assertEqual(len(revision.reviewed_events), 1)
        result = composition.compose_revisions(source, (revision,))
        self.assertEqual([(d.operation_index, d.event_key) for d in result.decisions], [(0, "move"), (1, "move")])
        changed = effective(result.published_history, "move")
        self.assertEqual((changed.destination_zone_id, changed.temporal.instant), (C, fixtures.at(8)))

    def test_review_of_original_values_is_stale_after_previous_revision(self):
        source, _ = self.source()
        r1 = self.first(source)
        stale = self.revision(_project(source), (
            AdjustEventTemporal(EventTarget("move"), TemporalPosition.exact(fixtures.at(8))),
        ), sequence=2, predecessor=r1.revision_id)
        self.assert_error("revision_review_incompatible", source, (r1, stale))

    def test_changed_review_fields_and_original_source_content_are_stale(self):
        source, _ = self.source()
        revision = self.first(source)
        review = revision.reviewed_events[0]
        for changed in (replace(review, kind=EventKind.DEACTIVATION),
                        replace(review, temporal=TemporalPosition.exact(fixtures.at(6))),
                        replace(review, origin_zone_id=C), replace(review, destination_zone_id=A),
                        replace(review, source_fingerprint="b" * 64), replace(review, source_fingerprint=None)):
            with self.subTest(review=changed):
                self.assert_error("revision_review_incompatible", source,
                                  (replace(revision, reviewed_events=(changed,)),))
        changed_source = replace(source, events=(source.events[0],
                                 replace(source.events[1], details={"new_recorded_fact": 0}), source.events[2]))
        self.assert_error("revision_review_incompatible", changed_source, (revision,))

    def test_missing_target_with_review_rejects_whole_revision_atomically(self):
        source, _ = self.source()
        revision = self.first(source)
        missing = AdjustEventZones(EventTarget("missing"), destination_zone_id=A)
        revision = replace(revision, operations=(*revision.operations, missing), reviewed_events=(
            *revision.reviewed_events, replace(revision.reviewed_events[0], event_key="missing")))
        self.assert_error("revision_target_missing", source, (revision,))
        self.assertEqual(effective(composition.compose_revisions(source).published_history, "move").destination_zone_id, B)

    def test_engine_rejection_rejects_whole_revision_and_candidate_atomically(self):
        source, _ = self.source()
        revision = self.first(source, (
            AdjustEventZones(EventTarget("move"), destination_zone_id=C),
            AdjustEventZones(EventTarget("later"), destination_zone_id=999),
        ))
        error = self.assert_error("revision_operations_rejected", source, (revision,))
        self.assertEqual(error.rejections[0].index, 1)
        self.assert_error("revision_operations_rejected", source, candidate=revision)
        self.assertEqual(effective(_project(source), "move").destination_zone_id, B)

    def test_noop_is_explicit_rejection_not_a_silent_decision(self):
        source, _ = self.source()
        revision = self.first(source, (AdjustEventZones(EventTarget("move"), destination_zone_id=B),))
        error = self.assert_error("revision_operations_rejected", source, (revision,))
        self.assertEqual(error.rejections[0].code.value, "no_change")

    def test_exact_and_uncertain_positions_are_not_coerced_across_revisions(self):
        source, _ = self.source()
        positions = (
            TemporalPosition.exact(fixtures.at(6)),
            TemporalPosition.recorded_boundary(fixtures.at(7)),
            TemporalPosition.week(2026, 10, fixtures.PARIS),
            TemporalPosition.observation_interval(fixtures.at(5), fixtures.at(9), ordering_anchor=fixtures.at(4)),
            TemporalPosition.unknown(not_before=fixtures.at(5), before=fixtures.at(10)),
            TemporalPosition.unknown(),
        )
        history = _project(source)
        chain = []
        for index, position in enumerate(positions, 1):
            revision = self.revision(history, (AdjustEventTemporal(EventTarget("move"), position),),
                                     sequence=index, predecessor=chain[-1].revision_id if chain else None)
            chain.append(revision)
            history = composition.compose_revisions(source, tuple(chain)).published_history
            changed = effective(history, "move")
            self.assertIs(changed.temporal, position)
            self.assertIs(changed.original, source.events[1])
            self.assertEqual(revision.reviewed_events[0].temporal,
                             source.events[1].temporal if index == 1 else positions[index - 2])
        self.assertIn("move", [e.key for e in history.unplaced_events])

    def test_dst_fold_edit_and_restore_preserve_elapsed_instants_and_thermal_assignment(self):
        first = datetime(2026, 10, 25, 2, 30, tzinfo=fixtures.PARIS, fold=0)
        second = first.replace(fold=1)
        start = datetime(2026, 10, 24, tzinfo=fixtures.PARIS)
        end = datetime(2026, 10, 26, tzinfo=fixtures.PARIS)
        between = utc(first) + timedelta(minutes=30)
        source, thermal = fixtures.sources([
            fixtures.event("open", EventKind.LOCATION_ASSIGNMENT, start, destination=A),
            fixtures.event("move", EventKind.MOVEMENT, first, A, B),
        ], [fixtures.raw("a", A, between), fixtures.raw("b", B, between, probe=12)], start, end)
        r1 = self.first(source, (AdjustEventTemporal(EventTarget("move"), TemporalPosition.exact(second)),))
        one = composition.compose_revisions(source, (r1,), thermal_source=thermal)
        changed = effective(one.published_history, "move")
        self.assertEqual(changed.temporal.instant.fold, 1)
        self.assertEqual(utc(changed.temporal.instant) - utc(first), timedelta(hours=1))
        self.assertEqual({s.evidence.key for s in one.published_thermal.segments}, {"a"})
        r2 = self.revision(one.published_history, (AdjustEventTemporal(EventTarget("move"), TemporalPosition.exact(first)),),
                            sequence=2, predecessor=r1.revision_id)
        result = composition.compose_revisions(source, (r1, r2), thermal_source=thermal)
        self.assertEqual(effective(result.published_history, "move").temporal.instant.fold, 0)
        self.assertEqual({s.evidence.key for s in result.published_thermal.segments}, {"b"})
        self.assertIs(effective(result.published_history, "move").original, source.events[1])
        stale = replace(r2, reviewed_events=(composition.review_event(effective(result.source_history, "move")),))
        self.assert_error("revision_review_incompatible", source, (r1, stale))

    def test_zero_negative_null_and_all_temperature_source_categories_stay_distinct(self):
        evidence = [
            fixtures.raw("zero", A, fixtures.at(2), "0"),
            fixtures.raw("negative", A, fixtures.at(3), "-3.25", 12),
            replace(fixtures.raw("null", A, fixtures.at(4), probe=13), temperature_c=None),
            ThermalEvidence("setpoint", 1, A, ThermalSourceKind.HISTORICAL_SETPOINT,
                            TemporalPosition.exact(fixtures.at(2)), temperature_c=Decimal(0), box_id=10),
            ThermalEvidence("bucket", 1, A, ThermalSourceKind.CONDENSED_BUCKET,
                            TemporalPosition.observation_interval(fixtures.at(2), fixtures.at(3)),
                            temperature_c=Decimal("-1.01"), temperature_sum_c=Decimal("-3.02"),
                            measurement_count=3, probe_id=11),
            ThermalEvidence("daily", 1, A, ThermalSourceKind.MIXED_DAILY_AGGREGATE,
                            TemporalPosition.observation_interval(fixtures.at(3), fixtures.at(4)),
                            temperature_c=Decimal(0), measurement_count=2),
        ]
        source, thermal = self.source(evidence)
        result = composition.compose_revisions(source, (self.first(source),), thermal_source=thermal)
        segments = {s.evidence.key: s for s in (*result.published_thermal.segments, *result.published_thermal.unassigned)}
        self.assertEqual(set(segments), {e.key for e in evidence})
        for item in evidence:
            self.assertIs(segments[item.key].evidence, item)
        self.assertEqual(segments["zero"].evidence.temperature_c, Decimal(0))
        self.assertEqual(segments["negative"].evidence.temperature_c, Decimal("-3.25"))
        self.assertIsNone(segments["null"].evidence.temperature_c)
        self.assertEqual({s.evidence.source_kind for s in segments.values()}, set(ThermalSourceKind))
        self.assertEqual(result.published_thermal.continuity, "not_inferred")
        missing = next(stay for stay in result.published_thermal.stays if stay.occupancy.started_by == "move")
        self.assertEqual(missing.source_keys, ())
        self.assertIn("temperature_data_unavailable", missing.missing_reasons)

    def test_provisional_scientific_status_survives_restore_and_unrelated_revision(self):
        source, thermal = self.source((fixtures.raw("b", B, fixtures.at(8), probe=12),))
        r1 = self.first(source)
        one = composition.compose_revisions(source, (r1,))
        r2 = self.revision(one.published_history, (AdjustEventZones(EventTarget("move"), destination_zone_id=B),),
                            sequence=2, predecessor=r1.revision_id)
        two = composition.compose_revisions(source, (r1, r2))
        r3 = self.revision(two.published_history, (
            AdjustEventTemporal(EventTarget("later"), TemporalPosition.exact(fixtures.at(16))),
        ), sequence=3, predecessor=r2.revision_id)
        for chain in ((r1, r2), (r1, r2, r3)):
            with self.subTest(length=len(chain)):
                result = composition.compose_revisions(source, chain, thermal_source=thermal)
                changed = effective(result.published_history, "move")
                self.assertEqual(changed.status, EffectiveStatus.MODIFIED)
                self.assertFalse(changed.established)
                interval = next(i for i in result.published_history.intervals if i.started_by == "move")
                self.assertIn("draft_assertion", interval.provisional_reasons)
                self.assertTrue(all("draft_assertion" in s.provisional_reasons
                                    for s in result.published_history.sides if s.event_key == "move"))
                attribution = result.published_thermal.segments[0].attributions[0]
                self.assertFalse(attribution.established)
                self.assertIn("draft_assertion", attribution.provisional_reasons)
                self.assertFalse(result.publication_evaluated)

    def test_semantic_conflicts_are_reported_not_used_as_publication_policy(self):
        source, _ = self.source()
        r1 = self.first(source)
        one = composition.compose_revisions(source, (r1,))
        self.assertIn(ConflictCode.ORIGIN_DESTINATION_DISCONTINUITY,
                      [c.code for c in one.published_history.conflicts])
        r2 = self.revision(one.published_history, (ModifyEvent(
            EventTarget("later"), origin_zone_id=C, destination_zone_id=A),),
                            sequence=2, predecessor=r1.revision_id)
        result = composition.compose_revisions(source, (r1, r2))
        self.assertEqual(result.published_history.conflicts, ())
        self.assertFalse(one.publication_evaluated)
        self.assertFalse(result.publication_evaluated)
        self.assertEqual(len(result.decisions), 2)

    def test_each_revision_allows_100_operations_without_lifetime_100_operation_cap(self):
        events = [fixtures.event(f"event:{index}", EventKind.LOCATION_ASSIGNMENT,
                                fixtures.at(1) + timedelta(hours=index), destination=A) for index in range(100)]
        source, _ = fixtures.sources(events)
        history = _project(source)
        revisions = []
        for sequence, zone in ((1, B), (2, C)):
            operations = tuple(AdjustEventZones(EventTarget(e.identity.key), destination_zone_id=zone) for e in events)
            revision = self.revision(history, operations, sequence=sequence,
                                     predecessor=revisions[-1].revision_id if revisions else None)
            revisions.append(revision)
            history = composition.compose_revisions(source, tuple(revisions)).published_history
        result = composition.compose_revisions(source, tuple(revisions))
        self.assertEqual(len(result.decisions), 200)
        self.assertEqual([d.operation_index for d in result.decisions], list(range(100)) * 2)
        self.assertEqual(result.revisions, tuple(revisions))
        self.assertTrue(all(e.destination_zone_id == C for e in result.published_history.events))

    def test_more_than_100_revisions_preserve_every_decision_without_truncation(self):
        source, _ = fixtures.sources([self.source()[0].events[0]])
        history = _project(source)
        revisions = []
        for sequence in range(1, 106):
            revision = self.revision(history, (AdjustEventZones(EventTarget("open"),
                                                               destination_zone_id=B if sequence % 2 else A),),
                                     sequence=sequence, predecessor=revisions[-1].revision_id if revisions else None)
            revisions.append(revision)
            # Build the next review from the actual previous effective result.
            history = composition.compose_revisions(source, tuple(revisions)).published_history
        result = composition.compose_revisions(source, tuple(revisions))
        self.assertEqual(result.revisions, tuple(revisions))
        self.assertEqual(len(result.revisions), 105)
        self.assertEqual([d.revision_id for d in result.decisions], [r.revision_id for r in revisions])
        self.assertEqual(effective(result.published_history, "open").destination_zone_id, B)
        self.assertIs(effective(result.published_history, "open").original, source.events[0])

    def test_101_operations_in_one_revision_fail_even_when_an_earlier_revision_is_valid(self):
        source, _ = self.source()
        r1 = self.first(source)
        bad = replace(r1, revision_id=identity(2), sequence=2, predecessor_id=r1.revision_id,
                      operations=r1.operations * 101)
        self.assert_error("too_many_operations", source, (r1, bad))
        self.assert_error("too_many_operations", source, (r1,), candidate=bad)

    def test_inputs_remain_immutable_on_success_failure_and_repeated_composition(self):
        source, thermal = self.source((fixtures.raw("zero", A, fixtures.at(3)),))
        operations = [AdjustEventZones(EventTarget("move"), destination_zone_id=C)]
        revision = self.revision(_project(source), operations)
        revisions = [revision]
        anchors = []
        snapshot = _signature((source, thermal, operations, revisions, anchors))
        first = composition.compose_revisions(source, revisions, anchors=anchors, thermal_source=thermal)
        second = composition.compose_revisions(source, revisions, anchors=anchors, thermal_source=thermal)
        self.assertEqual(first, second)
        self.assertEqual(_signature((source, thermal, operations, revisions, anchors)), snapshot)
        self.assertIs(first.source, source)
        self.assertIs(first.revisions[0], revision)
        self.assertEqual(revision.operations, tuple(operations))
        self.assertEqual(source.events[1].destination_zone_id, B)
        self.assert_error("invalid_revision_chain", source, (replace(revision, sequence=2),), thermal_source=thermal)
        self.assertEqual(_signature((source, thermal, operations, revisions, anchors)), snapshot)

    def test_composition_reuses_existing_project_and_provisional_engines_with_effective_fold(self):
        source, _ = self.source()
        r1 = self.first(source)
        one = composition.compose_revisions(source, (r1,))
        r2 = self.revision(one.published_history, (
            AdjustEventTemporal(EventTarget("move"), TemporalPosition.exact(fixtures.at(8))),
        ), sequence=2, predecessor=r1.revision_id)
        with patch(f"{MODULE}._project", wraps=_project) as project, \
                patch(f"{MODULE}._mark_provisional", wraps=_mark_provisional) as provisional:
            result = composition.compose_revisions(source, (r1, r2))
        self.assertEqual(project.call_count, 3)
        self.assertEqual(provisional.call_count, 2)
        self.assertIs(project.call_args_list[0].args[0], source)
        folded = project.call_args_list[2].args[0]
        self.assertEqual(folded.events[1].destination_zone_id, C)
        self.assertEqual(folded.events[1].temporal, source.events[1].temporal)
        self.assertIs(effective(result.published_history, "move").original, source.events[1])
        self.assertEqual(source.events[1].destination_zone_id, B)

    def test_history_budget_fails_explicitly_before_any_engine_projection(self):
        source, _ = self.source()
        with patch(f"{PREVIEW}.MAX_EVENT_COMPARISONS", 1), \
                patch(f"{MODULE}._project", side_effect=AssertionError("must reject before replay")):
            self.assert_error("history_work_limit_exceeded", source)

    def test_insertions_count_toward_history_budget_before_revision_projection(self):
        source, _ = self.source()
        source = replace(source, events=source.events[:2])
        revision = self.revision(_project(source), (
            InsertEvent(identity(700), EventKind.MOVEMENT, TemporalPosition.exact(fixtures.at(10)), B, C),
        ), justifications=(OperationJustification(0, "Reviewed historical insertion"),))
        with patch(f"{PREVIEW}.MAX_EVENT_COMPARISONS", 1), \
                patch(f"{MODULE}._project", wraps=_project) as project:
            self.assert_error("history_work_limit_exceeded", source, (revision,))
        self.assertEqual(project.call_count, 1)

    def test_thermal_overlap_budget_fails_before_history_or_thermal_allocation(self):
        bucket = ThermalEvidence("bucket:1", 1, A, ThermalSourceKind.CONDENSED_BUCKET,
                                 TemporalPosition.observation_interval(fixtures.at(2), fixtures.at(4)),
                                 temperature_c=Decimal(0), probe_id=11)
        source, thermal = self.source((bucket, replace(bucket, key="bucket:2")))
        with patch(f"{PREVIEW}.MAX_STORAGE_OVERLAP_REFERENCES", 1), \
                patch(f"{MODULE}._project", side_effect=AssertionError("must reject before replay")), \
                patch(f"{MODULE}.project_temperature", side_effect=AssertionError("must reject before attribution")):
            self.assert_error("thermal_overlap_limit_exceeded", source, thermal_source=thermal)

    def test_published_thermal_attribution_budget_fails_explicitly_before_projection(self):
        source, thermal = self.source((fixtures.raw("zero", A, fixtures.at(3)),))
        with patch(f"{PREVIEW}.MAX_ATTRIBUTION_WORK", 1), \
                patch(f"{MODULE}.project_temperature", side_effect=AssertionError("must reject before attribution")):
            self.assert_error("thermal_attribution_work_limit_exceeded", source, thermal_source=thermal)

    def test_candidate_thermal_attribution_budget_is_checked_independently(self):
        source, thermal = fixtures.sources([self.source()[0].events[0]],
                                          (fixtures.raw("zero", A, fixtures.at(3)),))
        revision = self.revision(_project(source), (
            InsertEvent(identity(700), EventKind.MOVEMENT, TemporalPosition.exact(fixtures.at(5)), A, B),
        ), justifications=(OperationJustification(0, "Reviewed historical insertion"),))
        with patch(f"{PREVIEW}.MAX_ATTRIBUTION_WORK", 1), \
                patch(f"{MODULE}.project_temperature", wraps=project_temperature) as thermal_engine:
            self.assert_error("thermal_attribution_work_limit_exceeded", source,
                              candidate=revision, thermal_source=thermal)
        self.assertEqual(thermal_engine.call_count, 1)
