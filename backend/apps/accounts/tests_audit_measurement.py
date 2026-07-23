"""The history exposes the measurement behind an entry, so it can be corrected.

Clicking an entry in the history opens the measurement itself for editing. That
only works if the entry carries enough to reach it: the measurement id and the
box it belongs to.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.cultures.models import Box, ThermalZone
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import OrganizationMembership


class AuditLogMeasurementLinkTests(TestCase):
    def setUp(self):
        user_model = get_user_model()

        self.organization = Organization.objects.create(name="Aquarium de Paris", slug="paris")
        self.admin = user_model.objects.create_user(username="org_admin", password="secret")
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )

        species = Species.objects.create(scientific_name="Aurelia aurita", genus_species_code="AAU")
        strain = Strain.objects.create(species=species, code="1-ATL", number=1, origin_code="ATL")
        zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Cabinet-15",
            zone_type=ThermalZone.ZoneType.CABINET,
        )
        self.box = Box.objects.create(
            organization=self.organization,
            global_code="ATL-AAU-1.001",
            box_number="001",
            strain=strain,
            thermal_zone=zone,
        )
        self.measurement = BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=date(2026, 6, 15),
            polyp_count=42,
            ephyrae_count=3,
            notes="Releve du matin",
            user=self.admin,
        )

    def get_entries(self):
        response = self.client.get(reverse("api_account_audit_log"))
        self.assertEqual(response.status_code, 200)
        return response.json()["results"]

    def test_entry_carries_what_is_needed_to_open_the_measurement(self):
        AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.ENTRY,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement recorded",
            metadata={"measurement_id": self.measurement.id},
        )
        self.client.login(username="org_admin", password="secret")

        entry = self.get_entries()[0]

        editable = entry["editable_measurement"]
        self.assertIsNotNone(editable)
        self.assertEqual(editable["id"], self.measurement.id)
        self.assertEqual(editable["box_id"], self.box.id)
        self.assertEqual(editable["box_code"], "ATL-AAU-1.001")
        self.assertEqual(editable["measured_on"], "2026-06-15")
        self.assertEqual(editable["polyp_count"], 42)
        self.assertEqual(editable["ephyrae_count"], 3)
        self.assertEqual(editable["notes"], "Releve du matin")

    def test_entry_without_a_measurement_is_not_editable(self):
        AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.CREATION,
            object_type="account",
            object_id="org_admin",
            description="Member access created",
        )
        self.client.login(username="org_admin", password="secret")

        entry = self.get_entries()[0]

        self.assertIsNone(entry["editable_measurement"])

    def test_entry_pointing_at_a_deleted_measurement_is_not_editable(self):
        # The link must degrade quietly rather than offer a dead form.
        AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.ENTRY,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement recorded",
            metadata={"measurement_id": self.measurement.id},
        )
        self.measurement.delete()
        self.client.login(username="org_admin", password="secret")

        entry = self.get_entries()[0]

        self.assertIsNone(entry["editable_measurement"])

    def test_export_entry_is_never_editable(self):
        # Only measurements may be corrected from the history. An export whose
        # description carries a date must not be tied to a measurement.
        AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.EXPORT,
            object_type="box",
            object_id=self.box.global_code,
            description="CSV export for 2026-06-15",
        )
        self.client.login(username="org_admin", password="secret")

        entry = self.get_entries()[0]

        self.assertIsNone(entry["editable_measurement"])

    def test_transfer_entry_is_never_editable(self):
        AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.TRANSFER,
            object_type="box",
            object_id=self.box.global_code,
            description="Transfer prepared on 2026-06-15",
            metadata={"measurement_id": self.measurement.id},
        )
        self.client.login(username="org_admin", password="secret")

        entry = self.get_entries()[0]

        self.assertIsNone(entry["editable_measurement"])

    def test_correcting_a_measurement_keeps_a_single_history_entry(self):
        # The correction overwrites the stored measurement, so the history must
        # not end up showing the same reading twice.
        self.client.login(username="org_admin", password="secret")
        url = reverse("api_box_measurement_detail", args=[self.box.id, self.measurement.id])

        self.client.patch(url, data={"polyp_count": 50}, content_type="application/json")
        self.client.patch(url, data={"polyp_count": 60}, content_type="application/json")

        entries = AuditLog.objects.filter(metadata__measurement_id=self.measurement.id)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().metadata["valeurs"]["polypes"], 60)

    def test_the_entry_keeps_the_date_the_measurement_was_first_recorded(self):
        self.client.login(username="org_admin", password="secret")
        first = AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.ENTRY,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement for 2026-06-15",
            metadata={"measurement_id": self.measurement.id},
        )
        recorded_at = first.created_at

        self.client.patch(
            reverse("api_box_measurement_detail", args=[self.box.id, self.measurement.id]),
            data={"polyp_count": 77},
            content_type="application/json",
        )

        first.refresh_from_db()
        self.assertEqual(first.created_at, recorded_at)
        self.assertEqual(first.metadata["valeurs"]["polypes"], 77)

    def test_a_correction_surfaces_at_the_top_of_the_history(self):
        """The corrected entry must be visible, not buried at its old date.

        Its entry is updated in place and keeps created_at, so without ordering
        on the edit time the user would correct a measurement and see nothing
        change in the history.
        """
        old_entry = AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.ENTRY,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement for 2026-06-15",
            metadata={"measurement_id": self.measurement.id},
        )
        # A newer, unrelated entry that would otherwise sit on top.
        AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.CREATION,
            object_type="account",
            object_id="someone",
            description="Member access created",
        )
        self.client.login(username="org_admin", password="secret")

        self.client.patch(
            reverse("api_box_measurement_detail", args=[self.box.id, self.measurement.id]),
            data={"polyp_count": 90},
            content_type="application/json",
        )

        entries = self.get_entries()
        self.assertEqual(entries[0]["id"], old_entry.id)
        self.assertIsNotNone(entries[0]["edited_at"])
        self.assertEqual(entries[0]["edited_by"], "org_admin")
        self.assertEqual(entries[0]["metadata"]["valeurs"]["polypes"], 90)

    def test_an_untouched_entry_reports_no_edit(self):
        AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.CREATION,
            object_type="account",
            object_id="someone",
            description="Member access created",
        )
        self.client.login(username="org_admin", password="secret")

        entry = self.get_entries()[0]

        self.assertIsNone(entry["edited_at"])
        self.assertIsNone(entry["edited_by"])

    def test_correcting_the_measurement_updates_it_and_logs_the_change(self):
        self.client.login(username="org_admin", password="secret")

        response = self.client.patch(
            reverse("api_box_measurement_detail", args=[self.box.id, self.measurement.id]),
            data={"polyp_count": 50, "notes": "Comptage corrige"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.measurement.refresh_from_db()
        self.assertEqual(self.measurement.polyp_count, 50)
        self.assertEqual(self.measurement.notes, "Comptage corrige")
        # The correction is itself recorded, which is the whole point of doing
        # it this way rather than rewriting the history entry.
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.UPDATE,
                object_id=self.box.global_code,
                metadata__measurement_id=self.measurement.id,
            ).exists()
        )
