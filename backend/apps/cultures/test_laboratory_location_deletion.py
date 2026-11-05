"""Permanent deletion of never-used laboratory locations.

Product rule (2026-11-05): a location may be deleted only when it has never
been used and nothing references it. Zero active boxes is not proof of
non-use. Rejected deletions change nothing; accepted ones keep every audit
entry and add a DELETION entry in the same transaction.
"""

import json
import uuid
from datetime import date, datetime
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import models
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.audit.models import Alert, AuditLog
from apps.exports.models import DataExport
from apps.measurements.models import (
    BiologicalMeasurement,
    DailyTemperature,
    Probe,
    SalinityMeasurement,
    ThermalAnomaly,
)
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from . import location_deletion
from .models import (
    MOVEMENT_WEEK_TIME_ZONE,
    AutomaticBoxCreationRequest,
    Box,
    BoxLocation,
    BoxMovement,
    HistoricalBoxMovement,
    IdentificationTag,
    ThermalZone,
)


class LocationDeletionTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.organization = Organization.objects.create(name="Deletion home", slug="deletion-home")
        self.other_organization = Organization.objects.create(name="Deletion other", slug="deletion-other")
        self.admin = user_model.objects.create_user(username="del_admin", email="del_admin@example.org", password="x")
        OrganizationMembership.objects.create(
            user=self.admin, organization=self.organization, role=OrganizationMembership.Role.ADMIN,
        )
        OrganizationMembership.objects.create(
            user=self.admin, organization=self.other_organization, role=OrganizationMembership.Role.VIEWER,
        )
        self.technician = user_model.objects.create_user(username="del_tech", email="del_tech@example.org", password="x")
        OrganizationMembership.objects.create(
            user=self.technician, organization=self.organization,
            role=OrganizationMembership.Role.LAB_TECHNICIAN,
        )
        self.viewer = user_model.objects.create_user(username="del_viewer", email="del_viewer@example.org", password="x")
        OrganizationMembership.objects.create(
            user=self.viewer, organization=self.organization, role=OrganizationMembership.Role.VIEWER,
        )
        species = Species.objects.create(scientific_name="Aurelia deletion", genus_species_code="ADL")
        self.strain = Strain.objects.create(
            species=species, code="ADL-LAB-1", organization=self.organization, number=1, origin_code="LAB",
        )
        self.other_zone = ThermalZone.objects.create(organization=self.organization, name="Armoire 10")
        self.foreign_zone = ThermalZone.objects.create(organization=self.other_organization, name="Foreign 15")

    # -- helpers -----------------------------------------------------------------

    def headers(self, organization=None):
        return {"HTTP_X_ORGANIZATION_ID": str((organization or self.organization).pk)}

    def create_zone_through_api(self, payload=None):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("api_thermal_zone_list"),
            data=json.dumps({"organization": self.organization.pk, "name": "Armoire 30", **(payload or {})}),
            content_type="application/json", **self.headers(),
        )
        self.assertEqual(response.status_code, 201, response.content)
        return ThermalZone.objects.get(pk=response.json()["id"])

    def delete(self, zone, *, user=None, organization=None):
        self.client.force_login(user or self.admin)
        return self.client.delete(reverse("api_thermal_zone_detail", args=[zone.pk]), **self.headers(organization))

    def eligibility(self, zone, *, user=None, organization=None):
        self.client.force_login(user or self.admin)
        return self.client.get(reverse("api_thermal_zone_deletion", args=[zone.pk]), **self.headers(organization))

    def create_box(self, number, *, zone, status=Box.Status.ACTIVE):
        box = Box.objects.create(
            organization=self.organization, global_code=f"ADL-LAB-1.{number}", box_number=number,
            strain=self.strain, status=status, thermal_zone=zone,
        )
        if zone is not None:
            BoxLocation.objects.create(
                box=box, thermal_zone=zone, starts_at=datetime(2025, 1, 6, 9, 0, tzinfo=MOVEMENT_WEEK_TIME_ZONE),
            )
        return box

    def assert_refused(self, zone, reasons):
        audit_ids = set(AuditLog.objects.values_list("id", flat=True))
        eligibility = self.eligibility(zone)
        self.assertEqual(eligibility.status_code, 200)
        self.assertEqual(eligibility.json()["deletable"], False)
        self.assertEqual(eligibility.json()["reasons"], reasons)
        response = self.delete(zone)
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["code"], "thermal_zone_in_use")
        self.assertEqual(response.json()["reasons"], reasons)
        self.assertTrue(ThermalZone.objects.filter(pk=zone.pk).exists())
        self.assertEqual(set(AuditLog.objects.values_list("id", flat=True)), audit_ids, "no audit on refusal")

    # -- accepted deletion -----------------------------------------------------------

    def test_never_used_location_is_deleted_with_audit_and_history_kept(self):
        zone = self.create_zone_through_api({"capacity": 0, "target_temperature_c": "0", "salinity_psu": "0"})
        self.client.patch(
            reverse("api_thermal_zone_detail", args=[zone.pk]), data=json.dumps({"name": "Armoire 31"}),
            content_type="application/json", **self.headers(),
        )
        history = list(
            AuditLog.objects.filter(metadata__thermal_zone_id=zone.pk).order_by("id").values_list("id", "action")
        )
        self.assertEqual([action for _, action in history], ["creation", "update"])
        unrelated = AuditLog.objects.create(organization=self.organization, action=AuditLog.Action.UPDATE,
                                            object_type="box", object_id="X", metadata={})

        eligibility = self.eligibility(zone)
        self.assertEqual(eligibility.json(), {
            "thermal_zone_id": zone.pk, "deletable": True, "reasons": [], "box_count": 0,
        })
        response = self.delete(zone)

        self.assertEqual(response.status_code, 204, response.content)
        self.assertFalse(ThermalZone.objects.filter(pk=zone.pk).exists())
        # Earlier entries are untouched and reachable without the location.
        for entry_id, action in history:
            self.assertEqual(AuditLog.objects.get(pk=entry_id).action, action)
        self.assertTrue(AuditLog.objects.filter(pk=unrelated.pk).exists())
        deletion = AuditLog.objects.get(action=AuditLog.Action.DELETION)
        self.assertEqual(deletion.organization, self.organization)
        self.assertEqual(deletion.user, self.admin)
        self.assertEqual((deletion.object_type, deletion.object_id), ("thermal_zone", "Armoire 31"))
        self.assertEqual(deletion.description, "Thermal zone deleted: Armoire 31")
        # Zero configuration values are recorded as zero, never as missing.
        self.assertEqual(deletion.metadata, {
            "thermal_zone_id": zone.pk,
            "event": "thermal_zone_deletion",
            "reason": "never_used",
            "valeurs": {
                "nom": "Armoire 31", "type": "cabinet", "temperature_consigne": "0.0", "capacite": 0,
                "salinite_psu": "0.00", "active": True,
            },
        })
        self.assertTrue(ThermalZone.objects.filter(pk=self.other_zone.pk).exists())

    def test_deletion_appears_in_institution_history(self):
        zone = self.create_zone_through_api()
        self.assertEqual(self.delete(zone).status_code, 204)
        from apps.audit.services import impactful_audit_logs

        actions = list(impactful_audit_logs(organization_id=self.organization.pk).values_list("action", flat=True))
        self.assertIn("deletion", actions)

    # -- refused deletion -----------------------------------------------------------

    def test_occupied_location_is_refused(self):
        zone = self.create_zone_through_api()
        box = self.create_box("001", zone=zone)
        self.assert_refused(zone, ["occupied", "history"])
        box.refresh_from_db()
        self.assertEqual(box.thermal_zone_id, zone.pk)
        self.assertEqual(BoxLocation.objects.filter(box=box, thermal_zone=zone).count(), 1)

    def test_previously_used_location_with_zero_active_boxes_is_refused(self):
        zone = self.create_zone_through_api()
        box = self.create_box("001", zone=zone)
        # The box left: closed period, current location elsewhere.
        BoxLocation.objects.filter(box=box).update(ends_at=timezone.now())
        Box.objects.filter(pk=box.pk).update(thermal_zone=self.other_zone)
        self.assertEqual(self.eligibility(zone).json()["box_count"], 0)
        self.assert_refused(zone, ["history"])

    def test_location_of_an_inactive_box_history_is_refused(self):
        zone = self.create_zone_through_api()
        box = self.create_box("001", zone=zone, status=Box.Status.INACTIVE)
        Box.objects.filter(pk=box.pk).update(thermal_zone=None)
        BoxLocation.objects.filter(box=box).update(ends_at=timezone.now())
        self.assert_refused(zone, ["history"])

    def test_every_associated_record_kind_blocks_deletion(self):
        box = self.create_box("009", zone=self.other_zone)
        records = {
            "movement origin": lambda zone: BoxMovement.objects.create(
                box=box, from_thermal_zone=zone, to_thermal_zone=self.other_zone,
            ),
            "movement destination": lambda zone: BoxMovement.objects.create(box=box, to_thermal_zone=zone),
            "daily temperature": lambda zone: DailyTemperature.objects.create(
                thermal_zone=zone, date=date(2025, 1, 6), average_temperature_c="0.0",
            ),
            "salinity": lambda zone: SalinityMeasurement.objects.create(
                thermal_zone=zone, measured_on=date(2025, 1, 6), salinity_psu="0.00",
            ),
            "probe": lambda zone: Probe.objects.create(organization=self.organization, thermal_zone=zone, code=f"P{zone.pk}"),
            "tag": lambda zone: IdentificationTag.objects.create(code=f"tag-{zone.pk}", thermal_zone=zone),
            "anomaly": lambda zone: ThermalAnomaly.objects.create(thermal_zone=zone, starts_at=timezone.now()),
            "alert": lambda zone: Alert.objects.create(
                organization=self.organization, thermal_zone=zone, alert_type="temperature", message="old",
            ),
            "pending box creation intent": lambda zone: AutomaticBoxCreationRequest.objects.create(
                organization=self.organization, actor=self.admin, request_id=uuid.uuid4(),
                resume_token_hash=uuid.uuid4().hex * 2,
                intent={"strain": self.strain.pk, "thermal_zone": zone.pk, "entered_on": "2025-01-06",
                        "volume_liters": None, "notes": ""},
            ),
            "export filter": lambda zone: DataExport.objects.create(
                organization=self.organization, export_type="inventory", file_format="csv",
                filters={"zones": [self.other_zone.pk, zone.pk], "include_other_zones": False},
            ),
            "manual temperature audit": lambda zone: AuditLog.objects.create(
                organization=self.organization, action=AuditLog.Action.UPDATE, object_type="thermal_zone",
                object_id=zone.name, description=f"Manual temperature recorded: {zone.name}",
                metadata={"thermal_zone_id": zone.pk, "valeurs": {"date": "2025-01-06", "temperature_c": "0"}},
            ),
            "legacy location audit by name only": lambda zone: AuditLog.objects.create(
                organization=self.organization, action=AuditLog.Action.UPDATE, object_type="thermal_zone",
                object_id=zone.name, metadata={}, original_values={},
            ),
            "box audit by name": lambda zone: AuditLog.objects.create(
                organization=self.organization, action=AuditLog.Action.CREATION, object_type="box",
                object_id="ADL-LAB-1.099", metadata={"valeurs": {"emplacement": zone.name}},
            ),
            "movement audit by id": lambda zone: AuditLog.objects.create(
                organization=self.organization, action=AuditLog.Action.UPDATE, object_type="box",
                object_id="ADL-LAB-1.098", metadata={"to_thermal_zone_id": zone.pk},
            ),
        }
        for index, (label, create) in enumerate(records.items()):
            with self.subTest(label):
                zone = ThermalZone.objects.create(organization=self.organization, name=f"Used {index}")
                create(zone)
                self.assert_refused(zone, ["history"])

    def test_former_name_reference_blocks_after_a_rename(self):
        zone = self.create_zone_through_api()
        AuditLog.objects.create(
            organization=self.organization, action=AuditLog.Action.CREATION, object_type="box",
            object_id="ADL-LAB-1.097", metadata={"valeurs": {"emplacement": "Armoire 30"}},
        )
        self.client.patch(
            reverse("api_thermal_zone_detail", args=[zone.pk]), data=json.dumps({"name": "Armoire 32"}),
            content_type="application/json", **self.headers(),
        )
        self.assert_refused(zone, ["history"])

    def test_historical_movement_evidence_blocks_only_its_institution(self):
        zone = self.create_zone_through_api()
        box = self.create_box("010", zone=self.other_zone)
        origin = BoxLocation.objects.get(box=box)
        destination = BoxLocation.objects.create(
            box=box, thermal_zone=zone, starts_at=timezone.now(),
        )
        evidence_zone = ThermalZone.objects.create(organization=self.organization, name="Evidence only")
        reconstruction = HistoricalBoxMovement.objects.create(
            box=box, iso_year=2025, iso_week=2,
            from_thermal_zone=self.other_zone, to_thermal_zone=zone,
            origin_location=origin, destination_location=destination,
            candidate_key="a" * 64, plan_fingerprint="b" * 64,
            evidence={"thermal_zone_ids": [evidence_zone.pk, self.foreign_zone.pk]}, accepted_by=self.admin,
        )
        self.assert_refused(zone, ["history"])
        # Evidence may name a third location without a direct foreign key.
        self.assert_refused(evidence_zone, ["history"])
        self.assertEqual(location_deletion.thermal_zone_usage_reasons(self.foreign_zone), [])
        self.assertTrue(HistoricalBoxMovement.objects.filter(pk=reconstruction.pk).exists())

    def test_references_to_other_locations_and_booleans_do_not_block(self):
        zone = self.create_zone_through_api()
        AuditLog.objects.create(
            organization=self.organization, action=AuditLog.Action.UPDATE, object_type="box", object_id="B",
            metadata={"to_thermal_zone_id": self.other_zone.pk, "has_current_thermal_zone": True,
                      "zone_type": "cabinet", "count": zone.pk},
        )
        self.assertEqual(self.delete(zone).status_code, 204)

    # -- access ------------------------------------------------------------------------

    def test_another_institution_location_is_not_found(self):
        for response in (self.delete(self.foreign_zone), self.eligibility(self.foreign_zone)):
            self.assertEqual(response.status_code, 404)
        self.assertTrue(ThermalZone.objects.filter(pk=self.foreign_zone.pk).exists())
        # Reader membership there does not allow deleting either.
        self.assertEqual(self.delete(self.foreign_zone, organization=self.other_organization).status_code, 403)
        self.assertTrue(ThermalZone.objects.filter(pk=self.foreign_zone.pk).exists())

    def test_technician_and_viewer_cannot_delete(self):
        zone = self.create_zone_through_api()
        for user in (self.technician, self.viewer):
            with self.subTest(user.username):
                self.assertEqual(self.delete(zone, user=user).status_code, 403)
                self.assertEqual(self.eligibility(zone, user=user).status_code, 403)
        self.client.logout()
        self.assertIn(self.client.delete(reverse("api_thermal_zone_detail", args=[zone.pk])).status_code, (401, 403))
        self.assertTrue(ThermalZone.objects.filter(pk=zone.pk).exists())
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.DELETION).exists())

    # -- defensive guards ---------------------------------------------------------------

    def test_a_reference_missed_by_the_checks_aborts_without_cascade(self):
        zone = self.create_zone_through_api()
        DailyTemperature.objects.create(thermal_zone=zone, date=date(2025, 1, 6), average_temperature_c="0.0")
        Box.objects.filter(pk=self.create_box("002", zone=self.other_zone).pk).update(thermal_zone=zone)
        BoxLocation.objects.filter(thermal_zone=zone).delete()
        # Simulate checks that see nothing: the collector guard must still refuse.
        with patch.object(location_deletion, "thermal_zone_usage_reasons", return_value=[]):
            response = self.delete(zone)
        self.assertEqual(response.status_code, 409)
        self.assertTrue(ThermalZone.objects.filter(pk=zone.pk).exists())
        self.assertEqual(DailyTemperature.objects.filter(thermal_zone=zone).count(), 1)
        self.assertEqual(Box.objects.filter(thermal_zone=zone).count(), 1, "no reference was nulled")
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.DELETION).exists(), "audit rolled back")

    def test_location_removed_meanwhile_is_reported_not_found(self):
        zone = self.create_zone_through_api()
        with patch.object(location_deletion.ThermalZone.objects, "select_for_update") as locked:
            locked.return_value.get.side_effect = ThermalZone.DoesNotExist
            response = self.delete(zone)
        self.assertEqual(response.status_code, 404)

    # -- coverage of the checked relations ------------------------------------------------

    def test_every_relation_to_locations_is_checked(self):
        relations = {
            (relation.related_model._meta.label, relation.field.name)
            for relation in ThermalZone._meta.related_objects
        }
        # A new relation must be reviewed: it is checked automatically, this
        # list only makes the change visible.
        self.assertEqual(relations, {
            ("cultures.Box", "thermal_zone"), ("cultures.BoxLocation", "thermal_zone"),
            ("cultures.BoxMovement", "from_thermal_zone"), ("cultures.BoxMovement", "to_thermal_zone"),
            ("cultures.HistoricalBoxMovement", "from_thermal_zone"),
            ("cultures.HistoricalBoxMovement", "to_thermal_zone"),
            ("cultures.IdentificationTag", "thermal_zone"), ("measurements.Probe", "thermal_zone"),
            ("measurements.DailyTemperature", "thermal_zone"), ("measurements.SalinityMeasurement", "thermal_zone"),
            ("measurements.ThermalAnomaly", "thermal_zone"), ("audit.Alert", "thermal_zone"),
        })

    def test_every_json_document_model_is_scanned_or_exempted(self):
        json_models = {
            model._meta.label for model in apps.get_models()
            if any(isinstance(field, models.JSONField) for field in model._meta.concrete_fields)
        }
        covered = location_deletion.scanned_json_models() | set(location_deletion.JSON_EXEMPTIONS)
        self.assertEqual(json_models, covered)


class ExportLocationReferenceTests(TestCase):
    """Recorded exports and location deletion (2026-11-05 product decision).

    A recorded export naming a location is historical data of that location;
    an export without a location filter names none.
    """

    # Same fixtures and helpers as the deletion tests, without their tests.
    headers = LocationDeletionTests.headers
    delete = LocationDeletionTests.delete
    eligibility = LocationDeletionTests.eligibility
    create_box = LocationDeletionTests.create_box
    assert_refused = LocationDeletionTests.assert_refused

    def setUp(self):
        LocationDeletionTests.setUp(self)
        self.used_zone = ThermalZone.objects.create(organization=self.organization, name="Armoire 40")
        self.unused_zone = ThermalZone.objects.create(organization=self.organization, name="Armoire 41")
        box = self.create_box("040", zone=self.used_zone)
        BiologicalMeasurement.objects.create(
            box=box, measured_on=date(2025, 1, 8), polyp_count=0, ephyrae_count=0, user=self.admin,
        )

    def export(self, query, *, user=None):
        self.client.force_login(user or self.admin)
        return self.client.get(
            reverse("api_export_measurements_csv"),
            {"date_from": "2025-01-01", "date_to": "2025-01-31", **query}, **self.headers(),
        )

    def recorded(self):
        export = DataExport.objects.get()
        audit = AuditLog.objects.get(action=AuditLog.Action.EXPORT)
        return export.filters, audit.metadata

    def test_location_filtered_export_blocks_the_deletion_of_each_named_location(self):
        response = self.export({"zones": f"{self.used_zone.pk},{self.unused_zone.pk}"})
        self.assertEqual(response.status_code, 200)
        filters, metadata = self.recorded()
        self.assertEqual(filters["zones"], [self.used_zone.pk, self.unused_zone.pk])
        self.assertEqual(metadata["zones"], [self.used_zone.pk, self.unused_zone.pk])
        # Zero counts are exported values, not missing data.
        self.assertIn(",0,", response.content.decode("utf-8-sig"))
        self.assert_refused(self.unused_zone, ["history"])
        self.assertEqual(DataExport.objects.count(), 1, "the export record is kept")

    def test_unfiltered_export_does_not_block_a_never_used_location(self):
        self.assertEqual(self.export({}).status_code, 200)
        filters, metadata = self.recorded()
        self.assertNotIn("zones", filters)
        self.assertNotIn("zones", metadata)
        self.assertEqual(self.delete(self.unused_zone).status_code, 204)
        self.assertEqual(DataExport.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.EXPORT).count(), 1)

    def test_only_locations_of_the_institution_are_recorded_and_the_file_is_unchanged(self):
        reference = self.export({"zones": str(self.used_zone.pk)})
        DataExport.objects.all().delete()
        AuditLog.objects.filter(action=AuditLog.Action.EXPORT).delete()
        self.assertEqual(self.delete(self.unused_zone).status_code, 204)
        deleted_id = self.unused_zone.pk

        response = self.export({"zones": f"{self.used_zone.pk},{self.foreign_zone.pk},{deleted_id},999999"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, reference.content)
        filters, metadata = self.recorded()
        self.assertEqual(filters["zones"], [self.used_zone.pk])
        self.assertEqual(metadata["zones"], [self.used_zone.pk])
        # The other institution's location stays deletable by its own admins.
        self.assertFalse(location_deletion._has_json_reference(self.foreign_zone, {self.foreign_zone.name}))

    def test_export_naming_only_unavailable_locations_records_nothing(self):
        response = self.export({"zones": str(self.foreign_zone.pk)})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(DataExport.objects.exists())
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.EXPORT).exists())

    def test_unauthorized_export_records_nothing(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("api_export_measurements_csv"),
            {"organizations": str(self.other_organization.pk), "zones": str(self.unused_zone.pk)},
            **self.headers(),
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(DataExport.objects.exists())
        self.assertEqual(self.delete(self.unused_zone).status_code, 204)

    def test_failed_audit_rolls_back_the_export_record(self):
        with patch("apps.exports.views.AuditLog.objects.create", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError):
                self.export({"zones": f"{self.used_zone.pk},{self.unused_zone.pk}"})
        self.assertFalse(DataExport.objects.exists())
        self.assertEqual(self.delete(self.unused_zone).status_code, 204)
