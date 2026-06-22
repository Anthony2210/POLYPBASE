from django.utils import timezone
from rest_framework import serializers

from apps.accounts.models import UserPreference
from apps.audit.models import Alert, AuditLog
from apps.cultures import qr
from apps.cultures.models import (
    Box,
    BoxLocation,
    BoxMovement,
    IdentificationTag,
    SubcultureEvent,
    ThermalZone,
)
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.serializers import OrganizationSummarySerializer
from apps.taxonomy.models import Species, Strain


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
            "active_alert_count",
        ]

    def get_species(self, obj):
        return SpeciesSummarySerializer(obj.strain.species).data

    def get_latest_measurement(self, obj):
        measurement = _first_prefetched(obj, "biological_measurements")
        if measurement is None:
            measurement = obj.biological_measurements.order_by("-measured_on", "-created_at").first()
        return BiologicalMeasurementSerializer(measurement).data if measurement else None

    def get_active_alert_count(self, obj):
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
            "culture_status",
            "needs_attention",
            "notes",
        ]


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
    copy_volume_liters = serializers.BooleanField(default=True)
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
