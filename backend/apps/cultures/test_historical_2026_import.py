"""Dry-run, apply, rollback and idempotence of the reviewed 2026 importer."""

from datetime import date
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .historical_2026 import deactivation, importer, manifest as manifest_module
from .historical_2026.decisions import EXPECTED_WORKBOOK_SHA256, ISO_YEAR
from .models import Box, BoxLineage, IdentificationTag, ThermalZone

FINGERPRINT_FIELDS = {"fingerprint"}


def box_spec(code, *, strain_code=None, species="Aurelia test", local=None, number="01"):
    strain_code = strain_code or code.rsplit(".", 1)[0]
    return {
        "code": code,
        "local_code": local or f"{strain_code}.{number}",
        "box_number": number,
        "strain_code": strain_code,
        "strain_number": int(strain_code.rsplit("-", 1)[1]),
        "origin_code": strain_code.split("-")[1],
        "species_aaa": strain_code.split("-")[0],
        "species_label_source": species,
        "species_name": species,
        "sheet": "Test",
        "first_source_row": 3,
        "source_identifier": f"{strain_code}.{number}",
        "identifier_correction": None,
    }


def item(box, week, polyps, ephyrae, *, operation="CREATE_OR_MATCH", expected=None, corrections=(), legacy=None):
    return {
        "sheet": "Test",
        "source_box": box,
        "box": box,
        "polyp_cell": f"F{week}",
        "polyp_value": polyps,
        "ephyrae_cell": f"F{week + 1}",
        "ephyrae_value": ephyrae,
        "block_temperature_c": 15,
        "year": ISO_YEAR,
        "iso_week": week,
        "measured_on": date.fromisocalendar(ISO_YEAR, week, 1).isoformat(),
        "polyps": polyps,
        "ephyrae": ephyrae,
        "strobila": None,
        "operation": operation,
        "expected_current": expected,
        "legacy_reading": legacy,
        "corrections": list(corrections),
    }


def make_manifest(boxes, measurements, *, identity=(), exceptions=None, excluded=()):
    return manifest_module.with_fingerprint(
        {
            "schema": "polypbase.historical_import",
            "schema_version": 1,
            "source": {"filename": "x.xlsx", "sha256": EXPECTED_WORKBOOK_SHA256},
            "target_organization": {"name": "Lab A"},
            "conventions": {"iso_year": ISO_YEAR},
            "decisions": {
                "identity_corrections": list(identity),
                "measurement_corrections": [],
                "identifier_aliases": {},
                "approved_operational_taxa": [],
                "strain_label_exceptions": exceptions or {},
            },
            "counts": {},
            "skipped_sheets": [],
            "boxes": list(boxes),
            "measurements": list(measurements),
            "excluded": list(excluded),
        }
    )


class ImporterBase(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Lab A")
        self.other = Organization.objects.create(name="Lab B")
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(username="admin-a", email="a@example.org")
        OrganizationMembership.objects.create(user=self.admin, organization=self.org, role="admin")
        self.tech = user_model.objects.create_user(username="tech-a", email="t@example.org")
        OrganizationMembership.objects.create(user=self.tech, organization=self.org, role="lab_technician")
        self.foreign_admin = user_model.objects.create_user(username="admin-b", email="b@example.org")
        OrganizationMembership.objects.create(user=self.foreign_admin, organization=self.other, role="admin")
        self.species = Species.objects.create(scientific_name="Aurelia test", genus_species_code="AAA")
        self.strain = Strain.objects.create(species=self.species, organization=self.org, code="AAA-BBB-1")
        self.box = self.make_box("AAA-BBB-1.001")

    def make_box(self, code, *, status=Box.Status.ACTIVE, org=None, strain=None, local=None, number="01"):
        strain = strain or self.strain
        return Box.objects.create(
            organization=org or self.org,
            strain=strain,
            global_code=code,
            local_code=local or code.replace(".00", ".0"),
            box_number=number,
            status=status,
        )

    def measurement(self, box, week, polyps, ephyrae, **extra):
        return BiologicalMeasurement.objects.create(
            box=box,
            measured_on=extra.pop("measured_on", date.fromisocalendar(ISO_YEAR, week, 1)),
            polyp_count=polyps,
            ephyrae_count=ephyrae,
            **extra,
        )

    def standard(self, *measurements, boxes=None, **kwargs):
        return make_manifest(boxes or [box_spec("AAA-BBB-1.001")], list(measurements), **kwargs)

    def plan(self, manifest):
        return importer.build_plan(manifest, self.org)

    def apply(self, manifest, *, actor=None, plan_hash=None):
        plan_hash = plan_hash or self.plan(manifest).plan_hash
        return importer.apply_import(manifest, self.org, actor=actor or self.admin, expected_plan_hash=plan_hash)

    def counts(self):
        return (
            Box.objects.count(),
            BiologicalMeasurement.objects.count(),
            AuditLog.objects.count(),
            Strain.objects.count(),
            Species.objects.count(),
        )

    def classes(self, plan):
        return {(row["item"]["box"], row["item"]["iso_week"]): row["classification"] for row in plan.rows}


class ClassificationTests(ImporterBase):
    def test_default_dry_run_performs_no_mutation(self):
        manifest = self.standard(item("AAA-BBB-1.001", 1, 5, 0))
        before = self.counts()
        self.plan(manifest)
        self.assertEqual(self.counts(), before)

    def test_command_defaults_to_dry_run_and_apply_needs_the_reviewed_hash(self):
        manifest = self.standard(item("AAA-BBB-1.001", 1, 5, 0))
        manifest_module.write_manifest(manifest, self.tmp_manifest())
        out = StringIO()
        before = self.counts()
        call_command("import_historical_2026", organization_id=self.org.pk, manifest=str(self.manifest_path), stdout=out)
        self.assertIn("Dry-run only", out.getvalue())
        self.assertIn("CREATE: 1", out.getvalue())
        self.assertEqual(self.counts(), before)
        with self.assertRaisesMessage(CommandError, "--expected-plan-hash"):
            call_command("import_historical_2026", organization_id=self.org.pk, manifest=str(self.manifest_path), apply=True)
        with self.assertRaisesMessage(CommandError, "differs from the expected"):
            call_command(
                "import_historical_2026", organization_id=self.org.pk, manifest=str(self.manifest_path),
                expected_fingerprint="0" * 64, stdout=StringIO(),
            )
        self.assertEqual(self.counts(), before)

    def tmp_manifest(self):
        import tempfile
        from pathlib import Path

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.manifest_path = Path(directory.name) / "manifest.json"
        return self.manifest_path

    def test_command_rejects_a_manifest_for_another_organization(self):
        manifest_module.write_manifest(self.standard(), self.tmp_manifest())
        with self.assertRaisesMessage(CommandError, "Manifest targets 'Lab A'"):
            call_command("import_historical_2026", organization_id=self.other.pk, manifest=str(self.manifest_path))

    def test_classifications(self):
        self.measurement(self.box, 1, 5, 0)  # exact
        self.measurement(self.box, 2, 7, 7)  # occupied by different counts
        self.measurement(self.box, 4, 0, 0, measured_on=date.fromisocalendar(ISO_YEAR, 4, 3))  # same slot, later day
        manifest = self.standard(
            item("AAA-BBB-1.001", 1, 5, 0),
            item("AAA-BBB-1.001", 2, 1, 1),
            item("AAA-BBB-1.001", 3, 2, 2),
            item("AAA-BBB-1.001", 4, 0, 0),
            excluded=[{"reason": "RECAP_2025_COLUMN_E"}],
        )
        plan = self.plan(manifest)
        self.assertEqual(
            self.classes(plan),
            {
                ("AAA-BBB-1.001", 1): importer.EXACT_ALREADY_PRESENT,
                ("AAA-BBB-1.001", 2): importer.CONFLICT,
                ("AAA-BBB-1.001", 3): importer.CREATE,
                ("AAA-BBB-1.001", 4): importer.EXACT_ALREADY_PRESENT,
            },
        )
        self.assertEqual(plan.counts[importer.EXCLUDED_SOURCE], 1)
        self.assertEqual(plan.blockers[0]["iso_week"], 2)

    def test_conflict_blocks_apply_without_any_write(self):
        self.measurement(self.box, 2, 7, 7)
        manifest = self.standard(item("AAA-BBB-1.001", 2, 1, 1), item("AAA-BBB-1.001", 3, 2, 2))
        before = self.counts()
        with self.assertRaises(importer.HistoricalImportBlocked):
            self.apply(manifest, plan_hash="anything")
        self.assertEqual(self.counts(), before)
        self.assertEqual(BiologicalMeasurement.objects.get(box=self.box, polyp_count=7).ephyrae_count, 7)

    def test_cross_organization_box_is_an_identity_mismatch(self):
        foreign_strain = Strain.objects.create(species=self.species, organization=self.other, code="ZZZ-BBB-1")
        self.make_box("ZZZ-BBB-1.001", org=self.other, strain=foreign_strain)
        manifest = make_manifest([box_spec("ZZZ-BBB-1.001")], [item("ZZZ-BBB-1.001", 1, 1, 1)])
        plan = self.plan(manifest)
        self.assertEqual(self.classes(plan), {("ZZZ-BBB-1.001", 1): importer.IDENTITY_MISMATCH})
        with self.assertRaises(importer.HistoricalImportBlocked):
            self.apply(manifest, plan_hash="x")
        self.assertFalse(BiologicalMeasurement.objects.filter(box__global_code="ZZZ-BBB-1.001").exists())

    def test_local_code_or_strain_mismatch_is_an_identity_mismatch(self):
        manifest = make_manifest([box_spec("AAA-BBB-1.001", local="AAA-BBB-1.99")], [item("AAA-BBB-1.001", 1, 1, 1)])
        self.assertEqual(self.classes(self.plan(manifest)), {("AAA-BBB-1.001", 1): importer.IDENTITY_MISMATCH})

    def test_strain_owned_by_another_organization_blocks_materialization(self):
        Strain.objects.create(species=self.species, organization=self.other, code="NEW-QA-1")
        manifest = make_manifest([box_spec("NEW-QA-1.001")], [item("NEW-QA-1.001", 1, 1, 1)])
        self.assertEqual(self.classes(self.plan(manifest)), {("NEW-QA-1.001", 1): importer.IDENTITY_MISMATCH})


class ApplyTests(ImporterBase):
    def test_apply_creates_rows_with_null_strobila_unknown_observer_and_keeps_zero(self):
        manifest = self.standard(item("AAA-BBB-1.001", 1, 0, 0), item("AAA-BBB-1.001", 2, 4, 1))
        plan, receipt = self.apply(manifest)
        zero = BiologicalMeasurement.objects.get(box=self.box, week_start=date(2025, 12, 29))
        self.assertEqual((zero.polyp_count, zero.ephyrae_count), (0, 0))
        self.assertIsNone(zero.strobila_count)
        self.assertIsNone(zero.user)
        self.assertEqual(zero.measured_on, date(2025, 12, 29))
        self.assertEqual(zero.culture_status, "not_specified")
        self.assertEqual(receipt.action, AuditLog.Action.IMPORT)
        self.assertEqual(receipt.metadata["measurements_created"], 2)
        self.assertEqual(receipt.metadata["manifest_fingerprint"], manifest["fingerprint"])
        self.assertEqual(receipt.user, self.admin)

    def test_inactive_box_receives_history_without_reactivation(self):
        self.box.status = Box.Status.INACTIVE
        self.box.save(update_fields=["status"])
        self.apply(self.standard(item("AAA-BBB-1.001", 1, 3, 3)))
        self.box.refresh_from_db()
        self.assertEqual(self.box.status, Box.Status.INACTIVE)
        self.assertIsNone(self.box.thermal_zone)
        self.assertEqual(BiologicalMeasurement.objects.filter(box=self.box).count(), 1)

    def test_existing_strobila_zero_is_never_rewritten_to_null(self):
        existing = self.measurement(self.box, 1, 5, 0, strobila_count=0)
        self.apply(self.standard(item("AAA-BBB-1.001", 1, 5, 0), item("AAA-BBB-1.001", 2, 1, 1)))
        existing.refresh_from_db()
        self.assertEqual(existing.strobila_count, 0)

    def test_missing_box_is_materialized_as_pending_review_without_inventions(self):
        manifest = make_manifest(
            [box_spec("NEW-QA-2.003", species="Operational sp.", number="03")],
            [item("NEW-QA-2.003", 1, 3, 0), item("NEW-QA-2.003", 2, 0, 0)],
        )
        plan = self.plan(manifest)
        self.assertEqual(plan.report()["boxes_to_materialize"], ["NEW-QA-2.003"])
        self.assertEqual(plan.report()["new_measurements"]["on_newly_materialized_boxes"], 2)
        self.apply(manifest)
        box = Box.objects.get(global_code="NEW-QA-2.003")
        self.assertEqual(box.status, Box.Status.PENDING_REVIEW)
        self.assertEqual((box.organization, box.local_code, box.box_number), (self.org, "NEW-QA-2.03", "03"))
        self.assertIsNone(box.thermal_zone)
        self.assertIsNone(box.entered_on)
        self.assertEqual(box.strain.organization, self.org)
        self.assertEqual((box.strain.number, box.strain.origin_code), (2, "QA"))
        self.assertEqual(box.strain.species.scientific_name, "Operational sp.")
        self.assertEqual(box.biological_measurements.count(), 2)
        self.assertTrue(AuditLog.objects.filter(object_type="box", object_id="NEW-QA-2.003", action="creation").exists())
        self.assertEqual(self.plan(manifest).box_states(), {"PRESENT": 1})

    def test_materialized_box_reuses_an_existing_strain_and_species(self):
        manifest = make_manifest([box_spec("AAA-BBB-1.002", number="02")], [item("AAA-BBB-1.002", 1, 1, 0)])
        before = (Strain.objects.count(), Species.objects.count())
        self.apply(manifest)
        self.assertEqual((Strain.objects.count(), Species.objects.count()), before)
        self.assertEqual(Box.objects.get(global_code="AAA-BBB-1.002").strain, self.strain)

    def test_strain_species_disagreeing_with_the_source_needs_a_reviewed_exception(self):
        spec = box_spec("AAA-BBB-1.002", species="Chrysaora lactea", number="02")
        manifest = make_manifest([spec], [item("AAA-BBB-1.002", 1, 1, 0)])
        self.assertEqual(self.classes(self.plan(manifest)), {("AAA-BBB-1.002", 1): importer.IDENTITY_MISMATCH})
        excepted = make_manifest(
            [spec], [item("AAA-BBB-1.002", 1, 1, 0)],
            exceptions={"AAA-BBB-1": {"source_label": "Chrysaora lactea", "reason": "TEST"}},
        )
        self.assertEqual(self.classes(self.plan(excepted)), {("AAA-BBB-1.002", 1): importer.MISSING_BOX})

    def test_box_creation_rolls_back_when_a_later_measurement_fails(self):
        manifest = make_manifest(
            [box_spec("NEW-QA-2.001")],
            [item("NEW-QA-2.001", 1, 3, 0), item("NEW-QA-2.001", 2, 4, 0)],
        )
        plan_hash = self.plan(manifest).plan_hash
        before = self.counts()
        real_save = BiologicalMeasurement.save
        calls = []

        def failing_save(instance, *args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("measurement failure")
            return real_save(instance, *args, **kwargs)

        with mock.patch.object(BiologicalMeasurement, "save", failing_save):
            with self.assertRaisesMessage(RuntimeError, "measurement failure"):
                self.apply(manifest, plan_hash=plan_hash)
        self.assertEqual(self.counts(), before)
        self.assertFalse(Box.objects.filter(global_code="NEW-QA-2.001").exists())
        self.assertFalse(Strain.objects.filter(code="NEW-QA-2").exists())

    def test_audit_failure_rolls_everything_back(self):
        manifest = self.standard(item("AAA-BBB-1.001", 1, 3, 0))
        plan_hash = self.plan(manifest).plan_hash
        before = self.counts()
        with mock.patch.object(AuditLog.objects, "create", side_effect=RuntimeError("audit down")):
            with self.assertRaisesMessage(RuntimeError, "audit down"):
                self.apply(manifest, plan_hash=plan_hash)
        self.assertEqual(self.counts(), before)

    def test_rerun_is_idempotent(self):
        manifest = make_manifest(
            [box_spec("AAA-BBB-1.001"), box_spec("NEW-QA-2.001")],
            [item("AAA-BBB-1.001", 1, 3, 0), item("NEW-QA-2.001", 1, 0, 0)],
        )
        self.apply(manifest)
        after_first = self.counts()
        plan, receipt = self.apply(manifest)
        self.assertIsNone(receipt)
        self.assertEqual(self.counts(), after_first)
        self.assertEqual(set(self.classes(plan).values()), {importer.EXACT_ALREADY_PRESENT})
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.IMPORT).count(), 1)

    def test_stale_plan_hash_is_rejected(self):
        manifest = self.standard(item("AAA-BBB-1.001", 1, 3, 0))
        plan_hash = self.plan(manifest).plan_hash
        self.measurement(self.box, 1, 9, 9)
        with self.assertRaises(importer.HistoricalImportBlocked):
            self.apply(manifest, plan_hash=plan_hash)
        with self.assertRaisesMessage(importer.HistoricalImportError, "plan hash"):
            importer.apply_import(manifest, self.org, actor=self.admin, expected_plan_hash=None)

    def test_plan_hash_mismatch_is_rejected_without_writes(self):
        manifest = self.standard(item("AAA-BBB-1.001", 1, 3, 0))
        before = self.counts()
        with self.assertRaisesMessage(importer.HistoricalImportError, "database changed"):
            self.apply(manifest, plan_hash="f" * 64)
        self.assertEqual(self.counts(), before)

    def test_only_an_active_administrator_of_the_organization_may_apply(self):
        manifest = self.standard(item("AAA-BBB-1.001", 1, 3, 0))
        plan_hash = self.plan(manifest).plan_hash
        for actor in (self.tech, self.foreign_admin):
            with self.subTest(actor=actor.username), self.assertRaises(PermissionDenied):
                self.apply(manifest, actor=actor, plan_hash=plan_hash)
        self.assertEqual(BiologicalMeasurement.objects.count(), 0)

    def test_other_organization_data_is_never_touched(self):
        foreign_strain = Strain.objects.create(species=self.species, organization=self.other, code="FOR-EIG-1")
        foreign_box = self.make_box("FOR-EIG-1.001", org=self.other, strain=foreign_strain)
        self.measurement(foreign_box, 1, 5, 5)
        self.apply(self.standard(item("AAA-BBB-1.001", 1, 3, 0)))
        self.assertEqual(list(foreign_box.biological_measurements.values_list("polyp_count", "ephyrae_count")), [(5, 5)])


class ExplicitCorrectionTests(ImporterBase):
    def setUp(self):
        super().setUp()
        self.ldr_strain = Strain.objects.create(species=self.species, organization=self.org, code="LDR-JAP-1")
        self.ldr = self.make_box("LDR-JAP-1.001", strain=self.ldr_strain)
        self.row = item(
            "LDR-JAP-1.001", 18, 80, 0, operation="EXPLICIT_CORRECTION",
            expected={"polyps": 80, "ephyrae": 8}, corrections=[{"code": "TEST_LDR"}],
        )
        self.manifest = make_manifest([box_spec("LDR-JAP-1.001")], [self.row])

    def test_80_8_is_corrected_to_80_0_with_audit(self):
        existing = self.measurement(self.ldr, 18, 80, 8, strobila_count=0)
        before_revision = Box.objects.get(pk=self.ldr.pk).polyp_state_revision
        plan, receipt = self.apply(self.manifest)
        self.assertEqual(plan.counts[importer.EXPECTED_EXPLICIT_CORRECTION], 1)
        existing.refresh_from_db()
        self.assertEqual((existing.polyp_count, existing.ephyrae_count, existing.strobila_count), (80, 0, 0))
        self.assertEqual(BiologicalMeasurement.objects.filter(box=self.ldr).count(), 1)
        self.assertGreater(Box.objects.get(pk=self.ldr.pk).polyp_state_revision, before_revision)
        audit = AuditLog.objects.get(object_type="box", object_id="LDR-JAP-1.001", action=AuditLog.Action.UPDATE)
        self.assertEqual(audit.metadata["before"]["ephyrules"], 8)
        self.assertEqual(audit.metadata["after"]["ephyrules"], 0)
        self.assertEqual(audit.metadata["modifications"], {"ephyrules": {"avant": 8, "apres": 0}})
        self.assertEqual(audit.metadata["reason"], "TEST_LDR")
        self.assertEqual(audit.metadata["measurement_id"], existing.pk)

    def test_unexpected_current_value_fails_closed(self):
        existing = self.measurement(self.ldr, 18, 80, 5)
        before = self.counts()
        plan = self.plan(self.manifest)
        self.assertEqual(plan.counts[importer.CONFLICT], 1)
        with self.assertRaises(importer.HistoricalImportBlocked):
            self.apply(self.manifest, plan_hash="x")
        existing.refresh_from_db()
        self.assertEqual((existing.polyp_count, existing.ephyrae_count), (80, 5))
        self.assertEqual(self.counts(), before)

    def test_missing_row_to_correct_is_a_conflict_not_a_create(self):
        self.assertEqual(self.plan(self.manifest).counts[importer.CONFLICT], 1)

    def test_rerun_after_correction_is_satisfied_and_not_repeated(self):
        self.measurement(self.ldr, 18, 80, 8)
        self.apply(self.manifest)
        plan, receipt = self.apply(self.manifest)
        self.assertIsNone(receipt)
        self.assertEqual(self.classes(plan), {("LDR-JAP-1.001", 18): importer.EXACT_ALREADY_PRESENT})
        self.assertEqual(AuditLog.objects.filter(object_id="LDR-JAP-1.001", action=AuditLog.Action.UPDATE).count(), 1)

    def test_correction_rolls_back_if_the_audit_fails(self):
        existing = self.measurement(self.ldr, 18, 80, 8)
        plan_hash = self.plan(self.manifest).plan_hash
        with mock.patch.object(AuditLog.objects, "create", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError):
                self.apply(self.manifest, plan_hash=plan_hash)
        existing.refresh_from_db()
        self.assertEqual(existing.ephyrae_count, 8)


class IdentityCorrectionTests(ImporterBase):
    CORRECTION = {
        "from_code": "COR-JIS-1.001",
        "to_code": "ATO-JIS-1.001",
        "from_strain_code": "COR-JIS-1",
        "to_strain_code": "ATO-JIS-1",
        "to_species_name": "Atorella sp.",
        "to_species_code": "ATO",
        "reason": "TEST",
    }

    def setUp(self):
        super().setUp()
        cory = Species.objects.create(scientific_name="Corynidae sp.", genus_species_code="COR")
        self.cor_strain = Strain.objects.create(species=cory, organization=self.org, code="COR-JIS-1")
        self.cor = self.make_box("COR-JIS-1.001", strain=self.cor_strain)
        self.old_measurement = self.measurement(self.cor, 40, 34, 0)
        self.audit = AuditLog.objects.create(
            organization=self.org, action="entry", object_type="box", object_id="COR-JIS-1.001",
            description="old history", metadata={"measurement_id": self.old_measurement.pk},
        )
        self.tag = IdentificationTag.objects.create(code="QR-COR-JIS-1.001", box=self.cor)
        self.child = self.make_box("AAA-BBB-1.002", number="02")
        self.lineage = BoxLineage.objects.create(parent_box=self.cor, child_box=self.child)
        self.manifest = make_manifest(
            [box_spec("ATO-JIS-1.001", species="Atorella sp.")],
            [item("ATO-JIS-1.001", 1, 34, 0), item("ATO-JIS-1.001", 40, 34, 0)],
            identity=[self.CORRECTION],
        )

    def test_correction_keeps_one_physical_box_with_history_and_relations(self):
        plan = self.plan(self.manifest)
        self.assertEqual(plan.report()["identity_corrections"], ["ATO-JIS-1.001"])
        self.assertEqual(plan.report()["boxes_to_materialize"], [])
        self.assertEqual(self.classes(plan), {
            ("ATO-JIS-1.001", 1): importer.CREATE,
            ("ATO-JIS-1.001", 40): importer.EXACT_ALREADY_PRESENT,
        })
        before_boxes = Box.objects.count()
        self.apply(self.manifest)
        self.assertEqual(Box.objects.count(), before_boxes)
        self.assertFalse(Box.objects.filter(global_code="COR-JIS-1.001").exists())
        box = Box.objects.get(pk=self.cor.pk)
        self.assertEqual((box.global_code, box.local_code, box.strain.code), ("ATO-JIS-1.001", "ATO-JIS-1.01", "ATO-JIS-1"))
        self.assertEqual(box.strain.species.scientific_name, "Atorella sp.")
        self.assertEqual(box.strain.organization, self.org)
        self.assertEqual(box.status, Box.Status.ACTIVE)
        self.assertEqual(sorted(box.biological_measurements.values_list("measured_on__year", "polyp_count")), [(2025, 34), (2026, 34)])
        self.old_measurement.refresh_from_db()
        self.assertEqual((self.old_measurement.polyp_count, self.old_measurement.ephyrae_count), (34, 0))
        self.audit.refresh_from_db()
        self.assertEqual(self.audit.object_id, "ATO-JIS-1.001")
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.code, "QR-ATO-JIS-1.001")
        self.lineage.refresh_from_db()
        self.assertEqual(self.lineage.parent_box_id, box.pk)
        event = AuditLog.objects.get(object_id="ATO-JIS-1.001", description__startswith="Box identity corrected")
        self.assertEqual(event.metadata["previous_global_code"], "COR-JIS-1.001")
        self.assertTrue(Strain.objects.filter(code="COR-JIS-1").exists())

    def test_rerun_does_not_repeat_the_identity_correction(self):
        self.apply(self.manifest)
        counts = self.counts()
        plan, receipt = self.apply(self.manifest)
        self.assertIsNone(receipt)
        self.assertEqual(plan.box_states(), {"PRESENT": 1})
        self.assertEqual(self.counts(), counts)

    def test_both_identities_existing_would_duplicate_the_culture_and_is_refused(self):
        ato_strain = Strain.objects.create(
            species=Species.objects.create(scientific_name="Atorella sp.", genus_species_code="ATO"),
            organization=self.org, code="ATO-JIS-1",
        )
        self.make_box("ATO-JIS-1.001", strain=ato_strain)
        plan = self.plan(self.manifest)
        self.assertEqual(plan.box_states(), {"IDENTITY_MISMATCH": 1})
        with self.assertRaises(importer.HistoricalImportBlocked):
            self.apply(self.manifest, plan_hash="x")
        self.assertTrue(Box.objects.filter(global_code="COR-JIS-1.001").exists())

    def test_correction_source_in_another_organization_is_refused(self):
        self.cor.organization = self.other
        self.cor.save(update_fields=["organization"])
        self.assertEqual(self.plan(self.manifest).box_states(), {"IDENTITY_MISMATCH": 1})
        with self.assertRaises(importer.HistoricalImportBlocked):
            self.apply(self.manifest, plan_hash="x")
        self.cor.refresh_from_db()
        self.assertEqual(self.cor.global_code, "COR-JIS-1.001")

    def test_wrong_source_strain_is_an_identity_mismatch(self):
        self.cor.strain = self.strain
        self.cor.save(update_fields=["strain"])
        self.assertEqual(self.plan(self.manifest).box_states(), {"IDENTITY_MISMATCH": 1})

    def test_identity_correction_rolls_back_on_failure(self):
        plan_hash = self.plan(self.manifest).plan_hash
        with mock.patch.object(BiologicalMeasurement, "save", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.apply(self.manifest, plan_hash=plan_hash)
        self.cor.refresh_from_db()
        self.assertEqual((self.cor.global_code, self.cor.strain.code), ("COR-JIS-1.001", "COR-JIS-1"))
        self.assertFalse(Strain.objects.filter(code="ATO-JIS-1").exists())
        self.assertFalse(Species.objects.filter(scientific_name="Atorella sp.").exists())
        self.audit.refresh_from_db()
        self.assertEqual(self.audit.object_id, "COR-JIS-1.001")


class HsDeactivationTests(ImporterBase):
    def setUp(self):
        super().setUp()
        self.zone = ThermalZone.objects.create(organization=self.org, name="Zone")
        self.box.thermal_zone = self.zone
        self.box.save(update_fields=["thermal_zone"])

    def test_plan_classifies_without_writing_and_apply_uses_the_lifecycle_service(self):
        with mock.patch.multiple(
            "apps.cultures.historical_2026.deactivation.decisions",
            HS_BOXES=("AAA-BBB-1.001", "AAA-BBB-1.404", "COR-JIS-1.001", "CTU-CFC-2.007"),
            HS_CODE_CORRECTIONS={"COR-JIS-1.001": "ATO-JIS-1.001"},
        ):
            entries = deactivation.build_deactivation_plan(self.org)
            self.assertEqual(
                deactivation.summarize(entries),
                {
                    "READY": ["AAA-BBB-1.001"],
                    "NOT_FOUND": ["AAA-BBB-1.404", "COR-JIS-1.001"],
                    "PENDING_CONFIRMATION": ["CTU-CFC-2.007"],
                },
            )
            self.box.refresh_from_db()
            self.assertEqual(self.box.status, Box.Status.ACTIVE)
            plan_hash = deactivation.plan_hash(self.org, entries)
            with self.assertRaises(PermissionDenied):
                deactivation.apply_deactivations(self.org, actor=self.tech, expected_plan_hash=plan_hash)
            _, done = deactivation.apply_deactivations(self.org, actor=self.admin, expected_plan_hash=plan_hash)
            self.assertEqual(done, ["AAA-BBB-1.001"])
            self.box.refresh_from_db()
            self.assertEqual(self.box.status, Box.Status.INACTIVE)
            self.assertIsNone(self.box.thermal_zone)
            self.assertTrue(Box.objects.filter(pk=self.box.pk).exists())
            self.assertTrue(AuditLog.objects.filter(object_id="AAA-BBB-1.001", metadata__transition="active->inactive").exists())
            again = deactivation.build_deactivation_plan(self.org)
            self.assertEqual(deactivation.summarize(again)["ALREADY_INACTIVE"], ["AAA-BBB-1.001"])

    def test_other_organization_boxes_are_not_found(self):
        foreign_strain = Strain.objects.create(species=self.species, organization=self.other, code="FOR-EIG-1")
        self.make_box("FOR-EIG-1.001", org=self.other, strain=foreign_strain)
        with mock.patch("apps.cultures.historical_2026.deactivation.decisions.HS_BOXES", ("FOR-EIG-1.001",)):
            entries = deactivation.build_deactivation_plan(self.org)
        self.assertEqual(entries[0]["status"], deactivation.NOT_FOUND)


class TthCorrectionTests(ImporterBase):
    def setUp(self):
        super().setUp()
        self.tth_strain = Strain.objects.create(species=self.species, organization=self.org, code="TTH-AVI-1")
        self.tth = self.make_box("TTH-AVI-1.009", strain=self.tth_strain)
        self.inversion = [{"code": "ANTHONY_TTH_LABEL_INVERSION"}]

    def row(self, week, polyps, ephyrae):
        return item(
            "TTH-AVI-1.009", week, polyps, ephyrae, corrections=self.inversion,
            legacy={"polyps": ephyrae, "ephyrae": polyps},
        )

    def manifest(self, *rows):
        return make_manifest([box_spec("TTH-AVI-1.009", local="TTH-AVI-1.09")], list(rows))

    def test_swapped_old_state_is_an_expected_correction_and_zero_is_preserved(self):
        old = self.measurement(self.tth, 1, 20, 300, strobila_count=0)
        zero = self.measurement(self.tth, 2, 0, 300)
        manifest = self.manifest(self.row(1, 300, 20), self.row(2, 300, 0), self.row(3, 250, 5))
        plan = self.plan(manifest)
        self.assertEqual(plan.report()["tth"], {
            "TTH_CREATE": 1, "TTH_EXPECTED_CORRECTION": 2, "TTH_ALREADY_CORRECT": 0, "TTH_CONFLICT": 0,
        })
        self.assertEqual(plan.counts[importer.EXPECTED_TTH_CORRECTION], 2)
        self.assertEqual(plan.blockers, [])
        _, receipt = self.apply(manifest)
        old.refresh_from_db(), zero.refresh_from_db()
        self.assertEqual((old.polyp_count, old.ephyrae_count, old.strobila_count), (300, 20, 0))
        self.assertEqual((zero.polyp_count, zero.ephyrae_count), (300, 0))
        self.assertEqual(BiologicalMeasurement.objects.filter(box=self.tth).count(), 3)
        self.assertEqual(receipt.metadata["tth_measurements_corrected"], 2)
        audit = AuditLog.objects.get(metadata__measurement_id=old.pk, action=AuditLog.Action.UPDATE)
        self.assertEqual(audit.metadata["before"]["polypes"], 20)
        self.assertEqual(audit.metadata["after"]["polypes"], 300)
        self.assertEqual(audit.metadata["reason"], "ANTHONY_TTH_LABEL_INVERSION")
        self.assertEqual(audit.object_id, "TTH-AVI-1.009")

    def test_already_corrected_row_is_satisfied_without_a_second_mutation(self):
        self.measurement(self.tth, 1, 300, 20)
        manifest = self.manifest(self.row(1, 300, 20))
        plan = self.plan(manifest)
        self.assertEqual(plan.report()["tth"]["TTH_ALREADY_CORRECT"], 1)
        _, receipt = self.apply(manifest)
        self.assertIsNone(receipt)
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.UPDATE).exists())

    def test_correction_is_applied_once_then_idempotent(self):
        self.measurement(self.tth, 1, 20, 300)
        manifest = self.manifest(self.row(1, 300, 20))
        self.apply(manifest)
        _, receipt = self.apply(manifest)
        self.assertIsNone(receipt)
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.UPDATE).count(), 1)

    def test_unexpected_third_value_fails_closed(self):
        existing = self.measurement(self.tth, 1, 5, 5)
        manifest = self.manifest(self.row(1, 300, 20))
        plan = self.plan(manifest)
        self.assertEqual(plan.report()["tth"]["TTH_CONFLICT"], 1)
        before = self.counts()
        with self.assertRaises(importer.HistoricalImportBlocked):
            self.apply(manifest, plan_hash="x")
        existing.refresh_from_db()
        self.assertEqual((existing.polyp_count, existing.ephyrae_count), (5, 5))
        self.assertEqual(self.counts(), before)

    def test_swapped_value_on_another_day_is_not_corrected(self):
        self.measurement(self.tth, 1, 20, 300, measured_on=date.fromisocalendar(ISO_YEAR, 1, 3))
        plan = self.plan(self.manifest(self.row(1, 300, 20)))
        self.assertEqual(plan.report()["tth"]["TTH_CONFLICT"], 1)

    def test_only_rows_carrying_the_reviewed_inversion_may_be_corrected(self):
        self.measurement(self.tth, 1, 20, 300)
        manifest = self.manifest(item("TTH-AVI-1.009", 1, 300, 20))
        self.assertEqual(self.plan(manifest).counts[importer.CONFLICT], 1)

    def test_state_changed_between_review_and_apply_is_rejected_atomically(self):
        existing = self.measurement(self.tth, 1, 20, 300)
        manifest = self.manifest(self.row(1, 300, 20), self.row(2, 250, 5))
        plan_hash = self.plan(manifest).plan_hash
        existing.polyp_count = 21
        existing.save(update_fields=["polyp_count"])
        before = self.counts()
        with self.assertRaises(importer.HistoricalImportBlocked):
            self.apply(manifest, plan_hash=plan_hash)
        self.assertEqual(self.counts(), before)

    def test_correction_rolls_back_with_the_audit(self):
        existing = self.measurement(self.tth, 1, 20, 300)
        manifest = self.manifest(self.row(1, 300, 20))
        plan_hash = self.plan(manifest).plan_hash
        with mock.patch.object(AuditLog.objects, "create", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError):
                self.apply(manifest, plan_hash=plan_hash)
        existing.refresh_from_db()
        self.assertEqual((existing.polyp_count, existing.ephyrae_count), (20, 300))
