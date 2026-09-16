"""Tests for the business-facing audit API contract."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.cultures.models import Box, ThermalZone
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import AuditLog
from .services import AUDIT_FAMILIES, classify_audit_log, impactful_audit_logs


class BusinessAuditApiTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Paris Lab", slug="paris-lab")
        self.other_organization = Organization.objects.create(name="Partner Lab", slug="partner-lab")
        self.user = get_user_model().objects.create_user(
            username="internal_audit_admin",
            email="audit-admin@example.org",
            first_name="Audit",
            last_name="Admin",
            password="secret",
        )
        OrganizationMembership.objects.create(
            user=self.user,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        OrganizationMembership.objects.create(
            user=self.user,
            organization=self.other_organization,
            role=OrganizationMembership.Role.VIEWER,
        )
        species = Species.objects.create(
            scientific_name="Aurelia aurita",
            genus_species_code="AAU",
        )
        strain = Strain.objects.create(
            species=species,
            code="1-ATL",
            number=1,
            origin_code="ATL",
        )
        self.zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Cabinet 15",
            zone_type=ThermalZone.ZoneType.CABINET,
        )
        self.other_zone = ThermalZone.objects.create(
            organization=self.other_organization,
            name="Partner cabinet",
            zone_type=ThermalZone.ZoneType.CABINET,
        )
        self.box = Box.objects.create(
            organization=self.organization,
            global_code="ATL-AAU-1.001",
            box_number="001",
            strain=strain,
            thermal_zone=self.zone,
        )
        self.other_box = Box.objects.create(
            organization=self.other_organization,
            global_code="ATL-AAU-1.999",
            box_number="999",
            strain=strain,
            thermal_zone=self.other_zone,
        )

    def _log(
        self,
        *,
        action=AuditLog.Action.UPDATE,
        object_type="box",
        object_id=None,
        metadata=None,
        organization=None,
        description="Business action",
    ):
        return AuditLog.objects.create(
            organization=organization or self.organization,
            user=self.user,
            action=action,
            object_type=object_type,
            object_id=self.box.global_code if object_id is None else object_id,
            description=description,
            metadata=metadata or {},
        )

    def _admin(self, query="", organization=None):
        self.client.login(username=self.user.username, password="secret")
        return self.client.get(
            f"{reverse('api_account_audit_log')}{query}",
            HTTP_X_ORGANIZATION_ID=str((organization or self.organization).id),
        )

    def _personal(self, query="", organization=None):
        self.client.login(username=self.user.username, password="secret")
        return self.client.get(
            f"{reverse('api_profile_actions')}{query}",
            HTTP_X_ORGANIZATION_ID=str((organization or self.organization).id),
        )

    def test_all_approved_families_are_classified(self):
        logs = {
            "measurements": self._log(action=AuditLog.Action.ENTRY),
            "transfers": self._log(action=AuditLog.Action.TRANSFER),
            "subcultures": self._log(action=AuditLog.Action.SUBCULTURE),
            "boxes": self._log(action=AuditLog.Action.CREATION),
            "exports": self._log(
                action=AuditLog.Action.EXPORT,
                object_type="measurements",
                object_id="export.csv",
            ),
            "environment": self._log(object_type="thermal_zone", object_id="Cabinet 15"),
            "accounts": self._log(object_type="account", object_id=self.user.username),
            "references": self._log(object_type="species", object_id="1"),
        }

        response = self._admin()

        self.assertEqual(response.status_code, 200)
        families_by_id = {entry["id"]: entry["family"] for entry in response.json()["results"]}
        self.assertEqual(
            {family: families_by_id[log.id] for family, log in logs.items()},
            {family: family for family in AUDIT_FAMILIES},
        )
        self.assertEqual(
            {family: classify_audit_log(log) for family, log in logs.items()},
            {family: family for family in AUDIT_FAMILIES},
        )

    def test_ambiguous_box_updates_use_structured_measurement_discriminator(self):
        measurement = self._log(
            metadata={"measurement_id": 123, "valeurs": {"polypes": 0}},
            description="Biological measurement for 2026-09-16",
        )
        movement = self._log(
            metadata={
                "movement_id": 456,
                "from_thermal_zone_name": "Cabinet 14",
                "to_thermal_zone_name": "Cabinet 15",
            },
            description="Box moved to Cabinet 15",
        )

        response = self._admin()

        entries = {entry["id"]: entry for entry in response.json()["results"]}
        self.assertEqual(entries[measurement.id]["family"], "measurements")
        self.assertEqual(entries[movement.id]["family"], "boxes")

    def test_legacy_measurement_prefix_and_unknown_fallback_are_deterministic(self):
        legacy = self._log(
            description="Biological measurement for 2024-01-02",
        )
        unknown_box = self._log(description="Legacy box action")
        unknown_other = self._log(object_type="legacy_resource", object_id="legacy")

        self.assertEqual(classify_audit_log(legacy), "measurements")
        self.assertEqual(classify_audit_log(unknown_box), "boxes")
        self.assertEqual(classify_audit_log(unknown_other), "references")

    def test_legacy_measurement_dates_have_python_orm_filter_and_count_parity(self):
        expected_families = {
            "Biological measurement for 2026-06-15": "measurements",
            "Biological measurement for 2024-02-29": "measurements",
            "Biological measurement for 2000-02-29": "measurements",
            "Biological measurement for 2026-02-30": "boxes",
            "Biological measurement for 2026-04-31": "boxes",
            "Biological measurement for 2025-02-29": "boxes",
            "Biological measurement for 1900-02-29": "boxes",
            "Biological measurement for 0000-01-01": "boxes",
            "Biological measurement for 2026-00-15": "boxes",
            "Biological measurement for 2026-13-15": "boxes",
            "Biological measurement for 2026-06-00": "boxes",
            "Biological measurement for 2026-01-32": "boxes",
            "Biological measurement for 2026-06-15 corrected": "boxes",
            "Biological measurement for 2026-06-15\n": "boxes",
        }
        logs = {
            description: self._log(description=description)
            for description in expected_families
        }
        structured = self._log(
            description="Biological measurement for 2026-02-30",
            metadata={"measurement_id": 123},
        )
        lifecycle = self._log(
            description="Box deactivated on 2026-06-15",
            metadata={
                "transition": "active->inactive",
                "after": {"stop_reason": "Culture ended"},
            },
        )

        orm_families = {
            log.id: log.business_family
            for log in impactful_audit_logs(organization_id=self.organization.id)
        }
        for description, expected_family in expected_families.items():
            log = logs[description]
            with self.subTest(description=description):
                self.assertEqual(classify_audit_log(log), expected_family)
                self.assertEqual(orm_families[log.id], expected_family)
        self.assertEqual(classify_audit_log(structured), "measurements")
        self.assertEqual(orm_families[structured.id], "measurements")
        self.assertEqual(classify_audit_log(lifecycle), "boxes")
        self.assertEqual(orm_families[lifecycle.id], "boxes")

        measurement_response = self._admin("?family=measurements&include_options=1")
        box_response = self._admin("?family=boxes")

        self.assertEqual(measurement_response.status_code, 200)
        measurement_ids = {
            entry["id"] for entry in measurement_response.json()["results"]
        }
        box_ids = {entry["id"] for entry in box_response.json()["results"]}
        expected_measurement_ids = {
            log.id
            for description, log in logs.items()
            if expected_families[description] == "measurements"
        } | {structured.id}
        expected_box_ids = {
            log.id
            for description, log in logs.items()
            if expected_families[description] == "boxes"
        } | {lifecycle.id}
        self.assertEqual(measurement_ids, expected_measurement_ids)
        self.assertEqual(box_ids, expected_box_ids)

        family_counts = {
            option["key"]: option["count"]
            for option in measurement_response.json()["family_options"]
        }
        self.assertEqual(family_counts["measurements"], len(expected_measurement_ids))
        self.assertEqual(family_counts["boxes"], len(expected_box_ids))

    def test_family_filter_is_applied_before_pagination(self):
        wanted = self._log(action=AuditLog.Action.ENTRY)
        for index in range(3):
            self._log(action=AuditLog.Action.CREATION, description=f"Newer box action {index}")

        response = self._admin("?family=measurements&limit=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([entry["id"] for entry in response.json()["results"]], [wanted.id])
        self.assertFalse(response.json()["has_more"])

    def test_family_filter_combines_with_date_without_changing_date_semantics(self):
        old = self._log(action=AuditLog.Action.ENTRY)
        AuditLog.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=2))
        today = self._log(action=AuditLog.Action.ENTRY)
        self._log(action=AuditLog.Action.CREATION)
        today_value = timezone.localdate().isoformat()

        response = self._admin(f"?family=measurements&date={today_value}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([entry["id"] for entry in response.json()["results"]], [today.id])

    def test_invalid_family_is_explicit(self):
        response = self._admin("?family=not-a-family")

        self.assertEqual(response.status_code, 400)
        self.assertIn("family", response.json())

    def test_family_options_include_all_keys_and_date_scoped_counts(self):
        today_measurement = self._log(action=AuditLog.Action.ENTRY)
        second_measurement = self._log(action=AuditLog.Action.ENTRY)
        old_box = self._log(action=AuditLog.Action.CREATION)
        AuditLog.objects.filter(pk=old_box.pk).update(created_at=timezone.now() - timedelta(days=2))
        foreign = self._log(
            action=AuditLog.Action.ENTRY,
            organization=self.other_organization,
            object_id=self.other_box.global_code,
        )
        response = self._admin(
            f"?include_options=1&date={timezone.localdate().isoformat()}"
        )

        self.assertEqual(response.status_code, 200)
        options = response.json()["family_options"]
        self.assertEqual([option["key"] for option in options], list(AUDIT_FAMILIES))
        counts = {option["key"]: option["count"] for option in options}
        self.assertEqual(counts["measurements"], 2)
        self.assertEqual(counts["boxes"], 0)
        self.assertNotIn(foreign.id, [entry["id"] for entry in response.json()["results"]])
        self.assertEqual(
            {entry["id"] for entry in response.json()["results"]},
            {today_measurement.id, second_measurement.id},
        )

    def test_raw_action_filter_remains_compatible_with_family_filter(self):
        measurement = self._log(action=AuditLog.Action.ENTRY)
        self._log(action=AuditLog.Action.UPDATE, metadata={"measurement_id": 123})

        response = self._admin("?family=measurements&action=entry")

        self.assertEqual([entry["id"] for entry in response.json()["results"]], [measurement.id])

    def test_business_details_normalize_known_metadata_and_preserve_zero(self):
        subculture = self._log(
            action=AuditLog.Action.SUBCULTURE,
            metadata={
                "subculture_event_id": 91,
                "child_box_ids": [92, 93],
                "child_global_codes": ["ATL-AAU-1.002", "ATL-AAU-1.003"],
                "initial_polyp_counts": {
                    "ATL-AAU-1.002": 0,
                    "ATL-AAU-1.003": 12,
                },
            },
        )
        transfer_out = self._log(
            action=AuditLog.Action.TRANSFER,
            metadata={
                "transfer_id": 22,
                "box_id": self.box.id,
                "to_organization": "Destination Lab",
                "date": "2026-09-16",
                "polypes": 0,
                "note": "Handle carefully",
            },
        )
        transfer_import = self._log(
            action=AuditLog.Action.IMPORT,
            metadata={
                "transfer_import_id": 33,
                "created_box_id": self.box.id,
                "source_transfer_id": "external-7",
                "source_global_code": "SRC-AAU-1.001",
                "source_organization": "Source Lab",
            },
        )
        deactivation = self._log(
            metadata={
                "box_id": self.box.id,
                "transition": "active->inactive",
                "before": {"status": "active"},
                "after": {
                    "status": "inactive",
                    "stop_reason": "Culture ended",
                    "stop_reason_missing_from_history": False,
                    "deactivated_on": "2026-09-16",
                },
                "closed_location_ids": [123],
            },
        )
        export = self._log(
            action=AuditLog.Action.EXPORT,
            object_type="measurements",
            object_id="export.csv",
            metadata={
                "box_count": 0,
                "measurement_count": 0,
                "week_count": 0,
                "date_from": "2026-09-01",
                "zones": [99],
            },
        )

        response = self._admin()

        details = {
            entry["id"]: entry["business_details"]
            for entry in response.json()["results"]
        }
        self.assertEqual(
            details[subculture.id],
            {
                "type": "subculture",
                "child_global_codes": ["ATL-AAU-1.002", "ATL-AAU-1.003"],
                "initial_polyp_counts": {
                    "ATL-AAU-1.002": 0,
                    "ATL-AAU-1.003": 12,
                },
            },
        )
        self.assertEqual(
            details[transfer_out.id],
            {
                "type": "transfer_out",
                "destination_organization": "Destination Lab",
                "date": "2026-09-16",
                "polyp_count": 0,
                "note": "Handle carefully",
            },
        )
        self.assertEqual(
            details[transfer_import.id],
            {
                "type": "transfer_import",
                "source_global_code": "SRC-AAU-1.001",
                "source_organization": "Source Lab",
            },
        )
        self.assertEqual(
            details[deactivation.id],
            {
                "type": "box_status",
                "transition": {"from": "active", "to": "inactive"},
                "stop_reason": "Culture ended",
                "stop_reason_missing_from_history": False,
                "deactivated_on": "2026-09-16",
            },
        )
        self.assertNotIn("closed_location_ids", details[deactivation.id])
        self.assertEqual(details[export.id]["box_count"], 0)
        self.assertEqual(details[export.id]["measurement_count"], 0)
        self.assertEqual(details[export.id]["week_count"], 0)
        self.assertEqual(details[export.id]["filters"], {"date_from": "2026-09-01"})

    def test_box_reference_is_safe_for_admin_and_personal_histories(self):
        action = self._log(action=AuditLog.Action.CREATION)

        admin_entry = self._admin().json()["results"][0]
        personal_entry = self._personal().json()["results"][0]
        expected = {
            "id": self.box.id,
            "global_code": self.box.global_code,
            "species_scientific_name": "Aurelia aurita",
        }

        self.assertEqual(admin_entry["id"], action.id)
        self.assertEqual(admin_entry["box_reference"], expected)
        self.assertEqual(personal_entry["box_reference"], expected)

    def test_unresolved_deleted_and_foreign_box_targets_degrade_to_null(self):
        legacy = self._log(object_id="MISSING-BOX")
        foreign = self._log(object_id=self.other_box.global_code)
        disposable = Box.objects.create(
            organization=self.organization,
            global_code="ATL-AAU-1.777",
            box_number="777",
            strain=self.box.strain,
        )
        deleted = self._log(object_id=disposable.global_code)
        disposable.delete()

        response = self._admin()
        personal_response = self._personal()

        entries = {entry["id"]: entry for entry in response.json()["results"]}
        personal_entries = {
            entry["id"]: entry for entry in personal_response.json()["results"]
        }
        for action in (legacy, foreign, deleted):
            self.assertIsNone(entries[action.id]["box_reference"])
            self.assertIsNone(personal_entries[action.id]["box_reference"])

    def test_personal_history_never_exposes_raw_metadata_or_opaque_account_ids(self):
        action = self._log(
            object_type="account",
            object_id=self.user.username,
            metadata={
                "user_id": self.user.id,
                "membership_id": 999,
                "valeurs": {"nom": "Audit ADMIN", "email": self.user.email},
            },
        )

        entry = next(
            item for item in self._personal().json()["results"] if item["id"] == action.id
        )

        self.assertNotIn("metadata", entry)
        self.assertEqual(entry["resource"]["identifier"], "Audit ADMIN")
        self.assertNotIn("internal_", str(entry))

    def test_context_exposes_only_encoded_relationships(self):
        measurement = BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=date(2026, 9, 16),
            polyp_count=0,
            ephyrae_count=0,
            user=self.user,
        )
        measurement_log = self._log(
            action=AuditLog.Action.ENTRY,
            metadata={
                "measurement_id": measurement.id,
                "valeurs": {"date": "2026-09-16", "polypes": 0, "ephyrules": 0},
            },
        )
        subculture = self._log(
            action=AuditLog.Action.SUBCULTURE,
            metadata={
                "child_global_codes": ["ATL-AAU-1.002"],
                "initial_polyp_counts": {"ATL-AAU-1.002": 0},
            },
        )
        transfer = self._log(
            action=AuditLog.Action.TRANSFER,
            metadata={"to_organization": "Destination Lab"},
        )
        movement = self._log(
            metadata={
                "movement_id": 10,
                "from_thermal_zone_name": "Cabinet 14",
                "to_thermal_zone_name": "Cabinet 15",
            },
        )
        deactivation = self._log(
            metadata={
                "transition": "active->inactive",
                "after": {"stop_reason": "End"},
            },
        )

        entries = {entry["id"]: entry for entry in self._admin().json()["results"]}
        personal_entries = {
            entry["id"]: entry for entry in self._personal().json()["results"]
        }

        self.assertEqual(entries[measurement_log.id]["context"], {"measurement": {"id": measurement.id}})
        self.assertEqual(
            personal_entries[measurement_log.id]["context"],
            {"measurement": {"id": measurement.id}},
        )
        self.assertNotIn("measurement_id", personal_entries[measurement_log.id])
        self.assertEqual(
            entries[subculture.id]["context"]["subculture"],
            {
                "parent_global_code": self.box.global_code,
                "children": [
                    {"global_code": "ATL-AAU-1.002", "initial_polyp_count": 0}
                ],
            },
        )
        self.assertEqual(
            entries[transfer.id]["context"]["transfer"],
            {
                "source_organization": self.organization.name,
                "destination_organization": "Destination Lab",
                "source_global_code": self.box.global_code,
            },
        )
        self.assertEqual(
            entries[movement.id]["context"]["movement"],
            {"from_zone": "Cabinet 14", "to_zone": "Cabinet 15"},
        )
        self.assertEqual(entries[deactivation.id]["context"], {})

    def test_foreign_measurement_identity_is_not_exposed(self):
        foreign_measurement = BiologicalMeasurement.objects.create(
            box=self.other_box,
            measured_on=date(2026, 9, 16),
            polyp_count=88,
            ephyrae_count=7,
            user=self.user,
        )
        action = self._log(
            action=AuditLog.Action.ENTRY,
            metadata={"measurement_id": foreign_measurement.id},
        )

        admin_entry = next(
            entry for entry in self._admin().json()["results"] if entry["id"] == action.id
        )
        personal_entry = next(
            entry for entry in self._personal().json()["results"] if entry["id"] == action.id
        )

        self.assertEqual(admin_entry["context"], {})
        self.assertIsNone(admin_entry["editable_measurement"])
        self.assertEqual(personal_entry["context"], {})
        self.assertNotIn("measurement_id", str(personal_entry))
