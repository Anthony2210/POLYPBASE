"""Direct domain writes and HTTP compatibility at the shared service boundary."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OrganizationMembership, UserPreference
from apps.audit.models import AuditLog
from apps.cultures.models import Box, SubcultureAllocation, SubcultureEvent, ThermalZone
from apps.cultures.polyp_state import resolve_current_polyp_state
from apps.cultures.services import create_subculture
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from . import services
from .models import BiologicalMeasurement
from .serializers import BiologicalMeasurementCreateSerializer
from .services import (
    MeasurementNotFound,
    MeasurementWeekConflict,
    MeasurementWriteForbidden,
    MeasurementWriteInvalid,
    correct_biological_measurement,
    create_biological_measurement,
)


class MeasurementWriteServiceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Measurement service laboratory")
        self.foreign_org = Organization.objects.create(name="Other measurement laboratory")
        users = get_user_model()
        self.admin = users.objects.create_user(username="service-admin", email="admin@example.org")
        UserPreference.objects.update_or_create(user=self.admin, defaults={"interface_language": "en"})
        self.technician = users.objects.create_user(username="service-tech", email="tech@example.org")
        self.viewer = users.objects.create_user(username="service-viewer", email="viewer@example.org")
        for user, role in ((self.admin, "admin"), (self.technician, "lab_technician"), (self.viewer, "viewer")):
            OrganizationMembership.objects.create(user=user, organization=self.org, role=role)
        species = Species.objects.create(scientific_name="Aurelia service", genus_species_code="AMS")
        self.strain = Strain.objects.create(organization=self.org, species=species, code="AMS-QA-1")
        self.zone = ThermalZone.objects.create(organization=self.org, name="Service zone")
        self.box = Box.objects.create(
            organization=self.org, strain=self.strain, thermal_zone=self.zone,
            global_code="AMS-QA-1.001", box_number="001",
        )
        self.foreign_box = Box.objects.create(
            organization=self.foreign_org, strain=self.strain,
            global_code="AMS-QA-1.002", box_number="002",
        )
        self.day = date(2026, 9, 14)

    def create(self, data=None, **kwargs):
        arguments = dict(
            box_id=self.box.pk, organization_ids=[self.org.pk], user=self.admin,
            data={"measured_on": self.day, "polyp_count": 100, "ephyrae_count": 2} if data is None else data,
        )
        arguments.update(kwargs)
        return create_biological_measurement(**arguments)

    def correct(self, measurement, data=None, **kwargs):
        arguments = dict(
            box_id=self.box.pk, measurement_id=measurement.pk,
            organization_ids=[self.org.pk], user=self.admin,
            data={"polyp_count": 0} if data is None else data,
        )
        arguments.update(kwargs)
        return correct_biological_measurement(**arguments)

    def revision(self):
        self.box.refresh_from_db()
        return self.box.polyp_state_revision

    def snapshot(self):
        return (
            list(BiologicalMeasurement.objects.order_by("pk").values()),
            list(AuditLog.objects.order_by("pk").values()),
            self.revision(),
        )

    def test_create_validates_typed_values_and_records_entry_in_one_revision(self):
        before = self.revision()
        row = self.create({
            "measured_on": self.day.isoformat(), "polyp_count": 0, "ephyrae_count": 0,
            "strobila_count": 0, "salinity_psu": "0.00", "notes": "Scientific zero",
        }, user=self.technician)
        row.refresh_from_db()
        self.assertEqual(row.week_start, self.day)
        self.assertEqual(row.temporal_precision, "exact_date")
        self.assertEqual((row.polyp_count, row.ephyrae_count, row.strobila_count), (0, 0, 0))
        self.assertEqual(row.salinity_psu, Decimal("0.00"))
        self.assertEqual(row.user, self.technician)
        self.assertEqual(self.revision(), before + 1)
        self.assertEqual(row.polyp_state_sequence, before + 1)
        audit = AuditLog.objects.get()
        self.assertEqual(audit.organization, self.org)
        self.assertEqual(audit.user, self.technician)
        self.assertEqual(audit.action, AuditLog.Action.ENTRY)
        self.assertEqual(audit.object_id, self.box.global_code)
        self.assertEqual(audit.description, f"Biological measurement for {self.day}")
        self.assertEqual(audit.metadata, {"measurement_id": row.pk, "valeurs": {
            "date": self.day.isoformat(), "polypes": 0, "ephyrules": 0, "strobiles": 0,
            "salinite_psu": "0.00", "statut_culture": "not_specified", "a_verifier": False,
            "note": "Scientific zero",
        }})

    def test_correction_preserves_identity_sequence_and_partial_values_with_exact_audit(self):
        row = self.create({"measured_on": self.day, "polyp_count": 100, "ephyrae_count": 2, "salinity_psu": "34.25"})
        identity = (row.pk, row.created_at, row.polyp_state_sequence)
        before_revision = self.revision()
        corrected = self.correct(row, {"polyp_count": 0, "salinity_psu": None}, user=self.technician)
        self.assertEqual((corrected.pk, corrected.created_at, corrected.polyp_state_sequence), identity)
        self.assertEqual(corrected.user, self.technician)
        self.assertEqual(corrected.ephyrae_count, 2)
        self.assertIsNone(corrected.salinity_psu)
        self.assertEqual(self.revision(), before_revision + 1)
        audit = AuditLog.objects.get(action=AuditLog.Action.UPDATE)
        self.assertEqual(audit.user, self.technician)
        self.assertEqual(audit.metadata["measurement_id"], row.pk)
        self.assertEqual(audit.metadata["before"]["polypes"], 100)
        self.assertEqual(audit.metadata["after"]["polypes"], 0)
        self.assertEqual(audit.metadata["valeurs"], audit.metadata["after"])
        self.assertEqual(audit.metadata["modifications"], {
            "polypes": {"avant": 100, "apres": 0},
            "salinite_psu": {"avant": "34.25", "apres": None},
        })
        self.assertEqual(AuditLog.objects.get(action=AuditLog.Action.ENTRY).user, self.admin)

    def test_nullable_historical_value_is_preserved_unless_explicitly_corrected(self):
        row = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=self.day, polyp_count=5, strobila_count=None, user=None,
        )
        corrected = self.correct(row)
        self.assertIsNone(corrected.strobila_count)
        self.assertEqual(corrected.temporal_precision, "legacy_unverified")
        audit = AuditLog.objects.get()
        self.assertIsNone(audit.metadata["before"]["strobiles"])
        self.assertIsNone(audit.metadata["after"]["strobiles"])
        self.assertNotIn("strobiles", audit.metadata["modifications"])
        corrected = self.correct(row, {"strobila_count": 0})
        self.assertEqual(corrected.strobila_count, 0)

    def test_live_defaults_and_optional_absence_remain_unchanged(self):
        row = self.create({"measured_on": self.day})
        self.assertEqual((row.polyp_count, row.ephyrae_count, row.strobila_count), (0, 0, 0))
        self.assertIsNone(row.salinity_psu)
        self.assertIsNone(AuditLog.objects.get().metadata["valeurs"]["salinite_psu"])

    def test_invalid_values_match_the_shared_serializer_and_do_not_write(self):
        payloads = [
            {}, {"measured_on": "invalid"}, {"measured_on": self.day, "polyp_count": -1},
            {"measured_on": self.day, "polyp_count": None},
            {"measured_on": self.day, "strobila_count": None},
            {"measured_on": self.day, "culture_status": "invalid"},
            {"measured_on": self.day, "salinity_psu": "12.345"},
        ]
        before = self.snapshot()
        for payload in payloads:
            with self.subTest(payload=payload):
                serializer = BiologicalMeasurementCreateSerializer(data=payload)
                self.assertFalse(serializer.is_valid())
                with self.assertRaises(MeasurementWriteInvalid) as caught:
                    self.create(payload)
                self.assertEqual(caught.exception.detail, serializer.errors)
                self.assertEqual(self.snapshot(), before)
        row = self.create()
        before = self.snapshot()
        with self.assertRaises(MeasurementWriteInvalid):
            self.correct(row, {"strobila_count": None})
        self.assertEqual(self.snapshot(), before)

    def test_same_week_duplicate_including_zero_has_no_effect(self):
        row = self.create({"measured_on": self.day, "polyp_count": 0})
        before = self.snapshot()
        for day in (self.day, self.day + timedelta(days=6)):
            with self.subTest(day=day), self.assertRaises(MeasurementWeekConflict) as caught:
                self.create({"measured_on": day, "polyp_count": 42})
            self.assertEqual(caught.exception.measurement_id, row.pk)
            self.assertEqual(caught.exception.week_start, self.day)
            self.assertEqual(self.snapshot(), before)

    def test_date_changes_update_week_without_changing_identity_or_source_order(self):
        row = self.create()
        identity = (row.pk, row.created_at, row.polyp_state_sequence)
        for day in (self.day + timedelta(days=6), self.day + timedelta(days=7)):
            corrected = self.correct(row, {"measured_on": day})
            self.assertEqual(corrected.measured_on, day)
            self.assertEqual(corrected.week_start, BiologicalMeasurement.week_start_for(day))
            self.assertEqual((corrected.pk, corrected.created_at, corrected.polyp_state_sequence), identity)
            self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.UPDATE).latest("pk").metadata["after"]["date"], day.isoformat())

    def test_correction_into_occupied_week_rolls_back_everything(self):
        row = self.create()
        occupied = self.create({"measured_on": self.day + timedelta(days=7), "polyp_count": 0})
        before = self.snapshot()
        with self.assertRaises(MeasurementWeekConflict) as caught:
            self.correct(row, {"measured_on": self.day + timedelta(days=8), "polyp_count": 10})
        self.assertEqual(caught.exception.measurement_id, occupied.pk)
        self.assertEqual(self.snapshot(), before)

    def test_inactive_box_rejects_creation_but_allows_authorized_historical_correction(self):
        row = self.create()
        self.box.status = Box.Status.INACTIVE
        self.box.save(update_fields=["status"])
        before = self.snapshot()
        with self.assertRaises(MeasurementWriteInvalid):
            self.create({"measured_on": self.day + timedelta(days=7)})
        self.assertEqual(self.snapshot(), before)
        # Existing weekly conflict takes precedence over inactive-box validation.
        with self.assertRaises(MeasurementWeekConflict):
            self.create()
        self.assertEqual(self.correct(row).polyp_count, 0)
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.INACTIVE)

    def test_creation_and_correction_check_active_membership_without_parsing_unauthorized_data(self):
        row = self.create()
        for user in (self.viewer, None):
            for operation in (lambda data: self.create(data, user=user), lambda data: self.correct(row, data, user=user)):
                data = Mock(side_effect=AssertionError("Unauthorized input must not be parsed"))
                with self.assertRaises(MeasurementWriteForbidden):
                    operation(data)
                data.assert_not_called()
        OrganizationMembership.objects.filter(user=self.admin, organization=self.org).update(is_active=False)
        with self.assertRaises(MeasurementWriteForbidden):
            self.create()
        with self.assertRaises(MeasurementWriteForbidden):
            self.correct(row)

    def test_creation_rechecks_permission_under_box_lock(self):
        before = self.snapshot()
        def parse_after_preflight():
            OrganizationMembership.objects.filter(user=self.admin, organization=self.org).update(role="viewer")
            return {"measured_on": self.day}
        with self.assertRaises(MeasurementWriteForbidden):
            self.create(parse_after_preflight)
        self.assertEqual(self.snapshot(), before)

    def test_technician_deadline_is_not_extended_by_correction(self):
        row = self.create()
        deadline = row.created_at + services.MEASUREMENT_EDIT_WINDOW
        with patch("apps.measurements.services.timezone.now", return_value=deadline - timedelta(microseconds=1)):
            self.correct(row, user=self.technician)
        before = self.snapshot()
        with patch("apps.measurements.services.timezone.now", return_value=deadline):
            with self.assertRaises(MeasurementWriteForbidden) as caught:
                self.correct(row, user=self.technician)
        self.assertEqual(caught.exception.error_code, "edit_window_expired")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.correct(row).pk, row.pk)

    def test_scope_and_box_identity_are_enforced_even_for_multi_institution_admin(self):
        OrganizationMembership.objects.create(user=self.admin, organization=self.foreign_org, role="admin")
        foreign = BiologicalMeasurement.objects.create(box=self.foreign_box, measured_on=self.day, polyp_count=5)
        before = self.snapshot()
        for operation in (
            lambda: self.create(box_id=self.foreign_box.pk),
            lambda: self.correct(foreign, box_id=self.foreign_box.pk),
            lambda: self.correct(foreign),
            lambda: self.create(organization_ids=[]),
        ):
            with self.assertRaises(MeasurementNotFound):
                operation()
        self.assertEqual(self.snapshot(), before)

    def test_empty_creation_scope_does_not_query_boxes_or_memberships_or_parse_data(self):
        before = self.snapshot()
        self.admin.is_superuser = True
        data = Mock(side_effect=AssertionError("Empty-scope input must not be parsed"))
        for user in (self.admin, self.viewer, None):
            for box_id in (self.box.pk, self.foreign_box.pk, self.foreign_box.pk + 1000):
                with self.subTest(user=user, box_id=box_id):
                    with self.assertNumQueries(0), self.assertRaises(MeasurementNotFound) as caught:
                        self.create(data, box_id=box_id, organization_ids=[], user=user)
                    self.assertEqual(caught.exception.detail, "No Box matches the given query.")
        data.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_serializer_compatibility_export_is_the_shared_validator(self):
        from apps.cultures.serializers import BiologicalMeasurementCreateSerializer as CompatibilitySerializer

        self.assertIs(CompatibilitySerializer, BiologicalMeasurementCreateSerializer)

    def test_in_scope_box_still_requires_its_own_membership(self):
        row = BiologicalMeasurement.objects.create(box=self.foreign_box, measured_on=self.day)
        with self.assertRaises(MeasurementWriteForbidden):
            self.create(box_id=self.foreign_box.pk, organization_ids=[self.foreign_org.pk])
        with self.assertRaises(MeasurementWriteForbidden):
            self.correct(row, box_id=self.foreign_box.pk, organization_ids=[self.foreign_org.pk])

    def test_identity_and_attribution_cannot_be_supplied_as_writable_fields(self):
        row = self.create({
            "measured_on": self.day, "box": self.foreign_box.pk, "user": self.viewer.pk,
            "id": 123456, "polyp_state_sequence": 123456, "week_start": "2026-01-01",
        })
        self.assertEqual(row.box_id, self.box.pk)
        self.assertEqual(row.user, self.admin)
        sequence = row.polyp_state_sequence
        corrected = self.correct(row, {"user": self.viewer.pk, "box": self.foreign_box.pk, "polyp_state_sequence": 123456})
        self.assertEqual(corrected.box_id, self.box.pk)
        self.assertEqual(corrected.user, self.admin)
        self.assertEqual(corrected.polyp_state_sequence, sequence)

    def test_noop_correction_still_increments_revision_and_appends_empty_diff(self):
        row = self.create()
        revision = self.revision()
        corrected = self.correct(row, {}, user=self.technician)
        self.assertEqual(corrected.user, self.technician)
        self.assertEqual(corrected.temporal_precision, "exact_date")
        self.assertEqual(self.revision(), revision + 1)
        audit = AuditLog.objects.get(action=AuditLog.Action.UPDATE)
        self.assertEqual(audit.user, self.technician)
        self.assertEqual(audit.metadata["before"], audit.metadata["after"])
        self.assertEqual(audit.metadata["modifications"], {})

    def test_audit_failure_after_insert_rolls_back_create_and_correction_including_revision(self):
        original = services._record_measurement_audit
        def fail_after_audit(**kwargs):
            original(**kwargs)
            raise RuntimeError("Failure after audit insertion")
        before = self.snapshot()
        with patch.object(services, "_record_measurement_audit", side_effect=fail_after_audit), self.assertRaises(RuntimeError):
            self.create()
        self.assertEqual(self.snapshot(), before)
        row = self.create()
        before = self.snapshot()
        with patch.object(services, "_record_measurement_audit", side_effect=fail_after_audit), self.assertRaises(RuntimeError):
            self.correct(row, {"polyp_count": 0, "measured_on": self.day + timedelta(days=7)}, user=self.technician)
        self.assertEqual(self.snapshot(), before)

    def test_subculture_sources_and_snapshots_survive_old_correction_and_new_observation(self):
        today = timezone.localdate()
        row = self.create({"measured_on": today - timedelta(days=7), "polyp_count": 100})
        self.box.refresh_from_db()
        event, children = create_subculture(
            parent_box=self.box, user=self.admin, organization=self.org,
            expected_current_state_revision=resolve_current_polyp_state(self.box)["revision"],
            children=[{"thermal_zone": self.zone, "allocated_polyps": 30}],
        )
        snapshot = list(SubcultureEvent.objects.values())
        allocations = list(SubcultureAllocation.objects.values())
        revision = self.revision()
        corrected = self.correct(row, {"polyp_count": 5, "measured_on": today})
        self.assertEqual(corrected.temporal_precision, "exact_date")
        self.box.refresh_from_db()
        state = resolve_current_polyp_state(self.box)
        self.assertEqual(state["source"]["kind"], "subculture")
        self.assertEqual(state["polyp_count"], 70)
        self.assertEqual(self.revision(), revision + 1)
        self.assertEqual(list(SubcultureEvent.objects.values()), snapshot)
        self.assertEqual(list(SubcultureAllocation.objects.values()), allocations)
        new = self.create({"measured_on": today + timedelta(days=7), "polyp_count": 0})
        self.assertEqual(new.temporal_precision, "exact_date")
        self.assertGreater(new.polyp_state_sequence, event.parent_state_sequence)
        self.box.refresh_from_db()
        self.assertEqual(resolve_current_polyp_state(self.box)["polyp_count"], 0)
        self.assertEqual(resolve_current_polyp_state(children[0])["polyp_count"], 30)

    def test_status_is_rechecked_under_lock_after_preflight(self):
        before = self.snapshot()
        def parse_after_preflight():
            Box.objects.filter(pk=self.box.pk).update(status=Box.Status.INACTIVE)
            return {"measured_on": self.day}
        with self.assertRaises(MeasurementWriteInvalid):
            self.create(parse_after_preflight)
        self.assertEqual(self.snapshot(), before)

    @patch("apps.cultures.api_views.create_biological_measurement")
    def test_http_empty_active_scope_keeps_legacy_preflight_errors_without_writing(self, create_service):
        create_service.side_effect = AssertionError("Empty-scope HTTP preflight must not invoke a write")
        Organization.objects.update(is_active=False)
        self.admin.is_superuser = True
        self.admin.save(update_fields=["is_superuser"])
        self.client.force_login(self.admin)
        url = reverse("api_box_measurements", args=[self.box.pk])
        OrganizationMembership.objects.filter(user=self.admin).update(role="viewer")
        forbidden = self.client.post(url, data="{", content_type="application/json")
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(forbidden.json(), {
            "detail": "This account cannot create biological measurements.",
            "code": "measurement_write_forbidden",
        })
        OrganizationMembership.objects.filter(user=self.admin).update(role="admin")
        invalid = self.client.post(url, data={}, content_type="application/json")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json(), {"measured_on": ["This field is required."]})
        scoped_out = self.client.post(url, data={"measured_on": self.day}, content_type="application/json")
        self.assertEqual(scoped_out.status_code, 404)
        self.assertEqual(scoped_out.json(), {"detail": "No Box matches the given query."})
        malformed = self.client.post(url, data="{", content_type="application/json")
        self.assertEqual(malformed.status_code, 400)
        missing = self.client.post(
            reverse("api_box_measurements", args=[self.foreign_box.pk + 1000]),
            data="{", content_type="application/json",
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json(), {"detail": "No Box matches the given query."})
        create_service.assert_not_called()
        self.assertFalse(BiologicalMeasurement.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_http_authorized_creation_still_uses_the_strict_service(self):
        self.client.force_login(self.admin)
        with patch("apps.cultures.api_views.create_biological_measurement", wraps=create_biological_measurement) as create_service:
            response = self.client.post(
                reverse("api_box_measurements", args=[self.box.pk]),
                {"measured_on": self.day, "polyp_count": 0, "ephyrae_count": 0},
                content_type="application/json", HTTP_X_ORGANIZATION_ID=str(self.org.pk),
            )
        self.assertEqual(response.status_code, 201)
        create_service.assert_called_once()
        self.assertEqual(create_service.call_args.kwargs["organization_ids"], [self.org.pk])
        self.assertEqual(response.json()["polyp_count"], 0)
        self.assertEqual(BiologicalMeasurement.objects.get().user, self.admin)
        self.assertEqual(AuditLog.objects.get().action, AuditLog.Action.ENTRY)

    def test_http_validation_conflict_and_status_error_precedence(self):
        row = self.create()
        self.box.status = Box.Status.INACTIVE
        self.box.save(update_fields=["status"])
        self.client.force_login(self.admin)
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.org.pk)}
        url = reverse("api_box_measurements", args=[self.box.pk])
        invalid = self.client.post(url, {"measured_on": self.day, "polyp_count": -1}, content_type="application/json", **headers)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json(), {"polyp_count": ["Ensure this value is greater than or equal to 0."]})
        conflict = self.client.post(url, {"measured_on": self.day}, content_type="application/json", **headers)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json(), {
            "detail": "A biological measurement already exists for this box and ISO week.",
            "code": "measurement_week_conflict", "measurement_id": row.pk,
            "week_start": self.day.isoformat(),
        })
        inactive = self.client.post(url, {"measured_on": self.day + timedelta(days=7)}, content_type="application/json", **headers)
        self.assertEqual(inactive.status_code, 400)
        self.assertEqual(inactive.json(), ["An inactive box cannot receive a new measurement."])

    def test_http_error_precedence_and_shapes_are_unchanged_for_malformed_json(self):
        row = self.create()
        self.client.force_login(self.viewer)
        headers = {"HTTP_X_ORGANIZATION_ID": str(self.org.pk)}
        urls = (
            ("post", reverse("api_box_measurements", args=[self.box.pk]), "measurement_write_forbidden"),
            ("patch", reverse("api_box_measurement_detail", args=[self.box.pk, row.pk]), "role_read_only"),
        )
        for method, url, code in urls:
            with self.subTest(method=method):
                response = getattr(self.client, method)(url, data="{", content_type="application/json", **headers)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["code"], code)
        self.client.force_login(self.admin)
        for method, url, _ in urls:
            response = getattr(self.client, method)(url, data="{", content_type="application/json", **headers)
            self.assertEqual(response.status_code, 400)
        missing = self.client.patch(
            reverse("api_box_measurement_detail", args=[self.box.pk, row.pk + 1000]),
            data="{", content_type="application/json", **headers,
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json(), {"detail": "No BiologicalMeasurement matches the given query."})
        foreign = self.client.post(
            reverse("api_box_measurements", args=[self.foreign_box.pk]),
            data="{", content_type="application/json", **headers,
        )
        self.assertEqual(foreign.status_code, 404)
        self.assertEqual(foreign.json(), {"detail": "No Box matches the given query."})
