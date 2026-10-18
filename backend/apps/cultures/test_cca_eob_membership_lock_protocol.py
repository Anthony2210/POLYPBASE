"""PostgreSQL-only lock order between the CCA-EOB corrector and member PATCH."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.api_views import OrganizationMembershipDetailAPIView
from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement

from . import services as culture_services
from .historical_2026 import cca_eob_correction as correction
from .models import Box, SubcultureAllocation, SubcultureEvent, ThermalZone
from .polyp_state import resolve_current_polyp_state
from .test_cca_eob_correction import CcaEobFixtureMixin

WAIT = 8


def sqlstate(error):
    while error is not None:
        if getattr(error, "sqlstate", None):
            return error.sqlstate
        error = error.__cause__
    return None


@skipUnless(connection.vendor == "postgresql", "Lock order checks require PostgreSQL row locks.")
class CcaEobMembershipLockProtocolTests(CcaEobFixtureMixin, TransactionTestCase):
    """Corrector: Organization, Box, membership. Member PATCH: Organization, membership."""

    reset_sequences = True

    def setUp(self):
        super().setUp()
        self.manager = get_user_model().objects.create_user(
            username="cca-responsable", email="cca-responsable@example.org",
        )
        OrganizationMembership.objects.create(
            organization=self.org, user=self.manager, role="admin", is_responsable=True,
        )
        self.admin_membership = OrganizationMembership.objects.get(
            organization=self.org, user=self.admin,
        )
        self.pids = {}
        self.at_audit = threading.Event()
        self.release = threading.Event()
        self.addCleanup(self.release.set)

    def worker(self, kind, action, *, pause_on_audit=False, fail_after_pause=False):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                self.pids[kind] = cursor.fetchone()[0]
            paused = False

            def observe(execute, sql, params, many, context):
                nonlocal paused
                if pause_on_audit and not paused and sql.upper().startswith('INSERT INTO "AUDIT_AUDITLOG"'):
                    paused = True
                    self.at_audit.set()
                    if not self.release.wait(WAIT):
                        raise TimeoutError("Audit gate was not released.")
                    if fail_after_pause:
                        raise RuntimeError("Synthetic corrector rollback")
                return execute(sql, params, many, context)

            with connection.execute_wrapper(observe):
                return "ok", action()
        except Exception as error:
            return "error", error
        finally:
            connections.close_all()

    def patch_member(self, data):
        def action():
            request = APIRequestFactory().patch(
                "/qa/members/", data, format="json", HTTP_X_ORGANIZATION_ID=str(self.org.pk),
            )
            force_authenticate(request, user=self.manager)
            response = OrganizationMembershipDetailAPIView.as_view()(
                request, pk=self.admin_membership.pk,
            )
            response.render()
            return response.status_code
        return action

    def assert_waits_for(self, waiter, holder):
        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline:
            if waiter in self.pids and holder in self.pids:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_blocking_pids(%s)", [self.pids[waiter]])
                    if self.pids[holder] in cursor.fetchone()[0]:
                        return
            time.sleep(0.02)
        self.fail(f"{waiter} never waited for {holder}.")

    def state(self):
        return {
            "measurements": list(BiologicalMeasurement.objects.order_by("pk").values()),
            "boxes": list(Box.objects.order_by("pk").values()),
        }

    def correction_audits(self):
        return AuditLog.objects.filter(metadata__operation=correction.OPERATION)

    def member_audits(self):
        return AuditLog.objects.filter(description="Member access updated")

    def assert_no_deadlock(self, *outcomes):
        for kind, value in outcomes:
            if kind == "error":
                self.assertNotEqual(sqlstate(value), "40P01", repr(value))

    def assert_unchanged(self, before):
        self.assertEqual(self.state(), before)
        self.assertFalse(self.correction_audits().exists())

    def test_correction_holding_locks_then_member_patch_waits_without_deadlock(self):
        plan = self.plan()
        self.assertTrue(plan["ready"], plan["blockers"])
        before = self.state()
        moved = set(plan["move_order"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            corrector = pool.submit(self.worker, "corrector", lambda: self.apply(plan=plan),
                                    pause_on_audit=True)
            self.assertTrue(self.at_audit.wait(WAIT))
            patch = pool.submit(self.worker, "patch", self.patch_member({"role": "viewer"}))
            self.assert_waits_for("patch", "corrector")
            self.assertFalse(patch.done())
            self.release.set()
            corrected, patched = corrector.result(timeout=15), patch.result(timeout=15)
        self.assert_no_deadlock(corrected, patched)
        self.assertEqual(corrected, ("ok", plan))
        self.assertEqual(patched, ("ok", 200))
        self.admin_membership.refresh_from_db()
        self.assertEqual(self.admin_membership.role, "viewer")
        self.assertEqual(self.correction_audits().count(), 4)
        self.assertEqual(self.member_audits().count(), 1)
        # Commit order is coherent: the correction committed before the PATCH.
        self.assertGreater(self.member_audits().get().pk, self.correction_audits().order_by("-pk").first().pk)
        after = self.state()
        for old, new in zip(before["measurements"], after["measurements"]):
            if old["id"] in moved:
                self.assertEqual({**old, "box_id": new["box_id"]}, new)
                self.assertNotEqual(old["box_id"], new["box_id"])
            else:
                self.assertEqual(old, new)

    def revoked_patch_then_correction(self, data):
        plan = self.plan()
        before = self.state()
        audits_before = AuditLog.objects.count()
        with ThreadPoolExecutor(max_workers=2) as pool:
            patch = pool.submit(self.worker, "patch", self.patch_member(data), pause_on_audit=True)
            self.assertTrue(self.at_audit.wait(WAIT))
            corrector = pool.submit(self.worker, "corrector", lambda: self.apply(plan=plan))
            self.assert_waits_for("corrector", "patch")
            self.assertFalse(corrector.done())
            self.release.set()
            patched, corrected = patch.result(timeout=15), corrector.result(timeout=15)
        self.assert_no_deadlock(patched, corrected)
        self.assertEqual(patched, ("ok", 200))
        self.assertEqual(corrected[0], "error")
        self.assertIsInstance(corrected[1], PermissionDenied)
        self.assert_unchanged(before)
        self.assertEqual(AuditLog.objects.count(), audits_before + 1)
        self.assertEqual(self.member_audits().count(), 1)

    def test_role_revoked_first_then_waiting_correction_is_denied_without_deadlock(self):
        self.revoked_patch_then_correction({"role": "viewer"})
        self.admin_membership.refresh_from_db()
        self.assertEqual(self.admin_membership.role, "viewer")

    def test_membership_deactivated_first_then_waiting_correction_is_denied_without_deadlock(self):
        self.revoked_patch_then_correction({"is_active": False})
        self.admin_membership.refresh_from_db()
        self.assertFalse(self.admin_membership.is_active)

    def test_correction_rollback_releases_waiting_member_patch_with_nothing_persisted(self):
        plan = self.plan()
        before = self.state()
        with ThreadPoolExecutor(max_workers=2) as pool:
            corrector = pool.submit(self.worker, "corrector", lambda: self.apply(plan=plan),
                                    pause_on_audit=True, fail_after_pause=True)
            self.assertTrue(self.at_audit.wait(WAIT))
            patch = pool.submit(self.worker, "patch", self.patch_member({"role": "viewer"}))
            self.assert_waits_for("patch", "corrector")
            self.release.set()
            corrected, patched = corrector.result(timeout=15), patch.result(timeout=15)
        self.assert_no_deadlock(corrected, patched)
        self.assertEqual(corrected[0], "error")
        self.assertIsInstance(corrected[1], RuntimeError)
        self.assertEqual(patched, ("ok", 200))
        self.assert_unchanged(before)
        self.assertEqual(AuditLog.objects.count(), 1)
        self.assertEqual(self.member_audits().count(), 1)

    def test_unrelated_subculture_commits_while_member_patch_waits_behind_correction(self):
        parent = Box.objects.create(
            organization=self.org, strain=self.strain, thermal_zone=self.zone,
            global_code="CCA-EOB-2.099", box_number="099",
        )
        BiologicalMeasurement.objects.create(
            box=parent, measured_on=date(2026, 9, 1), polyp_count=9, ephyrae_count=0,
        )
        parent.refresh_from_db()
        state = resolve_current_polyp_state(parent)
        zone = ThermalZone.objects.create(organization=self.org, name="Unrelated lock protocol zone")
        plan = self.plan()

        def subculture():
            event, _children = culture_services.create_subculture(
                parent_box=parent, user=self.admin, organization=self.org,
                expected_current_state_revision=state["revision"],
                children=[{"thermal_zone": zone, "allocated_polyps": 0}],
            )
            return event.pk

        with ThreadPoolExecutor(max_workers=3) as pool:
            corrector = pool.submit(self.worker, "corrector", lambda: self.apply(plan=plan),
                                    pause_on_audit=True)
            self.assertTrue(self.at_audit.wait(WAIT))
            patch = pool.submit(self.worker, "patch", self.patch_member({"role": "viewer"}))
            self.assert_waits_for("patch", "corrector")
            # The queued PATCH must not make an ordinary subculture wait or deadlock.
            created = pool.submit(self.worker, "subculture", subculture).result(timeout=WAIT)
            self.assertEqual(created[0], "ok", repr(created[1]))
            self.assertFalse(corrector.done())
            self.assertFalse(patch.done())
            self.release.set()
            corrected, patched = corrector.result(timeout=15), patch.result(timeout=15)
        self.assert_no_deadlock(created, corrected, patched)
        self.assertEqual(corrected, ("ok", plan))
        self.assertEqual(patched, ("ok", 200))
        event = SubcultureEvent.objects.get(pk=created[1])
        self.assertEqual(event.parent_state_snapshot, state)
        self.assertEqual(SubcultureAllocation.objects.get(event=event).allocated_polyps, 0)
        self.assertEqual(self.correction_audits().count(), 4)
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.SUBCULTURE).count(), 1)
        self.assertEqual(self.member_audits().count(), 1)

    def test_member_patch_first_then_correction_and_subculture_are_denied_without_cycle(self):
        parent = Box.objects.create(
            organization=self.org, strain=self.strain, thermal_zone=self.zone,
            global_code="CCA-EOB-2.099", box_number="099",
        )
        BiologicalMeasurement.objects.create(
            box=parent, measured_on=date(2026, 9, 1), polyp_count=9, ephyrae_count=0,
        )
        parent.refresh_from_db()
        state = resolve_current_polyp_state(parent)
        zone = ThermalZone.objects.create(organization=self.org, name="Reverse lock protocol zone")
        plan = self.plan()
        before = self.state()

        def subculture():
            event, _children = culture_services.create_subculture(
                parent_box=parent, user=self.admin, organization=self.org,
                expected_current_state_revision=state["revision"],
                children=[{"thermal_zone": zone, "allocated_polyps": 0}],
            )
            return event.pk

        with ThreadPoolExecutor(max_workers=3) as pool:
            patch = pool.submit(self.worker, "patch", self.patch_member({"role": "viewer"}),
                                pause_on_audit=True)
            self.assertTrue(self.at_audit.wait(WAIT))
            corrector = pool.submit(self.worker, "corrector", lambda: self.apply(plan=plan))
            created = pool.submit(self.worker, "subculture", subculture)
            self.assert_waits_for("corrector", "patch")
            self.release.set()
            patched = patch.result(timeout=15)
            corrected, created = corrector.result(timeout=15), created.result(timeout=15)
        self.assert_no_deadlock(patched, corrected, created)
        self.assertEqual(patched, ("ok", 200))
        self.assertEqual(corrected[0], "error")
        self.assertIsInstance(corrected[1], PermissionDenied)
        # No priority rule is imposed: the subculture either read the permission
        # before the PATCH committed or is denied after it. Both are serial orders.
        after = self.state()
        self.assertEqual(after["measurements"], before["measurements"])
        # A committed subculture legitimately advances its parent and adds child boxes.
        existing = {box["id"] for box in before["boxes"]} - {parent.pk}
        self.assertEqual([box for box in after["boxes"] if box["id"] in existing],
                         [box for box in before["boxes"] if box["id"] in existing])
        self.assertFalse(self.correction_audits().exists())
        if created[0] == "ok":
            self.assertEqual(SubcultureEvent.objects.count(), 1)
            self.assertEqual(AuditLog.objects.count(), 2)
        else:
            self.assertIsInstance(created[1], PermissionDenied)
            self.assertFalse(SubcultureEvent.objects.exists())
            self.assertEqual(AuditLog.objects.count(), 1)
