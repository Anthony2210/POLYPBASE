"""Synthetic populated fixture for the Box 1902 SQL and migration harness."""

from datetime import date, datetime, timedelta, timezone

from django.contrib.auth import get_user_model

from apps.accounts.models import OrganizationMembership
from apps.audit.models import Alert, AuditLog
from apps.measurements.models import BiologicalMeasurement, DailyTemperature, Observation
from apps.organizations.models import Organization
from apps.taxonomy.models import (
    OrganizationSpeciesCode, Species, Strain,
)

from .models import (
    Box, BoxCodeNamespace, BoxLineage, BoxLocation, BoxMovement, BoxTransfer,
    SubcultureEvent, ThermalZone,
)
from .qr import sync_box_qr_tag



OLD_CODE = "CLA-JKA-1.009"
NEW_CODE = "CLC-JKA-1.009"
PRIVATE_NOTE = "Synthetic private historical note: never print this"


class ClaBox1902Fixture:
    @classmethod
    def create_fixture(cls):
        cls.org = Organization.objects.create(pk=1, name="Aquarium de Paris", slug="aquarium-de-paris")
        cls.other_org = Organization.objects.create(pk=2, name="Foreign QA", slug="foreign-1902-qa")
        cls.admin = get_user_model().objects.create_user(
            username="cla-1902-admin", email="cla-1902-admin@example.org",
        )
        OrganizationMembership.objects.create(user=cls.admin, organization=cls.org, role="admin")
        cls.source_species = Species.objects.create(pk=131, scientific_name="Cyanea lamarckii")
        cls.target_species = Species.objects.create(pk=164, scientific_name="Chrysaora lactea")
        cls.source = Strain.objects.create(
            pk=213, organization=None, species=cls.source_species, code="CLA-JKA-1",
            notes=PRIVATE_NOTE,
        )
        cls.target = Strain.objects.create(
            pk=285, organization=cls.org, species=cls.target_species, code="CLC-JKA-1",
            origin_code="JKA", number=1, notes=PRIVATE_NOTE,
        )
        for species, code in ((cls.source_species, "CLA"), (cls.target_species, "CLC")):
            OrganizationSpeciesCode.objects.create(organization=cls.org, species=species, code=code)
        cls.zone = ThermalZone.objects.create(organization=cls.org, name="Historical QA zone")
        cls.previous_zone = ThermalZone.objects.create(organization=cls.org, name="Earlier QA zone")
        cls.box = Box.objects.create(
            pk=1902, organization=cls.org, strain=cls.source, global_code=OLD_CODE,
            local_code="CLA-JKA-1.09", box_number="09", status=Box.Status.INACTIVE,
            entered_on=date(2026, 1, 5), deactivated_on=date(2026, 9, 22),
            stop_reason="Historical stop", notes=PRIVATE_NOTE, volume_liters="0.00",
            polyp_state_revision=41,
        )
        cls.sentinels = []
        for number in range(1, 11):
            if number != 9:
                cls.sentinels.append(Box.objects.create(
                    organization=cls.org, strain=cls.source, global_code=f"CLA-JKA-1.{number:03}",
                    local_code=f"CLA-JKA-1.{number:02}", box_number=f"{number:02}",
                    status=Box.Status.PENDING_REVIEW, notes=PRIVATE_NOTE,
                ))
        for code, scientific_name, numbers in (
            ("SME", "Stomolophus meleagris", (1, 4, 5, 6, 7, 8, 9, 10)),
            ("SSP", "Stomolophus sp.2", (1, 4, 5, 6, 7, 8, 9, 10)),
            ("CCP", "Synthetic CCP species", (1, 2, 3)),
            ("SMA", "Synthetic SMA species", (1, 2, 3)),
        ):
            species = Species.objects.create(scientific_name=scientific_name)
            strain = Strain.objects.create(organization=cls.org, species=species, code=f"{code}-JKA-1")
            for number in numbers:
                cls.sentinels.append(Box.objects.create(
                    organization=cls.org, strain=strain, global_code=f"{code}-JKA-1.{number:03}",
                    box_number=f"{number:02}", status=Box.Status.PENDING_REVIEW, notes=PRIVATE_NOTE,
                ))
        # A shared source is eligible via own Boxes, but is not owned or editable.
        cls.foreign_box = Box.objects.create(
            organization=cls.other_org, strain=cls.source, global_code="CLA-JKA-1.099", box_number="99",
        )
        moved_at = datetime(2026, 9, 21, 10, tzinfo=timezone.utc)
        previous = BoxLocation.objects.create(
            box=cls.box, thermal_zone=cls.previous_zone, starts_at=moved_at - timedelta(days=7),
            ends_at=moved_at, notes=PRIVATE_NOTE,
        )
        current = BoxLocation.objects.create(
            box=cls.box, thermal_zone=cls.zone, starts_at=moved_at,
            ends_at=moved_at + timedelta(days=1), notes=PRIVATE_NOTE,
        )
        BoxMovement.objects.create(
            box=cls.box, from_thermal_zone=cls.previous_zone, to_thermal_zone=cls.zone,
            moved_at=moved_at, user=cls.admin, kind=BoxMovement.Kind.ORDINARY,
            origin_location=previous, destination_location=current, notes=PRIVATE_NOTE,
        )
        for week, strobila, salinity in ((37, 0, "0.00"), (38, None, None)):
            BiologicalMeasurement.objects.create(
                box=cls.box, measured_on=date.fromisocalendar(2026, week, 1),
                polyp_count=0, ephyrae_count=0, strobila_count=strobila, salinity_psu=salinity,
                user=cls.admin, notes=PRIVATE_NOTE, culture_status=None, needs_attention=None,
            )
        Observation.objects.create(box=cls.box, user=cls.admin, notes=PRIVATE_NOTE)
        DailyTemperature.objects.create(
            thermal_zone=cls.zone, date=date(2026, 9, 21), average_temperature_c="0.00",
        )
        Alert.objects.create(
            organization=cls.org, box=cls.box, alert_type="biological", message=PRIVATE_NOTE,
            created_by=cls.admin,
        )
        BoxTransfer.objects.create(
            box=cls.box, from_organization=cls.org, to_organization=cls.other_org,
            polyp_count=0, user=cls.admin, notes=PRIVATE_NOTE,
        )
        event = SubcultureEvent.objects.create(parent_box=cls.box, user=cls.admin, notes=PRIVATE_NOTE)
        BoxLineage.objects.create(parent_box=cls.box, child_box=cls.sentinels[0], subculture_event=event)
        sync_box_qr_tag(cls.box)
        cls.old_audit = AuditLog.objects.create(
            organization=cls.org, user=cls.admin, action="entry", object_type="box",
            object_id=OLD_CODE, metadata={"historical": True, "notes": PRIVATE_NOTE},
        )
