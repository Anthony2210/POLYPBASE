import logging
import re
import uuid

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Count
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
    get_required_active_organization_from_request,
    user_can_administer_organization,
    user_can_manage_admin_memberships,
    user_can_relinquish_responsable,
    user_is_org_admin,
)
from apps.audit.models import AuditLog
from apps.audit.services import (
    impactful_audit_logs,
    paginate_audit_logs,
    parse_audit_pagination,
    serialize_personal_audit_log,
)
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
from .tokens import (
    INVITATION_TOKEN_PREFIX,
    invitation_token_generator,
)


logger = logging.getLogger(__name__)

LOGIN_IP_SCOPE = "login_ip"
LOGIN_ACCOUNT_SCOPE = "login_account"
PASSWORD_RESET_IP_SCOPE = "password_reset_ip"
PASSWORD_RESET_ACCOUNT_SCOPE = "password_reset_account"


EMAIL_LOCAL_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")
EMAIL_DOMAIN_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)


def _ascii_lower(value):
    return str(value or "").translate(
        str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
    )


def _canonical_email(value):
    candidate = str(value or "").strip()
    if candidate.count("@") != 1:
        return None
    local, domain = candidate.split("@")
    local = _ascii_lower(local)
    if (
        not local
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or not local.isascii()
        or not EMAIL_LOCAL_PATTERN.fullmatch(local)
    ):
        return None
    try:
        ascii_domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if not EMAIL_DOMAIN_PATTERN.fullmatch(ascii_domain):
        return None
    canonical = f"{local}@{ascii_domain}"
    return canonical if len(canonical) <= 254 else None


def normalize_email(value):
    """Normalize an email identity, preserving invalid input for safe lookup."""
    candidate = str(value or "").strip()
    canonical = _canonical_email(candidate)
    return canonical if canonical is not None else _ascii_lower(candidate)


def validate_email_identity(value):
    """Validate and canonicalize the Polypbase email identity domain."""
    email = _canonical_email(value)
    if email is None:
        raise ValidationError("Enter a valid email address.")
    return email


class AccountPermissionDenied(PermissionDenied):
    """Return a stable code so the frontend can translate expected denials."""

    def __init__(self, detail, *, error_code):
        super().__init__({"detail": detail, "code": error_code})


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


def _password_setup_link(user, *, invitation):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    if invitation:
        token = f"{INVITATION_TOKEN_PREFIX}{invitation_token_generator.make_token(user)}"
    else:
        token = default_token_generator.make_token(user)
    return f"{settings.PUBLIC_BASE_URL}/reset-password/{uid}/{token}"


def _send_password_email(user, *, invitation):
    link = _password_setup_link(user, invitation=invitation)
    if invitation:
        subject = "Invitation Polypbase"
        message = (
            "Hello,\n\n"
            "A Polypbase account has been created for you.\n\n"
            "Use this one-time link to choose your password:\n"
            f"{link}\n\n"
            "This link is valid for 24 hours and can only be used once.\n"
        )
    else:
        subject = "Réinitialisation de votre mot de passe Polypbase"
        message = (
            "Bonjour,\n\n"
            "Vous avez demandé la réinitialisation de votre mot de passe Polypbase.\n"
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
        email = normalize_email(request.data.get("email"))
        password = str(request.data.get("password", ""))
        client_ip = get_client_ip(request)
        user_model = get_user_model()
        matching_accounts = (
            list(user_model.objects.filter(email=email, is_active=True).only("pk")[:2])
            if email
            else []
        )
        if len(matching_accounts) > 1:
            logger.error("Ambiguous email identity encountered during login.")
        account = matching_accounts[0] if len(matching_accounts) == 1 else None

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

        user = authenticate(
            request,
            username=account.get_username() if account is not None else "",
            password=password,
        )

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
        preference, _created = UserPreference.objects.get_or_create(user=user)
        request.session["interface_language"] = preference.interface_language
        translation.activate(preference.interface_language)
        request.LANGUAGE_CODE = preference.interface_language
        return Response({"interface_language": preference.interface_language})


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
        email = normalize_email(request.data.get("email"))
        client_ip = get_client_ip(request)
        retry_seconds = consume_event(
            PASSWORD_RESET_IP_SCOPE,
            client_ip,
            password_reset_ip_policy(),
        )
        if retry_seconds:
            return _too_many_attempts(retry_seconds)

        if email:
            matching_users = list(
                get_user_model().objects.filter(email=email, is_active=True)[:2]
            )
            if len(matching_users) > 1:
                logger.error("Ambiguous email identity encountered during password reset.")
            user = matching_users[0] if len(matching_users) == 1 else None
            if user is not None:
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
        if user is None or not self._check_token(user, token):
            return Response(
                {"detail": "Ce lien est invalide ou a expire. Demandez-en un nouveau."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            validate_password(password, user)
        except DjangoValidationError as error:
            return Response({"password": list(error.messages)}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
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

    def _check_token(self, user, token):
        if token.startswith(INVITATION_TOKEN_PREFIX):
            invitation_token = token.removeprefix(INVITATION_TOKEN_PREFIX)
            return invitation_token_generator.check_token(user, invitation_token)
        return default_token_generator.check_token(user, token)

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
                        "is_responsable": membership.is_responsable,
                    }
                    for membership in memberships
                ],
                "available_languages": available_interface_languages(),
            }
        )
        return serializer.data


def _role_choices(*, can_manage_admin_memberships):
    """Return roles the current actor may assign to a membership."""
    choices = OrganizationMembership.Role.choices
    if not can_manage_admin_memberships:
        choices = [
            choice
            for choice in choices
            if choice[0] != OrganizationMembership.Role.ADMIN
        ]
    return [{"value": value, "label": str(label)} for value, label in choices]


def _account_management_capabilities(user, organization):
    can_manage_admin_memberships = user_can_manage_admin_memberships(
        user,
        organization,
    )
    return {
        "roles": _role_choices(
            can_manage_admin_memberships=can_manage_admin_memberships,
        ),
        "can_manage_admin_memberships": can_manage_admin_memberships,
        "can_relinquish_responsable": user_can_relinquish_responsable(
            user,
            organization,
        ),
    }


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
        "full_name": full_name,
        "email": user.email,
        "organization": {
            "id": membership.organization.id,
            "name": membership.organization.name,
        },
        "role": membership.role,
        "role_label": membership.get_role_display(),
        "is_responsable": membership.is_responsable,
        "is_active": membership.is_active,
        "last_login": user.last_login.isoformat() if user.last_login else None,
        "is_self": user.id == current_user.id,
    }


def _member_audit_values(membership):
    """Keep account-management audit entries readable for administrators."""
    user = membership.user
    full_name = " ".join(
        part for part in [user.first_name, user.last_name] if part
    ).strip()
    return {
        "nom": full_name or user.get_username(),
        "email": user.email,
        "structure": membership.organization.name,
        "role": membership.role,
        "is_responsable": membership.is_responsable,
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
            .order_by("organization__name", "user__last_name", "user__first_name", "user__email")
        )
        organizations = get_admin_organizations(request.user).filter(id__in=organization_ids).order_by("name")
        active_organization = organizations.get(pk=organization_ids[0])

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
                **_account_management_capabilities(
                    request.user,
                    active_organization,
                ),
            }
        )

    def post(self, request):
        if not user_is_org_admin(request.user):
            raise PermissionDenied("This account cannot manage members.")

        admin_org_ids = get_active_admin_organization_ids(request)
        if not admin_org_ids:
            raise PermissionDenied("This account cannot manage members for the selected organization.")
        data = request.data
        if "is_responsable" in data:
            raise ValidationError(
                {"is_responsable": "Responsable status cannot be assigned by invitation."}
            )

        organization = self._get_managed_organization(data.get("organization_id"), admin_org_ids)
        capabilities = _account_management_capabilities(request.user, organization)
        role = self._validate_role(
            data.get("role"),
            allowed_roles={choice["value"] for choice in capabilities["roles"]},
        )

        email = normalize_email(data.get("email"))
        if not email:
            raise ValidationError({"email": "Une adresse email est requise."})
        try:
            email = validate_email_identity(email)
        except ValidationError as error:
            raise ValidationError({"email": error.detail})
        if data.get("password") not in (None, ""):
            raise ValidationError(
                {"password": "Un mot de passe ne peut pas être défini par un administrateur."}
            )
        if not settings.EMAIL_DELIVERY_ENABLED:
            raise EmailDeliveryUnavailable()

        user_model = get_user_model()
        with transaction.atomic():
            organization = self._lock_invitation_organization(
                request.user,
                organization,
                role=role,
            )
            self._validate_new_user_identity(user_model, email)
            user = self._create_user(user_model, email, data)

            membership = OrganizationMembership.objects.create(
                user=user,
                organization=organization,
                role=role,
                is_active=True,
            )

            UserPreference.objects.get_or_create(user=user)
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
            self._send_account_invitation(user)

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

    def _lock_invitation_organization(self, actor, organization, *, role):
        try:
            locked_organization = Organization.objects.select_for_update().get(
                pk=organization.pk,
                is_active=True,
            )
        except Organization.DoesNotExist as error:
            raise AccountPermissionDenied(
                "This account cannot manage members for the selected organization.",
                error_code="membership_admin_required",
            ) from error
        if not user_can_administer_organization(actor, locked_organization):
            raise AccountPermissionDenied(
                "This account cannot manage members for the selected organization.",
                error_code="membership_admin_required",
            )
        if (
            role == OrganizationMembership.Role.ADMIN
            and not user_can_manage_admin_memberships(actor, locked_organization)
        ):
            raise AccountPermissionDenied(
                "Only an institution Responsable can assign the Admin role.",
                error_code="responsable_required",
            )
        return locked_organization

    def _validate_new_user_identity(self, user_model, email):
        if user_model.objects.filter(email=email).exists():
            raise ValidationError({"email": "Cette adresse email est déjà utilisée."})

    def _validate_role(self, role, *, allowed_roles):
        stored_roles = {
            value for value, _label in OrganizationMembership.Role.choices
        }
        if role not in stored_roles:
            raise ValidationError({"role": "Rôle invalide."})
        if role not in allowed_roles:
            raise AccountPermissionDenied(
                "Only an institution Responsable can assign the Admin role.",
                error_code="responsable_required",
            )
        return role

    def _create_user(self, user_model, email, data):
        for _attempt in range(5):
            user = user_model(
                username=f"internal_{uuid.uuid4().hex}",
                email=email,
                first_name=_format_first_name(data.get("first_name")),
                last_name=_format_last_name(data.get("last_name")),
            )
            user.set_unusable_password()
            try:
                with transaction.atomic():
                    user.save(force_insert=True)
            except IntegrityError:
                if user_model.objects.filter(email=email).exists():
                    raise ValidationError({"email": "Cette adresse email est déjà utilisée."})
                continue
            return user
        raise ValidationError({"detail": "Unable to create the account. Please try again."})

    def _send_account_invitation(self, user):
        try:
            sent_count = _send_password_email(user, invitation=True)
        except Exception as error:
            raise EmailDeliveryUnavailable() from error
        if sent_count != 1:
            raise EmailDeliveryUnavailable()


@method_decorator(ensure_csrf_cookie, name="dispatch")
class OrganizationMembershipDetailAPIView(APIView):
    """Update a membership after validating its complete final state."""

    @transaction.atomic
    def patch(self, request, pk):
        if not user_is_org_admin(request.user):
            raise PermissionDenied("This account cannot manage members.")

        admin_org_ids = get_active_admin_organization_ids(request)
        if not admin_org_ids:
            raise PermissionDenied("This account cannot manage members for the selected organization.")
        try:
            initial_membership = OrganizationMembership.objects.only(
                "organization_id"
            ).get(pk=pk)
        except OrganizationMembership.DoesNotExist:
            raise ValidationError({"detail": "Membre introuvable."})

        if initial_membership.organization_id not in admin_org_ids:
            raise PermissionDenied("You cannot manage members for this organization.")

        organization = Organization.objects.select_for_update().get(
            pk=initial_membership.organization_id
        )
        actor = get_user_model().objects.get(pk=request.user.pk)
        actor_membership = self._get_fresh_actor_membership(actor, organization)
        membership = OrganizationMembership.objects.select_related(
            "organization", "user"
        ).get(pk=pk, organization=organization)

        if "is_responsable" in request.data:
            raise ValidationError(
                {"is_responsable": "Responsable status requires a controlled action."}
            )
        if "status" in request.data:
            raise ValidationError({"status": "Statut invalide."})

        final_role = membership.role
        if "role" in request.data:
            final_role = self._validate_stored_role(request.data.get("role"))

        final_is_active = membership.is_active
        if "is_active" in request.data:
            final_is_active = request.data.get("is_active")
            if not isinstance(final_is_active, bool):
                raise ValidationError({"is_active": "Statut invalide."})

        self._validate_final_state(
            actor=actor,
            actor_membership=actor_membership,
            membership=membership,
            final_role=final_role,
            final_is_active=final_is_active,
        )

        before_values = _member_audit_values(membership)
        updated_fields = []
        if final_role != membership.role:
            membership.role = final_role
            updated_fields.append("role")
        if final_is_active != membership.is_active:
            membership.is_active = final_is_active
            updated_fields.append("is_active")

        if updated_fields:
            membership.save(update_fields=updated_fields)
            after_values = _member_audit_values(membership)
            AuditLog.objects.create(
                organization=membership.organization,
                user=actor,
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

        return Response(_member_data(membership, current_user=actor))

    def _get_fresh_actor_membership(self, actor, organization):
        if not organization.is_active:
            raise AccountPermissionDenied(
                "This account cannot manage members for the selected organization.",
                error_code="membership_admin_required",
            )
        if actor.is_superuser:
            return None
        try:
            return OrganizationMembership.objects.get(
                user=actor,
                organization=organization,
                is_active=True,
                role=OrganizationMembership.Role.ADMIN,
            )
        except OrganizationMembership.DoesNotExist as error:
            raise AccountPermissionDenied(
                "This account cannot manage members for the selected organization.",
                error_code="membership_admin_required",
            ) from error

    def _validate_final_state(
        self,
        *,
        actor,
        actor_membership,
        membership,
        final_role,
        final_is_active,
    ):
        role_changes = final_role != membership.role
        activation_changes = final_is_active != membership.is_active
        if not role_changes and not activation_changes:
            return

        if membership.is_responsable:
            raise AccountPermissionDenied(
                "Responsable status can only be changed through a controlled action.",
                error_code="responsable_membership_protected",
            )

        actor_can_manage_admins = (
            actor.is_superuser or actor_membership.is_responsable
        )
        target_is_self = membership.user_id == actor.id
        original_is_admin = membership.role == OrganizationMembership.Role.ADMIN
        final_is_admin = final_role == OrganizationMembership.Role.ADMIN

        if not actor_can_manage_admins:
            if target_is_self:
                if activation_changes and not final_is_active:
                    raise PermissionDenied(
                        "Vous ne pouvez pas désactiver votre propre accès."
                    )
                if role_changes and not original_is_admin:
                    raise PermissionDenied(
                        "You cannot change your own membership role."
                    )
            elif original_is_admin and (role_changes or activation_changes):
                if activation_changes and not role_changes:
                    raise PermissionDenied(
                        "Un administrateur ne peut pas désactiver un autre administrateur."
                    )
                raise AccountPermissionDenied(
                    "An ordinary Admin cannot modify another Admin membership.",
                    error_code="responsable_required",
                )

            if not original_is_admin and final_is_admin:
                raise AccountPermissionDenied(
                    "Only an institution Responsable can assign the Admin role.",
                    error_code="responsable_required",
                )

        removes_active_admin = (
            membership.is_active
            and original_is_admin
            and not (final_is_active and final_is_admin)
        )
        if removes_active_admin and not self._other_active_admin_exists(membership):
            if role_changes:
                raise PermissionDenied(
                    "Le dernier administrateur actif de cette structure ne peut pas être rétrogradé."
                )
            raise PermissionDenied(
                "Le dernier administrateur actif de cette structure ne peut pas être désactivé."
            )

    def _other_active_admin_exists(self, membership):
        return OrganizationMembership.objects.filter(
            organization=membership.organization,
            is_active=True,
            role=OrganizationMembership.Role.ADMIN,
        ).exclude(pk=membership.pk).exists()

    def _validate_stored_role(self, role):
        valid_roles = {value for value, _label in OrganizationMembership.Role.choices}
        if role not in valid_roles:
            raise ValidationError({"role": "Rôle invalide."})
        return role


@method_decorator(ensure_csrf_cookie, name="dispatch")
class InstitutionResponsableRelinquishAPIView(APIView):
    """Let an active Responsable relinquish only their own status."""

    def post(self, request):
        initial_organization = get_required_active_organization_from_request(request)

        with transaction.atomic():
            organization = Organization.objects.select_for_update().get(
                pk=initial_organization.pk
            )
            actor = get_user_model().objects.get(pk=request.user.pk)
            membership = self._get_active_responsable_membership(actor, organization)
            self._ensure_another_active_responsable(membership)

            before_values = _member_audit_values(membership)
            membership.is_responsable = False
            membership.save(update_fields=["is_responsable"])
            after_values = _member_audit_values(membership)
            AuditLog.objects.create(
                organization=organization,
                user=actor,
                action=AuditLog.Action.UPDATE,
                object_type="account",
                object_id=membership.user.get_username(),
                description="Institution Responsable relinquished",
                metadata={
                    "user_id": actor.id,
                    "membership_id": membership.id,
                    "valeurs": after_values,
                    "modifications": _changed_values(before_values, after_values),
                },
            )

            membership = OrganizationMembership.objects.select_related(
                "organization",
                "user",
            ).get(pk=membership.pk)
            return Response(
                {
                    "member": _member_data(membership, current_user=actor),
                    **_account_management_capabilities(actor, organization),
                }
            )

    def _get_active_responsable_membership(self, actor, organization):
        if actor.is_superuser or not organization.is_active:
            raise AccountPermissionDenied(
                "This account is not an active institution Responsable.",
                error_code="active_responsable_required",
            )
        try:
            return OrganizationMembership.objects.select_related(
                "organization",
                "user",
            ).get(
                user=actor,
                organization=organization,
                is_active=True,
                role=OrganizationMembership.Role.ADMIN,
                is_responsable=True,
            )
        except OrganizationMembership.DoesNotExist as error:
            raise AccountPermissionDenied(
                "This account is not an active institution Responsable.",
                error_code="active_responsable_required",
            ) from error

    def _ensure_another_active_responsable(self, membership):
        if not OrganizationMembership.objects.filter(
            organization=membership.organization,
            is_active=True,
            role=OrganizationMembership.Role.ADMIN,
            is_responsable=True,
        ).exclude(pk=membership.pk).exists():
            raise AccountPermissionDenied(
                "The last active institution Responsable cannot relinquish status.",
                error_code="last_active_responsable",
            )


class PersonalAuditLogListAPIView(APIView):
    """Return the current user's supported actions in the active organization."""

    def get(self, request):
        organization = get_required_active_organization_from_request(request)
        limit, offset = parse_audit_pagination(request.query_params)
        logs_query = impactful_audit_logs(
            organization_id=organization.id,
            actor=request.user,
        )
        logs, has_more = paginate_audit_logs(
            logs_query,
            limit=limit,
            offset=offset,
        )
        return Response(
            {
                "results": [serialize_personal_audit_log(log) for log in logs],
                "limit": limit,
                "offset": offset,
                "has_more": has_more,
                "next_offset": offset + len(logs) if has_more else None,
            }
        )


@method_decorator(ensure_csrf_cookie, name="dispatch")
class AdminAuditLogListAPIView(APIView):
    """Return recent audit trail entries for organizations administered by the user."""

    def get(self, request):
        if not user_is_org_admin(request.user):
            raise PermissionDenied("This account cannot view the audit log.")

        limit, offset = parse_audit_pagination(request.query_params)

        organization_ids = get_active_admin_organization_ids(request)
        if not organization_ids:
            raise PermissionDenied("This account cannot view the audit log for the selected organization.")
        logs_query = impactful_audit_logs(organization_id=organization_ids[0])

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

        logs, has_more = paginate_audit_logs(
            logs_query.select_related("organization", "user", "edited_by"),
            limit=limit,
            offset=offset,
            # edited_at remains part of the Administration compatibility contract
            # for rows produced by the legacy mutable measurement-audit behavior.
            legacy_effective_order=True,
        )
        measurement_ids = []
        for log in logs:
            if not isinstance(log.metadata, dict):
                continue
            measurement_id = log.metadata.get("measurement_id")
            if type(measurement_id) is int and measurement_id > 0:
                measurement_ids.append(measurement_id)
        measurements_by_id = {
            measurement.id: measurement
            # select_related: the edit link reads measurement.box.global_code.
            for measurement in BiologicalMeasurement.objects.filter(
                id__in=measurement_ids,
                box__organization_id__in=organization_ids,
            ).select_related("box")
        }

        payload = {
            "results": [
                self._serialize_log(log, measurements_by_id, organization_ids)
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

    def _serialize_log(self, log, measurements_by_id, organization_ids):
        # Resolved once and reused: the fallback lookup hits the database, so
        # doing it separately for the metadata and for the edit link would
        # double the queries.
        measurement = self._resolve_measurement(
            log, measurements_by_id, organization_ids
        )
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
            # Legacy mutable rows may still have a later effective_at. New
            # append-only events use their own created_at for both values.
            "effective_at": getattr(log, "effective_at", None) or log.created_at,
            "edited_at": log.edited_at,
            "edited_by": log.edited_by.get_username() if log.edited_by else None,
            "metadata": self._enriched_metadata(log, measurement),
            # Lets the history open the measurement itself for correction,
            # instead of sending the user off to the box sheet.
            "editable_measurement": self._editable_measurement(log, measurement),
        }

    def _resolve_measurement(self, log, measurements_by_id, organization_ids):
        metadata = log.metadata if isinstance(log.metadata, dict) else {}
        if "measurement_id" in metadata:
            measurement_id = metadata["measurement_id"]
            if type(measurement_id) is not int or measurement_id <= 0:
                return None
            return measurements_by_id.get(measurement_id)
        # Entries created before measurement_id was recorded use the legacy lookup.
        return self._find_measurement_from_log(log, organization_ids)

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
        if measurement is None:
            # Do not expose an explicit foreign, deleted, or otherwise invalid
            # measurement reference from the institution-scoped response.
            metadata.pop("measurement_id", None)
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

    def _find_measurement_from_log(self, log, organization_ids):
        if log.object_type != "box" or not log.object_id:
            return None

        match = re.search(r"(\d{4}-\d{2}-\d{2})", log.description or "")
        if not match:
            return None

        return (
            BiologicalMeasurement.objects.filter(
                box__global_code=log.object_id,
                box__organization_id__in=organization_ids,
                measured_on=match.group(1),
            )
            .select_related("box")
            .order_by("-created_at")
            .first()
        )
