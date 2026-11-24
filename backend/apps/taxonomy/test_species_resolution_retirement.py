"""Retired implicit Species resolution and its hidden journal noise."""

import json
from unittest.mock import patch

from django.urls import reverse

from apps.audit.models import AuditLog
from apps.audit.services import impactful_audit_logs

from .models import InstitutionSpecies, OrganizationSpeciesCode, Species, Strain
from .test_institution_species_api import (
    DETAIL_URL, HISTORICAL_URL, LIST_URL, InstitutionSpeciesApiBase, _snapshot,
)

RESOLVE_URL = "api_admin_institution_species_resolve"


class RetiredResolutionTests(InstitutionSpeciesApiBase):
    def setUp(self):
        super().setUp()
        self.species = Species.objects.create(scientific_name="Acromitus hardenbergi")
        # Historical use without a profile or code assignment: the old resolve
        # path would have created both.
        Strain.objects.create(organization=self.a, species=self.species, code="AHA-FRA-1")
        network = patch("apps.taxonomy.worms.urlopen", side_effect=AssertionError("No network allowed"))
        network.start()
        self.addCleanup(network.stop)

    def state(self):
        return (
            _snapshot(),
            list(InstitutionSpecies.objects.order_by("pk").values()),
            list(OrganizationSpeciesCode.objects.order_by("pk").values()),
            list(AuditLog.objects.order_by("pk").values()),
        )

    def post_resolve(self, user, organization, action="strain"):
        self.client.force_login(user)
        return self.client.post(
            reverse(RESOLVE_URL), data=json.dumps({"species_id": self.species.pk, "action": action}),
            content_type="application/json", **self.headers(organization),
        )

    def test_list_and_historical_detail_navigation_write_nothing(self):
        before = self.state()
        self.client.force_login(self.admin)
        self.assertEqual(self.get(LIST_URL, self.a).status_code, 200)
        body = self.get(HISTORICAL_URL, self.a, self.species.pk).json()
        self.assertIsNone(body["institution_species_id"])
        # The recorded AHA is recoverable by an explicit Strain creation only.
        self.assertIsNone(body["strain_creation_issue"])
        self.assertTrue(body["can_create_strain"])
        self.assertEqual(self.state(), before)

    def test_retired_resolve_never_creates_profile_code_or_audit(self):
        before = self.state()
        for action in ("edit", "strain"):
            response = self.post_resolve(self.admin, self.a, action)
            self.assertEqual(response.status_code, 410, response.content)
            self.assertEqual(response.json(), {"code": "institution_species_resolution_retired"})
        self.assertEqual(self.state(), before)
        self.assertFalse(InstitutionSpecies.objects.exists())
        self.assertFalse(OrganizationSpeciesCode.objects.exists())

    def test_retired_resolve_still_rejects_other_institution_and_non_admins(self):
        before = self.state()
        # Admin of B targeting A's context, and non-admins of A.
        self.assertEqual(self.post_resolve(self.b_admin, self.a).status_code, 403)
        for user in (self.tech, self.viewer):
            self.assertEqual(self.post_resolve(user, self.a).status_code, 403)
        self.client.logout()
        response = self.client.post(reverse(RESOLVE_URL), data="{}", content_type="application/json",
                                    **self.headers(self.a))
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(self.state(), before)

    def test_established_profile_still_edits_and_creates_strain(self):
        profile = InstitutionSpecies.objects.create(
            organization=self.a, species=self.species, scientific_name="Acromitus hardenbergi",
        )
        OrganizationSpeciesCode.objects.create(organization=self.a, species=self.species, code="AHA")
        self.client.force_login(self.admin)
        detail = self.get(DETAIL_URL, self.a, profile.pk).json()
        self.assertTrue(detail["can_create_strain"])
        response = self.edit(self.a, profile.pk, {
            "expected_revision": 1, "scientific_name": "Acromitus hardenbergi", "worms_aphia_id": None,
        })
        self.assertIn(response.status_code, (200, 204), response.content)
        # Other institution cannot reach this profile.
        self.client.force_login(self.b_admin)
        self.assertEqual(self.get(DETAIL_URL, self.b, profile.pk).status_code, 404)


class TechnicalActivityNoiseTests(InstitutionSpeciesApiBase):
    def setUp(self):
        super().setUp()
        self.species = Species.objects.create(scientific_name="Versuriga anadyomene")

    def log(self, organization, object_type, source, user=None):
        return AuditLog.objects.create(
            organization=organization, user=user or self.admin, action=AuditLog.Action.CREATION,
            object_type=object_type, object_id="1", description="technical",
            metadata={"source": source, "species_id": self.species.pk} if source else {},
        )

    def ids(self, url_name, organization, user):
        self.client.force_login(user)
        response = self.client.get(reverse(url_name), **self.headers(organization))
        self.assertEqual(response.status_code, 200, response.content)
        return {entry["id"] for entry in response.json()["results"]}

    def test_personal_journal_hides_only_resolution_side_effects(self):
        profile_noise = self.log(self.a, "institution_species", "institution_species_resolution")
        code_noise = self.log(self.a, "organization_species_code", "institution_species_resolution")
        manual_code = self.log(self.a, "organization_species_code", "manual_assignment")
        registration = self.log(self.a, "institution_species", None)
        before = list(AuditLog.objects.order_by("pk").values())

        personal = self.ids("api_profile_actions", self.a, self.admin)
        self.assertNotIn(profile_noise.pk, personal)
        self.assertNotIn(code_noise.pk, personal)
        self.assertTrue({manual_code.pk, registration.pk} <= personal)

        # The authoritative administrative audit keeps every row, author intact.
        admin = self.ids("api_account_audit_log", self.a, self.admin)
        self.assertTrue({profile_noise.pk, code_noise.pk, manual_code.pk, registration.pk} <= admin)
        self.assertEqual(
            set(impactful_audit_logs(organization_id=self.a.pk).values_list("pk", flat=True)),
            {profile_noise.pk, code_noise.pk, manual_code.pk, registration.pk},
        )
        self.assertEqual(list(AuditLog.objects.order_by("pk").values()), before)

    def test_admin_audit_stays_institution_scoped(self):
        own = self.log(self.a, "institution_species", "institution_species_resolution")
        foreign = self.log(self.b, "institution_species", "institution_species_resolution", user=self.b_admin)
        admin = self.ids("api_account_audit_log", self.a, self.admin)
        self.assertIn(own.pk, admin)
        self.assertNotIn(foreign.pk, admin)
        self.assertNotIn(foreign.pk, self.ids("api_profile_actions", self.a, self.admin))
