"""Six-Box colour-variant Species correction: additive references, then relations only."""

import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.management.color import no_style
from django.db import connection, transaction
from django.test import TestCase

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import LocalStrainIdentity, OrganizationSpeciesCode, Species, Strain
from apps.taxonomy.scoping import eligible_strains

from .box_identity import USABLE, strain_box_identity
from .historical_2026 import variant_species_correction as correction
from .models import (
    Box, BoxLineage, BoxLocation, BoxMovement, ThermalZone, TransferEnvelope, TransferItem,
)
from .qr import box_scan_url, sync_box_qr_tag
from .serializers import BoxDetailSerializer, BoxInventorySerializer, BoxListSerializer

def csv(pks):
    return ",".join(str(pk) for pk in pks)


TARGETS = {pk: (code, strain_code, name) for pk, code, strain_code, name in correction.SPECS}


class VariantSpeciesCorrectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Mirror the reviewed production contract PKs; contents remain synthetic.
        cls.org = Organization.objects.create(pk=correction.ORGANIZATION_PK, name="Variant laboratory",
                                              slug="variant-lab", portable_id=correction.ORGANIZATION_PORTABLE_ID)
        cls.other_org = Organization.objects.create(pk=2, name="Other laboratory", slug="other-variant-lab")
        User = get_user_model()
        cls.admin = User.objects.create_user(username="variant-admin", email="variant-admin@example.org")
        OrganizationMembership.objects.create(organization=cls.org, user=cls.admin, role="admin")
        cls.white = Species.objects.create(pk=127, scientific_name=correction.WHITE)
        cls.striped = Species.objects.create(pk=128, scientific_name=correction.SOURCE_SPECIES[128])
        cls.sma_white = Species.objects.create(pk=143, scientific_name=correction.SOURCE_SPECIES[143])
        # Shared legacy references (organization NULL) with the reviewed source PKs.
        cls.src_ach = Strain.objects.create(pk=201, species=cls.striped, code="CCP-ACH-2", number=2,
                                            origin_code="ACH")
        cls.src_tpr = Strain.objects.create(pk=203, species=cls.striped, code="CCP-TPR-1", number=1,
                                            origin_code="TPR")
        cls.src_sma = Strain.objects.create(pk=242, species=cls.sma_white, code="SMA-JKA-2", number=2,
                                            origin_code="JKA")
        cls.ach3 = Strain.objects.create(pk=202, species=cls.striped, code="CCP-ACH-3", number=3, origin_code="ACH")
        cls.advance_explicit_pk_sequences()
        cls.sources = [cls.src_tpr.pk, cls.src_ach.pk, cls.src_sma.pk]
        cls.ccp = OrganizationSpeciesCode.objects.create(organization=cls.org, species=cls.striped, code="CCP")
        OrganizationSpeciesCode.objects.create(organization=cls.org, species=cls.sma_white, code="SMA")
        cls.zone = ThermalZone.objects.create(organization=cls.org, name="Zone 20")
        cls.previous_zone = ThermalZone.objects.create(organization=cls.org, name="Zone 25")
        source_for = {"CCP-TPR-1": cls.src_tpr, "CCP-ACH-2": cls.src_ach, "SMA-JKA-2": cls.src_sma}
        moved_at = datetime(2026, 9, 21, 10, tzinfo=timezone.utc)
        cls.boxes = {}
        for index, (pk, (code, strain_code, _)) in enumerate(TARGETS.items()):
            status = (Box.Status.ACTIVE, Box.Status.INACTIVE, Box.Status.PENDING_REVIEW)[index % 3]
            box = Box.objects.create(
                pk=pk, organization=cls.org, global_code=code, local_code=code[:-3] + code[-2:],
                box_number=code[-2:], strain=source_for[strain_code], status=status,
                thermal_zone=cls.zone if status == Box.Status.ACTIVE else None,
                notes=f"Historical {code}", entered_on=date(2026, 1, 5), volume_liters="0.00",
                stop_reason="Reviewed" if status == Box.Status.INACTIVE else "",
                deactivated_on=date(2026, 9, 22) if status == Box.Status.INACTIVE else None,
            )
            previous = BoxLocation.objects.create(box=box, thermal_zone=cls.previous_zone,
                                                  starts_at=moved_at - timedelta(days=7), ends_at=moved_at)
            current = BoxLocation.objects.create(box=box, thermal_zone=cls.zone, starts_at=moved_at)
            BoxMovement.objects.create(box=box, from_thermal_zone=cls.previous_zone, to_thermal_zone=cls.zone,
                                       moved_at=moved_at, user=cls.admin, kind=BoxMovement.Kind.ORDINARY,
                                       origin_location=previous, destination_location=current)
            for measured_on, strobila, salinity in ((date(2026, 9, 7), 0, "0.00"), (date(2026, 9, 14), None, None)):
                BiologicalMeasurement.objects.create(
                    box=box, measured_on=measured_on, polyp_count=0, ephyrae_count=0,
                    strobila_count=strobila, salinity_psu=salinity, user=cls.admin,
                    culture_status=BiologicalMeasurement.CultureStatus.GOOD, notes="Scientific zero",
                )
            sync_box_qr_tag(box)
            cls.boxes[pk] = box
        # Non-target consumers of the shared references, including another institution.
        cls.unrelated = [
            Box.objects.create(organization=org, strain=strain, global_code=code, box_number=code[-3:],
                               status=Box.Status.INACTIVE, deactivated_on=date(2026, 3, 1), stop_reason="Old")
            for org, strain, code in (
                (cls.org, cls.src_ach, "CCP-ACH-2.003"), (cls.org, cls.src_sma, "SMA-JKA-2.001"),
                (cls.org, cls.src_sma, "SMA-JKA-2.002"), (cls.other_org, cls.src_sma, "SMA-JKA-2.099"),
            )
        ]
        cls.ach3_box = Box.objects.create(organization=cls.org, strain=cls.ach3, global_code="CCP-ACH-3.001",
                                          box_number="001")
        BoxLineage.objects.create(parent_box=cls.unrelated[0], child_box=cls.boxes[1854])
        BoxLineage.objects.create(parent_box=cls.boxes[1854], child_box=cls.boxes[1855])
        AuditLog.objects.create(organization=cls.org, user=cls.admin, action=AuditLog.Action.ENTRY,
                                object_type="box", object_id="CCP-ACH-2.001", metadata={"existing": True})

    # Helpers -----------------------------------------------------------------

    @staticmethod
    def advance_explicit_pk_sequences():
        # Explicit PKs do not advance PostgreSQL sequences (no-op on SQLite).
        with connection.cursor() as cursor:
            for sql in connection.ops.sequence_reset_sql(no_style(), [Organization, Species, Strain, Box]):
                cursor.execute(sql)

    def database_snapshot(self):
        with connection.cursor() as cursor:
            result = {}
            for table in connection.introspection.table_names(cursor):
                cursor.execute(f"SELECT * FROM {connection.ops.quote_name(table)}")
                result[table] = sorted(cursor.fetchall(), key=repr)
            return result

    def ref_plan(self, sources=None):
        return correction.build_reference_plan(self.org.pk, source_strain_ids=sources or self.sources)

    def prepare(self, actor=None):
        plan = self.ref_plan()
        return correction.apply_reference_plan(self.org.pk, source_strain_ids=self.sources,
                                               actor=actor or self.admin,
                                               expected_plan_hash=correction.fingerprint(plan))

    def targets(self):
        return sorted(self.prepare()["prepared_target_strain_ids"].values())

    def box_plan(self, targets):
        return correction.build_box_plan(self.org.pk, source_strain_ids=self.sources, target_strain_ids=targets)

    def correct(self, targets, actor=None, expected_plan_hash=None):
        return correction.apply_box_plan(
            self.org.pk, source_strain_ids=self.sources, target_strain_ids=targets, actor=actor or self.admin,
            expected_plan_hash=expected_plan_hash or correction.fingerprint(self.box_plan(targets)))

    def assert_box_blocked(self, targets, message="Unresolved conflicts", expected_plan_hash=None):
        before = self.database_snapshot()
        with self.assertRaisesMessage(ValueError, message):
            self.correct(targets, expected_plan_hash=expected_plan_hash)
        self.assertEqual(self.database_snapshot(), before)

    # Reference preparation -----------------------------------------------------

    def test_reference_dry_run_is_read_only_and_plans_only_missing_rows(self):
        before = self.database_snapshot()
        plan = self.ref_plan()
        output = StringIO()
        call_command("correct_variant_species", "references", organization_id=self.org.pk,
                     source_strain_ids=csv(self.sources), stdout=output)
        self.assertEqual(self.database_snapshot(), before)
        self.assertIn(f"Plan SHA-256: {correction.fingerprint(plan)}", output.getvalue())
        self.assertIn("Dry-run only", output.getvalue())
        self.assertEqual(plan["classification"], correction.READY)
        self.assertEqual([e["classification"] for e in plan["entries"]], [correction.NEEDED] * 6)
        groups = {g["strain_code"]: g for g in plan["groups"]}
        self.assertEqual({k: g["create_species"] for k, g in groups.items()},
                         {"CCP-ACH-2": True, "CCP-TPR-1": False, "SMA-JKA-2": True})
        self.assertEqual(groups["CCP-TPR-1"]["species_pk"], self.white.pk)
        self.assertTrue(all(g["create_strain"] for g in groups.values()))
        self.assertEqual(groups["CCP-ACH-2"]["create_strain"]["copied_from_strain_pk"], self.src_ach.pk)
        # A missing AAA assignment is reported, never created.
        self.assertEqual({g["assignment"]["classification"] for g in groups.values()}, {"missing (not created)"})

    def test_reference_apply_adds_exact_owned_technical_strains_only(self):
        species_before = list(Species.objects.order_by("pk").values())
        strains_before = list(Strain.objects.order_by("pk").values())
        assignments_before = list(OrganizationSpeciesCode.objects.order_by("pk").values())
        boxes_before = list(Box.objects.order_by("pk").values())
        result = self.prepare()
        prepared = result["prepared_target_strain_ids"]
        self.assertEqual(set(prepared), {"CCP-ACH-2", "CCP-TPR-1", "SMA-JKA-2"})
        # Existing rows are untouched; only additions.
        self.assertEqual(list(Species.objects.filter(pk__in=[r["id"] for r in species_before])
                              .order_by("pk").values()), species_before)
        self.assertEqual(list(Strain.objects.filter(pk__in=[r["id"] for r in strains_before])
                              .order_by("pk").values()), strains_before)
        self.assertEqual(list(OrganizationSpeciesCode.objects.order_by("pk").values()), assignments_before)
        self.assertEqual(list(Box.objects.order_by("pk").values()), boxes_before)
        self.assertFalse(LocalStrainIdentity.objects.exists())
        self.assertEqual(Species.objects.count(), len(species_before) + 2)
        self.assertEqual(Strain.objects.count(), len(strains_before) + 3)
        source = {"CCP-TPR-1": self.src_tpr, "CCP-ACH-2": self.src_ach, "SMA-JKA-2": self.src_sma}
        names = {"CCP-TPR-1": correction.WHITE, "CCP-ACH-2": correction.PINK, "SMA-JKA-2": correction.BROWN}
        for code, pk in prepared.items():
            with self.subTest(code=code):
                strain = Strain.objects.select_related("species").get(pk=pk)
                self.assertEqual((strain.organization_id, strain.code, strain.number, strain.origin_code),
                                 (self.org.pk, code, source[code].number, source[code].origin_code))
                self.assertEqual(strain.species.scientific_name, names[code])
                self.assertIsNone(strain.global_identity_id)
                self.assertIsNone(strain.origin_id)
                self.assertTrue(eligible_strains(self.org).filter(pk=pk).exists())
                self.assertFalse(eligible_strains(self.other_org).filter(pk=pk).exists())
                self.assertEqual(strain_box_identity(strain, self.org)["status"], USABLE)
        audits = AuditLog.objects.filter(metadata__operation=correction.REFERENCE_OPERATION)
        self.assertEqual(sorted(audits.values_list("object_type", flat=True)),
                         ["species", "species", "strain", "strain", "strain",
                          "variant_species_reference_preparation"])
        self.assertTrue(all(a.user_id == self.admin.pk and a.organization_id == self.org.pk for a in audits))

    def test_reference_apply_is_idempotent(self):
        first = self.prepare()["prepared_target_strain_ids"]
        plan = self.ref_plan()
        self.assertEqual(plan["classification"], correction.ALREADY_PRESENT)
        before = self.database_snapshot()
        self.assertEqual(self.prepare()["prepared_target_strain_ids"], first)
        self.assertEqual(self.database_snapshot(), before)

    def test_reference_apply_reuses_compatible_existing_target(self):
        pink = Species.objects.create(scientific_name=correction.PINK)
        owned = Strain.objects.create(organization=self.org, species=pink, code="CCP-ACH-2", number=2,
                                      origin_code="ACH")
        group = {g["strain_code"]: g for g in self.ref_plan()["groups"]}["CCP-ACH-2"]
        self.assertEqual((group["create_species"], group["create_strain"], group["target_strain_pk"]),
                         (False, None, owned.pk))
        self.assertEqual(self.prepare()["prepared_target_strain_ids"]["CCP-ACH-2"], owned.pk)

    def test_reference_stale_hash_is_rejected(self):
        reviewed = correction.fingerprint(self.ref_plan())
        Box.objects.filter(pk=1854).update(notes="Changed after review")
        before = self.database_snapshot()
        with self.assertRaisesMessage(ValueError, "Database state changed"):
            correction.apply_reference_plan(self.org.pk, source_strain_ids=self.sources, actor=self.admin,
                                            expected_plan_hash=reviewed)
        self.assertEqual(self.database_snapshot(), before)

    def test_contradictory_target_assignment_blocks_both_steps(self):
        OrganizationSpeciesCode.objects.create(organization=self.org, species=self.white, code="CWH")
        plan = self.ref_plan()
        self.assertEqual(plan["classification"], correction.CONFLICT)
        self.assertIn("contradictory", " ".join(plan["conflicts"]))
        before = self.database_snapshot()
        with self.assertRaisesMessage(ValueError, "Unresolved conflicts"):
            correction.apply_reference_plan(self.org.pk, source_strain_ids=self.sources, actor=self.admin,
                                            expected_plan_hash=correction.fingerprint(plan))
        self.assertEqual(self.database_snapshot(), before)

    def test_near_duplicate_species_name_needs_separate_review(self):
        Species.objects.create(scientific_name="Chrysaora chesapeakei “Pink”")
        plan = self.ref_plan()
        self.assertEqual(plan["classification"], correction.CONFLICT)
        self.assertIn("Near-duplicate", " ".join(plan["conflicts"]))

    def test_foreign_existing_target_is_withheld_and_blocks(self):
        pink = Species.objects.create(scientific_name=correction.PINK)
        Strain.objects.create(organization=self.other_org, species=pink, code="CCP-ACH-2", notes="secret")
        plan = self.ref_plan()
        self.assertIn("outside this institution", " ".join(plan["conflicts"]))
        self.assertNotIn("secret", str(plan))

    def test_scope_validation(self):
        for sources in ([], [0], ["1"], [1, 1], None):
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                correction.build_reference_plan(self.org.pk, source_strain_ids=sources)
        with self.assertRaises(ValueError):
            correction.build_box_plan(self.org.pk, source_strain_ids=self.sources,
                                      target_strain_ids=[self.src_ach.pk])

    # Box correction -----------------------------------------------------------

    def test_box_plan_without_prepared_targets_is_blocked(self):
        missing = [max(Strain.objects.values_list("pk", flat=True)) + 100]
        plan = self.box_plan(missing)
        self.assertEqual(plan["classification"], correction.CONFLICT)
        self.assert_box_blocked(missing)

    def test_six_boxes_are_corrected_with_complete_audit(self):
        targets = self.targets()
        urls = {pk: box_scan_url(box) for pk, box in self.boxes.items()}
        plan = self.box_plan(targets)
        self.assertEqual(plan["classification"], correction.READY)
        lineage = {e["box_pk"]: e["evidence"]["lineage"] for e in plan["entries"]}
        self.assertEqual(len(lineage[1854]), 2)
        reviewed = correction.fingerprint(plan)
        self.correct(targets, expected_plan_hash=reviewed)
        audits = AuditLog.objects.filter(metadata__operation=correction.BOX_OPERATION)
        self.assertEqual(audits.count(), 6)
        for pk, (code, strain_code, name) in TARGETS.items():
            with self.subTest(box=pk):
                box = Box.objects.select_related("strain__species").get(pk=pk)
                original = self.boxes[pk]
                self.assertEqual(box.strain.species.scientific_name, name)
                self.assertEqual(box.strain.code, strain_code)
                self.assertEqual(box.strain.organization_id, self.org.pk)
                for field in ("global_code", "local_code", "box_number", "status", "thermal_zone_id",
                              "notes", "entered_on", "deactivated_on", "stop_reason", "organization_id"):
                    self.assertEqual(getattr(box, field), getattr(original, field))
                self.assertEqual(box_scan_url(box), urls[pk])
                self.assertEqual(box.tags.get(tag_type="qr").url, urls[pk])
                for serializer in (BoxListSerializer, BoxInventorySerializer, BoxDetailSerializer):
                    self.assertEqual(serializer(box).data["species"]["scientific_name"], name)
                audit = audits.get(object_id=code)
                self.assertEqual((audit.user_id, audit.organization_id, audit.action),
                                 (self.admin.pk, self.org.pk, AuditLog.Action.UPDATE))
                self.assertEqual(audit.metadata["box_id"], pk)
                self.assertEqual(audit.metadata["plan_hash"], reviewed)
                self.assertEqual(correction.fingerprint(audit.metadata["reviewed_plan"]), reviewed)
                self.assertEqual(audit.metadata["before"]["identity"]["strain_pk"], original.strain_id)
                self.assertEqual(audit.metadata["after"]["identity"]["strain_pk"], box.strain_id)
                self.assertEqual(audit.metadata["after"]["identity"]["species_name"], name)
                self.assertEqual(audit.metadata["historical_effect"], correction.HISTORICAL_EFFECT)

    def test_only_six_box_relations_and_new_audits_change(self):
        targets = self.targets()
        before = self.database_snapshot()
        self.correct(targets)
        after = self.database_snapshot()
        for table in before:
            if table not in {Box._meta.db_table, AuditLog._meta.db_table}:
                with self.subTest(table=table):
                    self.assertEqual(after[table], before[table])
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {connection.ops.quote_name(Box._meta.db_table)} LIMIT 0")
            columns = [c[0] for c in cursor.description]
        pk_i, strain_i = columns.index("id"), columns.index("strain_id")
        by_code = {Strain.objects.get(pk=pk).code: pk for pk in targets}
        expected = []
        for row in before[Box._meta.db_table]:
            row = list(row)
            if row[pk_i] in TARGETS:
                row[strain_i] = by_code[TARGETS[row[pk_i]][1]]
            expected.append(tuple(row))
        self.assertEqual(after[Box._meta.db_table], sorted(expected, key=repr))
        old = {row[0]: row for row in before[AuditLog._meta.db_table]}
        self.assertTrue(all(old[row[0]] == row for row in after[AuditLog._meta.db_table] if row[0] in old))
        self.assertEqual(len(after[AuditLog._meta.db_table]), len(old) + 6)
        self.assertEqual(BiologicalMeasurement.objects.filter(
            polyp_count=0, ephyrae_count=0, strobila_count=0, salinity_psu=0).count(), 6)
        self.assertEqual(BiologicalMeasurement.objects.filter(
            strobila_count__isnull=True, salinity_psu__isnull=True).count(), 6)

    def test_shared_references_and_unrelated_boxes_are_preserved(self):
        shared = list(Strain.objects.filter(pk__in=self.sources + [self.ach3.pk]).order_by("pk").values())
        species = list(Species.objects.filter(pk__in=[self.striped.pk, self.white.pk, self.sma_white.pk])
                       .order_by("pk").values())
        unrelated = list(Box.objects.filter(pk__in=[b.pk for b in self.unrelated] + [self.ach3_box.pk])
                         .order_by("pk").values())
        self.correct(self.targets())
        self.assertEqual(list(Strain.objects.filter(pk__in=self.sources + [self.ach3.pk])
                              .order_by("pk").values()), shared)
        self.assertEqual(list(Species.objects.filter(pk__in=[self.striped.pk, self.white.pk, self.sma_white.pk])
                              .order_by("pk").values()), species)
        self.assertEqual(list(Box.objects.filter(pk__in=[b.pk for b in self.unrelated] + [self.ach3_box.pk])
                              .order_by("pk").values()), unrelated)
        self.assertEqual(OrganizationSpeciesCode.objects.get(pk=self.ccp.pk).species_id, self.striped.pk)

    def test_box_apply_is_idempotent(self):
        targets = self.targets()
        self.correct(targets)
        plan = self.box_plan(targets)
        self.assertEqual(plan["classification"], correction.ALREADY_PRESENT)
        self.assertEqual([e["classification"] for e in plan["entries"]], [correction.CORRECT] * 6)
        # The unowned legacy CCP-TPR-1 source had no other own Box: it is reported
        # as no longer eligible here, without contents, and is itself unchanged.
        sources = {row["strain_pk"]: row for row in plan["sources"]}
        self.assertEqual(sources[self.src_tpr.pk], {"strain_pk": self.src_tpr.pk, "eligible": False})
        self.assertEqual(sources[self.src_ach.pk]["remaining_institution_boxes"], 1)
        self.assertEqual(sources[self.src_sma.pk]["remaining_institution_boxes"], 2)
        before = self.database_snapshot()
        output = StringIO()
        call_command("correct_variant_species", "boxes", organization_id=self.org.pk,
                     source_strain_ids=csv(self.sources), target_strain_ids=csv(targets), actor=self.admin.username,
                     expected_plan_hash=correction.fingerprint(plan), apply=True, stdout=output)
        self.assertIn("Corrected 0 Box relation(s)", output.getvalue())
        self.assertEqual(self.database_snapshot(), before)

    def test_box_stale_plan_is_rejected(self):
        targets = self.targets()
        for change in (lambda: Box.objects.filter(pk=2228).update(notes="Changed"),
                       lambda: BiologicalMeasurement.objects.create(box_id=1865, measured_on=date(2026, 10, 1),
                                                                    polyp_count=0, user=self.admin)):
            with self.subTest(change=change):
                reviewed = correction.fingerprint(self.box_plan(targets))
                change()
                self.assert_box_blocked(targets, "Database state changed", expected_plan_hash=reviewed)

    def test_wrong_or_partial_targets_are_not_inferred(self):
        targets = self.targets()
        self.assert_box_blocked(targets[:2])
        self.assertIn("no explicit reviewed target", " ".join(self.box_plan(targets[:2])["conflicts"]))
        foreign = Strain.objects.create(organization=self.other_org, species=self.white, code="CCP-XXX-1")
        self.assertIn("missing or ineligible", " ".join(self.box_plan(targets + [foreign.pk])["conflicts"]))
        self.assert_box_blocked(targets + [foreign.pk])
        unrelated_target = Strain.objects.create(organization=self.org, species=self.white, code="CCP-TPR-9")
        self.assertIn("does not match", " ".join(self.box_plan(targets + [unrelated_target.pk])["conflicts"]))

    def test_target_with_contradictory_assignment_blocks_box_step(self):
        targets = self.targets()
        OrganizationSpeciesCode.objects.create(organization=self.org, species=self.white, code="CWH")
        self.assert_box_blocked(targets)

    def test_unexpected_current_strain_or_foreign_box_blocks_batch(self):
        targets = self.targets()
        other = Strain.objects.create(organization=self.org, species=self.striped, code="CCP-ACH-2-x")
        Box.objects.filter(pk=1855).update(strain=other)
        self.assert_box_blocked(targets)
        Box.objects.filter(pk=1855).update(strain=self.src_ach, organization=self.other_org)
        plan = self.box_plan(targets)
        entry = next(e for e in plan["entries"] if e["box_pk"] == 1855)
        self.assertEqual(entry["conflicts"], ["Box is missing from this institution."])
        self.assertNotIn("box", entry)
        self.assert_box_blocked(targets)

    def test_source_not_listed_blocks(self):
        targets = self.targets()
        with self.assertRaisesMessage(ValueError, "exactly the reviewed source contract"):
            correction.build_box_plan(self.org.pk, source_strain_ids=self.sources[:2], target_strain_ids=targets)

    def test_frozen_transfer_package_blocks_without_modification(self):
        targets = self.targets()
        envelope = TransferEnvelope.objects.create(source_organization=self.org, source_institution_id=uuid.uuid4(),
                                                   source_institution_name="Variant laboratory")
        TransferItem.objects.create(envelope=envelope, source_box_id=2227, source_box_code="SMA-JKA-2.005",
                                    source_strain_code="SMA-JKA-2", species_scientific_name=self.sma_white.scientific_name,
                                    global_strain_id=uuid.uuid4(), declared_polyp_quantity=0)
        self.assertIn("frozen transfer package", " ".join(self.box_plan(targets)["conflicts"]))
        self.assert_box_blocked(targets)

    def test_permissions_are_enforced_for_both_steps(self):
        User = get_user_model()
        member = User.objects.create_user(username="variant-member", email="m@example.org")
        OrganizationMembership.objects.create(organization=self.org, user=member, role="member")
        foreign_admin = User.objects.create_user(username="variant-foreign", email="f@example.org")
        OrganizationMembership.objects.create(organization=self.other_org, user=foreign_admin, role="admin")
        inactive = User.objects.create_user(username="variant-inactive", email="i@example.org", is_active=False)
        OrganizationMembership.objects.create(organization=self.org, user=inactive, role="admin")
        for actor in (member, foreign_admin, inactive):
            with self.subTest(actor=actor.username):
                before = self.database_snapshot()
                with self.assertRaises(PermissionDenied):
                    self.prepare(actor=actor)
                self.assertEqual(self.database_snapshot(), before)
        targets = self.targets()
        for actor in (member, foreign_admin, inactive):
            with self.subTest(actor=actor.username):
                before = self.database_snapshot()
                with self.assertRaises(PermissionDenied):
                    self.correct(targets, actor=actor)
                self.assertEqual(self.database_snapshot(), before)
        OrganizationMembership.objects.filter(user=self.admin).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.correct(targets)

    def test_locks_are_taken_before_the_plan_is_rebuilt(self):
        targets = self.targets()
        calls = []
        lock, build = correction._lock, correction.build_box_plan
        with patch.object(correction, "_lock", side_effect=lambda *a: calls.append("lock") or lock(*a)), \
                patch.object(correction, "build_box_plan",
                             side_effect=lambda *a, **k: calls.append("plan") or build(*a, **k)):
            correction.apply_box_plan(self.org.pk, source_strain_ids=self.sources, target_strain_ids=targets,
                                      actor=self.admin, expected_plan_hash=correction.fingerprint(build(
                                          self.org.pk, source_strain_ids=self.sources, target_strain_ids=targets)))
        self.assertEqual(calls, ["lock", "plan"])

    def test_last_audit_failure_rolls_back_every_relation(self):
        targets = self.targets()
        before = self.database_snapshot()
        create, calls = AuditLog.objects.create, []

        def fail_last(**kwargs):
            calls.append(kwargs)
            if len(calls) == 6:
                raise RuntimeError("Audit unavailable")
            return create(**kwargs)

        with patch.object(correction.AuditLog.objects, "create", side_effect=fail_last):
            with self.assertRaisesMessage(RuntimeError, "Audit unavailable"):
                self.correct(targets)
        self.assertEqual(self.database_snapshot(), before)

    def test_command_flags_and_end_to_end(self):
        with self.assertRaisesMessage(CommandError, "--target-strain-ids"):
            call_command("correct_variant_species", "boxes", organization_id=self.org.pk,
                         source_strain_ids=csv(self.sources), stdout=StringIO())
        plan = self.ref_plan()
        with self.assertRaisesMessage(CommandError, "--actor"):
            call_command("correct_variant_species", "references", organization_id=self.org.pk,
                         source_strain_ids=csv(self.sources), apply=True,
                         expected_plan_hash=correction.fingerprint(plan), stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("correct_variant_species", "references", organization_id=self.org.pk,
                         source_strain_ids=csv(self.sources), apply=True, actor="missing",
                         expected_plan_hash=correction.fingerprint(plan), stdout=StringIO())
        output = StringIO()
        call_command("correct_variant_species", "references", organization_id=self.org.pk,
                     source_strain_ids=csv(self.sources), apply=True, actor=self.admin.username,
                     expected_plan_hash=correction.fingerprint(plan), stdout=output)
        self.assertIn("Prepared target Strain PKs", output.getvalue())
        targets = sorted(self.ref_plan()["groups"][i]["target_strain_pk"] for i in range(3))
        output = StringIO()
        call_command("correct_variant_species", "boxes", organization_id=self.org.pk,
                     source_strain_ids=csv(self.sources), target_strain_ids=csv(targets), apply=True,
                     actor=self.admin.username, expected_plan_hash=correction.fingerprint(self.box_plan(targets)),
                     stdout=output)
        self.assertIn("Corrected 6 Box relation(s)", output.getvalue())

    # Reviewed source contract ---------------------------------------------------

    @contextmanager
    def rolled_back(self):
        with transaction.atomic():
            yield
            transaction.set_rollback(True)

    def assert_reference_blocked(self, message="Unresolved conflicts", expected_plan_hash=None, sources=None):
        before = self.database_snapshot()
        sources = sources or self.sources
        with self.assertRaisesMessage(ValueError, message):
            correction.apply_reference_plan(
                self.org.pk, source_strain_ids=sources, actor=self.admin,
                expected_plan_hash=expected_plan_hash or correction.fingerprint(self.ref_plan()))
        self.assertEqual(self.database_snapshot(), before)

    def same_code_strains(self):
        """Same code as Strain 201 under other Species: one institutional, one legacy."""
        other = Species.objects.create(scientific_name="Chrysaora sp. synthetic")
        return (Strain.objects.create(organization=self.org, species=self.white, code="CCP-ACH-2"),
                Strain.objects.create(species=other, code="CCP-ACH-2"))

    def test_reviewed_source_mapping_is_encoded_exactly(self):
        self.assertEqual(correction.SOURCE_FOR_BOX,
                         {1854: 201, 1855: 201, 1865: 203, 1866: 203, 2227: 242, 2228: 242})
        self.assertEqual(correction.SOURCE_STRAINS,
                         {201: ("CCP-ACH-2", 128), 203: ("CCP-TPR-1", 128), 242: ("SMA-JKA-2", 143)})
        self.assertEqual(tuple(correction.SOURCE_FOR_BOX), correction.BOX_PKS)
        self.assertEqual((correction.ORGANIZATION_PK, str(correction.ORGANIZATION_PORTABLE_ID)),
                         (1, "ebc09e8a-9dbc-4a86-b80d-fddba628d6fe"))

    def test_both_steps_resolve_each_box_to_its_reviewed_source(self):
        expected = {1854: (201, 128), 1855: (201, 128), 1865: (203, 128), 1866: (203, 128),
                    2227: (242, 143), 2228: (242, 143)}
        plans = [self.ref_plan()]
        plans.append(self.box_plan(self.targets()))
        for plan in plans:
            with self.subTest(operation=plan["operation"]):
                self.assertEqual(plan["conflicts"], [])
                self.assertEqual({e["box_pk"]: (e["current"]["strain_pk"], e["current"]["species_pk"])
                                  for e in plan["entries"]}, expected)

    def test_source_parameters_cannot_substitute_or_extend_the_contract(self):
        same_code = self.same_code_strains()[0]
        for sources in ([same_code.pk, 203, 242], [201, 203, 242, same_code.pk], [201, 203]):
            with self.subTest(sources=sources):
                with self.assertRaisesMessage(ValueError, "exactly the reviewed source contract"):
                    correction.build_reference_plan(self.org.pk, source_strain_ids=sources)
                with self.assertRaisesMessage(ValueError, "exactly the reviewed source contract"):
                    correction.build_box_plan(self.org.pk, source_strain_ids=sources, target_strain_ids=[999999])
                self.assert_reference_blocked("exactly the reviewed source contract", "reviewed", sources)
        with self.assertRaisesMessage(CommandError, "exactly the reviewed source contract"):
            call_command("correct_variant_species", "references", organization_id=self.org.pk,
                         source_strain_ids=csv([same_code.pk, 203, 242]), stdout=StringIO())

    def test_other_institution_pk_or_portable_id_is_rejected(self):
        with self.assertRaisesMessage(ValueError, "reviewed institution"):
            correction.build_reference_plan(self.other_org.pk, source_strain_ids=self.sources)
        Organization.objects.filter(pk=self.org.pk).update(portable_id=uuid.uuid4())
        self.assertIn("Institution differs from the reviewed source contract.", self.ref_plan()["conflicts"])
        self.assert_reference_blocked()

    def test_box_on_same_code_strain_is_rejected_at_references(self):
        for impostor in self.same_code_strains():
            with self.subTest(impostor=impostor.pk), self.rolled_back():
                Box.objects.filter(pk=1855).update(strain=impostor)
                plan = self.ref_plan()
                self.assertEqual(plan["classification"], correction.CONFLICT)
                self.assertIn("CCP-ACH-2.002: Current Strain differs from the reviewed source contract.",
                              plan["conflicts"])
                self.assert_reference_blocked()

    def test_box_on_same_code_strain_is_rejected_at_boxes(self):
        targets = self.targets()
        for impostor in self.same_code_strains():
            with self.subTest(impostor=impostor.pk), self.rolled_back():
                Box.objects.filter(pk=1854).update(strain=impostor)
                self.assertIn("CCP-ACH-2.001: Current Strain differs from the reviewed source contract.",
                              self.box_plan(targets)["conflicts"])
                self.assert_box_blocked(targets)
        # Each Box must use its own reviewed source, not another reviewed source.
        with self.rolled_back():
            Box.objects.filter(pk=1854).update(strain_id=203)
            self.assertIn("CCP-ACH-2.001: Current Strain code differs from the reviewed code.",
                          self.box_plan(targets)["conflicts"])
            self.assert_box_blocked(targets)

    def source_drifts(self):
        stray = Species.objects.create(scientific_name="Sanderia sp. synthetic")
        return (
            ("source Strain Species", lambda: Strain.objects.filter(pk=242).update(species=stray)),
            ("source Species name", lambda: Species.objects.filter(pk=128).update(
                scientific_name="Chrysaora chesapeakei")),
            ("source Strain code", lambda: Strain.objects.filter(pk=203).update(code="CCP-TPR-2")),
        )

    def test_unexpected_source_species_or_reference_is_rejected_at_references(self):
        for label, drift in self.source_drifts():
            with self.subTest(label), self.rolled_back():
                drift()
                plan = self.ref_plan()
                self.assertEqual(plan["classification"], correction.CONFLICT)
                self.assertTrue(any("source contract" in c for c in plan["conflicts"]), plan["conflicts"])
                self.assert_reference_blocked()

    def test_unexpected_source_species_or_reference_is_rejected_at_boxes(self):
        targets = self.targets()
        for label, drift in self.source_drifts():
            with self.subTest(label), self.rolled_back():
                drift()
                self.assertTrue(any("source contract" in c for c in self.box_plan(targets)["conflicts"]))
                self.assert_box_blocked(targets)

    def test_contract_drift_after_review_is_rejected_even_with_a_recomputed_hash(self):
        targets = self.targets()
        reviewed = correction.fingerprint(self.box_plan(targets))
        Strain.objects.filter(pk=201).update(species=self.white)
        self.assert_box_blocked(targets, "Database state changed", expected_plan_hash=reviewed)
        self.assert_box_blocked(targets, "Unresolved conflicts")  # fresh hash of the drifted state

    def drift_boxes(self, impostor):
        Box.objects.filter(pk=2228).update(strain_id=203)
        Box.objects.filter(pk=1855).update(strain=impostor)

    def test_concurrent_change_before_lock_is_revalidated_in_box_transaction(self):
        targets = self.targets()
        impostor = self.same_code_strains()[0]
        reviewed = correction.fingerprint(self.box_plan(targets))
        with self.rolled_back():
            self.drift_boxes(impostor)
            drifted = correction.fingerprint(self.box_plan(targets))
        lock = correction._lock

        def writer_then_lock(*args):
            # A write committed just before this transaction acquired its locks.
            self.drift_boxes(impostor)
            return lock(*args)

        before = self.database_snapshot()
        for expected_hash, message in ((reviewed, "Database state changed"), (drifted, "Unresolved conflicts")):
            with self.subTest(message=message), patch.object(correction, "_lock", side_effect=writer_then_lock):
                with self.assertRaisesMessage(ValueError, message):
                    correction.apply_box_plan(self.org.pk, source_strain_ids=self.sources,
                                              target_strain_ids=targets, actor=self.admin,
                                              expected_plan_hash=expected_hash)
            self.assertEqual(self.database_snapshot(), before)
        self.assertFalse(AuditLog.objects.filter(metadata__operation=correction.BOX_OPERATION).exists())

    def test_concurrent_change_before_lock_is_revalidated_in_reference_transaction(self):
        impostor = self.same_code_strains()[1]
        reviewed = correction.fingerprint(self.ref_plan())
        lock = correction._lock

        def writer_then_lock(*args):
            Box.objects.filter(pk=1866).update(strain=impostor)
            return lock(*args)

        before = self.database_snapshot()
        with patch.object(correction, "_lock", side_effect=writer_then_lock):
            with self.assertRaisesMessage(ValueError, "Database state changed"):
                correction.apply_reference_plan(self.org.pk, source_strain_ids=self.sources, actor=self.admin,
                                                expected_plan_hash=reviewed)
        self.assertEqual(self.database_snapshot(), before)
        self.assertFalse(AuditLog.objects.filter(metadata__operation=correction.REFERENCE_OPERATION).exists())
