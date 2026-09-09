from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.crypto import constant_time_compare
from django.utils.http import base36_to_int


INVITATION_TOKEN_PREFIX = "invitation-"
INVITATION_TOKEN_TIMEOUT = 24 * 60 * 60


class InvitationTokenGenerator(PasswordResetTokenGenerator):
    """Issue one-time password setup tokens with an invitation-only lifetime."""

    key_salt = "apps.accounts.tokens.InvitationTokenGenerator"

    def check_token(self, user, token):
        if not (user and token):
            return False

        try:
            timestamp_base36, _hash = token.split("-")
            timestamp = base36_to_int(timestamp_base36)
        except (TypeError, ValueError):
            return False

        for secret in [self.secret, *self.secret_fallbacks]:
            expected_token = self._make_token_with_timestamp(user, timestamp, secret)
            if constant_time_compare(expected_token, token):
                break
        else:
            return False

        age_seconds = self._num_seconds(self._now()) - timestamp
        return age_seconds <= INVITATION_TOKEN_TIMEOUT


invitation_token_generator = InvitationTokenGenerator()
