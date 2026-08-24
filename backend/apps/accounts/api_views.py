import logging
import re

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Count
from django.db.models.functions import Coalesce
from django.utils import translation
from django.utils.dateparse import parse_date
from django.utils.decorators import method_decorator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import (
    get_active_admin_organization_ids,
    get_active_organization_from_request,
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
from .throttling import (
    clear_events,
    consume_event,
    get_client_ip,
    login_account_policy,
    login_ip_policy,
    password_reset_account_policy,
    password_reset_ip_policy,
    record_event,
    retry_after,
)


logger = logging.getLogger(__name__)

LOGIN_IP_SCOPE = "login_ip"
LOGIN_ACCOUNT_SCOPE = "login_account"
PASSWORD_RESET_IP_SCOPE = "password_reset_ip"
PASSWORD_RESET_ACCOUNT_SCOPE = "password_reset_account"


class EmailDeliveryUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = (
        "L'envoi d'email n'est pas disponible. Configurez le serveur SMTP avant "
        "de créer un accès."
    )
    default_code = "email_delivery_unavailable"


def _too_many_attempts(retry_seconds):
    response = Response(
        {"detail": "Trop de tentatives. Réessayez plus tard."},
        status=status.HTTP_429_TOO_MANY_REQUESTS,
    )
    response["Retry-After"] = str(max(1, retry_seconds))
    return response


def _password_setup_link(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return f"{settings.PUBLIC_BASE_URL}/reset-password/{uid}/{token}"


def _send_password_email(user, *, invitation):
    link = _password_setup_link(user)
    if invitation:
        subject = "Invitation Polypbase"
        message = (
            "Bonjour,\n\n"
            "Un accès Polypbase vient d'être créé pour vous.\n"
            f"Identifiant : {user.get_username()}\n\n"
            "Choisissez votre mot de passe avec ce lien à usage unique :\n"
            f"{link}\n\n"
            "Ce lien est valable une heure.\n"
        )
    else:
        subject = "Réinitialisation de votre mot de passe Polypbase"
        message = (
            "Bonjour,\n\n"
            "Vous avez demandé la réinitialisation de votre mot de passe Polypbase.\n"
            f"Identifiant : {user.get_username()}\n\n"
            "Choisissez un nouveau mot de passe avec ce lien à usage unique :\n"
            f"{link}\n\n"
            "Ce lien est valable une heure. Si vous n'êtes pas à l'origine de cette "
            "demande, ignorez ce message : votre mot de passe reste inchangé.\n"
        )

    return send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
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
        client_ip = get_client_ip(request)
        user_model = get_user_model()
        account = user_model.objects.filter(
            username__iexact=username,
            is_active=True,
        ).only("pk").first()

        retry_seconds = retry_after(LOGIN_IP_SCOPE, client_ip, login_ip_policy())
        if account is not None:
            retry_seconds = max(
                retry_seconds,
                retry_after(
                    LOGIN_ACCOUNT_SCOPE,
                    account.pk,
                    login_account_policy(),
                ),
            )
        if retry_seconds:
            return _too_many_attempts(retry_seconds)

        user = authenticate(request, username=username, password=password)

        if user is None or not user.is_active:
            record_event(LOGIN_IP_SCOPE, client_ip, login_ip_policy())
            if account is not None:
                record_event(
                    LOGIN_ACCOUNT_SCOPE,
                    account.pk,
                    login_account_policy(),
                )
            return Response(
                {"detail": "Invalid credentials."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        clear_events(LOGIN_ACCOUNT_SCOPE, user.pk)
        login(request, user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SessionLogoutAPIView(APIView):
    """End the current Django session from the React profile page."""

    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(ensure_csrf_cookie, name="dispatch")
@method_decorator(csrf_protect, name="dispatch")
class PasswordResetRequestAPIView(APIView):
    """Email a reset link to whoever owns the address.

    The answer is deliberately identical whether or not an account exists: a
    different response would let anyone probe which addresses are registered.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"detail": "CSRF cookie set."})

    def post(self, request):
        email = str(request.data.get("email", "")).strip()
        client_ip = get_client_ip(request)
        retry_seconds = consume_event(
            PASSWORD_RESET_IP_SCOPE,
            client_ip,
            password_reset_ip_policy(),
        )
        if retry_seconds:
            return _too_many_attempts(retry_seconds)

        if email:
            # An address could in theory be shared by several accounts; each one
            # gets its own link. Inactive accounts are skipped.
            for user in get_user_model().objects.filter(email__iexact=email, is_active=True):
                account_retry = consume_event(
                    PASSWORD_RESET_ACCOUNT_SCOPE,
                    user.pk,
                    password_reset_account_policy(),
                )
                if not account_retry and settings.EMAIL_DELIVERY_ENABLED:
                    self._send_reset_link(user)

        return Response(status=status.HTTP_204_NO_CONTENT)

    def _send_reset_link(self, user):
        try:
            _send_password_email(user, invitation=False)
        except Exception:
            # The public response stays identical to avoid revealing accounts.
            logger.exception("Password-reset email delivery failed")


@method_decorator(ensure_csrf_cookie, name="dispatch")
@method_decorator(csrf_protect, name="dispatch")
class PasswordResetConfirmAPIView(APIView):
    """Set a new password from a link produced by the request endpoint."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"detail": "CSRF cookie set."})

    def post(self, request):
        uid = str(request.data.get("uid", ""))
        token = str(request.data.get("token", ""))
        password = str(request.data.get("password", ""))

        user = self._get_user(uid)
        if user is None or not default_token_generator.check_token(user, token):
            return Response(
                {"detail": "Ce lien est invalide ou a expire. Demandez-en un nouveau."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            validate_password(password, user)
        except DjangoValidationError as error:
            return Response({"password": list(error.messages)}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(password)
        user.save(update_fields=["password"])

        # Saving the new password changes the hash the token is derived from, so
        # the link stops working here: it can only be used once.
        AuditLog.objects.create(
            user=user,
            action=AuditLog.Action.UPDATE,
            object_type="account",
            object_id=user.get_username(),
            description="Password reset from the login page",
        )

        return Response(status=status.HTTP_204_NO_CONTENT)

    def _get_user(self, uid):
        user_model = get_user_model()
        try:
            pk = urlsafe_base64_decode(uid).decode()
            return user_model.objects.get(pk=pk, is_active=True)
        except (TypeError, ValueError, OverflowError, user_model.DoesNotExist):
            return None


@method_decorator(ensure_csrf_cookie, name="dispatch")
class UserProfileAPIView(APIView):
    """Return and update the current user's account preferences."""

    def get(self, request):
        preference = self._get_preference(request.user)
        return Response(self._profile_data(request, preference))

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

        return Response(self._profile_data(request, preference))

    def _get_preference(self, user):
        preference, _created = UserPreference.objects.get_or_create(user=user)
        return preference

    def _profile_data(self, request, preference):
        user = request.user
        organizations = get_authorized_organizations(user).order_by("name")
        active_organization = get_active_organization_from_request(request)
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
                "active_organization": active_organization,
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

        organization_ids = get_active_admin_organization_ids(request)
        if not organization_ids:
            raise PermissionDenied("This account cannot manage members for the selected organization.")
        memberships = (
            OrganizationMembership.objects.filter(organization_id__in=organization_ids)
            .select_related("organization", "user")
            .order_by("organization__name", "user__username")
        )
        organizations = get_admin_organizations(request.user).filter(id__in=organization_ids).order_by("name")

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

        admin_org_ids = get_active_admin_organization_ids(request)
        if not admin_org_ids:
            raise PermissionDenied("This account cannot manage members for the selected organization.")
        data = request.data

        organization = self._get_managed_organization(data.get("organization_id"), admin_org_ids)
        role = self._validate_role(data.get("role"))

        username = (data.get("username") or "").strip()
        if not username:
            raise ValidationError({"username": "Un identifiant est requis."})

        email = (data.get("email") or "").strip().lower()
        if not email:
            raise ValidationError({"email": "Une adresse email est requise."})
        if data.get("password") not in (None, ""):
            raise ValidationError(
                {"password": "Un mot de passe ne peut pas être défini par un administrateur."}
            )
        if not settings.EMAIL_DELIVERY_ENABLED:
            raise EmailDeliveryUnavailable()

        user_model = get_user_model()
        with transaction.atomic():
            self._validate_new_user_identity(user_model, username, email)
            user = self._create_user(user_model, username, email, data)

            membership = OrganizationMembership.objects.create(
                user=user,
                organization=organization,
                role=role,
                is_active=True,
            )

            UserPreference.objects.get_or_create(user=user)
            self._send_account_invitation(user)

        AuditLog.objects.create(
            organization=organization,
            user=request.user,
            action=AuditLog.Action.CREATION,
            object_type="account",
            object_id=user.get_username(),
            description="Member access created",
            metadata={
                "user_id": user.id,
                "membership_id": membership.id,
                "valeurs": _member_audit_values(membership),
            },
        )

        return Response(
            _member_data(membership, current_user=request.user),
            status=status.HTTP_201_CREATED,
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

    def _validate_new_user_identity(self, user_model, username, email):
        errors = {}
        if user_model.objects.filter(username__iexact=username).exists():
            errors["username"] = "Cet identifiant est déjà utilisé."
        if user_model.objects.filter(email__iexact=email).exists():
            errors["email"] = "Cette adresse email est déjà utilisée."
        if errors:
            raise ValidationError(errors)

    def _validate_role(self, role):
        valid_roles = {value for value, _label in OrganizationMembership.Role.choices}
        if role not in valid_roles:
            raise ValidationError({"role": "Rôle invalide."})
        return role

    def _create_user(self, user_model, username, email, data):
        user = user_model(
            username=username,
            email=email,
            first_name=_format_first_name(data.get("first_name")),
            last_name=_format_last_name(data.get("last_name")),
        )
        user.set_unusable_password()
        user.save()
        return user

    def _send_account_invitation(self, user):
        try:
            sent_count = _send_password_email(user, invitation=True)
        except Exception as error:
            raise EmailDeliveryUnavailable() from error
        if sent_count != 1:
            raise EmailDeliveryUnavailable()


@method_decorator(ensure_csrf_cookie, name="dispatch")
class OrganizationMembershipDetailAPIView(APIView):
    """Update a single membership (role or activation) within a managed organization."""

    def patch(self, request, pk):
        if not user_is_org_admin(request.user):
            raise PermissionDenied("This account cannot manage members.")

        admin_org_ids = get_active_admin_organization_ids(request)
        if not admin_org_ids:
            raise PermissionDenied("This account cannot manage members for the selected organization.")
        try:
            membership = OrganizationMembership.objects.select_related(
                "organization", "user"
            ).get(pk=pk)
        except OrganizationMembership.DoesNotExist:
            raise ValidationError({"detail": "Membre introuvable."})

        if membership.organization_id not in admin_org_ids:
            raise PermissionDenied("You cannot manage members for this organization.")

        before_values = _member_audit_values(membership)
        updated_fields = []
        if "role" in request.data:
            next_role = self._validate_role(request.data.get("role"))
            self._ensure_role_change_allowed(
                membership=membership,
                next_role=next_role,
            )
            membership.role = next_role
            updated_fields.append("role")
        if "is_active" in request.data:
            next_is_active = request.data.get("is_active")
            if not isinstance(next_is_active, bool):
                raise ValidationError({"is_active": "Statut invalide."})
            self._ensure_activation_change_allowed(
                request_user=request.user,
                membership=membership,
                next_is_active=next_is_active,
            )
            membership.is_active = next_is_active
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

    def _ensure_role_change_allowed(self, membership, next_role):
        if (
            membership.role == OrganizationMembership.Role.ADMIN
            and next_role != OrganizationMembership.Role.ADMIN
        ):
            other_admin_exists = OrganizationMembership.objects.filter(
                organization=membership.organization,
                is_active=True,
                role=OrganizationMembership.Role.ADMIN,
            ).exclude(pk=membership.pk).exists()
            if not other_admin_exists:
                raise PermissionDenied(
                    "Le dernier administrateur actif de cette structure ne peut pas être rétrogradé."
                )

    def _ensure_activation_change_allowed(self, request_user, membership, next_is_active):
        if next_is_active or not membership.is_active:
            return
        if membership.user_id == request_user.id:
            raise PermissionDenied(
                "Vous ne pouvez pas désactiver votre propre accès."
            )
        if membership.role != OrganizationMembership.Role.ADMIN:
            return
        other_admin_exists = OrganizationMembership.objects.filter(
            organization=membership.organization,
            is_active=True,
            role=OrganizationMembership.Role.ADMIN,
        ).exclude(pk=membership.pk).exists()
        if not other_admin_exists:
            raise PermissionDenied(
                "Le dernier administrateur actif de cette structure ne peut pas être désactivé."
            )

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

        organization_ids = get_active_admin_organization_ids(request)
        if not organization_ids:
            raise PermissionDenied("This account cannot view the audit log for the selected organization.")
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
            logs_query.select_related("organization", "user", "edited_by")
            # Sorted on the last time the entry changed: a corrected measurement
            # updates its existing entry in place, so ordering on created_at
            # alone would leave the correction buried in the past.
            .annotate(effective_at=Coalesce("edited_at", "created_at"))
            .order_by("-effective_at")[offset : offset + limit + 1]
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
            # select_related: the edit link reads measurement.box.global_code.
            for measurement in BiologicalMeasurement.objects.filter(
                id__in=measurement_ids
            ).select_related("box")
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
        # Resolved once and reused: the fallback lookup hits the database, so
        # doing it separately for the metadata and for the edit link would
        # double the queries.
        measurement = self._resolve_measurement(log, measurements_by_id)
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
            # The entry is placed in the timeline by effective_at, but still
            # shows created_at as the moment the measurement was recorded.
            "effective_at": getattr(log, "effective_at", None) or log.created_at,
            "edited_at": log.edited_at,
            "edited_by": log.edited_by.get_username() if log.edited_by else None,
            "metadata": self._enriched_metadata(log, measurement),
            # Lets the history open the measurement itself for correction,
            # instead of sending the user off to the box sheet.
            "editable_measurement": self._editable_measurement(log, measurement),
        }

    def _resolve_measurement(self, log, measurements_by_id):
        metadata = log.metadata if isinstance(log.metadata, dict) else {}
        measurement_id = metadata.get("measurement_id")
        if measurement_id and measurement_id in measurements_by_id:
            return measurements_by_id[measurement_id]
        # An entry may predate measurement_id, or point at a deleted row.
        return self._find_measurement_from_log(log)

    def _editable_measurement(self, log, measurement):
        """Only a real measurement entry may be corrected from the history.

        Exports, transfers, account changes and the like are never editable
        here. The date-matching fallback used to enrich the display is not
        trusted for this: an export mentioning a date could otherwise be tied to
        an unrelated measurement. An explicit measurement_id is required.
        """
        if measurement is None:
            return None

        if log.action not in {AuditLog.Action.ENTRY, AuditLog.Action.UPDATE}:
            return None

        metadata = log.metadata if isinstance(log.metadata, dict) else {}
        if metadata.get("measurement_id") != measurement.id:
            return None
        return {
            "id": measurement.id,
            "box_id": measurement.box_id,
            "box_code": measurement.box.global_code,
            "measured_on": measurement.measured_on.isoformat(),
            "polyp_count": measurement.polyp_count,
            "ephyrae_count": measurement.ephyrae_count,
            "salinity_psu": (
                str(measurement.salinity_psu) if measurement.salinity_psu is not None else ""
            ),
            "notes": measurement.notes or "",
        }

    def _enriched_metadata(self, log, measurement):
        metadata = dict(log.metadata or {})
        if "valeurs" in metadata:
            return metadata

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
            .select_related("box")
            .order_by("-created_at")
            .first()
        )
