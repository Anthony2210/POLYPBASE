import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
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

    def test_create_uses_active_organization_not_submitted_ownership(self):
        self.client.force_login(self.admin)
        response = self.create(
            self.a, "NEW-A", organization=self.b.pk, global_identity=self.identity.pk,
            species_code_assignment=self.b_code.pk, local_identity=self.b_code.pk,
        )
        self.assertEqual(response.status_code, 201)
        strain = Strain.objects.get(code="NEW-A")
        self.assertEqual(strain.organization, self.a)
        self.assertIsNone(strain.global_identity_id)
        self.assertEqual(strain.local_identity.species_code_assignment, self.a_code)
        self.assertEqual(LocalStrainIdentity.objects.filter(strain=strain).count(), 1)
        self.assertEqual(AuditLog.objects.get(object_type="strain", object_id=str(strain.pk)).organization, self.a)
        self.assertNotIn("organization", response.json())
        self.assertNotIn("global_identity", response.json())
        self.assertEqual(self.create(self.b, "NEW-B").status_code, 201)
        self.assertEqual(Strain.objects.get(code="NEW-B").organization, self.b)
        self.assertEqual(Strain.objects.get(code="NEW-B").local_identity.species_code_assignment, self.b_code)
        response = self.client.post(reverse("api_taxonomy_strains"), data=json.dumps({
            "species": self.species.pk, "code": "NO-CONTEXT",
            "translations": {"fr": {"name": "Souche"}},
        }), content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Strain.objects.filter(code="NO-CONTEXT").exists())
        self.client.force_login(self.tech)
        self.assertEqual(self.create(self.a, "TECH-NEW").status_code, 403)
        self.assertFalse(Strain.objects.filter(code="TECH-NEW").exists())

    def test_missing_active_aaa_rejects_without_falling_back_or_leaking_foreign_code(self):
        self.client.force_login(self.admin)
        species = Species.objects.create(
            scientific_name="Unassigned species", genus_species_code="LEG"
        )
        foreign = OrganizationSpeciesCode.objects.create(
            organization=self.b, species=species, code="XYZ"
        )
        before = (Strain.objects.count(), LocalStrainIdentity.objects.count(),
                  StrainTranslation.objects.count(), AuditLog.objects.count())
        response = self.create(self.a, "NO-AAA", species=species.pk,
                               organization=self.b.pk, species_code_assignment=foreign.pk)
        self.assertEqual(response.status_code, 400)
        self.assertIn("species", response.json())
        self.assertIn("AAA", str(response.json()["species"]))
        self.assertIn("Administration", str(response.json()["species"]))
        self.assertNotIn("XYZ", json.dumps(response.json()))
        self.assertNotIn(str(foreign.pk), json.dumps(response.json()))
        self.assertEqual(
            (Strain.objects.count(), LocalStrainIdentity.objects.count(),
             StrainTranslation.objects.count(), AuditLog.objects.count()), before
        )
        # Even without any assignment anywhere, the legacy shared species code is not AAA.
        foreign.delete()
        response = self.create(self.a, "NO-AAA", species=species.pk)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Strain.objects.count(), before[0])
        self.assertEqual(LocalStrainIdentity.objects.count(), before[1])

    @patch("apps.taxonomy.api_views.create_local_strain_identity", side_effect=RuntimeError("Identity unavailable"))
    def test_identity_failure_rolls_back_strain_and_translations(self, create_identity):
        self.client.force_login(self.admin)
        before = (Strain.objects.count(), StrainTranslation.objects.count(),
                  LocalStrainIdentity.objects.count(), AuditLog.objects.count())
        with self.assertRaises(RuntimeError):
            self.create(self.a, "FAILED-IDENTITY")
        create_identity.assert_called_once()
        self.assertEqual(
            (Strain.objects.count(), StrainTranslation.objects.count(),
             LocalStrainIdentity.objects.count(), AuditLog.objects.count()), before
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
            "notes": "Updated", "number": 0,
            "translations": {"fr": {"name": "Updated strain"}},
        })
        self.assertEqual(response.status_code, 200)
        self.a_strain.refresh_from_db()
        self.assertEqual(self.a_strain.species_id, self.species.pk)
        self.assertEqual(self.a_strain.notes, "Updated")
        self.assertEqual(self.a_strain.number, 0)
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
        self.client.force_login(self.admin)
        response = self.create(self.b, self.a_strain.code)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Strain.objects.filter(species=self.species, code=self.a_strain.code).count(), 1)
