from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from apps.accounts.models import UserPreference
from apps.audit.models import Alert, AuditLog
from apps.cultures import qr
from apps.cultures.models import (
    Box,
    BoxLocation,
    BoxMovement,
    BoxTransfer,
    IdentificationTag,
    SubcultureEvent,
    ThermalZone,
)
from apps.measurements.models import BiologicalMeasurement, Probe
from apps.organizations.models import Organization
from apps.organizations.serializers import OrganizationSummarySerializer
from apps.taxonomy.models import Species, Strain


#: Salinity can reach the serializer as a Decimal (model field, PostgreSQL) or
#: as a float (subquery annotation on SQLite). Render it through a real
#: DecimalField so the API always answers the same way ("35.00"), whatever the
#: database.
_SALINITY_FIELD = serializers.DecimalField(max_digits=5, decimal_places=2)


def _render_salinity(value):
    if value is None:
        return None
    return _SALINITY_FIELD.to_representation(Decimal(str(value)))


class SpeciesSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Species
        fields = ["id", "scientific_name", "common_name", "genus_species_code"]


class StrainSummarySerializer(serializers.ModelSerializer):
    species = SpeciesSummarySerializer(read_only=True)

    class Meta:
        model = Strain
        fields = ["id", "code", "number", "origin_code", "species"]


class ThermalZoneSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = ThermalZone
        fields = ["id", "name", "zone_type", "target_temperature_c", "is_active"]


class IdentificationTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = IdentificationTag
        fields = ["id", "tag_type", "code", "url", "is_active"]


class BoxLocationSerializer(serializers.ModelSerializer):
    thermal_zone = ThermalZoneSummarySerializer(read_only=True)

    class Meta:
        model = BoxLocation
        fields = ["id", "thermal_zone", "starts_at", "ends_at", "notes"]


class BoxMovementSerializer(serializers.ModelSerializer):
    from_thermal_zone = ThermalZoneSummarySerializer(read_only=True)
    to_thermal_zone = ThermalZoneSummarySerializer(read_only=True)
    user = serializers.SerializerMethodField()

    class Meta:
        model = BoxMovement
        fields = ["id", "from_thermal_zone", "to_thermal_zone", "moved_at", "notes", "user"]

    def get_user(self, obj):
        return obj.user.get_username() if obj.user else None


class AlertSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Alert
        fields = ["id", "alert_type", "level", "message", "created_at"]


class BiologicalMeasurementSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()

    class Meta:
        model = BiologicalMeasurement
        fields = [
            "id",
            "measured_on",
            "polyp_count",
            "ephyrae_count",
            "strobila_count",
            "salinity_psu",
            "culture_status",
            "needs_attention",
            "notes",
            "user",
            "created_at",
        ]
        read_only_fields = ["id", "user", "created_at"]

    def get_user(self, obj):
        return obj.user.get_username() if obj.user else None


class BoxListSerializer(serializers.ModelSerializer):
    organization = OrganizationSummarySerializer(read_only=True)
    species = serializers.SerializerMethodField()
    strain = StrainSummarySerializer(read_only=True)
    thermal_zone = ThermalZoneSummarySerializer(read_only=True)
    latest_measurement = serializers.SerializerMethodField()
    latest_salinity_psu = serializers.SerializerMethodField()
    active_alert_count = serializers.SerializerMethodField()

    class Meta:
        model = Box
        fields = [
            "id",
            "global_code",
            "local_code",
            "box_number",
            "status",
            "organization",
            "species",
            "strain",
            "thermal_zone",
            "entered_on",
            "latest_measurement",
            "latest_salinity_psu",
            "active_alert_count",
        ]

    def get_species(self, obj):
        return SpeciesSummarySerializer(obj.strain.species).data

    def get_latest_measurement(self, obj):
        measurement = _first_prefetched(obj, "biological_measurements")
        if measurement is None:
            measurement = obj.biological_measurements.order_by("-measured_on", "-created_at").first()
        return BiologicalMeasurementSerializer(measurement).data if measurement else None

    def get_latest_salinity_psu(self, obj):
        # Salinity is entered per measurement but rarely changes, so we surface
        # the most recent measurement that actually recorded one. This keeps the
        # value visible even when later measurements leave salinity blank.
        annotated = getattr(obj, "latest_salinity_annotation", None)
        if annotated is not None:
            return _render_salinity(annotated)

        measurements = _prefetched_list(obj, "biological_measurements")
        if measurements is not None:
            for measurement in measurements:  # ordered most-recent first
                if measurement.salinity_psu is not None:
                    return _render_salinity(measurement.salinity_psu)
            return None

        latest = (
            obj.biological_measurements.filter(salinity_psu__isnull=False)
            .order_by("-measured_on", "-created_at")
            .first()
        )
        return _render_salinity(latest.salinity_psu) if latest else None

    def get_active_alert_count(self, obj):
        # The light list queryset annotates the count to avoid loading alerts.
        annotated = getattr(obj, "active_alert_count_annotation", None)
        if annotated is not None:
            return annotated
        alerts = _prefetched_list(obj, "alerts")
        if alerts is not None:
            return sum(1 for alert in alerts if alert.is_active)
        return obj.alerts.filter(resolved_at__isnull=True).count()


class BoxDetailSerializer(BoxListSerializer):
    tags = IdentificationTagSerializer(many=True, read_only=True)
    active_alerts = serializers.SerializerMethodField()
    lineage = serializers.SerializerMethodField()
    locations = BoxLocationSerializer(many=True, read_only=True)
    movements = BoxMovementSerializer(many=True, read_only=True)
    biological_measurements = BiologicalMeasurementSerializer(many=True, read_only=True)
    scan_url = serializers.SerializerMethodField()
    qr_image_url = serializers.SerializerMethodField()

    class Meta(BoxListSerializer.Meta):
        fields = BoxListSerializer.Meta.fields + [
            "created_on",
            "volume_liters",
            "stop_reason",
            "notes",
            "tags",
            "active_alerts",
            "lineage",
            "locations",
            "movements",
            "biological_measurements",
            "scan_url",
            "qr_image_url",
        ]

    def get_scan_url(self, obj):
        return qr.box_scan_url(obj)

    def get_qr_image_url(self, obj):
        return qr.box_qr_image_url(obj)

    def get_active_alerts(self, obj):
        alerts = _prefetched_list(obj, "alerts")
        if alerts is None:
            alerts = obj.alerts.filter(resolved_at__isnull=True)
        else:
            alerts = [alert for alert in alerts if alert.is_active]
        return AlertSummarySerializer(alerts, many=True).data

    def get_lineage(self, obj):
        parent_lineages = _prefetched_list(obj, "parent_lineages")
        child_lineages = _prefetched_list(obj, "child_lineages")
        if parent_lineages is None:
            parent_lineages = obj.parent_lineages.select_related(
                "parent_box",
                "parent_box__strain",
                "parent_box__strain__species",
                "parent_box__thermal_zone",
                "subculture_event",
                "subculture_event__user",
            )
        if child_lineages is None:
            child_lineages = obj.child_lineages.select_related(
                "child_box",
                "child_box__strain",
                "child_box__strain__species",
                "child_box__thermal_zone",
                "subculture_event",
                "subculture_event__user",
            )
        return {
            "parents": [
                _serialize_lineage_relation(lineage, lineage.parent_box)
                for lineage in parent_lineages
            ],
            "children": [
                _serialize_lineage_relation(lineage, lineage.child_box)
                for lineage in child_lineages
            ],
        }


class BiologicalMeasurementCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = BiologicalMeasurement
        fields = [
            "measured_on",
            "polyp_count",
            "ephyrae_count",
            "strobila_count",
            "salinity_psu",
            "culture_status",
            "needs_attention",
            "notes",
        ]


class BoxCreateSerializer(serializers.Serializer):
    organization = serializers.PrimaryKeyRelatedField(queryset=Organization.objects.filter(is_active=True))
    strain = serializers.PrimaryKeyRelatedField(queryset=Strain.objects.select_related("species"))
    thermal_zone = serializers.PrimaryKeyRelatedField(
        queryset=ThermalZone.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    global_code = serializers.CharField(max_length=100)
    local_code = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    box_number = serializers.CharField(max_length=80)
    entered_on = serializers.DateField(default=timezone.localdate)
    volume_liters = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        request = self.context["request"]
        organization = attrs["organization"]
        thermal_zone = attrs.get("thermal_zone")

        if not request.user.is_superuser:
            from apps.accounts.permissions import user_can_write_lab_data

            if not user_can_write_lab_data(request.user, organization):
                raise serializers.ValidationError("This user cannot create boxes for this organization.")

        if thermal_zone and thermal_zone.organization_id != organization.id:
            raise serializers.ValidationError("The thermal zone must belong to the box organization.")

        if Box.objects.filter(global_code=attrs["global_code"]).exists():
            raise serializers.ValidationError({"global_code": "A box already uses this global code."})

        return attrs

    def create(self, validated_data):
        thermal_zone = validated_data.pop("thermal_zone", None)
        box = Box.objects.create(
            **validated_data,
            thermal_zone=thermal_zone,
            status=Box.Status.ACTIVE,
        )
        if thermal_zone:
            BoxLocation.objects.create(
                box=box,
                thermal_zone=thermal_zone,
                starts_at=timezone.now(),
                notes="Initial location after manual creation.",
            )
        return box


class BoxMoveCreateSerializer(serializers.Serializer):
    thermal_zone_id = serializers.PrimaryKeyRelatedField(
        queryset=ThermalZone.objects.filter(is_active=True),
        source="thermal_zone",
    )
    moved_at = serializers.DateTimeField(required=False, default=timezone.now)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_thermal_zone_id(self, thermal_zone):
        box = self.context["box"]
        if thermal_zone.organization_id != box.organization_id:
            raise serializers.ValidationError(
                "The thermal zone must belong to the box organization."
            )
        if box.thermal_zone_id == thermal_zone.id:
            raise serializers.ValidationError("The box is already in this thermal zone.")
        return thermal_zone


class SubcultureChildCreateSerializer(serializers.Serializer):
    global_code = serializers.CharField(max_length=100)
    local_code = serializers.CharField(max_length=100, required=False, allow_blank=True)
    box_number = serializers.CharField(max_length=80)
    thermal_zone_id = serializers.PrimaryKeyRelatedField(
        queryset=ThermalZone.objects.filter(is_active=True),
        source="thermal_zone",
    )
    copy_origin = serializers.BooleanField(default=True)
    copy_volume_liters = serializers.BooleanField(default=True, required=False)
    initial_polyp_count = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate_global_code(self, value):
        if Box.objects.filter(global_code=value).exists():
            raise serializers.ValidationError("A box already uses this global code.")
        return value

    def validate_thermal_zone_id(self, thermal_zone):
        parent_box = self.context["parent_box"]
        if thermal_zone.organization_id != parent_box.organization_id:
            raise serializers.ValidationError(
                "The thermal zone must belong to the parent box organization."
            )
        return thermal_zone


class SubcultureCreateSerializer(serializers.Serializer):
    event_date = serializers.DateField(default=timezone.localdate)
    reason = serializers.CharField(max_length=180, required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    children = SubcultureChildCreateSerializer(many=True, min_length=1, max_length=20)

    def validate_children(self, children):
        global_codes = [child["global_code"] for child in children]
        if len(global_codes) != len(set(global_codes)):
            raise serializers.ValidationError(
                "Each child box must have a different global code."
            )
        return children


class SubcultureEventSerializer(serializers.ModelSerializer):
    parent_box = serializers.CharField(source="parent_box.global_code", read_only=True)
    user = serializers.SerializerMethodField()
    children = serializers.SerializerMethodField()

    class Meta:
        model = SubcultureEvent
        fields = [
            "id",
            "parent_box",
            "event_date",
            "reason",
            "notes",
            "user",
            "children",
        ]

    def get_user(self, obj):
        return obj.user.get_username() if obj.user else None

    def get_children(self, obj):
        child_boxes = self.context.get("child_boxes")
        if child_boxes is None:
            child_boxes = [
                lineage.child_box
                for lineage in obj.lineages.select_related(
                    "child_box",
                    "child_box__organization",
                    "child_box__strain",
                    "child_box__strain__species",
                    "child_box__thermal_zone",
                )
            ]
        return BoxListSerializer(child_boxes, many=True).data


class ThermalZoneSerializer(serializers.ModelSerializer):
    organization = OrganizationSummarySerializer(read_only=True)
    box_count = serializers.IntegerField(read_only=True)
    latest_temperature = serializers.SerializerMethodField()
    latest_salinity = serializers.SerializerMethodField()
    probes = serializers.SerializerMethodField()

    class Meta:
        model = ThermalZone
        fields = [
            "id",
            "name",
            "zone_type",
            "organization",
            "target_temperature_c",
            "capacity",
            "salinity_psu",
            "is_active",
            "box_count",
            "latest_temperature",
            "latest_salinity",
            "probes",
        ]

    def get_latest_temperature(self, obj):
        temperature = _first_prefetched(obj, "daily_temperatures")
        if temperature is None:
            temperature = obj.daily_temperatures.order_by("-date").first()
        if not temperature:
            return None
        return {
            "date": temperature.date,
            "average_temperature_c": temperature.average_temperature_c,
            "min_temperature_c": temperature.min_temperature_c,
            "max_temperature_c": temperature.max_temperature_c,
            "measurement_count": temperature.measurement_count,
        }

    def get_latest_salinity(self, obj):
        salinity = _first_prefetched(obj, "salinity_measurements")
        if salinity is None:
            salinity = obj.salinity_measurements.order_by("-measured_on").first()
        if not salinity:
            return None
        return {
            "measured_on": salinity.measured_on,
            "salinity_psu": salinity.salinity_psu,
        }

    def get_probes(self, obj):
        return [
            {
                "id": probe.id,
                "code": probe.code,
                "probe_type": probe.probe_type,
                "location": probe.location,
                "is_active": probe.is_active,
            }
            for probe in obj.probes.all()
        ]


class ThermalZoneCreateSerializer(serializers.ModelSerializer):
    """Write serializer used by organization admins to create a thermal zone."""

    organization = serializers.PrimaryKeyRelatedField(
        queryset=Organization.objects.filter(is_active=True)
    )

    class Meta:
        model = ThermalZone
        fields = [
            "id",
            "organization",
            "name",
            "zone_type",
            "target_temperature_c",
            "capacity",
            "salinity_psu",
        ]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Le nom de la zone est requis.")
        return value

    def validate(self, attrs):
        # Mirror the model's unique (organization, name) constraint with a clean
        # error instead of a database IntegrityError.
        organization = attrs.get("organization", getattr(self.instance, "organization", None))
        name = attrs.get("name", getattr(self.instance, "name", None))
        queryset = ThermalZone.objects.filter(organization=organization, name=name)
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError(
                {"name": "Une zone porte déjà ce nom dans cette structure."}
            )
        return attrs


class ManualTemperatureCreateSerializer(serializers.Serializer):
    measured_on = serializers.DateField()
    temperature_c = serializers.DecimalField(max_digits=5, decimal_places=2)


class ProbeCreateSerializer(serializers.ModelSerializer):
    """Write serializer used by org admins to register a probe in a zone.

    The owning organization is derived from the selected thermal zone in the view.
    """

    thermal_zone = serializers.PrimaryKeyRelatedField(
        queryset=ThermalZone.objects.filter(is_active=True)
    )

    class Meta:
        model = Probe
        fields = ["id", "thermal_zone", "code", "probe_type", "location"]

    def validate_code(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Le code de la sonde est requis.")
        return value

    def validate(self, attrs):
        zone = attrs["thermal_zone"]
        if Probe.objects.filter(organization=zone.organization, code=attrs["code"]).exists():
            raise serializers.ValidationError(
                {"code": "Une sonde porte déjà ce code dans cette structure."}
            )
        return attrs


class BoxTransferCreateSerializer(serializers.ModelSerializer):
    """Write serializer to record a planned box transfer to another organization.

    The source organization is derived from the box; the transfer only logs the
    intent (status defaults to PLANNED) and does not reassign the box.
    """

    box = serializers.PrimaryKeyRelatedField(queryset=Box.objects.all())
    to_organization = serializers.PrimaryKeyRelatedField(
        queryset=Organization.objects.filter(is_active=True)
    )

    class Meta:
        model = BoxTransfer
        fields = ["id", "box", "to_organization", "transfer_date", "notes"]
        extra_kwargs = {"transfer_date": {"required": False}}

    def validate(self, attrs):
        if attrs["to_organization"] == attrs["box"].organization:
            raise serializers.ValidationError(
                {"to_organization": "La structure cible doit différer de la structure actuelle."}
            )
        return attrs


class UserPreferenceSerializer(serializers.ModelSerializer):
    available_languages = serializers.SerializerMethodField()

    class Meta:
        model = UserPreference
        fields = ["interface_language", "available_languages"]

    def get_available_languages(self, obj):
        return [
            {"code": UserPreference.InterfaceLanguage.FRENCH, "label": "Français"},
            {"code": UserPreference.InterfaceLanguage.ENGLISH, "label": "English"},
        ]


class UserProfileSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    email = serializers.EmailField(allow_blank=True)
    first_name = serializers.CharField(allow_blank=True)
    last_name = serializers.CharField(allow_blank=True)
    interface_language = serializers.CharField()
    organizations = OrganizationSummarySerializer(many=True)
    available_languages = serializers.ListField()


class AuditLogAccessSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = ["id", "object_id", "description", "metadata", "created_at", "user"]

    def get_user(self, obj):
        return obj.user.get_username() if obj.user else None


def _prefetched_list(obj, related_name):
    cache = getattr(obj, "_prefetched_objects_cache", {})
    return cache.get(related_name)


def _first_prefetched(obj, related_name):
    values = _prefetched_list(obj, related_name)
    if values is None:
        return None
    return next(iter(values), None)


def _serialize_lineage_relation(lineage, related_box):
    event = lineage.subculture_event
    return {
        "id": lineage.id,
        "relationship_type": lineage.relationship_type,
        "box": {
            "id": related_box.id,
            "global_code": related_box.global_code,
            "local_code": related_box.local_code,
            "status": related_box.status,
            "species_name": related_box.strain.species.scientific_name,
            "thermal_zone_name": (
                related_box.thermal_zone.name
                if related_box.thermal_zone
                else None
            ),
        },
        "event": (
            {
                "id": event.id,
                "event_date": event.event_date,
                "reason": event.reason,
                "notes": event.notes,
                "user": event.user.get_username() if event.user else None,
            }
            if event
            else None
        ),
    }
