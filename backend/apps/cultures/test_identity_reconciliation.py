"""Synthetic, isolated regression tests for the reviewed CLA and SME identities."""

import json
from copy import deepcopy
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import OrganizationMembership
from apps.audit.models import Alert, AuditLog
from apps.measurements.models import BiologicalMeasurement, DailyTemperature, Observation
from apps.organizations.models import Organization
from apps.taxonomy.models import (
    GlobalStrainIdentity, LocalStrainIdentity, OrganizationSpeciesCode, Species, Strain,
)

from .historical_2026 import identity_execution as execution
from .historical_2026 import identity_reconciliation as reconciliation
from .management.commands import correct_historical_identities as identity_command
from .models import (
    Box, BoxCodeNamespace, BoxLineage, BoxLocation, BoxMovement, BoxTransfer, SubcultureEvent, ThermalZone,
)
from .qr import box_scan_url, sync_box_qr_tag


# Keep the expected scope independent of the service allowlist.
FAMILIES = {
    "cla": {
        "numbers": tuple(range(1, 11)), "old_code": "CLA", "aaa": "CLC",
        "old_species": "Cyanea lamarckii", "species": "Chrysaora lactea",
    },
    "sme": {
        "numbers": (1, 4, 5, 6, 7, 8, 9, 10), "old_code": "SME", "aaa": "SSP",
        "old_species": "Stomolophus meleagris", "species": "Stomolophus sp.2",
    },
}


def expected_specs(family):
    spec = FAMILIES[family]
    return tuple({
        "family": family,
        "old_code": f"{spec['old_code']}-JKA-1.{number:03}",
        "target_code": f"CLC-JKA-1.{number:03}" if family == "cla" and number >= 9
                       else f"{spec['old_code']}-JKA-1.{number:03}",
        "old_species": spec["old_species"], "species": spec["species"],
        "aaa": spec["aaa"], "strain_code": f"{spec['aaa']}-JKA-1",
        "old_strain_code": f"{spec['old_code']}-JKA-1",
    } for number in spec["numbers"])


class ReconciliationFixture:
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.org = Organization.objects.create(name="Identity QA", slug="identity-qa")
        cls.other_org = Organization.objects.create(name="Other identity QA", slug="other-identity-qa")
        cls.admin = get_user_model().objects.create_user(
            username="identity-admin", email="identity-admin@example.org",
        )
        OrganizationMembership.objects.create(organization=cls.org, user=cls.admin, role="admin")
        cls.zone = ThermalZone.objects.create(organization=cls.org, name="Current QA zone")
        cls.previous_zone = ThermalZone.objects.create(organization=cls.org, name="Previous QA zone")
        cls.sources, cls.targets, cls.boxes, cls.assignments = {}, {}, {}, {}
        moved_at = datetime(2026, 9, 21, 10, tzinfo=timezone.utc)
        for family, spec in FAMILIES.items():
            old_species = Species.objects.create(scientific_name=spec["old_species"])
            target_species = Species.objects.create(scientific_name=spec["species"])
            cls.sources[family] = Strain.objects.create(
                organization=cls.org, species=old_species, code=f"{spec['old_code']}-JKA-1",
                notes="Shared historical strain; do not edit",
            )
            cls.targets[family] = Strain.objects.create(
                organization=cls.org, species=target_species, code=f"{spec['aaa']}-JKA-1",
                number=1, origin_code="JKA", notes="Existing target; no local identity required",
            )
            for species, code in ((old_species, spec["old_code"]), (target_species, spec["aaa"])):
                assignment = OrganizationSpeciesCode.objects.create(
                    organization=cls.org, species=species, code=code,
                )
                cls.assignments[code] = assignment
            for index, entry in enumerate(expected_specs(family)):
                status = (Box.Status.ACTIVE, Box.Status.INACTIVE, Box.Status.PENDING_REVIEW)[index % 3]
                box = Box.objects.create(
                    organization=cls.org, strain=cls.sources[family], global_code=entry["old_code"],
                    local_code=entry["old_code"].replace(".00", ".0"),
                    box_number=entry["old_code"].rsplit(".", 1)[1], status=status,
                    thermal_zone=cls.zone if status == Box.Status.ACTIVE else None,
                    notes="Preserve historical metadata", volume_liters="0.00",
                    entered_on=date(2026, 1, 5),
                    stop_reason="Historical stop" if status == Box.Status.INACTIVE else "",
                    deactivated_on=date(2026, 9, 22) if status == Box.Status.INACTIVE else None,
                    polyp_state_revision=41 + index,
                )
                cls.boxes[entry["old_code"]] = box
                previous = BoxLocation.objects.create(
                    box=box, thermal_zone=cls.previous_zone, starts_at=moved_at - timedelta(days=7),
                    ends_at=moved_at, notes="Previous location evidence",
                )
                current = BoxLocation.objects.create(
                    box=box, thermal_zone=cls.zone, starts_at=moved_at,
                    ends_at=None if status == Box.Status.ACTIVE else moved_at + timedelta(days=1),
                )
                BoxMovement.objects.create(
                    box=box, from_thermal_zone=cls.previous_zone, to_thermal_zone=cls.zone,
                    moved_at=moved_at, user=cls.admin, kind=BoxMovement.Kind.ORDINARY,
                    origin_location=previous, destination_location=current, notes="Keep movement",
                )
                for week, strobila, salinity in ((37, 0, "0.00"), (38, None, None)):
                    BiologicalMeasurement.objects.create(
                        box=box, measured_on=date.fromisocalendar(2026, week, 1),
                        polyp_count=0, ephyrae_count=0, strobila_count=strobila, salinity_psu=salinity,
                        user=cls.admin, notes=None if strobila is None else "Measured zero",
                        culture_status=None, needs_attention=None,
                    )
                Observation.objects.create(box=box, user=cls.admin, notes="Keep observation")
                Alert.objects.create(
                    organization=cls.org, box=box, alert_type="biological", message="Keep alert",
                    created_by=cls.admin,
                )
                BoxTransfer.objects.create(
                    box=box, from_organization=cls.org, to_organization=cls.other_org,
                    polyp_count=0, user=cls.admin, notes="Keep transfer evidence",
                )
                sync_box_qr_tag(box)
            parent, child = [cls.boxes[e["old_code"]] for e in expected_specs(family)[:2]]
            event = SubcultureEvent.objects.create(parent_box=parent, user=cls.admin, notes="Legacy event")
            BoxLineage.objects.create(parent_box=parent, child_box=child, subculture_event=event)
            # Sharing a source strain is not authority to correct another Box.
            for organization, suffix in ((cls.org, "099"), (cls.other_org, "100")):
                Box.objects.create(
                    organization=organization, strain=cls.sources[family],
                    global_code=f"{spec['old_code']}-JKA-1.{suffix}", box_number=suffix,
                )
        DailyTemperature.objects.create(
            thermal_zone=cls.zone, date=date(2026, 9, 21), average_temperature_c="0.00",
        )
        AuditLog.objects.create(
            organization=cls.org, user=cls.admin, action="entry", object_type="box",
            object_id="CLA-JKA-1.009", metadata={"historical": True},
        )

    def database_snapshot(self):
        with connection.cursor() as cursor:
            result = {}
            for table in connection.introspection.table_names(cursor):
                cursor.execute(f"SELECT * FROM {connection.ops.quote_name(table)}")
                result[table] = sorted(cursor.fetchall(), key=repr)
            return result

    def plan(self, **overrides):
        options = {"family": self.family, "target_strain_id": self.targets[self.family].pk}
        options.update(overrides)
        organization_id = options.pop("organization_id", self.org.pk)
        return reconciliation.build_plan(organization_id, **options)

    def apply(self, *, plan=None, **overrides):
        options = {
            "family": self.family, "target_strain_id": self.targets[self.family].pk,
            "actor": self.admin,
            "expected_plan_hash": reconciliation.fingerprint(self.plan() if plan is None else plan),
        }
        options.update(overrides)
        organization_id = options.pop("organization_id", self.org.pk)
        return reconciliation.apply_plan(organization_id, **options)

    def assert_blocked(self, plan=None, **options):
        plan = self.plan(**options) if plan is None else plan
        blocked = [e for e in plan["entries"] if e["classification"] == "BLOCKED"]
        self.assertTrue(blocked, plan)
        self.assertTrue(all(e["conflicts"] for e in blocked), plan)
        before = self.database_snapshot()
        with self.assertRaises(ValueError):
            self.apply(plan=plan, **options)
        self.assertEqual(self.database_snapshot(), before)

    def first_box(self):
        return self.boxes[expected_specs(self.family)[0]["old_code"]]

    def test_specs_are_exact_and_do_not_expand_scope(self):
        self.assertIsInstance(reconciliation.SPECS, tuple)
        expected = expected_specs("cla") + expected_specs("sme")
        keys = set(expected[0])
        actual = tuple({key: entry[key] for key in keys} for entry in reconciliation.SPECS)
        self.assertEqual(actual, expected)

    def test_dry_run_is_json_deterministic_and_read_only_across_all_tables(self):
        before = self.database_snapshot()
        plan = self.plan()
        self.assertEqual(json.loads(json.dumps(plan)), plan)
        self.assertEqual(self.plan(), plan)
        self.assertEqual(reconciliation.fingerprint(self.plan()), reconciliation.fingerprint(plan))
        self.assertEqual(self.database_snapshot(), before)
        self.assertTrue(plan["operation"])
        self.assertEqual(plan["family"], self.family)
        self.assertEqual(plan["target_strain_id"], self.targets[self.family].pk)
        self.assertEqual([e["old_code"] for e in plan["entries"]],
                         [e["old_code"] for e in expected_specs(self.family)])
        self.assertEqual([e["target_code"] for e in plan["entries"]],
                         [e["target_code"] for e in expected_specs(self.family)])
        for entry in plan["entries"]:
            self.assertEqual(entry["classification"], "READY", entry)
            self.assertEqual(entry["conflicts"], [])
            self.assertEqual(entry["box_pk"], self.boxes[entry["old_code"]].pk)
            self.assertIsInstance(entry["box"], dict)
            self.assertIsInstance(entry["proposed"], dict)

    def test_apply_changes_only_reviewed_box_strain_and_explicit_global_codes(self):
        plan = self.plan()
        before = self.database_snapshot()
        boxes_before = {b["id"]: b for b in Box.objects.order_by("pk").values()}
        urls = {b.pk: box_scan_url(b) for b in self.boxes.values()}
        namespaces_before = dict(BoxCodeNamespace.objects.values_list("namespace", "high_water"))
        self.assertEqual(self.apply(plan=plan), plan)
        after = self.database_snapshot()
        mutable_tables = {Box._meta.db_table, AuditLog._meta.db_table}
        if self.family == "cla":
            mutable_tables.add(BoxCodeNamespace._meta.db_table)
        for table in before:
            if table not in mutable_tables:
                self.assertEqual(after[table], before[table], table)
        if self.family == "cla":
            namespaces_after = dict(BoxCodeNamespace.objects.values_list("namespace", "high_water"))
            self.assertEqual(namespaces_after["CLA-JKA-1"], namespaces_before["CLA-JKA-1"])
            self.assertGreaterEqual(namespaces_after["CLC-JKA-1"], 10)
            self.assertGreaterEqual(
                namespaces_after["CLC-JKA-1"], namespaces_before.get("CLC-JKA-1", 0),
            )
            self.assertEqual(
                {key: value for key, value in namespaces_after.items() if key != "CLC-JKA-1"},
                {key: value for key, value in namespaces_before.items() if key != "CLC-JKA-1"},
            )
        expected_boxes = deepcopy(boxes_before)
        for entry in plan["entries"]:
            expected_boxes[entry["box_pk"]].update(
                strain_id=self.targets[self.family].pk, global_code=entry["target_code"],
            )
        self.assertEqual({b["id"]: b for b in Box.objects.order_by("pk").values()}, expected_boxes)
        for box in Box.objects.filter(pk__in=urls):
            self.assertEqual(box_scan_url(box), urls[box.pk])
            self.assertEqual(box.tags.get(tag_type="qr").url, urls[box.pk])
            self.assertEqual(box.previous_global_code, "")
            self.assertIsNone(box.merged_into_id)
        old_audits = {row[0]: row for row in before[AuditLog._meta.db_table]}
        for row in after[AuditLog._meta.db_table]:
            if row[0] in old_audits:
                self.assertEqual(row, old_audits[row[0]])
        self.assertEqual(BiologicalMeasurement.objects.filter(strobila_count=0, salinity_psu=0).count(), 18)
        self.assertEqual(BiologicalMeasurement.objects.filter(
            strobila_count__isnull=True, salinity_psu__isnull=True,
        ).count(), 18)

    def test_batch_receipt_records_exact_pk_mapping_and_after_snapshots(self):
        plan = self.plan()
        self.apply(plan=plan)
        receipt = AuditLog.objects.get(
            organization=self.org, object_type="identity_reconciliation",
            metadata__operation=plan["operation"],
        )
        self.assertEqual(receipt.user_id, self.admin.pk)
        entries = receipt.metadata["entries"]
        self.assertEqual(len(entries), len(plan["entries"]))
        for actual, expected in zip(entries, plan["entries"]):
            for key in ("box_pk", "old_code", "target_code"):
                self.assertEqual(actual[key], expected[key])
            self.assertIsInstance(actual["after"], dict)
            self.assertEqual(actual["after"]["global_code"], expected["target_code"])
            self.assertEqual(actual["after"]["strain_id"], self.targets[self.family].pk)

    def test_fresh_reviewed_rerun_is_noop_without_duplicate_audits(self):
        self.apply()
        plan = self.plan()
        self.assertEqual([e["classification"] for e in plan["entries"]],
                         ["ALREADY_CORRECT"] * len(expected_specs(self.family)))
        before = self.database_snapshot()
        self.assertEqual(self.apply(plan=plan), plan)
        self.assertEqual(self.database_snapshot(), before)

    def test_explicit_actor_and_reviewed_hash_are_required(self):
        for overrides in ({"actor": None}, {"expected_plan_hash": ""}):
            with self.subTest(overrides=overrides):
                before = self.database_snapshot()
                with self.assertRaises(ValueError):
                    self.apply(**overrides)
                self.assertEqual(self.database_snapshot(), before)

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
                actor = get_user_model().objects.create_user(
                    username=f"actor-{get_user_model().objects.count()}",
                    email=f"actor-{get_user_model().objects.count()}@example.org",
                    is_active=user_active, is_superuser=superuser,
                )
                if role:
                    OrganizationMembership.objects.create(
                        user=actor, organization=self.other_org if foreign else self.org,
                        role=role, is_active=member_active,
                    )
                before = self.database_snapshot()
                with self.assertRaises(PermissionDenied):
                    self.apply(actor=actor)
                self.assertEqual(self.database_snapshot(), before)

    def test_foreign_source_box_is_not_selected_or_disclosed(self):
        box = self.first_box()
        Box.objects.filter(pk=box.pk).update(organization=self.other_org, notes="Private foreign notes")
        plan = self.plan()
        entry = next(e for e in plan["entries"] if e["old_code"] == box.global_code)
        self.assertEqual(entry["classification"], "BLOCKED")
        self.assertIsNone(entry["box_pk"])
        self.assertIsNone(entry["box"])
        self.assertNotIn("Private foreign notes", json.dumps(plan))
        self.assert_blocked(plan)

    def test_foreign_owned_source_strain_on_own_box_is_blocked_without_disclosure(self):
        species = Species.objects.create(scientific_name="Private foreign source species")
        strain = Strain.objects.create(
            organization=self.other_org, species=species, code="PRIVATE-SOURCE-1",
            notes="Private foreign source strain notes",
        )
        assignment = OrganizationSpeciesCode.objects.create(
            organization=self.other_org, species=species, code="PVT",
        )
        local = LocalStrainIdentity.objects.create(strain=strain, species_code_assignment=assignment)
        box = self.first_box()
        Box._base_manager.filter(pk=box.pk).update(strain=strain)
        before = self.database_snapshot()
        plan = self.plan()
        self.assertEqual(self.database_snapshot(), before)
        entry = next(e for e in plan["entries"] if e["box_pk"] == box.pk)
        self.assertEqual(entry["classification"], "BLOCKED")
        self.assertTrue(entry["conflicts"])
        self.assertNotIn(strain.pk, [row["id"] for row in plan["strains"]])
        self.assertNotIn(species.pk, [row["id"] for row in plan["species"]])
        self.assertNotIn(local.pk, [row["id"] for row in plan["local_identities"]])
        self.assertNotIn(assignment.pk, [row["id"] for row in plan["assignments"]])
        serialized = json.dumps(plan)
        for private in (strain.code, strain.notes, species.scientific_name, assignment.code):
            self.assertNotIn(private, serialized)
        public_entry = next(e for e in reconciliation.summary(plan)["entries"] if e["box_pk"] == box.pk)
        self.assertIsNone(public_entry["current_strain_code"])
        self.assertIsNone(public_entry["current_species"])
        self.assert_blocked(plan)

    def test_foreign_alert_dependency_is_redacted_and_blocks_family(self):
        alert = Alert.objects.create(
            organization=self.other_org, box=self.first_box(), alert_type="other",
            message="Private foreign alert contents",
        )
        before = self.database_snapshot()
        plan = self.plan()
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual({e["classification"] for e in plan["entries"]}, {"BLOCKED"})
        self.assertNotIn(alert.message, json.dumps(plan))
        self.assertNotIn(alert.pk, [row["id"] for row in plan["dependencies"].get("audit.alert", [])])
        self.assert_blocked(plan)

    def test_foreign_merged_source_dependency_is_redacted_and_blocks_family(self):
        foreign_strain = Strain.objects.create(
            organization=self.other_org, species=self.sources[self.family].species,
            code="PRIVATE-FOREIGN-1", notes="Private foreign strain contents",
        )
        source = Box.objects.create(
            organization=self.other_org, strain=foreign_strain,
            global_code="PRIVATE-FOREIGN-1.777", local_code="Private foreign local code",
            box_number="777", status=Box.Status.INACTIVE,
            merged_into=self.first_box(), notes="Private foreign merged source contents",
        )
        before = self.database_snapshot()
        plan = self.plan()
        self.assertEqual(self.database_snapshot(), before)
        self.assertEqual({e["classification"] for e in plan["entries"]}, {"BLOCKED"})
        serialized = json.dumps(plan)
        for private in (source.global_code, source.local_code, source.notes, foreign_strain.notes):
            self.assertNotIn(private, serialized)
        self.assertNotIn(source.pk, [row["id"] for row in plan["dependencies"].get("cultures.box", [])])
        self.assert_blocked(plan)

    def test_lineage_child_event_with_parent_outside_batch_invalidates_review(self):
        parent = Box.objects.get(
            organization=self.org, global_code=f"{self.sources[self.family].code}.099",
        )
        child = self.first_box()
        event = SubcultureEvent.objects.create(
            parent_box=parent, user=self.admin, notes="External parent event evidence",
        )
        BoxLineage.objects.create(parent_box=parent, child_box=child, subculture_event=event)
        before = self.database_snapshot()
        plan = self.plan()
        self.assertEqual(self.database_snapshot(), before)
        self.assertNotIn(parent.pk, [e["box_pk"] for e in plan["entries"]])
        self.assertEqual({e["classification"] for e in plan["entries"]}, {"READY"})
        saved_event = next(row for row in plan["dependencies"]["cultures.subcultureevent"]
                           if row["id"] == event.pk)
        self.assertEqual(saved_event["parent_box_id"], parent.pk)
        self.assertEqual(saved_event["notes"], event.notes)
        SubcultureEvent.objects.filter(pk=event.pk).update(notes="Event changed after review")
        self.assertNotEqual(reconciliation.fingerprint(self.plan()), reconciliation.fingerprint(plan))
        before = self.database_snapshot()
        with self.assertRaisesMessage(ValueError, "Persisted state changed since review"):
            self.apply(plan=plan)
        self.assertEqual(self.database_snapshot(), before)

    def test_all_precorrected_target_states_without_receipt_block_family(self):
        for entry in expected_specs(self.family):
            Box.objects.filter(pk=self.boxes[entry["old_code"]].pk).update(
                strain_id=self.targets[self.family].pk, global_code=entry["target_code"],
            )
        self.assertFalse(AuditLog.objects.filter(object_type="identity_reconciliation").exists())
        plan = self.plan()
        self.assertEqual({e["classification"] for e in plan["entries"]}, {"BLOCKED"})
        for entry in plan["entries"]:
            self.assertEqual(entry["box"]["strain_id"], self.targets[self.family].pk)
            self.assertEqual(entry["box"]["global_code"], entry["target_code"])
            self.assertTrue(any("receipt" in conflict.lower() for conflict in entry["conflicts"]), entry)
        self.assert_blocked(plan)

    def test_nonrenamed_target_states_lose_idempotence_without_batch_receipt(self):
        self.apply()
        AuditLog.objects.filter(object_type="identity_reconciliation").delete()
        plan = self.plan()
        unchanged_codes = [e for e in plan["entries"] if e["old_code"] == e["target_code"]]
        self.assertTrue(unchanged_codes)
        self.assertEqual({e["classification"] for e in unchanged_codes}, {"BLOCKED"})
        self.assert_blocked(plan)

    def test_different_established_global_strain_identities_block_family(self):
        source_identity = GlobalStrainIdentity.objects.create()
        target_identity = GlobalStrainIdentity.objects.create()
        Strain.objects.filter(pk=self.sources[self.family].pk).update(global_identity=source_identity)
        Strain.objects.filter(pk=self.targets[self.family].pk).update(global_identity=target_identity)
        self.assertNotEqual(source_identity.pk, target_identity.pk)
        plan = self.plan()
        self.assertEqual({e["classification"] for e in plan["entries"]}, {"BLOCKED"})
        for entry in plan["entries"]:
            self.assertTrue(any("global" in conflict.lower() for conflict in entry["conflicts"]), entry)
        self.assert_blocked(plan)

    def command_options(self, **overrides):
        options = {
            "organization_id": self.org.pk, "family": self.family,
            "target_strain_id": self.targets[self.family].pk,
        }
        options.update(overrides)
        return options

    def test_cli_dry_run_needs_no_actor_and_is_deterministic_read_only(self):
        before = self.database_snapshot()
        outputs = []
        for _ in range(2):
            output = StringIO()
            call_command("correct_historical_identities", stdout=output, **self.command_options())
            outputs.append(output.getvalue())
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(self.database_snapshot(), before)
        reported, _ = json.JSONDecoder().raw_decode(outputs[0])
        plan = self.plan()
        self.assertEqual(reported, reconciliation.summary(plan))
        self.assertIn(f"Plan SHA-256: {reconciliation.fingerprint(plan)}", outputs[0])
        self.assertIn("Dry-run only: no writes.", outputs[0])

    def test_cli_summary_withholds_notes_on_dry_run_and_apply(self):
        marker = "Private historical user notes must not reach CLI output"
        box = self.first_box()
        Box.objects.filter(pk=box.pk).update(notes=marker)
        Strain.objects.filter(pk__in=(self.sources[self.family].pk, self.targets[self.family].pk)).update(notes=marker)
        BiologicalMeasurement.objects.filter(box=box).update(notes=marker)
        plan = self.plan()
        self.assertIn(marker, json.dumps(plan))
        required = {
            "old_code", "target_code", "box_pk", "current_strain_id", "current_species",
            "current_strain_code", "classification", "conflicts", "corresponding_ssp_present", "physical_continuity",
        }
        for apply in (False, True):
            with self.subTest(apply=apply):
                output = StringIO()
                options = self.command_options()
                if apply:
                    options.update(apply=True, actor=self.admin.username,
                                   expected_plan_hash=reconciliation.fingerprint(plan))
                before = self.database_snapshot()
                call_command("correct_historical_identities", stdout=output, **options)
                if not apply:
                    self.assertEqual(self.database_snapshot(), before)
                self.assertFalse(marker in output.getvalue(), "CLI output exposes private historical notes")
                reported, _ = json.JSONDecoder().raw_decode(output.getvalue())
                self.assertEqual(reported, reconciliation.summary(plan))
                for entry in reported["entries"]:
                    self.assertTrue(required <= entry.keys(), entry)
                    self.assertNotIn("box", entry)
                    self.assertNotIn("notes", entry)
                self.assertIn(f"Plan SHA-256: {reconciliation.fingerprint(plan)}", output.getvalue())

    def test_cli_hash_binds_hidden_notes_not_only_public_summary(self):
        plan = self.plan()
        Box.objects.filter(pk=self.first_box().pk).update(notes="Private change after review")
        changed = self.plan()
        original_summary = reconciliation.summary(plan)
        changed_summary = reconciliation.summary(changed)
        original_hash = original_summary.pop("plan_hash")
        changed_hash = changed_summary.pop("plan_hash")
        self.assertEqual(original_summary, changed_summary)
        self.assertEqual(original_hash, reconciliation.fingerprint(plan))
        self.assertEqual(changed_hash, reconciliation.fingerprint(changed))
        self.assertNotEqual(original_hash, changed_hash)
        before = self.database_snapshot()
        with self.assertRaises(CommandError):
            call_command(
                "correct_historical_identities", stdout=StringIO(),
                **self.command_options(
                    apply=True, actor=self.admin.username,
                    expected_plan_hash=reconciliation.fingerprint(plan),
                ),
            )
        self.assertEqual(self.database_snapshot(), before)

    def test_cli_apply_requires_explicit_actor_and_reviewed_hash(self):
        reviewed_hash = reconciliation.fingerprint(self.plan())
        for options in (
            {"expected_plan_hash": reviewed_hash},
            {"actor": self.admin.username},
            {"actor": self.admin.username, "expected_plan_hash": "0" * 64},
        ):
            with self.subTest(options=options):
                before = self.database_snapshot()
                with self.assertRaises(CommandError):
                    call_command(
                        "correct_historical_identities", stdout=StringIO(),
                        **self.command_options(apply=True, **options),
                    )
                self.assertEqual(self.database_snapshot(), before)

    def test_cli_actor_username_must_resolve_to_authorized_admin(self):
        actor = get_user_model().objects.create_user(
            username="identity-cli-technician", email="identity-cli-technician@example.org",
        )
        OrganizationMembership.objects.create(
            organization=self.org, user=actor, role="lab_technician",
        )
        for username in ("missing-identity-cli-user", actor.username):
            with self.subTest(username=username):
                before = self.database_snapshot()
                with self.assertRaises(CommandError):
                    call_command(
                        "correct_historical_identities", stdout=StringIO(),
                        **self.command_options(
                            apply=True, actor=username,
                            expected_plan_hash=reconciliation.fingerprint(self.plan()),
                        ),
                    )
                self.assertEqual(self.database_snapshot(), before)

    def test_cli_apply_with_admin_username_and_reviewed_hash_succeeds(self):
        plan = self.plan()
        output = StringIO()
        call_command(
            "correct_historical_identities", stdout=output,
            **self.command_options(
                apply=True, actor=self.admin.username,
                expected_plan_hash=reconciliation.fingerprint(plan),
            ),
        )
        reported, _ = json.JSONDecoder().raw_decode(output.getvalue())
        self.assertEqual(reported, reconciliation.summary(plan))
        self.assertIn(f"Plan SHA-256: {reconciliation.fingerprint(plan)}", output.getvalue())
        self.assertIn("Applied atomically; no physical Box was created or merged.", output.getvalue())
        self.assertEqual({e["classification"] for e in self.plan()["entries"]}, {"ALREADY_CORRECT"})
        receipt = AuditLog.objects.get(object_type="identity_reconciliation")
        self.assertEqual(receipt.user_id, self.admin.pk)
        self.assertEqual(receipt.organization_id, self.org.pk)

    def assert_cli_account_queries_are_session_scoped(self, **actor_options):
        plan = self.plan()
        output = StringIO()
        sessions, account_queries = [], []
        active_sessions = 0
        real_session = execution.session
        account_tables = (get_user_model()._meta.db_table, OrganizationMembership._meta.db_table)

        def watch_account_sql(execute, sql, params, many, context):
            if any(table.lower() in sql.lower() for table in account_tables):
                self.assertGreater(active_sessions, 0, "Account SQL executed outside an execution session")
                account_queries.append(sql)
            return execute(sql, params, many, context)

        @contextmanager
        def entered_session(**kwargs):
            nonlocal active_sessions
            if not sessions:
                self.assertEqual(len(queries), 0, "CLI queried the database before entering execution.session")
            sessions.append(kwargs)
            with real_session(**kwargs) as binding:
                active_sessions += 1
                try:
                    yield binding
                finally:
                    active_sessions -= 1

        with CaptureQueriesContext(connection) as queries, connection.execute_wrapper(watch_account_sql), \
                patch.object(execution, "session", side_effect=entered_session):
            call_command(
                "correct_historical_identities", stdout=output,
                **self.command_options(
                    apply=True, expected_plan_hash=reconciliation.fingerprint(plan), **actor_options,
                ),
            )
        self.assertTrue(account_queries, "The administrator must be revalidated inside the session")
        if "actor_id" in actor_options:
            self.assertEqual(len(sessions), 1)
            self.assertTrue(sessions[0]["apply"])
            self.assertEqual(sessions[0]["actor_id"], actor_options["actor_id"])
        else:
            self.assertEqual(len(sessions), 2)
            self.assertFalse(sessions[0].get("apply", False))
            self.assertTrue(sessions[1]["apply"])
        reported, _ = json.JSONDecoder().raw_decode(output.getvalue())
        self.assertEqual(reported, reconciliation.summary(plan))
        receipt = AuditLog.objects.get(object_type="identity_reconciliation")
        self.assertEqual(receipt.user_id, self.admin.pk)
        self.assertEqual({e["classification"] for e in self.plan()["entries"]}, {"ALREADY_CORRECT"})

    def test_cli_actor_id_applies_without_account_sql_before_session(self):
        self.assert_cli_account_queries_are_session_scoped(actor_id=self.admin.pk)

    def test_cli_sqlite_username_lookup_occurs_inside_read_only_session(self):
        self.assert_cli_account_queries_are_session_scoped(actor=self.admin.username)

    def test_cli_session_rejection_prevents_actor_lookup_and_all_queries(self):
        reviewed_hash = reconciliation.fingerprint(self.plan())
        for actor_options in ({"actor_id": self.admin.pk}, {"actor": self.admin.username}):
            with self.subTest(actor_options=actor_options):
                before = self.database_snapshot()
                entered = []

                def reject_entry():
                    entered.append(True)
                    raise ValueError("Synthetic execution authorization rejected")

                with patch.object(execution, "session") as session, self.assertNumQueries(0):
                    session.return_value.__enter__.side_effect = reject_entry
                    with self.assertRaisesMessage(CommandError, "Synthetic execution authorization rejected"):
                        call_command(
                            "correct_historical_identities", stdout=StringIO(),
                            **self.command_options(
                                apply=True, expected_plan_hash=reviewed_hash, **actor_options,
                            ),
                        )
                    session.assert_called_once()
                self.assertEqual(entered, [True])
                self.assertEqual(self.database_snapshot(), before)

    def test_cli_postgresql_username_is_rejected_before_session_or_account_sql(self):
        reviewed_hash = reconciliation.fingerprint(self.plan())
        before = self.database_snapshot()
        # Exercise only mode routing; no PostgreSQL connection is configured or opened.
        with patch.object(identity_command, "require_local_database", return_value="production"), \
                patch.object(execution, "require_database", return_value="production"), \
                patch.object(execution, "session") as session, self.assertNumQueries(0):
            with self.assertRaisesMessage(CommandError, "requires --actor-id"):
                call_command(
                    "correct_historical_identities", stdout=StringIO(),
                    **self.command_options(
                        apply=True, actor=self.admin.username, expected_plan_hash=reviewed_hash,
                    ),
                )
            session.assert_not_called()
        self.assertEqual(self.database_snapshot(), before)

    def test_cli_actor_id_requires_positive_id_and_reviewed_hash_before_queries(self):
        reviewed_hash = reconciliation.fingerprint(self.plan())
        for options in (
            {"actor_id": 0, "expected_plan_hash": reviewed_hash},
            {"actor_id": -1, "expected_plan_hash": reviewed_hash},
            {"actor_id": self.admin.pk},
        ):
            with self.subTest(options=options):
                before = self.database_snapshot()
                with patch.object(execution, "session") as session, self.assertNumQueries(0):
                    with self.assertRaises(CommandError):
                        call_command(
                            "correct_historical_identities", stdout=StringIO(),
                            **self.command_options(apply=True, **options),
                        )
                    session.assert_not_called()
                self.assertEqual(self.database_snapshot(), before)

    def test_cli_environment_guard_rejects_dry_run_and_apply_before_queries(self):
        reviewed_hash = reconciliation.fingerprint(self.plan())
        for apply in (False, True):
            for config in (
                {"ENGINE": "django.db.backends.postgresql", "HOST": "not-local.invalid"},
                {"NAME": "synthetic-file-database.sqlite3"},
            ):
                with self.subTest(apply=apply, config=config):
                    before = self.database_snapshot()
                    with patch.dict(connection.settings_dict, config), self.assertNumQueries(0):
                        with self.assertRaises(CommandError):
                            call_command(
                                "correct_historical_identities", stdout=StringIO(),
                                **self.command_options(
                                    apply=apply, actor=self.admin.username, expected_plan_hash=reviewed_hash,
                                ),
                            )
                    self.assertEqual(self.database_snapshot(), before)
            with self.subTest(apply=apply, settings_module="config.settings"):
                before = self.database_snapshot()
                with override_settings(SETTINGS_MODULE="config.settings"), self.assertNumQueries(0):
                    with self.assertRaises(CommandError):
                        call_command(
                            "correct_historical_identities", stdout=StringIO(),
                            **self.command_options(
                                apply=apply, actor=self.admin.username, expected_plan_hash=reviewed_hash,
                            ),
                        )
                self.assertEqual(self.database_snapshot(), before)

    def test_missing_source_box_blocks_family_without_creating_it(self):
        box = self.first_box()
        Box.objects.filter(pk=box.pk).update(global_code=f"{box.strain.code}.098")
        self.assert_blocked()
        self.assertFalse(Box.objects.filter(global_code=box.global_code).exists())

    def test_missing_or_wrong_explicit_target_is_not_inferred(self):
        for pk in (None, self.targets[self.family].pk + 10000, self.sources[self.family].pk):
            with self.subTest(target_strain_id=pk):
                self.assert_blocked(target_strain_id=pk)

    def test_global_or_foreign_target_with_local_identity_is_invalid_even_with_witness(self):
        target = self.targets[self.family]
        Box.objects.create(
            organization=self.org, strain=target, global_code=f"{target.code}.098", box_number="098",
        )
        LocalStrainIdentity.objects.create(
            strain=target, species_code_assignment=self.assignments[FAMILIES[self.family]["aaa"]],
        )
        for owner in (None, self.other_org.pk):
            with self.subTest(owner=owner):
                Strain.objects.filter(pk=target.pk).update(organization_id=owner)
                self.assert_blocked()

    def test_global_target_with_prior_own_box_witness_is_used_without_claiming(self):
        target = self.targets[self.family]
        Strain.objects.filter(pk=target.pk).update(organization=None)
        witness = Box.objects.create(
            organization=self.org, strain=target, global_code=f"{target.code}.098", box_number="098",
            notes="Prior institutional witness, not part of the correction",
        )
        before_strains = list(Strain.objects.order_by("pk").values())
        before_witness = Box.objects.filter(pk=witness.pk).values().get()
        before_local = list(LocalStrainIdentity.objects.order_by("pk").values())
        plan = self.plan()
        self.assertEqual({e["classification"] for e in plan["entries"]}, {"READY"})
        self.assertEqual([row["id"] for row in plan["target_eligibility_witnesses"]], [witness.pk])
        self.assertEqual(self.apply(plan=plan), plan)
        self.assertEqual(list(Strain.objects.order_by("pk").values()), before_strains)
        self.assertEqual(Box.objects.filter(pk=witness.pk).values().get(), before_witness)
        self.assertEqual(list(LocalStrainIdentity.objects.order_by("pk").values()), before_local)
        self.assertIsNone(Strain.objects.get(pk=target.pk).organization_id)
        for entry in plan["entries"]:
            self.assertEqual(Box.objects.get(pk=entry["box_pk"]).strain_id, target.pk)
        self.assertEqual({e["classification"] for e in self.plan()["entries"]}, {"ALREADY_CORRECT"})

    def test_global_target_without_own_box_witness_is_not_inferred_or_claimed(self):
        target = self.targets[self.family]
        Strain.objects.filter(pk=target.pk).update(organization=None)
        self.assert_blocked()
        Box.objects.create(
            organization=self.other_org, strain=target, global_code=f"{target.code}.098", box_number="098",
        )
        self.assert_blocked()
        self.assertIsNone(Strain.objects.get(pk=target.pk).organization_id)

    def test_global_target_witness_loss_invalidates_review(self):
        target = self.targets[self.family]
        Strain.objects.filter(pk=target.pk).update(organization=None)
        witness = Box.objects.create(
            organization=self.org, strain=target, global_code=f"{target.code}.098", box_number="098",
        )
        plan = self.plan()
        self.assertEqual({e["classification"] for e in plan["entries"]}, {"READY"})
        witness.delete()
        before = self.database_snapshot()
        with self.assertRaises(ValueError):
            self.apply(plan=plan)
        self.assertEqual(self.database_snapshot(), before)
        self.assert_blocked()

    def test_target_requires_exact_species_and_strain_code(self):
        target = self.targets[self.family]
        for changes in ({"code": "WRONG-JKA-1"}, {"species_id": self.sources[self.family].species_id}):
            with self.subTest(changes=changes):
                Strain.objects.filter(pk=target.pk).update(**changes)
                self.assert_blocked()
                Strain.objects.filter(pk=target.pk).update(code=target.code, species_id=target.species_id)

    def test_original_strain_with_either_reviewed_species_is_accepted(self):
        Strain.objects.filter(pk=self.sources[self.family].pk).update(
            species_id=self.targets[self.family].species_id,
        )
        self.assertEqual({e["classification"] for e in self.plan()["entries"]}, {"READY"})
        self.apply()

    def test_incompatible_source_strain_blocks_entire_family(self):
        source = self.sources[self.family]
        other_family = "sme" if self.family == "cla" else "cla"
        for changes in ({"code": "WRONG-JKA-1"},
                        {"species_id": self.sources[other_family].species_id}):
            with self.subTest(changes=changes):
                Strain.objects.filter(pk=source.pk).update(**changes)
                self.assert_blocked()
                Strain.objects.filter(pk=source.pk).update(code=source.code, species_id=source.species_id)

    def test_missing_target_species_assignment_requires_explicit_preparation(self):
        aaa = FAMILIES[self.family]["aaa"]
        self.assignments[aaa].delete()
        before = self.database_snapshot()
        plan = self.plan()
        self.assertEqual(self.database_snapshot(), before)
        for entry in plan["entries"]:
            self.assertEqual(entry["classification"], "BLOCKED")
            self.assertTrue(any(aaa in conflict and "prepar" in conflict.lower()
                                for conflict in entry["conflicts"]), entry)
        self.assert_blocked(plan)
        self.assertFalse(OrganizationSpeciesCode.objects.filter(organization=self.org, code=aaa).exists())

    def test_target_species_assignment_collision_or_wrong_code_blocks(self):
        assignment = self.assignments[FAMILIES[self.family]["aaa"]]
        foreign_species = Species.objects.create(scientific_name="Unrelated assignment QA")
        for changes in ({"species_id": foreign_species.pk}, {"code": "BAD"},
                        {"organization_id": self.other_org.pk}):
            with self.subTest(changes=changes):
                OrganizationSpeciesCode.objects.filter(pk=assignment.pk).update(**changes)
                self.assert_blocked()
                OrganizationSpeciesCode.objects.filter(pk=assignment.pk).update(
                    organization=self.org, species_id=assignment.species_id, code=assignment.code,
                )

    def test_wrong_old_species_assignment_blocks_without_rewriting(self):
        assignment = self.assignments[FAMILIES[self.family]["old_code"]]
        unrelated = Species.objects.create(scientific_name="Wrong historical assignment QA")
        OrganizationSpeciesCode.objects.filter(pk=assignment.pk).update(species=unrelated)
        self.assert_blocked()

    def test_optional_target_local_identity_must_be_consistent(self):
        target = self.targets[self.family]
        identity = LocalStrainIdentity.objects.create(
            strain=target, species_code_assignment=self.assignments[FAMILIES[self.family]["aaa"]],
        )
        self.assertEqual({e["classification"] for e in self.plan()["entries"]}, {"READY"})
        LocalStrainIdentity.objects.filter(pk=identity.pk).update(
            species_code_assignment=self.assignments[FAMILIES[self.family]["old_code"]],
        )
        self.assert_blocked()

    def test_stale_box_state_or_strain_snapshot_is_rejected(self):
        for model, obj, changes in (
            (Box, self.first_box(), {"notes": "Changed after review"}),
            (Box, self.first_box(), {"polyp_state_revision": 999}),
            (Strain, self.sources[self.family], {"notes": "Source changed after review"}),
            (Strain, self.targets[self.family], {"notes": "Target changed after review"}),
        ):
            with self.subTest(model=model.__name__, changes=changes):
                plan = self.plan()
                original = model.objects.filter(pk=obj.pk).values(*changes).get()
                model.objects.filter(pk=obj.pk).update(**changes)
                self.assertNotEqual(reconciliation.fingerprint(self.plan()), reconciliation.fingerprint(plan))
                before = self.database_snapshot()
                with self.assertRaises(ValueError):
                    self.apply(plan=plan)
                self.assertEqual(self.database_snapshot(), before)
                model.objects.filter(pk=obj.pk).update(**original)

    def test_changed_measurement_or_location_invalidates_review(self):
        box = self.first_box()
        for model, obj, changes in (
            (BiologicalMeasurement, box.biological_measurements.order_by("pk").first(),
             {"strobila_count": 7}),
            (BoxLocation, box.locations.order_by("pk").first(), {"notes": "Changed location evidence"}),
        ):
            with self.subTest(model=model.__name__):
                plan = self.plan()
                original = model.objects.filter(pk=obj.pk).values(*changes).get()
                model.objects.filter(pk=obj.pk).update(**changes)
                self.assertNotEqual(reconciliation.fingerprint(self.plan()), reconciliation.fingerprint(plan))
                before = self.database_snapshot()
                with self.assertRaises(ValueError):
                    self.apply(plan=plan)
                self.assertEqual(self.database_snapshot(), before)
                model.objects.filter(pk=obj.pk).update(**original)

    def test_new_dependency_invalidates_review_without_changing_scientific_data(self):
        plan = self.plan()
        BiologicalMeasurement.objects.create(
            box=self.first_box(), measured_on=date(2026, 10, 5), polyp_count=0,
            ephyrae_count=0, strobila_count=None, salinity_psu=None,
        )
        self.assertNotEqual(reconciliation.fingerprint(self.plan()), reconciliation.fingerprint(plan))
        before = self.database_snapshot()
        with self.assertRaises(ValueError):
            self.apply(plan=plan)
        self.assertEqual(self.database_snapshot(), before)

    def test_audit_failure_rolls_back_all_changes_and_prior_audits(self):
        before = self.database_snapshot()
        create = AuditLog.objects.create
        calls = []

        def fail_batch_receipt(**kwargs):
            calls.append(kwargs)
            if kwargs.get("object_type") == "identity_reconciliation":
                raise RuntimeError("Batch receipt unavailable")
            return create(**kwargs)

        with patch.object(AuditLog.objects, "create", side_effect=fail_batch_receipt):
            with self.assertRaisesMessage(RuntimeError, "Batch receipt unavailable"):
                self.apply()
        self.assertTrue(calls)
        self.assertEqual(self.database_snapshot(), before)

    def test_wrong_hash_cannot_authorize_apply(self):
        before = self.database_snapshot()
        with self.assertRaises(ValueError):
            self.apply(expected_plan_hash="0" * 64)
        self.assertEqual(self.database_snapshot(), before)

    def test_apply_rejects_nonlocal_database_configuration_before_writes(self):
        plan = self.plan()
        before = self.database_snapshot()
        # Only replace configuration metadata; never open another connection.
        with patch.dict(connection.settings_dict, {
            "ENGINE": "django.db.backends.postgresql", "HOST": "not-local.invalid",
            "NAME": "synthetic_not_authorized", "PORT": "5432",
        }):
            with self.assertRaises(ValueError):
                self.apply(plan=plan)
        self.assertEqual(self.database_snapshot(), before)


class ClaIdentityReconciliationTests(ReconciliationFixture, TestCase):
    family = "cla"

    def historical_clc_target(self, code, *, organization=None, notes=""):
        target = Box.objects.create(
            organization=self.org if organization is None else organization, strain=self.targets["cla"],
            global_code="CLC-JKA-1.999", box_number=code.rsplit(".", 1)[1], notes=notes,
        )
        # Deliberately reconstruct a historical collision rejected by ordinary saves.
        Box._base_manager.filter(pk=target.pk).update(global_code=code)
        target.refresh_from_db()
        return target

    def test_preexisting_renamed_target_blocks_with_or_without_old_box(self):
        for number in (9, 10):
            for foreign in (False, True):
                with self.subTest(number=number, foreign=foreign):
                    target = self.historical_clc_target(
                        f"CLC-JKA-1.{number:03}", organization=self.other_org if foreign else self.org,
                        notes="Private target notes" if foreign else "",
                    )
                    self.assert_blocked()
                    old = self.boxes[f"CLA-JKA-1.{number:03}"]
                    Box.objects.filter(pk=old.pk).update(global_code=f"CLA-JKA-1.{number + 200:03}")
                    plan = self.plan()
                    self.assertNotIn("Private target notes", json.dumps(plan))
                    self.assert_blocked(plan)
                    Box.objects.filter(pk=old.pk).update(global_code=old.global_code)
                    target.delete()

    def test_renamed_target_requires_matching_batch_receipt(self):
        self.apply()
        receipt = AuditLog.objects.get(object_type="identity_reconciliation")
        original = deepcopy(receipt.metadata)
        mutations = []
        wrong_operation = deepcopy(original)
        wrong_operation["operation"] = "unrelated_operation"
        mutations.append(wrong_operation)
        for key, value in (("box_pk", -1), ("old_code", "CLA-JKA-1.999"),
                           ("target_code", "CLC-JKA-1.999")):
            metadata = deepcopy(original)
            next(e for e in metadata["entries"] if e["old_code"] == "CLA-JKA-1.009")[key] = value
            mutations.append(metadata)
        metadata = deepcopy(original)
        next(e for e in metadata["entries"] if e["old_code"] == "CLA-JKA-1.009")["after"] = {}
        mutations.append(metadata)
        for metadata in mutations:
            with self.subTest(metadata=metadata):
                AuditLog.objects.filter(pk=receipt.pk).update(metadata=metadata)
                self.assert_blocked()
        AuditLog.objects.filter(pk=receipt.pk).update(metadata=original, organization=self.other_org)
        self.assert_blocked()
        AuditLog.objects.filter(pk=receipt.pk).delete()
        self.assert_blocked()

    def test_cla_blocker_does_not_prevent_separately_reviewed_sme_apply(self):
        self.historical_clc_target("CLC-JKA-1.009")
        self.assert_blocked()
        plan = self.plan(family="sme", target_strain_id=self.targets["sme"].pk)
        self.apply(plan=plan, family="sme", target_strain_id=self.targets["sme"].pk)
        self.assertEqual(Box.objects.get(pk=self.first_box().pk).strain_id, self.sources["cla"].pk)
        self.assertEqual({Box.objects.get(pk=self.boxes[e["old_code"]].pk).strain_id
                          for e in expected_specs("sme")}, {self.targets["sme"].pk})


class SmeIdentityReconciliationTests(ReconciliationFixture, TestCase):
    family = "sme"

    def test_sme_keeps_every_exact_historical_code_and_never_renames_to_ssp(self):
        plan = self.plan()
        self.assertTrue(all(e["old_code"] == e["target_code"] for e in plan["entries"]))
        self.apply(plan=plan)
        self.assertFalse(Box.objects.filter(global_code__startswith="SSP-JKA-1.").exists())
        self.assertTrue(Box.objects.filter(global_code="SME-JKA-1.001").exists())
        self.assertFalse(Box.objects.filter(global_code__in=("SME-JKA-1.002", "SME-JKA-1.003")).exists())

    def assert_ssp_candidates_are_observational_and_preserved(self, *, foreign):
        candidates = []
        for number in FAMILIES["sme"]["numbers"]:
            candidate = Box.objects.create(
                organization=self.other_org if foreign else self.org,
                strain=self.targets["sme"], global_code=f"SSP-JKA-1.{number:03}",
                box_number=f"{number:03}", notes="Private SSP candidate",
            )
            candidates.append(candidate.pk)
            BiologicalMeasurement.objects.create(
                box=candidate, measured_on=date(2026, 9, 7), polyp_count=0, ephyrae_count=0,
                strobila_count=None, notes="Independent SSP scientific history",
            )
        before_candidates = list(Box.objects.filter(pk__in=candidates).order_by("pk").values())
        before_measurements = list(BiologicalMeasurement.objects.order_by("pk").values())
        before_strains = list(Strain.objects.order_by("pk").values())
        plan = self.plan()
        self.assertEqual({e["classification"] for e in plan["entries"]}, {"READY"})
        for entry in plan["entries"]:
            self.assertTrue(entry["corresponding_ssp_present"], entry)
            self.assertEqual(entry["physical_continuity"], "NOT_ASSERTED")
            self.assertEqual(entry["old_code"], entry["target_code"])
        if foreign:
            self.assertNotIn("Private SSP candidate", json.dumps(plan))
        self.apply(plan=plan)
        self.assertEqual(list(Box.objects.filter(pk__in=candidates).order_by("pk").values()), before_candidates)
        self.assertEqual(list(BiologicalMeasurement.objects.order_by("pk").values()), before_measurements)
        self.assertEqual(list(Strain.objects.order_by("pk").values()), before_strains)
        for entry in plan["entries"]:
            box = Box.objects.get(pk=entry["box_pk"])
            self.assertEqual(box.global_code, entry["old_code"])
            self.assertEqual(box.strain_id, self.targets["sme"].pk)
            self.assertIsNone(box.merged_into_id)
            self.assertEqual(box.previous_global_code, "")
        self.assertEqual({e["classification"] for e in self.plan()["entries"]}, {"ALREADY_CORRECT"})
        before_rerun = self.database_snapshot()
        self.apply()
        self.assertEqual(self.database_snapshot(), before_rerun)

    def test_all_same_suffix_ssp_candidates_are_observational_and_preserved(self):
        self.assert_ssp_candidates_are_observational_and_preserved(foreign=False)

    def test_foreign_same_suffix_ssp_candidates_are_observational_and_preserved(self):
        self.assert_ssp_candidates_are_observational_and_preserved(foreign=True)

    def test_ssp_candidate_without_old_sme_box_is_not_adopted(self):
        old = self.boxes["SME-JKA-1.008"]
        Box.objects.filter(pk=old.pk).update(global_code="SME-JKA-1.098")
        Box.objects.create(
            organization=self.org, strain=self.targets["sme"],
            global_code="SSP-JKA-1.008", box_number="008",
        )
        self.assert_blocked()

    def test_sme_blocker_does_not_prevent_separately_reviewed_cla_apply(self):
        Box.objects.create(
            organization=self.org, strain=self.targets["sme"],
            global_code="SSP-JKA-1.008", box_number="008",
        )
        Box._base_manager.filter(pk=self.first_box().pk).update(global_code="SME-JKA-1.098")
        self.assert_blocked()
        plan = self.plan(family="cla", target_strain_id=self.targets["cla"].pk)
        self.apply(plan=plan, family="cla", target_strain_id=self.targets["cla"].pk)
        self.assertEqual(Box.objects.get(pk=self.first_box().pk).strain_id, self.sources["sme"].pk)
        self.assertTrue(Box.objects.filter(global_code="CLC-JKA-1.009", strain=self.targets["cla"]).exists())
