from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import translation
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import (
    get_admin_organization_ids,
    get_admin_organizations,
    get_authorized_organizations,
    user_is_org_admin,
)
from apps.organizations.models import Organization

from .models import OrganizationMembership, UserPreference
from .serializers import (
    UserPreferenceSerializer,
    UserProfileSerializer,
    available_interface_languages,
)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class UserProfileAPIView(APIView):
    """Return and update the current user's account preferences."""

    def get(self, request):
        preference = self._get_preference(request.user)
        return Response(self._profile_data(request.user, preference))

    def patch(self, request):
        preference = self._get_preference(request.user)
        serializer = UserPreferenceSerializer(
            preference,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        preference = serializer.save()

        request.session["interface_language"] = preference.interface_language
        translation.activate(preference.interface_language)
        request.LANGUAGE_CODE = preference.interface_language

        return Response(self._profile_data(request.user, preference))

    def _get_preference(self, user):
        preference, _created = UserPreference.objects.get_or_create(user=user)
        return preference

    def _profile_data(self, user, preference):
        organizations = get_authorized_organizations(user).order_by("name")
        memberships = OrganizationMembership.objects.filter(
            user=user,
            is_active=True,
            organization__is_active=True,
        ).select_related("organization").order_by("organization__name")
        serializer = UserProfileSerializer(
            {
                "id": user.id,
                "username": user.get_username(),
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "is_superuser": user.is_superuser,
                "interface_language": preference.interface_language,
                "organizations": organizations,
                "memberships": [
                    {
                        "organization": {
                            "id": membership.organization.id,
                            "name": membership.organization.name,
                            "slug": membership.organization.slug,
                        },
                        "role": membership.role,
                        "role_label": membership.get_role_display(),
                    }
                    for membership in memberships
                ],
                "available_languages": available_interface_languages(),
            }
        )
        return serializer.data


def _role_choices():
    """Return the assignable roles with their display labels."""
    return [
        {"value": value, "label": str(label)}
        for value, label in OrganizationMembership.Role.choices
    ]


def _member_data(membership, *, current_user):
    """Serialize one membership into the account-management payload."""
    user = membership.user
    full_name = " ".join(
        part for part in [user.first_name, user.last_name] if part
    ).strip()
    return {
        "membership_id": membership.id,
        "user_id": user.id,
        "username": user.get_username(),
        "full_name": full_name or user.get_username(),
        "email": user.email,
        "organization": {
            "id": membership.organization.id,
            "name": membership.organization.name,
        },
        "role": membership.role,
        "role_label": membership.get_role_display(),
        "is_active": membership.is_active,
        "last_login": user.last_login.date().isoformat() if user.last_login else None,
        "is_self": user.id == current_user.id,
    }


@method_decorator(ensure_csrf_cookie, name="dispatch")
class OrganizationMemberListCreateAPIView(APIView):
    """List and create memberships within the organizations the user administers."""

    def get(self, request):
        if not user_is_org_admin(request.user):
            raise PermissionDenied("This account cannot manage members.")

        organization_ids = get_admin_organization_ids(request.user)
        memberships = (
            OrganizationMembership.objects.filter(organization_id__in=organization_ids)
            .select_related("organization", "user")
            .order_by("organization__name", "user__username")
        )
        organizations = get_admin_organizations(request.user).order_by("name")

        return Response(
            {
                "members": [
                    _member_data(membership, current_user=request.user)
                    for membership in memberships
                ],
                "manageable_organizations": [
                    {"id": organization.id, "name": organization.name}
                    for organization in organizations
                ],
                "roles": _role_choices(),
            }
        )

    def post(self, request):
        if not user_is_org_admin(request.user):
            raise PermissionDenied("This account cannot manage members.")

        admin_org_ids = get_admin_organization_ids(request.user)
        data = request.data

        organization = self._get_managed_organization(data.get("organization_id"), admin_org_ids)
        role = self._validate_role(data.get("role"))

        username = (data.get("username") or "").strip()
        if not username:
            raise ValidationError({"username": "Un identifiant est requis."})

        user_model = get_user_model()
        with transaction.atomic():
            user = user_model.objects.filter(username__iexact=username).first()
            if user is None:
                user = self._create_user(user_model, username, data)

            membership, created = OrganizationMembership.objects.get_or_create(
                user=user,
                organization=organization,
                defaults={"role": role, "is_active": True},
            )
            if not created:
                membership.role = role
                membership.is_active = True
                membership.save(update_fields=["role", "is_active"])

            UserPreference.objects.get_or_create(user=user)

        return Response(
            _member_data(membership, current_user=request.user),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def _get_managed_organization(self, organization_id, admin_org_ids):
        if organization_id in (None, ""):
            if len(admin_org_ids) == 1:
                organization_id = admin_org_ids[0]
            else:
                raise ValidationError({"organization_id": "La structure est requise."})
        try:
            organization_id = int(organization_id)
        except (TypeError, ValueError):
            raise ValidationError({"organization_id": "Structure invalide."})
        if organization_id not in admin_org_ids:
            raise PermissionDenied("You cannot manage members for this organization.")
        return Organization.objects.get(id=organization_id)

    def _validate_role(self, role):
        valid_roles = {value for value, _label in OrganizationMembership.Role.choices}
        if role not in valid_roles:
            raise ValidationError({"role": "Rôle invalide."})
        return role

    def _create_user(self, user_model, username, data):
        password = (data.get("password") or "").strip()
        if not password:
            raise ValidationError(
                {"password": "Un mot de passe initial est requis pour un nouveau compte."}
            )
        if len(password) < 8:
            raise ValidationError(
                {"password": "Le mot de passe doit contenir au moins 8 caractères."}
            )
        user = user_model(
            username=username,
            email=(data.get("email") or "").strip(),
            first_name=(data.get("first_name") or "").strip(),
            last_name=(data.get("last_name") or "").strip(),
        )
        user.set_password(password)
        user.save()
        return user


@method_decorator(ensure_csrf_cookie, name="dispatch")
class OrganizationMembershipDetailAPIView(APIView):
    """Update a single membership (role or activation) within a managed organization."""

    def patch(self, request, pk):
        if not user_is_org_admin(request.user):
            raise PermissionDenied("This account cannot manage members.")

        admin_org_ids = get_admin_organization_ids(request.user)
        try:
            membership = OrganizationMembership.objects.select_related(
                "organization", "user"
            ).get(pk=pk)
        except OrganizationMembership.DoesNotExist:
            raise ValidationError({"detail": "Membre introuvable."})

        if membership.organization_id not in admin_org_ids:
            raise PermissionDenied("You cannot manage members for this organization.")

        if membership.user_id == request.user.id:
            raise PermissionDenied("Vous ne pouvez pas modifier votre propre rôle.")

        updated_fields = []
        if "role" in request.data:
            membership.role = self._validate_role(request.data.get("role"))
            updated_fields.append("role")
        if "is_active" in request.data:
            membership.is_active = bool(request.data.get("is_active"))
            updated_fields.append("is_active")

        if updated_fields:
            membership.save(update_fields=updated_fields)

        return Response(_member_data(membership, current_user=request.user))

    def _validate_role(self, role):
        valid_roles = {value for value, _label in OrganizationMembership.Role.choices}
        if role not in valid_roles:
            raise ValidationError({"role": "Rôle invalide."})
        return role
