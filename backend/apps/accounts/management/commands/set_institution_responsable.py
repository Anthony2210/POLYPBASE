from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.api_views import _changed_values, _member_audit_values
from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.organizations.models import Organization


class Command(BaseCommand):
    help = (
        "Grant or revoke institution Responsable status for one exact membership. "
        "The default mode is a dry run."
    )

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument("--membership-id", type=int, required=True)
        parser.add_argument("--actor-user-id", type=int, required=True)
        action = parser.add_mutually_exclusive_group(required=True)
        action.add_argument("--grant", action="store_true")
        action.add_argument("--revoke", action="store_true")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the requested status change. Without this flag, only report it.",
        )

    def handle(self, *args, **options):
        organization_id = options["organization_id"]
        membership_id = options["membership_id"]
        actor_user_id = options["actor_user_id"]
        grant = options["grant"]

        if options["apply"]:
            self._apply(
                organization_id=organization_id,
                membership_id=membership_id,
                actor_user_id=actor_user_id,
                grant=grant,
            )
            return

        organization = self._get_organization(organization_id)
        actor = self._get_actor(actor_user_id)
        self._validate_actor(actor)
        membership = self._get_membership(membership_id, organization)
        self._validate_target(membership, organization, grant=grant)
        action = "grant" if grant else "revoke"
        self.stdout.write(
            f"DRY RUN: would {action} institution Responsable status for "
            f"membership {membership.id} in organization {organization.id}."
        )

    def _apply(self, *, organization_id, membership_id, actor_user_id, grant):
        with transaction.atomic():
            organization = self._lock_organization(organization_id)
            actor = self._get_actor(actor_user_id)
            self._validate_actor(actor)

            membership = self._get_membership(membership_id, organization)
            self._validate_target(membership, organization, grant=grant)
            if membership.is_responsable == grant:
                self.stdout.write("No change required.")
                return

            before_values = _member_audit_values(membership)
            membership.is_responsable = grant
            membership.save(update_fields=["is_responsable"])
            after_values = _member_audit_values(membership)
            action = "granted" if grant else "revoked"
            AuditLog.objects.create(
                organization=organization,
                user=actor,
                action=AuditLog.Action.UPDATE,
                object_type="account",
                object_id=membership.user.get_username(),
                description=f"Institution Responsable {action} by platform",
                metadata={
                    "user_id": membership.user_id,
                    "membership_id": membership.id,
                    "valeurs": after_values,
                    "modifications": _changed_values(before_values, after_values),
                },
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Institution Responsable status {action} for membership "
                    f"{membership.id} in organization {organization.id}."
                )
            )

    def _validate_actor(self, actor):
        if not actor.is_superuser or not actor.is_active:
            raise CommandError("An active Django superuser actor is required.")

    def _validate_target(self, membership, organization, *, grant):
        if not grant:
            return
        if membership.user.is_superuser:
            raise CommandError("A Django superuser cannot be an institution Responsable.")
        if not organization.is_active:
            raise CommandError("Cannot grant Responsable status in an inactive organization.")
        if not membership.is_active or membership.role != OrganizationMembership.Role.ADMIN:
            raise CommandError("Responsable status can only be granted to an active Admin membership.")

    def _lock_organization(self, organization_id):
        try:
            return Organization.objects.select_for_update().get(pk=organization_id)
        except Organization.DoesNotExist as error:
            raise CommandError(f"Organization {organization_id} does not exist.") from error

    def _get_organization(self, organization_id):
        try:
            return Organization.objects.get(pk=organization_id)
        except Organization.DoesNotExist as error:
            raise CommandError(f"Organization {organization_id} does not exist.") from error

    def _get_actor(self, actor_user_id):
        try:
            return get_user_model().objects.get(pk=actor_user_id)
        except get_user_model().DoesNotExist as error:
            raise CommandError(f"Actor user {actor_user_id} does not exist.") from error

    def _get_membership(self, membership_id, organization):
        try:
            return OrganizationMembership.objects.select_related("user").get(
                pk=membership_id,
                organization=organization,
            )
        except OrganizationMembership.DoesNotExist as error:
            raise CommandError(
                f"Membership {membership_id} does not belong to organization "
                f"{organization.id}."
            ) from error
