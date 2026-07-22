import re

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count
from django.utils import translation
from django.utils.crypto import get_random_string
from django.utils.dateparse import parse_date
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import (
    get_admin_organization_ids,
    get_admin_organizations,
    get_authorized_organizations,
    user_is_org_admin,
)
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization

from .models import OrganizationMembership, UserPreference
from .serializers import (
    UserPreferenceSerializer,
    UserProfileSerializer,
    available_interface_languages,
)


@method_decorator(ensure_csrf_cookie, name="dispatch")
@method_decorator(csrf_protect, name="dispatch")
class SessionLoginAPIView(APIView):
    """Create a Django session for the React login form."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"detail": "CSRF cookie set."})

    def post(self, request):
        username = str(request.data.get("username", "")).strip()
        password = str(request.data.get("password", ""))
        user = authenticate(request, username=username, password=password)

        if user is None or not user.is_active:
            return Response(
                {"detail": "Invalid credentials."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        login(request, user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SessionLogoutAPIView(APIView):
    """End the current Django session from the React profile page."""

    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


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


def _changed_values(before, after):
    """Return only values changed by an admin action."""
    return {
        key: {"avant": before.get(key), "apres": after_value}
        for key, after_value in after.items()
        if before.get(key) != after_value
    }


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


def _member_audit_values(membership):
    """Keep account-management audit entries readable for administrators."""
    user = membership.user
    full_name = " ".join(
        part for part in [user.first_name, user.last_name] if part
    ).strip()
    return {
        "identifiant": user.get_username(),
        "nom": full_name or user.get_username(),
        "email": user.email,
        "structure": membership.organization.name,
        "role": membership.role,
        "acces_actif": membership.is_active,
    }


def _format_first_name(value):
    name = " ".join(str(value or "").strip().split()).lower()
    return re.sub(
        r"(^|[\s'-])([^\W\d_])",
        lambda match: f"{match.group(1)}{match.group(2).upper()}",
        name,
        flags=re.UNICODE,
    )


def _format_last_name(value):
    return " ".join(str(value or "").strip().split()).upper()


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

        AuditLog.objects.create(
            organization=organization,
            user=request.user,
            action=AuditLog.Action.CREATION if created else AuditLog.Action.UPDATE,
            object_type="account",
            object_id=user.get_username(),
            description="Member access created" if created else "Member access restored",
            metadata={
                "user_id": user.id,
                "membership_id": membership.id,
                "valeurs": _member_audit_values(membership),
            },
        )

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
        generated_password = False
        email = (data.get("email") or "").strip()
        if not password:
            if not email:
                raise ValidationError(
                    {"email": "Un email est requis pour envoyer le mot de passe temporaire."}
                )
            password = get_random_string(
                14,
                allowed_chars="abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789",
            )
            generated_password = True
        if len(password) < 8:
            raise ValidationError(
                {"password": "Le mot de passe doit contenir au moins 8 caractères."}
            )
        user = user_model(
            username=username,
            email=email,
            first_name=_format_first_name(data.get("first_name")),
            last_name=_format_last_name(data.get("last_name")),
        )
        user.set_password(password)
        user.save()
        if generated_password:
            self._send_temporary_password(user, password)
        return user

    def _send_temporary_password(self, user, password):
        if not user.email:
            return
        message = (
            "Bonjour,\n\n"
            "Un compte Polypbase vient d'etre cree pour vous.\n"
            f"Identifiant : {user.username}\n"
            f"Mot de passe temporaire : {password}\n\n"
            "Connectez-vous, puis remplacez ce mot de passe par un mot de passe personnel.\n"
        )
        send_mail(
            "Acces Polypbase",
            message,
            settings.DEFAULT_FROM_EMAIL,
            [user.email],
            fail_silently=True,
        )


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

        before_values = _member_audit_values(membership)
        updated_fields = []
        if "role" in request.data:
            membership.role = self._validate_role(request.data.get("role"))
            updated_fields.append("role")
        if "is_active" in request.data:
            membership.is_active = bool(request.data.get("is_active"))
            updated_fields.append("is_active")

        if updated_fields:
            membership.save(update_fields=updated_fields)
            after_values = _member_audit_values(membership)
            AuditLog.objects.create(
                organization=membership.organization,
                user=request.user,
                action=AuditLog.Action.UPDATE,
                object_type="account",
                object_id=membership.user.get_username(),
                description="Member access updated",
                metadata={
                    "user_id": membership.user_id,
                    "membership_id": membership.id,
                    "valeurs": after_values,
                    "modifications": _changed_values(before_values, after_values),
                },
            )

        return Response(_member_data(membership, current_user=request.user))

    def _validate_role(self, role):
        valid_roles = {value for value, _label in OrganizationMembership.Role.choices}
        if role not in valid_roles:
            raise ValidationError({"role": "Rôle invalide."})
        return role


@method_decorator(ensure_csrf_cookie, name="dispatch")
class AdminAuditLogListAPIView(APIView):
    """Return recent audit trail entries for organizations administered by the user."""

    def get(self, request):
        if not user_is_org_admin(request.user):
            raise PermissionDenied("This account cannot view the audit log.")

        try:
            limit = int(request.query_params.get("limit", 40))
        except (TypeError, ValueError):
            limit = 40
        limit = max(1, min(limit, 100))

        try:
            offset = int(request.query_params.get("offset", 0))
        except (TypeError, ValueError):
            offset = 0
        offset = max(0, offset)

        organization_ids = get_admin_organization_ids(request.user)
        impactful_actions = [
            AuditLog.Action.CREATION,
            AuditLog.Action.UPDATE,
            AuditLog.Action.ARCHIVE,
            AuditLog.Action.ENTRY,
            AuditLog.Action.SUBCULTURE,
            AuditLog.Action.TRANSFER,
            AuditLog.Action.IMPORT,
            AuditLog.Action.EXPORT,
        ]
        logs_query = AuditLog.objects.filter(
            organization_id__in=organization_ids,
            action__in=impactful_actions,
        )

        date_filter = request.query_params.get("date", "").strip()
        if date_filter:
            selected_date = parse_date(date_filter)
            if selected_date is None:
                raise ValidationError({"date": "Date invalide."})
            logs_query = logs_query.filter(created_at__date=selected_date)

        include_options = request.query_params.get("include_options") == "1"
        include_total = request.query_params.get("include_total") == "1"
        action_options = []
        if include_options:
            action_labels = dict(AuditLog.Action.choices)
            action_options = [
                {
                    "value": row["action"],
                    "label": action_labels.get(row["action"], row["action"]),
                    "count": row["count"],
                }
                for row in logs_query.values("action")
                .annotate(count=Count("id"))
                .order_by("action")
            ]

        action_filter = request.query_params.get("action", "").strip()
        if action_filter:
            selected_actions = [
                action.strip()
                for action in action_filter.split(",")
                if action.strip()
            ]
            valid_actions = {value for value, _label in AuditLog.Action.choices}
            invalid_actions = [
                action for action in selected_actions if action not in valid_actions
            ]
            if invalid_actions:
                raise ValidationError({"action": "Type d'action invalide."})
            logs_query = logs_query.filter(action__in=selected_actions)

        logs = list(
            logs_query.select_related("organization", "user")
            .order_by("-created_at")[offset : offset + limit + 1]
        )
        has_more = len(logs) > limit
        logs = logs[:limit]
        measurement_ids = [
            log.metadata.get("measurement_id")
            for log in logs
            if isinstance(log.metadata, dict) and log.metadata.get("measurement_id")
        ]
        measurements_by_id = {
            measurement.id: measurement
            for measurement in BiologicalMeasurement.objects.filter(id__in=measurement_ids)
        }

        payload = {
            "results": [
                self._serialize_log(log, measurements_by_id)
                for log in logs
            ],
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "next_offset": offset + len(logs) if has_more else None,
        }
        if include_total:
            payload["total_count"] = logs_query.count()
        if include_options:
            payload["action_options"] = action_options

        return Response(payload)

    def _serialize_log(self, log, measurements_by_id):
        return {
            "id": log.id,
            "created_at": log.created_at,
            "organization": log.organization.name if log.organization else None,
            "user": log.user.get_username() if log.user else None,
            "action": log.action,
            "action_label": log.get_action_display(),
            "object_type": log.object_type,
            "object_id": log.object_id,
            "description": log.description,
            "metadata": self._enriched_metadata(log, measurements_by_id),
        }

    def _enriched_metadata(self, log, measurements_by_id):
        metadata = dict(log.metadata or {})
        if "valeurs" in metadata:
            return metadata

        measurement_id = metadata.get("measurement_id")
        measurement = measurements_by_id.get(measurement_id) or self._find_measurement_from_log(log)
        if measurement is not None:
            metadata["valeurs"] = {
                "date": measurement.measured_on.isoformat(),
                "polypes": measurement.polyp_count,
                "ephyrules": measurement.ephyrae_count,
                "strobiles": measurement.strobila_count,
                "salinite_psu": str(measurement.salinity_psu) if measurement.salinity_psu is not None else None,
                "statut_culture": measurement.culture_status,
                "a_verifier": measurement.needs_attention,
                "note": measurement.notes,
            }
        return metadata

    def _find_measurement_from_log(self, log):
        if log.object_type != "box" or not log.object_id:
            return None

        match = re.search(r"(\d{4}-\d{2}-\d{2})", log.description or "")
        if not match:
            return None

        return (
            BiologicalMeasurement.objects.filter(
                box__global_code=log.object_id,
                measured_on=match.group(1),
            )
            .order_by("-created_at")
            .first()
        )
