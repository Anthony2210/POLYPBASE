import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse


from apps.accounts.models import OrganizationMembership, UserPreference
from apps.audit.models import AuditLog
from apps.cultures.models import Box
from apps.organizations.models import Organization
from apps.taxonomy.models import (GlobalStrainIdentity, LocalStrainIdentity,
                                  OrganizationSpeciesCode, Species, Strain, StrainTranslation)


class StrainScopingApiTests(TestCase):
    def setUp(self):
        self.a = Organization.objects.create(name="Institution A")
        self.b = Organization.objects.create(name="Institution B")
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(username="scope_admin", email="scope_admin@example.org", password="secret")
        self.b_admin = user_model.objects.create_user(username="scope_b_admin", email="scope_b_admin@example.org", password="secret")
        self.tech = user_model.objects.create_user(username="scope_tech", email="scope_tech@example.org", password="secret")
        for user, organization, role in (
            (self.admin, self.a, OrganizationMembership.Role.ADMIN),
            (self.admin, self.b, OrganizationMembership.Role.ADMIN),
            (self.b_admin, self.b, OrganizationMembership.Role.ADMIN),
            (self.tech, self.a, OrganizationMembership.Role.LAB_TECHNICIAN),
        ):
            OrganizationMembership.objects.create(user=user, organization=organization, role=role)
        self.species = Species.objects.create(scientific_name="Shared species", genus_species_code="OLD")
        self.a_code = OrganizationSpeciesCode.objects.create(
            organization=self.a, species=self.species, code="AAA"
        )
        self.b_code = OrganizationSpeciesCode.objects.create(
            organization=self.b, species=self.species, code="BBB"
        )
        self.identity = GlobalStrainIdentity.objects.create()
        self.a_strain = Strain.objects.create(species=self.species, code="A-1", organization=self.a, global_identity=self.identity)
        self.b_strain = Strain.objects.create(species=self.species, code="B-1", organization=self.b, global_identity=self.identity)
        self.legacy = Strain.objects.create(species=self.species, code="OLD-1")
        self.b_only = Strain.objects.create(species=self.species, code="OLD-2")
        self.orphan = Strain.objects.create(species=self.species, code="OLD-3")
        Box.objects.create(organization=self.a, strain=self.legacy, global_code="OLD-1.001", box_number="001", status=Box.Status.INACTIVE)
        Box.objects.create(organization=self.b, strain=self.legacy, global_code="OLD-1.002", box_number="002")
        Box.objects.create(organization=self.b, strain=self.b_only, global_code="OLD-2.001", box_number="001")

    def headers(self, organization):
        return {"HTTP_X_ORGANIZATION_ID": str(organization.pk)}

    def create(self, active_organization, code, **extra):
        return self.client.post(reverse("api_taxonomy_strains"), data=json.dumps({
            "species": self.species.pk, "code": code,
            "translations": {"fr": {"name": "Souche"}}, **extra,
        }), content_type="application/json", **self.headers(active_organization))

    def test_manual_creation_is_closed_whatever_payload_or_context(self):
        """Strains are created only from an institutional Species (server-built AAA-BBB-X).

        Ownership from context, required AAA without fallback, identity, audit and
        rollback are covered on that endpoint by test_institution_strains.
        """
        species = Species.objects.create(scientific_name="Unassigned species", genus_species_code="LEG")
        foreign = OrganizationSpeciesCode.objects.create(organization=self.b, species=species, code="XYZ")
        before = (Strain.objects.count(), LocalStrainIdentity.objects.count(),
                  StrainTranslation.objects.count(), AuditLog.objects.count())
        self.client.force_login(self.admin)
        for organization, code, extra in (
            (self.a, "NEW-A", {"organization": self.b.pk, "global_identity": self.identity.pk,
                               "species_code_assignment": self.b_code.pk, "local_identity": self.b_code.pk}),
            (self.b, "NEW-B", {}),
            (self.a, "NO-AAA", {"species": species.pk, "species_code_assignment": foreign.pk}),
            (self.b, self.a_strain.code, {}),
        ):
            with self.subTest(code=code):
                response = self.create(organization, code, **extra)
                self.assertEqual(response.status_code, 410)
                self.assertEqual(response.json(), {"code": "superseded_by_institution_species_strains"})
                self.assertNotIn("XYZ", response.content.decode())
        response = self.client.post(reverse("api_taxonomy_strains"), data=json.dumps({
            "species": self.species.pk, "code": "NO-CONTEXT",
            "translations": {"fr": {"name": "Souche"}},
        }), content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.tech)
        self.assertEqual(self.create(self.a, "TECH-NEW").status_code, 403)
        self.assertEqual(
            (Strain.objects.count(), LocalStrainIdentity.objects.count(),
             StrainTranslation.objects.count(), AuditLog.objects.count()), before
        )

    def test_references_are_scoped_and_species_count_does_not_leak(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("api_taxonomy_references"), **self.headers(self.a))
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["id"] for row in response.json()["strains"]}, {self.a_strain.pk, self.legacy.pk})
        self.assertFalse(LocalStrainIdentity.objects.filter(strain=self.legacy).exists())
        self.assertEqual(response.json()["species"][0]["strain_count"], 2)
        response = self.client.get(reverse("api_taxonomy_references"), **self.headers(self.b))
        self.assertEqual({row["id"] for row in response.json()["strains"]}, {self.b_strain.pk, self.legacy.pk, self.b_only.pk})
        self.assertEqual(response.json()["species"][0]["strain_count"], 3)
        self.client.force_login(self.tech)
        response = self.client.get(reverse("api_taxonomy_references"), **self.headers(self.a))
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["id"] for row in response.json()["strains"]}, {self.a_strain.pk, self.legacy.pk})

    def test_patch_only_owned_strains_without_cross_institution_audit(self):
        self.client.force_login(self.admin)
        url = lambda pk: reverse("api_taxonomy_strains_detail", args=[pk])
        payload = json.dumps({"notes": "Updated", "organization": self.b.pk, "global_identity": self.identity.pk})
        for strain in (self.b_strain, self.legacy):
            response = self.client.patch(url(strain.pk), data=payload, content_type="application/json", **self.headers(self.a))
            self.assertEqual(response.status_code, 404)
            strain.refresh_from_db()
            self.assertEqual(strain.notes, "")
        self.assertFalse(AuditLog.objects.exists())
        response = self.client.patch(url(self.a_strain.pk), data=payload, content_type="application/json", **self.headers(self.a))
        self.assertEqual(response.status_code, 200)
        self.a_strain.refresh_from_db()
        self.assertEqual(self.a_strain.organization, self.a)
        self.assertEqual(self.a_strain.notes, "Updated")
        self.assertEqual(AuditLog.objects.count(), 1)
        response = self.client.patch(url(self.a_strain.pk), data=payload, content_type="application/json", **self.headers(self.b))
        self.assertEqual(response.status_code, 404)

    def patch_strain(self, strain, payload, organization=None):
        return self.client.patch(
            reverse("api_taxonomy_strains_detail", args=[strain.pk]),
            data=json.dumps(payload), content_type="application/json",
            **self.headers(organization or self.a),
        )

    def identify_strain(self):
        return LocalStrainIdentity.objects.create(
            strain=self.a_strain, species_code_assignment=self.a_code,
        )

    def assert_identity_unchanged(self, identity):
        identity.refresh_from_db()
        self.assertEqual(identity.strain_id, self.a_strain.pk)
        self.assertEqual(identity.species_code_assignment_id, self.a_code.pk)
        self.assertIsNone(identity.provenance_code_assignment_id)
        self.assertEqual(identity.species_code_assignment.species_id, self.species.pk)
        self.assertEqual(LocalStrainIdentity.objects.filter(strain=self.a_strain).count(), 1)

    def test_identified_species_change_is_rejected_with_or_without_target_aaa(self):
        self.client.force_login(self.admin)
        identity = self.identify_strain()
        target = Species.objects.create(scientific_name="Target species")
        StrainTranslation.objects.create(
            strain=self.a_strain, language_code="fr", name="Original strain",
        )
        box = Box.objects.create(
            organization=self.a, strain=self.a_strain,
            global_code="A-1.001", box_number="001",
        )
        before_audit = AuditLog.objects.count()
        for has_target_aaa in (False, True):
            with self.subTest(has_target_aaa=has_target_aaa):
                if has_target_aaa:
                    OrganizationSpeciesCode.objects.create(
                        organization=self.a, species=target, code="CCC",
                    )
                response = self.patch_strain(self.a_strain, {
                    "species": target.pk, "notes": "Rejected notes",
                    "translations": {"fr": {"name": "Rejected translation"}},
                })
                self.assertEqual(response.status_code, 400)
                self.assertIn("species", response.json())
                self.a_strain.refresh_from_db()
                self.assertEqual(self.a_strain.species_id, self.species.pk)
                self.assertEqual(self.a_strain.notes, "")
                self.assertEqual(self.a_strain.code, "A-1")
                self.assertEqual(self.a_strain.global_identity_id, self.identity.pk)
                self.assertEqual(self.a_strain.translations.get().name, "Original strain")
                self.assert_identity_unchanged(identity)
                self.assertEqual(AuditLog.objects.count(), before_audit)
                box.refresh_from_db()
                self.assertEqual((box.global_code, box.box_number), ("A-1.001", "001"))

    def test_identified_same_species_payload_preserves_patch_semantics(self):
        self.client.force_login(self.admin)
        identity = self.identify_strain()
        for payload in (
            {"species": self.species.pk},
            {"species": self.species.pk, "notes": "Updated"},
        ):
            with self.subTest(payload=payload):
                before = AuditLog.objects.count()
                response = self.patch_strain(self.a_strain, payload)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["species"], self.species.pk)
                self.assertEqual(AuditLog.objects.count(), before + 1)
                self.assert_identity_unchanged(identity)
        self.a_strain.refresh_from_db()
        self.assertEqual(self.a_strain.notes, "Updated")

    def test_identified_non_species_fields_remain_editable_and_audited(self):
        self.client.force_login(self.admin)
        identity = self.identify_strain()
        response = self.patch_strain(self.a_strain, {
            "notes": "Updated",
            "translations": {"fr": {"name": "Updated strain"}},
        })
        self.assertEqual(response.status_code, 200)
        self.a_strain.refresh_from_db()
        self.assertEqual(self.a_strain.species_id, self.species.pk)
        self.assertEqual(self.a_strain.notes, "Updated")
        self.assertIsNone(self.a_strain.number)
        self.assertEqual(self.a_strain.translations.get().name, "Updated strain")
        self.assert_identity_unchanged(identity)
        audit = AuditLog.objects.get()
        self.assertEqual(audit.action, AuditLog.Action.UPDATE)
        self.assertEqual(audit.object_type, "strain")
        self.assertEqual(audit.object_id, str(self.a_strain.pk))
        self.assertEqual(audit.organization, self.a)
        self.assertEqual(audit.user, self.admin)

    def test_identityless_owned_species_change_does_not_require_aaa(self):
        self.client.force_login(self.admin)
        target = Species.objects.create(scientific_name="Identityless target")
        response = self.patch_strain(self.a_strain, {"species": target.pk})
        self.assertEqual(response.status_code, 200)
        self.a_strain.refresh_from_db()
        self.assertEqual(self.a_strain.species_id, target.pk)
        self.assertFalse(LocalStrainIdentity.objects.filter(strain=self.a_strain).exists())
        self.assertFalse(OrganizationSpeciesCode.objects.filter(species=target).exists())
        self.assertEqual(AuditLog.objects.get().action, AuditLog.Action.UPDATE)

    def test_identified_patch_permissions_remain_scoped(self):
        identity = self.identify_strain()
        target = Species.objects.create(scientific_name="Permission target")
        viewer = get_user_model().objects.create_user(
                    username="strain_viewer", email="strain_viewer@example.org",
                )
        OrganizationMembership.objects.create(
            user=viewer, organization=self.a, role=OrganizationMembership.Role.VIEWER,
        )
        for user, organization, expected_status in (
            (self.admin, self.b, 404),
            (self.b_admin, self.a, 403),
            (self.tech, self.a, 403),
            (viewer, self.a, 403),
        ):
            with self.subTest(user=user.username, organization=organization.pk):
                self.client.force_login(user)
                response = self.patch_strain(
                    self.a_strain, {"species": target.pk}, organization,
                )
                self.assertEqual(response.status_code, expected_status)
        self.a_strain.refresh_from_db()
        self.assertEqual(self.a_strain.species_id, self.species.pk)
        self.assert_identity_unchanged(identity)
        self.assertFalse(AuditLog.objects.exists())

    @patch("apps.taxonomy.api_views.AuditLog.objects.create", side_effect=RuntimeError("Audit unavailable"))
    def test_identified_permitted_patch_rolls_back_when_audit_fails(self, create_audit):
        self.client.force_login(self.admin)
        identity = self.identify_strain()
        StrainTranslation.objects.create(
            strain=self.a_strain, language_code="fr", name="Original strain",
        )
        with self.assertRaises(RuntimeError):
            self.patch_strain(self.a_strain, {
                "species": self.species.pk, "notes": "Rejected notes",
                "translations": {"fr": {"name": "Rejected translation"}},
            })
        self.a_strain.refresh_from_db()
        self.assertEqual(self.a_strain.species_id, self.species.pk)
        self.assertEqual(self.a_strain.notes, "")
        self.assertEqual(self.a_strain.translations.get().name, "Original strain")
        self.assert_identity_unchanged(identity)
        self.assertFalse(AuditLog.objects.exists())
        create_audit.assert_called_once()

    def test_identified_species_rejection_is_localized(self):
        self.client.force_login(self.admin)
        self.identify_strain()
        target = Species.objects.create(scientific_name="Localized target")
        for language, expected in (
            ("fr", "L'espèce d'une souche possédant une identité locale"),
            ("en", "The species of a strain with a local identity"),
        ):
            with self.subTest(language=language):
                UserPreference.objects.update_or_create(
                    user=self.admin, defaults={"interface_language": language},
                )
                response = self.patch_strain(self.a_strain, {"species": target.pk})
                self.assertEqual(response.status_code, 400)
                self.assertIn(expected, response.json()["species"])

    def test_global_code_uniqueness_still_rejects_other_organization(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Strain.objects.create(species=self.species, organization=self.b, code=self.a_strain.code)
        self.assertEqual(Strain.objects.filter(species=self.species, code=self.a_strain.code).count(), 1)


class StrainIdentityPatchTests(TestCase):
    """Ordinary PATCH never rewrites AAA-BBB-X identity fields (code, number, origin_code)."""

    # Reuse the scoping fixture (institutions, shared/legacy Strains) without its tests.
    headers = StrainScopingApiTests.headers
    patch_strain = StrainScopingApiTests.patch_strain

    def setUp(self):
        StrainScopingApiTests.setUp(self)
        self.a_strain.number = 1
        self.a_strain.origin_code = "BBB"
        self.a_strain.save(update_fields=["number", "origin_code"])
        StrainTranslation.objects.create(strain=self.a_strain, language_code="fr", name="Original strain")
        self.box = Box.objects.create(
            organization=self.a, strain=self.a_strain, global_code="A-1.001", box_number="001",
        )

    def snapshot(self, strain):
        strain.refresh_from_db()
        return (
            strain.species_id, strain.code, strain.number, strain.origin_code, strain.notes,
            strain.organization_id, strain.global_identity_id,
            list(strain.translations.values_list("language_code", "name")),
            list(Box.objects.filter(strain=strain).order_by("pk").values_list("global_code", "box_number")),
            list(LocalStrainIdentity.objects.filter(strain=strain).values_list(
                "species_code_assignment_id", "provenance_code_assignment_id")),
        )

    def assert_rejected(self, response, fields):
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "strain_identity_immutable")
        self.assertEqual(response.json()["fields"], fields)

    def test_identity_field_changes_are_rejected_with_or_without_local_identity(self):
        self.client.force_login(self.admin)
        cases = (
            ({"code": "A-2"}, ["code"]),
            ({"code": "a-1.extra"}, ["code"]),
            ({"number": 2}, ["number"]),
            ({"number": 0}, ["number"]),
            ({"number": None}, ["number"]),
            ({"number": "not a number"}, ["number"]),
            ({"origin_code": "XYZ"}, ["origin_code"]),
            ({"origin_code": ""}, ["origin_code"]),
            ({"code": "A-9", "number": 9, "origin_code": "CCC"}, ["code", "number", "origin_code"]),
        )
        for identified in (False, True):
            if identified:
                LocalStrainIdentity.objects.create(strain=self.a_strain, species_code_assignment=self.a_code)
            for payload, fields in cases:
                with self.subTest(identified=identified, payload=payload):
                    before, audits = self.snapshot(self.a_strain), AuditLog.objects.count()
                    response = self.patch_strain(self.a_strain, {
                        **payload, "species": self.species.pk, "notes": "Rejected notes",
                        "translations": {"fr": {"name": "Rejected translation"}},
                    })
                    self.assert_rejected(response, fields)
                    self.assertEqual(self.snapshot(self.a_strain), before)
                    self.assertEqual(AuditLog.objects.count(), audits)

    def test_identity_rejected_for_owned_strain_without_boxes_or_local_identity(self):
        self.client.force_login(self.admin)
        unused = Strain.objects.create(species=self.species, code="A-UNUSED", organization=self.a)
        before = self.snapshot(unused)
        response = self.patch_strain(unused, {"code": "A-FIXED"})
        self.assert_rejected(response, ["code"])
        self.assertEqual(self.snapshot(unused), before)
        self.assertFalse(AuditLog.objects.exists())

    def test_identity_unchanged_values_and_metadata_edits_are_allowed_and_audited(self):
        self.client.force_login(self.admin)
        LocalStrainIdentity.objects.create(strain=self.a_strain, species_code_assignment=self.a_code)
        response = self.patch_strain(self.a_strain, {
            "species": self.species.pk, "code": " a-1 ", "number": 1, "origin_code": "bbb",
            "notes": "Updated", "translations": {"fr": {"name": "Updated strain"}},
        })
        self.assertEqual(response.status_code, 200)
        response = self.patch_strain(self.a_strain, {"notes": "Metadata only"})
        self.assertEqual(response.status_code, 200)
        self.a_strain.refresh_from_db()
        self.assertEqual(
            (self.a_strain.code, self.a_strain.number, self.a_strain.origin_code, self.a_strain.notes),
            ("A-1", 1, "BBB", "Metadata only"),
        )
        self.assertEqual(self.a_strain.translations.get().name, "Updated strain")
        self.box.refresh_from_db()
        self.assertEqual(self.box.global_code, "A-1.001")
        self.assertEqual(AuditLog.objects.filter(object_type="strain", action=AuditLog.Action.UPDATE).count(), 2)

    def test_identity_shared_legacy_and_foreign_strains_stay_unreachable(self):
        """Unowned legacy Strains (Boxes in A and B) and foreign Strains are 404, not 409."""
        self.client.force_login(self.admin)
        legacy_before, b_before = self.snapshot(self.legacy), self.snapshot(self.b_strain)
        for organization, strain in ((self.a, self.legacy), (self.b, self.legacy), (self.a, self.b_strain)):
            with self.subTest(organization=organization.pk, strain=strain.code):
                response = self.patch_strain(strain, {"code": "HIJACK", "number": 7}, organization)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("strain_identity_immutable", response.content.decode())
        self.assertEqual(self.snapshot(self.legacy), legacy_before)
        self.assertEqual(self.snapshot(self.b_strain), b_before)
        self.assertFalse(AuditLog.objects.exists())

    def test_identity_rejection_follows_institution_authorization(self):
        b_before = self.snapshot(self.b_strain)
        self.client.force_login(self.b_admin)
        self.assertEqual(self.patch_strain(self.a_strain, {"code": "X"}, self.a).status_code, 403)
        self.assert_rejected(self.patch_strain(self.b_strain, {"origin_code": "ZZZ"}, self.b), ["origin_code"])
        self.client.force_login(self.tech)
        self.assertEqual(self.patch_strain(self.a_strain, {"code": "X"}).status_code, 403)
        self.assertEqual(self.snapshot(self.b_strain), b_before)
        self.a_strain.refresh_from_db()
        self.assertEqual(self.a_strain.code, "A-1")
        self.assertFalse(AuditLog.objects.exists())
