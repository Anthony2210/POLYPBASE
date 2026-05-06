from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import OrganizationMembership, UserPreference
from apps.audit.models import Alert, AuditLog
from apps.cultures.models import (
    Box,
    BoxLineage,
    BoxLocation,
    BoxMovement,
    BoxTransfer,
    IdentificationTag,
    SubcultureEvent,
    ThermalZone,
)
from apps.exports.models import DataExport, ExcelImport, ExcelImportRow
from apps.measurements.models import (
    BiologicalMeasurement,
    DailyTemperature,
    Observation,
    Probe,
    SalinityMeasurement,
    TemperatureMeasurement,
)
from apps.organizations.models import Organization, PartnerInstitution, SharingAgreement
from apps.taxonomy.models import Origin, Species, Strain, Taxon


DEMO_PASSWORD = "polypbase-demo"


class Command(BaseCommand):
    help = "Create a small, idempotent demo dataset for local development."

    def handle(self, *args, **options):
        admin_user, lab_user, viewer_user = self._create_users()
        paris, partner = self._create_organizations()
        self._create_memberships(paris, partner, admin_user, lab_user, viewer_user)
        self._create_sharing(paris, partner)
        species = self._create_taxonomy()
        strains = self._create_strains(species)
        zones = self._create_thermal_zones(paris, partner)
        probes = self._create_probes(paris, partner, zones)
        boxes = self._create_boxes(paris, partner, strains, zones)

        self._create_tags(boxes)
        self._create_locations(boxes, zones)
        self._create_measurements(boxes, lab_user)
        self._create_environment_data(zones, probes, lab_user)
        self._create_observations_and_alerts(paris, boxes, lab_user)
        self._create_lineage(boxes, lab_user)
        self._create_transfer(boxes, paris, partner, admin_user)
        self._create_import_export_records(paris, lab_user)
        self._create_audit_logs(paris, boxes, lab_user)

        self.stdout.write(self.style.SUCCESS("Demo data is ready."))
        self.stdout.write("Users:")
        self.stdout.write(f"  demo_admin / {DEMO_PASSWORD}")
        self.stdout.write(f"  demo_lab / {DEMO_PASSWORD}")
        self.stdout.write(f"  demo_viewer / {DEMO_PASSWORD}")

    def _create_users(self):
        user_model = get_user_model()
        users = []
        user_specs = [
            {
                "username": "demo_admin",
                "email": "demo_admin@example.test",
                "first_name": "Demo",
                "last_name": "Admin",
                "is_staff": True,
                "is_superuser": True,
            },
            {
                "username": "demo_lab",
                "email": "demo_lab@example.test",
                "first_name": "Demo",
                "last_name": "Lab",
                "is_staff": False,
                "is_superuser": False,
            },
            {
                "username": "demo_viewer",
                "email": "demo_viewer@example.test",
                "first_name": "Demo",
                "last_name": "Viewer",
                "is_staff": False,
                "is_superuser": False,
            },
        ]

        for spec in user_specs:
            user, _created = user_model.objects.update_or_create(
                username=spec["username"],
                defaults={
                    "email": spec["email"],
                    "first_name": spec["first_name"],
                    "last_name": spec["last_name"],
                    "is_staff": spec["is_staff"],
                    "is_superuser": spec["is_superuser"],
                    "is_active": True,
                },
            )
            user.set_password(DEMO_PASSWORD)
            user.save(update_fields=["password"])
            UserPreference.objects.update_or_create(
                user=user,
                defaults={"interface_language": UserPreference.InterfaceLanguage.FRENCH},
            )
            users.append(user)

        return users

    def _create_organizations(self):
        paris, _created = Organization.objects.update_or_create(
            slug="aquarium-de-paris",
            defaults={
                "name": "Aquarium de Paris",
                "city": "Paris",
                "country": "France",
                "contact_email": "polypbase@example.test",
                "is_active": True,
                "notes": "Main demo organization for local development.",
            },
        )
        partner, _created = Organization.objects.update_or_create(
            slug="partner-aquarium-demo",
            defaults={
                "name": "Partner Aquarium Demo",
                "city": "Lisbon",
                "country": "Portugal",
                "contact_email": "partner@example.test",
                "is_active": True,
                "notes": "Second organization used to test data scoping and sharing.",
            },
        )
        PartnerInstitution.objects.update_or_create(
            name="Mediterranean Research Station",
            defaults={
                "city": "Villefranche-sur-Mer",
                "country": "France",
                "contact_name": "Demo Contact",
                "contact_email": "research@example.test",
                "notes": "Sample partner institution used for origins.",
            },
        )
        return paris, partner

    def _create_memberships(self, paris, partner, admin_user, lab_user, viewer_user):
        membership_specs = [
            (admin_user, paris, OrganizationMembership.Role.ADMIN),
            (lab_user, paris, OrganizationMembership.Role.LAB_TECHNICIAN),
            (viewer_user, paris, OrganizationMembership.Role.VIEWER),
            (viewer_user, partner, OrganizationMembership.Role.VIEWER),
        ]

        for user, organization, role in membership_specs:
            OrganizationMembership.objects.update_or_create(
                user=user,
                organization=organization,
                defaults={
                    "role": role,
                    "is_active": True,
                    "starts_on": date(2026, 5, 1),
                    "ends_on": None,
                },
            )

    def _create_sharing(self, paris, partner):
        SharingAgreement.objects.update_or_create(
            owner_organization=paris,
            partner_organization=partner,
            defaults={
                "status": SharingAgreement.Status.ACTIVE,
                "can_view_inventory": True,
                "can_view_measurements": False,
                "can_export_data": False,
                "starts_on": date(2026, 5, 1),
                "ends_on": None,
                "notes": "Demo sharing rule: inventory only.",
            },
        )

    def _create_taxonomy(self):
        cnidaria, _created = Taxon.objects.update_or_create(
            name="Cnidaria",
            defaults={"rank": "phylum", "parent": None},
        )
        scyphozoa, _created = Taxon.objects.update_or_create(
            name="Scyphozoa",
            defaults={"rank": "class", "parent": cnidaria},
        )
        semaeostomeae, _created = Taxon.objects.update_or_create(
            name="Semaeostomeae",
            defaults={"rank": "order", "parent": scyphozoa},
        )
        rhizostomeae, _created = Taxon.objects.update_or_create(
            name="Rhizostomeae",
            defaults={"rank": "order", "parent": scyphozoa},
        )

        aurelia, _created = Species.objects.update_or_create(
            scientific_name="Aurelia aurita",
            defaults={
                "common_name": "Moon jellyfish",
                "genus_species_code": "AAU",
                "taxon": semaeostomeae,
                "is_described": True,
                "notes": "Common demo species for lab tracking.",
            },
        )
        chrysaora, _created = Species.objects.update_or_create(
            scientific_name="Chrysaora colorata",
            defaults={
                "common_name": "Purple-striped jelly",
                "genus_species_code": "CCO",
                "taxon": semaeostomeae,
                "is_described": True,
                "notes": "Demo species with lower observation counts.",
            },
        )
        cassiopea, _created = Species.objects.update_or_create(
            scientific_name="Cassiopea andromeda",
            defaults={
                "common_name": "Upside-down jellyfish",
                "genus_species_code": "CAN",
                "taxon": rhizostomeae,
                "is_described": True,
                "notes": "Demo species used to test another taxonomic group.",
            },
        )
        return {
            "aurelia": aurelia,
            "chrysaora": chrysaora,
            "cassiopea": cassiopea,
        }

    def _create_strains(self, species):
        field_origin, _created = Origin.objects.get_or_create(
            source_type=Origin.SourceType.FIELD_COLLECTION,
            origin_institution_name="Atlantic coast",
            event_date=date(2022, 6, 15),
            defaults={
                "description": "Demo field collection origin.",
                "latitude": Decimal("48.390000"),
                "longitude": Decimal("-4.490000"),
                "technicians": "Demo team",
            },
        )
        donation_origin, _created = Origin.objects.get_or_create(
            source_type=Origin.SourceType.DONATION,
            origin_institution_name="Mediterranean Research Station",
            event_date=date(2023, 3, 10),
            defaults={
                "description": "Demo donation origin.",
                "technicians": "Demo team",
            },
        )
        reproduction_origin, _created = Origin.objects.get_or_create(
            source_type=Origin.SourceType.REPRODUCTION,
            origin_institution_name="Aquarium de Paris",
            event_date=date(2025, 11, 20),
            defaults={
                "description": "Demo internal reproduction origin.",
                "technicians": "Demo lab",
            },
        )

        strain_specs = {
            "aurelia_atl": (species["aurelia"], "1-ATL", 1, "ATL", field_origin),
            "aurelia_med": (species["aurelia"], "3-MED", 3, "MED", donation_origin),
            "chrysaora_pac": (species["chrysaora"], "2-PAC", 2, "PAC", donation_origin),
            "cassiopea_lab": (species["cassiopea"], "1-LAB", 1, "LAB", reproduction_origin),
        }

        strains = {}
        for key, (strain_species, code, number, origin_code, origin) in strain_specs.items():
            strain, _created = Strain.objects.update_or_create(
                species=strain_species,
                code=code,
                defaults={
                    "number": number,
                    "origin_code": origin_code,
                    "origin": origin,
                    "notes": "Demo strain.",
                },
            )
            strains[key] = strain

        return strains

    def _create_thermal_zones(self, paris, partner):
        zone_specs = [
            ("paris_10", paris, "Cabinet 10 C", ThermalZone.ZoneType.CABINET, Decimal("10.0")),
            ("paris_15", paris, "Cabinet 15 C", ThermalZone.ZoneType.CABINET, Decimal("15.0")),
            ("paris_20", paris, "Cabinet 20 C", ThermalZone.ZoneType.CABINET, Decimal("20.0")),
            ("paris_incubator", paris, "Incubator 25 C", ThermalZone.ZoneType.INCUBATOR, Decimal("25.0")),
            ("partner_15", partner, "Partner Cabinet 15 C", ThermalZone.ZoneType.CABINET, Decimal("15.0")),
        ]

        zones = {}
        for key, organization, name, zone_type, target_temperature in zone_specs:
            zone, _created = ThermalZone.objects.update_or_create(
                organization=organization,
                name=name,
                defaults={
                    "zone_type": zone_type,
                    "target_temperature_c": target_temperature,
                    "is_active": True,
                    "notes": "Demo thermal zone.",
                },
            )
            zones[key] = zone

        return zones

    def _create_probes(self, paris, partner, zones):
        probe_specs = [
            ("paris_10_a", paris, zones["paris_10"], "P10-A", Probe.ProbeType.MANUAL, "Top shelf"),
            ("paris_15_a", paris, zones["paris_15"], "P15-A", Probe.ProbeType.LORAWAN, "Top shelf"),
            ("paris_15_b", paris, zones["paris_15"], "P15-B", Probe.ProbeType.LORAWAN, "Bottom shelf"),
            ("paris_20_a", paris, zones["paris_20"], "P20-A", Probe.ProbeType.MANUAL, "Main shelf"),
            ("partner_15_a", partner, zones["partner_15"], "PT15-A", Probe.ProbeType.MANUAL, "Partner shelf"),
        ]

        probes = {}
        for key, organization, zone, code, probe_type, location in probe_specs:
            probe, _created = Probe.objects.update_or_create(
                organization=organization,
                code=code,
                defaults={
                    "thermal_zone": zone,
                    "probe_type": probe_type,
                    "location": location,
                    "is_active": True,
                    "notes": "Demo probe.",
                },
            )
            probes[key] = probe

        return probes

    def _create_boxes(self, paris, partner, strains, zones):
        box_specs = [
            ("aau_001", paris, "AAU-1.001-ATL", "001", strains["aurelia_atl"], zones["paris_15"], Box.Status.ACTIVE),
            ("aau_002", paris, "AAU-1.002-ATL", "002", strains["aurelia_atl"], zones["paris_15"], Box.Status.ACTIVE),
            ("aau_003", paris, "AAU-1.003-ATL", "003", strains["aurelia_atl"], zones["paris_20"], Box.Status.ACTIVE),
            ("cco_001", paris, "CCO-2.001-PAC", "001", strains["chrysaora_pac"], zones["paris_10"], Box.Status.ACTIVE),
            ("can_001", paris, "CAN-1.001-LAB", "001", strains["cassiopea_lab"], zones["paris_incubator"], Box.Status.STOPPED),
            ("partner_aau", partner, "AAU-3.001-MED", "001", strains["aurelia_med"], zones["partner_15"], Box.Status.ACTIVE),
        ]

        boxes = {}
        for key, organization, global_code, number, strain, zone, status in box_specs:
            box, _created = Box.objects.update_or_create(
                global_code=global_code,
                defaults={
                    "organization": organization,
                    "local_code": global_code.split("-")[-2],
                    "box_number": number,
                    "strain": strain,
                    "origin": strain.origin,
                    "thermal_zone": zone,
                    "status": status,
                    "entered_on": date(2026, 4, 1),
                    "volume_liters": Decimal("0.30"),
                    "stop_reason": "Demo stopped culture." if status == Box.Status.STOPPED else "",
                    "notes": "Demo box used for local development.",
                },
            )
            boxes[key] = box

        return boxes

    def _create_tags(self, boxes):
        for box in boxes.values():
            IdentificationTag.objects.update_or_create(
                code=f"QR-{box.global_code}",
                defaults={
                    "tag_type": IdentificationTag.TagType.QR,
                    "url": f"/boites/{box.id}/",
                    "box": box,
                    "thermal_zone": None,
                    "is_active": True,
                },
            )

    def _create_locations(self, boxes, zones):
        starts_at = self._aware_datetime(date(2026, 4, 1), time(9, 0))
        for box in boxes.values():
            BoxLocation.objects.update_or_create(
                box=box,
                starts_at=starts_at,
                defaults={
                    "thermal_zone": box.thermal_zone,
                    "ends_at": None,
                    "notes": "Initial demo location.",
                },
            )

        BoxMovement.objects.update_or_create(
            box=boxes["aau_003"],
            moved_at=self._aware_datetime(date(2026, 4, 18), time(10, 30)),
            defaults={
                "from_thermal_zone": zones["paris_15"],
                "to_thermal_zone": zones["paris_20"],
                "user": None,
                "notes": "Demo movement after subculture.",
            },
        )

    def _create_measurements(self, boxes, lab_user):
        base_date = date(2026, 4, 6)
        measurement_specs = {
            "aau_001": [(120, 0, 0), (135, 2, 0), (148, 8, 1), (142, 15, 2)],
            "aau_002": [(88, 0, 0), (91, 0, 0), (95, 1, 0), (99, 4, 1)],
            "aau_003": [(30, 0, 0), (48, 0, 0), (64, 3, 0), (72, 6, 1)],
            "cco_001": [(42, 0, 0), (39, 0, 0), (35, 0, 0), (31, 0, 0)],
            "can_001": [(12, 0, 0), (8, 0, 0), (0, 0, 0), (0, 0, 0)],
            "partner_aau": [(60, 0, 0), (66, 1, 0), (70, 2, 0), (75, 5, 1)],
        }

        for box_key, weekly_values in measurement_specs.items():
            for index, (polyp_count, ephyrae_count, strobila_count) in enumerate(weekly_values):
                measured_on = base_date + timedelta(days=index * 7)
                status = BiologicalMeasurement.CultureStatus.GOOD
                needs_attention = False
                notes = "Weekly demo measurement."
                if box_key == "cco_001" and index >= 2:
                    status = BiologicalMeasurement.CultureStatus.MEDIUM
                    needs_attention = True
                    notes = "Polyp count is decreasing in the demo data."
                if box_key == "can_001" and index >= 2:
                    status = BiologicalMeasurement.CultureStatus.DEAD
                    needs_attention = True
                    notes = "Stopped demo culture."

                BiologicalMeasurement.objects.update_or_create(
                    box=boxes[box_key],
                    measured_on=measured_on,
                    defaults={
                        "polyp_count": polyp_count,
                        "ephyrae_count": ephyrae_count,
                        "strobila_count": strobila_count,
                        "culture_status": status,
                        "needs_attention": needs_attention,
                        "notes": notes,
                        "user": lab_user,
                    },
                )

    def _create_environment_data(self, zones, probes, lab_user):
        base_date = date(2026, 4, 20)
        target_by_zone = {
            zones["paris_10"]: Decimal("10.0"),
            zones["paris_15"]: Decimal("15.0"),
            zones["paris_20"]: Decimal("20.0"),
            zones["paris_incubator"]: Decimal("25.0"),
            zones["partner_15"]: Decimal("15.0"),
        }

        for zone, target in target_by_zone.items():
            for offset in range(5):
                current_date = base_date + timedelta(days=offset)
                average = target + Decimal(offset - 2) / Decimal("10")
                DailyTemperature.objects.update_or_create(
                    thermal_zone=zone,
                    date=current_date,
                    defaults={
                        "min_temperature_c": average - Decimal("0.3"),
                        "average_temperature_c": average,
                        "max_temperature_c": average + Decimal("0.4"),
                        "measurement_count": 3,
                    },
                )
                SalinityMeasurement.objects.update_or_create(
                    thermal_zone=zone,
                    measured_on=current_date,
                    defaults={
                        "salinity_psu": Decimal("35.0") + Decimal(offset) / Decimal("10"),
                        "user": lab_user,
                        "notes": "Demo salinity measurement.",
                    },
                )

        for probe in probes.values():
            for offset in range(3):
                measured_at = self._aware_datetime(base_date + timedelta(days=offset), time(9, 15))
                TemperatureMeasurement.objects.update_or_create(
                    probe=probe,
                    measured_at=measured_at,
                    defaults={
                        "temperature_c": probe.thermal_zone.target_temperature_c + Decimal(offset) / Decimal("10"),
                        "source": "demo_seed",
                        "raw_data": {"source": "seed_demo_data"},
                        "user": lab_user,
                    },
                )

    def _create_observations_and_alerts(self, paris, boxes, lab_user):
        Observation.objects.update_or_create(
            box=boxes["aau_001"],
            observed_on=date(2026, 4, 27),
            observation_type=Observation.ObservationType.GENERAL,
            defaults={
                "notes": "Healthy culture, first ephyrae visible.",
                "user": lab_user,
            },
        )
        Observation.objects.update_or_create(
            box=boxes["cco_001"],
            observed_on=date(2026, 4, 27),
            observation_type=Observation.ObservationType.SUBCULTURE_NEEDED,
            defaults={
                "notes": "Polyp density is low. Check next week.",
                "user": lab_user,
            },
        )
        Alert.objects.update_or_create(
            organization=paris,
            box=boxes["cco_001"],
            thermal_zone=None,
            alert_type=Alert.AlertType.BIOLOGICAL,
            message="Polyp count is decreasing",
            defaults={
                "level": Alert.Level.WARNING,
                "resolved_at": None,
                "created_by": lab_user,
                "resolved_by": None,
            },
        )
        Alert.objects.update_or_create(
            organization=paris,
            box=None,
            thermal_zone=boxes["aau_003"].thermal_zone,
            alert_type=Alert.AlertType.TEMPERATURE,
            message="Temperature should be checked",
            defaults={
                "level": Alert.Level.INFO,
                "resolved_at": None,
                "created_by": lab_user,
                "resolved_by": None,
            },
        )

    def _create_lineage(self, boxes, lab_user):
        event, _created = SubcultureEvent.objects.update_or_create(
            parent_box=boxes["aau_001"],
            event_date=date(2026, 4, 18),
            defaults={
                "user": lab_user,
                "reason": "High polyp density",
                "notes": "Demo subculture event creating a child box.",
            },
        )
        BoxLineage.objects.update_or_create(
            parent_box=boxes["aau_001"],
            child_box=boxes["aau_003"],
            defaults={
                "subculture_event": event,
                "relationship_type": BoxLineage.RelationshipType.SUBCULTURE,
                "notes": "Demo lineage link.",
            },
        )

    def _create_transfer(self, boxes, paris, partner, admin_user):
        BoxTransfer.objects.update_or_create(
            box=boxes["partner_aau"],
            from_organization=partner,
            to_organization=paris,
            transfer_date=date(2026, 5, 15),
            defaults={
                "status": BoxTransfer.Status.PLANNED,
                "user": admin_user,
                "notes": "Demo planned transfer used to test transfer screens.",
            },
        )

    def _create_import_export_records(self, paris, lab_user):
        excel_import, _created = ExcelImport.objects.update_or_create(
            organization=paris,
            file_name="Suivi_2026_demo.xlsx",
            defaults={
                "status": ExcelImport.Status.VALIDATED,
                "user": lab_user,
                "notes": "Demo import record. No real Excel file is stored in Git.",
            },
        )
        for row_number in range(1, 4):
            ExcelImportRow.objects.update_or_create(
                excel_import=excel_import,
                row_number=row_number,
                defaults={
                    "raw_data": {"row": row_number, "source": "demo"},
                    "is_valid": True,
                    "errors": [],
                },
            )

        DataExport.objects.update_or_create(
            organization=paris,
            file_name="demo_measurements_export.csv",
            defaults={
                "export_type": DataExport.ExportType.MEASUREMENTS,
                "file_format": DataExport.FileFormat.CSV,
                "filters": {
                    "species": ["Aurelia aurita"],
                    "thermal_zones": ["Cabinet 15 C"],
                    "date_range": ["2026-04-01", "2026-04-30"],
                },
                "user": lab_user,
            },
        )

    def _create_audit_logs(self, paris, boxes, lab_user):
        AuditLog.objects.update_or_create(
            organization=paris,
            user=lab_user,
            action=AuditLog.Action.SCAN,
            object_type="box",
            object_id=boxes["aau_001"].global_code,
            defaults={
                "description": "Demo QR scan from the lab tablet.",
                "metadata": {"screen": "pilotage"},
            },
        )
        AuditLog.objects.update_or_create(
            organization=paris,
            user=lab_user,
            action=AuditLog.Action.ENTRY,
            object_type="box",
            object_id=boxes["aau_002"].global_code,
            defaults={
                "description": "Demo biological measurement entry.",
                "metadata": {"screen": "box_detail"},
            },
        )

    def _aware_datetime(self, current_date, current_time):
        naive_datetime = datetime.combine(current_date, current_time)
        return timezone.make_aware(naive_datetime, timezone.get_current_timezone())
