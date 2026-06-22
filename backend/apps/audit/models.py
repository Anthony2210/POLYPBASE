from django.conf import settings
from django.db import models


class Alert(models.Model):
    class AlertType(models.TextChoices):
        BIOLOGICAL = "biological", "Biological"
        TEMPERATURE = "temperature", "Temperature"
        SALINITY = "salinity", "Salinity"
        SUBCULTURE = "subculture", "Subculture"
        OTHER = "other", "Other"

    class Level(models.TextChoices):
        INFO = "info", "Information"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="alerts")
    box = models.ForeignKey(
        "cultures.Box",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="alerts",
    )
    thermal_zone = models.ForeignKey(
        "cultures.ThermalZone",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="alerts",
    )
    alert_type = models.CharField(max_length=40, choices=AlertType.choices)
    level = models.CharField(max_length=20, choices=Level.choices, default=Level.WARNING)
    message = models.CharField(max_length=250)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_alerts",
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_alerts",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "alert_type", "created_at"]),
        ]

    @property
    def is_active(self):
        return self.resolved_at is None

    def __str__(self):
        return self.message


class AuditLog(models.Model):
    class Action(models.TextChoices):
        CREATION = "creation", "Creation"
        UPDATE = "update", "Update"
        ARCHIVE = "archive", "Archive"
        ENTRY = "entry", "Entry"
        SUBCULTURE = "subculture", "Subculture"
        TRANSFER = "transfer", "Transfer"
        IMPORT = "import", "Import"
        EXPORT = "export", "Export"
        SCAN = "scan", "Scan"
        VIEW = "view", "View"
        LOGIN = "login", "Login"

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=40, choices=Action.choices)
    object_type = models.CharField(max_length=80, blank=True)
    object_id = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "created_at"]),
            models.Index(fields=["action", "created_at"]),
        ]

    def __str__(self):
        return f"{self.get_action_display()} - {self.created_at:%Y-%m-%d %H:%M}"
