"""Shared presentation of current account identity, without technical usernames."""


def serialize_user_identity(user):
    """Return structured identity for a live user, or None when absent."""
    if user is None:
        return None
    return {
        field: (getattr(user, field, "") or "").strip()
        for field in ("first_name", "last_name", "email")
    }


def readable_user_identity_label(user):
    """Return a current name with an uppercase surname, falling back to email."""
    identity = serialize_user_identity(user)
    if identity is None:
        return None
    name = " ".join(
        part for part in (identity["first_name"], identity["last_name"].upper()) if part
    )
    return name or identity["email"] or None
