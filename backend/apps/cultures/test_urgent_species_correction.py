"""Regression coverage for the six reviewed, relation-only identity corrections."""

import json
from copy import copy, deepcopy
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import LocalStrainIdentity, OrganizationSpeciesCode, Species, Strain

from .historical_2026 import species_correction as correction
from .models import Box, BoxLocation, BoxMovement, ThermalZone
from .qr import box_scan_url, sync_box_qr_tag
from .serializers import BoxDetailSerializer, BoxInventorySerializer, BoxListSerializer


class UrgentSpeciesCorrectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Correction laboratory", slug="correction-lab")
        cls.other_org = Organization.objects.create(name="Other laboratory", slug="other-lab")
        cls.admin = get_user_model().objects.create_user(
                    username="correction-admin", email="correction-admin@example.org",
                )
        cls.membership = OrganizationMembership.objects.create(
            organization=cls.org, user=cls.admin, role="admin",
        )
        cls.old_cac_species = Species.objects.create(scientific_name='Chrysaora chesapeakei "white"')
        cls.old_oce_species = Species.objects.create(scientific_name="Pennaria disticha")
        cls.cac_species = Species.objects.create(scientific_name="Chrysaora achlyos")
        cls.oce_species = Species.objects.create(scientific_name="Oceaniidae sp.")
        cls.old_cac = Strain.objects.create(
            organization=cls.org, species=cls.old_cac_species, code="CAC-JKA-2",
        )
        cls.old_oce = Strain.objects.create(
            organization=cls.org, species=cls.old_oce_species, code="OCE-TAI-1",
        )
        cls.cac = Strain.objects.create(
            organization=cls.org, species=cls.cac_species, code="CAC-JKA-2",
        )
        cls.oce = Strain.objects.create(
            organization=cls.org, species=cls.oce_species, code="OCE-TAI-1",
        )
        for strain, code in ((cls.cac, "CAC"), (cls.oce, "OCE")):
            assignment = OrganizationSpeciesCode.objects.create(
                organization=cls.org, species=strain.species, code=code,
            )
            LocalStrainIdentity.objects.create(strain=strain, species_code_assignment=assignment)
        cls.zone = ThermalZone.objects.create(organization=cls.org, name="Current zone")
        cls.previous_zone = ThermalZone.objects.create(organization=cls.org, name="Previous zone")
        cls.codes = tuple(f"CAC-JKA-2.{n:03}" for n in range(1, 6)) + ("OCE-TAI-1.002",)
        cls.boxes = []
        moved_at = datetime(2026, 9, 21, 10, tzinfo=timezone.utc)
        for index, code in enumerate(cls.codes):
            status = (Box.Status.ACTIVE, Box.Status.INACTIVE, Box.Status.PENDING_REVIEW)[index % 3]
            box = Box.objects.create(
                organization=cls.org, global_code=code, local_code=f"local-{index}",
                box_number=code.rsplit(".", 1)[1], strain=cls.old_cac if index < 5 else cls.old_oce,
                thermal_zone=cls.zone if status == Box.Status.ACTIVE else None,
                status=status, notes=f"Historical box {index}", entered_on=date(2026, 1, 5),
                volume_liters="0.00", stop_reason="Reviewed history" if status == Box.Status.INACTIVE else "",
                deactivated_on=date(2026, 9, 22) if status == Box.Status.INACTIVE else None,
            )
            previous = BoxLocation.objects.create(
                box=box, thermal_zone=cls.previous_zone,
                starts_at=moved_at - timedelta(days=7), ends_at=moved_at, notes="Previous location",
            )
            current = BoxLocation.objects.create(
                box=box, thermal_zone=cls.zone, starts_at=moved_at,
                ends_at=None if status == Box.Status.ACTIVE else moved_at + timedelta(days=1),
                notes="Preserved location",
            )
            BoxMovement.objects.create(
                box=box, from_thermal_zone=cls.previous_zone, to_thermal_zone=cls.zone,
                moved_at=moved_at, user=cls.admin, kind=BoxMovement.Kind.ORDINARY,
                origin_location=previous, destination_location=current, notes="Historical movement",
            )
            for measured_on, strobila, salinity in ((date(2026, 9, 7), 0, "0.00"),
                                                   (date(2026, 9, 14), None, None)):
                BiologicalMeasurement.objects.create(
                    box=box, measured_on=measured_on, polyp_count=0, ephyrae_count=0,
                    strobila_count=strobila, salinity_psu=salinity, user=cls.admin,
                    culture_status=BiologicalMeasurement.CultureStatus.GOOD, notes="Keep scientific zero",
                )
            sync_box_qr_tag(box)
            cls.boxes.append(box)
        cls.unrelated = []
        for org, strain, code in (
            (cls.org, cls.old_cac, "CAC-JKA-2.099"),
            (cls.org, cls.old_oce, "OCE-TAI-1.099"),
            (cls.other_org, cls.old_cac, "CAC-JKA-2.100"),
        ):
            cls.unrelated.append(Box.objects.create(
                organization=org, strain=strain, global_code=code, box_number=code.rsplit(".", 1)[1],
            ))
        AuditLog.objects.create(
            organization=cls.org, user=cls.admin, action=AuditLog.Action.ENTRY,
            object_type="box", object_id=cls.codes[0], metadata={"existing": True},
        )

    def database_snapshot(self):
        """Compare persisted rows, not just counts, including non-domain tables."""
        with connection.cursor() as cursor:
            result = {}
            for table in connection.introspection.table_names(cursor):
                cursor.execute(f"SELECT * FROM {connection.ops.quote_name(table)}")
                result[table] = sorted(cursor.fetchall(), key=repr)
            return result

    def plan(self, **overrides):
        options = {"cac_strain_id": self.cac.pk, "oce_strain_id": self.oce.pk}
        options.update(overrides)
        return correction.build_plan(self.org.pk, **options)

    def apply(self, **overrides):
        options = {
            "actor": self.admin, "expected_plan_hash": correction.fingerprint(self.plan()),
            "cac_strain_id": self.cac.pk, "oce_strain_id": self.oce.pk,
        }
        options.update(overrides)
        return correction.apply_plan(self.org.pk, **options)

    def command_options(self):
        return {
            "organization_id": self.org.pk, "cac_strain_id": self.cac.pk,
            "oce_strain_id": self.oce.pk, "actor": self.admin.username,
            "expected_plan_hash": correction.fingerprint(self.plan()), "apply": True,
        }

    def assert_blocked(self, plan=None):
        plan = self.plan() if plan is None else plan
        self.assertTrue(any(entry["classification"] not in {"correction needed", "already correct"}
                            for entry in plan["entries"]))
        before = self.database_snapshot()
        with self.assertRaisesMessage(ValueError, "Unresolved conflicts"):
            self.apply(expected_plan_hash=correction.fingerprint(plan))
        self.assertEqual(self.database_snapshot(), before)

    def test_six_reviewed_relations_and_complete_audit_evidence(self):
        plan = self.plan()
        self.assertEqual(tuple(entry["global_code"] for entry in plan["entries"]), self.codes)
        self.assertEqual([entry["classification"] for entry in plan["entries"]], ["correction needed"] * 6)
        reviewed_hash = correction.fingerprint(plan)
        self.assertEqual(self.apply(expected_plan_hash=reviewed_hash), plan)
        audits = AuditLog.objects.filter(metadata__operation=correction.OPERATION)
        self.assertEqual(audits.count(), 6)
        for entry in plan["entries"]:
            with self.subTest(code=entry["global_code"]):
                box = Box.objects.get(pk=entry["box_pk"])
                target = self.cac if box.global_code.startswith("CAC") else self.oce
                self.assertEqual(box.strain_id, target.pk)
                self.assertEqual(box.strain.species_id, target.species_id)
                audit = audits.get(object_id=box.global_code)
                self.assertEqual((audit.organization_id, audit.user_id, audit.action, audit.object_type),
                                 (self.org.pk, self.admin.pk, AuditLog.Action.UPDATE, "box"))
                self.assertEqual(audit.description, correction.REASON)
                self.assertEqual(audit.metadata["box_id"], box.pk)
                self.assertEqual(audit.metadata["global_code"], box.global_code)
                self.assertEqual(audit.metadata["plan_hash"], reviewed_hash)
                self.assertEqual(audit.metadata["reviewed_plan"], json.loads(json.dumps(plan, default=str)))
                self.assertEqual(correction.fingerprint(audit.metadata["reviewed_plan"]), reviewed_hash)
                self.assertEqual(audit.metadata["previous"], entry["current"])
                self.assertEqual(audit.metadata["new"], entry["proposed"])
                self.assertEqual(audit.metadata["evidence"], entry["evidence"])
                self.assertEqual(audit.metadata["reason"], correction.REASON)
                self.assertEqual(audit.metadata["v2_sha256"], correction.V2_SHA256)
                self.assertEqual(audit.metadata["ledger_sha256"], correction.LEDGER_SHA256)

    def test_plan_and_default_command_are_read_only_across_all_tables(self):
        before = self.database_snapshot()
        plan = self.plan()
        output = StringIO()
        call_command("correct_urgent_species", organization_id=self.org.pk,
                     cac_strain_id=self.cac.pk, oce_strain_id=self.oce.pk, stdout=output)
        self.assertIn("Dry-run only", output.getvalue())
        self.assertIn(f"Plan SHA-256: {correction.fingerprint(plan)}", output.getvalue())
        self.assertEqual(self.database_snapshot(), before)

    def test_command_requires_organization(self):
        before = self.database_snapshot()
        with self.assertRaisesMessage(CommandError, "--organization-id"):
            call_command("correct_urgent_species", stdout=StringIO())
        self.assertEqual(self.database_snapshot(), before)

    def test_apply_command_requires_actor_hash_and_explicit_target_flags(self):
        for flag in ("actor", "expected_plan_hash", "cac_strain_id", "oce_strain_id"):
            with self.subTest(flag=flag):
                options = self.command_options()
                options.pop(flag)
                before = self.database_snapshot()
                with self.assertRaisesMessage(CommandError, "--apply requires"):
                    call_command("correct_urgent_species", stdout=StringIO(), **options)
                self.assertEqual(self.database_snapshot(), before)

    def test_service_requires_actor_and_reviewed_hash(self):
        for options in ({"actor": None}, {"expected_plan_hash": ""}):
            with self.subTest(options=options):
                before = self.database_snapshot()
                with self.assertRaisesMessage(ValueError, "explicit actor and reviewed plan hash"):
                    self.apply(**options)
                self.assertEqual(self.database_snapshot(), before)

    def test_apply_requires_active_user_and_active_admin_in_selected_organization(self):
        User = get_user_model()
        for role, membership_active, user_active, foreign, superuser in (
            ("viewer", True, True, False, False),
            ("lab_technician", True, True, False, False),
            ("admin", False, True, False, False),
            ("admin", True, False, False, False),
            ("admin", True, True, True, False),
            (None, True, True, False, True),
        ):
            with self.subTest(role=role, membership_active=membership_active,
                              user_active=user_active, foreign=foreign, superuser=superuser):
                actor = User.objects.create_user(
                    username=f"actor-{User.objects.count()}",
                    email=f"actor-{User.objects.count()}@example.org",
                    is_active=user_active, is_superuser=superuser,
                )
                if role:
                    OrganizationMembership.objects.create(
                        organization=self.other_org if foreign else self.org, user=actor,
                        role=role, is_active=membership_active,
                    )
                before = self.database_snapshot()
                with self.assertRaises(PermissionDenied):
                    self.apply(actor=actor)
                self.assertEqual(self.database_snapshot(), before)

    def test_command_rejects_unknown_actor(self):
        options = self.command_options()
        options["actor"] = "missing-actor"
        before = self.database_snapshot()
        with self.assertRaises(CommandError):
            call_command("correct_urgent_species", stdout=StringIO(), **options)
        self.assertEqual(self.database_snapshot(), before)

    def test_cross_organization_box_is_not_selected(self):
        Box.objects.filter(pk=self.boxes[0].pk).update(organization=self.other_org)
        plan = self.plan()
        self.assertEqual(plan["entries"][0]["classification"], "missing")
        self.assertIsNone(plan["entries"][0]["box_pk"])
        self.assert_blocked(plan)

    def test_foreign_target_strain_is_rejected(self):
        Strain.objects.filter(pk=self.cac.pk).update(organization=self.other_org)
        plan = self.plan()
        self.assertIn("not eligible", " ".join(plan["entries"][0]["conflicts"]))
        self.assert_blocked(plan)

    def test_unexpected_current_species_blocks_entire_batch(self):
        wrong = Strain.objects.create(organization=self.org, species=self.oce_species, code="CAC-JKA-2")
        Box.objects.filter(pk=self.boxes[0].pk).update(strain=wrong)
        self.assertEqual(self.plan()["entries"][0]["classification"], "unexpected current identity")
        self.assert_blocked()

    def test_unexpected_current_strain_code_blocks_entire_batch(self):
        Strain.objects.filter(pk=self.old_cac.pk).update(code="CAC-JKA-99")
        self.assertEqual(self.plan()["entries"][0]["classification"], "unexpected current identity")
        self.assert_blocked()

    def test_target_requires_exact_reviewed_species_and_strain_code(self):
        for changes in ({"code": "CAC-JKA-99"}, {"species_id": self.oce_species.pk}):
            with self.subTest(changes=changes):
                Strain.objects.filter(pk=self.cac.pk).update(**changes)
                self.assert_blocked()
                Strain.objects.filter(pk=self.cac.pk).update(code="CAC-JKA-2", species=self.cac_species)

    def test_missing_explicit_targets_are_not_inferred_from_codes(self):
        for flag in ("cac_strain_id", "oce_strain_id"):
            for value in (None, max(self.cac.pk, self.oce.pk) + 1000):
                with self.subTest(flag=flag, value=value):
                    plan = self.plan(**{flag: value})
                    self.assertIn("ambiguous target/reference", [e["classification"] for e in plan["entries"]])
                    before = self.database_snapshot()
                    with self.assertRaisesMessage(ValueError, "Unresolved conflicts"):
                        self.apply(expected_plan_hash=correction.fingerprint(plan), **{flag: value})
                    self.assertEqual(self.database_snapshot(), before)

    def test_missing_box_blocks_all_remaining_corrections(self):
        Box.objects.filter(pk=self.boxes[-1].pk).update(global_code="OCE-TAI-1.098")
        self.assertEqual(self.plan()["entries"][-1]["classification"], "missing")
        self.assert_blocked()

    def test_missing_organization_fails_closed(self):
        plan = correction.build_plan(self.other_org.pk + 1000,
                                     cac_strain_id=self.cac.pk, oce_strain_id=self.oce.pk)
        self.assertEqual([e["classification"] for e in plan["entries"]],
                         ["invalid organization or Strain"] * 6)
        options = self.command_options()
        options["organization_id"] = self.other_org.pk + 1000
        options["expected_plan_hash"] = correction.fingerprint(plan)
        before = self.database_snapshot()
        with self.assertRaises(CommandError):
            call_command("correct_urgent_species", stdout=StringIO(), **options)
        self.assertEqual(self.database_snapshot(), before)

    def test_conflicting_local_species_assignments_block_without_rewriting(self):
        assignment = OrganizationSpeciesCode.objects.get(organization=self.org, code="CAC")
        for changes in ({"species_id": self.old_cac_species.pk}, {"code": "BAD"}):
            with self.subTest(changes=changes):
                OrganizationSpeciesCode.objects.filter(pk=assignment.pk).update(**changes)
                self.assert_blocked()
                OrganizationSpeciesCode.objects.filter(pk=assignment.pk).update(
                    species=self.cac_species, code="CAC",
                )

    def test_missing_required_local_species_assignment_blocks(self):
        LocalStrainIdentity.objects.filter(strain=self.oce).delete()
        OrganizationSpeciesCode.objects.filter(organization=self.org, code="OCE").delete()
        self.assert_blocked()

    def test_reviewed_hash_is_stale_after_box_metadata_change(self):
        reviewed_hash = correction.fingerprint(self.plan())
        Box.objects.filter(pk=self.boxes[0].pk).update(notes="Changed after review")
        before = self.database_snapshot()
        with self.assertRaisesMessage(ValueError, "Database state changed"):
            self.apply(expected_plan_hash=reviewed_hash)
        self.assertEqual(self.database_snapshot(), before)

    def test_reviewed_hash_is_stale_after_current_or_target_strain_change(self):
        for strain in (self.old_cac, self.cac, self.old_oce, self.oce):
            with self.subTest(strain=strain.pk):
                reviewed_hash = correction.fingerprint(self.plan())
                Strain.objects.filter(pk=strain.pk).update(notes="Changed after review")
                before = self.database_snapshot()
                with self.assertRaisesMessage(ValueError, "Database state changed"):
                    self.apply(expected_plan_hash=reviewed_hash)
                self.assertEqual(self.database_snapshot(), before)
                Strain.objects.filter(pk=strain.pk).update(notes="")

    def test_last_audit_failure_rolls_back_all_six_relations_and_prior_audits(self):
        before = self.database_snapshot()
        create = AuditLog.objects.create
        calls = []

        def fail_last_audit(**kwargs):
            calls.append(kwargs)
            if len(calls) == 6:
                self.assertEqual(list(Box.objects.filter(global_code__in=self.codes).order_by("global_code")
                                      .values_list("strain_id", flat=True)), [self.cac.pk] * 5 + [self.oce.pk])
                raise RuntimeError("Audit unavailable")
            return create(**kwargs)

        with patch.object(correction.AuditLog.objects, "create", side_effect=fail_last_audit):
            with self.assertRaisesMessage(RuntimeError, "Audit unavailable"):
                self.apply()
        self.assertEqual(len(calls), 6)
        self.assertEqual(self.database_snapshot(), before)

    def test_second_apply_with_fresh_review_is_noop_without_duplicate_audits(self):
        self.apply()
        plan = self.plan()
        self.assertEqual([e["classification"] for e in plan["entries"]], ["already correct"] * 6)
        before = self.database_snapshot()
        output = StringIO()
        call_command("correct_urgent_species", stdout=output, **self.command_options())
        self.assertIn("Corrected 0 Box relation(s)", output.getvalue())
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual(AuditLog.objects.filter(metadata__operation=correction.OPERATION).count(), 6)

    def test_only_target_box_strain_relations_and_new_audits_change(self):
        before = self.database_snapshot()
        self.apply()
        after = self.database_snapshot()
        for table in before:
            if table not in {Box._meta.db_table, AuditLog._meta.db_table}:
                with self.subTest(table=table):
                    self.assertEqual(after[table], before[table])

        # Compare every Box field except the one explicitly approved for correction.
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {connection.ops.quote_name(Box._meta.db_table)} LIMIT 0")
            columns = [column[0] for column in cursor.description]
        strain_index = columns.index("strain_id")
        pk_index = columns.index("id")
        targets = {box.pk: self.cac.pk if index < 5 else self.oce.pk
                   for index, box in enumerate(self.boxes)}
        expected = []
        for row in before[Box._meta.db_table]:
            row = list(row)
            if row[pk_index] in targets:
                row[strain_index] = targets[row[pk_index]]
            expected.append(tuple(row))
        self.assertEqual(after[Box._meta.db_table], sorted(expected, key=repr))
        old_audits = {row[0]: row for row in before[AuditLog._meta.db_table]}
        for row in after[AuditLog._meta.db_table]:
            if row[0] in old_audits:
                self.assertEqual(row, old_audits[row[0]])
        self.assertEqual(len(after[AuditLog._meta.db_table]), len(old_audits) + 6)
        self.assertEqual(BiologicalMeasurement.objects.filter(polyp_count=0, ephyrae_count=0,
                                                             strobila_count=0, salinity_psu=0).count(), 6)
        self.assertEqual(BiologicalMeasurement.objects.filter(strobila_count__isnull=True,
                                                             salinity_psu__isnull=True).count(), 6)

    def test_qr_urls_stay_stable_and_api_serializers_show_persisted_species(self):
        urls = {box.pk: box_scan_url(box) for box in self.boxes}
        self.apply()
        for original in self.boxes:
            box = Box.objects.select_related("strain__species").get(pk=original.pk)
            target = self.cac if box.global_code.startswith("CAC") else self.oce
            with self.subTest(box=box.global_code):
                self.assertEqual(box_scan_url(box), urls[box.pk])
                self.assertEqual(box.tags.get(tag_type="qr").url, urls[box.pk])
                for serializer in (BoxListSerializer, BoxInventorySerializer, BoxDetailSerializer):
                    data = serializer(box).data
                    self.assertEqual(data["species"]["id"], target.species_id)
                    self.assertEqual(data["species"]["scientific_name"], target.species.scientific_name)
                    if serializer is not BoxInventorySerializer:
                        self.assertEqual(data["strain"]["id"], target.pk)
                    if serializer is BoxDetailSerializer:
                        self.assertEqual(data["scan_url"], urls[box.pk])

    def test_unowned_current_strain_with_local_identity_is_rejected(self):
        for strain, code in ((self.old_cac, "CCW"), (self.old_oce, "PDI")):
            with self.subTest(strain=strain.pk):
                assignment = OrganizationSpeciesCode.objects.create(
                    organization=self.org, species=strain.species, code=code,
                )
                local = LocalStrainIdentity.objects.create(
                    strain=strain, species_code_assignment=assignment,
                )
                Strain.objects.filter(pk=strain.pk).update(organization=None)
                plan = self.plan()
                affected = [entry for entry in plan["entries"]
                            if entry["current"]["strain_pk"] == strain.pk]
                self.assertTrue(affected)
                for entry in affected:
                    self.assertEqual(entry["classification"], "invalid organization or Strain")
                    self.assertIn(f"Strain {strain.pk} has inconsistent local identity.", entry["conflicts"])
                    self.assertFalse(any("not eligible" in conflict for conflict in entry["conflicts"]))
                self.assert_blocked(plan)
                Strain.objects.filter(pk=strain.pk).update(organization=self.org)
                local.delete()
                assignment.delete()

    def test_unowned_target_with_local_identity_is_rejected_despite_institutional_witness(self):
        for strain in (self.cac, self.oce):
            with self.subTest(strain=strain.pk):
                witness = Box.objects.create(
                    organization=self.org, strain=strain,
                    global_code=f"{strain.code}.098", box_number="098",
                )
                Strain.objects.filter(pk=strain.pk).update(organization=None)
                plan = self.plan()
                affected = [entry for entry in plan["entries"]
                            if entry["proposed"]["strain_pk"] == strain.pk]
                self.assertTrue(affected)
                for entry in affected:
                    self.assertEqual(entry["classification"], "invalid organization or Strain")
                    self.assertIn(f"Strain {strain.pk} has inconsistent local identity.", entry["conflicts"])
                    self.assertFalse(any("not eligible" in conflict for conflict in entry["conflicts"]))
                self.assert_blocked(plan)
                Strain.objects.filter(pk=strain.pk).update(organization=self.org)
                witness.delete()

    def test_unowned_legacy_strains_without_local_identity_work_with_target_witnesses(self):
        LocalStrainIdentity.objects.filter(strain__in=[self.cac, self.oce]).delete()
        Strain.objects.filter(pk__in=[self.old_cac.pk, self.old_oce.pk, self.cac.pk, self.oce.pk]).update(
            organization=None,
        )
        witnesses = []
        for strain in (self.cac, self.oce):
            witnesses.append(Box.objects.create(
                organization=self.org, strain=strain,
                global_code=f"{strain.code}.098", box_number="098",
            ))
        witnesses_before = list(Box.objects.filter(pk__in=[box.pk for box in witnesses]).order_by("pk").values())
        plan = self.plan()
        self.assertEqual([entry["classification"] for entry in plan["entries"]], ["correction needed"] * 6)
        self.assertTrue(all(entry["local_identities"] == [None, None] for entry in plan["entries"]))
        self.apply(expected_plan_hash=correction.fingerprint(plan))
        self.assertEqual(list(Box.objects.filter(global_code__in=self.codes).order_by("global_code")
                              .values_list("strain_id", flat=True)), [self.cac.pk] * 5 + [self.oce.pk])
        self.assertEqual(list(Box.objects.filter(pk__in=[box.pk for box in witnesses]).order_by("pk").values()),
                         witnesses_before)
        self.assertEqual(AuditLog.objects.filter(metadata__operation=correction.OPERATION).count(), 6)
        self.assertFalse(Strain.objects.filter(pk__in=[self.cac.pk, self.oce.pk],
                                              organization__isnull=False).exists())

    def test_exact_duplicate_species_and_code_is_rejected_by_database(self):
        for strain in (self.old_cac, self.old_oce, self.cac, self.oce):
            for organization in (self.org, self.other_org, None):
                with self.subTest(strain=strain.pk, organization=organization):
                    before = self.database_snapshot()
                    with self.assertRaises(IntegrityError), transaction.atomic():
                        Strain.objects.create(
                            organization=organization, species=strain.species, code=strain.code,
                        )
                    self.assertEqual(self.database_snapshot(), before)

    def test_explicit_wrong_target_pk_is_rejected_instead_of_guessing_valid_reference(self):
        for flag, target in (("cac_strain_id", self.cac), ("oce_strain_id", self.oce)):
            with self.subTest(flag=flag):
                wrong = Strain.objects.create(
                    organization=self.org, species=target.species, code=f"{target.code}-WRONG",
                )
                options = self.command_options()
                options[flag] = wrong.pk
                plan = self.plan(**{flag: wrong.pk})
                affected = [entry for entry in plan["entries"]
                            if entry["global_code"].startswith(target.code + ".")]
                self.assertTrue(affected)
                for entry in affected:
                    self.assertEqual(entry["proposed"]["strain_pk"], wrong.pk)
                    self.assertEqual(entry["classification"], "invalid organization or Strain")
                    self.assertIn("Target Species or exact Strain code does not match the reviewed reference.",
                                  entry["conflicts"])
                options["expected_plan_hash"] = correction.fingerprint(plan)
                before = self.database_snapshot()
                with self.assertRaisesMessage(CommandError, "Unresolved conflicts"):
                    call_command("correct_urgent_species", stdout=StringIO(), **options)
                self.assertEqual(self.database_snapshot(), before)

    def test_incomplete_initial_locked_selection_rejects_before_plan_reresolves_boxes(self):
        reviewed_hash = correction.fingerprint(self.plan())
        before = self.database_snapshot()
        # The row still exists for ordinary reads; only the initial lock selection omits it.
        locked_selection = Box.objects.exclude(pk=self.boxes[0].pk)
        with patch.object(correction.Box.objects, "select_for_update", return_value=locked_selection) as lock, \
                patch.object(correction, "build_plan", wraps=correction.build_plan) as build:
            with self.assertRaisesMessage(
                ValueError,
                "Unresolved conflicts: all six Boxes must be present in the locked selection.",
            ):
                correction.apply_plan(
                    self.org.pk, actor=self.admin, expected_plan_hash=reviewed_hash,
                    cac_strain_id=self.cac.pk, oce_strain_id=self.oce.pk,
                )
            lock.assert_called_once_with()
            build.assert_not_called()
        self.assertEqual(Box.objects.filter(organization=self.org, global_code__in=self.codes).count(), 6)
        self.assertEqual(self.database_snapshot(), before)

    def test_duplicate_global_code_is_rejected_across_organizations(self):
        before = self.database_snapshot()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Box.objects.create(
                organization=self.other_org, strain=self.old_cac,
                global_code=self.codes[0], box_number="duplicate",
            )
        self.assertEqual(self.database_snapshot(), before)

    def test_unexpected_six_row_locked_sets_fail_before_plan_reconstruction(self):
        reviewed_hash = correction.fingerprint(self.plan())
        for unexpected_code in (self.unrelated[0].global_code, self.codes[1]):
            with self.subTest(unexpected_code=unexpected_code):
                # Simulate an invalid query result, not a reachable duplicate in the database.
                rows = [copy(box) for box in self.boxes]
                rows[0].global_code = unexpected_code
                before = self.database_snapshot()
                with patch.object(correction.Box.objects, "select_for_update") as lock, \
                        patch.object(correction, "build_plan") as build:
                    lock.return_value.filter.return_value.order_by.return_value = rows
                    with self.assertRaisesMessage(ValueError, "all six Boxes must be present"):
                        correction.apply_plan(
                            self.org.pk, actor=self.admin, expected_plan_hash=reviewed_hash,
                            cac_strain_id=self.cac.pk, oce_strain_id=self.oce.pk,
                        )
                    build.assert_not_called()
                self.assertEqual(self.database_snapshot(), before)

    def test_reconstructed_plan_pk_mapping_must_match_locked_rows_even_with_matching_hash(self):
        original = self.plan()
        for replacement_pk in (self.unrelated[0].pk, self.boxes[1].pk, None):
            with self.subTest(replacement_pk=replacement_pk):
                plan = deepcopy(original)
                plan["entries"][0]["box_pk"] = replacement_pk
                before = self.database_snapshot()
                with patch.object(correction, "build_plan", return_value=plan):
                    with self.assertRaisesMessage(ValueError, "planned Box identities do not match"):
                        correction.apply_plan(
                            self.org.pk, actor=self.admin,
                            expected_plan_hash=correction.fingerprint(plan),
                            cac_strain_id=self.cac.pk, oce_strain_id=self.oce.pk,
                        )
                self.assertEqual(self.database_snapshot(), before)

    def test_incomplete_or_unexpected_reconstructed_plan_codes_fail_closed(self):
        original = self.plan()
        malformed_entries = (
            original["entries"][:-1],
            original["entries"] + [original["entries"][0]],
            original["entries"][:-1] + [original["entries"][0]],
        )
        for entries in malformed_entries:
            with self.subTest(codes=[entry["global_code"] for entry in entries]):
                plan = deepcopy(original)
                plan["entries"] = deepcopy(entries)
                before = self.database_snapshot()
                with patch.object(correction, "build_plan", return_value=plan):
                    with self.assertRaisesMessage(ValueError, "planned Box identities do not match"):
                        correction.apply_plan(
                            self.org.pk, actor=self.admin,
                            expected_plan_hash=correction.fingerprint(plan),
                            cac_strain_id=self.cac.pk, oce_strain_id=self.oce.pk,
                        )
                self.assertEqual(self.database_snapshot(), before)

    def test_unrelated_boxes_sharing_old_strains_are_unchanged(self):
        before = list(Box.objects.filter(pk__in=[box.pk for box in self.unrelated]).order_by("pk").values())
        strains_before = list(Strain.objects.order_by("pk").values())
        self.apply()
        self.assertEqual(list(Box.objects.filter(pk__in=[box.pk for box in self.unrelated])
                              .order_by("pk").values()), before)
        self.assertEqual(list(Strain.objects.order_by("pk").values()), strains_before)
