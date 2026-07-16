from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class ThermalZone(models.Model):
    class ZoneType(models.TextChoices):
        CABINET = "cabinet", _("Armoire")
        INCUBATOR = "incubator", _("Étuve")
        TANK = "tank", "Tank"
        OTHER = "other", _("Autre")

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="thermal_zones",
    )
    name = models.CharField(max_length=120)
    zone_type = models.CharField(max_length=30, choices=ZoneType.choices, default=ZoneType.CABINET)
    target_temperature_c = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    # Salinity of the water in this zone, maintained by hand like the capacity.
    # It is the reference shown on every box sheet of the zone; each measurement
    # can still record the salinity actually read for one box.
    salinity_psu = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="unique_thermal_zone_per_organization",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.organization})"


class Box(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", _("Archivée")
        LOST = "lost", _("Perdue")
        STOPPED = "stopped", _("Arrêtée")

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="boxes",
    )
    global_code = models.CharField(max_length=100, unique=True)
    local_code = models.CharField(max_length=100, blank=True)
    box_number = models.CharField(max_length=80)
    strain = models.ForeignKey("taxonomy.Strain", on_delete=models.PROTECT, related_name="boxes")
    origin = models.ForeignKey("taxonomy.Origin", on_delete=models.SET_NULL, null=True, blank=True)
    thermal_zone = models.ForeignKey(
        ThermalZone,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="boxes",
    )
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.ACTIVE)
    created_on = models.DateField(auto_now_add=True)
    entered_on = models.DateField(null=True, blank=True)
    volume_liters = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    stop_reason = models.CharField(max_length=250, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["global_code"]),
            models.Index(fields=["local_code"]),
        ]

    def __str__(self):
        return f"{self.global_code} - {self.box_number}"


class BoxLocation(models.Model):
    box = models.ForeignKey(Box, on_delete=models.CASCADE, related_name="locations")
    thermal_zone = models.ForeignKey(ThermalZone, on_delete=models.PROTECT, related_name="box_locations")
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-starts_at"]
        indexes = [
            models.Index(fields=["box", "starts_at"]),
            models.Index(fields=["thermal_zone", "starts_at"]),
        ]

    def clean(self):
        if self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError("The end date must be after the start date.")

    def __str__(self):
        return f"{self.box} in {self.thermal_zone}"


class BoxMovement(models.Model):
    box = models.ForeignKey(Box, on_delete=models.CASCADE, related_name="movements")
    from_thermal_zone = models.ForeignKey(
        ThermalZone,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="outgoing_box_movements",
    )
    to_thermal_zone = models.ForeignKey(
        ThermalZone,
        on_delete=models.PROTECT,
        related_name="incoming_box_movements",
    )
    moved_at = models.DateTimeField(default=timezone.now)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-moved_at"]

    def __str__(self):
        return f"{self.box} to {self.to_thermal_zone}"


class SubcultureEvent(models.Model):
    parent_box = models.ForeignKey(
        Box,
        on_delete=models.PROTECT,
        related_name="source_subculture_events",
    )
    event_date = models.DateField(default=timezone.localdate)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    reason = models.CharField(max_length=180, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-event_date"]

    def __str__(self):
        return f"Subculture from {self.parent_box} on {self.event_date}"


class BoxLineage(models.Model):
    class RelationshipType(models.TextChoices):
        SUBCULTURE = "subculture", _("Repiquage")
        SEXUAL_REPRODUCTION = "sexual_reproduction", _("Reproduction sexuée")
        HISTORICAL_IMPORT = "historical_import", _("Import historique")
        OTHER = "other", _("Autre")

    parent_box = models.ForeignKey(
        Box,
        on_delete=models.PROTECT,
        related_name="child_lineages",
    )
    child_box = models.ForeignKey(
        Box,
        on_delete=models.PROTECT,
        related_name="parent_lineages",
    )
    subculture_event = models.ForeignKey(
        SubcultureEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lineages",
    )
    relationship_type = models.CharField(
        max_length=40,
        choices=RelationshipType.choices,
        default=RelationshipType.SUBCULTURE,
    )
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["parent_box", "child_box"],
                name="unique_box_lineage",
            ),
            models.CheckConstraint(
                condition=~Q(parent_box=models.F("child_box")),
                name="box_lineage_parent_differs_from_child",
            ),
        ]

    def __str__(self):
        return f"{self.parent_box} -> {self.child_box}"


class IdentificationTag(models.Model):
    class TagType(models.TextChoices):
        QR = "qr", "QR code"
        NFC = "nfc", "NFC"
        RFID = "rfid", "RFID"

    tag_type = models.CharField(max_length=20, choices=TagType.choices, default=TagType.QR)
    code = models.CharField(max_length=160, unique=True)
    url = models.URLField(blank=True)
    box = models.ForeignKey(
        Box,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tags",
    )
    thermal_zone = models.ForeignKey(
        ThermalZone,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="tags",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(box__isnull=False, thermal_zone__isnull=True)
                    | Q(box__isnull=True, thermal_zone__isnull=False)
                ),
                name="identification_tag_targets_one_object",
            )
        ]

    def __str__(self):
        return f"{self.get_tag_type_display()} {self.code}"


class BoxTransfer(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", _("Prévu")
        COMPLETED = "completed", _("Terminé")
        CANCELLED = "cancelled", _("Annulé")

    box = models.ForeignKey(Box, on_delete=models.CASCADE, related_name="transfers")
    from_organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="outgoing_box_transfers",
    )
    to_organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="incoming_box_transfers",
    )
    transfer_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-transfer_date"]

    def __str__(self):
        return f"{self.box} from {self.from_organization} to {self.to_organization}"
