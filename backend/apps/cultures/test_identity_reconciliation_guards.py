"""Synthetic replay and HS guards for the exact reviewed CLA/CLC identities."""

from unittest import mock

from apps.measurements.models import BiologicalMeasurement
from apps.taxonomy.models import Species, Strain

from .historical_2026 import deactivation, importer
from .models import Box
from .test_historical_2026_import import ImporterBase, box_spec, item, make_manifest


class IdentityReconciliationGuardTests(ImporterBase):
    CODES = (
        ("CLA-JKA-1.009", "CLC-JKA-1.009", "09"),
        ("CLA-JKA-1.010", "CLC-JKA-1.010", "10"),
    )
    BLOCK_DETAIL = "historical identity is corrected or reserved; old-manifest replay is blocked"

    def setUp(self):
        super().setUp()
        self.lactea = Species.objects.create(scientific_name="Chrysaora lactea")
        self.clc = Strain.objects.create(
            species=self.lactea, organization=self.org, code="CLC-JKA-1",
        )

    def historical_target(self, code, *, strain, number, org=None):
        box = self.make_box("CLC-JKA-1.999", org=org, strain=strain, number=number)
        # This persisted historical collision is forbidden by ordinary creation.
        Box._base_manager.filter(pk=box.pk).update(global_code=code)
        box.refresh_from_db()
        return box

    def old_manifest(self, code, number):
        return make_manifest(
            [box_spec("AAA-BBB-1.001"), box_spec(code, species="Chrysaora lactea", number=number)],
            [item("AAA-BBB-1.001", 1, 5, 1), item(code, 1, 0, 0)],
            exceptions={"CLA-JKA-1": {"source_label": "Chrysaora lactea", "reason": "TEST_D11"}},
        )

    def assert_replay_blocked(self, code, number):
        manifest = self.old_manifest(code, number)
        before = (self.counts(), list(Box.objects.order_by("pk").values()))
        plan = self.plan(manifest)
        self.assertEqual(plan.boxes[code]["state"], importer.BOX_IDENTITY_MISMATCH)
        self.assertEqual(plan.boxes[code]["detail"], self.BLOCK_DETAIL)
        self.assertIsNone(plan.boxes[code]["box"])
        self.assertEqual(self.classes(plan)[(code, 1)], importer.IDENTITY_MISMATCH)
        self.assertNotIn(code, plan.report()["boxes_to_materialize"])
        with self.assertRaises(importer.HistoricalImportBlocked):
            self.apply(manifest, plan_hash=plan.plan_hash)
        self.assertEqual((self.counts(), list(Box.objects.order_by("pk").values())), before)
        self.assertFalse(BiologicalMeasurement.objects.exists())

    def test_missing_old_code_with_corrected_target_blocks_replay_without_writes(self):
        for old, corrected, number in self.CODES:
            with self.subTest(code=old):
                self.make_box(corrected, strain=self.clc, number=number)
                self.assert_replay_blocked(old, number)
                self.assertFalse(Box.objects.filter(global_code=old).exists())

    def test_foreign_corrected_target_blocks_without_exposing_its_identity(self):
        private_species = Species.objects.create(scientific_name="Private foreign species")
        private_strain = Strain.objects.create(
            species=private_species, organization=self.other, code="PRIVATE-FOREIGN-1",
        )
        for old, corrected, number in self.CODES:
            with self.subTest(code=old):
                self.make_box(corrected, org=self.other, strain=private_strain, number=number)
                self.assert_replay_blocked(old, number)
                detail = self.plan(self.old_manifest(old, number)).boxes[old]
                self.assertNotIn(private_species.scientific_name, str(detail))
                self.assertNotIn(private_strain.code, str(detail))
                self.assertNotIn(self.other.name, str(detail))

    def test_both_codes_existing_block_even_when_old_species_matches(self):
        historical = Strain.objects.create(
            species=self.lactea, organization=self.org, code="CLA-JKA-1",
        )
        for old, corrected, number in self.CODES:
            with self.subTest(code=old):
                self.make_box(old, strain=historical, number=number, local=f"CLA-JKA-1.{number}")
                self.historical_target(corrected, strain=self.clc, number=number)
                self.assert_replay_blocked(old, number)

    def test_both_codes_existing_with_foreign_corrected_target_block(self):
        historical = Strain.objects.create(
            species=self.lactea, organization=self.org, code="CLA-JKA-1",
        )
        foreign = Strain.objects.create(
            species=self.lactea, organization=self.other, code="FOREIGN-JKA-1",
        )
        for old, corrected, number in self.CODES:
            with self.subTest(code=old):
                self.make_box(old, strain=historical, number=number, local=f"CLA-JKA-1.{number}")
                self.historical_target(corrected, org=self.other, strain=foreign, number=number)
                self.assert_replay_blocked(old, number)

    def test_reserved_old_code_blocks_without_a_corrected_target(self):
        for old, corrected, number in self.CODES:
            with self.subTest(code=old):
                holder = self.make_box(f"CLC-JKA-1.1{number}", strain=self.clc, number=f"1{number}")
                # Existing reservations are checked, never created by either guard.
                Box.objects.filter(pk=holder.pk).update(previous_global_code=old)
                self.assertFalse(Box.objects.filter(global_code=corrected).exists())
                self.assert_replay_blocked(old, number)

    def test_foreign_reservation_blocks_with_the_same_generic_detail(self):
        for old, corrected, number in self.CODES:
            with self.subTest(code=old):
                holder = self.make_box(f"OTHER-JKA-1.1{number}", org=self.other, number=f"1{number}")
                Box.objects.filter(pk=holder.pk).update(previous_global_code=old)
                self.assert_replay_blocked(old, number)

    def test_original_d11_species_guard_still_blocks_without_corrected_target(self):
        lamarckii = Species.objects.create(scientific_name="Cyanea lamarckii")
        historical = Strain.objects.create(
            species=lamarckii, organization=self.org, code="CLA-JKA-1",
        )
        for old, corrected, number in self.CODES:
            with self.subTest(code=old):
                self.make_box(old, strain=historical, number=number, local=f"CLA-JKA-1.{number}")
                manifest = self.old_manifest(old, number)
                plan = self.plan(manifest)
                self.assertEqual(plan.boxes[old]["state"], importer.BOX_IDENTITY_MISMATCH)
                self.assertIn("D11", plan.boxes[old]["detail"])
                self.assertFalse(Box.objects.filter(global_code=corrected).exists())
                before = self.counts()
                with self.assertRaises(importer.HistoricalImportBlocked):
                    self.apply(manifest, plan_hash=plan.plan_hash)
                self.assertEqual(self.counts(), before)

    def test_compatible_original_d11_identity_still_accepts_zero(self):
        historical = Strain.objects.create(
            species=self.lactea, organization=self.org, code="CLA-JKA-1",
        )
        for old, corrected, number in self.CODES:
            with self.subTest(code=old):
                box = self.make_box(old, strain=historical, number=number, local=f"CLA-JKA-1.{number}")
                manifest = self.old_manifest(old, number)
                plan = self.plan(manifest)
                self.assertEqual(plan.boxes[old]["state"], importer.BOX_PRESENT)
                self.apply(manifest, plan_hash=plan.plan_hash)
                measurement = box.biological_measurements.get()
                self.assertEqual(
                    (measurement.polyp_count, measurement.ephyrae_count, measurement.strobila_count),
                    (0, 0, None),
                )
                self.assertFalse(Box.objects.filter(global_code=corrected).exists())

    def test_replay_guard_does_not_expand_to_other_cla_codes(self):
        historical = Strain.objects.create(
            species=self.lactea, organization=self.org, code="CLA-JKA-1",
        )
        old = "CLA-JKA-1.008"
        self.make_box(old, strain=historical, number="08", local="CLA-JKA-1.08")
        self.make_box("CLC-JKA-1.008", strain=self.clc, number="08")
        plan = self.plan(self.old_manifest(old, "08"))
        self.assertEqual(plan.boxes[old]["state"], importer.BOX_PRESENT)

    def test_hs_contradiction_protects_original_and_corrected_clc(self):
        for code in ("CLA-JKA-1.009", "CLC-JKA-1.009"):
            with self.subTest(code=code):
                reason = deactivation.deactivation_block_reason(code)
                self.assertIn("V2 observations", reason)
                self.assertIn("CLC-JKA-1.09", reason)
                self.assertIn("identity correction does not authorize deactivation", reason)
                self.assertNotIn("identity is unresolved", reason)
        self.assertEqual(deactivation.deactivation_block_reason("CLC-JKA-1.010"), "")
        self.assertNotIn("CLA-JKA-1.009", deactivation.decisions.HS_CODE_CORRECTIONS)

    def test_hs_apply_guard_checks_corrected_actual_box_even_with_reviewed_hash(self):
        box = self.make_box("CLC-JKA-1.009", strain=self.clc, number="09")
        entries = [{
            "listed_code": "AAL-FGU-1.002", "resolved_code": "AAL-FGU-1.002",
            "status": deactivation.READY, "box": box,
        }]
        reviewed_hash = deactivation.plan_hash(self.org, entries)
        before = (self.counts(), list(Box.objects.order_by("pk").values()))
        with mock.patch.object(deactivation, "build_deactivation_plan", return_value=entries), \
                self.assertRaisesMessage(ValueError, "identity correction does not authorize deactivation"):
            deactivation.apply_deactivations(self.org, actor=self.admin, expected_plan_hash=reviewed_hash)
        self.assertEqual((self.counts(), list(Box.objects.order_by("pk").values())), before)
