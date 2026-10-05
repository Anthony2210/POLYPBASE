"""Regression tests for scoped, batched current audit target identity."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import OrganizationMembership
from apps.organizations.models import Organization

from .models import AuditLog
from .services import resolve_audit_accounts


class AuditAccountResolutionTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Audit Identity Lab")
        self.other = Organization.objects.create(name="Other Audit Identity Lab")
        self.user = get_user_model().objects.create_user(
            username="legacy-tech", email="tech@example.org",
        )
        OrganizationMembership.objects.create(
            user=self.user, organization=self.organization, role="viewer", is_active=False,
        )

    def _log(self, organization, **kwargs):
        return AuditLog.objects.create(
            organization=organization, action=AuditLog.Action.UPDATE,
            object_type="account", object_id=self.user.username, **kwargs,
        )

    def test_batch_resolution_retains_inactive_members_and_ignores_foreign_logs(self):
        current = self._log(self.organization, metadata={"user_id": self.user.id})
        legacy = self._log(self.organization)
        foreign = self._log(self.other, metadata={"user_id": self.user.id})
        with self.assertNumQueries(1):
            accounts = resolve_audit_accounts(
                [current, legacy, foreign], organization_id=self.organization.id,
            )
        self.assertEqual(accounts, {current.id: self.user, legacy.id: None})
        with self.assertNumQueries(0):
            self.assertEqual(resolve_audit_accounts(
                [foreign], organization_id=self.organization.id,
            ), {})

    def test_numeric_object_id_is_not_assumed_to_be_a_user_primary_key(self):
        log = self._log(self.organization)
        log.object_id = str(self.user.id)
        log.save(update_fields=["object_id"])
        self.assertEqual(resolve_audit_accounts(
            [log], organization_id=self.organization.id,
        ), {log.id: None})

    def test_missing_explicit_id_never_matches_reused_legacy_username(self):
        log = self._log(self.organization, metadata={"user_id": self.user.id + 1000})
        self.assertEqual(resolve_audit_accounts(
            [log], organization_id=self.organization.id,
        ), {log.id: None})
