import json
from collections import defaultdict
from datetime import date, timedelta
from importlib import import_module
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .management.commands.import_bdd_csv import Command as HistoricalImportCommand
from .management.commands.initialize_box_inventory import _selection_hash
from .models import Box, BoxInventoryInitialization, BoxLocation, ThermalZone


class BoxLifecycleTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(username="lifecycle_admin", password="secret")
        self.technician = user_model.objects.create_user(username="lifecycle_tech", password="secret")
        self.organization = Organization.objects.create(name="Lifecycle laboratory")
        self.other_organization = Organization.objects.create(name="Other laboratory")
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        OrganizationMembership.objects.create(
            user=self.technician,
            organization=self.organization,
            role=OrganizationMembership.Role.LAB_TECHNICIAN,
        )
        self.species = Species.objects.create(
            scientific_name="Aurelia lifecycle",
            genus_species_code="ALC",
        )
        self.strain = Strain.objects.create(
            species=self.species,
            code="ALC-LAB-1",
            number=1,
            origin_code="LAB",
        )
        self.zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Zone 15.0°C",
            target_temperature_c=15,
        )
        self.second_zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Zone 20.0°C",
            target_temperature_c=20,
        )
        self.other_zone = ThermalZone.objects.create(
            organization=self.other_organization,
            name="Foreign zone",
            target_temperature_c=10,
        )

    def create_box(self, *, code="ALC-LAB-1.001", status=Box.Status.ACTIVE, zone=True):
        return Box.objects.create(
            organization=self.organization,
            global_code=code,
            box_number=code.rsplit(".", 1)[-1],
            strain=self.strain,
            status=status,
            thermal_zone=self.zone if zone else None,
        )

    def test_model_exposes_only_the_three_lifecycle_statuses(self):
        self.assertEqual(
            [value for value, _ in Box.Status.choices],
            [Box.Status.PENDING_REVIEW, Box.Status.ACTIVE, Box.Status.INACTIVE],
        )

    def test_normal_creation_requires_a_location_and_creates_an_active_box(self):
        self.client.login(username=self.technician.username, password="secret")
        payload = {
            "strain": self.strain.id,
            "global_code": "ALC-LAB-1.002",
            "box_number": "002",
            "entered_on": "2026-08-27",
        }

        missing_location = self.client.post(
            reverse("api_box_list"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(missing_location.status_code, 400)

        payload["thermal_zone"] = self.zone.id
        created = self.client.post(
            reverse("api_box_list"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        box = Box.objects.get(global_code=payload["global_code"])
        self.assertEqual(box.status, Box.Status.ACTIVE)
        self.assertEqual(box.thermal_zone, self.zone)
        self.assertTrue(
            BoxLocation.objects.filter(
                box=box,
                thermal_zone=self.zone,
                ends_at__isnull=True,
                end_date_unknown=False,
            ).exists()
        )

    def test_admin_can_qualify_pending_box_as_active_without_a_location(self):
        box = self.create_box(status=Box.Status.PENDING_REVIEW, zone=False)
        self.client.login(username=self.admin.username, password="secret")

        response = self.client.post(
            reverse("api_box_qualify", args=[box.id]),
            data={"target_status": Box.Status.ACTIVE},
        )

        self.assertEqual(response.status_code, 200)
        box.refresh_from_db()
        self.assertEqual(box.status, Box.Status.ACTIVE)
        self.assertIsNone(box.thermal_zone)

    def test_pending_box_can_be_qualified_inactive_with_unknown_historical_end(self):
        box = self.create_box(status=Box.Status.PENDING_REVIEW)
        location = BoxLocation.objects.create(
            box=box,
            thermal_zone=self.zone,
            starts_at=timezone.now() - timedelta(days=200),
        )
        self.client.login(username=self.admin.username, password="secret")

        response = self.client.post(
            reverse("api_box_qualify", args=[box.id]),
            data={
                "target_status": Box.Status.INACTIVE,
                "reason_missing_from_history": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        box.refresh_from_db()
        location.refresh_from_db()
        self.assertEqual(box.status, Box.Status.INACTIVE)
        self.assertIsNone(box.thermal_zone)
        self.assertEqual(box.stop_reason, "")
        self.assertTrue(box.stop_reason_missing_from_history)
        self.assertIsNone(box.deactivated_on)
        self.assertIsNone(location.ends_at)
        self.assertTrue(location.end_date_unknown)

    def test_technician_cannot_qualify_a_pending_box(self):
        box = self.create_box(status=Box.Status.PENDING_REVIEW, zone=False)
        self.client.login(username=self.technician.username, password="secret")

        response = self.client.post(
            reverse("api_box_qualify", args=[box.id]),
            data={"target_status": Box.Status.ACTIVE},
        )

        self.assertEqual(response.status_code, 403)
        box.refresh_from_db()
        self.assertEqual(box.status, Box.Status.PENDING_REVIEW)

    def test_admin_cannot_qualify_a_box_from_another_organization(self):
        foreign_box = Box.objects.create(
            organization=self.other_organization,
            global_code="ALC-LAB-1.099",
            box_number="099",
            strain=self.strain,
            status=Box.Status.PENDING_REVIEW,
        )
        self.client.login(username=self.admin.username, password="secret")

        response = self.client.post(
            reverse("api_box_qualify", args=[foreign_box.id]),
            data={"target_status": Box.Status.ACTIVE},
        )

        self.assertEqual(response.status_code, 404)
        foreign_box.refresh_from_db()
        self.assertEqual(foreign_box.status, Box.Status.PENDING_REVIEW)

    def test_active_deactivation_requires_a_reason_and_closes_current_location(self):
        box = self.create_box()
        historical_location = BoxLocation.objects.create(
            box=box,
            thermal_zone=self.second_zone,
            starts_at=timezone.now() - timedelta(days=20),
            ends_at=timezone.now() - timedelta(days=10),
        )
        current_location = BoxLocation.objects.create(
            box=box,
            thermal_zone=self.zone,
            starts_at=timezone.now() - timedelta(days=10),
        )
        self.client.login(username=self.admin.username, password="secret")

        missing_reason = self.client.post(reverse("api_box_archive", args=[box.id]), data={})
        self.assertEqual(missing_reason.status_code, 400)
        box.refresh_from_db()
        self.assertEqual(box.status, Box.Status.ACTIVE)

        response = self.client.post(
            reverse("api_box_archive", args=[box.id]),
            data={"reason": "Culture volontairement arrêtée."},
        )
        self.assertEqual(response.status_code, 200)
        box.refresh_from_db()
        current_location.refresh_from_db()
        historical_location.refresh_from_db()
        self.assertEqual(box.status, Box.Status.INACTIVE)
        self.assertIsNone(box.thermal_zone)
        self.assertEqual(box.deactivated_on, timezone.localdate())
        self.assertIsNotNone(current_location.ends_at)
        self.assertFalse(current_location.end_date_unknown)
        self.assertIsNotNone(historical_location.ends_at)

    def test_reactivation_requires_a_new_location_in_the_same_organization(self):
        box = self.create_box(status=Box.Status.INACTIVE, zone=False)
        self.client.login(username=self.admin.username, password="secret")

        missing_location = self.client.post(reverse("api_box_activate", args=[box.id]), data={})
        self.assertEqual(missing_location.status_code, 400)
        foreign_location = self.client.post(
            reverse("api_box_activate", args=[box.id]),
            data={"thermal_zone_id": self.other_zone.id},
        )
        self.assertEqual(foreign_location.status_code, 400)

        response = self.client.post(
            reverse("api_box_activate", args=[box.id]),
            data={"thermal_zone_id": self.second_zone.id, "notes": "Nouvelle affectation."},
        )
        self.assertEqual(response.status_code, 200)
        box.refresh_from_db()
        self.assertEqual(box.status, Box.Status.ACTIVE)
        self.assertEqual(box.thermal_zone, self.second_zone)
        self.assertTrue(
            BoxLocation.objects.filter(
                box=box,
                thermal_zone=self.second_zone,
                ends_at__isnull=True,
                end_date_unknown=False,
            ).exists()
        )

    def test_inactive_box_rejects_new_measurement_but_allows_zero_correction(self):
        box = self.create_box(status=Box.Status.INACTIVE, zone=False)
        measurement = BiologicalMeasurement.objects.create(
            box=box,
            measured_on=date(2026, 8, 20),
            polyp_count=12,
            ephyrae_count=4,
            user=self.technician,
        )
        self.client.login(username=self.technician.username, password="secret")

        new_measurement = self.client.post(
            reverse("api_box_measurements", args=[box.id]),
            data={
                "measured_on": "2026-08-27",
                "polyp_count": 0,
                "ephyrae_count": 0,
            },
        )
        self.assertEqual(new_measurement.status_code, 400)

        correction = self.client.patch(
            reverse("api_box_measurement_detail", args=[box.id, measurement.id]),
            data=json.dumps({"polyp_count": 0, "ephyrae_count": 0}),
            content_type="application/json",
        )
        self.assertEqual(correction.status_code, 200)
        measurement.refresh_from_db()
        self.assertEqual(measurement.polyp_count, 0)
        self.assertEqual(measurement.ephyrae_count, 0)

    def test_overview_excludes_inactive_boxes_but_keeps_pending_review_boxes(self):
        pending = self.create_box(code="ALC-LAB-1.010", status=Box.Status.PENDING_REVIEW)
        inactive = self.create_box(code="ALC-LAB-1.011", status=Box.Status.INACTIVE)
        for box in (pending, inactive):
            BiologicalMeasurement.objects.create(
                box=box,
                measured_on=date(2026, 8, 20),
                polyp_count=0,
                ephyrae_count=0,
            )
        self.client.login(username=self.technician.username, password="secret")

        response = self.client.get(reverse("api_overview_active_boxes"))

        self.assertEqual(response.status_code, 200)
        codes = {item["global_code"] for item in response.json()["results"]}
        self.assertIn(pending.global_code, codes)
        self.assertNotIn(inactive.global_code, codes)


class HistoricalInventoryInitializationTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Historical laboratory")
        self.other_organization = Organization.objects.create(name="Untouched laboratory")
        species = Species.objects.create(
            scientific_name="Aurelia historical",
            genus_species_code="AHI",
        )
        self.strain = Strain.objects.create(species=species, code="AHI-LAB-1")
        self.box = Box.objects.create(
            organization=self.organization,
            global_code="AHI-LAB-1.001",
            box_number="001",
            strain=self.strain,
            status=Box.Status.ACTIVE,
        )
        self.other_box = Box.objects.create(
            organization=self.other_organization,
            global_code="AHI-LAB-1.002",
            box_number="002",
            strain=self.strain,
            status=Box.Status.ACTIVE,
        )

    def test_status_report_is_explicit_and_scoped_to_one_organization(self):
        output = StringIO()
        call_command(
            "report_box_statuses",
            organization_id=self.organization.id,
            stdout=output,
        )

        report = output.getvalue()
        self.assertIn("Boxes found: 1", report)
        self.assertIn(self.box.global_code, report)
        self.assertNotIn(self.other_box.global_code, report)
        self.assertIn("archived: 0", report)
        self.assertIn("lost: 0", report)
        self.assertIn("stopped: 0", report)

    def test_initialization_is_dry_run_scoped_audited_and_idempotent(self):
        dry_run_output = StringIO()
        call_command(
            "initialize_box_inventory",
            organization_id=self.organization.id,
            stdout=dry_run_output,
        )
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.ACTIVE)
        self.assertIn("Boxes found: 1", dry_run_output.getvalue())
        self.assertFalse(BoxInventoryInitialization.objects.exists())

        with self.assertRaises(CommandError):
            call_command(
                "initialize_box_inventory",
                organization_id=self.organization.id,
                apply=True,
                expected_count=1,
                expected_hash="selection-changed",
                stdout=StringIO(),
            )
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.ACTIVE)

        call_command(
            "initialize_box_inventory",
            organization_id=self.organization.id,
            apply=True,
            expected_count=1,
            expected_hash=_selection_hash(
                [(self.box.id, self.box.global_code, Box.Status.ACTIVE)]
            ),
            stdout=StringIO(),
        )
        self.box.refresh_from_db()
        self.other_box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.PENDING_REVIEW)
        self.assertEqual(self.other_box.status, Box.Status.ACTIVE)
        marker = BoxInventoryInitialization.objects.get(organization=self.organization)
        self.assertEqual(marker.box_count, 1)
        self.assertEqual(marker.previous_status_counts, {Box.Status.ACTIVE: 1})
        self.assertTrue(
            AuditLog.objects.filter(
                organization=self.organization,
                object_type="box_inventory_initialization",
            ).exists()
        )

        self.box.status = Box.Status.ACTIVE
        self.box.save(update_fields=["status"])
        second_output = StringIO()
        call_command(
            "initialize_box_inventory",
            organization_id=self.organization.id,
            apply=True,
            expected_count=1,
            expected_hash="ignored-after-durable-marker",
            stdout=second_output,
        )
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.ACTIVE)
        self.assertIn("already initialized", second_output.getvalue())

    def test_historical_import_creates_pending_boxes_without_resetting_qualified_status(self):
        command = HistoricalImportCommand()
        command.counts = defaultdict(int)
        with TemporaryDirectory() as directory:
            csv_path = Path(directory) / "boite.csv"
            csv_path.write_text(
                "id_boite,id_souche,code_local,numero_boite_local\n"
                "42,1,legacy,003\n",
                encoding="utf-8",
            )
            boxes = command._import_boxes(
                Path(directory),
                self.organization,
                {"1": self.strain},
            )
            imported = boxes["42"]
            self.assertEqual(imported.status, Box.Status.PENDING_REVIEW)
            self.assertIsNone(imported.thermal_zone)

            zone = ThermalZone.objects.create(
                organization=self.organization,
                name="Imported historical zone",
            )
            (Path(directory) / "range.csv").write_text(
                "id_boite,annee,semaine,id_zone\n"
                "42,2024,10,7\n",
                encoding="utf-8",
            )
            command._import_locations(Path(directory), boxes, {"7": zone})
            imported.refresh_from_db()
            self.assertEqual(imported.thermal_zone, zone)

            imported.status = Box.Status.INACTIVE
            imported.thermal_zone = None
            imported.save(update_fields=["status", "thermal_zone"])
            BoxLocation.objects.filter(box=imported, ends_at__isnull=True).update(
                end_date_unknown=True
            )
            command._import_boxes(Path(directory), self.organization, {"1": self.strain})
            command._import_locations(Path(directory), boxes, {"7": zone})
            imported.refresh_from_db()
            self.assertEqual(imported.status, Box.Status.INACTIVE)
            self.assertIsNone(imported.thermal_zone)
            self.assertFalse(
                BoxLocation.objects.filter(
                    box=imported,
                    ends_at__isnull=True,
                    end_date_unknown=False,
                ).exists()
            )

            (Path(directory) / "saisir_releve.csv").write_text(
                "id_boite,annee,semaine,nombre_polypes,nombre_ephyrules\n"
                "42,2026,34,0,0\n",
                encoding="utf-8",
            )
            command._import_measurements(Path(directory), boxes)
            self.assertFalse(BiologicalMeasurement.objects.filter(box=imported).exists())

    def test_historical_import_refuses_a_code_owned_by_another_organization(self):
        command = HistoricalImportCommand()
        command.counts = defaultdict(int)
        with TemporaryDirectory() as directory:
            (Path(directory) / "boite.csv").write_text(
                "id_boite,id_souche,code_local,numero_boite_local\n"
                "42,1,foreign-collision,002\n",
                encoding="utf-8",
            )

            with self.assertRaises(CommandError):
                command._import_boxes(
                    Path(directory),
                    self.organization,
                    {"1": self.strain},
                )

        self.other_box.refresh_from_db()
        self.assertEqual(self.other_box.organization, self.other_organization)

    def test_data_migration_converts_every_legacy_status_to_inactive(self):
        legacy_boxes = []
        for index, legacy_status in enumerate(("archived", "lost", "stopped"), start=10):
            legacy_boxes.append(
                Box.objects.create(
                    organization=self.organization,
                    global_code=f"AHI-LAB-1.{index:03d}",
                    box_number=f"{index:03d}",
                    strain=self.strain,
                    status=legacy_status,
                    stop_reason=f"Preserved reason {legacy_status}",
                )
            )

        migration = import_module(
            "apps.cultures.migrations.0007_box_inventory_lifecycle"
        )
        migration.convert_legacy_box_statuses(django_apps, None)

        for box in legacy_boxes:
            box.refresh_from_db()
            self.assertEqual(box.status, Box.Status.INACTIVE)
            self.assertTrue(box.stop_reason.startswith("Preserved reason"))
