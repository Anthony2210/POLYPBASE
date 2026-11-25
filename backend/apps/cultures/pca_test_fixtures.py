"""Synthetic recorded PCA history for runtime API and ownership tests."""

from datetime import date, datetime, timezone

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement, MeasurementImportReceipt
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .box_codes import register_box_code
from .models import Box, BoxLocation, ThermalZone

OLD_CODE = "PCA-CHO-1.005"
OFFICIAL_CODE = "PCA-JKA-1.005"
RETIRED_CODE = "MERGED:2141:PCA-JKA-1.005"


class PcaFixtureMixin:
    def _fixture_teardown(self):
        if getattr(settings, "HISTORICAL_TEMPERATURE_PGQA", False):
            from config.historical_temperature_pgqa import cleanup_test_data

            cleanup_test_data()
        else:
            super()._fixture_teardown()

    def setUp(self):
        super().setUp()
        self.org = Organization.objects.create(pk=1, name="Synthetic PCA QA", slug="pca-qa")
        self.other_org = Organization.objects.create(pk=2, name="Other QA", slug="other-pca-qa")
        self.admin = get_user_model().objects.create_user(
            username="pca-admin", email="pca-admin@example.test",
        )
        self.writer = get_user_model().objects.create_user(
            username="pca-writer", email="pca-writer@example.test",
        )
        self.member = OrganizationMembership.objects.create(organization=self.org, user=self.admin, role="admin")
        OrganizationMembership.objects.create(organization=self.org, user=self.writer, role="lab_technician")
        self.species = Species.objects.create(pk=78, scientific_name="Synthetic Phacellophora QA")
        self.cho_strain = Strain.objects.create(pk=140, organization=self.org, species=self.species, code="PCA-CHO-1")
        self.jka_strain = Strain.objects.create(pk=54, organization=self.org, species=self.species, code="PCA-JKA-1")
        self.zone1 = ThermalZone.objects.create(pk=1, organization=self.org, name="Historical zone")
        self.zone2 = ThermalZone.objects.create(pk=2, organization=self.org, name="Current zone")
        self.canonical = Box.objects.create(
            pk=2135, organization=self.org, strain=self.cho_strain, global_code=OLD_CODE,
            local_code="Old CHO label", box_number="005", thermal_zone=self.zone2,
            volume_liters="0.00", entered_on=date(2025, 12, 29), notes="Physical box metadata",
        )
        self.donor = Box.objects.create(
            pk=2141, organization=self.org, strain=self.jka_strain, global_code=OFFICIAL_CODE,
            local_code="PCA-JKA-1.05", box_number="005", status="inactive",
            stop_reason="Historical stop", stop_reason_missing_from_history=True,
            deactivated_on=date(2026, 10, 1), notes="Keep donor metadata",
        )
        self.other_box = Box.objects.create(
            organization=self.other_org, strain=self.cho_strain, global_code="PCA-CHO-1.099", box_number="099",
        )
        def instant(year, month, day):
            return datetime(year, month, day, tzinfo=timezone.utc)
        BoxLocation.objects.bulk_create([
            BoxLocation(pk=5979, box=self.canonical, thermal_zone=self.zone1,
                        starts_at=instant(2025, 12, 29), ends_at=instant(2026, 1, 12), notes="Keep first"),
            BoxLocation(pk=5980, box=self.canonical, thermal_zone=self.zone2,
                        starts_at=instant(2026, 1, 12), notes="Keep current"),
            BoxLocation(pk=5945, box=self.donor, thermal_zone=self.zone2,
                        starts_at=instant(2024, 12, 30), end_date_unknown=True, notes="End unknown"),
        ])
        specs = ([(2141, 2025, week) for week in range(1, 53)]
                 + [(2141, 2026, week) for week in range(1, 41)]
                 + [(2135, 2026, week) for week in range(1, 19)]
                 + [(2135, 2026, 41), (2135, 2026, 42)])
        rows = []
        for index, (box_id, year, week) in enumerate(specs):
            measured_on = date.fromisocalendar(year, week, 3)
            pk = {41: 104426, 42: 104540}[week] if box_id == 2135 and week > 18 else 100000 + index
            actor = self.writer if index % 2 else self.admin
            rows.append(BiologicalMeasurement(
                pk=pk, box_id=box_id, measured_on=measured_on,
                week_start=BiologicalMeasurement.week_start_for(measured_on),
                polyp_count=week % 7, ephyrae_count=week % 3,
                strobila_count=0 if box_id == 2135 else None, salinity_psu="0.00" if index % 2 else None,
                notes="" if index % 2 else None, culture_status="good" if index % 2 else None,
                needs_attention=False if index % 2 else None,
                user=actor, original_observer=actor, observer_status="known",
                imported_by=actor, origin="file_import", import_format="csv",
                polyp_state_sequence=900 + index if index % 3 else None,
            ))
        BiologicalMeasurement.objects.bulk_create(rows)
        Box.objects.filter(pk=2135).update(polyp_state_revision=80)
        Box.objects.filter(pk=2141).update(polyp_state_revision=150)
        self.old_audit = AuditLog.objects.create(
            organization=self.org, user=self.writer, action="entry", object_type="box",
            object_id=OLD_CODE, metadata={"measurement_id": 104426, "original": "keep"},
        )
        MeasurementImportReceipt.objects.create(
            organization=self.org, actor_id=self.writer.pk, source_sha256="a" * 64,
            source_bytes=b"synthetic,zero,0\n", preparation_fingerprint="b" * 64,
            plan_fingerprint="c" * 64, preparation={"synthetic": True},
            accepted_plan={"box_id": 2141}, result={"measurement_ids": [100000]},
        )

    @transaction.atomic
    def record_merged_history(self):
        """Seed the persisted result; do not reimplement the campaign workflow."""
        register_box_code(OLD_CODE, namespace="PCA-CHO-1")
        register_box_code(OFFICIAL_CODE, namespace="PCA-JKA-1")
        donor_rows = BiologicalMeasurement.objects.filter(box=self.donor)
        winners = dict(donor_rows.values_list("week_start", "pk"))
        for row in BiologicalMeasurement.objects.filter(box=self.canonical, pk__lt=104426):
            BiologicalMeasurement.objects.filter(pk=row.pk).update(duplicate_of_id=winners[row.week_start])
        donor_rows.update(box=self.canonical, environment_source_box=self.donor)
        Box.objects.filter(pk=self.donor.pk).update(
            global_code=RETIRED_CODE, previous_global_code=OFFICIAL_CODE,
            merged_into=self.canonical, polyp_state_revision=151,
        )
        Box.objects.filter(pk=self.canonical.pk).update(
            global_code=OFFICIAL_CODE, previous_global_code=OLD_CODE, strain=self.jka_strain,
            local_code=self.donor.local_code, polyp_state_revision=1012,
        )
