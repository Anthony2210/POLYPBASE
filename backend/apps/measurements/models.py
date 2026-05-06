from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class BiologicalMeasurement(models.Model):
    class CultureStatus(models.TextChoices):
        GOOD = "good", _("Bon")
        MEDIUM = "medium", _("Moyen")
        BAD = "bad", _("Mauvais")
        DEAD = "dead", _("Mort")
        NOT_SPECIFIED = "not_specified", _("Non précisé")

    box = models.ForeignKey(
        "cultures.Box",
        on_delete=models.CASCADE,
        related_name="biological_measurements",
    )
    measured_on = models.DateField()
    polyp_count = models.PositiveIntegerField(default=0)
    ephyrae_count = models.PositiveIntegerField(default=0)
    strobila_count = models.PositiveIntegerField(default=0)
    culture_status = models.CharField(
        max_length=30,
        choices=CultureStatus.choices,
        default=CultureStatus.NOT_SPECIFIED,
    )
    needs_attention = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-measured_on", "-created_at"]
        indexes = [
            models.Index(fields=["box", "measured_on"]),
        ]

    def __str__(self):
        return f"{self.box} - {self.measured_on}"


class Observation(models.Model):
    class ObservationType(models.TextChoices):
        GENERAL = "general", _("Générale")
        CONTAMINATION = "contamination", "Contamination"
        SUBCULTURE_NEEDED = "subculture_needed", _("Repiquage à prévoir")
        MORTALITY = "mortality", _("Mortalité")
        OTHER = "other", _("Autre")

    box = models.ForeignKey("cultures.Box", on_delete=models.CASCADE, related_name="observations")
    observed_on = models.DateField(default=timezone.localdate)
    observation_type = models.CharField(
        max_length=40,
        choices=ObservationType.choices,
        default=ObservationType.GENERAL,
    )
    notes = models.TextField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-observed_on", "-created_at"]

    def __str__(self):
        return f"{self.box} - {self.get_observation_type_display()}"


class Probe(models.Model):
    class ProbeType(models.TextChoices):
        LORAWAN = "lorawan", "LoRaWAN"
        IMINILIDE = "iminilide", "iMinilide"
        MANUAL = "manual", _("Saisie manuelle")
        OTHER = "other", _("Autre")

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="probes")
    thermal_zone = models.ForeignKey(
        "cultures.ThermalZone",
        on_delete=models.CASCADE,
        related_name="probes",
    )
    code = models.CharField(max_length=120)
    probe_type = models.CharField(max_length=30, choices=ProbeType.choices, default=ProbeType.OTHER)
    location = models.CharField(max_length=180, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"],
                name="unique_probe_per_organization",
            )
        ]

    def __str__(self):
        return f"{self.code} - {self.thermal_zone}"


class TemperatureMeasurement(models.Model):
    probe = models.ForeignKey(Probe, on_delete=models.CASCADE, related_name="temperature_measurements")
    measured_at = models.DateTimeField()
    temperature_c = models.DecimalField(max_digits=5, decimal_places=2)
    source = models.CharField(max_length=80, blank=True)
    raw_data = models.JSONField(default=dict, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-measured_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["probe", "measured_at"],
                name="unique_temperature_measurement_per_probe_date",
            )
        ]
        indexes = [
            models.Index(fields=["measured_at"]),
        ]

    def __str__(self):
        return f"{self.probe} - {self.measured_at:%Y-%m-%d %H:%M}"


class DailyTemperature(models.Model):
    thermal_zone = models.ForeignKey(
        "cultures.ThermalZone",
        on_delete=models.CASCADE,
        related_name="daily_temperatures",
    )
    date = models.DateField()
    min_temperature_c = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    average_temperature_c = models.DecimalField(max_digits=5, decimal_places=2)
    max_temperature_c = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    measurement_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(
                fields=["thermal_zone", "date"],
                name="unique_daily_temperature_per_zone_date",
            )
        ]

    def __str__(self):
        return f"{self.thermal_zone} - {self.date}"


class SalinityMeasurement(models.Model):
    thermal_zone = models.ForeignKey(
        "cultures.ThermalZone",
        on_delete=models.CASCADE,
        related_name="salinity_measurements",
    )
    measured_on = models.DateField()
    salinity_psu = models.DecimalField(max_digits=5, decimal_places=2)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-measured_on"]
        constraints = [
            models.UniqueConstraint(
                fields=["thermal_zone", "measured_on"],
                name="unique_salinity_measurement_per_zone_date",
            )
        ]

    def __str__(self):
        return f"{self.thermal_zone} - {self.measured_on}"


class ThermalAnomaly(models.Model):
    class Level(models.TextChoices):
        INFO = "info", "Information"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    thermal_zone = models.ForeignKey(
        "cultures.ThermalZone",
        on_delete=models.CASCADE,
        related_name="thermal_anomalies",
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    reference_temperature_c = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    max_deviation_c = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    level = models.CharField(max_length=20, choices=Level.choices, default=Level.WARNING)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-starts_at"]

    def __str__(self):
        return f"{self.thermal_zone} - anomaly {self.starts_at:%Y-%m-%d}"
