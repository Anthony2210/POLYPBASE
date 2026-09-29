import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.urls import Resolver404, resolve, reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box
from apps.organizations.models import Organization

from .models import (
    BiologicalProvenance,
    LocalStrainIdentity,
    OrganizationProvenanceCode,
    OrganizationSpeciesCode,
    Species,
    Strain,
)


class ProvenanceCodesApiTests(TestCase):
    def setUp(self):
        self.a = Organization.objects.create(name="Institution A")
        self.b = Organization.objects.create(name="Institution B")
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            username="bbb_admin", email="bbb_admin@example.org", password="secret"
        )
        self.tech = user_model.objects.create_user(
            username="bbb_tech", email="bbb_tech@example.org", password="secret"
        )
        self.viewer = user_model.objects.create_user(
            username="bbb_viewer", email="bbb_viewer@example.org", password="secret"
        )
        for user, organization, role in (
            (self.admin, self.a, OrganizationMembership.Role.ADMIN),
            (self.admin, self.b, OrganizationMembership.Role.ADMIN),
            (self.tech, self.a, OrganizationMembership.Role.LAB_TECHNICIAN),
            (self.viewer, self.a, OrganizationMembership.Role.VIEWER),
        ):
            OrganizationMembership.objects.create(user=user, organization=organization, role=role)
        self.provenance = BiologicalProvenance.objects.create(name="Shared source")
        self.other_provenance = BiologicalProvenance.objects.create(name="Other source")
        self.a_code = OrganizationProvenanceCode.objects.create(
            organization=self.a, biological_provenance=self.provenance, code="AAA"
        )
        self.b_code = OrganizationProvenanceCode.objects.create(
            organization=self.b, biological_provenance=self.provenance, code="BBB"
        )
        self.provenances_url = reverse("api_taxonomy_biological_provenances")
        self.codes_url = reverse("api_taxonomy_provenance_codes")

    def headers(self, organization):
        return {"HTTP_X_ORGANIZATION_ID": str(organization.pk)}

    def post(self, url, organization, data):
        return self.client.post(
            url, data=json.dumps(data), content_type="application/json", **self.headers(organization)
        )

    def test_collections_have_expected_paths_and_scoped_read_access(self):
        self.assertEqual(self.provenances_url, "/api/taxonomy/biological-provenances/")
        self.assertEqual(self.codes_url, "/api/taxonomy/provenance-codes/")
        expected_provenances = [
            {"id": self.provenance.pk, "name": "Shared source"},
            {"id": self.other_provenance.pk, "name": "Other source"},
        ]
        self.client.force_login(self.admin)
        for organization in (self.a, self.b):
            with self.subTest(organization=organization.pk):
                response = self.client.get(self.provenances_url, **self.headers(organization))
                self.assertEqual(response.status_code, 200)
                self.assertCountEqual(response.json(), expected_provenances)

        for organization, assignment in ((self.a, self.a_code), (self.b, self.b_code)):
            with self.subTest(organization=organization.pk):
                response = self.client.get(self.codes_url, **self.headers(organization))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), [{
                    "id": assignment.pk,
                    "biological_provenance": self.provenance.pk,
                    "biological_provenance_name": "Shared source",
                    "code": assignment.code,
                }])
                self.assertNotIn("organization", response.json()[0])

        self.client.force_login(self.tech)
        self.assertCountEqual(
            self.client.get(self.provenances_url, **self.headers(self.a)).json(),
            expected_provenances,
        )
        self.assertEqual(
            self.client.get(self.codes_url, **self.headers(self.a)).json()[0]["code"], "AAA"
        )
        self.client.force_login(self.viewer)
        for url in (self.provenances_url, self.codes_url):
            self.assertEqual(self.client.get(url, **self.headers(self.a)).status_code, 403)

    def test_create_provenance_is_shared_allows_duplicate_names_and_audits_each_write(self):
        self.client.force_login(self.admin)
        for organization in (self.a, self.b):
            response = self.post(self.provenances_url, organization, {"name": "Shared source"})
            self.assertEqual(response.status_code, 201)
            created = BiologicalProvenance.objects.get(pk=response.json()["id"])
            self.assertEqual(response.json(), {"id": created.pk, "name": "Shared source"})
            audit = AuditLog.objects.get(object_type="biological_provenance", object_id=str(created.pk))
            self.assertEqual(
                (audit.organization_id, audit.user_id, audit.action),
                (organization.pk, self.admin.pk, AuditLog.Action.CREATION),
            )
        self.assertEqual(BiologicalProvenance.objects.filter(name="Shared source").count(), 3)
        self.assertEqual(AuditLog.objects.count(), 2)
        self.assertFalse(LocalStrainIdentity.objects.exists())

    def test_create_code_binds_active_organization_and_audits_without_creating_strains(self):
        self.client.force_login(self.admin)
        response = self.post(self.codes_url, self.a, {
            "biological_provenance": self.other_provenance.pk, "code": "CCC",
        })
        self.assertEqual(response.status_code, 201)
        assignment = OrganizationProvenanceCode.objects.get(pk=response.json()["id"])
        self.assertEqual((assignment.organization_id, assignment.biological_provenance_id, assignment.code),
                         (self.a.pk, self.other_provenance.pk, "CCC"))
        self.assertEqual(response.json(), {
            "id": assignment.pk, "biological_provenance": self.other_provenance.pk,
            "biological_provenance_name": "Other source", "code": "CCC",
        })
        audit = AuditLog.objects.get()
        self.assertEqual(
            (audit.organization_id, audit.user_id, audit.action, audit.object_type, audit.object_id),
            (self.a.pk, self.admin.pk, AuditLog.Action.CREATION,
             "organization_provenance_code", str(assignment.pk)),
        )
        self.assertFalse(Strain.objects.exists())
        self.assertFalse(LocalStrainIdentity.objects.exists())

    def test_strict_payloads_unknown_ids_and_spoofing_leave_no_writes(self):
        self.client.force_login(self.admin)
        for payload, field in (
            ({}, "name"),
            ({"name": ""}, "name"),
            ({"name": "   "}, "name"),
            ({"name": "New", "organization": self.b.pk}, "organization"),
            ({"name": "New", "code": "ZZZ"}, "code"),
            ({"name": "New", "id": 999}, "id"),
        ):
            with self.subTest(url=self.provenances_url, payload=payload):
                response = self.post(self.provenances_url, self.a, payload)
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json())
        for payload, field in (
            ({}, "biological_provenance"),
            ({"biological_provenance": self.other_provenance.pk}, "code"),
            ({"biological_provenance": 999999, "code": "CCC"}, "biological_provenance"),
            ({"biological_provenance": self.other_provenance.pk, "code": ""}, "code"),
            ({"biological_provenance": self.other_provenance.pk, "code": "LONG"}, "code"),
            ({"biological_provenance": self.other_provenance.pk, "code": "CCC",
              "organization": self.b.pk}, "organization"),
            ({"biological_provenance": self.other_provenance.pk, "code": "CCC",
              "name": "Spoofed"}, "name"),
            ({"biological_provenance": self.other_provenance.pk, "code": "CCC",
              "global_identity": 1}, "global_identity"),
        ):
            with self.subTest(url=self.codes_url, payload=payload):
                response = self.post(self.codes_url, self.a, payload)
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json())
        self.assertEqual(BiologicalProvenance.objects.count(), 2)
        self.assertEqual(OrganizationProvenanceCode.objects.count(), 2)
        self.assertFalse(AuditLog.objects.exists())

    def test_duplicate_source_and_code_conflicts_are_distinct_and_local(self):
        self.client.force_login(self.admin)
        response = self.post(self.codes_url, self.a, {
            "biological_provenance": self.provenance.pk, "code": "CCC",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("biological_provenance", response.json())
        response = self.post(self.codes_url, self.a, {
            "biological_provenance": self.other_provenance.pk, "code": "AAA",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("code", response.json())

        # Both the source and a code already used elsewhere are valid locally.
        response = self.post(self.codes_url, self.b, {
            "biological_provenance": self.other_provenance.pk, "code": "AAA",
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            OrganizationProvenanceCode.objects.get(pk=response.json()["id"]).organization_id,
            self.b.pk,
        )
        response = self.post(self.codes_url, self.a, {
            "biological_provenance": self.other_provenance.pk, "code": "BBB",
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.post(self.codes_url, self.b, {
            "biological_provenance": self.other_provenance.pk, "code": "CCC",
        }).status_code, 400)
        self.assertEqual(OrganizationProvenanceCode.objects.count(), 4)
        self.assertEqual(AuditLog.objects.count(), 2)

    def test_permissions_follow_active_membership_and_organization_header(self):
        for url, data in (
            (self.provenances_url, {"name": "New"}),
            (self.codes_url, {"biological_provenance": self.other_provenance.pk, "code": "CCC"}),
        ):
            self.assertIn(self.client.get(url, **self.headers(self.a)).status_code, (401, 403))
            self.assertIn(self.post(url, self.a, data).status_code, (401, 403))
        self.client.force_login(self.tech)
        for url, data in (
            (self.provenances_url, {"name": "New"}),
            (self.codes_url, {"biological_provenance": self.other_provenance.pk, "code": "CCC"}),
        ):
            self.assertEqual(self.post(url, self.a, data).status_code, 403)
            self.assertEqual(self.client.get(url, **self.headers(self.b)).status_code, 403)
            self.assertEqual(self.post(url, self.b, data).status_code, 403)
        self.client.force_login(self.viewer)
        for url, data in (
            (self.provenances_url, {"name": "New"}),
            (self.codes_url, {"biological_provenance": self.other_provenance.pk, "code": "CCC"}),
        ):
            self.assertEqual(self.post(url, self.a, data).status_code, 403)

        self.client.force_login(self.admin)
        membership = OrganizationMembership.objects.get(user=self.admin, organization=self.b)
        membership.role = OrganizationMembership.Role.LAB_TECHNICIAN
        membership.save(update_fields=["role"])
        for url, data in (
            (self.provenances_url, {"name": "New"}),
            (self.codes_url, {"biological_provenance": self.other_provenance.pk, "code": "CCC"}),
        ):
            self.assertEqual(self.client.get(url, **self.headers(self.b)).status_code, 200)
            self.assertEqual(self.post(url, self.b, data).status_code, 403)
        self.assertEqual(self.client.get(self.codes_url, **self.headers(self.a)).status_code, 200)
        membership.role = OrganizationMembership.Role.VIEWER
        membership.save(update_fields=["role"])
        for url in (self.provenances_url, self.codes_url):
            self.assertEqual(self.client.get(url, **self.headers(self.b)).status_code, 403)
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        for url, data in (
            (self.provenances_url, {"name": "New"}),
            (self.codes_url, {"biological_provenance": self.other_provenance.pk, "code": "CCC"}),
        ):
            self.assertEqual(self.client.get(url, **self.headers(self.b)).status_code, 403)
            self.assertEqual(self.post(url, self.b, data).status_code, 403)
            self.assertEqual(self.client.get(url, HTTP_X_ORGANIZATION_ID="999999").status_code, 403)
            self.assertEqual(self.client.post(
                url, data=json.dumps(data), content_type="application/json",
                HTTP_X_ORGANIZATION_ID="999999",
            ).status_code, 403)
        self.assertEqual(BiologicalProvenance.objects.count(), 2)
        self.assertEqual(OrganizationProvenanceCode.objects.count(), 2)
        self.assertFalse(AuditLog.objects.exists())

    def test_only_collection_get_and_post_are_exposed(self):
        self.client.force_login(self.admin)
        for url, pk in ((self.provenances_url, self.provenance.pk),
                        (self.codes_url, self.a_code.pk)):
            for method in ("put", "patch", "delete"):
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(
                        url, data=json.dumps({"name": "Edited", "code": "XYZ"}),
                        content_type="application/json", **self.headers(self.a)
                    )
                    self.assertEqual(response.status_code, 405)
            with self.assertRaises(Resolver404):
                resolve(f"{url}{pk}/")
            self.assertEqual(self.client.get(f"{url}{pk}/", **self.headers(self.a)).status_code, 404)
        self.assertEqual(OrganizationProvenanceCode.objects.count(), 2)
        self.assertFalse(AuditLog.objects.exists())

    @patch("apps.taxonomy.api_views.AuditLog.objects.create", side_effect=RuntimeError("Audit unavailable"))
    def test_audit_failure_rolls_back_both_creates(self, create_audit):
        self.client.force_login(self.admin)
        with self.assertRaises(RuntimeError):
            self.post(self.provenances_url, self.a, {"name": "New source"})
        self.assertFalse(BiologicalProvenance.objects.filter(name="New source").exists())
        with self.assertRaises(RuntimeError):
            self.post(self.codes_url, self.a, {
                "biological_provenance": self.other_provenance.pk, "code": "CCC",
            })
        self.assertFalse(OrganizationProvenanceCode.objects.filter(
            organization=self.a, biological_provenance=self.other_provenance
        ).exists())
        self.assertEqual(create_audit.call_count, 2)
        self.assertFalse(AuditLog.objects.exists())

    def test_known_constraint_races_map_to_field_errors_without_audit(self):
        self.client.force_login(self.admin)
        sqlite_prefix = (
            "UNIQUE constraint failed: taxonomy_organizationprovenancecode.organization_id, "
            "taxonomy_organizationprovenancecode."
        )
        for constraint, field in (
            ("unique_provenance_code_per_organization_source", "biological_provenance"),
            ("unique_provenance_code_per_organization_code", "code"),
            (f"{sqlite_prefix}biological_provenance_id", "biological_provenance"),
            (f"{sqlite_prefix}code", "code"),
        ):
            with self.subTest(constraint=constraint):
                error = IntegrityError("duplicate")
                if not constraint.startswith("UNIQUE"):
                    error.__cause__ = type(
                        "DatabaseErrorCause", (Exception,),
                        {"diag": type("Diag", (), {"constraint_name": constraint})()},
                    )()
                else:
                    error = IntegrityError(constraint)
                with patch.object(OrganizationProvenanceCode._default_manager, "create", side_effect=error):
                    response = self.post(self.codes_url, self.a, {
                        "biological_provenance": self.other_provenance.pk, "code": "CCC",
                    })
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json())
        with (
            patch.object(OrganizationProvenanceCode._default_manager, "create",
                         side_effect=IntegrityError("unrelated constraint")),
            self.assertRaises(IntegrityError),
        ):
            self.post(self.codes_url, self.a, {
                "biological_provenance": self.other_provenance.pk, "code": "CCC",
            })
        self.assertEqual(OrganizationProvenanceCode.objects.count(), 2)
        self.assertFalse(AuditLog.objects.exists())

    def test_assigning_bbb_does_not_infer_historical_strain_provenance(self):
        species = Species.objects.create(scientific_name="Historical species")
        aaa = OrganizationSpeciesCode.objects.create(
            organization=self.a, species=species, code="HIS"
        )
        strain = Strain.objects.create(
            organization=self.a, species=species, code="HIS-1", origin_code="LEG"
        )
        identity = LocalStrainIdentity.objects.create(strain=strain, species_code_assignment=aaa)
        box = Box.objects.create(
            organization=self.a, strain=strain, global_code="HIS-1.001", box_number="001"
        )
        self.client.force_login(self.admin)
        response = self.post(self.codes_url, self.a, {
            "biological_provenance": self.other_provenance.pk, "code": "LEG",
        })
        self.assertEqual(response.status_code, 201)
        strain.refresh_from_db()
        identity.refresh_from_db()
        box.refresh_from_db()
        self.assertIsNone(identity.provenance_code_assignment_id)
        self.assertEqual((strain.code, strain.origin_code, box.global_code),
                         ("HIS-1", "LEG", "HIS-1.001"))
        self.assertEqual((Strain.objects.count(), LocalStrainIdentity.objects.count(), Box.objects.count()),
                         (1, 1, 1))
        self.assertEqual(AuditLog.objects.count(), 1)
