from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class OrganizationMembership(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", _("Administrateur")
        LAB_TECHNICIAN = "lab_technician", _("Technicien laboratoire")
        VIEWER = "viewer", _("Lecture seule")

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(max_length=30, choices=Role.choices, default=Role.VIEWER)
    is_active = models.BooleanField(default=True)
    starts_on = models.DateField(default=timezone.localdate)
    ends_on = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization"],
                name="unique_user_organization_membership",
            )
        ]

    def __str__(self):
        return f"{self.user} - {self.organization} - {self.role}"


class UserPreference(models.Model):
    class InterfaceLanguage(models.TextChoices):
        FRENCH = "fr", "Français"
        ENGLISH = "en", _("Anglais")

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="preference",
    )
    interface_language = models.CharField(
        max_length=5,
        choices=InterfaceLanguage.choices,
        default=InterfaceLanguage.FRENCH,
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user} preferences"
