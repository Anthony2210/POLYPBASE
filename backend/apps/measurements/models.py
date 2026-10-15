from datetime import date, datetime, timedelta
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class BiologicalMeasurement(models.Model):
    class TemporalPrecision(models.TextChoices):
        LEGACY_UNVERIFIED = "legacy_unverified", "Legacy stored date not verified"
        EXACT_DATE = "exact_date", "Verified exact observation date"

    class ObserverStatus(models.TextChoices):
        LEGACY_UNESTABLISHED = "legacy_unestablished", "Legacy attribution not established"
        NOT_RECORDED = "not_recorded", "Observer not recorded"
        KNOWN = "known", "Known observer"

    class Origin(models.TextChoices):
        LEGACY_UNCLASSIFIED = "legacy_unclassified", "Legacy unclassified origin"
        DIRECT_ENTRY = "direct_entry", "Direct entry"
        FILE_IMPORT = "file_import", "File import"

    class ImportFormat(models.TextChoices):
        CSV = "csv", "CSV"

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
    week_start = models.DateField(editable=False)
    # Only ordinary creation certifies an exact date. Generic saves and
    # corrections must not infer precision from a stored date or ISO Monday.
    temporal_precision = models.CharField(
        max_length=30, choices=TemporalPrecision.choices,
        default=TemporalPrecision.LEGACY_UNVERIFIED, editable=False,
    )
    polyp_count = models.PositiveIntegerField(default=0)
    ephyrae_count = models.PositiveIntegerField(default=0)
    # NULL means not measured (historical sources that never report strobilae);
    # 0 is a measured scientific zero. Live entry keeps defaulting to 0.
    strobila_count = models.PositiveIntegerField(default=0, null=True, blank=True)
    salinity_psu = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    # File imports preserve unknown optional metadata as NULL. Ordinary entry
    # keeps its existing defaults; canonical source states live in the receipt.
    culture_status = models.CharField(
        max_length=30,
        choices=CultureStatus.choices,
        default=CultureStatus.NOT_SPECIFIED, null=True,
    )
    needs_attention = models.BooleanField(default=False, null=True)
    notes = models.TextField(blank=True, null=True, default="")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    # user remains the legacy latest writer. Provenance is captured explicitly,
    # never inferred by save() from that field or a measurement's date.
    observer_status = models.CharField(
        max_length=30, choices=ObserverStatus.choices,
        default=ObserverStatus.LEGACY_UNESTABLISHED, editable=False,
    )
    original_observer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="original_observations", editable=False,
    )
    origin = models.CharField(
        max_length=30, choices=Origin.choices,
        default=Origin.LEGACY_UNCLASSIFIED, editable=False,
    )
    import_format = models.CharField(
        max_length=20, choices=ImportFormat.choices, blank=True, default="", editable=False,
    )
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="imported_measurements", editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    polyp_state_sequence = models.PositiveBigIntegerField(null=True, editable=False)

    class Meta:
        ordering = ["-measured_on", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["box", "week_start"],
                name="unique_biological_measurement_per_box_week",
            ),
            models.CheckConstraint(
                condition=models.Q(temporal_precision__in=["legacy_unverified", "exact_date"]),
                name="measurement_temporal_precision_supported",
            ),
            # A known account may later be deleted; do not retain a name snapshot
            # or silently relabel the original attribution as unknown/legacy.
            models.CheckConstraint(
                condition=(
                    models.Q(observer_status="known")
                    | models.Q(
                        observer_status__in=["not_recorded", "legacy_unestablished"],
                        original_observer__isnull=True,
                    )
                ),
                name="measurement_observer_consistent",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(origin="file_import", import_format="csv")
                    | models.Q(
                        origin__in=["direct_entry", "legacy_unclassified"],
                        import_format="", imported_by__isnull=True,
                    )
                ),
                name="measurement_origin_consistent",
            ),
            models.CheckConstraint(
                condition=~models.Q(origin="direct_entry") | models.Q(observer_status="known"),
                name="measurement_direct_observer_known",
            ),
        ]
        indexes = [
            models.Index(fields=["box", "measured_on"]),
        ]

    @staticmethod
    def week_start_for(measured_on):
        """Return the Monday that starts the ISO week containing the date."""
        if isinstance(measured_on, datetime) or not isinstance(measured_on, date):
            measured_on = models.DateField().to_python(measured_on)
        return measured_on - timedelta(days=measured_on.weekday())

    def _validate_attribution_accounts(self, *, previous=None):
        from apps.accounts.models import OrganizationMembership

        for field in ("original_observer", "imported_by"):
            account_id = getattr(self, f"{field}_id")
            if account_id is None:
                continue
            if previous and previous[f"{field}_id"] == account_id and previous["box_id"] == self.box_id:
                continue
            if not OrganizationMembership.objects.filter(
                user_id=account_id, organization_id=self.box.organization_id,
            ).exists():
                raise ValidationError({field: "The account must belong to the measurement institution."})

    def clean(self):
        super().clean()
        if self.box_id:
            self._validate_attribution_accounts()

    def save(self, *args, **kwargs):
        from apps.cultures.models import Box

        self.week_start = self.week_start_for(self.measured_on)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "measured_on" in update_fields:
            kwargs["update_fields"] = set(update_fields) | {"week_start"}
        # The interactive POST/PATCH already hold this lock. Keep imports and
        # admin writes on the same ordering protocol as well.
        with transaction.atomic(using=kwargs.get("using")):
            box = Box.objects.select_for_update().get(pk=self.box_id)
            if self.original_observer_id is not None or self.imported_by_id is not None:
                previous = None
                if not self._state.adding:
                    previous = type(self).objects.filter(pk=self.pk).values(
                        "box_id", "original_observer_id", "imported_by_id",
                    ).first()
                self._validate_attribution_accounts(previous=previous)
            box.polyp_state_revision += 1
            if self._state.adding:
                self.polyp_state_sequence = box.polyp_state_revision
            box.save(update_fields=["polyp_state_revision"])
            super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.box} - {self.measured_on}"


class ImmutableImportReceiptQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Accepted import evidence cannot be changed.")

    def delete(self):
        raise ValidationError("Accepted import evidence cannot be deleted.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Accepted import evidence cannot be changed.")


class MeasurementImportReceipt(models.Model):
    """Permanent private acceptance evidence, including the exact original bytes.

    No receipt is created for previews or rejected applications. JSON snapshots
    retain scientific identities even if live measurements are later corrected.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT,
        related_name="measurement_import_receipts",
    )
    # Keep the accepting account identity without mutable account name snapshots.
    actor_id = models.PositiveBigIntegerField()
    accepted_at = models.DateTimeField(default=timezone.now, editable=False)
    source_sha256 = models.CharField(max_length=64)
    source_bytes = models.BinaryField(editable=False)
    preparation_fingerprint = models.CharField(max_length=64)
    plan_fingerprint = models.CharField(max_length=64)
    preparation = models.JSONField()
    accepted_plan = models.JSONField()
    result = models.JSONField()

    objects = ImmutableImportReceiptQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "source_sha256", "preparation_fingerprint"],
                name="unique_measurement_import_acceptance",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Accepted import evidence cannot be changed.")
        # Never let an INSERT with an existing UUID fall back to an UPDATE.
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Accepted import evidence cannot be deleted.")


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
