from datetime import date, datetime, timedelta
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class BiologicalMeasurementQuerySet(models.QuerySet):
    def canonical(self):
        return self.filter(duplicate_of__isnull=True)

    def delete(self):
        self._for_write = True
        with transaction.atomic(using=self.db):
            # Lock a plain PK selection so distinct/search querysets keep their
            # delete semantics while a reviewed reclassification cannot race us.
            rows = list(self.model._base_manager.using(self.db).filter(
                pk__in=self.values("pk"),
            ).select_for_update().order_by("pk"))
            duplicates = [row for row in rows if row.duplicate_of_id is not None]
            if duplicates:
                raise ProtectedError(_("Duplicate measurement evidence cannot be deleted."), duplicates)
            result = models.QuerySet.delete(self.filter(pk__in=[row.pk for row in rows]))
            self._result_cache = None
            return result

    delete.alters_data = True
    delete.queryset_only = True


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
    duplicate_of = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True,
        related_name="duplicate_measurements", editable=False,
    )
    environment_source_box = models.ForeignKey(
        "cultures.Box", on_delete=models.PROTECT, null=True, blank=True,
        related_name="environment_source_measurements", editable=False,
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

    # Evidence remains visible to audits and reviewed operations. Scientific
    # readers must opt in to canonical(), including reverse-relation readers.
    objects = BiologicalMeasurementQuerySet.as_manager()

    class Meta:
        ordering = ["-measured_on", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["box", "week_start"],
                condition=models.Q(duplicate_of__isnull=True),
                name="unique_biological_measurement_per_box_week",
            ),
            models.CheckConstraint(
                condition=~models.Q(duplicate_of=models.F("pk")),
                name="measurement_duplicate_not_self",
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

    def _validate_evidence_relations(self):
        if self.environment_source_box_id is not None:
            if self.environment_source_box.organization_id != self.box.organization_id:
                raise ValidationError({
                    "environment_source_box": "The environment source must belong to the measurement institution.",
                })
        if self.duplicate_of_id is not None:
            target = self.duplicate_of
            if target.pk == self.pk or target.duplicate_of_id is not None:
                raise ValidationError({"duplicate_of": "A duplicate must reference a different canonical measurement."})
            if target.box.organization_id != self.box.organization_id:
                raise ValidationError({"duplicate_of": "The canonical measurement must belong to the same institution."})
            if target.week_start != self.week_start_for(self.measured_on):
                raise ValidationError({"duplicate_of": "The canonical measurement must belong to the same ISO week."})
            if self.pk and self.duplicate_measurements.exists():
                raise ValidationError({"duplicate_of": "A canonical measurement with duplicate evidence cannot become a duplicate."})
        elif self.pk and self.duplicate_measurements.exclude(
            week_start=self.week_start_for(self.measured_on),
            box__organization_id=self.box.organization_id,
        ).exists():
            raise ValidationError({"measured_on": "The correction would invalidate retained duplicate evidence."})

    def clean(self):
        super().clean()
        if self.box_id:
            self._validate_attribution_accounts()
            self._validate_evidence_relations()

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
            previous = None
            if self.pk is not None:
                previous = type(self).objects.select_for_update().filter(pk=self.pk).values(
                    "box_id", "original_observer_id", "imported_by_id", "duplicate_of_id",
                ).first()
                if previous and previous["duplicate_of_id"] is not None:
                    raise ValidationError(
                        _("Duplicate measurement evidence is read-only."), code="measurement_duplicate_read_only",
                    )
            self._validate_evidence_relations()
            if self.original_observer_id is not None or self.imported_by_id is not None:
                self._validate_attribution_accounts(previous=previous)
            box.polyp_state_revision += 1
            if self._state.adding:
                self.polyp_state_sequence = box.polyp_state_revision
            box.save(update_fields=["polyp_state_revision"])
            super().save(*args, **kwargs)

    def delete(self, using=None, keep_parents=False):
        using = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            current = type(self).objects.using(using).select_for_update().filter(pk=self.pk).first()
            if current is not None and current.duplicate_of_id is not None:
                raise ProtectedError(_("Duplicate measurement evidence cannot be deleted."), [current])
            return super().delete(using=using, keep_parents=keep_parents)

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
    # Identifies the remote channel collected for this probe, e.g. one channel of
    # an iMinilide data logger. Both are empty for probes that are not collected.
    external_serial = models.CharField(max_length=40, blank=True)
    external_channel = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"],
                name="unique_probe_per_organization",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(external_serial="", external_channel__isnull=True)
                    | (~models.Q(external_serial="") & models.Q(external_channel__isnull=False))
                ),
                name="probe_external_channel_complete",
            ),
            models.UniqueConstraint(
                fields=["probe_type", "external_serial", "external_channel"],
                condition=~models.Q(external_serial="") & models.Q(is_active=True),
                name="unique_probe_external_channel",
            ),
        ]

    def _validate_historical_zone(self, using, previous):
        if previous is not None and previous.thermal_zone_id != self.thermal_zone_id:
            has_history = (
                TemperatureMeasurement.objects.using(using).filter(probe_id=self.pk).exists()
                or ProbeTemperatureBucket.objects.using(using).filter(probe_id=self.pk).exists()
            )
            if has_history:
                raise ValidationError({
                    "thermal_zone": _(
                        "A probe with measurements cannot change thermal zone. "
                        "Use link_iminilide_probe --replace to preserve its history."
                    ),
                })

    def clean(self):
        super().clean()
        using = self._state.db or router.db_for_read(type(self), instance=self)
        previous = type(self).objects.using(using).filter(pk=self.pk).first() if self.pk else None
        self._validate_historical_zone(using, previous)

    def save(self, *args, **kwargs):
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            previous = type(self).objects.using(using).filter(pk=self.pk).first() if self.pk else None
            # Writers share the zone -> probe order with collection and replacement.
            from apps.cultures.models import ThermalZone
            zone_ids = {self.thermal_zone_id}
            if previous is not None:
                zone_ids.add(previous.thermal_zone_id)
            list(ThermalZone.objects.using(using).select_for_update().filter(
                pk__in=zone_ids,
            ).order_by("pk"))
            if previous is not None:
                previous = type(self).objects.using(using).select_for_update().get(pk=self.pk)
            self._validate_historical_zone(using, previous)
            return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} - {self.thermal_zone}"

    @property
    def is_collected(self):
        return bool(self.external_serial) and self.external_channel is not None


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


class ProbeTemperatureBucket(models.Model):
    """Condensed probe readings once per-minute rows age out.

    One bucket keeps the minimum, mean and maximum of the readings of one
    probe within a fixed period starting at ``period_start``.
    """

    probe = models.ForeignKey(Probe, on_delete=models.CASCADE, related_name="temperature_buckets")
    period_start = models.DateTimeField()
    period_minutes = models.PositiveSmallIntegerField()
    last_measured_at = models.DateTimeField()
    min_temperature_c = models.DecimalField(max_digits=5, decimal_places=2)
    average_temperature_c = models.DecimalField(max_digits=5, decimal_places=2)
    max_temperature_c = models.DecimalField(max_digits=5, decimal_places=2)
    measurement_count = models.PositiveIntegerField()
    # Keep the exact sum: the rounded mean cannot reconstruct it.
    temperature_sum_c = models.DecimalField(max_digits=18, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["period_start"]
        constraints = [
            models.UniqueConstraint(
                fields=["probe", "period_start"],
                name="unique_temperature_bucket_per_probe_period",
            ),
            models.CheckConstraint(
                condition=models.Q(measurement_count__gt=0) & models.Q(period_minutes__gt=0),
                name="temperature_bucket_not_empty",
            ),
        ]

    def __str__(self):
        return f"{self.probe} - {self.period_start:%Y-%m-%d %H:%M}"


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
    # True only while the whole day is recomputed from collected probe readings.
    # A day holding any manual or historical value keeps False and is never
    # overwritten by probe collection.
    computed_from_probes = models.BooleanField(default=False)

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


class HistoricalTemperatureQuerySet(models.QuerySet):
    """Require validated inserts and retain accepted historical associations."""

    def update(self, **kwargs):
        raise ValidationError("Historical temperature records cannot be changed.")

    def delete(self):
        raise ValidationError("Historical temperature records cannot be deleted.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("Historical temperature records cannot be changed.")

    def bulk_create(self, objs, *args, **kwargs):
        raise ValidationError("Historical temperature records require validated inserts.")


class HistoricalTemperatureImportReceipt(models.Model):
    """Private, durable approval, original workbooks and exact acceptance results."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT)
    # Match biological import receipts: retain identity even if an account is removed.
    actor_id = models.PositiveBigIntegerField()
    plan_fingerprint = models.CharField(max_length=64, unique=True)
    snapshot_sha256 = models.CharField(max_length=64)
    accepted_plan = models.JSONField()
    source_archive = models.BinaryField()
    results = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = HistoricalTemperatureQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Historical temperature receipts cannot be changed.")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Historical temperature receipts cannot be deleted.")


class HistoricalTemperatureReading(models.Model):
    """One week-precision reading; organization and Box exist only through biology.

    The ISO week is a source snapshot, not the biological observation's day or
    precision. Exact decimal text avoids fixed-scale database rounding.
    """

    class AssociationStatus(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmed"
        PENDING_REVIEW = "pending_review", "Pending review"

    biological_measurement = models.OneToOneField(
        BiologicalMeasurement, on_delete=models.PROTECT,
        related_name="historical_temperature",
    )
    association_status = models.CharField(
        max_length=20, choices=AssociationStatus.choices,
        default=AssociationStatus.CONFIRMED, editable=False,
    )
    iso_year = models.PositiveSmallIntegerField()
    iso_week = models.PositiveSmallIntegerField()
    temperature_c = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = HistoricalTemperatureQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(iso_year__gte=1, iso_year__lte=9999),
                name="historical_temp_year_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(association_status__in=["confirmed", "pending_review"]),
                name="historical_temp_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(iso_week__gte=1, iso_week__lte=53),
                name="historical_temp_week_valid",
            ),
        ]

    def clean(self):
        from .temperature_series import iso_week_bounds, temperature_decimal

        super().clean()
        try:
            temperature_decimal(self.temperature_c)
        except ValueError as exc:
            raise ValidationError({"temperature_c": str(exc)}) from exc
        try:
            first, _last = iso_week_bounds(self.iso_year, self.iso_week)
        except (ValueError, TypeError) as exc:
            raise ValidationError({"iso_week": "A valid ISO year and week are required."}) from exc
        if self._state.adding and self.association_status != self.AssociationStatus.CONFIRMED:
            raise ValidationError({"association_status": "New historical associations must be confirmed."})
        if self.biological_measurement_id and (
            self.biological_measurement.duplicate_of_id is not None
            or self.biological_measurement.box.merged_into_id is not None
        ):
            raise ValidationError({"biological_measurement": "Historical readings require canonical biology and Box identity."})
        if self.biological_measurement_id and self.association_status == self.AssociationStatus.CONFIRMED and (
            self.biological_measurement.week_start != first
            or BiologicalMeasurement.week_start_for(self.biological_measurement.measured_on) != first
        ):
            raise ValidationError({
                "biological_measurement": "The biological measurement must belong to the source ISO week.",
            })

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Historical temperature records cannot be changed.")
        # Validate before TextField coercion, which would conceal float input.
        self.clean()
        # A caller may have changed a cached related object without saving it.
        self.biological_measurement = BiologicalMeasurement.objects.db_manager(
            kwargs.get("using") or self._state.db,
        ).get(pk=self.biological_measurement_id)
        self.full_clean()
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Historical temperature records cannot be deleted.")


class HistoricalTemperatureEvidence(models.Model):
    """An original source association supporting an unchanged weekly reading.

    Source biological values are literal provenance, not instructions to change
    counts or to reinterpret reviewed source-label inversions.
    """

    reading = models.ForeignKey(
        HistoricalTemperatureReading, on_delete=models.PROTECT, related_name="evidences",
    )
    source_file = models.CharField(max_length=255)
    source_sha256 = models.CharField(max_length=64)
    sheet = models.CharField(max_length=255)
    temperature_cell = models.CharField(max_length=16)
    temperature_merged_range = models.CharField(max_length=35, blank=True, default="")
    source_value = models.TextField()
    source_box = models.TextField()
    polyp_cell = models.CharField(max_length=16)
    polyp_value = models.TextField()
    ephyrae_cell = models.CharField(max_length=16)
    ephyrae_value = models.TextField()
    week_cell = models.CharField(max_length=16)
    week_value = models.TextField()
    source_iso_year = models.PositiveSmallIntegerField()
    source_iso_week = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = HistoricalTemperatureQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["reading", "source_sha256", "sheet", "polyp_cell", "ephyrae_cell"],
                name="unique_historical_temp_evidence",
            ),
            models.CheckConstraint(
                condition=models.Q(source_iso_year__gte=1, source_iso_year__lte=9999),
                name="historical_temp_src_year_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(source_iso_week__gte=1, source_iso_week__lte=53),
                name="historical_temp_src_week_valid",
            ),
        ]

    def clean(self):
        import re
        from .historical_temperature_source import canonical_box
        from .historical_temperature_identity import temperature_source_matches
        from .temperature_series import iso_week_bounds, temperature_decimal

        super().clean()
        errors = {}
        if not isinstance(self.source_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.source_sha256):
            errors["source_sha256"] = "A lowercase SHA-256 digest is required."
        for field in ("temperature_cell", "polyp_cell", "ephyrae_cell", "week_cell"):
            value = getattr(self, field)
            pattern = r"C[1-9][0-9]*" if field == "temperature_cell" else r"[A-Z]+[1-9][0-9]*"
            if not isinstance(value, str) or not re.fullmatch(pattern, value):
                errors[field] = "An unqualified A1 cell reference is required (column C for temperature)."
        if self.temperature_merged_range:
            match = re.fullmatch(r"(C[1-9][0-9]*):C([1-9][0-9]*)", self.temperature_merged_range)
            if (not match or match[1] != self.temperature_cell
                    or int(match[2]) <= int(match[1][1:])):
                errors["temperature_merged_range"] = "The column C merge must start at the source cell."
        if not any(field in errors for field in (
            "temperature_cell", "temperature_merged_range", "polyp_cell", "ephyrae_cell", "week_cell",
        )):
            polyp = re.fullmatch(r"([A-Z]+)([0-9]+)", self.polyp_cell)
            ephyrae = re.fullmatch(r"([A-Z]+)([0-9]+)", self.ephyrae_cell)
            week = re.fullmatch(r"([A-Z]+)([0-9]+)", self.week_cell)
            start = int(self.temperature_cell[1:])
            end = int(self.temperature_merged_range.split(":")[1][1:]) if self.temperature_merged_range else None
            if (polyp[1] != ephyrae[1] or polyp[1] != week[1]
                    or int(ephyrae[2]) != int(polyp[2]) + 1
                    or (end is None and int(polyp[2]) != start)
                    or (end is not None and not (start <= int(polyp[2]) and int(ephyrae[2]) <= end))):
                errors["polyp_cell"] = "Biological source cells must be paired within the temperature block."
        try:
            source_temperature = temperature_decimal(self.source_value)
        except ValueError:
            errors["source_value"] = "A finite source decimal string is required."
        else:
            if self.reading_id and source_temperature != temperature_decimal(self.reading.temperature_c):
                errors["source_value"] = "Conflicting source temperatures cannot support one reading."
        try:
            iso_week_bounds(self.source_iso_year, self.source_iso_week)
        except (ValueError, TypeError):
            errors["source_iso_week"] = "A valid source ISO year and week are required."
        if self.reading_id and (self.source_iso_year, self.source_iso_week) != (
            self.reading.iso_year, self.reading.iso_week,
        ):
            errors["source_iso_week"] = "Source evidence must belong to the reading's ISO week."
        if self.reading_id:
            if self.reading.association_status != HistoricalTemperatureReading.AssociationStatus.CONFIRMED:
                errors["reading"] = "Pending associations cannot receive automatic source evidence."
            biology = self.reading.biological_measurement
            if biology.duplicate_of_id is not None or biology.box.merged_into_id is not None:
                errors["reading"] = "Historical evidence requires canonical biology and Box identity."
            if (not isinstance(self.source_box, str)
                    or not temperature_source_matches(canonical_box(self.source_box), biology.box.global_code)):
                errors["source_box"] = "The original source Box must match the linked Box identity."
            first, _last = iso_week_bounds(self.reading.iso_year, self.reading.iso_week)
            if (biology.week_start != first
                    or BiologicalMeasurement.week_start_for(biology.measured_on) != first):
                errors["reading"] = "The biological measurement must belong to the source ISO week."
            for field, expected in (("polyp_value", biology.polyp_count), ("ephyrae_value", biology.ephyrae_count)):
                try:
                    if temperature_decimal(getattr(self, field)) != expected:
                        errors[field] = "Source biological counts must match the linked measurement."
                except ValueError:
                    errors[field] = "A finite biological count string is required."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Historical temperature records cannot be changed.")
        self.clean()
        self.reading = HistoricalTemperatureReading.objects.db_manager(
            kwargs.get("using") or self._state.db,
        ).get(pk=self.reading_id)
        self.full_clean()
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Historical temperature records cannot be deleted.")
