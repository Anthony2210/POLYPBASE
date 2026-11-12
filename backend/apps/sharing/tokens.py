"""Single-purpose invitation secret of a Box reader share.

The secret is random, emailed once and never stored: the database keeps only
a purpose-bound SHA-256 digest, which is enough for a 256-bit random value.
Possessing the secret never grants a read; it only lets the invited account
accept the invitation.
"""

import hashlib
import re
import secrets

_PURPOSE = "polypbase.box-reader-share.invitation.v1:"
# Exact shape of `secrets.token_urlsafe(32)`; bounds parsing of hostile input.
_SECRET_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")


def _digest(secret):
    return hashlib.sha256((_PURPOSE + secret).encode("ascii")).hexdigest()


def new_invitation_secret():
    """Return `(secret, digest)`; only the digest may be persisted."""
    secret = secrets.token_urlsafe(32)
    return secret, _digest(secret)


def invitation_digest(token):
    """Return the lookup digest of a presented token, or None when malformed."""
    if not isinstance(token, str) or not _SECRET_PATTERN.fullmatch(token):
        return None
    return _digest(token)
