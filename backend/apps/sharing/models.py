"""Persistent state of external read-only sharing of one source Box.

One `BoxReaderShare` row is one recipient-specific invitation and, once
accepted, the individual read grant of exactly one account for exactly one
Box. It never creates a membership and references no biological state other
than the shared Box itself.
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class BoxReaderShare(models.Model):
    class Mode(models.TextChoices):
        # A stored capture of one identified source state.
        FROZEN = "frozen", "Frozen"
        # A projection of current source data at every authorized read.
        LIVE = "live", "Live"

    class DeliveryStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        UNCERTAIN = "uncertain", "Uncertain"

    # Opaque reader-facing reference. It grants nothing by itself.
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    # Source ownership. Never updated after creation; PROTECT keeps the
    # disclosure evidence when someone tries to delete the institution or Box.
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT, related_name="box_reader_shares",
    )
    box = models.ForeignKey("cultures.Box", on_delete=models.PROTECT, related_name="reader_shares")
    # Canonical invited mailbox. Never updated after creation.
    recipient_email = models.CharField(max_length=254)
    # Bound once by acceptance. If the account is deleted the row keeps
    # `accepted_at`, so it can never look like a pending invitation again.
    recipient_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="box_reader_shares",
    )
    mode = models.CharField(max_length=10, choices=Mode.choices)

    # Disclosure policy: scientific snapshot contract version and the exact
    # catalogue selectors approved by a source Admin. Every selector change
    # increments the revision.
    schema_version = models.CharField(max_length=10)
    selectors = models.JSONField()
    policy_revision = models.PositiveIntegerField(default=1)

    # Invitation. Only a digest of the emailed secret is stored; NULL means no
    # secret can be accepted (already accepted, or revoked).
    invitation_token_hash = models.CharField(max_length=64, null=True, blank=True, unique=True)
    invitation_generation = models.PositiveIntegerField(default=1)
    invitation_issued_at = models.DateTimeField()
    invitation_expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    # Outcome of sending the current invitation generation. The email is sent
    # after the transaction committed, so this is the only evidence of it:
    # `pending` (not recorded yet, also after a crash), `sent` (accepted by
    # the relay), `failed` (definitely refused), `uncertain` (unknown outcome).
    delivery_status = models.CharField(
        max_length=10, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING,
    )
    delivery_updated_at = models.DateTimeField(null=True, blank=True)

    access_expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # One open (non-revoked) share per Box and invited mailbox.
            models.UniqueConstraint(
                fields=["box", "recipient_email"], condition=Q(revoked_at__isnull=True),
                name="box_reader_share_one_open_per_recipient",
            ),
            # A bound account always has acceptance evidence.
            models.CheckConstraint(
                condition=Q(recipient_user__isnull=True) | Q(accepted_at__isnull=False),
                name="box_reader_share_bound_requires_acceptance",
            ),
            # An accepted or revoked share holds no acceptable secret, so an
            # old invitation can never bind or reactivate it.
            models.CheckConstraint(
                condition=Q(invitation_token_hash__isnull=True)
                | Q(accepted_at__isnull=True, revoked_at__isnull=True),
                name="box_reader_share_secret_only_while_pending",
            ),
            models.CheckConstraint(
                condition=Q(policy_revision__gte=1), name="box_reader_share_revision_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "created_at"], name="box_reader_share_org_idx"),
        ]

    def __str__(self):
        return f"Reader share {self.public_id}"


class ImmutableCaptureQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Frozen share captures are immutable.")

    def delete(self):
        raise ValidationError("Frozen share captures are protected.")


class BoxReaderShareCapture(models.Model):
    """Disclosed content of a FROZEN share, captured for one policy revision.

    It stores only what the policy disclosed at capture time, as canonical
    JSON of the scientific snapshot contract. A policy change produces a new
    capture under a new revision; an existing row is never rewritten.
    """

    share = models.ForeignKey(BoxReaderShare, on_delete=models.PROTECT, related_name="captures")
    policy_revision = models.PositiveIntegerField()
    schema_version = models.CharField(max_length=10)
    content = models.TextField()
    content_sha256 = models.CharField(max_length=64)
    captured_at = models.DateTimeField()
    captured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )

    objects = ImmutableCaptureQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["share", "policy_revision"], name="box_reader_share_capture_per_revision",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Frozen share captures are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Frozen share captures are protected.")

    def __str__(self):
        return f"Capture of share {self.share_id} at revision {self.policy_revision}"
