import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
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
        PENDING_REVIEW = "pending_review", _("À vérifier")
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", _("Inactive")

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
    stop_reason_missing_from_history = models.BooleanField(default=False)
    deactivated_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    polyp_state_revision = models.PositiveBigIntegerField(default=0, editable=False)

    def save(self, *args, **kwargs):
        # All ordinary code writers share the allocator lock, including imports
        # and admin renames. Status/location updates never acquire this lock.
        from .box_codes import register_box_code

        update_fields = kwargs.get("update_fields")
        if not self._state.adding and update_fields is None:
            # Ordinary full saves must not overwrite a newer scientific revision
            # from an object fetched before a measurement/subculture committed.
            update_fields = {field.name for field in self._meta.concrete_fields
                             if not field.primary_key and field.name != "polyp_state_revision"}
            kwargs["update_fields"] = update_fields
        if self._state.adding or "global_code" in update_fields:
            with transaction.atomic(using=kwargs.get("using")):
                if not self._state.adding:
                    type(self).objects.select_for_update().only("pk").get(pk=self.pk)
                register_box_code(self.global_code, namespace=self.strain.code)
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

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
    end_date_unknown = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-starts_at"]
        indexes = [
            models.Index(fields=["box", "starts_at"]),
            models.Index(fields=["thermal_zone", "starts_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date_unknown=False) | Q(ends_at__isnull=True),
                name="box_location_unknown_end_has_no_date",
            )
        ]

    def clean(self):
        if self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError("The end date must be after the start date.")
        if self.ends_at and self.end_date_unknown:
            raise ValidationError("A location with an unknown end date cannot also have an end date.")

    def __str__(self):
        return f"{self.box} in {self.thermal_zone}"


class BoxInventoryInitialization(models.Model):
    organization = models.OneToOneField(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="box_inventory_initialization",
    )
    initialized_at = models.DateTimeField(auto_now_add=True)
    box_count = models.PositiveIntegerField()
    previous_status_counts = models.JSONField(default=dict)
    selection_hash = models.CharField(max_length=64)

    def __str__(self):
        return f"Box inventory initialized for {self.organization}"


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


class ProtectedSubcultureQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if self.filter(occurred_at__isnull=False).exists() and set(kwargs) != {"user"}:
            raise ValidationError("Quantitative subculture history is immutable.")
        return super().update(**kwargs)

    def delete(self):
        if self.filter(occurred_at__isnull=False).exists():
            raise ValidationError("Quantitative subculture history is protected.")
        return super().delete()


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
    # NULL denotes legacy unknown history, never a reconstructed occurrence.
    occurred_at = models.DateTimeField(null=True, editable=False)
    parent_state_sequence = models.PositiveBigIntegerField(null=True, editable=False)
    parent_polyp_count_before = models.PositiveIntegerField(null=True, editable=False)
    allocated_polyp_count = models.PositiveIntegerField(null=True, editable=False)
    parent_polyp_count_after = models.PositiveIntegerField(null=True, editable=False)
    parent_state_snapshot = models.JSONField(null=True, editable=False)
    author_name = models.CharField(max_length=150, blank=True, editable=False)

    objects = ProtectedSubcultureQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk, occurred_at__isnull=False).exists():
            raise ValidationError("Quantitative subculture history is immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.occurred_at is not None:
            raise ValidationError("Quantitative subculture history is protected.")
        return super().delete(*args, **kwargs)

    class Meta:
        ordering = ["-event_date"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(occurred_at__isnull=True, parent_state_sequence__isnull=True,
                      parent_polyp_count_before__isnull=True, allocated_polyp_count__isnull=True,
                      parent_polyp_count_after__isnull=True, parent_state_snapshot__isnull=True)
                    | Q(occurred_at__isnull=False, parent_state_sequence__isnull=False,
                        parent_polyp_count_before__isnull=False, allocated_polyp_count__isnull=False,
                        parent_polyp_count_after__isnull=False, parent_state_snapshot__isnull=False,
                        parent_polyp_count_before=models.F("allocated_polyp_count") + models.F("parent_polyp_count_after"))
                    | Q(occurred_at__isnull=False, parent_state_sequence__isnull=False,
                        parent_polyp_count_before__isnull=False, allocated_polyp_count__isnull=True,
                        parent_polyp_count_after__isnull=True, parent_state_snapshot__isnull=False)
                ),
                name="subculture_absolute_polyp_balance",
            ),
        ]

    def __str__(self):
        return f"Subculture from {self.parent_box} on {self.event_date}"


class ImmutableAllocationQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Subculture allocations are immutable.")

    def delete(self):
        raise ValidationError("Subculture allocations are protected.")


class SubcultureAllocation(models.Model):
    event = models.ForeignKey(SubcultureEvent, on_delete=models.PROTECT, related_name="allocations")
    child_box = models.OneToOneField(Box, on_delete=models.PROTECT, related_name="subculture_initialization")
    position = models.PositiveIntegerField()
    allocated_polyps = models.PositiveIntegerField(null=True)
    child_state_sequence = models.PositiveBigIntegerField(default=1, editable=False)
    child_global_code = models.CharField(max_length=100)

    objects = ImmutableAllocationQuerySet.as_manager()

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["event", "position"], name="unique_subculture_allocation_position"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Subculture allocations are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Subculture allocations are protected.")


class BoxCodeNamespace(models.Model):
    namespace = models.CharField(max_length=100, unique=True)
    high_water = models.PositiveBigIntegerField(default=0)


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


class PortableLineageNode(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="portable_lineage_nodes",
    )
    node_id = models.UUIDField(default=uuid.uuid4, editable=False)
    local_box = models.OneToOneField(
        Box,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="portable_lineage_node",
        editable=False,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "node_id"],
                name="portable_node_identity_unique",
            )
        ]


class PortableLineageEdge(models.Model):
    class RelationshipType(models.TextChoices):
        SUBCULTURE = "subculture", _("Repiquage")
        SEXUAL_REPRODUCTION = "sexual_reproduction", _("Reproduction sexuée")
        HISTORICAL_IMPORT = "historical_import", _("Import historique")
        OTHER = "other", _("Autre")
        TRANSFER = "transfer", _("Transfert")

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="portable_lineage_edges",
    )
    edge_id = models.UUIDField(default=uuid.uuid4, editable=False)
    source_node = models.ForeignKey(
        PortableLineageNode, on_delete=models.PROTECT, related_name="outgoing_edges",
    )
    target_node = models.ForeignKey(
        PortableLineageNode, on_delete=models.PROTECT, related_name="incoming_edges",
    )
    relationship_type = models.CharField(
        max_length=40,
        choices=RelationshipType.choices,
        default=RelationshipType.SUBCULTURE,
    )
    local_lineage = models.OneToOneField(
        BoxLineage,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="portable_lineage_edge",
    )
    transfer_id = models.UUIDField(null=True, blank=True)
    item_id = models.UUIDField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "edge_id"],
                name="portable_edge_identity_unique",
            ),
            models.CheckConstraint(
                condition=~Q(source_node=models.F("target_node")),
                name="portable_edge_not_self",
            ),
            models.CheckConstraint(
                condition=Q(relationship_type__in=[
                    "subculture", "sexual_reproduction", "historical_import", "other", "transfer",
                ]),
                name="portable_edge_type_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(relationship_type="transfer", transfer_id__isnull=False, item_id__isnull=False)
                    | (
                        ~Q(relationship_type="transfer")
                        & Q(transfer_id__isnull=True, item_id__isnull=True)
                    )
                ),
                name="portable_edge_provenance_valid",
            ),
            models.CheckConstraint(
                condition=~Q(relationship_type="transfer") | Q(local_lineage__isnull=True),
                name="portable_transfer_no_local_lineage",
            ),
        ]


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
    # Nullable for historical transfers; the API requires it for every new one.
    polyp_count = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLANNED)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-transfer_date"]

    def __str__(self):
        return f"{self.box} from {self.from_organization} to {self.to_organization}"


class TransferEnvelope(models.Model):
    """Source-owned v2 package. Portable content uses only frozen snapshots."""

    transfer_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    source_organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT,
        related_name="transfer_envelopes", editable=False,
    )
    source_institution_id = models.UUIDField(editable=False)
    source_institution_name = models.CharField(max_length=150, editable=False)
    destination_institution_id = models.UUIDField(null=True, blank=True, editable=False)
    destination_institution_name = models.CharField(
        max_length=150, blank=True, default="", editable=False,
    )
    protocol_major = models.PositiveIntegerField(default=2, editable=False)
    protocol_minor = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="transfer_envelopes", editable=False,
    )


class TransferItem(models.Model):
    """One source selection; portable identity is (transfer_id, item_id)."""

    envelope = models.ForeignKey(
        TransferEnvelope, on_delete=models.PROTECT, related_name="items", editable=False,
    )
    item_id = models.UUIDField(default=uuid.uuid4, editable=False)
    source_box = models.ForeignKey(
        Box, on_delete=models.PROTECT, related_name="transfer_items", editable=False,
    )
    source_box_code = models.CharField(max_length=100, editable=False)
    source_strain_code = models.CharField(max_length=80, editable=False)
    species_scientific_name = models.CharField(max_length=150, editable=False)
    global_strain_id = models.UUIDField(editable=False)
    declared_polyp_quantity = models.PositiveIntegerField(editable=False)
    lineage_snapshot = models.JSONField(null=True, blank=True, editable=False, default=None)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["envelope", "item_id"], name="unique_transfer_item_per_envelope",
            ),
            models.CheckConstraint(
                condition=Q(declared_polyp_quantity__gte=0),
                name="transfer_item_quantity_nonnegative",
            ),
        ]


class BoxTransferImport(models.Model):
    format_version = models.CharField(max_length=80)
    source_transfer_id = models.CharField(max_length=120)
    source_organization_name = models.CharField(max_length=180)
    source_global_code = models.CharField(max_length=100)
    destination_organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="box_transfer_imports",
    )
    created_box = models.OneToOneField(
        Box,
        on_delete=models.PROTECT,
        related_name="transfer_import",
    )
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="box_transfer_imports",
    )
    imported_at = models.DateTimeField(auto_now_add=True)
    source_data = models.JSONField(default=dict)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["format_version", "source_organization_name", "source_transfer_id"],
                name="unique_imported_box_transfer",
            )
        ]

    def __str__(self):
        return f"{self.source_global_code} -> {self.created_box.global_code}"
