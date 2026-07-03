"""Import the normalized POLYPBASE CSV files (data/tables/) into the database.

Source files (one row per entity / association, matching the validated MCD):
    espece.csv          -> taxonomy.Species
    souche.csv          -> taxonomy.Strain
    zone_thermique.csv  -> cultures.ThermalZone
    boite.csv           -> cultures.Box
    range.csv           -> cultures.BoxLocation   (weekly rows collapsed into stays)
    saisir_releve.csv   -> measurements.BiologicalMeasurement

The command is idempotent: it can be run several times without creating
duplicates. Run it with --dry-run first to preview the counts.
"""

import csv
from collections import defaultdict
from datetime import date, datetime, time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.cultures.models import (
    Box,
    BoxLineage,
    BoxLocation,
    SubcultureEvent,
    ThermalZone,
)
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

DEFAULT_CSV_DIR = Path(settings.BASE_DIR).parent / "data" / "tables"


class Command(BaseCommand):
    help = "Import the normalized CSV files from data/tables/ into the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default=str(DEFAULT_CSV_DIR),
            help="Directory containing the CSV files (default: data/tables/).",
        )
        parser.add_argument(
            "--organization",
            default="Aquarium de Paris",
            help="Owner organization for the imported boxes and zones.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Roll back at the end: import is simulated, nothing is saved.",
        )
        parser.add_argument(
            "--reset-boxes",
            action="store_true",
            help=(
                "Delete all existing boxes (and their lineage, subculture "
                "events, locations, movements, measurements...) before importing, "
                "so boxes are recreated with fresh global codes."
            ),
        )

    def handle(self, *args, **options):
        csv_dir = Path(options["path"])
        if not csv_dir.exists():
            raise CommandError(f"CSV directory not found: {csv_dir}")

        self.dry_run = options["dry_run"]
        self.counts = defaultdict(int)

        try:
            with transaction.atomic():
                if options["reset_boxes"]:
                    self._reset_boxes()
                organization = self._get_organization(options["organization"])
                species = self._import_species(csv_dir)
                strains = self._import_strains(csv_dir, species)
                zones = self._import_zones(csv_dir, organization)
                boxes = self._import_boxes(csv_dir, organization, strains)
                self._import_locations(csv_dir, boxes, zones)
                self._import_measurements(csv_dir, boxes)

                if self.dry_run:
                    self.stdout.write(self.style.WARNING("Dry-run: rolling back."))
                    transaction.set_rollback(True)
        except FileNotFoundError as error:
            raise CommandError(str(error))

        self._report()

    # -- helpers -----------------------------------------------------------

    def _read(self, csv_dir, name):
        path = csv_dir / name
        if not path.exists():
            raise FileNotFoundError(f"Missing CSV file: {path}")
        with path.open(encoding="utf-8-sig", newline="") as handle:
            yield from csv.DictReader(handle)

    def _week_to_date(self, year, week):
        """Return the Monday of the given ISO year/week."""
        try:
            return date.fromisocalendar(int(year), int(week), 1)
        except (TypeError, ValueError):
            try:
                return date.fromisocalendar(int(year), min(int(week), 52), 1)
            except (TypeError, ValueError):
                return None

    def _as_int(self, value):
        value = (value or "").strip()
        if not value:
            return 0
        try:
            return int(round(float(value)))
        except ValueError:
            return 0

    def _aware(self, day):
        return timezone.make_aware(datetime.combine(day, time.min))

    # -- import steps ------------------------------------------------------

    def _get_organization(self, name):
        organization, created = Organization.objects.get_or_create(name=name)
        self.counts["organization_created"] += int(created)
        return organization

    def _reset_boxes(self):
        """Delete every box so they can be recreated with fresh global codes.

        Lineage and subculture events point at boxes with on_delete=PROTECT,
        so they must be removed first. Everything else (locations, movements,
        measurements, transfers, alerts, tags) cascades with the box.
        """
        self.counts["lineages_deleted"] = BoxLineage.objects.all().delete()[0]
        self.counts["subcultures_deleted"] = SubcultureEvent.objects.all().delete()[0]
        self.counts["boxes_deleted"] = Box.objects.all().delete()[0]

    def _import_species(self, csv_dir):
        mapping = {}
        for row in self._read(csv_dir, "espece.csv"):
            species, created = Species.objects.update_or_create(
                scientific_name=row["nom_scientifique"].strip(),
                defaults={"genus_species_code": (row.get("code_espece") or "").strip()},
            )
            mapping[row["id_espece"]] = species
            self.counts["species_created"] += int(created)
            self.counts["species_total"] += 1
        return mapping

    def _import_strains(self, csv_dir, species):
        mapping = {}
        for row in self._read(csv_dir, "souche.csv"):
            related_species = species.get(row["id_espece"])
            if related_species is None:
                self.counts["strains_skipped"] += 1
                continue
            number = self._as_int(row.get("numero_souche_local")) or None
            strain, created = Strain.objects.update_or_create(
                species=related_species,
                code=row["code_souche"].strip(),
                defaults={
                    "number": number,
                    "origin_code": (row.get("code_provenance") or "").strip()[:12],
                },
            )
            mapping[row["id_souche"]] = strain
            self.counts["strains_created"] += int(created)
            self.counts["strains_total"] += 1
        return mapping

    def _import_zones(self, csv_dir, organization):
        mapping = {}
        for row in self._read(csv_dir, "zone_thermique.csv"):
            target = (row.get("temperature_cible") or "").strip() or None
            zone, created = ThermalZone.objects.update_or_create(
                organization=organization,
                name=row["nom_zone"].strip(),
                defaults={"target_temperature_c": target},
            )
            mapping[row["id_zone"]] = zone
            self.counts["zones_created"] += int(created)
            self.counts["zones_total"] += 1
        return mapping

    def _build_base_code(self, row, strain):
        """Compose ORG-ESPECE-SOUCHE.NNN for a box from its strain and row.

        ORG     = strain provenance code (code_provenance, e.g. JKA)
        ESPECE  = species code (code_espece, e.g. ALA)
        SOUCHE  = local strain number (numero_souche_local)
        NNN     = local box number, zero-padded to 3 digits
        """
        org = (strain.origin_code or "").strip()
        species = (strain.species.genus_species_code or "").strip()
        souche = strain.number if strain.number is not None else ""
        nnn = f"{self._as_int(row.get('numero_boite_local')):03d}"
        return f"{org}-{species}-{souche}.{nnn}"

    def _resolve_global_codes(self, rows, strains):
        """Build {id_boite: global_code} as ORG-ESPECE-SOUCHE.NNN.

        Duplicates (should not happen in clean data) are suffixed with the
        box id to keep the code unique.
        """
        by_code = defaultdict(list)
        for row in rows:
            strain = strains.get(row["id_souche"])
            if strain is None:
                continue
            by_code[self._build_base_code(row, strain)].append(row["id_boite"])

        global_codes = {}
        for code, ids in by_code.items():
            if len(ids) == 1:
                global_codes[ids[0]] = code
                continue
            keeper = min(ids, key=lambda value: int(value))
            for box_id in ids:
                global_codes[box_id] = code if box_id == keeper else f"{code}-{box_id}"
        return global_codes

    def _import_boxes(self, csv_dir, organization, strains):
        rows = list(self._read(csv_dir, "boite.csv"))
        global_codes = self._resolve_global_codes(rows, strains)
        mapping = {}
        for row in rows:
            strain = strains.get(row["id_souche"])
            if strain is None:
                self.counts["boxes_skipped"] += 1
                continue
            box, created = Box.objects.update_or_create(
                global_code=global_codes[row["id_boite"]],
                defaults={
                    "organization": organization,
                    "local_code": row["code_local"].strip(),
                    "box_number": (row.get("numero_boite_local") or "").strip(),
                    "strain": strain,
                },
            )
            mapping[row["id_boite"]] = box
            self.counts["boxes_created"] += int(created)
            self.counts["boxes_total"] += 1
        return mapping

    def _import_locations(self, csv_dir, boxes, zones):
        """Collapse consecutive weekly rows in the same zone into stays."""
        rows_by_box = defaultdict(list)
        for row in self._read(csv_dir, "range.csv"):
            day = self._week_to_date(row.get("annee"), row.get("semaine"))
            if day is None:
                continue
            rows_by_box[row["id_boite"]].append((day, row["id_zone"]))

        for box_id, entries in rows_by_box.items():
            box = boxes.get(box_id)
            if box is None:
                continue
            entries.sort(key=lambda item: item[0])

            stays = []  # (zone_csv_id, start_date)
            for day, zone_csv_id in entries:
                if not stays or stays[-1][0] != zone_csv_id:
                    stays.append((zone_csv_id, day))

            last_zone = None
            last_start = None
            for index, (zone_csv_id, start_day) in enumerate(stays):
                zone = zones.get(zone_csv_id)
                if zone is None:
                    continue
                ends_at = self._aware(stays[index + 1][1]) if index + 1 < len(stays) else None
                BoxLocation.objects.update_or_create(
                    box=box,
                    thermal_zone=zone,
                    starts_at=self._aware(start_day),
                    defaults={"ends_at": ends_at},
                )
                self.counts["locations_total"] += 1
                last_zone = zone
                last_start = start_day

            # The most recent stay defines the box current zone and entry date.
            if last_zone is not None:
                Box.objects.filter(pk=box.pk).update(
                    thermal_zone=last_zone, entered_on=last_start
                )

    def _import_measurements(self, csv_dir, boxes):
        for row in self._read(csv_dir, "saisir_releve.csv"):
            box = boxes.get(row["id_boite"])
            if box is None:
                self.counts["measurements_skipped"] += 1
                continue
            measured_on = self._week_to_date(row.get("annee"), row.get("semaine"))
            if measured_on is None:
                self.counts["measurements_skipped"] += 1
                continue
            BiologicalMeasurement.objects.update_or_create(
                box=box,
                measured_on=measured_on,
                defaults={
                    "polyp_count": self._as_int(row.get("nombre_polypes")),
                    "ephyrae_count": self._as_int(row.get("nombre_ephyrules")),
                },
            )
            self.counts["measurements_total"] += 1

    def _report(self):
        self.stdout.write(self.style.SUCCESS("Import summary:"))
        for key in sorted(self.counts):
            self.stdout.write(f"  {key}: {self.counts[key]}")
