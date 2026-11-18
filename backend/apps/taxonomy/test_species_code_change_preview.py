"""Read-only preview of an institutional Species code (AAA) change."""

from contextlib import contextmanager
from unittest import skipUnless

from django.core import serializers
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.exceptions import PermissionDenied

from apps.audit.models import AuditLog
from apps.cultures.models import (
    Box, BoxCodeNamespace, BoxGlobalIdentity, BoxLocalIdAlias, IdentificationTag, new_box_global_identity,
)
from apps.cultures.qr import box_scan_url, sync_box_qr_tag

from .models import (
    InstitutionSpecies, LocalStrainIdentity, LocalStrainNumberCounter, OrganizationSpeciesCode,
    Species, Strain,
)
from .species_code_change import plan_species_code_change
from .test_institution_strains import InstitutionStrainBase

PREVIEW_URL = "api_admin_institution_species_code_change_preview"
STATE_MODELS = (
    Species, InstitutionSpecies, OrganizationSpeciesCode, Strain, LocalStrainIdentity,
    LocalStrainNumberCounter, Box, BoxCodeNamespace, BoxGlobalIdentity, BoxLocalIdAlias, IdentificationTag, AuditLog,
)


def _state():
    """Every identifier and record a preview must leave untouched."""
    return [serializers.serialize("json", model.objects.order_by("pk")) for model in STATE_MODELS]


class SpeciesCodeChangePreviewBase(InstitutionStrainBase):
    def setUp(self):
        super().setUp()
        self.strain = self.owned_strain("AAU-BRE-1")

    def owned_strain(self, code, *, organization=None, species=None):
        organization = organization or self.a
        aaa, bbb, number = code.split("-")
        return Strain.objects.create(
            species=species or self.species, organization=organization, code=code,
            number=int(number), origin_code=bbb,
        )

    def box(self, code, *, strain=None, organization=None, **extra):
        return Box.objects.create(
            organization=organization or self.a, strain=strain or self.strain, global_code=code,
            box_number=code.rsplit(".", 1)[-1], **extra,
        )

    def preview(self, proposed="AUR", *, profile=None, organization=None, user=None):
        organization = organization or self.a
        self.client.force_login(user or (self.admin if organization == self.a else self.b_admin))
        return self.client.get(
            reverse(PREVIEW_URL, args=[(profile or self.profile).pk]), {"proposed_code": proposed},
            **self.headers(organization),
        )

    def plan(self, proposed="AUR", **kwargs):
        response = self.preview(proposed, **kwargs)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def read_only_plan(self):
        before = _state()
        with CaptureQueriesContext(connection) as queries:
            plan = plan_species_code_change(
                user=self.admin, organization=self.a, profile_pk=self.profile.pk, proposed_code="AUR",
            )
        self.assertEqual(_state(), before)
        for query in queries:
            self.assertNotRegex(query["sql"], r"(?i)^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b")
        return plan

    def blocker_codes(self, plan):
        return sorted({blocker["code"] for blocker in plan["blockers"]})

    def by_id(self, items):
        return {item["id"]: item for item in items}


class SpeciesCodeChangePlanTests(SpeciesCodeChangePreviewBase):
    def test_complete_plan_includes_inactive_records_and_writes_nothing(self):
        identity = new_box_global_identity(BoxGlobalIdentity.AllocationReason.AUTOMATIC_CREATION)
        active = self.box("AAU-BRE-1.001", global_identity=identity)
        inactive = self.box("AAU-BRE-1.002", status=Box.Status.INACTIVE)
        pending = self.box("AAU-BRE-1.003", status=Box.Status.PENDING_REVIEW)
        unused = self.owned_strain("AAU-FRA-2")
        sync_box_qr_tag(active)
        AuditLog.objects.create(
            organization=self.a, user=self.admin, action=AuditLog.Action.VIEW,
            object_type="box", object_id=active.global_code, description="viewed",
        )
        before = _state()

        plan = self.plan(" aur ")

        self.assertEqual(_state(), before)
        self.assertEqual((plan["current_code"], plan["proposed_code"]), ("AAU", "AUR"))
        self.assertTrue(plan["feasible"])
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(
            (plan["advisory_only"], plan["revalidation_required"], plan["mutation_available"]),
            (True, True, False),
        )
        self.assertEqual(plan["institution_species"]["id"], self.profile.pk)
        strains = self.by_id(plan["strains"])
        self.assertEqual(set(strains), {self.strain.pk, unused.pk})
        self.assertEqual(
            (strains[self.strain.pk]["proposed_code"], strains[self.strain.pk]["action"],
             strains[self.strain.pk]["ownership"], strains[self.strain.pk]["box_count"]),
            ("AUR-BRE-1", "rename", "institution_owned", 3),
        )
        self.assertEqual((strains[unused.pk]["proposed_code"], strains[unused.pk]["box_count"]), ("AUR-FRA-2", 0))
        boxes = self.by_id(plan["boxes"])
        self.assertEqual(
            {pk: (item["current_local_id"], item["proposed_local_id"], item["action"]) for pk, item in boxes.items()},
            {
                active.pk: ("AAU-BRE-1.001", "AUR-BRE-1.001", "rename"),
                inactive.pk: ("AAU-BRE-1.002", "AUR-BRE-1.002", "rename"),
                pending.pk: ("AAU-BRE-1.003", "AUR-BRE-1.003", "rename"),
            },
        )
        # The Global ID is reported as-is; a missing one is never invented.
        self.assertEqual(boxes[active.pk]["global_id"], str(identity.global_id))
        self.assertIsNone(boxes[inactive.pk]["global_id"])
        self.assertIn({"code": "missing_global_id", "box_ids": [inactive.pk, pending.pk]}, plan["warnings"])
        self.assertEqual(plan["summary"], {
            "strains": 2, "strains_to_rename": 2, "strains_blocked": 0, "strains_unchanged": 0,
            "boxes": 3, "boxes_to_rename": 3, "boxes_blocked": 0, "boxes_unchanged": 0,
            "boxes_missing_global_id": 2, "blockers": 0,
        })
        dependencies = {item["kind"]: item for item in plan["dependencies"]}
        self.assertEqual(dependencies["audit_entries_by_local_id"]["count"], 1)
        self.assertEqual(dependencies["qr_tags"], {"kind": "qr_tags", "effect": "unaffected", "count": 1})
        self.assertEqual(dependencies["box_code_namespaces"]["count"], 1)
        self.assertEqual(dependencies["physical_labels"]["count"], 2)
        self.assertIn({"kind": "box_local_id_url", "effect": "requires_alias"}, plan["code_based_lookups"])

    def test_plan_is_deterministic_and_fingerprint_tracks_identity_state(self):
        box = self.box("AAU-BRE-1.001")
        first = self.plan()
        # Unrelated activity (an audit entry) must not make the plan look stale.
        AuditLog.objects.create(
            organization=self.a, user=self.admin, action=AuditLog.Action.VIEW,
            object_type="box", object_id=box.global_code, description="viewed",
        )
        second = self.plan()
        self.assertEqual(first["plan_fingerprint"], second["plan_fingerprint"])
        self.assertEqual({**second, "dependencies": None}, {**first, "dependencies": None})
        self.assertNotEqual(self.plan("AUI")["plan_fingerprint"], first["plan_fingerprint"])
        # A concurrent identity change is visible to a revalidating mutation.
        self.box("AAU-BRE-1.002")
        self.assertNotEqual(self.plan()["plan_fingerprint"], first["plan_fingerprint"])

    def test_other_institutions_are_neither_listed_nor_planned(self):
        OrganizationSpeciesCode.objects.create(organization=self.b, species=self.species, code="AAU")
        b_strain = self.owned_strain("AAU-KOB-1", organization=self.b)
        b_box = self.box("AAU-KOB-1.001", strain=b_strain, organization=self.b)
        own_box = self.box("AAU-BRE-1.001")
        before = _state()

        plan = self.plan()

        self.assertEqual(_state(), before)
        self.assertTrue(plan["feasible"])
        self.assertEqual([item["id"] for item in plan["strains"]], [self.strain.pk])
        self.assertEqual([item["id"] for item in plan["boxes"]], [own_box.pk])
        self.assertNotIn(str(b_box.pk), str([b["id"] for b in plan["boxes"]]))
        self.assertNotIn("AAU-KOB-1", str(plan))
        self.assertNotIn(self.b.name, str(plan))

    def test_shared_legacy_and_foreign_strains_block_without_being_claimed(self):
        self.box("AAU-BRE-1.001")
        self.box("AAU-BRE-1.900", organization=self.b)  # B also uses A's owned Strain.
        legacy = Strain.objects.create(species=self.species, code="AAU-LEG-1")
        legacy_box = self.box("AAU-LEG-1.001", strain=legacy)
        foreign = self.owned_strain("AAU-KOB-1", organization=self.b)
        foreign_box = self.box("AAU-KOB-1.001", strain=foreign)
        old_format = Strain.objects.create(species=self.species, organization=self.a, code="OLD1")
        old_box = self.box("OLD1.001", strain=old_format)
        clean = self.owned_strain("AAU-FRA-3")
        before = _state()

        plan = self.plan()

        self.assertEqual(_state(), before)
        self.assertFalse(plan["feasible"])
        strains = self.by_id(plan["strains"])
        self.assertEqual(
            {pk: (item["ownership"], item["action"], item["reasons"]) for pk, item in strains.items()},
            {
                self.strain.pk: ("shared", "blocked", ["strain_used_by_another_institution"]),
                legacy.pk: ("legacy_unowned", "blocked", ["strain_without_owner"]),
                foreign.pk: ("foreign_owned", "blocked", ["strain_owned_by_another_institution"]),
                old_format.pk: ("institution_owned", "unchanged", ["code_format"]),
                clean.pk: ("institution_owned", "rename", []),
            },
        )
        self.assertFalse(strains[legacy.pk]["also_used_by_another_institution"])
        self.assertEqual(
            {b["code"] for b in plan["blockers"] if b["category"] == "shared_data"},
            {"strain_used_by_another_institution", "strain_without_owner", "strain_owned_by_another_institution"},
        )
        boxes = self.by_id(plan["boxes"])
        # Only this institution's Boxes are listed; B's Box of the shared Strain is not.
        self.assertEqual(len(boxes), 4)
        self.assertEqual(
            (boxes[legacy_box.pk]["proposed_local_id"], boxes[legacy_box.pk]["action"], boxes[legacy_box.pk]["reasons"]),
            ("AUR-LEG-1.001", "blocked", ["strain_blocked"]),
        )
        self.assertEqual(boxes[foreign_box.pk]["action"], "blocked")
        self.assertEqual(
            (boxes[old_box.pk]["proposed_local_id"], boxes[old_box.pk]["action"]), (None, "unchanged"),
        )
        self.assertIn({"code": "noncanonical_strain_unchanged", "strain_id": old_format.pk}, plan["warnings"])
        self.assertIn({"code": "box_without_current_code_unchanged", "box_id": old_box.pk}, plan["warnings"])

    def test_contradicting_identifiers_block(self):
        mismatched = self.owned_strain("AAI-BRE-4")
        stray_box = self.box("AAU-XXX-9.001", strain=mismatched)
        plan = self.plan()
        self.assertFalse(plan["feasible"])
        self.assertEqual(self.blocker_codes(plan), ["box_local_id_strain_mismatch", "strain_prefix_mismatch"])
        self.assertIsNone(self.by_id(plan["boxes"])[stray_box.pk]["proposed_local_id"])
        self.assertTrue(all(b["category"] == "consistency" for b in plan["blockers"]))

    def test_code_level_collisions(self):
        self.box("AAU-BRE-1.001")
        unchanged = self.plan("AAU")
        self.assertEqual(self.blocker_codes(unchanged), ["code_unchanged"])
        self.assertFalse(unchanged["feasible"])
        self.assertTrue(all(item["proposed_local_id"] is None for item in unchanged["boxes"]))

        taken = self.plan("CAN")
        self.assertFalse(taken["feasible"])
        self.assertIn({
            "code": "code_assigned_in_institution", "category": "collision",
            "species_id": self.other_species.pk, "scientific_name": "Cassiopea andromeda",
        }, taken["blockers"])

        # Another institution's assignment of the proposed code is not disclosed
        # and is not decided here: it is a warning without details.
        third = Species.objects.create(scientific_name="Chrysaora hysoscella")
        OrganizationSpeciesCode.objects.create(organization=self.b, species=third, code="AUR")
        elsewhere = self.plan("AUR")
        self.assertTrue(elsewhere["feasible"])
        self.assertIn({"code": "proposed_code_used_outside_institution"}, elsewhere["warnings"])

        # The proposed prefix already carried here by another Species blocks.
        self.owned_strain("AUI-FRA-7", species=self.other_species)
        self.assertEqual(self.blocker_codes(self.plan("AUI")), ["proposed_code_used_by_other_species"])

    def test_identifier_collisions_with_existing_and_reserved_identifiers(self):
        own_box = self.box("AAU-BRE-1.001")
        second = self.owned_strain("AAU-FRA-2")
        second_box = self.box("AAU-FRA-2.001", strain=second)
        third = self.owned_strain("AAU-KOB-3")
        third_box = self.box("AAU-KOB-3.001", strain=third)
        # Outside the institution: same Species, so the code-level rule is silent
        # and only the identifier collision is found.
        OrganizationSpeciesCode.objects.create(organization=self.b, species=self.species, code="AUR")
        foreign = self.owned_strain("AUR-BRE-1", organization=self.b)
        self.box("AUR-BRE-1.001", strain=foreign, organization=self.b)
        # A historically reserved previous Local ID, in this institution.
        holder = self.owned_strain("CAN-BRE-1", species=self.other_species)
        reserved = self.box("CAN-BRE-1.001", strain=holder, previous_global_code="AUR-FRA-2.001")
        # A namespace reserved without any remaining Box.
        BoxCodeNamespace.objects.create(namespace="AUR-KOB-3", high_water=4)
        before = _state()

        plan = self.plan()

        self.assertEqual(_state(), before)
        self.assertFalse(plan["feasible"])
        blockers = plan["blockers"]
        self.assertIn({
            "code": "strain_code_collision", "category": "collision", "strain_id": self.strain.pk,
            "identifier": "AUR-BRE-1", "scope": "outside_institution",
        }, blockers)
        self.assertIn({
            "code": "box_local_id_collision", "category": "collision", "box_id": own_box.pk,
            "identifier": "AUR-BRE-1.001", "scope": "outside_institution",
        }, blockers)
        self.assertIn({
            "code": "box_namespace_in_use", "category": "collision", "strain_id": self.strain.pk,
            "identifier": "AUR-BRE-1", "scope": "outside_institution",
        }, blockers)
        self.assertIn({
            "code": "box_local_id_historically_reserved", "category": "collision", "box_id": second_box.pk,
            "identifier": "AUR-FRA-2.001", "scope": "this_institution", "conflicting_box_id": reserved.pk,
        }, blockers)
        self.assertIn({
            "code": "box_namespace_reserved", "category": "collision", "strain_id": third.pk,
            "identifier": "AUR-KOB-3",
        }, blockers)
        # No foreign record id or institution leaks through a collision.
        self.assertNotIn(self.b.name, str(plan))
        self.assertFalse(any("conflicting_strain_id" in b for b in blockers if b.get("scope") == "outside_institution"))
        boxes = self.by_id(plan["boxes"])
        self.assertEqual({boxes[pk]["action"] for pk in (own_box.pk, second_box.pk, third_box.pk)}, {"blocked"})
        self.assertEqual(plan["summary"]["boxes_to_rename"], 0)

    def test_historical_identities_block_or_are_retained(self):
        current = self.box("AAU-BRE-1.001", previous_global_code="OLD-CODE.001")
        merged = self.box("AAU-BRE-1.002", status=Box.Status.INACTIVE, merged_into=current)
        plain = self.box("AAU-BRE-1.003")
        before = _state()

        plan = self.plan()

        self.assertEqual(_state(), before)
        self.assertTrue(plan["feasible"])
        self.assertEqual(plan["blockers"], [])
        boxes = self.by_id(plan["boxes"])
        self.assertEqual(boxes[current.pk]["previous_local_id"], "OLD-CODE.001")
        self.assertEqual(
            (boxes[merged.pk]["is_merged_alias"], boxes[merged.pk]["proposed_local_id"],
             boxes[merged.pk]["action"], boxes[merged.pk]["reasons"]),
            (True, None, "unchanged", ["merged_alias_retained"]),
        )
        self.assertEqual(boxes[plain.pk]["action"], "rename")
        dependencies = {item["kind"]: item["count"] for item in plan["dependencies"]}
        self.assertEqual(
            (dependencies["historical_alias_records"], dependencies["boxes_with_previous_local_id"]), (1, 1),
        )

    def test_reviewed_historical_identities_are_protected(self):
        species = Species.objects.create(scientific_name="Clytia languida")
        OrganizationSpeciesCode.objects.create(organization=self.a, species=species, code="CLA")
        profile = InstitutionSpecies.objects.create(
            organization=self.a, species=species, scientific_name="Clytia languida",
        )
        strain = self.owned_strain("CLA-JKA-1", species=species)
        box = self.box("CLA-JKA-1.001", strain=strain)
        plan = self.plan("CLG", profile=profile)
        self.assertFalse(plan["feasible"])
        self.assertEqual(plan["blockers"], [{
            "code": "reviewed_identity_protected", "category": "historical_identity",
            "box_id": box.pk, "identifier": "CLA-JKA-1.001",
        }])

    def test_species_without_code_cannot_be_planned(self):
        species = Species.objects.create(scientific_name="Rhizostoma pulmo")
        profile = InstitutionSpecies.objects.create(
            organization=self.a, species=species, scientific_name="Rhizostoma pulmo",
        )
        plan = self.plan("RPU", profile=profile)
        self.assertFalse(plan["feasible"])
        self.assertIsNone(plan["current_code"])
        self.assertEqual(self.blocker_codes(plan), ["species_code_missing"])

    def test_unusable_current_code_prevents_identifier_planning_without_writes(self):
        box = self.box("AAU-BRE-1.001")
        for code in ("", "aaU", "A1U", "AU"):
            with self.subTest(current_code=code):
                self.aaa.code = code
                self.aaa.save(update_fields=["code"])

                plan = self.read_only_plan()

                self.assertFalse(plan["feasible"])
                self.assertEqual(plan["current_code"], code)
                self.assertEqual(plan["blockers"], [{
                    "code": "current_code_unusable", "category": "validation",
                }])
                self.assertEqual(
                    [(item["id"], item["proposed_code"], item["action"], item["reasons"])
                     for item in plan["strains"]],
                    [(self.strain.pk, None, "unchanged", [])],
                )
                self.assertEqual(
                    [(item["id"], item["proposed_local_id"], item["action"], item["reasons"])
                     for item in plan["boxes"]],
                    [(box.pk, None, "unchanged", [])],
                )

    def test_local_identity_mismatch_blocks_strain_and_box_without_writes(self):
        box = self.box("AAU-BRE-1.001")
        identity = LocalStrainIdentity.objects.create(
            strain=self.strain, species_code_assignment=self.aaa,
        )
        self.assertTrue(self.read_only_plan()["feasible"])
        other_species_assignment = OrganizationSpeciesCode.objects.get(
            organization=self.a, species=self.other_species,
        )
        foreign_assignment = OrganizationSpeciesCode.objects.create(
            organization=self.b, species=self.species, code="AAU",
        )
        for assignment in (other_species_assignment, foreign_assignment):
            with self.subTest(assignment_id=assignment.pk):
                identity.species_code_assignment = assignment
                identity.save(update_fields=["species_code_assignment"])

                plan = self.read_only_plan()

                self.assertFalse(plan["feasible"])
                self.assertEqual(plan["blockers"], [{
                    "code": "local_identity_mismatch", "category": "consistency",
                    "strain_id": self.strain.pk,
                }])
                strain_item = self.by_id(plan["strains"])[self.strain.pk]
                self.assertEqual(
                    (strain_item["proposed_code"], strain_item["action"], strain_item["reasons"]),
                    ("AUR-BRE-1", "blocked", ["local_identity_mismatch"]),
                )
                box_item = self.by_id(plan["boxes"])[box.pk]
                self.assertEqual(
                    (box_item["proposed_local_id"], box_item["action"], box_item["reasons"]),
                    ("AUR-BRE-1.001", "blocked", ["strain_blocked"]),
                )

    def test_assignment_used_by_unplanned_strains_blocks_without_disclosure_or_writes(self):
        box = self.box("AAU-BRE-1.001")
        LocalStrainIdentity.objects.create(strain=self.strain, species_code_assignment=self.aaa)
        self.assertTrue(self.read_only_plan()["feasible"])
        unplanned = [
            self.owned_strain("CAN-BRE-2", species=self.other_species),
            self.owned_strain("AAU-KOB-2", organization=self.b),
            Strain.objects.create(species=self.species, code="AAU-LEG-3"),
        ]
        for strain in unplanned:
            LocalStrainIdentity.objects.create(strain=strain, species_code_assignment=self.aaa)

        plan = self.read_only_plan()

        self.assertFalse(plan["feasible"])
        self.assertEqual(plan["blockers"], [{
            "code": "assignment_used_by_unplanned_strain", "category": "consistency", "count": 3,
        }])
        self.assertEqual([item["id"] for item in plan["strains"]], [self.strain.pk])
        self.assertEqual([item["id"] for item in plan["boxes"]], [box.pk])
        self.assertEqual(plan["strains"][0]["action"], "rename")
        self.assertEqual(plan["strains"][0]["reasons"], [])
        self.assertEqual(plan["boxes"][0]["action"], "rename")
        self.assertEqual(plan["boxes"][0]["reasons"], [])
        for strain in unplanned:
            self.assertNotIn(strain.code, str(plan))
        self.assertNotIn(self.b.name, str(plan))

    def test_existing_urls_qr_and_global_id_resolution_are_unchanged(self):
        identity = new_box_global_identity(BoxGlobalIdentity.AllocationReason.AUTOMATIC_CREATION)
        box = self.box("AAU-BRE-1.001", global_identity=identity)
        tag = sync_box_qr_tag(box)
        scan_url = box_scan_url(box)

        self.assertTrue(self.plan()["feasible"])

        box.refresh_from_db()
        tag.refresh_from_db()
        self.assertEqual((box.global_code, box.previous_global_code), ("AAU-BRE-1.001", ""))
        self.assertEqual(box.global_identity_id, identity.global_id)
        self.assertEqual((box_scan_url(box), tag.url, tag.code), (scan_url, scan_url, f"box:{box.pk}"))
        resolved = self.client.get(
            reverse("api_box_global_id_resolve", args=[identity.global_id]), **self.headers(self.a),
        )
        self.assertEqual(resolved.json(), {
            "id": box.pk, "global_id": str(identity.global_id), "global_code": "AAU-BRE-1.001",
        })
        found = self.client.get("/api/boxes/", {"q": "AAU-BRE-1.001"}, **self.headers(self.a)).json()
        rows = found["results"] if isinstance(found, dict) else found
        self.assertEqual([row["global_code"] for row in rows], ["AAU-BRE-1.001"])
        self.assertEqual(self.client.get("/api/boxes/", {"q": "AUR-BRE-1.001"}, **self.headers(self.a)).status_code, 200)
        self.assertFalse(Box.objects.filter(global_code__startswith="AUR-").exists())


@contextmanager
def legacy_box_updates():
    """Reproduce pre-0019 data without weakening guards during the preview."""
    with connection.cursor() as cursor:
        sql = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = 'validate_box_local_id_update'",
        ).fetchone()[0]
        cursor.execute("DROP TRIGGER validate_box_local_id_update")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(sql)


class SpeciesCodeChangeAliasTests(SpeciesCodeChangePreviewBase):
    def test_multiple_aliases_and_previous_field_do_not_use_a_single_slot(self):
        box = self.box("AAU-BRE-1.001", previous_global_code="LEGACY.001")
        donor = self.box("OLD-DONOR.001", merged_into=box, status=Box.Status.INACTIVE)
        for source, code in ((box, "LEGACY.001"), (box, "OLDER.001"), (donor, "DONOR-ALIAS.001")):
            BoxLocalIdAlias.objects.create(box=source, code=code)
        plan = self.read_only_plan()
        self.assertTrue(plan["feasible"])
        self.assertEqual(self.by_id(plan["boxes"])[box.pk]["action"], "rename")
        self.assertNotIn("historical_alias_slot_occupied", str(plan))

    def test_alias_exact_and_namespace_collisions_are_global_and_redacted(self):
        box = self.box("AAU-BRE-1.001")
        unnumbered = self.owned_strain("AAU-FRA-2")
        for organization, scope, suffix in ((self.a, "this_institution", "001"),
                                             (self.b, "outside_institution", "002")):
            holder_strain = self.owned_strain(f"CAN-KOB-{int(suffix)}", organization=organization,
                                              species=self.other_species)
            holder = self.box(f"PRIVATE-HOLDER.{suffix}", organization=organization, strain=holder_strain)
            BoxLocalIdAlias.objects.create(box=holder, code=f"AUR-BRE-1.{suffix}")
            BoxLocalIdAlias.objects.create(box=holder, code=f"AUR-FRA-2.{suffix}")
        # A foreign exact reservation uses a different affected Box, since the
        # registry deliberately enforces global uniqueness of the alias code.
        second_box = self.box("AAU-BRE-1.002")
        plan = self.read_only_plan()
        self.assertFalse(plan["feasible"])
        for box_id, scope in ((box.pk, "this_institution"), (second_box.pk, "outside_institution")):
            collision = next(b for b in plan["blockers"] if b["code"] == "box_local_id_historically_reserved"
                             and b["box_id"] == box_id)
            self.assertEqual(collision["scope"], scope)
            self.assertEqual("conflicting_box_id" in collision, scope == "this_institution")
        for strain in (self.strain, unnumbered):
            collisions = [b for b in plan["blockers"] if b["code"] == "box_namespace_in_use"
                          and b["strain_id"] == strain.pk]
            self.assertEqual({b["scope"] for b in collisions}, {"this_institution", "outside_institution"})
            self.assertEqual(self.by_id(plan["strains"])[strain.pk]["action"], "blocked")
        self.assertNotIn("PRIVATE-HOLDER", str(plan))
        self.assertNotIn(self.b.name, str(plan))
        for blocker in plan["blockers"]:
            if blocker.get("scope") == "outside_institution":
                self.assertNotIn("conflicting_box_id", blocker)
                self.assertNotIn("alias", blocker)

    def test_fingerprint_tracks_alias_profile_revision_local_code_and_assignments(self):
        box = self.box("AAU-BRE-1.001")
        fingerprint = self.read_only_plan()["plan_fingerprint"]
        BoxLocalIdAlias.objects.create(box=box, code="OLDER.001")
        next_fingerprint = self.read_only_plan()["plan_fingerprint"]
        self.assertNotEqual(fingerprint, next_fingerprint)
        fingerprint = next_fingerprint
        InstitutionSpecies.objects.filter(pk=self.profile.pk).update(revision=self.profile.revision + 1)
        next_fingerprint = self.read_only_plan()["plan_fingerprint"]
        self.assertNotEqual(fingerprint, next_fingerprint)
        fingerprint = next_fingerprint
        Box.objects.filter(pk=box.pk).update(local_code="AAU-REVIEW")
        plan = self.read_only_plan()
        self.assertTrue(plan["feasible"])
        self.assertNotEqual(fingerprint, plan["plan_fingerprint"])
        dependency = next(d for d in plan["dependencies"] if d["kind"] == "box_local_code_field")
        self.assertEqual(dependency, {"kind": "box_local_code_field", "effect": "requires_review", "count": 1})
        fingerprint = plan["plan_fingerprint"]
        LocalStrainIdentity.objects.create(strain=self.strain, species_code_assignment=self.aaa)
        self.assertNotEqual(fingerprint, self.read_only_plan()["plan_fingerprint"])

    def test_fingerprint_tracks_alias_source_merge_target_even_when_plan_stays_feasible(self):
        first = self.box("AAU-BRE-1.001")
        second = self.box("AAU-BRE-1.002")
        donor = self.box("DONOR.001", merged_into=first, status=Box.Status.INACTIVE)
        BoxLocalIdAlias.objects.create(box=donor, code="OLDER.001")
        before = self.read_only_plan()
        Box.objects.filter(pk=donor.pk).update(merged_into=second)
        after = self.read_only_plan()
        self.assertTrue(before["feasible"])
        self.assertTrue(after["feasible"])
        self.assertNotEqual(before["plan_fingerprint"], after["plan_fingerprint"])

    @skipUnless(connection.vendor == "sqlite", "Legacy fixture requires isolated SQLite trigger handling")
    def test_skipped_legacy_previous_conflicts_still_block_without_registry_rows(self):
        box = self.box("AAU-BRE-1.001", previous_global_code="UNVERIFIED.001")
        holder_strain = self.owned_strain("CAN-KOB-9", organization=self.b, species=self.other_species)
        holder = self.box("PRIVATE-HOLDER.001", organization=self.b, strain=holder_strain)
        with legacy_box_updates():
            Box.objects.filter(pk=holder.pk).update(previous_global_code="UNVERIFIED.001")
        self.assertFalse(BoxLocalIdAlias.objects.exists())
        plan = self.read_only_plan()
        self.assertFalse(plan["feasible"])
        self.assertEqual(self.by_id(plan["boxes"])[box.pk]["action"], "blocked")
        self.assertIn("local_id_history_unresolved", self.blocker_codes(plan))
        self.assertNotIn("PRIVATE-HOLDER", str(plan))
        self.assertNotIn(self.b.name, str(plan))

    @skipUnless(connection.vendor == "sqlite", "Legacy fixture requires isolated SQLite trigger handling")
    def test_unsafe_current_previous_and_registered_claims_block_for_any_owner(self):
        box = self.box("AAU-BRE-1.001", previous_global_code="PREVIOUS.001")
        BoxLocalIdAlias.objects.create(box=box, code="REGISTERED.001")
        for organization, suffix in ((self.a, "1"), (self.b, "2")):
            strain = self.owned_strain(f"CAN-KOB-{suffix}", organization=organization,
                                      species=self.other_species)
            holder = self.box(f"PRIVATE-HOLDER.00{suffix}", organization=organization, strain=strain)
            for code in (box.global_code, box.previous_global_code, "REGISTERED.001"):
                with self.subTest(owner=organization.pk, claim=code):
                    with legacy_box_updates():
                        Box.objects.filter(pk=holder.pk).update(previous_global_code=code)
                    plan = self.read_only_plan()
                    self.assertFalse(plan["feasible"])
                    self.assertEqual(self.blocker_codes(plan), ["local_id_history_unresolved"])
                    self.assertEqual(self.by_id(plan["boxes"])[box.pk]["action"], "blocked")
                    self.assertNotIn("PRIVATE-HOLDER", str(plan))
                    with legacy_box_updates():
                        Box.objects.filter(pk=holder.pk).update(previous_global_code="")

    @skipUnless(connection.vendor == "sqlite", "Legacy fixture requires isolated SQLite trigger handling")
    def test_alias_source_cycles_and_foreign_merge_paths_block(self):
        box = self.box("AAU-BRE-1.001", status=Box.Status.INACTIVE)
        donor = self.box("DONOR.001", merged_into=box, status=Box.Status.INACTIVE)
        BoxLocalIdAlias.objects.create(box=donor, code="OLDER.001")
        foreign_strain = self.owned_strain("CAN-KOB-9", organization=self.b, species=self.other_species)
        foreign = self.box("PRIVATE-HOLDER.001", organization=self.b, strain=foreign_strain)
        for target in (donor, foreign):
            with self.subTest(target=target.pk):
                with legacy_box_updates():
                    Box.objects.filter(pk=box.pk).update(merged_into=target)
                plan = self.read_only_plan()
                self.assertFalse(plan["feasible"])
                self.assertIn("local_id_history_unresolved", self.blocker_codes(plan))
                self.assertNotIn("PRIVATE-HOLDER", str(plan))
                with legacy_box_updates():
                    Box.objects.filter(pk=box.pk).update(merged_into=None)


class SpeciesCodeChangePreviewAccessTests(SpeciesCodeChangePreviewBase):
    def test_only_the_institution_admin_can_preview(self):
        self.box("AAU-BRE-1.001")
        before = _state()
        for user in (self.tech, self.viewer, self.b_admin):
            with self.subTest(user=user.username):
                self.assertEqual(self.preview(user=user).status_code, 403)
        # B's admin in B's own context cannot reach A's profile.
        self.assertEqual(self.preview(organization=self.b).status_code, 404)
        self.client.force_login(self.admin)
        url = reverse(PREVIEW_URL, args=[self.profile.pk])
        self.assertEqual(self.client.get(url, {"proposed_code": "AUR"}).status_code, 403)
        self.client.logout()
        self.assertIn(
            self.client.get(url, {"proposed_code": "AUR"}, **self.headers(self.a)).status_code, (401, 403),
        )
        self.assertEqual(self.preview().status_code, 200)
        self.assertEqual(_state(), before)

    def test_service_rechecks_authorization(self):
        for user in (self.tech, self.b_admin):
            with self.assertRaises(PermissionDenied):
                plan_species_code_change(
                    user=user, organization=self.a, profile_pk=self.profile.pk, proposed_code="AUR",
                )

    def test_input_validation_and_unknown_profile(self):
        for value, error in (("", "required"), ("  ", "required"), ("AU", "invalid_format"),
                             ("AURE", "invalid_format"), ("A1R", "invalid_format"), ("ÉAU", "invalid_format")):
            with self.subTest(value=value):
                response = self.preview(value)
                self.assertEqual((response.status_code, response.json()), (400, {"proposed_code": [error]}))
        self.client.force_login(self.admin)
        missing = self.client.get(reverse(PREVIEW_URL, args=[self.profile.pk]), **self.headers(self.a))
        self.assertEqual((missing.status_code, missing.json()), (400, {"proposed_code": ["required"]}))
        self.assertEqual(self.preview(profile=type("Missing", (), {"pk": 999999})()).status_code, 404)
        # Only GET exists: there is no mutation route.
        url = reverse(PREVIEW_URL, args=[self.profile.pk])
        for method in ("post", "put", "patch", "delete"):
            response = getattr(self.client, method)(url, **self.headers(self.a))
            self.assertEqual(response.status_code, 405)
