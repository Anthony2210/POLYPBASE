"""External read-only sharing of one source Box.

Four separate authorizations, never derived from one another:

A. Managing shares: a current active Admin membership of the active source
   institution, re-checked under the institution lock in every mutation.
B. Accepting an invitation: an authenticated active account whose email is
   the invited mailbox and which presents the secret emailed to that mailbox.
C. Reading: the account bound to the grant by acceptance, re-checked at every
   read against revocation, expiry and the current policy revision.
D. Scientific projection: the canonical snapshot contract, called only after
   A or C succeeded, with identifiers taken from the stored share.

Nothing here creates a membership, selects an institution context for the
recipient, or writes to a Box, measurement, movement, strain or lineage.

Lock order (extends the accounts convention, never taken the other way):

    Organization -> BoxReaderShare

Acceptance follows the same order. It must: binding the account and writing
its audit row take key-share locks on the institution row through foreign
keys, so locking the share first would deadlock against a revocation.
"""

from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
import logging
import smtplib

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, OperationalError, connection, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.accounts.api_views import normalize_email, validate_email_identity
from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box
from apps.cultures.scientific_projection import (
    project_box_snapshot,
    project_verified_source_box_snapshot,
)
from apps.measurements.scientific_snapshot import (
    SUPPORTED_VERSIONS,
    validate_snapshot,
    SnapshotError,
    canonical_json,
    select_disclosure,
)
from apps.organizations.models import Organization

from .models import BoxReaderShare, BoxReaderShareCapture
from .tokens import invitation_digest, new_invitation_secret

logger = logging.getLogger(__name__)

AUDIT_OBJECT_TYPE = "box_reader_share"
# The recipient must always be able to identify the institution owning the
# shared Box. A source Admin has to select it explicitly; it is never added
# silently, and nothing else is mandatory.
MANDATORY_SELECTORS = frozenset({"source.institution_name"})

STATE_PENDING = "pending"
STATE_INVITATION_EXPIRED = "invitation_expired"
STATE_ACTIVE = "active"
STATE_EXPIRED = "expired"
STATE_REVOKED = "revoked"
STATE_UNAVAILABLE = "unavailable"


class SharingError(Exception):
    """Expected refusal with a stable code; `extra` never holds a secret."""

    def __init__(self, code, status, detail, **extra):
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail
        self.extra = extra


def sharing_now():
    """Current time for sharing decisions; one seam for tests."""
    return timezone.now()


# Policy -----------------------------------------------------------------------


def lifetime_policy():
    """Return configured `(invitation_max, access_max)` durations, or None.

    No lifetime has a product-approved default. Until both bounds are
    configured explicitly, nothing that starts or extends a validity period
    is available.
    """
    invitation_max = getattr(settings, "BOX_SHARING_INVITATION_MAX_LIFETIME_SECONDS", None)
    access_max = getattr(settings, "BOX_SHARING_ACCESS_MAX_LIFETIME_SECONDS", None)
    if not invitation_max or not access_max:
        return None
    return timedelta(seconds=invitation_max), timedelta(seconds=access_max)


def _require_lifetime_policy():
    policy = lifetime_policy()
    if policy is None:
        raise SharingError(
            "sharing_policy_not_configured", 503,
            "Box sharing lifetimes are not configured for this deployment.",
        )
    return policy


def _require_email_delivery():
    if not settings.EMAIL_DELIVERY_ENABLED:
        raise SharingError("email_delivery_unavailable", 503, "Email delivery is not available.")


def _validate_access_expiry(now, access_expires_at):
    _invitation_max, access_max = _require_lifetime_policy()
    if not now < access_expires_at <= now + access_max:
        raise SharingError(
            "access_expiry_out_of_bounds", 400,
            "The access end must be in the future and within the configured maximum.",
        )


def _validate_invitation_expiry(now, invitation_expires_at, access_expires_at):
    invitation_max, _access_max = _require_lifetime_policy()
    if not now < invitation_expires_at <= min(now + invitation_max, access_expires_at):
        raise SharingError(
            "invitation_expiry_out_of_bounds", 400,
            "The invitation end must be in the future, within the configured maximum "
            "and not after the access end.",
        )


def validate_selectors(raw):
    """Return the canonical selector list of an explicit, valid selection."""
    try:
        disclosure = select_disclosure(raw, required=MANDATORY_SELECTORS)
    except SnapshotError as error:
        raise _disclosure_error(error.diagnostics) from error
    return sorted(disclosure.selectors)


def _disclosure_error(diagnostics):
    return SharingError(
        "invalid_disclosure", 400, "The disclosure selection is not valid.",
        diagnostics=[{"code": item.code, "path": item.path} for item in diagnostics],
    )


def share_state(share, now):
    if share.revoked_at is not None:
        return STATE_REVOKED
    if now > share.access_expires_at:
        return STATE_EXPIRED
    if share.accepted_at is None:
        return STATE_INVITATION_EXPIRED if now > share.invitation_expires_at else STATE_PENDING
    return STATE_ACTIVE


# A. Source Admin authority ----------------------------------------------------


def _is_usable_account(user):
    return user is not None and getattr(user, "is_authenticated", False) and user.is_active


def _has_active_admin_membership(user, organization_id):
    # Deliberately no superuser shortcut: disclosing a Box outside its
    # institution belongs to that institution's own Admins only.
    return OrganizationMembership.objects.filter(
        user=user, organization_id=organization_id, is_active=True,
        role=OrganizationMembership.Role.ADMIN,
    ).exists()


def _admin_required():
    return SharingError(
        "sharing_admin_required", 403,
        "Only an active Admin of the source institution can manage its shares.",
    )


def require_source_admin(user, organization):
    """Non-locking check for read-only management endpoints."""
    if not _is_usable_account(user) or organization is None:
        raise _admin_required()
    source = Organization.objects.filter(pk=organization.pk, is_active=True).first()
    if source is None or not _has_active_admin_membership(user, source.pk):
        raise _admin_required()
    return source


def _locked_source(user, organization_id):
    """Lock the institution, then re-check authority on the locked state.

    Membership changes lock the institution first as well, so the Admin check
    cannot race with a concurrent demotion or deactivation.
    """
    if not _is_usable_account(user):
        raise _admin_required()
    source = Organization.objects.select_for_update().filter(pk=organization_id, is_active=True).first()
    if source is None or not _has_active_admin_membership(user, source.pk):
        raise _admin_required()
    return source


def _locked_share(source, share_id):
    share = (
        BoxReaderShare.objects.select_for_update(of=("self",))
        .select_related("box")
        .filter(pk=share_id, organization_id=source.pk)
        .first()
    )
    if share is None:
        raise _share_not_found()
    return share


def _share_not_found():
    return SharingError("share_not_found", 404, "This share does not exist.")


def frozen_capture_available():
    """Whether this deployment can honestly promise a consistent frozen capture."""
    if connection.vendor == "postgresql":
        return True
    return connection.vendor == "sqlite" and bool(getattr(settings, "BOX_SHARING_SQLITE_FROZEN_CAPTURE", False))


@contextmanager
def _mutation(*, consistent_capture):
    """Run one management mutation; optionally with a single-snapshot guarantee.

    A FROZEN capture reads the institution, Box, strain identity and
    measurements with several queries. They are mutually consistent only when
    all of them see one database snapshot. On PostgreSQL this requires owning
    the outermost transaction and running it REPEATABLE READ; a concurrent
    change to a row this transaction locks then fails it with a serialization
    error instead of mixing two states. Any backend without a proven guarantee
    (SQLite unless `BOX_SHARING_SQLITE_FROZEN_CAPTURE` is set by a verified
    environment) is refused rather than labelled frozen.
    """
    if not consistent_capture:
        with transaction.atomic():
            yield
        return
    if connection.vendor == "postgresql":
        if connection.in_atomic_block:
            raise SharingError(
                "frozen_capture_unavailable", 503,
                "A frozen capture must own its database transaction.",
            )
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                yield
        except OperationalError as error:
            if getattr(error.__cause__, "sqlstate", None) == "40001":
                raise SharingError(
                    "concurrent_source_change", 409,
                    "The source changed during the capture. Nothing was saved; retry.",
                ) from error
            raise
    elif connection.vendor == "sqlite":
        # No SQLite snapshot behaviour has been verified for production-like
        # deployments (journal mode, concurrent writers). It is accepted only
        # where a verified environment opted in explicitly: the isolated test
        # settings. A default deployment on SQLite refuses frozen captures.
        if not getattr(settings, "BOX_SHARING_SQLITE_FROZEN_CAPTURE", False):
            raise SharingError(
                "frozen_capture_unavailable", 503,
                "Frozen captures are not supported on this database backend.",
            )
        with transaction.atomic():
            yield
    else:
        raise SharingError(
            "frozen_capture_unavailable", 503,
            "Frozen captures are not supported on this database backend.",
        )


def _project_for_admin(actor, source, box_id, selectors):
    """Project with the ordinary institutional authorization of the Admin."""
    result = project_box_snapshot(
        actor=actor, source_organization=source, box_id=box_id,
        selectors=selectors, required_selectors=MANDATORY_SELECTORS,
    )
    if result.ok:
        return result.snapshot
    codes = {item.code for item in result.diagnostics}
    if codes == {"box_not_found"} or codes == {"box_reference_invalid"}:
        # Missing, foreign and merged Boxes are indistinguishable.
        raise SharingError("box_not_found", 404, "This Box does not exist.")
    if codes & {"unknown_selector", "empty_selection", "duplicate_selector", "selector_string_required",
                "selection_list_required", "observation_time_required", "required_selector_missing"}:
        raise _disclosure_error(result.diagnostics)
    raise SharingError(
        "box_not_projectable", 409,
        "The scientific data of this Box cannot be projected.",
        diagnostics=[{"code": item.code, "path": item.path} for item in result.diagnostics],
    )


def _store_capture(share, snapshot, actor, captured_at):
    content = canonical_json(snapshot)
    return BoxReaderShareCapture.objects.create(
        share=share,
        policy_revision=share.policy_revision,
        schema_version=snapshot["schema_version"],
        content=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        captured_at=captured_at,
        captured_by=actor,
    )


def _audit(action, share, user, description, **metadata):
    """Audit in the source institution. Never pass a secret, link or content."""
    AuditLog.objects.create(
        organization_id=share.organization_id,
        user=user,
        action=action,
        object_type=AUDIT_OBJECT_TYPE,
        object_id=str(share.public_id),
        description=description,
        metadata={
            "share_id": share.pk,
            "box_id": share.box_id,
            "mode": share.mode,
            "policy_revision": share.policy_revision,
            **metadata,
        },
    )


# Definite failures: the relay refused before accepting the message.
_DEFINITE_SMTP_FAILURES = (
    smtplib.SMTPConnectError,
    smtplib.SMTPHeloError,
    smtplib.SMTPNotSupportedError,
    smtplib.SMTPAuthenticationError,
    smtplib.SMTPSenderRefused,
    smtplib.SMTPRecipientsRefused,
)


def _invitation_message(share, source, secret):
    # The secret travels in the URL fragment, which browsers never send to a
    # server, so it stays out of access logs and referrers.
    link = f"{settings.PUBLIC_BASE_URL}/shared-boxes/accept#token={secret}"
    expires = share.invitation_expires_at.astimezone(timezone.get_default_timezone())
    return (
        "Bonjour,\n\n"
        f"{source.name} vous propose un accès en lecture seule aux données d'une boîte "
        "de culture dans Polypbase.\n\n"
        "Connectez-vous avec le compte Polypbase associé à cette adresse email, puis "
        "acceptez l'invitation avec ce lien à usage unique :\n"
        f"{link}\n\n"
        f"Ce lien est valable jusqu'au {expires:%Y-%m-%d %H:%M %Z}. Ouvrir le lien ne donne "
        "aucun accès tant que l'invitation n'est pas acceptée depuis votre compte.\n\n"
        "-----\n\n"
        "Hello,\n\n"
        f"{source.name} offers you read-only access to the data of one culture box "
        "in Polypbase.\n\n"
        "Sign in with the Polypbase account of this email address, then accept the "
        "invitation with this one-time link:\n"
        f"{link}\n\n"
        f"This link is valid until {expires:%Y-%m-%d %H:%M %Z}. Opening the link grants "
        "no access until the invitation is accepted from your account.\n"
    )


def _current_invitation_filter(share_pk, generation, secret):
    """Rows still carrying exactly this secret, never revoked or accepted."""
    return BoxReaderShare.objects.filter(
        pk=share_pk, invitation_generation=generation,
        invitation_token_hash=invitation_digest(secret),
        revoked_at__isnull=True, accepted_at__isnull=True,
    )


def deliver_invitation(share_pk, generation, secret):
    """Send one invitation after its transaction committed, then record the outcome.

    Called only once the mutation that stored the secret's digest is
    committed, and holds no lock and no transaction while SMTP runs, so a
    slow relay cannot block anything and a rollback can never leave a usable
    link in a mailbox. A transaction cannot make SMTP exactly-once: the
    outcome is recorded as one of

    - `sent`: the relay accepted the message (not that it was read);
    - `failed`: the relay definitely refused it (or accepted nothing);
    - `uncertain`: an error such as a timeout, where the message may or may
      not have left. A crash before this recording leaves `pending`, which
      means the same thing to the Admin.

    The remedy for anything but `sent` is an explicit reissue: a new secret
    replaces the old one, so a possible duplicate delivery of the old link
    is harmless. Nothing recoverable is stored. The status is only written
    for the invitation generation that was sent and only while it is still
    pending, so a concurrent revocation, acceptance or reissue is never
    overwritten.
    """
    share = (
        _current_invitation_filter(share_pk, generation, secret)
        .select_related("organization").first()
    )
    if share is None:
        return None  # Superseded, revoked or accepted meanwhile: send nothing.
    try:
        sent = send_mail(
            "Polypbase - Partage d'une boîte en lecture seule / Read-only box sharing",
            _invitation_message(share, share.organization, secret),
            settings.DEFAULT_FROM_EMAIL,
            [share.recipient_email],
            fail_silently=False,
        )
        status = (
            BoxReaderShare.DeliveryStatus.SENT if sent == 1 else BoxReaderShare.DeliveryStatus.FAILED
        )
    except _DEFINITE_SMTP_FAILURES as error:
        logger.warning("Box reader share %s: invitation refused (%s).", share_pk, type(error).__name__)
        status = BoxReaderShare.DeliveryStatus.FAILED
    except Exception as error:  # noqa: BLE001 - outcome unknown by definition
        logger.warning("Box reader share %s: invitation outcome unknown (%s).", share_pk, type(error).__name__)
        status = BoxReaderShare.DeliveryStatus.UNCERTAIN
    try:
        with transaction.atomic():
            # Common lock order, institution first: the audit row's foreign
            # key would otherwise lock it after the share row and deadlock
            # against a concurrent revocation. Held only for this short write.
            Organization.objects.select_for_update().filter(pk=share.organization_id).first()
            changed = _current_invitation_filter(share_pk, generation, secret).update(
                delivery_status=status, delivery_updated_at=sharing_now(),
            )
            if changed:
                _audit(
                    AuditLog.Action.UPDATE, share, None, "Box reader share invitation delivery recorded",
                    delivery_status=status, invitation_generation=generation,
                )
    except Exception:  # noqa: BLE001 - the message was already handled; stay `pending`
        logger.exception("Box reader share %s: delivery outcome could not be recorded.", share_pk)
    return status


def _open_share_exists(box_id, recipient_email):
    return BoxReaderShare.objects.filter(
        box_id=box_id, recipient_email=recipient_email, revoked_at__isnull=True,
    ).exists()


def _already_exists():
    return SharingError(
        "share_already_exists", 409,
        "A non-revoked share of this Box already exists for this recipient.",
    )


def preview_share(*, actor, organization, box_id, selectors):
    """Return exactly what a recipient would currently receive. Read-only."""
    source = require_source_admin(actor, organization)
    return _project_for_admin(actor, source, box_id, validate_selectors(selectors))


def create_share(*, actor, organization, box_id, recipient_email, mode, selectors,
                 invitation_expires_at, access_expires_at):
    """Create a recipient-specific invitation, then email its one-time secret.

    For FROZEN the disclosed content is captured in the same transaction as
    the authorization check, the policy and the audit row. The email is sent
    after that transaction committed (see `deliver_invitation`); a delivery
    problem never undoes the share and is reported in `delivery_status`.
    """
    if mode not in BoxReaderShare.Mode.values:
        raise SharingError("invalid_mode", 400, "Unknown sharing mode.")
    try:
        email = validate_email_identity(recipient_email)
    except DRFValidationError as error:
        raise SharingError("invalid_recipient_email", 400, "Enter a valid email address.") from error
    canonical_selectors = validate_selectors(selectors)
    _require_email_delivery()
    now = sharing_now()
    _validate_access_expiry(now, access_expires_at)
    _validate_invitation_expiry(now, invitation_expires_at, access_expires_at)

    try:
        with _mutation(consistent_capture=mode == BoxReaderShare.Mode.FROZEN):
            source = _locked_source(actor, organization.pk)
            # Scope, merge state and projectability are decided by the
            # canonical projection itself, for both modes.
            snapshot = _project_for_admin(actor, source, box_id, canonical_selectors)
            if _open_share_exists(box_id, email):
                raise _already_exists()
            secret, digest = new_invitation_secret()
            share = BoxReaderShare.objects.create(
                organization=source,
                box_id=box_id,
                recipient_email=email,
                mode=mode,
                schema_version=snapshot["schema_version"],
                selectors=canonical_selectors,
                invitation_token_hash=digest,
                invitation_issued_at=now,
                invitation_expires_at=invitation_expires_at,
                access_expires_at=access_expires_at,
                created_by=actor,
            )
            capture = None
            if mode == BoxReaderShare.Mode.FROZEN:
                capture = _store_capture(share, snapshot, actor, now)
            _audit(
                AuditLog.Action.CREATION, share, actor, "Box reader share created",
                recipient_email=email,
                selectors=canonical_selectors,
                invitation_expires_at=invitation_expires_at.isoformat(),
                access_expires_at=access_expires_at.isoformat(),
                **({"capture_sha256": capture.content_sha256} if capture else {}),
            )
    except IntegrityError:
        # Lost a creation race on the open-share uniqueness constraint.
        if _open_share_exists(box_id, email):
            raise _already_exists() from None
        raise
    # Committed and unlocked: only now can a usable link leave.
    deliver_invitation(share.pk, share.invitation_generation, secret)
    return BoxReaderShare.objects.get(pk=share.pk)


def update_share(*, actor, organization, share_id, expected_policy_revision, selectors=None,
                 access_expires_at=None, confirm_expansion=False, confirm_recapture=False):
    """Change the disclosure policy and/or the access end of one share.

    A selector change always creates a new policy revision. Widening it needs
    an explicit confirmation. For FROZEN it also needs an explicit recapture:
    the earlier capture is kept untouched and a new one is taken.
    """
    mode = (
        BoxReaderShare.objects.filter(pk=share_id, organization_id=organization.pk)
        .values_list("mode", flat=True).first()
    )
    if mode is None:
        # Still answer as a non-Admin would be answered before revealing absence.
        require_source_admin(actor, organization)
        raise _share_not_found()
    canonical_selectors = None if selectors is None else validate_selectors(selectors)
    capturing = mode == BoxReaderShare.Mode.FROZEN and canonical_selectors is not None

    with _mutation(consistent_capture=capturing):
        source = _locked_source(actor, organization.pk)
        share = _locked_share(source, share_id)
        if share.revoked_at is not None:
            raise SharingError("share_revoked", 409, "This share is revoked.")
        if share.policy_revision != expected_policy_revision:
            raise SharingError(
                "policy_revision_conflict", 409,
                "The disclosure policy changed. Reload it before editing.",
                current_policy_revision=share.policy_revision,
            )
        now = sharing_now()
        changes = {}
        update_fields = []
        capture = None
        if canonical_selectors is not None and canonical_selectors != share.selectors:
            added = sorted(set(canonical_selectors) - set(share.selectors))
            removed = sorted(set(share.selectors) - set(canonical_selectors))
            if added and not confirm_expansion:
                raise SharingError(
                    "disclosure_expansion_requires_confirmation", 409,
                    "Disclosing more data requires an explicit confirmation.",
                    added_selectors=added,
                )
            if share.mode == BoxReaderShare.Mode.FROZEN and not confirm_recapture:
                raise SharingError(
                    "frozen_policy_change_requires_recapture", 409,
                    "Changing a frozen share replaces its content with a new capture "
                    "of the current source state. Confirm the recapture.",
                )
            snapshot = _project_for_admin(actor, source, share.box_id, canonical_selectors)
            changes["previous_policy_revision"] = share.policy_revision
            changes["selectors_added"] = added
            changes["selectors_removed"] = removed
            share.selectors = canonical_selectors
            share.schema_version = snapshot["schema_version"]
            share.policy_revision += 1
            update_fields += ["selectors", "schema_version", "policy_revision"]
            if share.mode == BoxReaderShare.Mode.FROZEN:
                capture = _store_capture(share, snapshot, actor, now)
                changes["capture_sha256"] = capture.content_sha256
        if access_expires_at is not None and access_expires_at != share.access_expires_at:
            _validate_access_expiry(now, access_expires_at)
            if share.accepted_at is None and access_expires_at < share.invitation_expires_at:
                raise SharingError(
                    "access_expiry_out_of_bounds", 400,
                    "The access end cannot precede the end of the pending invitation.",
                )
            changes["access_expires_at"] = {
                "before": share.access_expires_at.isoformat(), "after": access_expires_at.isoformat(),
            }
            share.access_expires_at = access_expires_at
            update_fields.append("access_expires_at")
        if update_fields:
            share.save(update_fields=[*update_fields, "updated_at"])
            _audit(AuditLog.Action.UPDATE, share, actor, "Box reader share policy updated", **changes)
    return share


def revoke_share(*, actor, organization, share_id):
    """Revoke a share and destroy any acceptable invitation secret. Idempotent."""
    with transaction.atomic():
        source = _locked_source(actor, organization.pk)
        share = _locked_share(source, share_id)
        if share.revoked_at is None:
            share.revoked_at = sharing_now()
            share.revoked_by = actor
            share.invitation_token_hash = None
            share.save(update_fields=["revoked_at", "revoked_by", "invitation_token_hash", "updated_at"])
            _audit(
                AuditLog.Action.ARCHIVE, share, actor, "Box reader share revoked",
                was_accepted=share.accepted_at is not None,
            )
    return share


def reissue_invitation(*, actor, organization, share_id, invitation_expires_at):
    """Issue a new one-time secret; every earlier secret stops matching.

    This is also the retry of a `failed`, `uncertain` or `pending` delivery.
    It only runs on a non-revoked, non-accepted, non-expired share and within
    the configured lifetime bounds; the access end is never changed.
    """
    _require_email_delivery()
    with transaction.atomic():
        source = _locked_source(actor, organization.pk)
        share = _locked_share(source, share_id)
        now = sharing_now()
        if share.revoked_at is not None or share.accepted_at is not None:
            raise SharingError("invitation_not_pending", 409, "This invitation is no longer pending.")
        if now > share.access_expires_at:
            raise SharingError("share_expired", 409, "This share has expired.")
        _validate_invitation_expiry(now, invitation_expires_at, share.access_expires_at)
        secret, digest = new_invitation_secret()
        share.invitation_token_hash = digest
        share.invitation_generation += 1
        share.invitation_issued_at = now
        share.invitation_expires_at = invitation_expires_at
        share.delivery_status = BoxReaderShare.DeliveryStatus.PENDING
        share.delivery_updated_at = now
        share.save(update_fields=[
            "invitation_token_hash", "invitation_generation", "invitation_issued_at",
            "invitation_expires_at", "delivery_status", "delivery_updated_at", "updated_at",
        ])
        _audit(
            AuditLog.Action.UPDATE, share, actor, "Box reader share invitation reissued",
            invitation_generation=share.invitation_generation,
            invitation_expires_at=invitation_expires_at.isoformat(),
        )
    deliver_invitation(share.pk, share.invitation_generation, secret)
    return BoxReaderShare.objects.get(pk=share.pk)


# B. Invitation acceptance -----------------------------------------------------


def _invitation_unavailable():
    # One answer for malformed, unknown, superseded, consumed and revoked
    # secrets: nothing about the invitation can be probed.
    return SharingError("invitation_unavailable", 404, "This invitation is not available.")


def _resolve_invitation(user, token, *, lock):
    if not _is_usable_account(user):
        raise _invitation_unavailable()
    digest = invitation_digest(token)
    if digest is None:
        raise _invitation_unavailable()
    shares = BoxReaderShare.objects.select_related("organization")
    if lock:
        # Institution first, then the share: the common lock order. The share
        # is then re-read under its own lock, never trusted from this lookup.
        organization_id = (
            BoxReaderShare.objects.filter(invitation_token_hash=digest)
            .values_list("organization_id", flat=True).first()
        )
        if organization_id is None:
            raise _invitation_unavailable()
        Organization.objects.select_for_update().filter(pk=organization_id).first()
        shares = shares.select_for_update(of=("self",))
    share = shares.filter(invitation_token_hash=digest).first()
    if (
        share is None or share.revoked_at is not None or share.accepted_at is not None
        or not share.organization.is_active
    ):
        raise _invitation_unavailable()
    # The secret proves control of the invited mailbox; the signed-in account
    # must be the account of that same mailbox. Neither is enough alone.
    if normalize_email(user.email) != share.recipient_email:
        raise SharingError(
            "invitation_recipient_mismatch", 403,
            "This invitation was issued for another account.",
        )
    now = sharing_now()
    if now > share.invitation_expires_at:
        raise SharingError("invitation_expired", 410, "This invitation has expired.")
    if now > share.access_expires_at:
        raise SharingError("share_expired", 410, "This share has expired.")
    return share, now


def _recipient_summary(share, now):
    state = share_state(share, now)
    if state == STATE_ACTIVE and not share.organization.is_active:
        state = STATE_UNAVAILABLE
    return {
        "id": str(share.public_id),
        "mode": share.mode,
        "read_only": True,
        "state": state,
        "policy_revision": share.policy_revision,
        "schema_version": share.schema_version,
        "access_expires_at": share.access_expires_at,
        "source_institution": {"name": share.organization.name},
    }


def inspect_invitation(*, user, token):
    """Safe metadata of an acceptable invitation. Consumes nothing."""
    share, _now = _resolve_invitation(user, token, lock=False)
    return {
        "mode": share.mode,
        "read_only": True,
        "invitation_expires_at": share.invitation_expires_at,
        "access_expires_at": share.access_expires_at,
        "source_institution": {"name": share.organization.name},
    }


def accept_invitation(*, user, token):
    """Bind the invited account to the grant, once, and destroy the secret."""
    with transaction.atomic():
        share, now = _resolve_invitation(user, token, lock=True)
        share.recipient_user = user
        share.accepted_at = now
        share.invitation_token_hash = None
        share.save(update_fields=["recipient_user", "accepted_at", "invitation_token_hash", "updated_at"])
        _audit(
            AuditLog.Action.UPDATE, share, user, "Box reader share invitation accepted",
            invitation_generation=share.invitation_generation,
        )
        return _recipient_summary(share, now)


# C. Reading an active grant ---------------------------------------------------


def _recipient_shares(user):
    """Grants of exactly this account.

    The grant is bound to the account by acceptance. It is also only usable
    while the account still carries the invited mailbox, so an email change
    can never hand the grant to a different mailbox owner silently.
    """
    return BoxReaderShare.objects.select_related("organization").filter(
        recipient_user=user, accepted_at__isnull=False,
        recipient_email=normalize_email(user.email),
    )


def list_shared_boxes(*, user):
    if not _is_usable_account(user):
        return []
    now = sharing_now()
    return [
        _recipient_summary(share, now)
        for share in _recipient_shares(user).order_by("-accepted_at", "-pk")
    ]


def _source_unavailable():
    return SharingError(
        "share_source_unavailable", 409, "The shared data is currently unavailable.",
    )


def _require_readable(share, now):
    if share.revoked_at is not None:
        raise SharingError("share_revoked", 410, "This share has been revoked.")
    if now > share.access_expires_at:
        raise SharingError("share_expired", 410, "This share has expired.")
    if not share.organization.is_active or share.schema_version not in SUPPORTED_VERSIONS:
        raise _source_unavailable()


def _frozen_content(share):
    # Same scope rule as the live projection: the Box must still belong to
    # the source institution and must not have been merged away.
    in_scope = Box.objects.filter(
        pk=share.box_id, organization_id=share.organization_id, merged_into__isnull=True,
    ).exists()
    capture = (
        BoxReaderShareCapture.objects.filter(share_id=share.pk, policy_revision=share.policy_revision).first()
        if in_scope else None
    )
    if capture is None:
        raise _source_unavailable()
    snapshot = _verified_capture(share, capture)
    if snapshot is None:
        logger.error("Box reader share %s: stored capture is not valid for its policy.", share.pk)
        raise _source_unavailable()
    return snapshot, capture.content_sha256, {
        "kind": "frozen_capture",
        "captured_at": capture.captured_at,
        "content_sha256": capture.content_sha256,
    }


def _verified_capture(share, capture):
    """Return the parsed capture, or None if anything about it is untrustworthy.

    The digest is compared before the persisted text is parsed, so a
    corrupted row is never interpreted. The parsed payload must then be a
    valid canonical 1.0 snapshot of exactly the stored policy, byte for byte
    what `canonical_json` would produce. No stored content ever raises.
    """
    try:
        digest = hashlib.sha256(capture.content.encode("utf-8")).hexdigest()
        if digest != capture.content_sha256 or capture.schema_version != share.schema_version:
            return None
        snapshot = json.loads(capture.content)
        if (
            not isinstance(snapshot, dict)
            or validate_snapshot(snapshot)
            or snapshot["disclosed"] != share.selectors
            or canonical_json(snapshot) != capture.content
        ):
            return None
    except (ValueError, TypeError, KeyError, RecursionError):
        # UnicodeError and JSONDecodeError are ValueErrors.
        return None
    return snapshot


def _live_content(share, now):
    # D. Trusted projection: source and Box come from the stored grant only.
    result = project_verified_source_box_snapshot(
        source=share.organization, box_id=share.box_id,
        selectors=share.selectors, required_selectors=MANDATORY_SELECTORS,
    )
    if not result.ok or result.snapshot["schema_version"] != share.schema_version:
        logger.warning(
            "Box reader share %s: live projection unavailable (%s).",
            share.pk, ",".join(sorted({item.code for item in result.diagnostics})),
        )
        raise _source_unavailable()
    snapshot = result.snapshot
    digest = hashlib.sha256(canonical_json(snapshot).encode("utf-8")).hexdigest()
    return snapshot, digest, {
        "kind": "live_projection",
        # Server time just before the source was read. The projection uses
        # several reads and is not one atomic scientific snapshot.
        "projected_at": now,
        "consistency": "non_atomic",
    }


def read_shared_box(*, user, public_id):
    """Serve the disclosed content of one grant to its bound account.

    Authorization and the policy revision are re-read after the content was
    prepared: a revocation, expiry or policy change committed meanwhile
    prevents delivery. Delivery is audited server-side in the same request.
    """
    if not _is_usable_account(user):
        raise _share_not_found()
    now = sharing_now()
    share = _recipient_shares(user).filter(public_id=public_id).first()
    if share is None:
        raise _share_not_found()
    _require_readable(share, now)
    if share.mode == BoxReaderShare.Mode.FROZEN:
        snapshot, digest, content = _frozen_content(share)
    else:
        snapshot, digest, content = _live_content(share, now)

    current = _recipient_shares(user).filter(pk=share.pk).first()
    if current is None:
        raise _share_not_found()
    _require_readable(current, sharing_now())
    if current.policy_revision != share.policy_revision:
        raise SharingError("share_policy_changed", 409, "The share changed during the read; retry.")

    _audit(
        AuditLog.Action.VIEW, share, user, "Shared Box data served",
        content_kind=content["kind"], content_sha256=digest,
    )
    return {"share": _recipient_summary(share, now), "content": content, "snapshot": snapshot}
