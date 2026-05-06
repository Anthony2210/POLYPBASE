from django.db import models


class Organization(models.Model):
    name = models.CharField(max_length=150, unique=True)
    slug = models.SlugField(max_length=120, unique=True, null=True, blank=True)
    city = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=120, blank=True)
    contact_email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.name


class PartnerInstitution(models.Model):
    name = models.CharField(max_length=180, unique=True)
    city = models.CharField(max_length=120, blank=True)
    country = models.CharField(max_length=120, blank=True)
    contact_name = models.CharField(max_length=150, blank=True)
    contact_email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.name


class SharingAgreement(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        ENDED = "ended", "Ended"

    owner_organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="outgoing_sharing_agreements",
    )
    partner_organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="incoming_sharing_agreements",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    can_view_inventory = models.BooleanField(default=True)
    can_view_measurements = models.BooleanField(default=False)
    can_export_data = models.BooleanField(default=False)
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner_organization", "partner_organization"],
                name="unique_sharing_agreement_between_organizations",
            ),
            models.CheckConstraint(
                condition=~models.Q(owner_organization=models.F("partner_organization")),
                name="sharing_agreement_uses_two_organizations",
            ),
        ]

    def __str__(self):
        return f"{self.owner_organization} -> {self.partner_organization}"
