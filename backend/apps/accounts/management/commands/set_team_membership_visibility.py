from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.api_views import _member_audit_values
from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.organizations.models import Organization


class Command(BaseCommand):
    help = (
        "Hide or show exact memberships in the Administration team lists. This is "
        "display only: roles, permissions and last-Admin protections do not change. "
        "The default mode is a dry run."
    )

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, required=True)
        parser.add_argument(
            "--membership-id",
            type=int,
            action="append",
            required=True,
            help="Exact membership id. Repeat the option for several memberships.",
        )
        parser.add_argument("--actor-user-id", type=int, required=True)
        action = parser.add_mutually_exclusive_group(required=True)
        action.add_argument("--hide", action="store_true")
        action.add_argument("--show", action="store_true")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the change. Without this flag, only report it.",
        )

    def handle(self, *args, **options):
        hide = options["hide"]
        membership_ids = list(dict.fromkeys(options["membership_id"]))
        apply_changes = options["apply"]

        with transaction.atomic():
            try:
                organization = Organization.objects.select_for_update().get(
                    pk=options["organization_id"]
                )
            except Organization.DoesNotExist as error:
                raise CommandError(
                    f"Organization {options['organization_id']} does not exist."
                ) from error
            actor = self._get_actor(options["actor_user_id"])
            memberships = self._get_memberships(membership_ids, organization)

            for membership in memberships:
                label = (
                    f"membership {membership.id} "
                    f"({_member_audit_values(membership)['nom']}, {membership.user.email})"
                )
                if membership.is_hidden_from_team == hide:
                    self.stdout.write(f"No change required for {label}.")
                    continue
                if not apply_changes:
                    verb = "hide" if hide else "show"
                    self.stdout.write(f"DRY RUN: would {verb} {label}.")
                    continue

                membership.is_hidden_from_team = hide
                membership.save(update_fields=["is_hidden_from_team"])
                AuditLog.objects.create(
                    organization=organization,
                    user=actor,
                    action=AuditLog.Action.UPDATE,
                    object_type="account",
                    object_id=membership.user.get_username(),
                    description="Member team visibility updated",
                    metadata={
                        "user_id": membership.user_id,
                        "membership_id": membership.id,
                        "valeurs": _member_audit_values(membership),
                        "masque_equipe": hide,
                    },
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Team visibility {'hidden' if hide else 'shown'} for {label}."
                    )
                )

    def _get_actor(self, actor_user_id):
        try:
            actor = get_user_model().objects.get(pk=actor_user_id)
        except get_user_model().DoesNotExist as error:
            raise CommandError(f"Actor user {actor_user_id} does not exist.") from error
        if not actor.is_superuser or not actor.is_active:
            raise CommandError("An active Django superuser actor is required.")
        return actor

    def _get_memberships(self, membership_ids, organization):
        memberships = list(
            OrganizationMembership.objects.select_related("user").filter(
                pk__in=membership_ids,
                organization=organization,
            )
        )
        missing = set(membership_ids) - {membership.id for membership in memberships}
        if missing:
            raise CommandError(
                f"Memberships {sorted(missing)} do not belong to organization "
                f"{organization.id}."
            )
        return memberships
