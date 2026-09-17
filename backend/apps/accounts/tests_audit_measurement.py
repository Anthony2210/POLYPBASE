"""The history exposes the measurement behind an entry, so it can be corrected.

Clicking an entry in the history opens the measurement itself for editing. That
only works if the entry carries enough to reach it: the measurement id and the
box it belongs to.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.audit.services import classify_audit_log, legacy_measurement_lookup_key
from apps.cultures.models import Box, ThermalZone
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import OrganizationMembership


class AuditLogMeasurementLinkTests(TestCase):
    def setUp(self):
        user_model = get_user_model()

        self.organization = Organization.objects.create(name="Aquarium de Paris", slug="paris")
        self.admin = user_model.objects.create_user(username="org_admin", email="org_admin@example.org",password="secret")
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.second_user = user_model.objects.create_user(
            username="second_user",
            email="second_user@example.org",
            password="secret",
        )
        self.third_user = user_model.objects.create_user(
            username="third_user",
            email="third_user@example.org",
            password="secret",
        )
        for user in (self.second_user, self.third_user):
            OrganizationMembership.objects.create(
                user=user,
                organization=self.organization,
                role=OrganizationMembership.Role.LAB_TECHNICIAN,
            )
        self.other_organization = Organization.objects.create(
            name="Partner Laboratory",
            slug="partner-laboratory",
        )
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.other_organization,
            role=OrganizationMembership.Role.VIEWER,
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
        other_zone = ThermalZone.objects.create(
            organization=self.other_organization,
            name="Partner cabinet",
            zone_type=ThermalZone.ZoneType.CABINET,
        )
        self.other_box = Box.objects.create(
            organization=self.other_organization,
            global_code="PARTNER-AAU-1.001",
            box_number="001",
            strain=strain,
            thermal_zone=other_zone,
        )
        self.other_measurement = BiologicalMeasurement.objects.create(
            box=self.other_box,
            measured_on=date(2026, 6, 15),
            polyp_count=987,
            ephyrae_count=654,
            salinity_psu="31.25",
            notes="Foreign institution note",
            user=self.admin,
        )

    def get_entries(self):
        response = self.client.get(
            reverse("api_account_audit_log"),
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )
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
        self.assertEqual(entry["metadata"]["valeurs"]["polypes"], 42)
        self.assertEqual(entry["metadata"]["valeurs"]["strobiles"], 0)

    def test_foreign_direct_measurement_reference_is_not_enriched(self):
        AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.ENTRY,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement recorded",
            metadata={"measurement_id": self.other_measurement.id},
        )
        self.client.login(username="org_admin", password="secret")

        entry = self.get_entries()[0]

        self.assertIsNone(entry["editable_measurement"])
        self.assertEqual(entry["metadata"], {})

    def test_invalid_direct_measurement_reference_does_not_use_fallback(self):
        AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.ENTRY,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement for 2026-06-15",
            metadata={"measurement_id": "not-a-measurement-id"},
        )
        self.client.login(username="org_admin", password="secret")

        entry = self.get_entries()[0]

        self.assertIsNone(entry["editable_measurement"])
        self.assertEqual(entry["metadata"], {})

    def test_same_institution_legacy_box_date_reference_is_enriched(self):
        AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.ENTRY,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement for 2026-06-15",
        )
        self.client.login(username="org_admin", password="secret")

        entry = self.get_entries()[0]

        self.assertIsNone(entry["editable_measurement"])
        self.assertEqual(entry["metadata"]["valeurs"]["polypes"], 42)
        self.assertEqual(entry["metadata"]["valeurs"]["ephyrules"], 3)
        self.assertEqual(entry["metadata"]["valeurs"]["strobiles"], 0)
        self.assertEqual(entry["metadata"]["valeurs"]["note"], "Releve du matin")

    def test_foreign_legacy_box_date_reference_is_not_enriched(self):
        AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.ENTRY,
            object_type="box",
            object_id=self.other_box.global_code,
            description="Biological measurement for 2026-06-15",
        )
        self.client.login(username="org_admin", password="secret")

        entry = self.get_entries()[0]

        self.assertIsNone(entry["editable_measurement"])
        self.assertEqual(entry["metadata"], {})

    def test_legacy_lookup_predicate_matches_measurement_classification(self):
        legacy_measurement = AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.UPDATE,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement for 2026-06-15",
        )
        dated_movement = AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.UPDATE,
            object_type="box",
            object_id=self.box.global_code,
            description="Box moved on 2026-06-15",
            metadata={
                "movement_id": 12,
                "to_thermal_zone_name": "Cabinet-15",
            },
        )

        self.assertEqual(classify_audit_log(legacy_measurement), "measurements")
        self.assertEqual(
            legacy_measurement_lookup_key(legacy_measurement),
            (self.box.global_code, date(2026, 6, 15)),
        )
        self.assertEqual(classify_audit_log(dated_movement), "boxes")
        self.assertIsNone(legacy_measurement_lookup_key(dated_movement))

    def test_unrelated_dated_box_events_are_not_measurement_enriched(self):
        events = [
            AuditLog.objects.create(
                organization=self.organization,
                user=self.admin,
                action=AuditLog.Action.UPDATE,
                object_type="box",
                object_id=self.box.global_code,
                description="Box moved on 2026-06-15",
                metadata={
                    "movement_id": 12,
                    "from_thermal_zone_name": "Cabinet-14",
                    "to_thermal_zone_name": "Cabinet-15",
                },
            ),
            AuditLog.objects.create(
                organization=self.organization,
                user=self.admin,
                action=AuditLog.Action.UPDATE,
                object_type="box",
                object_id=self.box.global_code,
                description="Box deactivated on 2026-06-15",
                metadata={
                    "transition": "active->inactive",
                    "after": {"stop_reason": "Culture ended"},
                },
            ),
            AuditLog.objects.create(
                organization=self.organization,
                user=self.admin,
                action=AuditLog.Action.UPDATE,
                object_type="box",
                object_id=self.box.global_code,
                description="Arbitrary box note dated 2026-06-15",
            ),
        ]
        self.client.login(username="org_admin", password="secret")

        entries = {entry["id"]: entry for entry in self.get_entries()}

        for event in events:
            self.assertNotIn("valeurs", entries[event.id]["metadata"])
            self.assertEqual(entries[event.id]["family"], "boxes")

    def test_supported_legacy_format_must_match_the_complete_description(self):
        event = AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.UPDATE,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement for 2026-06-15 corrected",
        )
        self.client.login(username="org_admin", password="secret")

        entry = self.get_entries()[0]

        self.assertEqual(classify_audit_log(event), "boxes")
        self.assertIsNone(legacy_measurement_lookup_key(event))
        self.assertEqual(entry["family"], "boxes")
        self.assertNotIn("valeurs", entry["metadata"])

    def test_unresolved_supported_legacy_measurement_degrades_safely(self):
        AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.ENTRY,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement for 2026-06-14",
        )
        self.client.login(username="org_admin", password="secret")

        entry = self.get_entries()[0]

        self.assertEqual(entry["family"], "measurements")
        self.assertEqual(entry["metadata"], {})
        self.assertIsNone(entry["editable_measurement"])

    def test_legacy_measurements_are_resolved_with_one_page_level_query(self):
        measurements = [self.measurement]
        for measured_on, polyp_count in [
            (date(2026, 6, 16), 16),
            (date(2026, 6, 17), 17),
        ]:
            measurements.append(
                BiologicalMeasurement.objects.create(
                    box=self.box,
                    measured_on=measured_on,
                    polyp_count=polyp_count,
                    ephyrae_count=0,
                    user=self.admin,
                )
            )
        for measurement in measurements:
            AuditLog.objects.create(
                organization=self.organization,
                user=self.admin,
                action=AuditLog.Action.ENTRY,
                object_type="box",
                object_id=self.box.global_code,
                description=f"Biological measurement for {measurement.measured_on}",
            )
        self.client.login(username="org_admin", password="secret")

        with CaptureQueriesContext(connection) as queries:
            entries = self.get_entries()

        measurement_table = BiologicalMeasurement._meta.db_table.lower()
        measurement_queries = [
            query["sql"]
            for query in queries.captured_queries
            if measurement_table in query["sql"].lower()
        ]
        self.assertEqual(len(measurement_queries), 1)
        self.assertEqual(
            {
                entry["metadata"]["valeurs"]["date"]
                for entry in entries
            },
            {"2026-06-15", "2026-06-16", "2026-06-17"},
        )

    def test_non_admin_cannot_view_selected_organization_audit_log(self):
        self.client.login(username="org_admin", password="secret")

        response = self.client.get(
            reverse("api_account_audit_log"),
            HTTP_X_ORGANIZATION_ID=str(self.other_organization.id),
        )

        self.assertEqual(response.status_code, 403)

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

    def test_measurement_creation_and_corrections_are_append_only(self):
        measurement_date = date(2026, 6, 16)
        self.client.login(username="org_admin", password="secret")
        creation_started_at = timezone.now()
        creation_response = self.client.post(
            reverse("api_box_measurements", args=[self.box.id]),
            data={
                "measured_on": measurement_date.isoformat(),
                "polyp_count": 12,
                "ephyrae_count": 3,
            },
            content_type="application/json",
        )
        creation_finished_at = timezone.now()
        self.assertEqual(creation_response.status_code, 201)
        measurement_id = creation_response.json()["id"]
        detail_url = reverse(
            "api_box_measurement_detail",
            args=[self.box.id, measurement_id],
        )
        creation_event = AuditLog.objects.get(
            metadata__measurement_id=measurement_id,
            action=AuditLog.Action.ENTRY,
        )
        creation_snapshot = {
            "user_id": creation_event.user_id,
            "created_at": creation_event.created_at,
            "metadata": creation_event.metadata,
        }

        self.client.login(username="second_user", password="secret")
        second_started_at = timezone.now()
        second_response = self.client.patch(
            detail_url,
            data={"polyp_count": 0, "ephyrae_count": 0},
            content_type="application/json",
        )
        second_finished_at = timezone.now()
        self.assertEqual(second_response.status_code, 200)
        second_event = AuditLog.objects.get(
            metadata__measurement_id=measurement_id,
            user=self.second_user,
        )
        second_snapshot = {
            "user_id": second_event.user_id,
            "created_at": second_event.created_at,
            "metadata": second_event.metadata,
        }

        self.client.login(username="third_user", password="secret")
        third_started_at = timezone.now()
        third_response = self.client.patch(
            detail_url,
            data={"polyp_count": 7},
            content_type="application/json",
        )
        third_finished_at = timezone.now()
        self.assertEqual(third_response.status_code, 200)

        events = list(
            AuditLog.objects.filter(metadata__measurement_id=measurement_id).order_by(
                "created_at", "id"
            )
        )
        self.assertEqual(len(events), 3)
        self.assertEqual(
            [event.user_id for event in events],
            [self.admin.id, self.second_user.id, self.third_user.id],
        )
        self.assertEqual(
            [event.action for event in events],
            [AuditLog.Action.ENTRY, AuditLog.Action.UPDATE, AuditLog.Action.UPDATE],
        )
        self.assertTrue(
            all(event.organization_id == self.organization.id for event in events)
        )
        self.assertGreaterEqual(events[0].created_at, creation_started_at)
        self.assertLessEqual(events[0].created_at, creation_finished_at)
        self.assertGreaterEqual(events[1].created_at, second_started_at)
        self.assertLessEqual(events[1].created_at, second_finished_at)
        self.assertGreaterEqual(events[2].created_at, third_started_at)
        self.assertLessEqual(events[2].created_at, third_finished_at)
        self.assertTrue(all(event.edited_at is None for event in events))
        self.assertTrue(all(event.edited_by_id is None for event in events))

        creation_event.refresh_from_db()
        second_event.refresh_from_db()
        self.assertEqual(
            {
                "user_id": creation_event.user_id,
                "created_at": creation_event.created_at,
                "metadata": creation_event.metadata,
            },
            creation_snapshot,
        )
        self.assertEqual(
            {
                "user_id": second_event.user_id,
                "created_at": second_event.created_at,
                "metadata": second_event.metadata,
            },
            second_snapshot,
        )

        self.assertEqual(second_event.metadata["before"]["polypes"], 12)
        self.assertEqual(second_event.metadata["after"]["polypes"], 0)
        self.assertEqual(second_event.metadata["after"]["ephyrules"], 0)
        third_event = events[2]
        self.assertEqual(third_event.metadata["before"]["polypes"], 0)
        self.assertEqual(third_event.metadata["before"]["ephyrules"], 0)
        self.assertEqual(third_event.metadata["after"]["polypes"], 7)
        self.assertEqual(
            third_event.metadata["modifications"]["polypes"],
            {"avant": 0, "apres": 7},
        )

        self.client.login(username="org_admin", password="secret")
        serialized_events = [
            entry
            for entry in self.get_entries()
            if entry["metadata"].get("measurement_id") == measurement_id
        ]
        self.assertEqual(len(serialized_events), 3)
        serialized_by_user = {entry["user"]: entry for entry in serialized_events}
        self.assertEqual(
            set(serialized_by_user),
            {"org_admin", "second_user", "third_user"},
        )
        self.assertEqual(
            serialized_by_user["third_user"]["metadata"]["before"]["polypes"],
            0,
        )
        self.assertEqual(
            serialized_by_user["third_user"]["metadata"]["after"]["polypes"],
            7,
        )

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
        self.assertEqual(first.metadata, {"measurement_id": self.measurement.id})
        correction = AuditLog.objects.get(
            metadata__measurement_id=self.measurement.id,
            action=AuditLog.Action.UPDATE,
        )
        self.assertEqual(correction.metadata["before"]["polypes"], 42)
        self.assertEqual(correction.metadata["after"]["polypes"], 77)

    def test_a_correction_surfaces_as_a_new_history_event(self):
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

        correction = AuditLog.objects.get(
            metadata__measurement_id=self.measurement.id,
            action=AuditLog.Action.UPDATE,
        )
        entries = self.get_entries()
        self.assertEqual(entries[0]["id"], correction.id)
        self.assertNotEqual(entries[0]["id"], old_entry.id)
        self.assertEqual(entries[0]["user"], "org_admin")
        self.assertIsNone(entries[0]["edited_at"])
        self.assertIsNone(entries[0]["edited_by"])
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
        # The correction is recorded as its own event.
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.UPDATE,
                object_id=self.box.global_code,
                metadata__measurement_id=self.measurement.id,
            ).exists()
        )

    def test_related_action_counts_use_one_grouped_scoped_query(self):
        other_measurement = BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=date(2026, 6, 16),
            polyp_count=8,
            ephyrae_count=1,
            user=self.admin,
        )
        root = AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.ENTRY,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement for 2026-06-15",
            metadata={"measurement_id": self.measurement.id},
        )
        correction = AuditLog.objects.create(
            organization=self.organization,
            user=self.second_user,
            action=AuditLog.Action.UPDATE,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement for 2026-06-15",
            metadata={"measurement_id": self.measurement.id},
        )
        unrelated = AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.ENTRY,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement for 2026-06-16",
            metadata={"measurement_id": other_measurement.id},
        )
        legacy = AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.UPDATE,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement for 2026-06-15",
        )
        invalid = AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.UPDATE,
            object_type="box",
            object_id=self.box.global_code,
            description="Invalid structured measurement",
            metadata={"measurement_id": "not-an-integer"},
        )
        transfer = AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.TRANSFER,
            object_type="box",
            object_id=self.box.global_code,
            description="Transfer with unrelated metadata",
            metadata={"measurement_id": self.measurement.id},
        )
        AuditLog.objects.create(
            organization=self.other_organization,
            user=self.admin,
            action=AuditLog.Action.UPDATE,
            object_type="box",
            object_id=self.other_box.global_code,
            description="Foreign measurement event",
            metadata={"measurement_id": self.measurement.id},
        )
        self.client.login(username="org_admin", password="secret")

        with CaptureQueriesContext(connection) as queries:
            entries = self.get_entries()

        entries_by_id = {entry["id"]: entry for entry in entries}
        self.assertEqual(entries_by_id[root.id]["related_action_count"], 1)
        self.assertEqual(entries_by_id[correction.id]["related_action_count"], 1)
        self.assertEqual(entries_by_id[unrelated.id]["related_action_count"], 0)
        self.assertEqual(entries_by_id[legacy.id]["related_action_count"], 0)
        self.assertEqual(entries_by_id[invalid.id]["related_action_count"], 0)
        self.assertEqual(entries_by_id[transfer.id]["related_action_count"], 0)

        audit_table = AuditLog._meta.db_table.lower()
        grouped_count_queries = [
            query["sql"]
            for query in queries.captured_queries
            if audit_table in query["sql"].lower()
            and "measurement_id" in query["sql"].lower()
            and "group by" in query["sql"].lower()
        ]
        self.assertEqual(len(grouped_count_queries), 1)

    def test_linked_route_returns_complete_chronological_scoped_chain(self):
        root = AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.ENTRY,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement for 2026-06-15",
            metadata={"measurement_id": self.measurement.id},
        )
        correction_one = AuditLog.objects.create(
            organization=self.organization,
            user=self.second_user,
            action=AuditLog.Action.UPDATE,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement for 2026-06-15",
            metadata={"measurement_id": self.measurement.id},
        )
        correction_two = AuditLog.objects.create(
            organization=self.organization,
            user=self.third_user,
            action=AuditLog.Action.UPDATE,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement for 2026-06-15",
            metadata={"measurement_id": self.measurement.id},
        )
        other_measurement = BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=date(2026, 6, 16),
            polyp_count=99,
            ephyrae_count=0,
            user=self.admin,
        )
        unrelated = AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.ENTRY,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement for 2026-06-16",
            metadata={"measurement_id": other_measurement.id},
        )
        foreign = AuditLog.objects.create(
            organization=self.other_organization,
            user=self.admin,
            action=AuditLog.Action.UPDATE,
            object_type="box",
            object_id=self.other_box.global_code,
            description="Foreign event with matching metadata",
            metadata={"measurement_id": self.measurement.id},
        )
        self.client.login(username="org_admin", password="secret")

        response = self.client.get(
            f"{reverse('api_account_audit_log_linked', args=[correction_one.id])}?limit=1&offset=2",
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )

        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(
            [entry["id"] for entry in results],
            [root.id, correction_one.id, correction_two.id],
        )
        self.assertTrue(all(entry["related_action_count"] == 2 for entry in results))
        self.assertEqual(
            [entry["user_display"] for entry in results],
            ["org_admin@example.org", "second_user@example.org", "third_user@example.org"],
        )
        self.assertTrue(
            all(entry["editable_measurement"]["id"] == self.measurement.id for entry in results)
        )
        self.assertNotIn(unrelated.id, [entry["id"] for entry in results])
        self.assertNotIn(foreign.id, [entry["id"] for entry in results])

        foreign_response = self.client.get(
            reverse("api_account_audit_log_linked", args=[foreign.id]),
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )
        self.assertEqual(foreign_response.status_code, 404)

    def test_linked_route_rejects_non_measurement_and_missing_measurement_roots(self):
        non_measurement = AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.CREATION,
            object_type="box",
            object_id=self.box.global_code,
            description="Box created",
        )
        missing_measurement = AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.ENTRY,
            object_type="box",
            object_id=self.box.global_code,
            description="Deleted measurement",
            metadata={"measurement_id": 999999},
        )
        self.client.login(username="org_admin", password="secret")

        for root in (non_measurement, missing_measurement):
            with self.subTest(root=root.id):
                response = self.client.get(
                    reverse("api_account_audit_log_linked", args=[root.id]),
                    HTTP_X_ORGANIZATION_ID=str(self.organization.id),
                )
                self.assertEqual(response.status_code, 404)

        self.client.login(username="second_user", password="secret")
        forbidden = self.client.get(
            reverse("api_account_audit_log_linked", args=[missing_measurement.id]),
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_old_rows_expose_current_second_correction_and_remain_immutable(self):
        root = AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.ENTRY,
            object_type="box",
            object_id=self.box.global_code,
            description="Biological measurement for 2026-06-15",
            metadata={"measurement_id": self.measurement.id},
        )
        root_snapshot = {
            "created_at": root.created_at,
            "user_id": root.user_id,
            "metadata": root.metadata,
        }
        self.client.login(username="org_admin", password="secret")
        detail_url = reverse(
            "api_box_measurement_detail",
            args=[self.box.id, self.measurement.id],
        )
        first_response = self.client.patch(
            detail_url,
            data={"polyp_count": 55, "notes": "First correction"},
            content_type="application/json",
        )
        self.assertEqual(first_response.status_code, 200)
        first_correction = AuditLog.objects.get(
            action=AuditLog.Action.UPDATE,
            metadata__measurement_id=self.measurement.id,
        )
        first_snapshot = {
            "created_at": first_correction.created_at,
            "user_id": first_correction.user_id,
            "metadata": first_correction.metadata,
        }

        old_row_response = self.client.get(
            reverse("api_account_audit_log_linked", args=[root.id]),
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )
        old_editable = old_row_response.json()["results"][0]["editable_measurement"]
        second_response = self.client.patch(
            reverse(
                "api_box_measurement_detail",
                args=[old_editable["box_id"], old_editable["id"]],
            ),
            data={"polyp_count": 77, "notes": "Second correction"},
            content_type="application/json",
        )
        self.assertEqual(second_response.status_code, 200)

        root.refresh_from_db()
        first_correction.refresh_from_db()
        self.assertEqual(
            {
                "created_at": root.created_at,
                "user_id": root.user_id,
                "metadata": root.metadata,
            },
            root_snapshot,
        )
        self.assertEqual(
            {
                "created_at": first_correction.created_at,
                "user_id": first_correction.user_id,
                "metadata": first_correction.metadata,
            },
            first_snapshot,
        )

        chain_response = self.client.get(
            reverse("api_account_audit_log_linked", args=[root.id]),
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )
        chain = chain_response.json()["results"]
        self.assertEqual(len(chain), 3)
        self.assertTrue(
            all(entry["editable_measurement"]["polyp_count"] == 77 for entry in chain)
        )
        self.assertTrue(
            all(
                entry["editable_measurement"]["notes"] == "Second correction"
                for entry in chain
            )
        )
