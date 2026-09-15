import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.audit.models import AuditLog
from apps.organizations.models import Organization

from .api_views import (
    InstitutionResponsableRelinquishAPIView,
    OrganizationMemberListCreateAPIView,
    OrganizationMembershipDetailAPIView,
)
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

    def _invite_member(
        self,
        *,
        actor,
        payload,
        organization_lock_attempted=None,
        organization_lock_acquired=None,
    ):
        close_old_connections()
        try:
            request = APIRequestFactory().post(
                "/qa/account-members/",
                payload,
                format="json",
                HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
            )
            force_authenticate(request, user=actor)

            def perform_request():
                response = OrganizationMemberListCreateAPIView.as_view()(request)
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
                    organization_lock_attempted.set()
                    result = execute(sql, params, many, context)
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
        second_target=None,
    ):
        holder_organization_lock_acquired = threading.Event()
        release_lock_holder = threading.Event()
        waiter_organization_lock_attempted = threading.Event()
        waiter_organization_lock_acquired = threading.Event()
        second_payload = first_payload if second_payload is None else second_payload
        first_actor = first_actor or self.manager
        second_actor = second_actor or self.manager
        second_target = second_target or self.first_membership
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
                        target=second_target,
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
            first_validation_method="_validate_final_state",
            first_payload={"role": OrganizationMembership.Role.VIEWER},
        )

        self._assert_one_admin_remains(responses)

    def test_concurrent_admin_deactivations_keep_one_active_admin(self):
        responses = self._run_concurrent_mutations(
            first_validation_method="_validate_final_state",
            first_payload={"is_active": False},
        )

        self._assert_one_admin_remains(responses)

    def test_concurrent_demotion_and_deactivation_keep_one_active_admin(self):
        responses = self._run_concurrent_mutations(
            first_validation_method="_validate_final_state",
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
            first_validation_method="_validate_final_state",
            first_payload={"role": OrganizationMembership.Role.VIEWER},
        )

        self.assertEqual(self._active_admin_count(), 1)
        self.assertEqual(
            [response.status_code for response in responses],
            [200, 200],
        )
        self.assertEqual(self._account_update_audit_count(), 2)

    def test_waiting_request_rechecks_actor_admin_permission(self):
        waiting_target_user = get_user_model().objects.create_user(
            username="membership-waiting-target",
            email="membership-waiting-target@example.test",
        )
        waiting_target = OrganizationMembership.objects.create(
            user=waiting_target_user,
            organization=self.organization,
            role=OrganizationMembership.Role.VIEWER,
        )
        self.first_membership.is_responsable = True
        self.first_membership.save(update_fields=["is_responsable"])

        responses = self._run_concurrent_mutations(
            first_validation_method="_validate_final_state",
            first_payload={"role": OrganizationMembership.Role.VIEWER},
            second_payload={"role": OrganizationMembership.Role.LAB_TECHNICIAN},
            first_actor=self.first_admin,
            second_actor=self.second_admin,
            second_target=waiting_target,
        )

        self.assertEqual(self._active_admin_count(), 1)
        self.assertEqual(
            [response.status_code for response in responses],
            [200, 403],
        )
        self.second_membership.refresh_from_db()
        waiting_target.refresh_from_db()
        self.assertEqual(
            self.second_membership.role,
            OrganizationMembership.Role.VIEWER,
        )
        self.assertEqual(
            waiting_target.role,
            OrganizationMembership.Role.VIEWER,
        )
        self.assertEqual(
            str(responses[1].data["detail"]),
            "This account cannot manage members for the selected organization.",
        )
        self.assertEqual(self._account_update_audit_count(), 1)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_DELIVERY_ENABLED=True,
        DEFAULT_FROM_EMAIL="Polypbase <noreply@polypbase.org>",
    )
    def test_waiting_invitation_rechecks_actor_authority_after_lock(self):
        # A Responsable demotes the inviting Admin while the invitation waits for
        # the organization lock. The invitation must not create an account once
        # the actor has lost the authority it had before the lock.
        self.first_membership.is_responsable = True
        self.first_membership.save(update_fields=["is_responsable"])
        holder_lock_acquired = threading.Event()
        release_holder = threading.Event()
        waiter_lock_attempted = threading.Event()
        waiter_lock_acquired = threading.Event()
        original_validation = OrganizationMembershipDetailAPIView._validate_final_state

        def hold_demotion_after_lock(view, *args, **kwargs):
            result = original_validation(view, *args, **kwargs)
            if kwargs["membership"].pk == self.second_membership.pk:
                holder_lock_acquired.set()
                if not release_holder.wait(timeout=10):
                    raise TimeoutError(
                        "Timed out while holding the organization lock."
                    )
            return result

        with patch.object(
            OrganizationMembershipDetailAPIView,
            "_validate_final_state",
            hold_demotion_after_lock,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                demotion_future = executor.submit(
                    self._patch_membership,
                    actor=self.first_admin,
                    target=self.second_membership,
                    payload={"role": OrganizationMembership.Role.VIEWER},
                )
                try:
                    if not holder_lock_acquired.wait(timeout=10):
                        raise TimeoutError(
                            "Demotion did not acquire the organization lock."
                        )
                    invitation_future = executor.submit(
                        self._invite_member,
                        actor=self.second_admin,
                        payload={
                            "organization_id": self.organization.id,
                            "role": OrganizationMembership.Role.VIEWER,
                            "email": "waiting-invitation@example.test",
                        },
                        organization_lock_attempted=waiter_lock_attempted,
                        organization_lock_acquired=waiter_lock_acquired,
                    )
                    if not waiter_lock_attempted.wait(timeout=10):
                        raise TimeoutError(
                            "Invitation did not attempt the organization lock."
                        )
                    self.assertFalse(waiter_lock_acquired.is_set())
                finally:
                    release_holder.set()

                demotion_response = demotion_future.result(timeout=15)
                invitation_response = invitation_future.result(timeout=15)

        self.assertTrue(waiter_lock_acquired.is_set())
        self.assertEqual(demotion_response.status_code, 200)
        self.assertEqual(invitation_response.status_code, 403)
        self.assertFalse(
            get_user_model().objects.filter(
                email="waiting-invitation@example.test"
            ).exists()
        )
        self.assertFalse(
            AuditLog.objects.filter(
                organization=self.organization,
                description="Member access created",
            ).exists()
        )


@skipUnless(
    connection.vendor == "postgresql",
    "Responsable concurrency requires PostgreSQL row locks.",
)
class InstitutionResponsableConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        user_model = get_user_model()
        self.organization = Organization.objects.create(
            name="Responsable concurrency QA"
        )
        self.first_user = user_model.objects.create_user(
            username="responsable-concurrency-one",
            email="responsable-concurrency-one@example.test",
        )
        self.second_user = user_model.objects.create_user(
            username="responsable-concurrency-two",
            email="responsable-concurrency-two@example.test",
        )
        self.first_membership = OrganizationMembership.objects.create(
            user=self.first_user,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
            is_responsable=True,
        )
        self.second_membership = OrganizationMembership.objects.create(
            user=self.second_user,
            organization=self.organization,
            role=OrganizationMembership.Role.ADMIN,
            is_responsable=True,
        )

    def _relinquish(
        self,
        *,
        actor,
        organization_lock_attempted=None,
        organization_lock_acquired=None,
    ):
        close_old_connections()
        try:
            request = APIRequestFactory().post(
                "/qa/accounts/responsable/relinquish/",
                {},
                format="json",
                HTTP_X_ORGANIZATION_ID=str(self.organization.pk),
            )
            force_authenticate(request, user=actor)

            def perform_request():
                response = InstitutionResponsableRelinquishAPIView.as_view()(request)
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
                    organization_lock_attempted.set()
                    result = execute(sql, params, many, context)
                    organization_lock_acquired.set()
                    return result
                return execute(sql, params, many, context)

            with connection.execute_wrapper(observe_organization_lock):
                return perform_request()
        finally:
            connections.close_all()

    def test_concurrent_self_relinquishments_leave_one_responsable(self):
        holder_lock_acquired = threading.Event()
        release_holder = threading.Event()
        waiter_lock_attempted = threading.Event()
        waiter_lock_acquired = threading.Event()
        original_validation = (
            InstitutionResponsableRelinquishAPIView._ensure_another_active_responsable
        )

        def hold_first_after_post_lock_validation(view, membership):
            result = original_validation(view, membership)
            if membership.pk == self.first_membership.pk:
                holder_lock_acquired.set()
                if not release_holder.wait(timeout=10):
                    raise TimeoutError(
                        "Timed out while holding the organization lock."
                    )
            return result

        with patch.object(
            InstitutionResponsableRelinquishAPIView,
            "_ensure_another_active_responsable",
            hold_first_after_post_lock_validation,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first_future = executor.submit(
                    self._relinquish,
                    actor=self.first_user,
                )
                try:
                    if not holder_lock_acquired.wait(timeout=10):
                        raise TimeoutError(
                            "First relinquishment did not acquire the organization lock."
                        )
                    second_future = executor.submit(
                        self._relinquish,
                        actor=self.second_user,
                        organization_lock_attempted=waiter_lock_attempted,
                        organization_lock_acquired=waiter_lock_acquired,
                    )
                    if not waiter_lock_attempted.wait(timeout=10):
                        raise TimeoutError(
                            "Second relinquishment did not attempt the organization lock."
                        )
                    self.assertFalse(waiter_lock_acquired.is_set())
                finally:
                    release_holder.set()

                responses = [
                    first_future.result(timeout=15),
                    second_future.result(timeout=15),
                ]

        self.assertTrue(waiter_lock_acquired.is_set())
        self.assertCountEqual(
            [response.status_code for response in responses],
            [200, 403],
        )
        self.assertEqual(
            OrganizationMembership.objects.filter(
                organization=self.organization,
                is_active=True,
                role=OrganizationMembership.Role.ADMIN,
                is_responsable=True,
            ).count(),
            1,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                organization=self.organization,
                description="Institution Responsable relinquished",
            ).count(),
            1,
        )
