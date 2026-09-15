"""Shared querying and presentation helpers for audited business actions."""

from django.db.models.functions import Coalesce

from .models import AuditLog


IMPACTFUL_AUDIT_ACTIONS = (
    AuditLog.Action.CREATION,
    AuditLog.Action.UPDATE,
    AuditLog.Action.ARCHIVE,
    AuditLog.Action.ENTRY,
    AuditLog.Action.SUBCULTURE,
    AuditLog.Action.TRANSFER,
    AuditLog.Action.IMPORT,
    AuditLog.Action.EXPORT,
)

_PERSONAL_DETAIL_FIELDS_BY_OBJECT_TYPE = {
    "box": {
        "date",
        "polypes",
        "ephyrules",
        "strobiles",
        "salinite_psu",
        "statut_culture",
        "a_verifier",
        "note",
        "code_global",
        "numero_boite",
        "souche",
        "espece",
        "emplacement",
        "date_entree",
        "volume_litres",
        "ancienne_zone",
        "nouvelle_zone",
        "date_deplacement",
    },
    "probe": {"code", "emplacement", "type", "position"},
    "thermal_zone": {
        "nom",
        "type",
        "temperature_consigne",
        "capacite",
        "active",
        "date",
        "temperature_c",
    },
}


def impactful_audit_logs(*, organization_id, actor=None):
    """Return supported business actions within exactly one organization."""
    queryset = AuditLog.objects.filter(
        organization_id=organization_id,
        action__in=IMPACTFUL_AUDIT_ACTIONS,
    )
    if actor is not None:
        queryset = queryset.filter(user=actor)
    return queryset


def parse_audit_pagination(query_params, *, default_limit=40, max_limit=100):
    """Parse the existing limit/offset contract with bounded safe defaults."""
    try:
        limit = int(query_params.get("limit", default_limit))
    except (TypeError, ValueError):
        limit = default_limit
    limit = max(1, min(limit, max_limit))

    try:
        offset = int(query_params.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    offset = max(0, offset)
    return limit, offset


def paginate_audit_logs(queryset, *, limit, offset, legacy_effective_order=False):
    """Return one stable offset page and whether another row exists."""
    if legacy_effective_order:
        queryset = queryset.annotate(effective_at=Coalesce("edited_at", "created_at"))
        ordering = ("-effective_at", "-id")
    else:
        ordering = ("-created_at", "-id")

    logs = list(queryset.order_by(*ordering)[offset : offset + limit + 1])
    has_more = len(logs) > limit
    return logs[:limit], has_more


def readable_account_label(user):
    """Return a human-readable account label, never an internal username.

    The product shows "First name LAST NAME", falls back to the email, and
    never exposes the opaque ``internal_<uuid>`` username used as a technical
    login. When neither a name nor an email is available, return ``None`` so
    the caller can omit the value instead of guessing an identity.
    """
    if user is None:
        return None

    full_name = " ".join(
        part for part in [user.first_name, user.last_name] if part
    ).strip()
    if full_name:
        return full_name

    email = (user.email or "").strip()
    return email or None


def serialize_personal_audit_log(log):
    """Serialize one action without exposing raw or administration-only metadata."""
    return {
        "id": log.id,
        "created_at": log.created_at,
        "action": log.action,
        "action_label": log.get_action_display(),
        "resource": {
            "type": log.object_type,
            "identifier": log.object_id,
            "label": _personal_resource_label(log),
        },
        "description": log.description,
        "details": _personal_audit_details(log),
    }


def _personal_resource_label(log):
    """Give account resources a readable target instead of an internal username."""
    if log.object_type != "account":
        return log.object_id

    metadata = log.metadata if isinstance(log.metadata, dict) else {}
    values = metadata.get("valeurs")
    if not isinstance(values, dict):
        return None

    name = values.get("nom")
    if isinstance(name, str) and name.strip() and not name.strip().startswith("internal_"):
        return name.strip()

    email = values.get("email")
    if isinstance(email, str) and email.strip():
        return email.strip()
    return None


def _personal_audit_details(log):
    metadata = log.metadata if isinstance(log.metadata, dict) else {}
    allowed_fields = _PERSONAL_DETAIL_FIELDS_BY_OBJECT_TYPE.get(log.object_type, set())
    details = {}

    values = _allowlisted_values(metadata.get("valeurs"), allowed_fields)
    if values:
        details["values"] = values

    changes = _allowlisted_changes(metadata.get("modifications"), allowed_fields)
    if changes:
        details["changes"] = changes

    return details


def _allowlisted_values(candidate, allowed_fields):
    if not isinstance(candidate, dict):
        return {}
    return {
        key: value
        for key, value in candidate.items()
        if key in allowed_fields and _is_safe_scalar(value)
    }


def _allowlisted_changes(candidate, allowed_fields):
    if not isinstance(candidate, dict):
        return {}

    changes = {}
    for key, value in candidate.items():
        if key not in allowed_fields or not isinstance(value, dict):
            continue
        before = value.get("avant")
        after = value.get("apres")
        if _is_safe_scalar(before) and _is_safe_scalar(after):
            changes[key] = {"before": before, "after": after}
    return changes


def _is_safe_scalar(value):
    return value is None or isinstance(value, (bool, int, float, str))
