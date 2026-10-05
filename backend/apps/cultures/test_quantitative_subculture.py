"""Science, API, provenance, rollback and namespace regression coverage."""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .api_views import box_list_queryset_for_user
from .box_codes import allocate_box_codes
from .models import Box, BoxCodeNamespace, BoxLineage, BoxLocation, SubcultureAllocation, SubcultureEvent, ThermalZone
from .polyp_state import current_state_prefetches, resolve_current_polyp_state
from .serializers import BoxListSerializer
from .services import SubcultureInvalid, SubcultureStateChanged, create_subculture


class QuantitativeSubcultureTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Quantitative laboratory")
        self.foreign_org = Organization.objects.create(name="Other laboratory")
        self.user = get_user_model().objects.create_user(username="quantitative", email="quantitative@example.org")
        self.membership = OrganizationMembership.objects.create(user=self.user, organization=self.org, role="lab_technician")
        self.zone = ThermalZone.objects.create(organization=self.org, name="Zone")
        self.foreign_zone = ThermalZone.objects.create(organization=self.foreign_org, name="Foreign")
        species = Species.objects.create(scientific_name="Aurelia quantitative", genus_species_code="AQT")
        self.strain = Strain.objects.create(species=species, organization=self.org, code="AQT-QA-1")
        self.box = Box.objects.create(organization=self.org, strain=self.strain, thermal_zone=self.zone,
                                      global_code="AQT-QA-1.001", box_number="001", volume_liters=Decimal("0.30"))
        self.measurement = BiologicalMeasurement.objects.create(box=self.box, measured_on=timezone.localdate(),
            polyp_count=100, ephyrae_count=8, strobila_count=3, salinity_psu="34.25", user=self.user)
        self.client.force_login(self.user)

    def state(self, box=None):
        box = box or self.box
        box.refresh_from_db()
        return resolve_current_polyp_state(box)

    def payload(self, values=(30, 0)):
        return {"expected_current_state_revision": self.state()["revision"], "children": [
            {"thermal_zone_id": self.zone.pk, "allocated_polyps": value} for value in values
        ]}

    def post(self, payload=None):
        return self.client.post(reverse("api_box_subcultures", args=[self.box.pk]),
            data=self.payload() if payload is None else payload, content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str(self.org.pk))

    def operation(self, values=(30, 0), **kwargs):
        data = dict(parent_box=self.box, organization=self.org, user=self.user,
            expected_current_state_revision=self.state()["revision"],
            children=[{"thermal_zone": self.zone, "allocated_polyps": value} for value in values])
        data.update(kwargs)
        return create_subculture(**data)

    def test_absolute_parent_and_explicit_zero_child_without_synthetic_measurements(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.data if hasattr(response, "data") else response.content)
        data = response.json()
        self.assertEqual([child["global_code"] for child in data["children"]], ["AQT-QA-1.002", "AQT-QA-1.003"])
        self.assertEqual(data["parent_polyp_count_before"], 100)
        self.assertEqual(data["allocated_polyp_count"], 30)
        self.assertEqual(data["parent_polyp_count_after"], 70)
        self.assertEqual([row["allocated_polyps"] for row in data["allocations"]], [30, 0])
        self.assertEqual(self.state()["polyp_count"], 70)
        self.assertEqual(BiologicalMeasurement.objects.count(), 1)
        for child, value in zip(data["children"], (30, 0), strict=True):
            self.assertEqual(child["current_polyp_state"]["polyp_count"], value)
            self.assertIsNone(child["latest_measurement"])
            child_box = Box.objects.get(pk=child["id"])
            self.assertEqual(child_box.volume_liters, self.box.volume_liters)
            self.assertEqual(child_box.locations.get().starts_at, SubcultureEvent.objects.get().occurred_at)
        self.measurement.refresh_from_db()
        self.assertEqual((self.measurement.polyp_count, self.measurement.ephyrae_count, self.measurement.strobila_count), (100, 8, 3))
        self.assertEqual(self.measurement.salinity_psu, Decimal("34.25"))
        audit = AuditLog.objects.get(action=AuditLog.Action.SUBCULTURE)
        self.assertEqual(audit.metadata["parent_state_snapshot"]["source"]["id"], self.measurement.pk)
        self.assertEqual(audit.metadata["allocations"][1]["allocated_polyps"], 0)

    def test_repeat_subcultures_spend_resulting_absolute_state(self):
        first, _ = self.operation((70,))
        second, _ = self.operation((30,))
        self.assertEqual(second.parent_polyp_count_before, 30)
        self.assertEqual(second.parent_state_snapshot["source"], {"kind": "subculture", "id": first.pk, "timestamp": first.occurred_at.isoformat()})
        self.assertEqual(self.state()["polyp_count"], 0)
        self.assertEqual(self.post(self.payload((1,))).status_code, 400)
        self.assertEqual(self.post(self.payload((0,))).status_code, 201)

    def test_unknown_is_not_zero_and_refuses_even_zero_allocation(self):
        self.measurement.delete()
        self.assertIsNone(self.state()["polyp_count"])
        for values in ((0,), (None,), (0, None)):
            response = self.post(self.payload(values))
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["code"], "subculture_parent_count_unknown")
        self.assertFalse(SubcultureEvent.objects.exists())

    def test_real_zero_can_initialize_zero_children(self):
        self.measurement.polyp_count = 0
        self.measurement.save()
        self.assertEqual(self.post(self.payload((0, 0))).status_code, 201)
        self.assertEqual(self.state()["polyp_count"], 0)

    def test_rejects_negative_fraction_boolean_and_string_allocations(self):
        for value in (-1, 1.2, 1.0, True, False, "0", "", 2147483648):
            with self.subTest(value=value):
                response = self.post(self.payload((value,)))
                self.assertEqual(response.status_code, 400)
        self.assertFalse(SubcultureEvent.objects.exists())

    def test_complete_allocations_have_exact_balance(self):
        response = self.post(self.payload((30, 20)))
        self.assertEqual(response.status_code, 201)
        self.assertEqual((response.json()["parent_polyp_count_before"],
                          response.json()["allocated_polyp_count"],
                          response.json()["parent_polyp_count_after"]), (100, 50, 50))
        self.assertEqual(self.state()["polyp_count"], 50)

    def test_partial_counts_preserve_parent_source_and_nullable_audit_evidence(self):
        for values in ((30, None), (0, None)):
            with self.subTest(values=values):
                before = self.state()
                payload = self.payload(values)
                response = self.post(payload)
                self.assertEqual(response.status_code, 201, response.content)
                data = response.json()
                self.assertEqual(data["parent_polyp_count_before"], 100)
                self.assertIsNone(data["allocated_polyp_count"])
                self.assertIsNone(data["parent_polyp_count_after"])
                self.assertEqual(data["parent_state_snapshot"], before)
                self.assertEqual([row["allocated_polyps"] for row in data["allocations"]], list(values))
                self.assertEqual([child["current_polyp_state"]["polyp_count"] for child in data["children"]], list(values))
                self.assertIsNone(data["children"][1]["current_polyp_state"]["source"])
                after = self.state()
                self.assertEqual(after["polyp_count"], before["polyp_count"])
                self.assertEqual(after["source"], before["source"])
                self.assertNotEqual(after["revision"], before["revision"])
                prefetched = Box.objects.prefetch_related(*current_state_prefetches()).get(pk=self.box.pk)
                self.assertEqual(resolve_current_polyp_state(prefetched), after)
                self.assertEqual(self.post(payload).status_code, 409)
                audit = AuditLog.objects.filter(action=AuditLog.Action.SUBCULTURE).latest("pk")
                self.assertIsNone(audit.metadata["allocated_polyp_count"])
                self.assertIsNone(audit.metadata["parent_polyp_count_after"])
                self.assertEqual([row["allocated_polyps"] for row in audit.metadata["allocations"]], list(values))
                event = SubcultureEvent.objects.get(pk=data["id"])
                with self.assertRaises(ValidationError):
                    event.save()
                with self.assertRaises(ValidationError):
                    event.allocations.update(allocated_polyps=0)

    def test_service_accepts_omitted_allocation_and_rejects_noninteger_values(self):
        for value in (-1, True, 1.0, "0", "", 2147483648):
            with self.subTest(value=value), self.assertRaises(SubcultureInvalid):
                self.operation((value, None))
        before = self.state()
        event, children = self.operation(children=[{"thermal_zone": self.zone},
                                                  {"thermal_zone": self.zone, "allocated_polyps": 30}])
        self.assertIsNone(event.allocated_polyp_count)
        self.assertEqual(self.state()["source"], before["source"])
        self.assertIsNone(self.state(children[0])["polyp_count"])
        self.assertEqual(self.state(children[1])["polyp_count"], 30)

    def test_partial_known_sum_exceeding_parent_rejects_without_mutation(self):
        before = self.state()
        response = self.post(self.payload((120, None)))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "subculture_allocation_exceeds_parent")
        self.assertEqual(self.state(), before)
        self.assertFalse(SubcultureEvent.objects.exists())
        self.assertFalse(SubcultureAllocation.objects.exists())

    def test_all_omitted_or_null_allocations_keep_lineage_locations_without_initialization(self):
        payload = self.payload((None, None))
        del payload["children"][0]["allocated_polyps"]
        response = self.post(payload)
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        for child_data in data["children"]:
            child = Box.objects.prefetch_related(*current_state_prefetches()).get(pk=child_data["id"])
            state = resolve_current_polyp_state(child)
            self.assertIsNone(state["polyp_count"])
            self.assertIsNone(state["source"])
            self.assertEqual(child.locations.count(), 1)
            self.assertTrue(BoxLineage.objects.filter(parent_box=self.box, child_box=child).exists())
            from .biological_timeline import biological_timeline
            self.assertEqual(biological_timeline(child, context={}), [])
        self.assertEqual(self.state()["polyp_count"], 100)
        self.assertEqual(BiologicalMeasurement.objects.count(), 1)
        self.assertEqual(list(SubcultureAllocation.objects.values_list("allocated_polyps", flat=True)), [None, None])

    def test_previous_complete_then_partial_is_not_a_state_transition(self):
        complete, _ = self.operation((30, 20))
        before = self.state()
        partial, children = self.operation((0, None))
        self.assertEqual(partial.parent_polyp_count_before, 50)
        self.assertIsNone(partial.parent_polyp_count_after)
        for box in (Box.objects.get(pk=self.box.pk),
                    Box.objects.prefetch_related(*current_state_prefetches()).get(pk=self.box.pk)):
            state = resolve_current_polyp_state(box)
            self.assertEqual(state["polyp_count"], 50)
            self.assertEqual(state["source"]["id"], complete.pk)
            self.assertNotEqual(state["revision"], before["revision"])
        from .biological_timeline import biological_timeline
        entries = biological_timeline(self.box, context={})
        partial_entry = next(row for row in entries if row["kind"] == "subculture" and row["id"] == partial.pk)
        self.assertIsNone(partial_entry["polyp_count_after"])
        self.assertIsNone(partial_entry["allocated_polyps"])
        self.assertEqual(biological_timeline(children[1], context={}), [])
        self.assertEqual(biological_timeline(children[0], context={})[0]["polyp_count_after"], 0)

    def test_measurement_between_complete_and_partial_remains_authoritative(self):
        self.operation((30, 20))
        BiologicalMeasurement.objects.create(box=self.box, measured_on=timezone.localdate() + timedelta(days=7),
            polyp_count=80, ephyrae_count=0, strobila_count=0, user=self.user)
        before = self.state()
        self.operation((30, None))
        after = self.state()
        self.assertEqual(after["polyp_count"], 80)
        self.assertEqual(after["source"], before["source"])
        prefetched = Box.objects.prefetch_related(*current_state_prefetches()).get(pk=self.box.pk)
        self.assertEqual(resolve_current_polyp_state(prefetched), after)

    def test_server_date_and_codes_cannot_be_client_imposed(self):
        for field, value in (("event_date", "2000-01-01"), ("occurred_at", "2000-01-01T00:00:00Z")):
            payload = self.payload()
            payload[field] = value
            self.assertEqual(self.post(payload).status_code, 400)
        for field in ("global_code", "box_number", "initial_polyp_count"):
            payload = self.payload()
            payload["children"][0][field] = "999"
            self.assertEqual(self.post(payload).status_code, 400)
        response = self.post()
        self.assertEqual(response.json()["event_date"], timezone.localdate().isoformat())

    def test_revision_is_required_and_stale_state_conflicts_without_mutation(self):
        payload = self.payload()
        self.measurement.polyp_count = 90
        self.measurement.save()
        response = self.post(payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "subculture_current_state_changed")
        self.assertEqual(response.json()["current_polyp_state"]["polyp_count"], 90)
        del payload["expected_current_state_revision"]
        self.assertEqual(self.post(payload).status_code, 400)
        self.assertFalse(SubcultureEvent.objects.exists())

    def test_excess_and_invalid_parent_status_are_rejected(self):
        self.assertEqual(self.post(self.payload((60, 41))).status_code, 400)
        for status in (Box.Status.INACTIVE, Box.Status.PENDING_REVIEW):
            self.box.status = status
            self.box.save(update_fields=["status"])
            self.assertEqual(self.post().status_code, 400)
        self.assertEqual(Box.objects.count(), 1)

    def test_zone_and_permission_checks_are_authoritative_in_service(self):
        for zone in (self.foreign_zone,):
            with self.assertRaises(SubcultureInvalid):
                self.operation(children=[{"thermal_zone": zone, "allocated_polyps": 0}])
        self.zone.is_active = False
        self.zone.save()
        with self.assertRaises(SubcultureInvalid):
            self.operation()
        self.zone.is_active = True
        self.zone.save()
        with self.assertRaises(PermissionDenied):
            self.operation(organization=self.foreign_org)
        self.membership.role = "viewer"
        self.membership.save()
        self.assertEqual(self.post().status_code, 403)
        with self.assertRaises(PermissionDenied):
            self.operation()
        self.assertFalse(SubcultureEvent.objects.exists())

    def test_admin_allowed_and_foreign_parent_not_found(self):
        self.membership.role = "admin"
        self.membership.save()
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(self.client.post(reverse("api_box_subcultures", args=[self.box.pk]), data=self.payload(),
            content_type="application/json", HTTP_X_ORGANIZATION_ID=str(self.foreign_org.pk)).status_code, 403)

    def test_correction_does_not_rebase_or_replace_prior_transition(self):
        event, _ = self.operation()
        original_snapshot = event.parent_state_snapshot
        original_revision = self.state()["revision"]
        self.measurement.polyp_count = 1
        self.measurement.save()
        self.assertEqual(self.state()["polyp_count"], 70)
        self.assertNotEqual(self.state()["revision"], original_revision)
        event.refresh_from_db()
        self.assertEqual(event.parent_state_snapshot, original_snapshot)
        self.assertEqual(event.parent_polyp_count_after, 70)
        next_event, _ = self.operation((10,))
        self.assertEqual(next_event.parent_polyp_count_before, 70)

    def test_new_measurement_after_initialization_is_absolute_and_week_is_free(self):
        _, children = self.operation()
        child = children[0]
        response = self.client.post(reverse("api_box_measurements", args=[child.pk]),
            data={"measured_on": timezone.localdate().isoformat(), "polyp_count": 12, "ephyrae_count": 5},
            content_type="application/json", HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(self.state(child)["polyp_count"], 12)
        self.assertEqual(child.subculture_initialization.allocated_polyps, 30)
        response = self.client.post(reverse("api_box_measurements", args=[child.pk]),
            data={"measured_on": timezone.localdate().isoformat(), "polyp_count": 9},
            content_type="application/json", HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.assertEqual(response.status_code, 409)

    def test_backdated_measurement_and_its_correction_do_not_replace_current_state(self):
        self.operation()
        old = BiologicalMeasurement.objects.create(box=self.box, measured_on=timezone.localdate() - timedelta(days=14), polyp_count=999)
        self.assertEqual(self.state()["polyp_count"], 70)
        old.polyp_count = 2
        old.save()
        self.assertEqual(self.state()["polyp_count"], 70)

    def test_same_clock_tick_uses_sequence_not_timestamp_or_cross_table_id(self):
        now = timezone.now()
        with patch("django.utils.timezone.now", return_value=now):
            first, children = self.operation((10,))
            second, _ = self.operation((20,))
        self.assertEqual(first.occurred_at, second.occurred_at)
        self.assertEqual(self.state()["polyp_count"], 70)
        self.assertGreater(second.parent_state_sequence, first.parent_state_sequence)
        child = children[0]
        with patch("django.utils.timezone.now", return_value=now):
            BiologicalMeasurement.objects.create(box=child, measured_on=timezone.localdate(now), polyp_count=7)
        self.assertEqual(self.state(child)["polyp_count"], 7)

    def test_timeline_is_typed_and_only_measurement_is_editable(self):
        _, children = self.operation()
        response = self.client.get(reverse("api_box_detail", args=[self.box.pk]), HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()["biological_timeline"]
        self.assertEqual([row["kind"] for row in rows], ["subculture", "measurement"])
        self.assertEqual(len({row["identity"] for row in rows}), 2)
        self.assertFalse(rows[0]["can_edit"])
        self.assertTrue(rows[1]["can_edit"])
        self.assertEqual(rows[0]["author"]["id"], self.user.pk)
        self.assertEqual(rows[0]["allocations"][1]["allocated_polyps"], 0)
        child_rows = self.client.get(reverse("api_box_detail", args=[children[1].pk]), HTTP_X_ORGANIZATION_ID=str(self.org.pk)).json()["biological_timeline"]
        self.assertEqual(child_rows[0]["kind"], "subculture_initialization")
        self.assertEqual(child_rows[0]["polyp_count_after"], 0)
        self.assertFalse(child_rows[0]["can_edit"])

    def test_legacy_event_remains_unknown_without_any_effect(self):
        event = SubcultureEvent.objects.create(parent_box=self.box, event_date=timezone.localdate())
        self.assertIsNone(event.occurred_at)
        self.assertEqual(self.state()["polyp_count"], 100)
        rows = self.client.get(reverse("api_box_detail", args=[self.box.pk]), HTTP_X_ORGANIZATION_ID=str(self.org.pk)).json()["biological_timeline"]
        row = next(row for row in rows if row["kind"] == "subculture")
        self.assertIsNone(row["timestamp"])
        self.assertIsNone(row["polyp_count_before"])
        self.assertIsNone(row["polyp_count_after"])
        self.assertIsNone(row["allocated_polyps"])

    def test_snapshot_allocations_are_immutable_and_protected(self):
        event, children = self.operation()
        allocation = event.allocations.first()
        for write in (lambda: event.save(), lambda: event.delete(),
                      lambda: SubcultureEvent.objects.filter(pk=event.pk).update(parent_polyp_count_after=1),
                      lambda: allocation.save(), lambda: allocation.delete(),
                      lambda: event.allocations.update(allocated_polyps=99), lambda: event.allocations.all().delete()):
            with self.assertRaises(ValidationError):
                write()
        with self.assertRaises(ProtectedError):
            children[0].delete()
        with self.assertRaises(ProtectedError):
            self.box.delete()

    def test_transaction_rolls_back_every_component_and_counter_at_each_failure(self):
        revision = self.state()["revision"]
        high_water = BoxCodeNamespace.objects.get(namespace=self.strain.code).high_water
        targets = ("SubcultureEvent", "SubcultureAllocation", "BoxLineage", "BoxLocation", "AuditLog")
        for target in targets:
            with self.subTest(target=target), patch(f"apps.cultures.services.{target}.objects.create", side_effect=RuntimeError("injected")):
                with self.assertRaises(RuntimeError):
                    self.operation()
            self.assertEqual(Box.objects.count(), 1)
            self.assertFalse(SubcultureEvent.objects.exists())
            self.assertFalse(SubcultureAllocation.objects.exists())
            self.assertFalse(BoxLineage.objects.exists())
            self.assertFalse(BoxLocation.objects.exists())
            self.assertFalse(AuditLog.objects.exists())
            self.assertEqual(self.state()["revision"], revision)
            self.assertEqual(BoxCodeNamespace.objects.get(namespace=self.strain.code).high_water, high_water)
        _, children = self.operation()
        self.assertEqual(children[0].box_number, "002")

    def test_high_water_namespace_is_global_no_holes_and_padding_grows(self):
        foreign = Box.objects.create(organization=self.foreign_org, strain=self.strain, global_code="AQT-QA-1.999", box_number="999")
        foreign.delete()
        _, children = self.operation()
        self.assertEqual([box.global_code for box in children], ["AQT-QA-1.1000", "AQT-QA-1.1001"])
        self.assertEqual([row.position for row in SubcultureAllocation.objects.all()], [0, 1])

    def test_reconciles_existing_noncanonical_codes_on_namespace_first_use(self):
        Box.objects.create(organization=self.org, strain=self.strain, global_code="AQT-QA-1.87-legacy", box_number="87")
        BoxCodeNamespace.objects.all().delete()
        _, children = self.operation()
        self.assertEqual(children[0].global_code, "AQT-QA-1.088")

    def test_manual_renames_and_failed_collisions_do_not_corrupt_counter(self):
        self.box.global_code = "AQT-QA-1.010"
        self.box.save(update_fields=["global_code"])
        with self.assertRaises(IntegrityError), transaction.atomic():
            Box.objects.create(organization=self.org, strain=self.strain, global_code="AQT-QA-1.010", box_number="010")
        _, children = self.operation()
        self.assertEqual(children[0].global_code, "AQT-QA-1.011")

    def test_list_inventory_overview_zone_and_dashboard_expose_current_state(self):
        self.operation()
        listed = BoxListSerializer(box_list_queryset_for_user(self.user, [self.org.pk]), many=True).data
        parent = next(row for row in listed if row["id"] == self.box.pk)
        self.assertEqual(parent["current_polyp_state"]["polyp_count"], 70)
        self.assertEqual(parent["latest_measurement"]["polyp_count"], 100)
        overview = self.client.get(reverse("api_overview_active_boxes"), HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.assertEqual(overview.status_code, 200, overview.content)
        self.assertEqual(next(row for row in overview.json()["results"] if row["id"] == self.box.pk)["current_polyp_state"]["polyp_count"], 70)
        child_rows = [row for row in overview.json()["results"] if row["id"] != self.box.pk]
        self.assertTrue(all(not row["tracked_in_app"] and not row["measurements"] and row["earliest_biological_measurement_on"] is None for row in child_rows))
        self.membership.role = "admin"
        self.membership.save()
        inventory = self.client.get(reverse("api_admin_box_inventory"), HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.assertEqual(inventory.status_code, 200, inventory.content)
        self.assertEqual(next(row for row in inventory.json()["results"] if row["id"] == self.box.pk)["current_polyp_state"]["polyp_count"], 70)
        zones = self.client.get(reverse("api_thermal_zone_list"), HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.assertEqual(zones.status_code, 200, zones.content)
        self.assertEqual(zones.json()["results"][0]["current_polyp_totals"], {"polyp_count": 100, "unknown_box_count": 0})
        dashboard = self.client.get(reverse("api_dashboard"), HTTP_X_ORGANIZATION_ID=str(self.org.pk)).json()
        self.assertEqual(dashboard["stats"]["current_polyps"], 100)
        self.assertEqual(dashboard["stats"]["measured_polyps"], 100)
        self.assertEqual(dashboard["stats"]["current_polyps_unknown_box_count"], 0)

    def test_allocator_requires_transaction_and_rollback_reuses_reserved_block(self):
        initial = BoxCodeNamespace.objects.get(namespace=self.strain.code).high_water
        try:
            with transaction.atomic():
                codes = allocate_box_codes(self.strain.code, 2)
                self.assertEqual([number for _, number in codes], ["002", "003"])
                raise RuntimeError("rollback")
        except RuntimeError:
            pass
        self.assertEqual(BoxCodeNamespace.objects.get(namespace=self.strain.code).high_water, initial)

    def test_weekly_export_eligibility_preview_and_csv_remain_measurement_only(self):
        import csv
        from io import StringIO
        from apps.exports.services import build_weekly_measurement_csv, build_weekly_measurement_preview, get_measurement_export_eligibility
        self.operation()
        boxes = Box.objects.filter(organization=self.org)
        eligible_ids, measurement_count, _ = get_measurement_export_eligibility(boxes=boxes)
        self.assertEqual(eligible_ids, [self.box.pk])
        self.assertEqual(measurement_count, 1)
        content, metadata = build_weekly_measurement_csv(boxes=boxes)
        rows = list(csv.reader(StringIO(content)))
        self.assertEqual(metadata["box_count"], 1)
        self.assertEqual(metadata["measurement_count"], 1)
        self.assertEqual(rows[1][5:7], ["100", "8"])
        preview = build_weekly_measurement_preview(boxes=boxes)
        self.assertEqual(preview["points"][0]["polyp_count"], 100)
        self.assertEqual(preview["points"][0]["ephyrae_count"], 8)
        self.assertEqual(preview["points"][0]["measurement_count"], 1)

    def test_patch_endpoint_allows_scientific_correction_after_subculture_without_rewriting_event(self):
        event, children = self.operation()
        self.membership.role = "admin"
        self.membership.save()
        response = self.client.patch(reverse("api_box_measurement_detail", args=[self.box.pk, self.measurement.pk]),
            data={"polyp_count": 0, "ephyrae_count": 0, "strobila_count": 0}, content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.state()["polyp_count"], 70)
        event.refresh_from_db()
        self.assertEqual(event.parent_state_snapshot["polyp_count"], 100)
        self.assertEqual(event.allocations.first().allocated_polyps, 30)
        self.assertEqual(children[0].biological_measurements.count(), 0)

    def test_raw_sql_cannot_rewrite_quantitative_snapshot_or_allocations(self):
        event, _ = self.operation()
        allocation = event.allocations.first()
        for sql, pk in (
            ("UPDATE cultures_subcultureevent SET reason = 'rewritten' WHERE id = %s", event.pk),
            ("UPDATE cultures_subcultureevent SET parent_polyp_count_before = 200, parent_polyp_count_after = 170 WHERE id = %s", event.pk),
            ("UPDATE cultures_subcultureallocation SET allocated_polyps = 0 WHERE id = %s", allocation.pk),
        ):
            with self.subTest(sql=sql), self.assertRaises(IntegrityError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(sql, [pk])
        self.assertEqual(self.state()["polyp_count"], 70)

    def test_postgresql_raw_delete_protects_quantitative_history(self):
        if connection.vendor != "postgresql":
            self.skipTest("Production SQL deletion guards require PostgreSQL; SQLite flush uses DELETE.")
        event, _ = self.operation()
        for table, pk in (("cultures_subcultureevent", event.pk), ("cultures_subcultureallocation", event.allocations.first().pk)):
            with self.assertRaises(IntegrityError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(f"DELETE FROM {table} WHERE id = %s", [pk])

    def test_author_snapshot_survives_account_deletion(self):
        event, children = self.operation()
        username = self.user.username
        self.user.delete()
        event.refresh_from_db()
        self.assertIsNone(event.user_id)
        self.assertEqual(event.author_name, username)
        from .biological_timeline import biological_timeline
        timeline = biological_timeline(children[0], context={})
        self.assertEqual(timeline[0]["author"], {"id": None, "username": username})

    def test_personal_audit_exposes_quantitative_balance_and_keeps_legacy_contract(self):
        from apps.audit.services import serialize_business_details
        self.operation()
        details = serialize_business_details(AuditLog.objects.get(action=AuditLog.Action.SUBCULTURE))
        self.assertEqual(details["parent_polyp_count_before"], 100)
        self.assertEqual(details["allocated_polyp_count"], 30)
        self.assertEqual(details["parent_polyp_count_after"], 70)
        self.assertEqual(details["allocations"][1]["allocated_polyps"], 0)
        self.assertEqual(details["initial_polyp_counts"]["AQT-QA-1.003"], 0)

    def test_stale_full_box_save_does_not_reset_scientific_revision(self):
        stale = Box.objects.get(pk=self.box.pk)
        self.operation()
        revision = self.state()["revision"]
        stale.notes = "Administrative note"
        stale.save()
        self.assertEqual(self.state()["revision"], revision)

    def test_foreign_zone_and_foreign_active_organization_cannot_create_children(self):
        payload = self.payload()
        payload["children"][0]["thermal_zone_id"] = self.foreign_zone.pk
        self.assertEqual(self.post(payload).status_code, 400)
        OrganizationMembership.objects.create(user=self.user, organization=self.foreign_org, role="admin")
        response = self.client.post(reverse("api_box_subcultures", args=[self.box.pk]), data=self.payload(),
            content_type="application/json", HTTP_X_ORGANIZATION_ID=str(self.foreign_org.pk))
        self.assertEqual(response.status_code, 404)
        self.assertFalse(SubcultureEvent.objects.exists())

    def test_error_messages_use_french_and_english_catalogs(self):
        from django.utils.translation import override
        from .services import SubcultureStateChanged
        with override("fr"):
            self.assertEqual(SubcultureStateChanged({}).messages[0], "Le nombre actuel de polypes a changé. Rechargez la boîte parent.")
        with override("en"):
            self.assertEqual(SubcultureStateChanged({}).messages[0], "The current polyp state changed. Refresh the parent box.")

    def test_malformed_children_are_validation_errors_not_server_errors(self):
        for child in (None, "invalid", ["invalid"]):
            payload = self.payload()
            payload["children"] = [child]
            self.assertEqual(self.post(payload).status_code, 400)

    def test_second_child_creation_failure_rolls_back_first_child_and_counter(self):
        original_create = Box.objects.create
        created = []
        def fail_second(**kwargs):
            if created:
                raise RuntimeError("Second child failure")
            child = original_create(**kwargs)
            created.append(child.pk)
            return child
        with patch("apps.cultures.services.Box.objects.create", side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.operation()
        self.assertEqual(len(created), 1)
        self.assertEqual(Box.objects.count(), 1)
        self.assertFalse(SubcultureEvent.objects.exists())
        self.assertFalse(SubcultureAllocation.objects.exists())
        self.assertFalse(BoxLineage.objects.exists())
        self.assertFalse(BoxLocation.objects.exists())
        self.assertEqual(self.state()["polyp_count"], 100)
        self.assertEqual(BoxCodeNamespace.objects.get(namespace=self.strain.code).high_water, 1)

    def test_latest_state_prefetch_is_bounded_for_initialization_only_children(self):
        from django.test.utils import CaptureQueriesContext
        self.operation()
        def serialize_query_count():
            with CaptureQueriesContext(connection) as queries:
                list(BoxListSerializer(box_list_queryset_for_user(self.user, [self.org.pk]), many=True).data)
            return len(queries)
        original_count = serialize_query_count()
        for number in range(10, 20):
            Box.objects.create(organization=self.org, strain=self.strain, global_code=f"AQT-QA-1.{number:03}", box_number=str(number))
        self.assertEqual(serialize_query_count(), original_count)

    def test_future_dated_consumed_source_cannot_override_transition_or_double_spend(self):
        future = BiologicalMeasurement.objects.create(box=self.box,
            measured_on=timezone.localdate() + timedelta(days=14), polyp_count=80)
        self.assertEqual(self.state()["polyp_count"], 80)
        event, _ = self.operation((30,))
        self.assertEqual(event.parent_state_snapshot["source"]["id"], future.pk)
        self.assertEqual(self.state()["polyp_count"], 50)
        listed = BoxListSerializer(box_list_queryset_for_user(self.user, [self.org.pk]), many=True).data
        self.assertEqual(next(row for row in listed if row["id"] == self.box.pk)["current_polyp_state"]["polyp_count"], 50)
        future.polyp_count = 1000
        future.save()
        self.assertEqual(self.state()["polyp_count"], 50)
        self.assertEqual(self.post(self.payload((51,))).status_code, 400)
        second, _ = self.operation((50,))
        self.assertEqual(second.parent_polyp_count_before, 50)
        self.assertEqual(self.state()["polyp_count"], 0)

    def test_new_measurement_can_supersede_consumed_future_source(self):
        BiologicalMeasurement.objects.create(box=self.box, measured_on=timezone.localdate() + timedelta(days=14), polyp_count=80)
        self.operation((30,))
        newer = BiologicalMeasurement.objects.create(box=self.box,
            measured_on=timezone.localdate() + timedelta(days=7), polyp_count=20)
        self.assertEqual(self.state()["polyp_count"], 20)
        self.assertEqual(self.state()["source"]["id"], newer.pk)
        listed = BoxListSerializer(box_list_queryset_for_user(self.user, [self.org.pk]), many=True).data
        self.assertEqual(next(row for row in listed if row["id"] == self.box.pk)["current_polyp_state"]["polyp_count"], 20)

    def test_correcting_consumed_measurement_date_forward_does_not_rewrite_parent_state(self):
        event, _ = self.operation()
        self.measurement.measured_on = timezone.localdate() + timedelta(days=14)
        self.measurement.polyp_count = 999
        self.measurement.save()
        self.assertEqual(self.state()["polyp_count"], 70)
        event.refresh_from_db()
        self.assertEqual(event.parent_state_snapshot["polyp_count"], 100)
        self.assertEqual(event.parent_polyp_count_after, 70)

    def test_service_refuses_inactive_or_missing_actor(self):
        with self.assertRaises(PermissionDenied):
            self.operation(user=None)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            self.operation()
        self.assertFalse(SubcultureEvent.objects.exists())

    def test_numeric_trailing_decorations_register_the_actual_strain_namespace(self):
        Box.objects.create(organization=self.org, strain=self.strain, global_code="AQT-QA-1.087.1", box_number="087")
        self.assertEqual(BoxCodeNamespace.objects.get(namespace=self.strain.code).high_water, 87)
        self.assertFalse(BoxCodeNamespace.objects.filter(namespace="AQT-QA-1.087").exists())
        _, children = self.operation()
        self.assertEqual(children[0].global_code, "AQT-QA-1.088")

    def test_foreign_lineage_children_are_not_exposed_in_timeline(self):
        event, _ = self.operation()
        foreign = Box.objects.create(organization=self.foreign_org, strain=self.strain, global_code="FOREIGN.001", box_number="001")
        BoxLineage.objects.create(parent_box=self.box, child_box=foreign, subculture_event=event)
        response = self.client.get(reverse("api_box_detail", args=[self.box.pk]), HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.assertNotIn("FOREIGN.001", str(response.json()))
