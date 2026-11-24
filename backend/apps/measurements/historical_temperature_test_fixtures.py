"""Synthetic persisted history for the storage and correction tests."""

from datetime import date

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.cultures.models import Box
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import (
    BiologicalMeasurement, HistoricalTemperatureEvidence,
    HistoricalTemperatureImportReceipt, HistoricalTemperatureReading,
)


def evidence(**changes):
    if ("polyp_cell" in changes and "temperature_cell" not in changes
            and changes["polyp_cell"].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ").isdecimal()):
        row = int(changes["polyp_cell"].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
        changes = {"temperature_cell": f"C{row}", "temperature_merged_range": f"C{row}:C{row+1}", **changes}
    return {
        "source_file": "synthetic.xlsx", "source_sha256": "a" * 64,
        "sheet": "Synthetic", "temperature_cell": "C3", "temperature_merged_range": "C3:C4",
        "source_value": "15.000", "source_box": "ABC-DEF-1.01",
        "polyp_cell": "F3", "polyp_value": "0", "ephyrae_cell": "F4", "ephyrae_value": "0",
        "week_cell": "F2", "week_value": "1", "source_iso_year": 2026, "source_iso_week": 1,
        **changes,
    }


class HistoricalTemperatureFixture:
    def set_up_biology(self):
        self.organization = Organization.objects.create(name="Synthetic temperature laboratory")
        self.other = Organization.objects.create(name="Foreign synthetic laboratory")
        self.actor = get_user_model().objects.create_user(username="temperature-admin", email="temperature-admin@example.org")
        species = Species.objects.create(scientific_name="Synthetic species", genus_species_code="ABC")
        strain = Strain.objects.create(species=species, code="ABC-DEF-1")
        self.box = Box.objects.create(organization=self.organization, strain=strain, global_code="ABC-DEF-1.001", box_number="001")
        self.biology = BiologicalMeasurement.objects.create(box=self.box, measured_on=date(2026, 1, 1), polyp_count=0, ephyrae_count=0)

    def seed_history(self):
        # PostgreSQL checks that a reading has evidence at transaction commit.
        with transaction.atomic():
            reading = HistoricalTemperatureReading.objects.create(
                biological_measurement=self.biology, iso_year=2026, iso_week=1, temperature_c="0.000",
            )
            for values in (
                evidence(source_value="0"),
                evidence(source_value="0.000", polyp_cell="F7", ephyrae_cell="F8"),
            ):
                HistoricalTemperatureEvidence.objects.create(reading=reading, **values)
            self.receipt = HistoricalTemperatureImportReceipt.objects.create(
                organization=self.organization, actor_id=self.actor.pk,
                plan_fingerprint="a" * 64, snapshot_sha256="c" * 64,
                accepted_plan={}, source_archive=b"synthetic-source", results={},
            )
        return reading
