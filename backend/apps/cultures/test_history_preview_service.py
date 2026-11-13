"""Authorized history previews against real loaders and isolated Django rows."""

from contextlib import contextmanager
from datetime import timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import OrganizationMembership
from apps.accounts.permissions import user_can_administer_organization
from apps.measurements.models import DailyTemperature
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from . import history_preview as preview_engine
from . import history_preview_service as service
from . import test_effective_temperature_sources as thermal_fixtures
from .effective_history import (
    AdjustEventTemporal, AdjustEventZones, EventKind, EventTarget, InsertEvent,
    ModifyEvent, OccupancyState, RejectionCode, TemporalPosition, TemporalPrecision,
    ThermalSourceKind, to_payload,
)
from .effective_history_sources import HistorySourceNotFound
from .history_preview_validation import OperationJustification, PreviewInputError
from .location_history_diagnostics import _read_only_snapshot
from .models import Box, BoxLocation, BoxMovement, ThermalZone


class HistoryPreviewServiceTests(TestCase):
    # Reuse fixture builders, not the Phase 2 TestCase or its discovered tests.
    probe = thermal_fixtures.ThermalSourceTests.probe
    raw = thermal_fixtures.ThermalSourceTests.raw
    bucket = thermal_fixtures.ThermalSourceTests.bucket
    observe = thermal_fixtures.ThermalSourceTests.observe

    def setUp(self):
        self.org, _ = Organization.objects.update_or_create(
            pk=1, defaults={"name": "Preview laboratory", "is_active": True},
        )
        self.other = Organization.objects.create(name="Other preview laboratory")
        self.admin = get_user_model().objects.create_user(username="preview-admin", email="preview-admin@example.org")
        self.membership = OrganizationMembership.objects.create(
            user=self.admin, organization=self.org, role=OrganizationMembership.Role.ADMIN,
        )
        species = Species.objects.create(scientific_name="Preview synthetic", genus_species_code="PRV")
        self.strain = Strain.objects.create(
            species=species, code="PRV-LAB-1", organization=self.org, number=1, origin_code="LAB",
        )
        self.foreign_strain = Strain.objects.create(
            species=species, code="PRV-EXT-1", organization=self.other, number=1, origin_code="EXT",
        )
        self.a = ThermalZone.objects.create(organization=self.org, name="A", target_temperature_c=Decimal("15"))
        self.b = ThermalZone.objects.create(organization=self.org, name="B", target_temperature_c=Decimal("0"))
        self.c = ThermalZone.objects.create(organization=self.org, name="C", target_temperature_c=Decimal("10"))
        self.foreign = ThermalZone.objects.create(organization=self.other, name="Foreign")
        self.box = self.make_box("001", status=Box.Status.ACTIVE, thermal_zone=self.b)
        self.foreign_box = self.make_box("001", organization=self.other, strain=self.foreign_strain)
        self.start = thermal_fixtures.paris(2026, 2, 9)
        self.end = thermal_fixtures.paris(2026, 2, 16)
        self.moved_at = self.start + timedelta(days=2, hours=12)
        self.later = self.moved_at + timedelta(days=2)
        self.opening = BoxMovement.objects.create(
            box=self.box, to_thermal_zone=self.a, moved_at=self.start,
            kind=BoxMovement.Kind.INITIAL_LOCATION, user=self.admin,
        )
        self.movement = BoxMovement.objects.create(
            box=self.box, from_thermal_zone=self.a, to_thermal_zone=self.b,
            moved_at=self.moved_at, kind=BoxMovement.Kind.ORDINARY, user=self.admin,
        )
        self.target = EventTarget(f"movement:{self.movement.pk}")
        self.readings = []
        for zone in (self.a, self.b, self.c):
            probe = self.probe(f"zone-{zone.pk}", zone=zone)
            for instant in (self.moved_at - timedelta(microseconds=1), self.moved_at,
                            self.moved_at + timedelta(days=1), self.later):
                row = self.raw(probe, at=instant, value=str(zone.target_temperature_c))
                self.readings.append((zone.pk, row))

    def make_box(self, number, *, organization=None, strain=None, status=Box.Status.INACTIVE, **kwargs):
        strain = strain or self.strain
        return Box.objects.create(
            organization=organization or self.org, strain=strain, global_code=f"{strain.code}.{number}",
            box_number=number, status=status, **kwargs,
        )

    def arguments(self, **overrides):
        values = dict(actor=self.admin, organization_id=self.org.pk, box_id=self.box.pk,
                      start=self.start, end=self.end)
        values.update(overrides)
        return values

    def prepare(self, **overrides):
        return service.prepare_box_history_preview(**self.arguments(**overrides))

    def preview(self, **overrides):
        return service.preview_box_history(**self.arguments(**overrides))

    @contextmanager
    def no_loaders(self):
        with patch.object(service, "load_box_source_history") as history, \
                patch.object(service, "load_box_thermal_sources") as thermal:
            yield history, thermal
            history.assert_not_called()
            thermal.assert_not_called()

    def assert_authorization_denied(self, **overrides):
        for entrypoint in (self.prepare, self.preview):
            with self.subTest(entrypoint=entrypoint.__name__), self.no_loaders(), \
                    patch.object(service, "_check_budgets") as budgets:
                with self.assertRaises(PermissionDenied):
                    entrypoint(**overrides)
                budgets.assert_not_called()

    def assert_input_before_reads(self, code, **overrides):
        with self.no_loaders(), self.assertNumQueries(0), self.assertRaises(PreviewInputError) as caught:
            self.preview(**overrides)
        self.assertEqual(caught.exception.code, code)

    def assert_engine_rejection(self, operations, code, *, index=0):
        with self.assertRaises(PreviewInputError) as caught:
            self.preview(operations=operations)
        self.assertEqual(caught.exception.code, "draft_operations_rejected")
        self.assertEqual([(item.index, item.code) for item in caught.exception.rejections], [(index, code)])

    def snapshot(self):
        # Include identities, lineage, receipts and audits, not just the edited Box.
        return {
            model._meta.label: list(model._base_manager.order_by("pk").values())
            for model in apps.get_models() if model._meta.managed and not model._meta.proxy
        }

    def expected_keys(self, boundary, destination):
        return {
            f"temperature_measurement:{row.pk}" for zone, row in self.readings
            if zone == (self.a.pk if row.measured_at < boundary else destination)
        }

    def test_admin_uses_exact_active_institution_and_scoped_references(self):
        OrganizationMembership.objects.create(user=self.admin, organization=self.other, role="viewer")
        inactive_zone = ThermalZone.objects.create(organization=self.org, name="Inactive", is_active=False)
        inactive_probe = self.probe("inactive", zone=inactive_zone, is_active=False)
        foreign_probe = self.probe("foreign", zone=self.foreign, organization=self.other)
        malformed_probe = self.probe("malformed", zone=self.foreign)
        self.raw(foreign_probe)
        self.raw(malformed_probe)
        for entrypoint in (self.prepare, self.preview):
            with self.subTest(entrypoint=entrypoint.__name__), \
                    patch.object(service, "load_box_source_history", wraps=service.load_box_source_history) as history, \
                    patch.object(service, "load_box_thermal_sources", wraps=service.load_box_thermal_sources) as thermal:
                entrypoint()
                history.assert_called_once_with(organization_id=self.org.pk, box_id=self.box.pk)
                thermal.assert_called_once_with(
                    organization_id=self.org.pk, box_id=self.box.pk, start=self.start, end=self.end,
                )
        session = self.prepare()
        self.assertEqual((session.source.organization_id, session.source.box_id), (self.org.pk, self.box.pk))
        self.assertEqual(session.source.zone_ids, frozenset((self.a.pk, self.b.pk, self.c.pk, inactive_zone.pk)))
        self.assertEqual(session.source.zone_ids, session.thermal_source.zone_ids)
        self.assertIn(inactive_probe.pk, session.thermal_source.probe_ids)
        self.assertNotIn(foreign_probe.pk, session.thermal_source.probe_ids)
        self.assertNotIn(malformed_probe.pk, session.thermal_source.probe_ids)
        self.assertEqual(len(session.thermal_source.evidence), len(self.readings))
        self.assertTrue(all(item.organization_id == self.org.pk for item in session.thermal_source.evidence))
        self.assert_authorization_denied(organization_id=self.other.pk, box_id=self.foreign_box.pk)

    def test_active_admin_membership_matrix_for_regular_and_superuser_actors(self):
        for is_superuser in (False, True):
            for role in OrganizationMembership.Role:
                for is_active in (False, True):
                    actor = get_user_model().objects.create_user(
                        username=f"preview-{is_superuser}-{role}-{is_active}",
                        email=f"preview-{int(is_superuser)}-{role}-{int(is_active)}@example.org",
                        is_superuser=is_superuser,
                    )
                    OrganizationMembership.objects.create(
                        user=actor, organization=self.org, role=role, is_active=is_active,
                    )
                    OrganizationMembership.objects.create(
                        user=actor, organization=self.other, role=OrganizationMembership.Role.ADMIN,
                    )
                    with self.subTest(is_superuser=is_superuser, role=role, is_active=is_active):
                        if is_active and role == OrganizationMembership.Role.ADMIN:
                            for entrypoint in (self.prepare, self.preview):
                                with self.subTest(entrypoint=entrypoint.__name__):
                                    result = entrypoint(actor=actor)
                                    source = result.source if isinstance(result, service.HistoryPreviewSession) else result
                                    self.assertEqual((source.organization_id, source.box_id), (self.org.pk, self.box.pk))
                        else:
                            self.assert_authorization_denied(actor=actor)

    def test_stale_active_actor_is_reloaded_and_denied_when_inactive(self):
        for is_superuser in (False, True):
            get_user_model().objects.filter(pk=self.admin.pk).update(is_active=False, is_superuser=is_superuser)
            self.admin.is_superuser = is_superuser
            self.assertTrue(self.admin.is_active)
            with self.subTest(is_superuser=is_superuser):
                self.assert_authorization_denied()

    def test_stale_membership_is_rechecked_after_deactivation_demotion_or_deletion(self):
        for is_superuser in (False, True):
            get_user_model().objects.filter(pk=self.admin.pk).update(is_superuser=is_superuser)
            self.admin.is_superuser = is_superuser
            for changes in (
                {"is_active": False, "role": "admin"},
                {"is_active": True, "role": "lab_technician"},
                {"is_active": True, "role": "viewer"},
            ):
                OrganizationMembership.objects.filter(pk=self.membership.pk).update(**changes)
                self.assertTrue(self.membership.is_active)
                self.assertEqual(self.membership.role, "admin")
                with self.subTest(is_superuser=is_superuser, changes=changes):
                    self.assert_authorization_denied()
        OrganizationMembership.objects.filter(pk=self.membership.pk).delete()
        self.assert_authorization_denied()

    def test_stale_active_organization_is_reloaded_and_denied_when_inactive(self):
        Organization.objects.filter(pk=self.org.pk).update(is_active=False)
        self.assertTrue(self.org.is_active)
        for is_superuser in (False, True):
            get_user_model().objects.filter(pk=self.admin.pk).update(is_superuser=is_superuser)
            self.admin.is_superuser = is_superuser
            with self.subTest(is_superuser=is_superuser):
                self.assert_authorization_denied()

    def test_missing_institution_is_denied_even_with_admin_membership_elsewhere(self):
        for is_superuser in (False, True):
            get_user_model().objects.filter(pk=self.admin.pk).update(is_superuser=is_superuser)
            self.admin.is_superuser = is_superuser
            with self.subTest(is_superuser=is_superuser):
                self.assert_authorization_denied(organization_id=self.other.pk + 10000)

    def test_anonymous_unsaved_and_nonexistent_actors_are_denied(self):
        missing = get_user_model()(pk=self.admin.pk + 10000, username="missing")
        missing._state.adding = False
        for actor in (None, AnonymousUser(), get_user_model()(username="unsaved"), missing):
            with self.subTest(actor=type(actor).__name__):
                self.assert_authorization_denied(actor=actor)

    def test_foreign_missing_and_merged_box_are_not_followed_or_loaded(self):
        merged = self.make_box("002", merged_into=self.box)
        OrganizationMembership.objects.create(
            user=self.admin, organization=self.other, role=OrganizationMembership.Role.ADMIN,
        )
        for is_superuser in (False, True):
            get_user_model().objects.filter(pk=self.admin.pk).update(is_superuser=is_superuser)
            self.admin.is_superuser = is_superuser
            for entrypoint in (self.prepare, self.preview):
                for box_id in (self.foreign_box.pk, merged.pk, self.box.pk + 10000):
                    with self.subTest(is_superuser=is_superuser, entrypoint=entrypoint.__name__, box_id=box_id), \
                            self.no_loaders(), patch.object(service, "_check_budgets") as budgets:
                        with self.assertRaises(HistorySourceNotFound) as caught:
                            entrypoint(box_id=box_id)
                        self.assertEqual(str(caught.exception), "No such Box in this institution.")
                        budgets.assert_not_called()
                with self.subTest(is_superuser=is_superuser, entrypoint=entrypoint.__name__, scope="other"):
                    result = entrypoint(organization_id=self.other.pk, box_id=self.foreign_box.pk)
                    source = result.source if isinstance(result, service.HistoryPreviewSession) else result
                    self.assertEqual((source.organization_id, source.box_id), (self.other.pk, self.foreign_box.pk))

    def test_no_membership_is_denied_despite_unchanged_shared_superuser_break_glass(self):
        for is_superuser in (False, True):
            actor = get_user_model().objects.create_user(
                username=f"preview-no-membership-{is_superuser}",
                email=f"preview-no-membership-{int(is_superuser)}@example.org", is_superuser=is_superuser,
            )
            self.assertFalse(OrganizationMembership.objects.filter(user=actor).exists())
            self.assertEqual(user_can_administer_organization(actor, self.org), is_superuser)
            with self.subTest(is_superuser=is_superuser):
                self.assert_authorization_denied(actor=actor)
                OrganizationMembership.objects.create(
                    user=actor, organization=self.other, role=OrganizationMembership.Role.ADMIN,
                )
                self.assert_authorization_denied(actor=actor)

    def test_invalid_explicit_scope_ids_are_rejected_before_any_reads(self):
        for field in ("organization_id", "box_id"):
            for value in (None, 0, -1, True, "1"):
                with self.subTest(field=field, value=value):
                    self.assert_input_before_reads("invalid_scope", **{field: value})
                    with self.no_loaders(), self.assertNumQueries(0), self.assertRaises(PreviewInputError) as caught:
                        self.prepare(**{field: value})
                    self.assertEqual(caught.exception.code, "invalid_scope")

    def test_invalid_windows_are_rejected_before_reads_and_366_days_is_inclusive(self):
        for overrides, code in (
            ({"start": self.start.replace(tzinfo=None)}, "invalid_window"),
            ({"end": None}, "invalid_window"),
            ({"end": self.start}, "invalid_window"),
            ({"end": self.start - timedelta(seconds=1)}, "invalid_window"),
            ({"end": self.start + timedelta(days=366, microseconds=1)}, "window_too_large"),
        ):
            with self.subTest(overrides=overrides):
                self.assert_input_before_reads(code, **overrides)
                with self.no_loaders(), self.assertNumQueries(0), self.assertRaises(PreviewInputError) as caught:
                    self.prepare(**overrides)
                self.assertEqual(caught.exception.code, code)
        session = self.prepare(end=self.start + timedelta(days=366))
        self.assertEqual(session.thermal_source.end - session.thermal_source.start, timedelta(days=366))
        self.assertEqual(session.thermal_source.start.utcoffset(), timedelta(0))

    def test_invalid_operation_anchor_and_justification_fail_before_reads(self):
        cases = (
            ({"operations": ({"target": self.target.key},)}, "invalid_operation"),
            ({"operations": (AdjustEventZones(self.target, destination_zone_id=True),)}, "invalid_zone_id"),
            ({"operations": (InsertEvent("restart", EventKind.REACTIVATION,
                                         TemporalPosition.exact(self.later), destination_zone_id=self.c.pk),)},
             "missing_insertion_justification"),
            ({"anchors": (object(),)}, "invalid_anchor"),
            ({"justifications": (OperationJustification(0, "reason"),)}, "invalid_justification"),
        )
        for overrides, code in cases:
            with self.subTest(code=code):
                self.assert_input_before_reads(code, **overrides)
        with self.no_loaders(), self.assertNumQueries(0), self.assertRaises(PreviewInputError) as caught:
            self.prepare(anchors=(object(),))
        self.assertEqual(caught.exception.code, "invalid_anchor")

    def test_foreign_and_other_box_movement_targets_are_engine_rejections(self):
        local_other = self.make_box("003")
        for box, zone in ((local_other, self.a), (self.foreign_box, self.foreign)):
            movement = BoxMovement.objects.create(
                box=box, to_thermal_zone=zone, moved_at=self.start, kind=BoxMovement.Kind.INITIAL_LOCATION,
            )
            with self.subTest(box_id=box.pk):
                self.assert_engine_rejection(
                    (AdjustEventTemporal(EventTarget(f"movement:{movement.pk}"), TemporalPosition.exact(self.later)),),
                    RejectionCode.TARGET_NOT_FOUND,
                )

    def test_foreign_zone_is_rejected_by_engine_not_silently_dropped(self):
        self.assert_engine_rejection(
            (AdjustEventZones(self.target, destination_zone_id=self.foreign.pk),),
            RejectionCode.ZONE_OUTSIDE_INSTITUTION,
        )

    def test_batch_never_returns_partial_acceptance_or_contaminates_session(self):
        session = self.prepare()
        original = to_payload(session.before)
        good = AdjustEventZones(self.target, destination_zone_id=self.c.pk)
        bad = AdjustEventZones(self.target, destination_zone_id=self.foreign.pk)
        with patch.object(preview_engine, "project_temperature", wraps=preview_engine.project_temperature) as thermal:
            with self.assertNumQueries(0), self.assertRaises(PreviewInputError) as caught:
                session.preview(operations=(good, bad))
            self.assertEqual(caught.exception.code, "draft_operations_rejected")
            self.assertEqual([(item.index, item.code) for item in caught.exception.rejections],
                             [(1, RejectionCode.ZONE_OUTSIDE_INSTITUTION)])
            thermal.assert_not_called()
        self.assertEqual(to_payload(session.before), original)
        self.assertIs(session.preview().after, session.before)
        self.assertTrue(session.preview(operations=(good,)).event_changes)
        self.assert_engine_rejection((good, bad), RejectionCode.ZONE_OUTSIDE_INSTITUTION, index=1)

    def test_pipeline_is_select_only_and_all_model_rows_remain_unchanged(self):
        self.observe(7, "15")
        self.observe(8, "0")
        self.bucket(self.probe("bucket", zone=self.b))
        DailyTemperature.objects.create(
            thermal_zone=self.b, date=self.moved_at.date(), average_temperature_c=Decimal("0"),
        )
        before = self.snapshot()
        with CaptureQueriesContext(connection) as queries:
            session = self.prepare(end=self.end + timedelta(days=7))
            for operation in (
                AdjustEventZones(self.target, destination_zone_id=self.c.pk),
                AdjustEventTemporal(self.target, TemporalPosition.exact(self.later)),
                ModifyEvent(self.target, kind=EventKind.DEACTIVATION, destination_zone_id=None),
            ):
                session.preview(operations=(operation,))
            self.preview(operations=(AdjustEventZones(self.target, destination_zone_id=self.c.pk),))
        self.assertTrue(queries.captured_queries)
        self.assertTrue(all(item["sql"].lstrip().upper().startswith("SELECT") for item in queries))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual({item.source_kind for item in session.thermal_source.evidence}, set(ThermalSourceKind))

    def test_preflight_budgets_abort_before_either_source_loader(self):
        for constant, code in (
            ("MAX_HISTORY_ROWS", "history_row_limit_exceeded"),
            ("MAX_REFERENCE_ROWS", "reference_row_limit_exceeded"),
            ("MAX_THERMAL_EVIDENCE", "thermal_evidence_limit_exceeded"),
        ):
            with self.subTest(constant=constant), patch.object(service, constant, 1), self.no_loaders():
                with CaptureQueriesContext(connection) as queries, self.assertRaises(PreviewInputError) as caught:
                    self.prepare()
                self.assertEqual(caught.exception.code, code)
                self.assertTrue(any("COUNT(" in item["sql"].upper() for item in queries))
                self.assertTrue(all(item["sql"].lstrip().upper().startswith("SELECT") for item in queries))

    def test_loaded_expansion_cap_rejects_complete_output_before_projection(self):
        box = self.make_box("004")
        period = BoxLocation.objects.create(
            box=box, thermal_zone=self.a, starts_at=self.start, ends_at=self.moved_at,
        )
        with patch.object(service, "MAX_HISTORY_ROWS", 1), \
                patch.object(service, "load_box_source_history", wraps=service.load_box_source_history) as history, \
                patch.object(service, "load_box_thermal_sources", wraps=service.load_box_thermal_sources) as thermal, \
                patch.object(service, "HistoryPreviewSession", wraps=service.HistoryPreviewSession) as session:
            with self.assertRaises(PreviewInputError) as caught:
                self.prepare(box_id=box.pk)
            self.assertEqual(caught.exception.code, "history_row_limit_exceeded")
            history.assert_called_once_with(organization_id=self.org.pk, box_id=box.pk)
            thermal.assert_called_once()
            session.assert_not_called()
        self.assertTrue(BoxLocation.objects.filter(pk=period.pk).exists())

    def test_preparation_projects_baseline_once_and_each_draft_once_without_queries(self):
        with patch.object(preview_engine, "project_history", wraps=preview_engine.project_history) as history, \
                patch.object(preview_engine, "project_temperature", wraps=preview_engine.project_temperature) as thermal:
            session = self.prepare()
            self.assertEqual((history.call_count, thermal.call_count), (1, 1))
            for index, operation in enumerate((
                AdjustEventZones(self.target, destination_zone_id=self.c.pk),
                AdjustEventTemporal(self.target, TemporalPosition.exact(self.later)),
                ModifyEvent(self.target, kind=EventKind.DEACTIVATION, destination_zone_id=None),
            ), start=2):
                with self.assertNumQueries(0):
                    result = session.preview(operations=(operation,))
                self.assertIs(result.before, session.before)
                self.assertEqual((history.call_count, thermal.call_count), (index, index))
                self.assertIs(history.call_args.args[0], session.source)
                self.assertIs(thermal.call_args.args[1], session.thermal_source)

    def test_empty_drafts_reuse_baseline_without_database_or_recalculation(self):
        session = self.prepare()
        with patch.object(preview_engine, "project_history") as history, \
                patch.object(preview_engine, "project_temperature") as thermal, self.assertNumQueries(0):
            for operations in ((), [], ()):
                result = session.preview(operations=operations)
                self.assertIs(result.before, session.before)
                self.assertIs(result.after, session.before)
                for name in ("event_changes", "interval_changes", "side_changes", "thermal_changes",
                             "stay_changes", "gap_changes", "affected_event_keys"):
                    self.assertEqual(getattr(result, name), ())
            history.assert_not_called()
            thermal.assert_not_called()

    def test_query_count_is_finite_and_constant_as_rows_probes_and_operations_grow(self):
        first_operation = AdjustEventTemporal(self.target, TemporalPosition.exact(self.later))
        with CaptureQueriesContext(connection) as initial_queries:
            first = self.preview(operations=(first_operation,))
        operations = [first_operation]
        for index in range(12):
            probe = self.probe(f"extra-{index}", zone=self.b)
            self.raw(probe, at=self.start + timedelta(hours=index + 1), value="0")
            BoxMovement.objects.create(
                box=self.box, from_thermal_zone=self.a, to_thermal_zone=self.b,
                moved_at=self.start + timedelta(hours=index + 1), kind=BoxMovement.Kind.UNCLASSIFIED,
            )
            operations.append(AdjustEventTemporal(
                self.target, TemporalPosition.exact(self.later + timedelta(minutes=index + 1)),
            ))
        with CaptureQueriesContext(connection) as expanded_queries:
            expanded = self.preview(operations=operations)
        self.assertGreater(len(initial_queries), 0)
        self.assertLessEqual(len(initial_queries), 50)
        self.assertEqual(len(expanded_queries), len(initial_queries))
        self.assertEqual(len(expanded.before.history.events), len(first.before.history.events) + 12)
        self.assertEqual(len(expanded.before.thermal.segments) + len(expanded.before.thermal.unassigned),
                         len(first.before.thermal.segments) + len(first.before.thermal.unassigned) + 12)
        event = next(item for item in expanded.after.history.events if item.key == self.target.key)
        self.assertEqual(len(event.applied_operations), len(operations))

    def test_drafts_preserve_source_identities_fingerprints_and_whole_evidence(self):
        self.observe(7, "15")
        self.bucket(self.probe("whole-bucket", zone=self.b), last_value=Decimal("0"))
        DailyTemperature.objects.create(
            thermal_zone=self.b, date=self.moved_at.date(), average_temperature_c=Decimal("0"),
        )
        session = self.prepare()
        original_source = to_payload(session.source)
        original_thermal = to_payload(session.thermal_source)
        events = {item.identity.key: item for item in session.source.events}
        evidence = {item.key: item for item in session.thermal_source.evidence}
        with self.assertNumQueries(0):
            result = session.preview(operations=(AdjustEventZones(self.target, destination_zone_id=self.c.pk),))
            for event in (*result.after.history.events, *result.after.history.unplaced_events):
                self.assertIs(event.original, events[event.key])
                self.assertIs(event.identity, events[event.key].identity)
                self.assertEqual(event.sources, events[event.key].sources)
            for segment in (*result.after.thermal.segments, *result.after.thermal.unassigned):
                self.assertIs(segment.evidence, evidence[segment.evidence.key])
            for change in result.thermal_changes:
                self.assertIs(change.before.evidence, change.after.evidence)
            self.assertEqual(to_payload(session.source), original_source)
            self.assertEqual(to_payload(session.thermal_source), original_thermal)
        fresh = self.prepare()
        self.assertEqual(fresh.source, session.source)
        self.assertEqual(fresh.thermal_source, session.thermal_source)
        self.assertTrue(any(item.temperature_c == Decimal("0") for item in evidence.values()))
        self.assertTrue(any(item.last_temperature_c is None for item in evidence.values()))

    def test_thermal_selection_tracks_movement_destination_date_and_type(self):
        session = self.prepare()
        self.assertEqual({item.evidence.key for item in session.before.thermal.segments},
                         self.expected_keys(self.moved_at, self.b.pk))
        all_keys = {item.key for item in session.thermal_source.evidence}
        for name, operation, boundary, destination in (
            ("destination", AdjustEventZones(self.target, destination_zone_id=self.c.pk), self.moved_at, self.c.pk),
            ("date", AdjustEventTemporal(self.target, TemporalPosition.exact(self.later)), self.later, self.b.pk),
            ("type", ModifyEvent(self.target, kind=EventKind.DEACTIVATION, destination_zone_id=None),
             self.moved_at, None),
        ):
            with self.subTest(name=name), self.assertNumQueries(0):
                result = session.preview(operations=(operation,))
                expected = self.expected_keys(boundary, destination)
                self.assertEqual({item.evidence.key for item in result.after.thermal.segments}, expected)
                self.assertEqual({item.evidence.key for item in result.after.thermal.unassigned}, all_keys - expected)
                self.assertTrue(result.event_changes)
                self.assertTrue(result.interval_changes)
                self.assertTrue(result.thermal_changes)
                self.assertTrue(result.stay_changes)
                self.assertIn(self.target.key, result.affected_event_keys)
                self.assertFalse(result.after.safety.publication_evaluated)
                for segment in result.after.thermal.segments:
                    self.assertEqual(segment.evidence.temporal.precision, TemporalPrecision.EXACT)
                    for attribution in segment.attributions:
                        self.assertFalse(attribution.established)
                        self.assertIn("draft_assertion", attribution.provisional_reasons)
                if name == "type":
                    (gap,) = [item for item in result.after.thermal.gaps if item.reason == "inactive"]
                    self.assertEqual((gap.start, gap.end),
                                     (self.moved_at.astimezone(timezone.utc), session.thermal_source.end))

    def test_reactivation_uses_explicit_new_zone_after_inactive_gap(self):
        session = self.prepare()
        reason = OperationJustification(1, "Synthetic reactivation in the explicitly selected zone")
        with self.assertNumQueries(0):
            result = session.preview(operations=(
                ModifyEvent(self.target, kind=EventKind.DEACTIVATION, destination_zone_id=None),
                InsertEvent("restart", EventKind.REACTIVATION, TemporalPosition.exact(self.later),
                            destination_zone_id=self.c.pk),
            ), justifications=(reason,))
        expected = {
            f"temperature_measurement:{row.pk}" for zone, row in self.readings
            if (zone == self.a.pk and row.measured_at < self.moved_at)
            or (zone == self.c.pk and row.measured_at >= self.later)
        }
        self.assertEqual({item.evidence.key for item in result.after.thermal.segments}, expected)
        self.assertEqual([(item.state, item.zone_id) for item in result.after.history.intervals], [
            (OccupancyState.LOCATED, self.a.pk), (OccupancyState.INACTIVE, None), (OccupancyState.LOCATED, self.c.pk),
        ])
        (gap,) = [item for item in result.after.thermal.gaps if item.reason == "inactive"]
        self.assertEqual((gap.start, gap.end),
                         (self.moved_at.astimezone(timezone.utc), self.later.astimezone(timezone.utc)))
        self.assertEqual(result.justifications, (reason,))
        self.assertIn("draft:restart", result.affected_event_keys)
        self.assertEqual(result.after.thermal.continuity, "not_inferred")
        self.assertTrue(result.gap_changes)

    def test_outer_snapshot_encloses_authorization_budgets_and_both_real_loaders(self):
        active = False
        calls = []

        @contextmanager
        def recording_snapshot():
            nonlocal active
            with _read_only_snapshot():
                active = True
                calls.append("enter")
                try:
                    yield
                finally:
                    active = False
                    calls.append("exit")

        def inside_snapshot(name, function):
            def checked(*args, **kwargs):
                self.assertTrue(active)
                calls.append(name)
                return function(*args, **kwargs)
            return checked

        with patch.object(service, "_read_only_snapshot", recording_snapshot), \
                patch.object(service, "_authorized_box", inside_snapshot("authorize", service._authorized_box)), \
                patch.object(service, "_check_budgets", inside_snapshot("budget", service._check_budgets)), \
                patch.object(service, "load_box_source_history", inside_snapshot("history", service.load_box_source_history)), \
                patch.object(service, "load_box_thermal_sources", inside_snapshot("thermal", service.load_box_thermal_sources)):
            session = self.prepare()
        self.assertEqual(calls, ["enter", "authorize", "budget", "history", "thermal", "exit"])
        self.assertEqual(session.source.box_id, self.box.pk)
