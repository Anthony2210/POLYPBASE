import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, URLValidator
from django.db import models, transaction
from django.db.models import F, Q
from django.db.models.functions import Cast, TruncWeek
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class BoxDnaValidationQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError(_("DNA validations must be revoked, not deleted."))


class BoxDnaValidation(models.Model):
    box = models.ForeignKey(
        "cultures.Box",
        on_delete=models.PROTECT,
        related_name="dna_validations",
    )
    evidence_url = models.URLField(
        max_length=2000,
        validators=[URLValidator(schemes=["http", "https"])],
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_box_dna_validations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="updated_box_dna_validations",
    )
    updated_at = models.DateTimeField(auto_now=True)
    revision = models.PositiveBigIntegerField(default=1)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="revoked_box_dna_validations",
    )

    objects = BoxDnaValidationQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["box"],
                condition=Q(revoked_at__isnull=True),
                name="unique_active_box_dna_validation",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1),
                name="box_dna_revision_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(revoked_at__isnull=True, revoked_by__isnull=True)
                    | Q(revoked_at__isnull=False, revoked_by__isnull=False)
                ),
                name="box_dna_revocation_attribution",
            ),
        ]

    def save(self, *args, **kwargs):
        self.evidence_url = self.evidence_url.strip()
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_("DNA validations must be revoked, not deleted."))


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
    zone_type = models.CharField(
        max_length=30, choices=ZoneType.choices, default=ZoneType.CABINET
    )
    target_temperature_c = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True
    )
    capacity = models.PositiveIntegerField(null=True, blank=True)
    # Salinity of the water in this zone, maintained by hand like the capacity.
    # It is the reference shown on every box sheet of the zone; each measurement
    # can still record the salinity actually read for one box.
    salinity_psu = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
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


class BoxGlobalIdentityQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Box global identities are immutable.")


class BoxGlobalIdentity(models.Model):
    """Permanent identity of one physical Box, independent of any institution.

    Several institutional Box records may later reference the same identity
    (for example the same physical Box known by two institutions). A Global ID
    identifies; it never authorizes access to any Box record.
    """

    class AllocationReason(models.TextChoices):
        AUTOMATIC_CREATION = "automatic_creation", "Automatic creation"
        SUBCULTURE = "subculture", "Subculture child"
        INITIALIZED_AUTOMATIC_CREATION = (
            "initialized_automatic_creation",
            "Initialized from an automatic creation receipt",
        )
        INITIALIZED_SUBCULTURE = (
            "initialized_subculture",
            "Initialized from a subculture allocation",
        )

    global_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    allocation_reason = models.CharField(
        max_length=40, choices=AllocationReason.choices, editable=False
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    objects = BoxGlobalIdentityQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Box global identities are immutable.")
        return super().save(*args, **kwargs)

    def __str__(self):
        return str(self.global_id)


def new_box_global_identity(reason):
    """Allocate a fresh Global ID for a Box that is a new physical Box."""
    return BoxGlobalIdentity.objects.create(allocation_reason=reason)


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
    strain = models.ForeignKey(
        "taxonomy.Strain", on_delete=models.PROTECT, related_name="boxes"
    )
    origin = models.ForeignKey(
        "taxonomy.Origin", on_delete=models.SET_NULL, null=True, blank=True
    )
    thermal_zone = models.ForeignKey(
        ThermalZone,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="boxes",
    )
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.ACTIVE
    )
    created_on = models.DateField(auto_now_add=True)
    entered_on = models.DateField(null=True, blank=True)
    volume_liters = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    stop_reason = models.CharField(max_length=250, blank=True)
    stop_reason_missing_from_history = models.BooleanField(default=False)
    deactivated_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    # ISO weekday planned for the weekly record (1 = Monday ... 7 = Sunday).
    # NULL means no day is planned, never "any day".
    planned_weekday = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(7)],
    )
    polyp_state_revision = models.PositiveBigIntegerField(default=0, editable=False)
    merged_into = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="merged_sources",
        editable=False,
    )
    previous_global_code = models.CharField(max_length=100, blank=True, editable=False)
    # Despite its name, global_code is the institutional Local ID. The Global ID
    # is this permanent physical identity; NULL means not yet initialized, never
    # "a distinct Box". Once set it never changes (guarded in save and in SQL).
    global_identity = models.ForeignKey(
        BoxGlobalIdentity,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        to_field="global_id",
        db_column="global_id",
        related_name="boxes",
        editable=False,
    )

    _reviewed_identity_codes = frozenset(
        [f"CLA-JKA-1.{number:03}" for number in range(1, 11)]
        + [f"SME-JKA-1.{number:03}" for number in (1, 4, 5, 6, 7, 8, 9, 10)]
        + ["CLC-JKA-1.009", "CLC-JKA-1.010"]
    )

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        # Read the loaded dictionary only: deferred descriptors query recursively.
        if {"global_code", "strain_id"} <= instance.__dict__.keys():
            instance._loaded_identity = (
                instance.__dict__["global_code"],
                instance.__dict__["strain_id"],
            )
        else:
            instance._loaded_identity = None
        return instance

    def refresh_from_db(self, using=None, fields=None, **kwargs):
        super().refresh_from_db(using=using, fields=fields, **kwargs)
        if fields is None or (
            "global_code" in fields and ("strain" in fields or "strain_id" in fields)
        ):
            if {"global_code", "strain_id"} <= self.__dict__.keys():
                self._loaded_identity = (
                    self.__dict__["global_code"],
                    self.__dict__["strain_id"],
                )

    def save(self, *args, **kwargs):
        from .box_codes import (
            lock_local_id_registry,
            register_box_code,
            reserve_reconciled_box_identity,
        )

        # Recheck retirement after waiting for the same lock as laboratory
        # writers. A stale model instance must never resurrect an alias.
        with transaction.atomic(using=kwargs.get("using")):
            lock_local_id_registry(using=kwargs.get("using"))
            current = None
            if not self._state.adding or self.pk is not None:
                rows = (
                    type(self)
                    .objects.select_for_update()
                    .only(
                        "merged_into",
                        "previous_global_code",
                        "global_code",
                        "strain_id",
                        "global_identity",
                        "organization_id",
                    )
                )
                current = (
                    rows.filter(pk=self.pk).first()
                    if self._state.adding
                    else rows.get(pk=self.pk)
                )
            adding = current is None
            if not adding:
                if current.merged_into_id is not None:
                    raise ValidationError(_("A merged identity is read-only."))
                if current.previous_global_code != self.previous_global_code:
                    raise ValidationError(
                        _("The culture identity changed. Reload the culture.")
                    )
            update_fields = kwargs.get("update_fields")
            if not adding and update_fields is None:
                # A stale instance must never clear or replace a Global ID, nor
                # restore an older planned weekday: that field changes only
                # through set_box_planned_weekday, with explicit update_fields.
                update_fields = {
                    field.name
                    for field in self._meta.concrete_fields
                    if not field.primary_key
                    and field.name
                    not in {
                        "polyp_state_revision",
                        "global_identity",
                        "planned_weekday",
                    }
                }
                kwargs["update_fields"] = update_fields
            if not adding and {"global_identity", "global_identity_id"}.intersection(
                update_fields
            ):
                if (
                    current.global_identity_id is not None
                    and self.global_identity_id != current.global_identity_id
                ):
                    raise ValidationError("A box Global ID is permanent.")
            if not adding:
                reassigns = {"organization", "organization_id"}.intersection(
                    update_fields
                ) and self.organization_id != current.organization_id
                becomes_alias = {"merged_into", "merged_into_id"}.intersection(
                    update_fields
                ) and self.merged_into_id is not None
                if (reassigns or becomes_alias) and BoxOwnershipEpisode.objects.filter(
                    local_box_id=self.pk,
                ).exists():
                    # A representation in a global ownership chain keeps its
                    # institution and stays a living Box; a new owner receives
                    # its own local Box. Technical guard (admin/ORM only).
                    raise ValidationError(
                        "A Box in an ownership history cannot change institution or become an alias."
                    )
            identity_fields = {"global_code", "strain", "strain_id"}
            writes_identity = adding or bool(
                identity_fields.intersection(update_fields)
            )
            if not adding and writes_identity:
                persisted_identity = (current.global_code, current.strain_id)
                loaded_identity = getattr(self, "_loaded_identity", None)
                codes = {current.global_code, self.__dict__.get("global_code")}
                if loaded_identity is not None:
                    codes.add(loaded_identity[0])
                if codes.intersection(self._reviewed_identity_codes):
                    # An object constructed with a PK or missing deferred identity
                    # has no trusted original: only unchanged identity writes are safe.
                    if loaded_identity is None:
                        proposed_identity = (
                            self.global_code
                            if "global_code" in update_fields
                            else current.global_code,
                            self.strain_id
                            if {"strain", "strain_id"}.intersection(update_fields)
                            else current.strain_id,
                        )
                        changed = proposed_identity != persisted_identity
                    else:
                        changed = loaded_identity != persisted_identity
                    if changed:
                        raise ValidationError(
                            _("The culture identity changed. Reload the culture.")
                        )
            if adding or "global_code" in update_fields:
                if adding or self.global_code != current.global_code:
                    reserve_reconciled_box_identity(self.global_code)
                register_box_code(self.global_code, namespace=self.strain.code)
                if adding or self.global_code != current.global_code:
                    from .box_aliases import validate_local_id_claim

                    # Keep duplicate-current-code errors on the existing unique
                    # constraint; the advisory guard covers historical claims.
                    validate_local_id_claim(
                        self.global_code, box=self, check_current=False
                    )
            result = super().save(*args, **kwargs)
            if writes_identity:
                saved_identity = (
                    self.global_code
                    if adding or "global_code" in update_fields
                    else current.global_code,
                    self.strain_id
                    if adding or {"strain", "strain_id"}.intersection(update_fields)
                    else current.strain_id,
                )
                # A partial write must not bless conflicting unsaved attributes.
                # Read the dictionary without loading any deferred identity field.
                instance_identity = (
                    self.__dict__.get("global_code"),
                    self.__dict__.get("strain_id"),
                )
                self._loaded_identity = (
                    saved_identity if instance_identity == saved_identity else None
                )
            return result

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(merged_into__isnull=True)
                | (
                    Q(status="inactive", thermal_zone__isnull=True)
                    & ~Q(merged_into=models.F("pk"))
                ),
                name="merged_box_is_retired",
            ),
            # Explicit membership, not a range: SQLite would keep 1.5 as REAL.
            models.CheckConstraint(
                condition=Q(planned_weekday__isnull=True)
                | Q(planned_weekday__in=[1, 2, 3, 4, 5, 6, 7]),
                name="box_planned_weekday_iso_range",
            ),
            # One live record per physical Box within an institution; retired
            # merge aliases may share the identity of their terminal record.
            models.UniqueConstraint(
                fields=["organization", "global_identity"],
                condition=Q(merged_into__isnull=True, global_identity__isnull=False),
                name="unique_live_box_global_identity_per_organization",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["global_code"]),
            models.Index(fields=["local_code"]),
        ]

    def __str__(self):
        return f"{self.global_code} - {self.box_number}"


class BoxLocalIdAliasQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Box Local ID aliases are immutable.")

    def delete(self):
        raise ValidationError("Box Local ID aliases are immutable.")


class BoxLocalIdAlias(models.Model):
    """An explicitly confirmed old Local ID, attached permanently to its source.

    Organization is derived from box. Resolution of merged sources belongs to
    the resolver, not this registry. A shared physical Global ID is not evidence
    that two institutional records may share a Local ID.
    """

    code = models.CharField(max_length=100, unique=True, editable=False)
    box = models.ForeignKey(
        Box, on_delete=models.PROTECT, related_name="local_id_aliases", editable=False
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    objects = BoxLocalIdAliasQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~Q(code=""), name="box_local_id_alias_not_empty"
            ),
        ]

    @property
    def organization_id(self):
        return self.box.organization_id

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Box Local ID aliases are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Box Local ID aliases are immutable.")

    def __str__(self):
        return self.code


class AutomaticBoxCreationRequest(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    request_id = models.UUIDField()
    intent = models.JSONField(null=True, blank=True)
    resume_token_hash = models.CharField(
        max_length=64, unique=True, null=True, blank=True
    )
    failure = models.JSONField(null=True, blank=True)
    cancelled = models.BooleanField(default=False)
    box = models.OneToOneField(
        Box,
        on_delete=models.PROTECT,
        related_name="automatic_creation_request",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "actor", "request_id"],
                name="unique_automatic_box_request",
            ),
            models.CheckConstraint(
                condition=(
                    (
                        Q(resume_token_hash__isnull=True, failure__isnull=True)
                        & (
                            Q(cancelled=True, box__isnull=True, intent__isnull=True)
                            | Q(
                                cancelled=False, box__isnull=False, intent__isnull=False
                            )
                        )
                    )
                    | (
                        Q(resume_token_hash__isnull=False, intent__isnull=False)
                        & (
                            Q(cancelled=False, failure__isnull=True)
                            | Q(cancelled=True, box__isnull=True)
                        )
                    )
                ),
                name="automatic_box_request_coherent_state",
            ),
        ]


class BoxLocation(models.Model):
    box = models.ForeignKey(Box, on_delete=models.CASCADE, related_name="locations")
    thermal_zone = models.ForeignKey(
        ThermalZone, on_delete=models.PROTECT, related_name="box_locations"
    )
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
            raise ValidationError(
                "A location with an unknown end date cannot also have an end date."
            )

    def __str__(self):
        return f"{self.box} in {self.thermal_zone}"


class HistoricalBoxMovementQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Historical movement reconstructions are read-only.")

    def delete(self):
        raise ValidationError("Historical movement reconstructions cannot be deleted.")


class HistoricalBoxMovement(models.Model):
    """Reviewed week-only reconstruction, never an exact physical timestamp."""

    box = models.ForeignKey(
        Box, on_delete=models.PROTECT, related_name="historical_movements"
    )
    iso_year = models.PositiveSmallIntegerField()
    iso_week = models.PositiveSmallIntegerField()
    from_thermal_zone = models.ForeignKey(
        ThermalZone, on_delete=models.PROTECT, related_name="historical_departures"
    )
    to_thermal_zone = models.ForeignKey(
        ThermalZone, on_delete=models.PROTECT, related_name="historical_arrivals"
    )
    origin_location = models.ForeignKey(
        BoxLocation, on_delete=models.PROTECT, related_name="historical_departures"
    )
    destination_location = models.ForeignKey(
        BoxLocation, on_delete=models.PROTECT, related_name="historical_arrivals"
    )
    candidate_key = models.CharField(max_length=64, unique=True, editable=False)
    plan_fingerprint = models.CharField(max_length=64, editable=False)
    evidence = models.JSONField(default=dict, editable=False)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, editable=False
    )
    accepted_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    objects = HistoricalBoxMovementQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["box", "iso_year", "iso_week"],
                name="unique_historical_box_movement_week",
            ),
            models.CheckConstraint(
                condition=Q(iso_year__gte=1, iso_year__lte=9999),
                name="historical_movement_year_valid",
            ),
            models.CheckConstraint(
                condition=Q(iso_week__gte=1, iso_week__lte=53),
                name="historical_movement_week_valid",
            ),
            models.CheckConstraint(
                condition=~Q(from_thermal_zone=F("to_thermal_zone")),
                name="historical_movement_distinct_zones",
            ),
        ]

    def clean(self):
        from datetime import date

        super().clean()
        try:
            date.fromisocalendar(self.iso_year, self.iso_week, 1)
        except (ValueError, TypeError) as exc:
            raise ValidationError("A valid ISO week is required.") from exc
        for zone in (self.from_thermal_zone, self.to_thermal_zone):
            if zone.organization_id != self.box.organization_id:
                raise ValidationError(
                    "Historical movement zones must belong to the box institution."
                )
        for location, zone_id in (
            (self.origin_location, self.from_thermal_zone_id),
            (self.destination_location, self.to_thermal_zone_id),
        ):
            if location.box_id != self.box_id or location.thermal_zone_id != zone_id:
                raise ValidationError(
                    "Historical movement period evidence does not match."
                )

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Historical movement reconstructions are read-only.")
        self.full_clean()
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Historical movement reconstructions cannot be deleted.")


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


# Ordinary movements are limited per ISO week of the laboratory calendar,
# independently of the server time zone setting.
MOVEMENT_WEEK_TIME_ZONE = ZoneInfo("Europe/Paris")
_MOVEMENT_WEEK_FIELDS = {"moved_at", "kind", "iso_week_start"}


class ParisMovementWeekStart(Cast):
    """Migration-serializable Paris-local Monday expression; keep for migrations."""

    def __init__(self, expression):
        super().__init__(
            TruncWeek(expression, tzinfo=MOVEMENT_WEEK_TIME_ZONE),
            output_field=models.DateField(),
        )


class BoxMovementQuerySet(models.QuerySet):
    def update(self, **kwargs):
        # The weekly key is derived in save(); bulk writes would bypass it.
        if _MOVEMENT_WEEK_FIELDS & set(kwargs):
            raise ValidationError(
                "Box movement date and kind can only change through save() or the correction service."
            )
        return super().update(**kwargs)


class BoxMovement(models.Model):
    class Kind(models.TextChoices):
        # Explicit transfer between two zones; limited to one per ISO week.
        ORDINARY = "ordinary", _("Déplacement")
        # First location of an active box without a current location.
        INITIAL_LOCATION = "initial_location", _("Premier emplacement")
        # Location assigned while qualifying a historical box.
        QUALIFICATION = "qualification", _("Emplacement à la qualification")
        # Location opened by the reactivation of an inactive box.
        REACTIVATION = "reactivation", _("Réactivation")
        # Provenance not established: rows written before kinds existed, or
        # directly outside the movement service. Never inferred afterwards.
        UNCLASSIFIED = "unclassified", _("Provenance non établie")

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
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    notes = models.TextField(blank=True)
    kind = models.CharField(
        max_length=30, choices=Kind.choices, default=Kind.UNCLASSIFIED
    )
    # Paris-local Monday of moved_at, stored for ordinary movements only.
    iso_week_start = models.DateField(null=True, blank=True, editable=False)
    # Incremented by each correction; clients send it back to detect stale forms.
    revision = models.PositiveIntegerField(default=0, editable=False)
    # Periods written together with the movement. NULL means the relation was
    # not recorded (legacy rows) or was ambiguous; it is never reconstructed.
    origin_location = models.OneToOneField(
        BoxLocation,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        editable=False,
        related_name="closing_movement",
    )
    destination_location = models.OneToOneField(
        BoxLocation,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        editable=False,
        related_name="opening_movement",
    )

    objects = BoxMovementQuerySet.as_manager()

    class Meta:
        ordering = ["-moved_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    kind__in=[
                        "ordinary",
                        "initial_location",
                        "qualification",
                        "reactivation",
                        "unclassified",
                    ]
                ),
                name="box_movement_kind_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(kind="ordinary", iso_week_start__isnull=False)
                    | (~Q(kind="ordinary") & Q(iso_week_start__isnull=True))
                ),
                name="box_movement_week_only_for_ordinary",
            ),
            # Also cover writes that bypass save(): bulk inserts, fixtures and SQL.
            models.CheckConstraint(
                condition=(
                    ~Q(kind="ordinary")
                    | Q(iso_week_start=ParisMovementWeekStart("moved_at"))
                ),
                name="box_movement_week_matches_moved_at",
            ),
            models.UniqueConstraint(
                fields=["box", "iso_week_start"],
                condition=Q(kind="ordinary"),
                name="unique_ordinary_box_movement_per_iso_week",
            ),
        ]

    @staticmethod
    def week_start_for(moved_at):
        """Return the Monday of the Europe/Paris ISO week containing moved_at."""
        if timezone.is_naive(moved_at):
            moved_at = timezone.make_aware(moved_at)
        local_date = moved_at.astimezone(MOVEMENT_WEEK_TIME_ZONE).date()
        return local_date - timedelta(days=local_date.weekday())

    def save(self, *args, **kwargs):
        self.iso_week_start = (
            self.week_start_for(self.moved_at)
            if self.kind == self.Kind.ORDINARY
            else None
        )
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"iso_week_start"}
        return super().save(*args, **kwargs)

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
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
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
        if (
            self.pk
            and type(self)
            .objects.filter(pk=self.pk, occurred_at__isnull=False)
            .exists()
        ):
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
                    Q(
                        occurred_at__isnull=True,
                        parent_state_sequence__isnull=True,
                        parent_polyp_count_before__isnull=True,
                        allocated_polyp_count__isnull=True,
                        parent_polyp_count_after__isnull=True,
                        parent_state_snapshot__isnull=True,
                    )
                    | Q(
                        occurred_at__isnull=False,
                        parent_state_sequence__isnull=False,
                        parent_polyp_count_before__isnull=False,
                        allocated_polyp_count__isnull=False,
                        parent_polyp_count_after__isnull=False,
                        parent_state_snapshot__isnull=False,
                        parent_polyp_count_before=models.F("allocated_polyp_count")
                        + models.F("parent_polyp_count_after"),
                    )
                    | Q(
                        occurred_at__isnull=False,
                        parent_state_sequence__isnull=False,
                        parent_polyp_count_before__isnull=False,
                        allocated_polyp_count__isnull=True,
                        parent_polyp_count_after__isnull=True,
                        parent_state_snapshot__isnull=False,
                    )
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
    event = models.ForeignKey(
        SubcultureEvent, on_delete=models.PROTECT, related_name="allocations"
    )
    child_box = models.OneToOneField(
        Box, on_delete=models.PROTECT, related_name="subculture_initialization"
    )
    position = models.PositiveIntegerField()
    allocated_polyps = models.PositiveIntegerField(null=True)
    child_state_sequence = models.PositiveBigIntegerField(default=1, editable=False)
    child_global_code = models.CharField(max_length=100)

    objects = ImmutableAllocationQuerySet.as_manager()

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "position"],
                name="unique_subculture_allocation_position",
            ),
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
        PortableLineageNode,
        on_delete=models.PROTECT,
        related_name="outgoing_edges",
    )
    target_node = models.ForeignKey(
        PortableLineageNode,
        on_delete=models.PROTECT,
        related_name="incoming_edges",
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
                condition=Q(
                    relationship_type__in=[
                        "subculture",
                        "sexual_reproduction",
                        "historical_import",
                        "other",
                        "transfer",
                    ]
                ),
                name="portable_edge_type_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        relationship_type="transfer",
                        transfer_id__isnull=False,
                        item_id__isnull=False,
                    )
                    | (
                        ~Q(relationship_type="transfer")
                        & Q(transfer_id__isnull=True, item_id__isnull=True)
                    )
                ),
                name="portable_edge_provenance_valid",
            ),
            models.CheckConstraint(
                condition=~Q(relationship_type="transfer")
                | Q(local_lineage__isnull=True),
                name="portable_transfer_no_local_lineage",
            ),
        ]


class IdentificationTag(models.Model):
    class TagType(models.TextChoices):
        QR = "qr", "QR code"
        NFC = "nfc", "NFC"
        RFID = "rfid", "RFID"

    tag_type = models.CharField(
        max_length=20, choices=TagType.choices, default=TagType.QR
    )
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
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PLANNED
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-transfer_date"]

    def __str__(self):
        return f"{self.box} from {self.from_organization} to {self.to_organization}"


class TransferEnvelope(models.Model):
    """Source-owned v2 package. Portable content uses only frozen snapshots."""

    transfer_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    source_organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="transfer_envelopes",
        editable=False,
    )
    source_institution_id = models.UUIDField(editable=False)
    source_institution_name = models.CharField(max_length=150, editable=False)
    destination_institution_id = models.UUIDField(null=True, blank=True, editable=False)
    destination_institution_name = models.CharField(
        max_length=150,
        blank=True,
        default="",
        editable=False,
    )
    protocol_major = models.PositiveIntegerField(default=2, editable=False)
    protocol_minor = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transfer_envelopes",
        editable=False,
    )


class TransferItem(models.Model):
    """One source selection; portable identity is (transfer_id, item_id)."""

    envelope = models.ForeignKey(
        TransferEnvelope,
        on_delete=models.PROTECT,
        related_name="items",
        editable=False,
    )
    item_id = models.UUIDField(default=uuid.uuid4, editable=False)
    source_box = models.ForeignKey(
        Box,
        on_delete=models.PROTECT,
        related_name="transfer_items",
        editable=False,
    )
    source_box_code = models.CharField(max_length=100, editable=False)
    source_strain_code = models.CharField(max_length=80, editable=False)
    species_scientific_name = models.CharField(max_length=150, editable=False)
    global_strain_id = models.UUIDField(editable=False)
    declared_polyp_quantity = models.PositiveIntegerField(editable=False)
    lineage_snapshot = models.JSONField(
        null=True, blank=True, editable=False, default=None
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["envelope", "item_id"],
                name="unique_transfer_item_per_envelope",
            ),
            models.CheckConstraint(
                condition=Q(declared_polyp_quantity__gte=0),
                name="transfer_item_quantity_nonnegative",
            ),
        ]


class ProtectedBoxIdentityQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Global Box identities are immutable.")

    def delete(self):
        raise ValidationError("Global Box identities are protected.")


class GlobalBoxIdentity(models.Model):
    """The same biological Box across successive institutional representations.

    Distinct from Box.pk, Box.global_code, GlobalStrainIdentity and portable
    lineage nodes. It grants no permission and is never an operational lookup key.
    """

    global_box_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    objects = ProtectedBoxIdentityQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Global Box identities are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Global Box identities are protected.")

    def __str__(self):
        return str(self.global_box_id)


class ProtectedOwnershipEpisodeQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError(
            "Box ownership episodes change only through validated saves."
        )

    def delete(self):
        raise ValidationError("Box ownership history is protected.")


class BoxOwnershipEpisode(models.Model):
    """One institution's local representation of a GlobalBoxIdentity.

    Episodes form a gapless chronological sequence per identity. A return to a
    previous institution appends a new episode with a new local Box; earlier
    episodes are never rebound. Sequence 1 is the origin representation and has
    no arrival provenance; later episodes cite the Transfer v2 item that carried
    the Box. The current operational owner is the single episode without end.
    """

    identity = models.ForeignKey(
        GlobalBoxIdentity,
        on_delete=models.PROTECT,
        related_name="ownership_episodes",
        editable=False,
    )
    sequence = models.PositiveIntegerField(editable=False)
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="box_ownership_episodes",
        editable=False,
    )
    local_box = models.OneToOneField(
        Box,
        on_delete=models.PROTECT,
        related_name="ownership_episode",
        editable=False,
    )
    # NULL means unknown, never reconstructed: an origin episode recorded for
    # an existing Box does not know when that institution acquired it.
    started_at = models.DateTimeField(null=True, blank=True, editable=False)
    ended_at = models.DateTimeField(null=True, blank=True, editable=False)
    # Portable Transfer v2 provenance (TransferEnvelope.transfer_id,
    # TransferItem.item_id). UUIDs, not FKs: the source package may live in
    # another installation.
    arrival_transfer_id = models.UUIDField(null=True, blank=True, editable=False)
    arrival_item_id = models.UUIDField(null=True, blank=True, editable=False)
    recorded_at = models.DateTimeField(default=timezone.now, editable=False)

    objects = ProtectedOwnershipEpisodeQuerySet.as_manager()

    _closable_fields = frozenset({"ended_at"})

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["identity", "sequence"],
                name="box_ownership_sequence_unique",
            ),
            models.UniqueConstraint(
                fields=["identity"],
                condition=Q(ended_at__isnull=True),
                name="box_ownership_single_current",
            ),
            models.UniqueConstraint(
                fields=["arrival_transfer_id", "arrival_item_id"],
                condition=Q(arrival_transfer_id__isnull=False),
                name="box_ownership_arrival_unique",
            ),
            models.CheckConstraint(
                condition=Q(sequence__gte=1),
                name="box_ownership_sequence_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        sequence=1,
                        arrival_transfer_id__isnull=True,
                        arrival_item_id__isnull=True,
                    )
                    | Q(
                        sequence__gt=1,
                        arrival_transfer_id__isnull=False,
                        arrival_item_id__isnull=False,
                        started_at__isnull=False,
                    )
                ),
                name="box_ownership_arrival_provenance",
            ),
            models.CheckConstraint(
                condition=Q(started_at__isnull=True)
                | Q(ended_at__isnull=True)
                | Q(ended_at__gte=F("started_at")),
                name="box_ownership_chronology",
            ),
        ]
        indexes = [models.Index(fields=["organization", "ended_at"])]

    def save(self, *args, **kwargs):
        with transaction.atomic(using=kwargs.get("using")):
            # One identity lock serializes every writer of the same chain.
            GlobalBoxIdentity.objects.select_for_update().only("pk").get(
                pk=self.identity_id
            )
            if self._state.adding:
                self._validate_new_episode()
            else:
                self._validate_closing(kwargs.get("update_fields"))
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Box ownership history is protected.")

    def _validate_new_episode(self):
        box = (
            Box.objects.select_for_update()
            .only("organization_id", "merged_into_id")
            .get(pk=self.local_box_id)
        )
        if box.organization_id != self.organization_id:
            raise ValidationError(
                "A local representation must belong to the episode institution."
            )
        if box.merged_into_id is not None:
            raise ValidationError(
                "A merged identity alias cannot represent a biological Box."
            )
        if self.ended_at is not None:
            raise ValidationError(
                "A new ownership episode starts as the current owner."
            )
        previous = (
            type(self)
            .objects.filter(identity_id=self.identity_id)
            .order_by("-sequence")
            .first()
        )
        expected = 1 if previous is None else previous.sequence + 1
        if self.sequence != expected:
            raise ValidationError(
                "Ownership episodes must extend the chain without gaps."
            )
        if previous is None:
            return
        if previous.organization_id == self.organization_id:
            raise ValidationError("An ownership transfer must change institution.")
        if previous.ended_at is None or previous.ended_at != self.started_at:
            raise ValidationError(
                "The previous owner must end exactly when the next owner starts."
            )

    def _validate_closing(self, update_fields):
        current = type(self).objects.select_for_update().get(pk=self.pk)
        if update_fields is None or set(update_fields) - self._closable_fields:
            raise ValidationError(
                "Only the end of an ownership episode can be recorded."
            )
        if current.ended_at is not None:
            raise ValidationError("An ended ownership episode is immutable.")
        if self.ended_at is None:
            raise ValidationError("Closing an ownership episode requires its end.")
        if current.started_at is not None and self.ended_at < current.started_at:
            raise ValidationError("An ownership episode cannot end before it starts.")


class ProtectedDonationProposalQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError(
            "Box donation proposals change only through validated saves."
        )

    def delete(self):
        raise ValidationError("Box donation proposals are protected.")


class BoxDonationProposal(models.Model):
    """A source institution's pending offer to donate one whole biological Box.

    A proposal is not a transfer: it changes no ownership, creates no
    destination Box, TransferEnvelope, measurement, movement or stock effect,
    and grants the destination no read access to the source history. The
    source keeps full operational control of its Box while it is pending.
    Snapshots are frozen at issue time; only cancellation fields may change.
    """

    class State(models.TextChoices):
        PENDING = "pending"
        CANCELLED = "cancelled"

    proposal_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    source_organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="outgoing_box_donation_proposals",
        editable=False,
    )
    destination_organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="incoming_box_donation_proposals",
        editable=False,
    )
    source_box = models.ForeignKey(
        Box,
        on_delete=models.PROTECT,
        related_name="donation_proposals",
        editable=False,
    )
    identity = models.ForeignKey(
        GlobalBoxIdentity,
        on_delete=models.PROTECT,
        related_name="donation_proposals",
        editable=False,
    )
    # The current ownership episode at issue time. Acceptance must find it
    # still current (stale/replay control), as for a future protocol item.
    source_episode = models.ForeignKey(
        BoxOwnershipEpisode,
        on_delete=models.PROTECT,
        related_name="donation_proposals",
        editable=False,
    )
    source_ownership_sequence = models.PositiveIntegerField(editable=False)
    source_institution_name = models.CharField(max_length=150, editable=False)
    destination_institution_name = models.CharField(max_length=150, editable=False)
    source_box_code = models.CharField(max_length=100, editable=False)
    source_strain_code = models.CharField(max_length=80, editable=False)
    species_scientific_name = models.CharField(max_length=150, editable=False)
    global_strain_id = models.UUIDField(editable=False)
    # Explicit sender confirmation of having reviewed the history, bound to
    # the SHA-256 of the scientific snapshot it described. It is not proof
    # that the measurements are scientifically correct.
    history_verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="verified_box_donation_histories",
        editable=False,
    )
    history_verified_at = models.DateTimeField(editable=False)
    history_snapshot_version = models.CharField(max_length=20, editable=False)
    history_sha256 = models.CharField(max_length=64, editable=False)
    source_polyp_state_revision = models.PositiveBigIntegerField(editable=False)
    state = models.CharField(
        max_length=20, choices=State.choices, default=State.PENDING, editable=False
    )
    revision = models.PositiveIntegerField(default=1, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_box_donation_proposals",
        editable=False,
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    # Server-side idempotency: one request key per source institution and actor.
    request_id = models.UUIDField(editable=False)
    request_fingerprint = models.CharField(max_length=64, editable=False)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cancelled_box_donation_proposals",
        editable=False,
    )
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)

    objects = ProtectedDonationProposalQuerySet.as_manager()

    _cancellation_fields = frozenset(
        {"state", "revision", "cancelled_by", "cancelled_at"}
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["identity"],
                condition=Q(state="pending"),
                name="box_donation_single_pending",
            ),
            models.UniqueConstraint(
                fields=["source_organization", "created_by", "request_id"],
                name="box_donation_request_unique",
            ),
            models.CheckConstraint(
                condition=~Q(source_organization=F("destination_organization")),
                name="box_donation_distinct_institutions",
            ),
            models.CheckConstraint(
                condition=Q(revision__gte=1) & Q(source_ownership_sequence__gte=1),
                name="box_donation_positive_counters",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        state="pending",
                        cancelled_by__isnull=True,
                        cancelled_at__isnull=True,
                    )
                    | Q(
                        state="cancelled",
                        cancelled_by__isnull=False,
                        cancelled_at__isnull=False,
                        revision__gte=2,
                    )
                ),
                name="box_donation_coherent_state",
            ),
        ]
        indexes = [
            models.Index(fields=["source_organization", "created_at"]),
            models.Index(fields=["destination_organization", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        with transaction.atomic(using=kwargs.get("using")):
            if self._state.adding:
                if self.state != self.State.PENDING or self.revision != 1:
                    raise ValidationError(
                        "A donation proposal is issued pending at revision 1."
                    )
                return super().save(*args, **kwargs)
            update_fields = kwargs.get("update_fields")
            if update_fields is None or set(update_fields) - self._cancellation_fields:
                raise ValidationError(
                    "Only the cancellation of a donation proposal can be recorded."
                )
            current = type(self).objects.select_for_update().get(pk=self.pk)
            if (
                current.state != self.State.PENDING
                or self.state != self.State.CANCELLED
            ):
                raise ValidationError(
                    "Only a pending donation proposal can be cancelled."
                )
            if self.revision != current.revision + 1:
                raise ValidationError(
                    "A cancellation must advance the proposal revision by one."
                )
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Box donation proposals are protected.")


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
                fields=[
                    "format_version",
                    "source_organization_name",
                    "source_transfer_id",
                ],
                name="unique_imported_box_transfer",
            )
        ]

    def __str__(self):
        return f"{self.source_global_code} -> {self.created_box.global_code}"


from .history_revision_models import BoxHistoryEventIdentity, BoxHistoryRevision, BoxHistoryStream  # noqa: E402, F401  # fmt: skip
