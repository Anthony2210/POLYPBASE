import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.audit.models import AuditLog
from apps.organizations.models import Organization

from .api_views import OrganizationMembershipDetailAPIView
from .models import OrganizationMembership


@skipUnless(
    connection.vendor == "postgresql",
    "Membership concurrency requires PostgreSQL row locks.",
)
class OrganizationMembershipConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        user_model = get_user_model()
        self.manager = user_model.objects.create_superuser(
            username="membership-manager",
            email="membership-manager@example.test",
        )
        self.first_admin = user_model.objects.create_user(
            username="membership-admin-one",
            email="membership-admin-one@example.test",
        )
        self.second_admin = user_model.objects.create_user(
            username="membership-admin-two",
            email="membership-admin-two@example.test",
        )
        self.organization = Organization.objects.create(name="Membership concurrency QA")
        self.first_membership = OrganizationMembership.objects.create(
            user=self.first_admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.second_membership = OrganizationMembership.objects.create(
            user=self.second_admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )

    def _patch_membership(
        self,
        *,
        actor,
        target,
        payload,
        organization_lock_attempted=None,
        organization_lock_acquired=None,
    ):
        close_old_connections()
        try:
            request = APIRequestFactory().patch(
                "/qa/account-membership/",
                payload,
                format="json",
                HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
            )
            force_authenticate(request, user=actor)

            def perform_request():
                response = OrganizationMembershipDetailAPIView.as_view()(
                    request,
                    pk=target.pk,
                )
                response.render()
                return response

            if organization_lock_attempted is None:
                return perform_request()

            def observe_organization_lock(execute, sql, params, many, context):
                normalized_sql = sql.upper()
                if (
                    "FOR UPDATE" in normalized_sql
                    and "ORGANIZATIONS_ORGANIZATION" in normalized_sql
                ):
                    # This signal is pre-execution: the call may block below.
                    organization_lock_attempted.set()
                    result = execute(sql, params, many, context)
                    # Returning from SELECT FOR UPDATE proves the row lock was acquired.
                    organization_lock_acquired.set()
                    return result
                return execute(sql, params, many, context)

            with connection.execute_wrapper(observe_organization_lock):
                return perform_request()
        finally:
            connections.close_all()

    def _run_concurrent_mutations(
        self,
        *,
        first_validation_method,
        first_payload,
        second_payload=None,
        first_actor=None,
        second_actor=None,
    ):
        holder_organization_lock_acquired = threading.Event()
        release_lock_holder = threading.Event()
        waiter_organization_lock_attempted = threading.Event()
        waiter_organization_lock_acquired = threading.Event()
        second_payload = first_payload if second_payload is None else second_payload
        first_actor = first_actor or self.manager
        second_actor = second_actor or self.manager
        original_validation = getattr(
            OrganizationMembershipDetailAPIView,
            first_validation_method,
        )

        def hold_first_request_after_lock(view, *args, **kwargs):
            result = original_validation(view, *args, **kwargs)
            if kwargs["membership"].pk == self.second_membership.pk:
                # Validation is reached only after the organization lock, the
                # authorization refresh, and the target membership refresh.
                holder_organization_lock_acquired.set()
                if not release_lock_holder.wait(timeout=10):
                    raise TimeoutError(
                        "Timed out while holding the organization lock."
                    )
            return result

        with patch.object(
            OrganizationMembershipDetailAPIView,
            first_validation_method,
            hold_first_request_after_lock,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first_future = executor.submit(
                    self._patch_membership,
                    actor=first_actor,
                    target=self.second_membership,
                    payload=first_payload,
                )
                try:
                    if not holder_organization_lock_acquired.wait(timeout=10):
                        raise TimeoutError(
                            "First request did not acquire the organization lock."
                        )

                    second_future = executor.submit(
                        self._patch_membership,
                        actor=second_actor,
                        target=self.first_membership,
                        payload=second_payload,
                        organization_lock_attempted=(
                            waiter_organization_lock_attempted
                        ),
                        organization_lock_acquired=(
                            waiter_organization_lock_acquired
                        ),
                    )
                    if not waiter_organization_lock_attempted.wait(timeout=10):
                        raise TimeoutError(
                            "Second request did not attempt the organization lock."
                        )
                    self.assertFalse(
                        waiter_organization_lock_acquired.is_set(),
                        "Second request acquired the lock before the holder was released.",
                    )
                finally:
                    release_lock_holder.set()

                responses = [
                    first_future.result(timeout=15),
                    second_future.result(timeout=15),
                ]
                self.assertTrue(
                    waiter_organization_lock_acquired.is_set(),
                    "Second request did not acquire the lock after the holder committed.",
                )

        return responses

    def _active_admin_count(self):
        return OrganizationMembership.objects.filter(
            organization=self.organization,
            is_active=True,
            role=OrganizationMembership.Role.ADMIN,
        ).count()

    def _account_update_audit_count(self):
        return AuditLog.objects.filter(
            organization=self.organization,
            object_type="account",
            description="Member access updated",
        ).count()

    def _assert_one_admin_remains(self, responses):
        self.assertEqual(self._active_admin_count(), 1)
        self.assertCountEqual(
            [response.status_code for response in responses],
            [200, 403],
        )
        self.assertEqual(self._account_update_audit_count(), 1)

    def test_concurrent_admin_demotions_keep_one_active_admin(self):
        responses = self._run_concurrent_mutations(
            first_validation_method="_ensure_role_change_allowed",
            first_payload={"role": OrganizationMembership.Role.VIEWER},
        )

        self._assert_one_admin_remains(responses)

    def test_concurrent_admin_deactivations_keep_one_active_admin(self):
        responses = self._run_concurrent_mutations(
            first_validation_method="_ensure_activation_change_allowed",
            first_payload={"is_active": False},
        )

        self._assert_one_admin_remains(responses)

    def test_concurrent_demotion_and_deactivation_keep_one_active_admin(self):
        responses = self._run_concurrent_mutations(
            first_validation_method="_ensure_role_change_allowed",
            first_payload={"role": OrganizationMembership.Role.VIEWER},
            second_payload={"is_active": False},
        )

        self._assert_one_admin_remains(responses)

    def test_two_concurrent_demotions_succeed_when_three_admins_exist(self):
        third_admin = get_user_model().objects.create_user(
            username="membership-admin-three",
            email="membership-admin-three@example.test",
        )
        OrganizationMembership.objects.create(
            user=third_admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )

        responses = self._run_concurrent_mutations(
            first_validation_method="_ensure_role_change_allowed",
            first_payload={"role": OrganizationMembership.Role.VIEWER},
        )

        self.assertEqual(self._active_admin_count(), 1)
        self.assertEqual(
            [response.status_code for response in responses],
            [200, 200],
        )
        self.assertEqual(self._account_update_audit_count(), 2)

    def test_waiting_request_rechecks_actor_admin_permission(self):
        third_admin = get_user_model().objects.create_user(
            username="membership-admin-three",
            email="membership-admin-three@example.test",
        )
        OrganizationMembership.objects.create(
            user=third_admin,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
        )
        self.assertTrue(self.second_membership.is_active)
        self.assertEqual(
            self.second_membership.role,
            OrganizationMembership.Role.ADMIN,
        )

        responses = self._run_concurrent_mutations(
            first_validation_method="_ensure_role_change_allowed",
            first_payload={"role": OrganizationMembership.Role.VIEWER},
            first_actor=self.first_admin,
            second_actor=self.second_admin,
        )

        self.assertEqual(self._active_admin_count(), 2)
        self.assertEqual(
            [response.status_code for response in responses],
            [200, 403],
        )
        self.second_membership.refresh_from_db()
        self.first_membership.refresh_from_db()
        self.assertEqual(
            self.second_membership.role,
            OrganizationMembership.Role.VIEWER,
        )
        self.assertEqual(
            self.first_membership.role,
            OrganizationMembership.Role.ADMIN,
        )
        self.assertEqual(
            str(responses[1].data["detail"]),
            "This account cannot manage members for the selected organization.",
        )
        self.assertEqual(self._account_update_audit_count(), 1)
