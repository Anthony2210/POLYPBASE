from django.conf import settings
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.http import base36_to_int, int_to_base36


INVITATION_TOKEN_PREFIX = "invitation-"
INVITATION_TOKEN_TIMEOUT = 24 * 60 * 60
# Base36 of any realistic primary key stays well under this length; it only
# bounds parsing of hostile input.
_MAX_ID_LENGTH = 13


def invitation_is_expired(invitation, now):
    """Single expiry rule: valid up to and including `expires_at`.

    A missing invitation, or one without a recorded issue time (legacy), is expired.
    """
    return (
        invitation is None
        or invitation.expires_at is None
        or now > invitation.expires_at
    )


class InvitationTokenGenerator:
    """Derive one-time password setup tokens from the persisted invitation state.

    Token shape: `<invitation id>-<generation>-<digest>` (base36 numbers). The
    digest covers the exact invitation, membership and organization, the user's
    password hash and last login (so it stops working once the password is set),
    and the generation and issue time (so a resend supersedes every earlier link).
    Expiry always comes from `invitation_is_expired`, the single authoritative rule.
    """

    key_salt = "apps.accounts.tokens.InvitationTokenGenerator.v2"

    def make_token(self, invitation):
        return self._token(invitation, settings.SECRET_KEY)

    def parse_invitation_id(self, token):
        """Return the invitation id a token claims, or None for any malformed token."""
        parts = str(token or "").split("-")
        if len(parts) != 3 or not parts[0] or len(parts[0]) > _MAX_ID_LENGTH:
            return None
        try:
            invitation_id = base36_to_int(parts[0])
        except ValueError:
            return None
        return invitation_id if invitation_id > 0 else None

    def check_token(self, invitation, token, *, now):
        if (
            not token
            or invitation is None
            or invitation.issued_at is None
            or invitation_is_expired(invitation, now)
        ):
            return False
        secrets = [settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS]
        return any(
            constant_time_compare(self._token(invitation, secret), token)
            for secret in secrets
        )

    def _token(self, invitation, secret):
        membership = invitation.membership
        user = membership.user
        last_login = "" if user.last_login is None else int(user.last_login.timestamp())
        value = "|".join(
            str(part)
            for part in (
                invitation.pk,
                membership.pk,
                membership.organization_id,
                user.pk,
                user.password,
                last_login,
                invitation.generation,
                int(invitation.issued_at.timestamp()),
            )
        )
        digest = salted_hmac(
            self.key_salt,
            value,
            secret=secret,
            algorithm="sha256",
        ).hexdigest()[::2]
        return (
            f"{int_to_base36(invitation.pk)}-{int_to_base36(invitation.generation)}-{digest}"
        )


invitation_token_generator = InvitationTokenGenerator()
