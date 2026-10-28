"""Synthetic reference preparation tests; no real PK or database assumptions."""

import json
from datetime import date
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection
from django.test import TestCase

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import LocalStrainIdentity, OrganizationSpeciesCode, Species, Strain

from .historical_2026 import identity_references as references
from .models import Box


class IdentityReferenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Reference QA", slug="reference-qa")
        cls.other = Organization.objects.create(name="Other reference QA", slug="other-reference-qa")
        cls.admin = get_user_model().objects.create_user(
            username="reference-admin", email="reference-admin@example.org",
        )
        cls.membership = OrganizationMembership.objects.create(organization=cls.org, user=cls.admin, role="admin")
        cls.sources = {}
        for family, name, code in (("cla", "Cyanea lamarckii", "CLA"),
                                    ("sme", "Stomolophus meleagris", "SME")):
            species = Species.objects.create(scientific_name=name)
            cls.sources[family] = Strain.objects.create(species=species, code=f"{code}-JKA-1",
                organization=cls.org, number=None, origin_code="JKA", notes="Do not change source")
            box = Box.objects.create(organization=cls.org, strain=cls.sources[family],
                global_code=f"{code}-JKA-1.001", box_number="001", volume_liters="0.00")
            for measured_on, count in ((date(2021, 1, 4), 0), (date(2021, 1, 11), None)):
                BiologicalMeasurement.objects.create(box=box, measured_on=measured_on,
                    polyp_count=0, ephyrae_count=0, strobila_count=count, user=cls.admin)
        cls.sp2 = Species.objects.create(scientific_name="Stomolophus sp.2")
        # This is the global-247 deployment shape, intentionally without fixed PKs.
        cls.shared = Strain.objects.create(species=cls.sp2, code="SSP-JKA-1", number=None,
            organization=None, notes="Global reference must remain unowned and unchanged")
        cls.witnesses = [Box.objects.create(organization=cls.org, strain=cls.shared,
            global_code=f"SSP-JKA-1.{n:03}", box_number=f"{n:03}") for n in (1, 4, 8, 9, 10)]
        cls.foreign = Box.objects.create(organization=cls.other, strain=cls.shared,
            global_code="PRIVATE-OTHER.001", box_number="001", notes="Foreign confidential history")

    def snapshot(self):
        with connection.cursor() as cursor:
            result = {}
            for table in connection.introspection.table_names(cursor):
                cursor.execute(f"SELECT * FROM {connection.ops.quote_name(table)}")
                result[table] = sorted(cursor.fetchall(), key=repr)
            return result

    def plan(self, family="cla", **options):
        scope = {"family": family, "source_strain_id": self.sources[family].pk,
                 "target_strain_id": self.shared.pk if family == "sme" else None}
        scope.update(options)
        org = scope.pop("organization_id", self.org.pk)
        return references.build_plan(org, **scope)

    def apply(self, plan=None, family="cla", **options):
        plan = self.plan(family) if plan is None else plan
        scope = {"family": plan["family"], "source_strain_id": plan["source_strain_id"],
                 "target_strain_id": plan["requested_target_strain_id"], "actor": self.admin,
                 "expected_plan_hash": references.fingerprint(plan)}
        scope.update(options)
        return references.apply_plan(plan["organization_id"], **scope)

    def assert_blocked(self, plan):
        self.assertIn(plan["classification"], {"CONFLICT", "MISSING_REFERENCE"})
        before = self.snapshot()
        with self.assertRaises((ValueError, PermissionDenied)):
            self.apply(plan)
        self.assertEqual(before, self.snapshot())

    def test_cla_readonly_plan_is_deterministic_and_creates_nothing(self):
        before = self.snapshot()
        with patch.object(Species.objects, "create", side_effect=AssertionError("read-only")), \
                patch.object(Strain.objects, "create", side_effect=AssertionError("read-only")), \
                patch.object(OrganizationSpeciesCode.objects, "create", side_effect=AssertionError("read-only")), \
                patch.object(LocalStrainIdentity.objects, "create", side_effect=AssertionError("read-only")):
            plan = self.plan()
            self.assertEqual(plan, self.plan())
        self.assertEqual(plan["classification"], "READY")
        self.assertEqual({e["reference"] for e in plan["entries"] if e["create"]},
            {"target_species", "target_strain", "source_assignment", "target_assignment", "target_local_identity"})
        self.assertTrue(all(e["classification"] == "MISSING_REFERENCE" for e in plan["entries"]))
        self.assertEqual(json.loads(json.dumps(plan)), plan)
        self.assertEqual(before, self.snapshot())

    def test_cla_creates_only_missing_refs_and_preserves_every_existing_row(self):
        before = self.snapshot()
        result = self.apply()
        target = Strain.objects.get(pk=result["prepared_target_strain_id"])
        self.assertEqual(target.code, "CLC-JKA-1")
        self.assertEqual(target.number, 1)
        self.assertEqual(target.origin_code, "JKA")
        self.assertEqual(target.organization_id, self.org.pk)
        self.assertIsNone(target.origin_id)
        self.assertIsNone(target.global_identity_id)
        local = LocalStrainIdentity.objects.get(strain=target)
        self.assertIsNone(local.provenance_code_assignment_id)
        self.assertEqual(local.species_code_assignment.code, "CLC")
        self.assertEqual(local.species_code_assignment.species.scientific_name, "Chrysaora lactea")
        self.assertEqual(OrganizationSpeciesCode.objects.get(organization=self.org, code="CLA").species_id,
                         self.sources["cla"].species_id)
        after = self.snapshot()
        additions = {Species._meta.db_table, Strain._meta.db_table, OrganizationSpeciesCode._meta.db_table,
                     LocalStrainIdentity._meta.db_table, AuditLog._meta.db_table}
        for table, rows in before.items():
            if table in additions:
                self.assertTrue(all(row in after[table] for row in rows), table)
            else:
                self.assertEqual(rows, after[table], table)
        fresh = self.plan(target_strain_id=target.pk)
        self.assertEqual(fresh["classification"], "ALREADY_PRESENT")
        self.apply(fresh)
        self.assertEqual(after, self.snapshot())
        self.assertEqual(self.plan()["classification"], "ALREADY_PRESENT")

    def test_sme_global_target_creates_only_aaa_and_never_local_identity(self):
        before = self.snapshot()
        plan = self.plan("sme")
        self.assertEqual(plan["classification"], "READY")
        self.assertEqual(plan["state"]["global_target_witness_count"], 5)
        self.assertEqual({e["reference"] for e in plan["entries"] if e["create"]},
                         {"source_assignment", "target_assignment"})
        self.apply(plan)
        after = self.snapshot()
        for model in (Box, Species, Strain, LocalStrainIdentity, BiologicalMeasurement):
            self.assertEqual(before[model._meta.db_table], after[model._meta.db_table])
        self.shared.refresh_from_db()
        self.assertIsNone(self.shared.organization_id)
        self.assertFalse(LocalStrainIdentity.objects.filter(strain=self.shared).exists())
        self.assertEqual(OrganizationSpeciesCode.objects.get(organization=self.org, code="SME").species_id,
                         self.sources["sme"].species_id)
        self.assertEqual(OrganizationSpeciesCode.objects.get(organization=self.org, code="SSP").species_id, self.sp2.pk)
        fresh = self.plan("sme")
        self.assertEqual(fresh["classification"], "ALREADY_PRESENT")
        self.apply(fresh)
        self.assertEqual(after, self.snapshot())

    def test_production_shape_global_cla_source_missing_refs_preserves_everything(self):
        # Model the old global-213 reference without assigning any deployment PK.
        source = self.sources["cla"]
        Strain.objects.filter(pk=source.pk).update(organization=None)
        for number in range(2, 11):
            Box.objects.create(organization=self.org, strain=source,
                global_code=f"CLA-JKA-1.{number:03}", box_number=f"{number:03}")
        Box.objects.create(organization=self.other, strain=source,
            global_code="CLA-JKA-1.100", box_number="100", notes="Unchanged foreign source history")
        Box.objects.create(organization=self.org, strain=source,
            global_code="CLA-JKA-1.099", box_number="099", notes="Outside the ten approved histories")
        unrelated_species = Species.objects.create(scientific_name="Aurelia aurita")
        for org, code in ((self.org, "AUR-JKA-1"), (None, "OTHER-JKA-1")):
            Strain.objects.create(organization=org, species=unrelated_species, code=code,
                                  number=1, origin_code="JKA")
        self.assertFalse(Species.objects.filter(scientific_name__icontains="lactea").exists())
        self.assertFalse(OrganizationSpeciesCode.objects.exists())
        self.assertFalse(LocalStrainIdentity.objects.exists())
        before = self.snapshot()
        plan = self.plan()
        self.assertEqual(plan["classification"], "READY")
        self.assertEqual(before, self.snapshot())
        result = self.apply(plan)
        target = Strain.objects.get(pk=result["prepared_target_strain_id"])
        self.assertEqual(target.organization_id, self.org.pk)
        self.assertEqual(target.species.scientific_name, "Chrysaora lactea")
        self.assertEqual((target.code, target.number, target.origin_code), ("CLC-JKA-1", 1, "JKA"))
        self.assertIsNone(target.origin_id)
        self.assertIsNone(target.local_identity.provenance_code_assignment_id)
        self.assertEqual(target.local_identity.species_code_assignment.code, "CLC")
        self.assertEqual(target.local_identity.species_code_assignment.species_id, target.species_id)
        expected_additions = {Species._meta.db_table: 1, Strain._meta.db_table: 1,
                              OrganizationSpeciesCode._meta.db_table: 2, LocalStrainIdentity._meta.db_table: 1,
                              AuditLog._meta.db_table: 6}
        after = self.snapshot()
        for table, rows in before.items():
            self.assertEqual(len(after[table]), len(rows) + expected_additions.get(table, 0), table)
            self.assertTrue(all(row in after[table] for row in rows), table)
        source.refresh_from_db()
        self.assertIsNone(source.organization_id)
        for options in ({}, {"target_strain_id": target.pk}):
            fresh = self.plan(**options)
            self.assertEqual(fresh["classification"], "ALREADY_PRESENT")
            self.apply(fresh)
            self.assertEqual(after, self.snapshot())
        correction = references.reconciliation.build_plan(self.org.pk, family="cla", target_strain_id=target.pk)
        self.assertEqual(len(correction["entries"]), 10)
        self.assertTrue(all(e["classification"] == "READY" for e in correction["entries"]), correction["entries"])
        self.assertEqual(after, self.snapshot())

    def test_production_shape_global_sme_target_with_ssp_eight_nine_ten_coexists(self):
        Strain.objects.filter(pk=self.sources["sme"].pk).update(organization=None)
        for number in (4, 5, 6, 7, 8, 9, 10):
            Box.objects.create(organization=self.org, strain=self.sources["sme"],
                global_code=f"SME-JKA-1.{number:03}", box_number=f"{number:03}")
        self.assertEqual({b.global_code for b in self.witnesses},
                         {"SSP-JKA-1.001", "SSP-JKA-1.004", "SSP-JKA-1.008", "SSP-JKA-1.009", "SSP-JKA-1.010"})
        self.assertFalse(OrganizationSpeciesCode.objects.exists())
        self.assertFalse(LocalStrainIdentity.objects.exists())
        before = self.snapshot()
        plan = self.plan("sme")
        self.assertEqual(plan["classification"], "READY")
        self.assertEqual(plan["state"]["global_target_witness_count"], 5)
        self.apply(plan)
        after = self.snapshot()
        additions = {OrganizationSpeciesCode._meta.db_table: 2, AuditLog._meta.db_table: 3}
        for table, rows in before.items():
            self.assertEqual(len(after[table]), len(rows) + additions.get(table, 0), table)
            self.assertTrue(all(row in after[table] for row in rows), table)
        self.shared.refresh_from_db()
        self.assertIsNone(self.shared.organization_id)
        self.assertFalse(LocalStrainIdentity.objects.filter(strain=self.shared).exists())
        fresh = self.plan("sme")
        self.assertEqual(fresh["classification"], "ALREADY_PRESENT")
        self.apply(fresh)
        correction = references.reconciliation.build_plan(self.org.pk, family="sme", target_strain_id=self.shared.pk)
        self.assertEqual(len(correction["entries"]), 8)
        self.assertTrue(all(e["classification"] == "READY" for e in correction["entries"]), correction["entries"])
        self.assertEqual(after, self.snapshot())

    def test_unrelated_species_number_and_origin_are_not_target_candidates(self):
        species = Species.objects.create(scientific_name="Aurelia aurita")
        strain = Strain.objects.create(organization=self.org, species=species,
                                      code="AUR-JKA-1", number=1, origin_code="JKA")
        for family in ("cla", "sme"):
            plan = self.plan(family)
            self.assertEqual(plan["classification"], "READY")
            self.assertNotIn(strain.pk, {s["id"] for s in plan["state"]["references"]})
        self.apply()
        self.assertEqual(self.plan()["classification"], "ALREADY_PRESENT")

    def test_missing_source_is_not_manufactured(self):
        self.assert_blocked(self.plan(source_strain_id=max(s.pk for s in self.sources.values()) + 10000))

    def test_wrong_source_identity_is_blocked(self):
        self.assert_blocked(self.plan(source_strain_id=self.sources["sme"].pk))

    def test_sme_requires_explicit_existing_target(self):
        self.assert_blocked(self.plan("sme", target_strain_id=None))

    def test_global_target_without_own_witness_is_ineligible(self):
        Box.objects.filter(organization=self.org, strain=self.shared).delete()
        self.assert_blocked(self.plan("sme"))

    def test_global_source_requires_own_witness_without_ownership_claim(self):
        Strain.objects.filter(pk=self.sources["cla"].pk).update(organization=None)
        before = list(Strain.objects.order_by("pk").values())
        self.apply()
        self.assertEqual(before, list(Strain.objects.filter(pk__in=[s["id"] for s in before]).order_by("pk").values()))

    def test_foreign_target_and_source_are_not_disclosed(self):
        Strain.objects.filter(pk=self.shared.pk).update(organization=self.other)
        plan = self.plan("sme")
        self.assertNotIn("Global reference must", json.dumps(plan))
        self.assertNotIn("Foreign confidential", json.dumps(plan))
        self.assert_blocked(plan)
        self.assert_blocked(self.plan(organization_id=self.other.pk))

    def test_other_institution_assignments_are_not_adopted_or_exposed(self):
        OrganizationSpeciesCode.objects.create(organization=self.other, species=self.sources["cla"].species, code="ZZZ")
        plan = self.plan()
        self.assertNotIn("ZZZ", json.dumps(plan))
        self.apply(plan)
        self.assertEqual(OrganizationSpeciesCode.objects.get(organization=self.other).code, "ZZZ")

    def test_conflicting_old_aaa_blocks_instead_of_reassigning(self):
        OrganizationSpeciesCode.objects.create(organization=self.org, species=self.sp2, code="CLA")
        self.assert_blocked(self.plan())

    def test_conflicting_target_aaa_blocks(self):
        OrganizationSpeciesCode.objects.create(organization=self.org, species=self.sp2, code="CLC")
        self.assert_blocked(self.plan())

    def test_species_already_assigned_a_different_prefix_blocks(self):
        OrganizationSpeciesCode.objects.create(organization=self.org, species=self.sources["cla"].species, code="ZZZ")
        self.assert_blocked(self.plan())

    def test_approximate_lactea_spelling_blocks_creation(self):
        for name in ("Chrysaora Lactea", "Chrysaora lacte", "Chrysaora  lactea", "Chrysaora lacta"):
            with self.subTest(name=name):
                species = Species.objects.create(scientific_name=name)
                self.assert_blocked(self.plan())
                species.delete()

    def test_existing_exact_species_is_reused_without_any_edit(self):
        species = Species.objects.create(scientific_name="Chrysaora lactea", notes="Keep taxonomy", is_described=False)
        before = list(Species.objects.values())
        self.apply()
        self.assertEqual(before, list(Species.objects.values()))
        self.assertEqual(Strain.objects.get(code="CLC-JKA-1").species_id, species.pk)

    def test_multiple_reference_candidates_block(self):
        species = Species.objects.create(scientific_name="Chrysaora lactea")
        Strain.objects.create(organization=self.org, species=species, code="CLC-JKA-1", number=1, origin_code="JKA")
        Strain.objects.create(organization=self.org, species=species, code="CLC-alternate", number=1, origin_code="JKA")
        self.assert_blocked(self.plan())

    def test_hidden_exact_reference_blocks_creation_without_disclosing_it(self):
        species = Species.objects.create(scientific_name="Chrysaora lactea")
        Strain.objects.create(organization=self.other, species=species, code="CLC-JKA-1", notes="Private strain note")
        plan = self.plan()
        self.assertNotIn("Private strain note", json.dumps(plan))
        self.assert_blocked(plan)

    def test_eligible_global_cla_target_is_reused_without_claim_or_local_identity(self):
        species = Species.objects.create(scientific_name="Chrysaora lactea")
        target = Strain.objects.create(species=species, code="CLC-JKA-1", organization=None)
        Box.objects.create(organization=self.org, strain=target, global_code="CLC-JKA-1.099", box_number="099")
        before = list(Strain.objects.order_by("pk").values())
        self.apply(self.plan(target_strain_id=target.pk))
        self.assertEqual(before, list(Strain.objects.order_by("pk").values()))
        self.assertFalse(LocalStrainIdentity.objects.filter(strain=target).exists())

    def test_hidden_alternative_reference_blocks_creation(self):
        species = Species.objects.create(scientific_name="Chrysaora lactea")
        Strain.objects.create(species=species, organization=None, code="CLC alternate", number=1, origin_code="JKA")
        self.assert_blocked(self.plan())

    def test_namespace_clash_blocks(self):
        Box.objects.create(organization=self.org, strain=self.sources["cla"], global_code="CLC-JKA-1.099", box_number="099")
        self.assert_blocked(self.plan())

    def test_existing_target_must_be_exact_and_eligible(self):
        self.assert_blocked(self.plan(target_strain_id=self.shared.pk))
        species = Species.objects.create(scientific_name="Chrysaora lactea")
        target = Strain.objects.create(organization=self.org, species=species, code="CLC-JKA-1", number=0, origin_code="")
        before = list(Strain.objects.values())
        self.apply(self.plan(target_strain_id=target.pk))
        self.assertEqual(before, list(Strain.objects.values()))
        self.assertFalse(LocalStrainIdentity.objects.filter(strain=target).exists())

    def test_stale_assignment_reference_and_witness_plans_fail_closed(self):
        plan = self.plan()
        assignment = OrganizationSpeciesCode.objects.create(organization=self.org, species=self.sp2, code="ZZZ")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "changed since review"):
            self.apply(plan)
        self.assertEqual(before, self.snapshot())
        assignment.delete()
        plan = self.plan("sme")
        Strain.objects.filter(pk=self.shared.pk).update(notes="Changed shared reference")
        with self.assertRaisesRegex(ValueError, "changed since review"):
            self.apply(plan)
        plan = self.plan("sme")
        Box.objects.create(organization=self.org, strain=self.shared, global_code="SSP-JKA-1.099", box_number="099")
        with self.assertRaisesRegex(ValueError, "changed since review"):
            self.apply(plan)

    def test_schema_and_execution_binding_participate_in_hash(self):
        plan = self.plan()
        with patch.object(references.reconciliation, "_schema_state", return_value="changed schema"):
            with self.assertRaisesRegex(ValueError, "changed since review"):
                self.apply(plan)
        plan["execution"]["target_hash"] = "different execution target"
        with self.assertRaisesRegex(ValueError, "changed since review"):
            self.apply(plan)

    def test_only_active_database_admin_can_apply(self):
        for role, active in (("viewer", True), ("lab_technician", True), ("admin", False)):
            with self.subTest(role=role, active=active):
                OrganizationMembership.objects.filter(pk=self.membership.pk).update(role=role, is_active=active)
                before = self.snapshot()
                with self.assertRaises(PermissionDenied):
                    self.apply()
                self.assertEqual(before, self.snapshot())
        OrganizationMembership.objects.filter(pk=self.membership.pk).update(role="admin", is_active=True)
        get_user_model().objects.filter(pk=self.admin.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.apply()

    def test_table_locks_precede_authorization_and_revalidation(self):
        events = []
        lock, authorize, build = (references.reconciliation._lock_application_tables,
                                  references.reconciliation._authorize, references._build_plan)
        plan = self.plan()
        def tracked(name, function):
            def run(*args, **kwargs):
                events.append(name)
                return function(*args, **kwargs)
            return run
        with patch.object(references.reconciliation, "_lock_application_tables", tracked("tables", lock)), \
                patch.object(references.reconciliation, "_authorize", tracked("authorize", authorize)), \
                patch.object(references, "_build_plan", tracked("revalidate", build)):
            self.apply(plan)
        self.assertEqual(events[:3], ["tables", "authorize", "revalidate"])

    def test_audit_failure_rolls_back_all_reference_creations(self):
        before = self.snapshot()
        with patch.object(AuditLog.objects, "create", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                self.apply()
        self.assertEqual(before, self.snapshot())

    def test_local_identity_service_failure_rolls_back_all_refs(self):
        before = self.snapshot()
        with patch.object(references, "create_local_strain_identity", side_effect=ValueError("identity conflict")):
            with self.assertRaisesRegex(ValueError, "identity conflict"):
                self.apply()
        self.assertEqual(before, self.snapshot())

    def test_preparation_hash_cannot_be_replayed_after_creation(self):
        plan = self.plan()
        self.apply(plan)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "changed since review"):
            self.apply(plan)
        self.assertEqual(before, self.snapshot())

    def test_receipt_failure_rolls_back_even_after_individual_audits(self):
        before = self.snapshot()
        create = AuditLog.objects.create
        def fail_receipt(**kwargs):
            if kwargs["object_type"] == "identity_reference_preparation":
                raise RuntimeError("receipt unavailable")
            return create(**kwargs)
        with patch.object(AuditLog.objects, "create", side_effect=fail_receipt):
            with self.assertRaisesRegex(RuntimeError, "receipt unavailable"):
                self.apply()
        self.assertEqual(before, self.snapshot())

    def test_collision_rolls_back_species_and_aaa(self):
        before = self.snapshot()
        with patch.object(Strain.objects, "create", side_effect=IntegrityError("concurrent collision")):
            with self.assertRaisesRegex(IntegrityError, "concurrent collision"):
                self.apply()
        self.assertEqual(before, self.snapshot())

    def test_cli_is_safe_readonly_and_apply_is_explicit(self):
        before = self.snapshot()
        out = StringIO()
        call_command("prepare_historical_identity_references", organization_id=self.org.pk, family="sme",
            source_strain_id=self.sources["sme"].pk, target_strain_id=self.shared.pk, stdout=out)
        text = out.getvalue()
        self.assertIn("plan_hash", text)
        for private in ("notes", "witness_boxes", "Foreign confidential", "Do not change source", "Global reference must"):
            self.assertNotIn(private, text)
        self.assertEqual(before, self.snapshot())
        with self.assertRaises(CommandError):
            call_command("prepare_historical_identity_references", organization_id=self.org.pk, family="cla",
                         source_strain_id=self.sources["cla"].pk, apply=True, stdout=StringIO())
        self.assertEqual(before, self.snapshot())
        out = StringIO()
        call_command("prepare_historical_identity_references", organization_id=self.org.pk, family="cla",
            source_strain_id=self.sources["cla"].pk, apply=True, actor_id=self.admin.pk,
            expected_plan_hash=references.fingerprint(self.plan()), stdout=out)
        self.assertIn("prepared_target_strain_id", out.getvalue())
