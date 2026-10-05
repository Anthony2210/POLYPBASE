"""Real PostgreSQL races, with independent connections and observed lock waits."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, connection, connections
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .api_views import BoxMeasurementDetailAPIView, BoxMeasurementListCreateAPIView, BoxSubcultureCreateAPIView
from .models import Box, BoxCodeNamespace, SubcultureAllocation, SubcultureEvent, ThermalZone
from .polyp_state import resolve_current_polyp_state


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks and independent transactions.")
class QuantitativeSubcultureConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Disposable quantitative concurrency QA")
        self.user = get_user_model().objects.create_user(username="concurrent-quantitative", email="qa@example.org")
        OrganizationMembership.objects.create(user=self.user, organization=self.org, role="admin")
        species = Species.objects.create(scientific_name="Aurelia concurrency", genus_species_code="ACC")
        self.strain = Strain.objects.create(organization=self.org, species=species, code="ACC-QA-1")
        self.zone = ThermalZone.objects.create(organization=self.org, name="First zone")
        self.other_zone = ThermalZone.objects.create(organization=self.org, name="Second zone")
        self.box = Box.objects.create(organization=self.org, strain=self.strain, thermal_zone=self.zone,
                                      global_code="ACC-QA-1.001", box_number="001")
        self.other = Box.objects.create(organization=self.org, strain=self.strain, thermal_zone=self.other_zone,
                                        global_code="ACC-QA-1.002", box_number="002")
        self.measurement = BiologicalMeasurement.objects.create(box=self.box,
            measured_on=timezone.localdate() - timedelta(days=7), polyp_count=100, user=self.user)
        BiologicalMeasurement.objects.create(box=self.other, measured_on=timezone.localdate(), polyp_count=100, user=self.user)
        self.box.refresh_from_db()
        self.other.refresh_from_db()
        self.revision = resolve_current_polyp_state(self.box)["revision"]
        self.other_revision = resolve_current_polyp_state(self.other)["revision"]
        self.local = threading.local()

    def _request(self, view, method, payload, **kwargs):
        request = getattr(APIRequestFactory(), method)("/isolated-qa/", payload, format="json",
            HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        force_authenticate(request, user=self.user)
        response = view.as_view()(request, **kwargs)
        response.render()
        return response.status_code, response.data

    def _subculture(self, box=None, value=70, revision=None):
        box = box or self.box
        return self._request(BoxSubcultureCreateAPIView, "post", {
            "expected_current_state_revision": revision or (self.revision if box.pk == self.box.pk else self.other_revision),
            "children": [{"thermal_zone_id": box.thermal_zone_id, "allocated_polyps": value}],
        }, box_id=box.pk)

    def _post_measurement(self, value=90):
        return self._request(BoxMeasurementListCreateAPIView, "post", {
            "measured_on": timezone.localdate().isoformat(), "polyp_count": value, "ephyrae_count": 0,
        }, box_id=self.box.pk)

    def _patch_measurement(self, value=90):
        return self._request(BoxMeasurementDetailAPIView, "patch", {"polyp_count": value},
            box_id=self.box.pk, pk=self.measurement.pk)

    def _ordered(self, first, second, *, wait_table="cultures_box", fail_first=False):
        entered = threading.Event()
        attempted = threading.Event()
        release = threading.Event()
        original_create = AuditLog.objects.create

        def blocking_audit(**kwargs):
            result = original_create(**kwargs)
            if getattr(self.local, "first", False):
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("First operation was not released.")
                if fail_first:
                    raise RuntimeError("Injected rollback after audit.")
            return result

        def run(action, is_first):
            close_old_connections()
            self.local.first = is_first
            def observe(execute, sql, params, many, context):
                if not is_first and f'"{wait_table}"' in sql and ("FOR UPDATE" in sql or "INSERT INTO" in sql):
                    attempted.set()
                return execute(sql, params, many, context)
            try:
                with connection.execute_wrapper(observe):
                    return action()
            except (RuntimeError, IntegrityError) as error:
                return error
            finally:
                connections.close_all()

        with patch.object(AuditLog.objects, "create", blocking_audit), ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(run, first, True)
            try:
                if not entered.wait(10):
                    if one.done():
                        raise AssertionError(f"First operation exited before audit: {one.result()}")
                    raise TimeoutError("First operation did not reach its locked transaction.")
                two = pool.submit(run, second, False)
                self.assertTrue(attempted.wait(10), "Second operation did not attempt the shared lock.")
                self.assertFalse(two.done(), "Second operation did not wait for the first transaction.")
            finally:
                release.set()
            return one.result(timeout=20), two.result(timeout=20)

    def _current(self, box=None):
        box = box or self.box
        box.refresh_from_db()
        return resolve_current_polyp_state(box)["polyp_count"]

    def test_same_parent_double_spend_is_serialized_and_stale_intent_conflicts(self):
        one, two = self._ordered(self._subculture, self._subculture)
        self.assertEqual(one[0], 201)
        self.assertEqual(two[0], 409)
        self.assertEqual(two[1]["code"], "subculture_current_state_changed")
        self.assertEqual(self._current(), 30)
        self.assertEqual(SubcultureEvent.objects.count(), 1)
        self.assertEqual(SubcultureAllocation.objects.count(), 1)

    def test_measurement_post_wins_and_invalidates_subculture_intent(self):
        one, two = self._ordered(self._post_measurement, self._subculture)
        self.assertEqual((one[0], two[0]), (201, 409))
        self.assertFalse(SubcultureEvent.objects.exists())
        self.assertEqual(self._current(), 90)

    def test_measurement_patch_wins_and_invalidates_subculture_intent(self):
        one, two = self._ordered(self._patch_measurement, self._subculture)
        self.assertEqual((one[0], two[0]), (200, 409))
        self.assertFalse(SubcultureEvent.objects.exists())
        self.assertEqual(self._current(), 90)

    def test_subculture_wins_then_post_is_later_absolute_state(self):
        one, two = self._ordered(self._subculture, self._post_measurement)
        self.assertEqual((one[0], two[0]), (201, 201))
        self.assertEqual(self._current(), 90)
        event = SubcultureEvent.objects.get()
        self.assertEqual((event.parent_polyp_count_before, event.parent_polyp_count_after), (100, 30))

    def test_subculture_wins_then_old_measurement_patch_does_not_rebase_history(self):
        one, two = self._ordered(self._subculture, self._patch_measurement)
        self.assertEqual((one[0], two[0]), (201, 200))
        self.assertEqual(self._current(), 30)
        self.assertEqual(SubcultureEvent.objects.get().parent_state_snapshot["polyp_count"], 100)

    def test_different_parents_share_one_namespace_without_deadlock(self):
        one, two = self._ordered(self._subculture, lambda: self._subculture(self.other), wait_table="cultures_boxcodenamespace")
        self.assertEqual((one[0], two[0]), (201, 201))
        self.assertEqual([one[1]["children"][0]["global_code"], two[1]["children"][0]["global_code"]], ["ACC-QA-1.003", "ACC-QA-1.004"])
        self.assertEqual(BoxCodeNamespace.objects.get(namespace=self.strain.code).high_water, 4)

    def test_concurrent_namespace_first_use_reconciles_legacy_suffixes(self):
        BoxCodeNamespace.objects.all().delete()
        one, two = self._ordered(self._subculture, lambda: self._subculture(self.other), wait_table="cultures_boxcodenamespace")
        self.assertEqual((one[0], two[0]), (201, 201))
        self.assertEqual(BoxCodeNamespace.objects.count(), 1)
        self.assertEqual([row.child_global_code for row in SubcultureAllocation.objects.order_by("pk")], ["ACC-QA-1.003", "ACC-QA-1.004"])

    def test_first_use_rollback_releases_namespace_and_reuses_suffix(self):
        BoxCodeNamespace.objects.all().delete()
        one, two = self._ordered(self._subculture, lambda: self._subculture(self.other), wait_table="cultures_boxcodenamespace", fail_first=True)
        self.assertIsInstance(one, RuntimeError)
        self.assertEqual(two[0], 201)
        self.assertEqual(two[1]["children"][0]["global_code"], "ACC-QA-1.003")
        self.assertEqual(self._current(), 100)
        self.assertEqual(BoxCodeNamespace.objects.get(namespace=self.strain.code).high_water, 3)
        self.assertEqual(SubcultureEvent.objects.count(), 1)

    def test_existing_counter_rollback_releases_namespace_and_reuses_suffix(self):
        one, two = self._ordered(self._subculture, lambda: self._subculture(self.other), wait_table="cultures_boxcodenamespace", fail_first=True)
        self.assertIsInstance(one, RuntimeError)
        self.assertEqual(two[0], 201)
        self.assertEqual(two[1]["children"][0]["global_code"], "ACC-QA-1.003")
        self.assertEqual(self._current(), 100)
        self.assertEqual(AuditLog.objects.filter(action="subculture").count(), 1)

    def test_existing_manual_writer_collision_waits_and_rolls_back_safely(self):
        def writer():
            return Box.objects.create(organization=self.org, strain=self.strain, global_code="ACC-QA-1.003", box_number="003")
        one, two = self._ordered(self._subculture, writer, wait_table="cultures_boxcodenamespace")
        self.assertEqual(one[0], 201)
        self.assertIsInstance(two, IntegrityError)
        self.assertEqual(BoxCodeNamespace.objects.get(namespace=self.strain.code).high_water, 3)
        self.assertEqual(Box.objects.filter(global_code="ACC-QA-1.003").count(), 1)

    def test_parent_code_rename_obeys_parent_then_namespace_lock_order(self):
        def rename():
            box = Box.objects.get(pk=self.box.pk)
            box.global_code = "ACC-QA-1.100"
            box.save(update_fields=["global_code"])
            return box
        one, two = self._ordered(self._subculture, rename)
        self.assertEqual(one[0], 201)
        self.assertEqual(two.global_code, "ACC-QA-1.100")
        self.assertEqual(BoxCodeNamespace.objects.get(namespace=self.strain.code).high_water, 100)
        self.assertEqual(SubcultureAllocation.objects.get().child_global_code, "ACC-QA-1.003")

    def test_rollback_same_parent_allows_second_full_allocation(self):
        one, two = self._ordered(self._subculture, self._subculture, fail_first=True)
        self.assertIsInstance(one, RuntimeError)
        self.assertEqual(two[0], 201)
        self.assertEqual(self._current(), 30)
        self.assertEqual(SubcultureEvent.objects.count(), 1)
