from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.organizations.models import Organization

from .models import OrganizationMembership
from .permissions import (
    user_can_manage_admin_memberships,
    user_is_active_institution_responsable,
)


class OrganizationMembershipResponsableModelTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Model institution")
        self.user = get_user_model().objects.create_user(
            username="model-member",
            email="model-member@example.test",
        )

    def test_responsable_defaults_false_without_auto_designation(self):
        membership = OrganizationMembership.objects.create(
            user=self.user,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )

        self.assertFalse(membership.is_responsable)
        self.assertFalse(
            OrganizationMembership.objects.filter(is_responsable=True).exists()
        )

    def test_model_validation_rejects_lower_roles_as_responsable(self):
        for role in (
            OrganizationMembership.Role.VIEWER,
            OrganizationMembership.Role.LAB_TECHNICIAN,
        ):
            with self.subTest(role=role):
                membership = OrganizationMembership(
                    user=self.user,
                    organization=self.organization,
                    role=role,
                    is_responsable=True,
                )
                with self.assertRaises(DjangoValidationError) as error:
                    membership.full_clean()
                self.assertIn("is_responsable", error.exception.message_dict)

    def test_database_constraint_rejects_lower_roles_as_responsable(self):
        for index, role in enumerate(
            (
                OrganizationMembership.Role.VIEWER,
                OrganizationMembership.Role.LAB_TECHNICIAN,
            )
        ):
            with self.subTest(role=role), transaction.atomic():
                user = get_user_model().objects.create_user(
                    username=f"invalid-responsable-{index}",
                    email=f"invalid-responsable-{index}@example.test",
                )
                with self.assertRaises(IntegrityError):
                    OrganizationMembership.objects.create(
                        user=user,
                        organization=self.organization,
                        role=role,
                        is_responsable=True,
                    )

    def test_superuser_is_break_glass_not_responsable(self):
        superuser = get_user_model().objects.create_superuser(
            username="platform-model",
            email="platform-model@example.test",
        )

        self.assertFalse(
            user_is_active_institution_responsable(superuser, self.organization)
        )
        self.assertTrue(
            user_can_manage_admin_memberships(superuser, self.organization)
        )

    def test_django_admin_surfaces_but_cannot_edit_responsable_status(self):
        membership_admin = admin.site._registry[OrganizationMembership]

        self.assertIn("is_responsable", membership_admin.list_display)
        self.assertIn("is_responsable", membership_admin.list_filter)
        self.assertIn("is_responsable", membership_admin.readonly_fields)
        self.assertFalse(membership_admin.has_add_permission(None))
        self.assertFalse(membership_admin.has_change_permission(None))
        self.assertFalse(membership_admin.has_delete_permission(None))


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_DELIVERY_ENABLED=True,
    DEFAULT_FROM_EMAIL="Polypbase <noreply@polypbase.org>",
)
class InstitutionResponsableApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.organization = Organization.objects.create(name="Responsable institution")
        self.other_organization = Organization.objects.create(name="Other institution")
        self.regular_admin = user_model.objects.create_user(
            username="regular-admin",
            email="regular-admin@example.test",
            password="secret",
        )
        self.responsable = user_model.objects.create_user(
            username="responsable",
            email="responsable@example.test",
            password="secret",
        )
        self.second_responsable = user_model.objects.create_user(
            username="second-responsable",
            email="second-responsable@example.test",
            password="secret",
        )
        self.ordinary_admin = user_model.objects.create_user(
            username="ordinary-admin",
            email="ordinary-admin@example.test",
            password="secret",
        )
        self.viewer = user_model.objects.create_user(
            username="responsable-viewer",
            email="responsable-viewer@example.test",
            password="secret",
        )
        self.regular_admin_membership = self._membership(
            self.regular_admin,
            OrganizationMembership.Role.ADMIN,
        )
        self.responsable_membership = self._membership(
            self.responsable,
            OrganizationMembership.Role.ADMIN,
            is_responsable=True,
        )
        self.second_responsable_membership = self._membership(
            self.second_responsable,
            OrganizationMembership.Role.ADMIN,
            is_responsable=True,
        )
        self.ordinary_admin_membership = self._membership(
            self.ordinary_admin,
            OrganizationMembership.Role.ADMIN,
        )
        self.viewer_membership = self._membership(
            self.viewer,
            OrganizationMembership.Role.VIEWER,
        )
        self.list_url = reverse("api_account_members")
        self.relinquish_url = reverse("api_institution_responsable_relinquish")

    def _membership(self, user, role, *, is_responsable=False, organization=None):
        return OrganizationMembership.objects.create(
            user=user,
            organization=organization or self.organization,
            role=role,
            is_responsable=is_responsable,
        )

    def _organization_header(self, organization=None):
        return {
            "HTTP_X_ORGANIZATION_ID": str(
                (organization or self.organization).id
            )
        }

    def _patch(self, actor, membership, payload):
        self.client.force_login(actor)
        return self.client.patch(
            reverse("api_account_member_detail", args=[membership.id]),
            data=payload,
            content_type="application/json",
            **self._organization_header(),
        )

    def _role_values(self, response):
        return [choice["value"] for choice in response.json()["roles"]]

    def test_profile_and_member_payloads_include_responsable_status(self):
        self.client.force_login(self.responsable)

        profile = self.client.get(
            reverse("api_profile"),
            **self._organization_header(),
        )
        members = self.client.get(self.list_url, **self._organization_header())

        self.assertEqual(profile.status_code, 200)
        self.assertTrue(profile.json()["memberships"][0]["is_responsable"])
        self.assertEqual(members.status_code, 200)
        by_id = {
            member["membership_id"]: member
            for member in members.json()["members"]
        }
        self.assertTrue(
            by_id[self.responsable_membership.id]["is_responsable"]
        )
        self.assertFalse(by_id[self.viewer_membership.id]["is_responsable"])

    def test_member_list_capabilities_are_actor_specific(self):
        self.client.force_login(self.regular_admin)
        regular_response = self.client.get(
            self.list_url,
            **self._organization_header(),
        )

        self.assertEqual(regular_response.status_code, 200)
        self.assertEqual(
            self._role_values(regular_response),
            [
                OrganizationMembership.Role.LAB_TECHNICIAN,
                OrganizationMembership.Role.VIEWER,
            ],
        )
        self.assertFalse(
            regular_response.json()["can_manage_admin_memberships"]
        )
        self.assertFalse(
            regular_response.json()["can_relinquish_responsable"]
        )

        self.client.force_login(self.responsable)
        responsable_response = self.client.get(
            self.list_url,
            **self._organization_header(),
        )
        self.assertEqual(
            self._role_values(responsable_response),
            [value for value, _label in OrganizationMembership.Role.choices],
        )
        self.assertTrue(
            responsable_response.json()["can_manage_admin_memberships"]
        )
        self.assertTrue(
            responsable_response.json()["can_relinquish_responsable"]
        )

    def test_superuser_capabilities_are_separate_from_responsable(self):
        superuser = get_user_model().objects.create_superuser(
            username="platform-api",
            email="platform-api@example.test",
            password="secret",
        )
        self.client.force_login(superuser)

        response = self.client.get(self.list_url, **self._organization_header())

        self.assertEqual(response.status_code, 200)
        self.assertIn(OrganizationMembership.Role.ADMIN, self._role_values(response))
        self.assertTrue(response.json()["can_manage_admin_memberships"])
        self.assertFalse(response.json()["can_relinquish_responsable"])

    def test_ordinary_admin_cannot_invite_admin_or_responsable(self):
        self.client.force_login(self.regular_admin)
        base_payload = {
            "organization_id": self.organization.id,
            "role": OrganizationMembership.Role.ADMIN,
        }

        admin_response = self.client.post(
            self.list_url,
            data={**base_payload, "email": "forbidden-admin@example.test"},
            content_type="application/json",
            **self._organization_header(),
        )
        responsable_response = self.client.post(
            self.list_url,
            data={
                **base_payload,
                "email": "forbidden-responsable@example.test",
                "role": OrganizationMembership.Role.VIEWER,
                "is_responsable": True,
            },
            content_type="application/json",
            **self._organization_header(),
        )

        self.assertEqual(admin_response.status_code, 403)
        self.assertEqual(responsable_response.status_code, 400)
        self.assertFalse(
            get_user_model().objects.filter(
                email__in=[
                    "forbidden-admin@example.test",
                    "forbidden-responsable@example.test",
                ]
            ).exists()
        )

    def test_responsable_can_invite_admin_but_not_responsable(self):
        self.client.force_login(self.responsable)

        response = self.client.post(
            self.list_url,
            data={
                "organization_id": self.organization.id,
                "role": OrganizationMembership.Role.ADMIN,
                "email": "invited-admin@example.test",
            },
            content_type="application/json",
            **self._organization_header(),
        )

        self.assertEqual(response.status_code, 201)
        membership = OrganizationMembership.objects.get(
            user__email="invited-admin@example.test"
        )
        self.assertEqual(membership.role, OrganizationMembership.Role.ADMIN)
        self.assertFalse(membership.is_responsable)
        self.assertFalse(response.json()["is_responsable"])

    @patch("apps.accounts.api_views.user_can_administer_organization")
    def test_lower_role_invitation_rechecks_admin_authority_under_lock(
        self,
        can_administer_organization,
    ):
        # The pre-lock authorization runs through the permissions module and still
        # succeeds; only the post-lock revalidation sees the lost authority.
        can_administer_organization.return_value = False
        self.client.force_login(self.regular_admin)

        response = self.client.post(
            self.list_url,
            data={
                "organization_id": self.organization.id,
                "role": OrganizationMembership.Role.VIEWER,
                "email": "stale-admin@example.test",
            },
            content_type="application/json",
            **self._organization_header(),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "membership_admin_required")
        self.assertEqual(can_administer_organization.call_count, 1)
        self.assertFalse(
            get_user_model().objects.filter(email="stale-admin@example.test").exists()
        )
        self.assertFalse(AuditLog.objects.filter(description="Member access created").exists())

    @patch("apps.accounts.api_views.user_can_manage_admin_memberships")
    def test_admin_invitation_rechecks_responsable_authority_under_lock(
        self,
        can_manage_admin_memberships,
    ):
        can_manage_admin_memberships.side_effect = [True, False]
        self.client.force_login(self.responsable)

        response = self.client.post(
            self.list_url,
            data={
                "organization_id": self.organization.id,
                "role": OrganizationMembership.Role.ADMIN,
                "email": "stale-responsable@example.test",
            },
            content_type="application/json",
            **self._organization_header(),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "responsable_required")
        self.assertEqual(can_manage_admin_memberships.call_count, 2)
        self.assertFalse(
            get_user_model().objects.filter(
                email="stale-responsable@example.test"
            ).exists()
        )
        self.assertFalse(AuditLog.objects.filter(description="Member access created").exists())

    def test_superuser_can_invite_an_ordinary_admin(self):
        superuser = get_user_model().objects.create_superuser(
            username="platform-inviter",
            email="platform-inviter@example.test",
            password="secret",
        )
        self.client.force_login(superuser)

        response = self.client.post(
            self.list_url,
            data={
                "organization_id": self.organization.id,
                "role": OrganizationMembership.Role.ADMIN,
                "email": "platform-invited-admin@example.test",
            },
            content_type="application/json",
            **self._organization_header(),
        )

        self.assertEqual(response.status_code, 201)
        membership = OrganizationMembership.objects.get(
            user__email="platform-invited-admin@example.test"
        )
        self.assertEqual(membership.role, OrganizationMembership.Role.ADMIN)
        self.assertFalse(membership.is_responsable)

    def test_ordinary_admin_cannot_promote_or_modify_other_admins(self):
        promote = self._patch(
            self.regular_admin,
            self.viewer_membership,
            {"role": OrganizationMembership.Role.ADMIN},
        )
        change_role = self._patch(
            self.regular_admin,
            self.ordinary_admin_membership,
            {"role": OrganizationMembership.Role.VIEWER},
        )
        deactivate = self._patch(
            self.regular_admin,
            self.ordinary_admin_membership,
            {"is_active": False},
        )
        self.ordinary_admin_membership.is_active = False
        self.ordinary_admin_membership.save(update_fields=["is_active"])
        reactivate = self._patch(
            self.regular_admin,
            self.ordinary_admin_membership,
            {"is_active": True},
        )
        combined = self._patch(
            self.regular_admin,
            self.viewer_membership,
            {"role": OrganizationMembership.Role.ADMIN, "is_active": False},
        )

        self.assertEqual(
            [
                promote.status_code,
                change_role.status_code,
                deactivate.status_code,
                reactivate.status_code,
                combined.status_code,
            ],
            [403, 403, 403, 403, 403],
        )
        self.viewer_membership.refresh_from_db()
        self.ordinary_admin_membership.refresh_from_db()
        self.assertEqual(
            self.viewer_membership.role,
            OrganizationMembership.Role.VIEWER,
        )
        self.assertEqual(
            self.ordinary_admin_membership.role,
            OrganizationMembership.Role.ADMIN,
        )
        self.assertFalse(self.ordinary_admin_membership.is_active)

    def test_responsable_can_manage_ordinary_admin_memberships(self):
        promote = self._patch(
            self.responsable,
            self.viewer_membership,
            {"role": OrganizationMembership.Role.ADMIN},
        )
        demote = self._patch(
            self.responsable,
            self.viewer_membership,
            {"role": OrganizationMembership.Role.LAB_TECHNICIAN},
        )
        deactivate = self._patch(
            self.responsable,
            self.ordinary_admin_membership,
            {"is_active": False},
        )
        reactivate = self._patch(
            self.responsable,
            self.ordinary_admin_membership,
            {"is_active": True},
        )

        self.assertEqual(
            [promote.status_code, demote.status_code, deactivate.status_code, reactivate.status_code],
            [200, 200, 200, 200],
        )
        self.viewer_membership.refresh_from_db()
        self.ordinary_admin_membership.refresh_from_db()
        self.assertEqual(
            self.viewer_membership.role,
            OrganizationMembership.Role.LAB_TECHNICIAN,
        )
        self.assertTrue(self.ordinary_admin_membership.is_active)

    def test_admin_promotion_and_demotion_audit_actor_and_states(self):
        promotion = self._patch(
            self.responsable,
            self.viewer_membership,
            {"role": OrganizationMembership.Role.ADMIN},
        )
        demotion = self._patch(
            self.responsable,
            self.viewer_membership,
            {"role": OrganizationMembership.Role.VIEWER},
        )

        self.assertEqual([promotion.status_code, demotion.status_code], [200, 200])
        audits = list(
            AuditLog.objects.filter(
                organization=self.organization,
                user=self.responsable,
                object_id=self.viewer.username,
                description="Member access updated",
            ).order_by("created_at")
        )
        self.assertEqual(len(audits), 2)
        self.assertEqual(
            audits[0].metadata["modifications"]["role"],
            {
                "avant": OrganizationMembership.Role.VIEWER,
                "apres": OrganizationMembership.Role.ADMIN,
            },
        )
        self.assertEqual(
            audits[1].metadata["modifications"]["role"],
            {
                "avant": OrganizationMembership.Role.ADMIN,
                "apres": OrganizationMembership.Role.VIEWER,
            },
        )
        self.assertEqual(audits[0].metadata["membership_id"], self.viewer_membership.id)
        self.assertEqual(audits[0].metadata["user_id"], self.viewer.id)

    @patch("apps.accounts.api_views.AuditLog.objects.create")
    def test_admin_promotion_rolls_back_when_audit_fails(self, create_audit):
        create_audit.side_effect = RuntimeError("Audit unavailable")

        with self.assertRaises(RuntimeError):
            self._patch(
                self.responsable,
                self.viewer_membership,
                {"role": OrganizationMembership.Role.ADMIN},
            )

        self.viewer_membership.refresh_from_db()
        self.assertEqual(
            self.viewer_membership.role,
            OrganizationMembership.Role.VIEWER,
        )

    def test_generic_patch_never_mutates_a_responsable(self):
        superuser = get_user_model().objects.create_superuser(
            username="platform-patch",
            email="platform-patch@example.test",
            password="secret",
        )
        payloads = (
            {"role": OrganizationMembership.Role.VIEWER},
            {"is_active": False},
            {"role": OrganizationMembership.Role.VIEWER, "is_active": False},
        )
        for actor in (self.regular_admin, self.responsable, superuser):
            for payload in payloads:
                with self.subTest(actor=actor.username, payload=payload):
                    response = self._patch(
                        actor,
                        self.second_responsable_membership,
                        payload,
                    )
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(
                        response.json()["code"],
                        "responsable_membership_protected",
                    )

        rejected_responsable_field = self._patch(
            self.responsable,
            self.second_responsable_membership,
            {"is_responsable": False},
        )
        rejected_status_field = self._patch(
            self.responsable,
            self.second_responsable_membership,
            {"status": "inactive"},
        )
        self.assertEqual(rejected_responsable_field.status_code, 400)
        self.assertEqual(rejected_status_field.status_code, 400)
        self.second_responsable_membership.refresh_from_db()
        self.assertTrue(self.second_responsable_membership.is_responsable)
        self.assertTrue(self.second_responsable_membership.is_active)
        self.assertEqual(
            self.second_responsable_membership.role,
            OrganizationMembership.Role.ADMIN,
        )

    def test_superuser_can_manage_ordinary_admin_but_not_responsable(self):
        superuser = get_user_model().objects.create_superuser(
            username="platform-manager",
            email="platform-manager@example.test",
            password="secret",
        )

        ordinary_response = self._patch(
            superuser,
            self.ordinary_admin_membership,
            {"role": OrganizationMembership.Role.VIEWER},
        )
        responsable_response = self._patch(
            superuser,
            self.responsable_membership,
            {"is_active": False},
        )

        self.assertEqual(ordinary_response.status_code, 200)
        self.assertEqual(responsable_response.status_code, 403)

    def test_self_relinquish_retains_admin_access_and_audits_change(self):
        self.client.force_login(self.responsable)

        response = self.client.post(
            self.relinquish_url,
            data={"membership_id": self.second_responsable_membership.id},
            content_type="application/json",
            **self._organization_header(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()),
            {
                "member",
                "roles",
                "can_manage_admin_memberships",
                "can_relinquish_responsable",
            },
        )
        self.responsable_membership.refresh_from_db()
        self.second_responsable_membership.refresh_from_db()
        self.assertFalse(self.responsable_membership.is_responsable)
        self.assertEqual(
            self.responsable_membership.role,
            OrganizationMembership.Role.ADMIN,
        )
        self.assertTrue(self.responsable_membership.is_active)
        self.assertTrue(self.second_responsable_membership.is_responsable)
        self.assertFalse(response.json()["member"]["is_responsable"])
        self.assertFalse(response.json()["can_manage_admin_memberships"])
        self.assertFalse(response.json()["can_relinquish_responsable"])
        self.assertEqual(
            self._role_values(response),
            [
                OrganizationMembership.Role.LAB_TECHNICIAN,
                OrganizationMembership.Role.VIEWER,
            ],
        )

        audit = AuditLog.objects.get(
            organization=self.organization,
            user=self.responsable,
            description="Institution Responsable relinquished",
        )
        self.assertEqual(audit.metadata["user_id"], self.responsable.id)
        self.assertEqual(
            audit.metadata["membership_id"],
            self.responsable_membership.id,
        )
        self.assertEqual(
            audit.metadata["modifications"]["is_responsable"],
            {"avant": True, "apres": False},
        )
        self.assertFalse(audit.metadata["valeurs"]["is_responsable"])

    def test_relinquish_counts_membership_state_not_dates_or_user_activity(self):
        self.second_responsable.is_active = False
        self.second_responsable.save(update_fields=["is_active"])
        self.second_responsable_membership.starts_on = timezone.localdate() + timedelta(days=10)
        self.second_responsable_membership.ends_on = date(2000, 1, 1)
        self.second_responsable_membership.save(update_fields=["starts_on", "ends_on"])
        self.client.force_login(self.responsable)

        response = self.client.post(
            self.relinquish_url,
            **self._organization_header(),
        )

        self.assertEqual(response.status_code, 200)
        self.responsable_membership.refresh_from_db()
        self.assertFalse(self.responsable_membership.is_responsable)

    def test_last_active_responsable_cannot_relinquish(self):
        self.second_responsable_membership.is_responsable = False
        self.second_responsable_membership.save(update_fields=["is_responsable"])
        self.client.force_login(self.responsable)

        response = self.client.post(
            self.relinquish_url,
            **self._organization_header(),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "last_active_responsable")
        self.responsable_membership.refresh_from_db()
        self.assertTrue(self.responsable_membership.is_responsable)
        self.assertFalse(
            AuditLog.objects.filter(
                description="Institution Responsable relinquished"
            ).exists()
        )

    def test_responsable_in_another_organization_does_not_allow_relinquish(self):
        self.second_responsable_membership.is_responsable = False
        self.second_responsable_membership.save(update_fields=["is_responsable"])
        other_user = get_user_model().objects.create_user(
            username="other-responsable",
            email="other-responsable@example.test",
        )
        self._membership(
            other_user,
            OrganizationMembership.Role.ADMIN,
            is_responsable=True,
            organization=self.other_organization,
        )
        self.client.force_login(self.responsable)

        response = self.client.post(
            self.relinquish_url,
            **self._organization_header(),
        )

        self.assertEqual(response.status_code, 403)
        self.responsable_membership.refresh_from_db()
        self.assertTrue(self.responsable_membership.is_responsable)

    def test_wrong_actor_cannot_relinquish(self):
        self.client.force_login(self.regular_admin)

        response = self.client.post(
            self.relinquish_url,
            **self._organization_header(),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "active_responsable_required")
        self.responsable_membership.refresh_from_db()
        self.assertTrue(self.responsable_membership.is_responsable)

    @patch("apps.accounts.api_views.AuditLog.objects.create")
    def test_relinquish_rolls_back_when_audit_fails(self, create_audit):
        create_audit.side_effect = RuntimeError("Audit unavailable")
        self.client.force_login(self.responsable)

        with self.assertRaises(RuntimeError):
            self.client.post(
                self.relinquish_url,
                **self._organization_header(),
            )

        self.responsable_membership.refresh_from_db()
        self.assertTrue(self.responsable_membership.is_responsable)


class SetInstitutionResponsableCommandTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.organization = Organization.objects.create(name="Command institution")
        self.other_organization = Organization.objects.create(name="Command other")
        self.actor = user_model.objects.create_superuser(
            username="platform-command",
            email="platform-command@example.test",
        )
        self.non_superuser = user_model.objects.create_user(
            username="non-platform-command",
            email="non-platform-command@example.test",
        )
        self.target_user = user_model.objects.create_user(
            username="command-target",
            email="command-target@example.test",
        )
        self.membership = OrganizationMembership.objects.create(
            user=self.target_user,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )

    def _command(self, *, grant=False, revoke=False, apply=False, **overrides):
        organization_id = overrides.get("organization_id", self.organization.id)
        membership_id = overrides.get("membership_id", self.membership.id)
        actor_user_id = overrides.get("actor_user_id", self.actor.id)
        args = [
            "--organization-id",
            str(organization_id),
            "--membership-id",
            str(membership_id),
            "--actor-user-id",
            str(actor_user_id),
            "--grant" if grant else "--revoke",
        ]
        if apply:
            args.append("--apply")
        stdout = StringIO()
        call_command("set_institution_responsable", *args, stdout=stdout)
        return stdout.getvalue()

    def test_dry_run_is_default_and_does_not_mutate_or_audit(self):
        output = self._command(grant=True)

        self.membership.refresh_from_db()
        self.assertFalse(self.membership.is_responsable)
        self.assertIn("DRY RUN", output)
        self.assertFalse(AuditLog.objects.exists())

    def test_apply_grants_and_audits_exact_membership(self):
        self._command(grant=True, apply=True)

        self.membership.refresh_from_db()
        self.assertTrue(self.membership.is_responsable)
        audit = AuditLog.objects.get(
            organization=self.organization,
            user=self.actor,
            description="Institution Responsable granted by platform",
        )
        self.assertEqual(audit.metadata["user_id"], self.target_user.id)
        self.assertEqual(audit.metadata["membership_id"], self.membership.id)
        self.assertEqual(
            audit.metadata["modifications"]["is_responsable"],
            {"avant": False, "apres": True},
        )

    def test_apply_revoke_can_clear_inactive_membership(self):
        self.membership.is_responsable = True
        self.membership.is_active = False
        self.membership.save(update_fields=["is_responsable", "is_active"])
        self.organization.is_active = False
        self.organization.save(update_fields=["is_active"])

        self._command(revoke=True, apply=True)

        self.membership.refresh_from_db()
        self.assertFalse(self.membership.is_responsable)
        self.assertFalse(self.membership.is_active)
        self.assertEqual(
            AuditLog.objects.get().description,
            "Institution Responsable revoked by platform",
        )

    def test_apply_no_op_does_not_write_audit(self):
        output = self._command(revoke=True, apply=True)

        self.assertIn("No change required", output)
        self.assertFalse(AuditLog.objects.exists())

    def test_dry_run_and_apply_require_active_superuser_actor(self):
        inactive_superuser = get_user_model().objects.create_superuser(
            username="inactive-platform-command",
            email="inactive-platform-command@example.test",
        )
        inactive_superuser.is_active = False
        inactive_superuser.save(update_fields=["is_active"])

        for actor in (self.non_superuser, inactive_superuser):
            for apply in (False, True):
                with (
                    self.subTest(actor=actor.username, apply=apply),
                    self.assertRaises(CommandError),
                ):
                    self._command(
                        grant=True,
                        apply=apply,
                        actor_user_id=actor.id,
                    )

        self.membership.refresh_from_db()
        self.assertFalse(self.membership.is_responsable)

    def test_grant_rejects_inactive_or_lower_membership(self):
        for role, is_active in (
            (OrganizationMembership.Role.ADMIN, False),
            (OrganizationMembership.Role.VIEWER, True),
            (OrganizationMembership.Role.LAB_TECHNICIAN, True),
        ):
            with self.subTest(role=role, is_active=is_active):
                self.membership.role = role
                self.membership.is_active = is_active
                self.membership.save(update_fields=["role", "is_active"])
                with self.assertRaises(CommandError):
                    self._command(grant=True, apply=True)
                self.membership.refresh_from_db()
                self.assertFalse(self.membership.is_responsable)

    def test_exact_membership_must_belong_to_exact_organization(self):
        with self.assertRaises(CommandError):
            self._command(
                grant=True,
                apply=True,
                organization_id=self.other_organization.id,
            )

        self.membership.refresh_from_db()
        self.assertFalse(self.membership.is_responsable)

    def test_grant_rejects_superuser_target(self):
        super_membership = OrganizationMembership.objects.create(
            user=self.actor,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )

        with self.assertRaises(CommandError):
            self._command(
                grant=True,
                apply=True,
                membership_id=super_membership.id,
            )

        super_membership.refresh_from_db()
        self.assertFalse(super_membership.is_responsable)

    @patch(
        "apps.accounts.management.commands.set_institution_responsable."
        "AuditLog.objects.create"
    )
    def test_audit_failure_rolls_back_status_change(self, create_audit):
        create_audit.side_effect = RuntimeError("Audit unavailable")

        with self.assertRaises(RuntimeError):
            self._command(grant=True, apply=True)

        self.membership.refresh_from_db()
        self.assertFalse(self.membership.is_responsable)
        self.assertFalse(AuditLog.objects.exists())
