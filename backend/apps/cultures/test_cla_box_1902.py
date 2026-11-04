"""Isolated synthetic regressions for the single reviewed CLA Box 1902 correction."""

import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.test import TestCase

from apps.accounts.models import OrganizationMembership
from apps.audit.models import Alert, AuditLog
from apps.measurements.models import BiologicalMeasurement, DailyTemperature, Observation
from apps.organizations.models import Organization
from apps.taxonomy.models import (
    BiologicalProvenance, LocalStrainIdentity, OrganizationProvenanceCode,
    OrganizationSpeciesCode, Species, Strain,
)

from .historical_2026 import cla_box_1902 as correction
from .historical_2026 import identity_execution as execution
from .historical_2026 import identity_reconciliation as reconciliation
from .historical_2026 import identity_references as references
from .models import (
    Box, BoxCodeNamespace, BoxLineage, BoxLocation, BoxMovement, BoxTransfer,
    SubcultureEvent, ThermalZone,
)
from .qr import box_scan_url, sync_box_qr_tag


OPERATION = "cla_box_1902_identity_20261103_v1"
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
        cls.target_species = Species.objects.create(pk=132, scientific_name="Chrysaora lactea")
        cls.source = Strain.objects.create(
            pk=213, organization=None, species=cls.source_species, code="CLA-JKA-1",
            notes=PRIVATE_NOTE,
        )
        cls.target = Strain.objects.create(
            pk=214, organization=cls.org, species=cls.target_species, code="CLC-JKA-1",
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

    def snapshot(self):
        with connection.cursor() as cursor:
            result = {}
            for table in connection.introspection.table_names(cursor):
                cursor.execute(f"SELECT * FROM {connection.ops.quote_name(table)}")
                result[table] = sorted(cursor.fetchall(), key=repr)
            return result

    def options(self, **overrides):
        result = dict(
            organization_id=1, box_id=1902, source_strain_id=213,
            source_species_id=131, target_strain_id=self.target.pk,
        )
        result.update(overrides)
        return result

    def plan(self, **overrides):
        return correction.build_plan(**self.options(**overrides))

    def apply(self, *, plan=None, actor=None, **overrides):
        options = self.options()
        options.update(
            actor=self.admin if actor is None else actor,
            expected_plan_hash=correction.fingerprint(self.plan() if plan is None else plan),
        )
        options.update(overrides)
        return correction.apply_plan(**options)

    def assert_classification(self, plan, expected):
        self.assertEqual(plan["classification"], expected, plan)
        if expected == "BLOCKED":
            self.assertTrue(plan["conflicts"], plan)
        else:
            self.assertEqual(plan["conflicts"], [], plan)

    def assert_blocked(self, **overrides):
        before = self.snapshot()
        options = self.options(**overrides)
        fixed_scope = dict(organization_id=1, box_id=1902, source_strain_id=213, source_species_id=131)
        invalid_scope = (
            any(type(value) is not int for value in options.values())
            or any(options[key] != value for key, value in fixed_scope.items())
            or options["target_strain_id"] <= 0
            or options["target_strain_id"] == 213
        )
        if invalid_scope:
            # An invalid explicit scope must fail before entering any DB session.
            with self.assertNumQueries(0), self.assertRaises(ValueError):
                self.plan(**overrides)
            with self.assertNumQueries(0), self.assertRaises(ValueError):
                correction.apply_plan(**options, actor=self.admin, expected_plan_hash="0" * 64)
        else:
            # Persisted-state conflicts must still produce a reviewable BLOCKED plan.
            plan = self.plan(**overrides)
            self.assert_classification(plan, "BLOCKED")
            with self.assertRaises(ValueError):
                self.apply(plan=plan, **overrides)
        self.assertEqual(self.snapshot(), before)

    def receipt(self):
        return AuditLog.objects.get(
            organization=self.org, object_type="cla_box_1902_correction", object_id="1902",
            metadata__operation=OPERATION,
        )


class ClaBox1902Tests(ClaBox1902Fixture, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.create_fixture()

    def test_plan_is_deterministic_json_and_read_only_with_shared_source(self):
        before = self.snapshot()
        plan = self.plan()
        self.assert_classification(plan, "READY")
        self.assertEqual(plan["operation"], OPERATION)
        self.assertEqual(execution.BOX_1902_OPERATION, OPERATION)
        self.assertIs(correction.fingerprint, execution.fingerprint)
        self.assertEqual(json.loads(json.dumps(plan)), plan)
        self.assertEqual(self.plan(), plan)
        self.assertEqual(correction.fingerprint(self.plan()), correction.fingerprint(plan))
        self.assertEqual(self.snapshot(), before)
        self.assertIsNone(self.source.organization_id)

    def test_apply_changes_exactly_two_fields_on_exactly_one_box(self):
        plan = self.plan()
        before = self.snapshot()
        boxes_before = {row["id"]: row for row in Box.objects.order_by("pk").values()}
        old_audits = list(AuditLog.objects.order_by("pk").values())
        url = box_scan_url(self.box)
        self.assertEqual(self.apply(plan=plan), plan)
        expected = deepcopy(boxes_before)
        expected[1902].update(global_code=NEW_CODE, strain_id=self.target.pk)
        self.assertEqual({row["id"]: row for row in Box.objects.order_by("pk").values()}, expected)
        after = self.snapshot()
        mutable = {Box._meta.db_table, AuditLog._meta.db_table, BoxCodeNamespace._meta.db_table}
        for table in before:
            if table not in mutable:
                self.assertEqual(after[table], before[table], table)
        namespaces_before = {row[1]: row for row in before[BoxCodeNamespace._meta.db_table]}
        namespaces_after = {row[1]: row for row in after[BoxCodeNamespace._meta.db_table]}
        self.assertEqual(
            {key: row for key, row in namespaces_before.items() if key != "CLC-JKA-1"},
            {key: row for key, row in namespaces_after.items() if key != "CLC-JKA-1"},
        )
        self.box.refresh_from_db()
        self.assertEqual(self.box.local_code, "CLA-JKA-1.09")
        self.assertEqual(self.box.previous_global_code, "")
        self.assertIsNone(self.box.merged_into_id)
        self.assertEqual(self.box.status, Box.Status.INACTIVE)
        self.assertEqual(box_scan_url(self.box), url)
        self.assertEqual(self.box.tags.get(tag_type="qr").url, url)
        self.assertEqual(list(AuditLog.objects.filter(pk__in=[r["id"] for r in old_audits])
                              .order_by("pk").values()), old_audits)
        measurements = self.box.biological_measurements.order_by("measured_on")
        self.assertEqual(list(measurements.values_list("polyp_count", "ephyrae_count", "strobila_count")),
                         [(0, 0, 0), (0, 0, None)])
        self.assertEqual(list(measurements.values_list("salinity_psu", flat=True)), [0, None])

    def test_own_receipt_is_not_a_full_reconciliation_receipt(self):
        original_full = reconciliation.build_plan(1, family="cla", target_strain_id=self.target.pk)
        self.assertEqual([entry["classification"] for entry in original_full["entries"]], ["READY"] * 10)
        plan = self.plan()
        self.apply(plan=plan)
        receipt = self.receipt()
        self.assertEqual(receipt.user_id, self.admin.pk)
        self.assertEqual(receipt.metadata["plan_hash"], correction.fingerprint(plan))
        self.assertFalse(AuditLog.objects.filter(object_type="identity_reconciliation").exists())
        self.assertFalse(AuditLog.objects.filter(metadata__operation=reconciliation.OPERATION).exists())
        before = self.snapshot()
        full = reconciliation.build_plan(1, family="cla", target_strain_id=self.target.pk)
        entry = next(e for e in full["entries"] if e["old_code"] == OLD_CODE)
        self.assertEqual(entry["box_pk"], 1902)
        self.assertEqual(entry["classification"], "BLOCKED")
        self.assertTrue(any("receipt" in conflict.lower() for conflict in entry["conflicts"]))
        with self.assertRaises(ValueError):
            reconciliation.apply_plan(
                1, family="cla", target_strain_id=self.target.pk, actor=self.admin,
                expected_plan_hash=reconciliation.fingerprint(full),
            )
        self.assertEqual(self.snapshot(), before)

    def test_fresh_review_after_success_is_idempotent_but_old_hash_is_stale(self):
        old_plan = self.plan()
        self.apply(plan=old_plan)
        plan = self.plan()
        self.assert_classification(plan, "ALREADY_CORRECT")
        self.assertNotEqual(correction.fingerprint(plan), correction.fingerprint(old_plan))
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.apply(plan=old_plan)
        self.assertEqual(self.apply(plan=plan), plan)
        self.assertEqual(self.apply(plan=plan), plan)
        self.assertEqual(self.snapshot(), before)
        self.receipt()

    def test_precorrected_box_without_own_receipt_is_blocked(self):
        Box._base_manager.filter(pk=1902).update(global_code=NEW_CODE, strain_id=self.target.pk)
        self.assert_blocked()

    def test_receipt_operation_organization_and_object_id_are_required(self):
        self.apply()
        receipt = self.receipt()
        for changes in (
            {"object_id": "1903"}, {"organization_id": self.other_org.pk},
            {"object_type": "identity_reconciliation"},
            {"metadata": {**receipt.metadata, "operation": reconciliation.OPERATION}},
        ):
            with self.subTest(changes=changes), transaction.atomic():
                AuditLog.objects.filter(pk=receipt.pk).update(**changes)
                self.assert_blocked()
                transaction.set_rollback(True)
        with transaction.atomic():
            AuditLog.objects.filter(pk=receipt.pk).delete()
            self.assert_blocked()
            transaction.set_rollback(True)

    def test_explicit_scope_cannot_select_lookalikes_or_another_organization(self):
        for overrides in (
            {"organization_id": 2}, {"organization_id": 999999},
            {"box_id": self.sentinels[0].pk}, {"box_id": 999999},
            {"source_strain_id": self.target.pk}, {"source_strain_id": 999999},
            {"source_species_id": self.target_species.pk}, {"source_species_id": 999999},
        ):
            with self.subTest(overrides=overrides):
                self.assert_blocked(**overrides)

    def test_exact_persisted_identity_status_and_names_are_guarded(self):
        for model, pk, changes in (
            (Organization, 1, {"name": "Another aquarium"}),
            (Box, 1902, {"organization_id": 2}),
            (Box, 1902, {"strain_id": self.target.pk}),
            (Box, 1902, {"global_code": "CLA-JKA-1.109"}),
            (Box, 1902, {"local_code": "CLA-JKA-1.009"}),
            (Box, 1902, {"status": Box.Status.ACTIVE}),
            (Box, 1902, {"status": Box.Status.PENDING_REVIEW}),
            (Box, 1902, {"previous_global_code": "OLD-JKA-1.009"}),
            (Box, 1902, {"merged_into_id": self.sentinels[0].pk}),
            (Strain, 213, {"code": "CLA-JKA-2"}),
            (Strain, 213, {"species_id": self.target_species.pk}),
            (Strain, 213, {"organization_id": self.other_org.pk}),
            (Species, 131, {"scientific_name": "Cyanea lamarckii variant"}),
        ):
            with self.subTest(model=model.__name__, changes=changes), transaction.atomic():
                model._base_manager.filter(pk=pk).update(**changes)
                self.assert_blocked()
                transaction.set_rollback(True)

    def test_inactive_organization_blocks_correction(self):
        Organization.objects.filter(pk=1).update(is_active=False)
        self.assert_blocked()

    def test_incompatible_source_and_target_number_or_origin_blocks_correction(self):
        for strain_id in (213, self.target.pk):
            for changes in ({"number": 2}, {"origin_code": "FGU"}):
                with self.subTest(strain_id=strain_id, changes=changes), transaction.atomic():
                    Strain.objects.filter(pk=strain_id).update(**changes)
                    self.assert_blocked()
                    transaction.set_rollback(True)

    def test_matching_labels_on_different_source_pks_do_not_authorize_correction(self):
        with transaction.atomic():
            Strain.objects.filter(pk=213).update(code="CLA-JKA-2")
            lookalike = Strain.objects.create(organization=None, species=self.source_species, code="CLA-JKA-1")
            Box._base_manager.filter(pk=1902).update(strain=lookalike)
            self.assert_blocked(source_strain_id=lookalike.pk)
            transaction.set_rollback(True)
        with transaction.atomic():
            Species.objects.filter(pk=131).update(scientific_name="Other synthetic species")
            lookalike = Species.objects.create(scientific_name="Cyanea lamarckii")
            Strain.objects.filter(pk=213).update(species=lookalike)
            self.assert_blocked(source_species_id=lookalike.pk)
            transaction.set_rollback(True)

    def test_missing_or_incorrect_target_is_not_inferred_or_repaired(self):
        for target_id in (None, 999999, 213):
            with self.subTest(target_id=target_id):
                self.assert_blocked(target_strain_id=target_id)
        for changes in (
            {"code": "CLC-JKA-2"}, {"species_id": 131}, {"organization_id": self.other_org.pk},
        ):
            with self.subTest(changes=changes), transaction.atomic():
                Strain.objects.filter(pk=self.target.pk).update(**changes)
                self.assert_blocked()
                transaction.set_rollback(True)
        with transaction.atomic():
            Species.objects.filter(pk=self.target_species.pk).update(scientific_name="Chrysaora lactea variant")
            self.assert_blocked()
            transaction.set_rollback(True)

    def test_approximate_target_species_beside_exact_species_blocks_without_writes(self):
        Species.objects.create(scientific_name="Chrysaora lacta")
        plan = self.plan()
        self.assertTrue(any("ambiguous" in conflict.lower() for conflict in plan["conflicts"]))
        self.assert_blocked()

    def test_missing_target_requires_separate_reference_preparation_before_correction(self):
        self.assertFalse(Box.objects.filter(strain=self.target).exists())
        OrganizationSpeciesCode.objects.filter(organization=self.org, code="CLC").delete()
        Strain.objects.filter(pk=self.target.pk).delete()
        Species.objects.filter(pk=self.target_species.pk).delete()
        self.assertFalse(Species.objects.filter(scientific_name="Chrysaora lactea").exists())
        self.assertFalse(Strain.objects.filter(code="CLC-JKA-1").exists())
        self.assert_blocked()
        before_preparation = self.snapshot()
        boxes_before = {row["id"]: row for row in Box.objects.order_by("pk").values()}
        source_species_before = Species.objects.filter(pk=131).values().get()
        source_strain_before = Strain.objects.filter(pk=213).values().get()
        source_assignment_before = OrganizationSpeciesCode.objects.filter(
            organization=self.org, code="CLA",
        ).values().get()
        url = box_scan_url(self.box)

        reference_plan = references.build_plan(1, family="cla", source_strain_id=213)
        self.assertEqual(reference_plan["classification"], "READY", reference_plan)
        self.assertEqual({entry["reference"] for entry in reference_plan["entries"] if entry["create"]},
                         {"target_species", "target_strain", "target_assignment", "target_local_identity"})
        self.assertEqual(self.snapshot(), before_preparation)
        result = references.apply_plan(
            1, family="cla", source_strain_id=213, actor=self.admin,
            expected_plan_hash=references.fingerprint(reference_plan),
        )
        target_id = result["prepared_target_strain_id"]
        target = Strain.objects.select_related("species").get(pk=target_id)
        self.assertEqual(target.organization_id, 1)
        self.assertEqual(target.species.scientific_name, "Chrysaora lactea")
        self.assertEqual((target.code, target.number, target.origin_code), ("CLC-JKA-1", 1, "JKA"))
        self.assertIsNone(target.origin_id)
        self.assertIsNone(target.global_identity_id)
        local = LocalStrainIdentity.objects.select_related("species_code_assignment").get(strain=target)
        self.assertIsNone(local.provenance_code_assignment_id)
        self.assertEqual(local.species_code_assignment.code, "CLC")
        self.assertEqual(local.species_code_assignment.organization_id, 1)
        self.assertEqual(local.species_code_assignment.species_id, target.species_id)
        self.assertFalse(LocalStrainIdentity.objects.filter(strain_id=213).exists())
        self.assertEqual(Species.objects.filter(pk=131).values().get(), source_species_before)
        self.assertEqual(Strain.objects.filter(pk=213).values().get(), source_strain_before)
        self.assertEqual(OrganizationSpeciesCode.objects.filter(pk=source_assignment_before["id"])
                         .values().get(), source_assignment_before)
        self.assertEqual({row["id"]: row for row in Box.objects.order_by("pk").values()}, boxes_before)
        after_preparation = self.snapshot()
        additions = {
            Species._meta.db_table: 1, Strain._meta.db_table: 1,
            OrganizationSpeciesCode._meta.db_table: 1, LocalStrainIdentity._meta.db_table: 1,
            AuditLog._meta.db_table: 5,
        }
        for table, rows in before_preparation.items():
            self.assertEqual(len(after_preparation[table]), len(rows) + additions.get(table, 0), table)
            self.assertTrue(all(row in after_preparation[table] for row in rows), table)
        self.assertFalse(AuditLog.objects.filter(object_type="cla_box_1902_correction").exists())
        self.assertFalse(AuditLog.objects.filter(object_type="identity_reconciliation").exists())

        correction_plan = self.plan(target_strain_id=target_id)
        self.assert_classification(correction_plan, "READY")
        self.assertEqual(self.apply(plan=correction_plan, target_strain_id=target_id), correction_plan)
        expected_boxes = deepcopy(boxes_before)
        expected_boxes[1902].update(global_code=NEW_CODE, strain_id=target_id)
        self.assertEqual({row["id"]: row for row in Box.objects.order_by("pk").values()}, expected_boxes)
        after_correction = self.snapshot()
        mutable = {Box._meta.db_table, AuditLog._meta.db_table, BoxCodeNamespace._meta.db_table}
        for table in after_preparation.keys() - mutable:
            self.assertEqual(after_correction[table], after_preparation[table], table)
        self.box.refresh_from_db()
        self.assertEqual(self.box.local_code, "CLA-JKA-1.09")
        self.assertEqual(self.box.previous_global_code, "")
        self.assertEqual(box_scan_url(self.box), url)
        self.assertEqual(self.box.tags.get(tag_type="qr").url, url)
        self.assertEqual(self.receipt().metadata["after"]["strain_id"], target_id)
        self.assertFalse(AuditLog.objects.filter(object_type="identity_reconciliation").exists())

    def test_species_assignments_are_required_and_never_rewritten(self):
        for code in ("CLA", "CLC"):
            for action in ("missing", "wrong"):
                with self.subTest(code=code, action=action), transaction.atomic():
                    assignment = OrganizationSpeciesCode.objects.filter(organization=self.org, code=code)
                    if action == "missing":
                        assignment.delete()
                    else:
                        assignment.update(code="ZZZ")
                    self.assert_blocked()
                    transaction.set_rollback(True)

    def test_old_and_new_code_reservations_and_historical_collision_block(self):
        for organization in (self.org, self.other_org):
            for code in (OLD_CODE, NEW_CODE):
                with self.subTest(organization=organization.pk, reserved=code), transaction.atomic():
                    holder = Box.objects.create(
                        organization=organization, strain=self.source,
                        global_code="RES-JKA-1.001", box_number="1", notes="Private collision note",
                    )
                    Box._base_manager.filter(pk=holder.pk).update(previous_global_code=code)
                    self.assert_blocked()
                    transaction.set_rollback(True)
            with self.subTest(organization=organization.pk, collision=NEW_CODE), transaction.atomic():
                holder = Box.objects.create(
                    organization=organization, strain=self.target,
                    global_code="CLC-JKA-1.999", box_number="999", notes="Private collision note",
                )
                # Historical inconsistent data must bypass the ordinary reservation guard.
                Box._base_manager.filter(pk=holder.pk).update(global_code=NEW_CODE)
                self.assert_blocked()
                if organization == self.other_org:
                    self.assertNotIn("Private collision note", json.dumps(self.plan()))
                transaction.set_rollback(True)

    def test_old_global_code_collision_after_success_blocks_fresh_review(self):
        self.apply()
        holder = Box.objects.create(
            organization=self.other_org, strain=self.source,
            global_code="CLA-JKA-1.999", box_number="999",
        )
        Box._base_manager.filter(pk=holder.pk).update(global_code=OLD_CODE)
        self.assert_blocked()

    def test_different_box_pk_with_the_exact_old_labels_is_not_adopted(self):
        Box._base_manager.filter(pk=1902).update(global_code="CLA-JKA-1.109")
        lookalike = Box.objects.create(
            organization=self.org, strain=self.source, global_code=OLD_CODE,
            local_code="CLA-JKA-1.09", box_number="09", status=Box.Status.INACTIVE,
        )
        self.assert_blocked(box_id=lookalike.pk)
        self.assert_blocked()

    def test_wrong_or_missing_review_hash_and_actor_cannot_apply(self):
        for changes in ({"expected_plan_hash": ""}, {"expected_plan_hash": "0" * 64}, {"actor": None}):
            before = self.snapshot()
            options = self.options()
            options.update(actor=self.admin, expected_plan_hash=correction.fingerprint(self.plan()))
            options.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                correction.apply_plan(**options)
            self.assertEqual(self.snapshot(), before)

    def test_only_active_organization_admin_can_apply(self):
        for role, member_active, user_active, foreign, superuser in (
            ("viewer", True, True, False, False),
            ("lab_technician", True, True, False, False),
            ("admin", False, True, False, False),
            ("admin", True, False, False, False),
            ("admin", True, True, True, False),
            (None, True, True, False, True),
        ):
            with self.subTest(role=role, member_active=member_active, user_active=user_active,
                              foreign=foreign, superuser=superuser):
                index = get_user_model().objects.count()
                actor = get_user_model().objects.create_user(
                    username=f"cla-actor-{index}", email=f"cla-actor-{index}@example.org",
                    is_active=user_active, is_superuser=superuser,
                )
                if role:
                    OrganizationMembership.objects.create(
                        user=actor, organization=self.other_org if foreign else self.org,
                        role=role, is_active=member_active,
                    )
                before = self.snapshot()
                with self.assertRaises(PermissionDenied):
                    self.apply(actor=actor)
                self.assertEqual(self.snapshot(), before)

    def test_changed_measurement_location_or_notes_invalidates_review(self):
        measurement = self.box.biological_measurements.order_by("pk").first()
        location = self.box.locations.order_by("pk").first()
        for model, pk, changes in (
            (BiologicalMeasurement, measurement.pk, {"strobila_count": None}),
            (BiologicalMeasurement, measurement.pk, {"polyp_count": 7}),
            (BoxLocation, location.pk, {"notes": "Changed location evidence"}),
            (Box, 1902, {"notes": "Changed hidden note"}),
        ):
            with self.subTest(model=model.__name__, changes=changes), transaction.atomic():
                plan = self.plan()
                model._base_manager.filter(pk=pk).update(**changes)
                self.assertNotEqual(correction.fingerprint(self.plan()), correction.fingerprint(plan))
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.apply(plan=plan)
                self.assertEqual(self.snapshot(), before)
                transaction.set_rollback(True)

    def test_provenance_assignment_or_referenced_state_change_invalidates_review(self):
        original = BiologicalProvenance.objects.create(name="Synthetic historical provenance")
        replacement = BiologicalProvenance.objects.create(name="Different synthetic provenance")
        assignment = OrganizationProvenanceCode.objects.create(
            organization=self.org, biological_provenance=original, code="JKA",
        )
        for model, pk, changes in (
            (OrganizationProvenanceCode, assignment.pk, {"biological_provenance_id": replacement.pk}),
            (BiologicalProvenance, original.pk, {"name": "Changed referenced provenance evidence"}),
        ):
            with self.subTest(model=model.__name__, changes=changes), transaction.atomic():
                plan = self.plan()
                self.assert_classification(plan, "READY")
                model.objects.filter(pk=pk).update(**changes)
                fresh = self.plan()
                self.assert_classification(fresh, "READY")
                self.assertNotEqual(correction.fingerprint(fresh), correction.fingerprint(plan))
                before = self.snapshot()
                with self.assertRaisesMessage(ValueError, "changed since review"):
                    self.apply(plan=plan)
                self.assertEqual(self.snapshot(), before)
                transaction.set_rollback(True)

    def test_new_measurement_invalidates_review_without_losing_zero(self):
        plan = self.plan()
        BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 10, 5), polyp_count=0,
            ephyrae_count=0, strobila_count=None, salinity_psu=None,
        )
        self.assertNotEqual(correction.fingerprint(self.plan()), correction.fingerprint(plan))
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.apply(plan=plan)
        self.assertEqual(self.snapshot(), before)

    def test_receipt_audit_failure_rolls_back_box_namespace_and_other_audits(self):
        plan = self.plan()
        before = self.snapshot()
        create = AuditLog.objects.create
        attempted = []

        def fail_receipt(**kwargs):
            attempted.append(kwargs)
            if kwargs.get("object_type") == "cla_box_1902_correction":
                raise RuntimeError("Single-box receipt unavailable")
            return create(**kwargs)

        with patch.object(AuditLog.objects, "create", side_effect=fail_receipt):
            with self.assertRaisesMessage(RuntimeError, "Single-box receipt unavailable"):
                self.apply(plan=plan)
        self.assertTrue(any(row.get("object_type") == "cla_box_1902_correction" for row in attempted))
        self.assertEqual(self.snapshot(), before)

    def test_ordinary_creation_and_stale_save_cannot_resurrect_old_identity(self):
        stale = Box.objects.get(pk=1902)
        self.apply()
        before = self.snapshot()
        with self.assertRaises(ValidationError):
            Box.objects.create(
                organization=self.org, strain=self.source, global_code=OLD_CODE,
                local_code="CLA-JKA-1.09", box_number="09",
            )
        self.assertEqual(self.snapshot(), before)
        for fields in (None, ["global_code", "strain"]):
            stale.notes = "Must not restore the old identity"
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                stale.save(update_fields=fields)
            self.assertEqual(self.snapshot(), before)

    def command(self, *extra):
        output = StringIO()
        args = []
        for key, value in self.options().items():
            args.extend(("--" + key.replace("_", "-"), str(value)))
        call_command("correct_cla_box_1902", *args, *extra, stdout=output)
        return output.getvalue()

    def test_cli_default_is_deterministic_read_only_summary_without_notes(self):
        plan = self.plan()
        before = self.snapshot()
        output = self.command()
        self.assertEqual(output, self.command())
        self.assertEqual(self.snapshot(), before)
        reported, _ = json.JSONDecoder().raw_decode(output)
        self.assertEqual(reported, correction.summary(plan))
        self.assertEqual(reported["plan_hash"], correction.fingerprint(plan))
        self.assertNotIn(PRIVATE_NOTE, output)
        self.assertNotIn('"notes"', output)
        self.assertIn(PRIVATE_NOTE, json.dumps(plan))

    def test_cli_apply_requires_actor_id_and_reviewed_hash(self):
        plan_hash = correction.fingerprint(self.plan())
        for args in (
            ("--apply",),
            ("--apply", "--actor-id", str(self.admin.pk)),
            ("--apply", "--expected-plan-hash", plan_hash),
            ("--apply", "--actor-id", "0", "--expected-plan-hash", plan_hash),
            ("--apply", "--actor-id", str(self.admin.pk), "--expected-plan-hash", "0" * 64),
        ):
            with self.subTest(args=args):
                before = self.snapshot()
                with self.assertRaises(CommandError):
                    self.command(*args)
                self.assertEqual(self.snapshot(), before)

    def test_cli_apply_reports_only_reviewed_summary_and_fresh_repeat_is_noop(self):
        plan = self.plan()
        output = self.command(
            "--apply", "--actor-id", str(self.admin.pk),
            "--expected-plan-hash", correction.fingerprint(plan),
        )
        reported, _ = json.JSONDecoder().raw_decode(output)
        self.assertEqual(reported, correction.summary(plan))
        self.assertNotIn(PRIVATE_NOTE, output)
        self.assertNotIn('"notes"', output)
        self.receipt()
        fresh = self.plan()
        self.assert_classification(fresh, "ALREADY_CORRECT")
        before = self.snapshot()
        self.command("--apply", "--actor-id", str(self.admin.pk),
                     "--expected-plan-hash", correction.fingerprint(fresh))
        self.assertEqual(self.snapshot(), before)
