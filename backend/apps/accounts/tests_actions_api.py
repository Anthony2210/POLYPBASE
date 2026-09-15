from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.audit.models import AuditLog
from apps.cultures.models import Box, ThermalZone
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .models import OrganizationMembership


class ActionApiTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(
            name="Aquarium de Paris",
            slug="paris",
        )
        self.other_organization = Organization.objects.create(
            name="Partner Laboratory",
            slug="partner",
        )
        self.inactive_organization = Organization.objects.create(
            name="Inactive Laboratory",
            slug="inactive",
            is_active=False,
        )

        self.admin = self._create_member(
            "admin",
            self.organization,
            OrganizationMembership.Role.ADMIN,
        )
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.other_organization,
            role=OrganizationMembership.Role.VIEWER,
        )
        self.alice = self._create_member(
            "alice",
            self.organization,
            OrganizationMembership.Role.LAB_TECHNICIAN,
        )
        OrganizationMembership.objects.create(
            user=self.alice,
            organization=self.other_organization,
            role=OrganizationMembership.Role.LAB_TECHNICIAN,
        )
        self.bob = self._create_member(
            "bob",
            self.organization,
            OrganizationMembership.Role.LAB_TECHNICIAN,
        )
        self.carol = self._create_member(
            "carol",
            self.organization,
            OrganizationMembership.Role.LAB_TECHNICIAN,
        )
        self.viewer = self._create_member(
            "viewer",
            self.organization,
            OrganizationMembership.Role.VIEWER,
        )
        self.inactive_member = self._create_member(
            "inactive_member",
            self.inactive_organization,
            OrganizationMembership.Role.VIEWER,
        )

        species = Species.objects.create(
            scientific_name="Aurelia aurita",
            genus_species_code="AAU",
        )
        strain = Strain.objects.create(
            species=species,
            code="1-ATL",
            number=1,
            origin_code="ATL",
        )
        zone = ThermalZone.objects.create(
            organization=self.organization,
            name="Cabinet-15",
            zone_type=ThermalZone.ZoneType.CABINET,
        )
        self.box = Box.objects.create(
            organization=self.organization,
            global_code="ATL-AAU-1.001",
            box_number="001",
            strain=strain,
            thermal_zone=zone,
        )

    def _create_member(self, username, organization, role):
        user = get_user_model().objects.create_user(
            username=username,
            email=f"{username}@example.org",
            password="secret",
        )
        OrganizationMembership.objects.create(
            user=user,
            organization=organization,
            role=role,
        )
        return user

    def _create_action(
        self,
        *,
        user,
        organization=None,
        description=None,
        action=AuditLog.Action.UPDATE,
        metadata=None,
    ):
        organization = organization or self.organization
        return AuditLog.objects.create(
            organization=organization,
            user=user,
            action=action,
            object_type="box",
            object_id=self.box.global_code,
            description=description or f"Action by {user.username}",
            metadata=metadata or {},
        )

    def _personal_actions(self, user, organization, query=""):
        self.client.logout()
        self.client.login(username=user.username, password="secret")
        return self.client.get(
            f"{reverse('api_profile_actions')}{query}",
            HTTP_X_ORGANIZATION_ID=str(organization.id),
        )

    def _admin_actions(self, organization, query=""):
        self.client.logout()
        self.client.login(username=self.admin.username, password="secret")
        return self.client.get(
            f"{reverse('api_account_audit_log')}{query}",
            HTTP_X_ORGANIZATION_ID=str(organization.id),
        )

    def test_personal_actions_returns_only_the_actor_in_the_active_organization(self):
        own_action = self._create_action(user=self.alice, description="Alice in Paris")
        self._create_action(user=self.bob, description="Bob in Paris")
        self._create_action(
            user=self.alice,
            organization=self.other_organization,
            description="Alice at partner",
        )

        response = self._personal_actions(self.alice, self.organization)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([entry["id"] for entry in payload["results"]], [own_action.id])
        self.assertEqual(payload["results"][0]["description"], "Alice in Paris")

    def test_switching_active_organization_changes_personal_actions(self):
        paris_action = self._create_action(user=self.alice, description="Paris action")
        partner_action = self._create_action(
            user=self.alice,
            organization=self.other_organization,
            description="Partner action",
        )

        paris_response = self._personal_actions(self.alice, self.organization)
        partner_response = self._personal_actions(self.alice, self.other_organization)

        self.assertEqual(
            [entry["id"] for entry in paris_response.json()["results"]],
            [paris_action.id],
        )
        self.assertEqual(
            [entry["id"] for entry in partner_response.json()["results"]],
            [partner_action.id],
        )

    def test_all_active_membership_roles_can_view_their_own_actions(self):
        for user in (self.viewer, self.alice, self.admin):
            with self.subTest(user=user.username):
                action = self._create_action(user=user)
                response = self._personal_actions(user, self.organization)
                self.assertEqual(response.status_code, 200)
                self.assertIn(
                    action.id,
                    [entry["id"] for entry in response.json()["results"]],
                )

    def test_personal_actions_requires_authentication(self):
        response = self.client.get(
            reverse("api_profile_actions"),
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )

        self.assertEqual(response.status_code, 403)

    def test_personal_actions_keeps_the_supported_business_action_allowlist(self):
        supported = self._create_action(user=self.alice)
        self._create_action(
            user=self.alice,
            action=AuditLog.Action.VIEW,
            description="Box opened",
        )

        response = self._personal_actions(self.alice, self.organization)

        self.assertEqual(
            [entry["id"] for entry in response.json()["results"]],
            [supported.id],
        )

    def test_personal_actions_requires_an_explicit_active_organization(self):
        self.client.login(username=self.alice.username, password="secret")

        response = self.client.get(reverse("api_profile_actions"))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "Organization context is required.")

    def test_personal_actions_rejects_inactive_or_unauthorized_context(self):
        inactive_response = self._personal_actions(
            self.inactive_member,
            self.inactive_organization,
        )
        unauthorized_response = self._personal_actions(
            self.viewer,
            self.other_organization,
        )

        membership = OrganizationMembership.objects.get(
            user=self.viewer,
            organization=self.organization,
        )
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        inactive_membership_response = self._personal_actions(
            self.viewer,
            self.organization,
        )

        self.assertEqual(inactive_response.status_code, 403)
        self.assertEqual(unauthorized_response.status_code, 403)
        self.assertEqual(inactive_membership_response.status_code, 403)

    def test_an_affected_account_is_not_treated_as_the_actor(self):
        action = AuditLog.objects.create(
            organization=self.organization,
            user=self.admin,
            action=AuditLog.Action.UPDATE,
            object_type="account",
            object_id=self.alice.get_username(),
            description="Member access updated",
            metadata={"user_id": self.alice.id},
        )

        alice_response = self._personal_actions(self.alice, self.organization)
        admin_response = self._personal_actions(self.admin, self.organization)

        self.assertEqual(alice_response.json()["results"], [])
        self.assertEqual(
            [entry["id"] for entry in admin_response.json()["results"]],
            [action.id],
        )

    def test_legacy_edits_do_not_create_personal_actions_for_the_editor(self):
        action = self._create_action(user=self.alice)
        recorded_at = action.created_at
        AuditLog.objects.filter(pk=action.pk).update(
            edited_at=timezone.now(),
            edited_by=self.bob,
            original_values={"legacy": True},
        )

        alice_response = self._personal_actions(self.alice, self.organization)
        bob_response = self._personal_actions(self.bob, self.organization)

        alice_entry = alice_response.json()["results"][0]
        self.assertEqual(alice_entry["id"], action.id)
        self.assertEqual(
            parse_datetime(alice_entry["created_at"]),
            recorded_at,
        )
        self.assertNotIn("edited_at", alice_entry)
        self.assertNotIn("edited_by", alice_entry)
        self.assertEqual(bob_response.json()["results"], [])

    def test_query_manipulation_cannot_select_another_actor_or_organization(self):
        own_action = self._create_action(user=self.alice)
        self._create_action(user=self.bob)
        self._create_action(user=self.bob, organization=self.other_organization)
        query = (
            f"?user={self.bob.id}&actor={self.bob.username}"
            f"&organization={self.other_organization.id}"
        )

        response = self._personal_actions(self.alice, self.organization, query)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [entry["id"] for entry in response.json()["results"]],
            [own_action.id],
        )

    def test_personal_response_allowlists_metadata_and_preserves_zero(self):
        action = self._create_action(
            user=self.alice,
            metadata={
                "measurement_id": 999,
                "membership_id": 888,
                "secret": "must not be exposed",
                "before": {"polypes": 4},
                "after": {"polypes": 0},
                "valeurs": {
                    "date": "2026-09-15",
                    "polypes": 0,
                    "ephyrules": 0,
                    "internal_id": 777,
                    "nested": {"unsafe": True},
                },
                "modifications": {
                    "polypes": {"avant": 4, "apres": 0},
                    "internal_id": {"avant": 1, "apres": 2},
                },
            },
        )

        response = self._personal_actions(self.alice, self.organization)

        self.assertEqual(response.status_code, 200)
        entry = response.json()["results"][0]
        self.assertEqual(
            set(entry),
            {
                "id",
                "created_at",
                "action",
                "action_label",
                "resource",
                "description",
                "details",
            },
        )
        self.assertEqual(entry["id"], action.id)
        self.assertNotIn("metadata", entry)
        self.assertNotIn("editable_measurement", entry)
        self.assertEqual(
            entry["details"],
            {
                "values": {
                    "date": "2026-09-15",
                    "polypes": 0,
                    "ephyrules": 0,
                },
                "changes": {"polypes": {"before": 4, "after": 0}},
            },
        )

    def test_measurement_events_belong_to_the_users_who_performed_them(self):
        measurement_date = date(2026, 9, 15)
        measurement_url = reverse("api_box_measurements", args=[self.box.id])

        self.client.login(username=self.alice.username, password="secret")
        creation_response = self.client.post(
            measurement_url,
            data={
                "measured_on": measurement_date.isoformat(),
                "polyp_count": 12,
                "ephyrae_count": 3,
            },
            content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )
        self.assertEqual(creation_response.status_code, 201)
        measurement_id = creation_response.json()["id"]
        detail_url = reverse(
            "api_box_measurement_detail",
            args=[self.box.id, measurement_id],
        )

        self.client.login(username=self.bob.username, password="secret")
        second_response = self.client.patch(
            detail_url,
            data={"polyp_count": 0, "ephyrae_count": 0},
            content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )
        self.assertEqual(second_response.status_code, 200)

        self.client.login(username=self.carol.username, password="secret")
        third_response = self.client.patch(
            detail_url,
            data={"polyp_count": 7},
            content_type="application/json",
            HTTP_X_ORGANIZATION_ID=str(self.organization.id),
        )
        self.assertEqual(third_response.status_code, 200)

        events = list(
            AuditLog.objects.filter(metadata__measurement_id=measurement_id).order_by(
                "created_at",
                "id",
            )
        )
        self.assertEqual(
            [event.user_id for event in events],
            [self.alice.id, self.bob.id, self.carol.id],
        )

        for user, expected_event in zip(
            (self.alice, self.bob, self.carol),
            events,
            strict=True,
        ):
            with self.subTest(user=user.username):
                response = self._personal_actions(user, self.organization)
                measurement_entries = [
                    entry
                    for entry in response.json()["results"]
                    if entry["resource"]["identifier"] == self.box.global_code
                ]
                self.assertEqual(
                    [entry["id"] for entry in measurement_entries],
                    [expected_event.id],
                )

        bob_response = self._personal_actions(self.bob, self.organization)
        bob_entry = bob_response.json()["results"][0]
        self.assertEqual(bob_entry["details"]["values"]["polypes"], 0)
        self.assertEqual(bob_entry["details"]["values"]["ephyrules"], 0)

        admin_response = self._admin_actions(self.organization)
        serialized_events = [
            entry
            for entry in admin_response.json()["results"]
            if entry["metadata"].get("measurement_id") == measurement_id
        ]
        self.assertEqual(len(serialized_events), 3)
        self.assertEqual(
            {entry["user"] for entry in serialized_events},
            {"alice", "bob", "carol"},
        )
        self.assertEqual(
            next(entry for entry in serialized_events if entry["user"] == "bob")[
                "metadata"
            ]["valeurs"]["polypes"],
            0,
        )

    def test_personal_pagination_is_stable_for_equal_timestamps(self):
        actions = [self._create_action(user=self.alice) for _index in range(5)]
        fixed_time = timezone.now()
        AuditLog.objects.filter(id__in=[action.id for action in actions]).update(
            created_at=fixed_time
        )
        expected_ids = sorted((action.id for action in actions), reverse=True)

        first_response = self._personal_actions(
            self.alice,
            self.organization,
            "?limit=2&offset=0",
        )
        second_response = self._personal_actions(
            self.alice,
            self.organization,
            "?limit=2&offset=2",
        )

        first_payload = first_response.json()
        second_payload = second_response.json()
        first_ids = [entry["id"] for entry in first_payload["results"]]
        second_ids = [entry["id"] for entry in second_payload["results"]]
        self.assertEqual(first_ids, expected_ids[:2])
        self.assertEqual(second_ids, expected_ids[2:4])
        self.assertFalse(set(first_ids) & set(second_ids))
        self.assertTrue(first_payload["has_more"])
        self.assertEqual(first_payload["next_offset"], 2)

    def test_administration_keeps_active_institution_scope_across_actors(self):
        alice_action = self._create_action(user=self.alice, description="Alice action")
        bob_action = self._create_action(user=self.bob, description="Bob action")
        self._create_action(
            user=self.alice,
            organization=self.other_organization,
            description="Foreign action",
        )

        response = self._admin_actions(self.organization)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {entry["id"] for entry in response.json()["results"]},
            {alice_action.id, bob_action.id},
        )

    def test_administration_rejects_an_active_institution_not_administered(self):
        response = self._admin_actions(self.other_organization)

        self.assertEqual(response.status_code, 403)

    def test_administration_pagination_is_stable_for_equal_timestamps(self):
        actions = [self._create_action(user=self.alice) for _index in range(5)]
        fixed_time = timezone.now()
        AuditLog.objects.filter(id__in=[action.id for action in actions]).update(
            created_at=fixed_time
        )
        expected_ids = sorted((action.id for action in actions), reverse=True)

        first_response = self._admin_actions(
            self.organization,
            "?limit=2&offset=0",
        )
        second_response = self._admin_actions(
            self.organization,
            "?limit=2&offset=2",
        )

        first_ids = [entry["id"] for entry in first_response.json()["results"]]
        second_ids = [entry["id"] for entry in second_response.json()["results"]]
        self.assertEqual(first_ids, expected_ids[:2])
        self.assertEqual(second_ids, expected_ids[2:4])
        self.assertFalse(set(first_ids) & set(second_ids))
