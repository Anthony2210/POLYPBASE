from rest_framework import serializers

from apps.accounts.models import UserPreference
from apps.audit.models import Alert, AuditLog
from apps.cultures.models import Box, IdentificationTag, ThermalZone
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
    biological_measurements = BiologicalMeasurementSerializer(many=True, read_only=True)

    class Meta(BoxListSerializer.Meta):
        fields = BoxListSerializer.Meta.fields + [
            "created_on",
            "volume_liters",
            "stop_reason",
            "notes",
            "tags",
            "active_alerts",
            "lineage",
            "biological_measurements",
        ]

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
            parent_lineages = obj.parent_lineages.select_related("parent_box", "child_box")
        if child_lineages is None:
            child_lineages = obj.child_lineages.select_related("parent_box", "child_box")
        return [
            {
                "parent": lineage.parent_box.global_code,
                "child": lineage.child_box.global_code,
                "relationship_type": lineage.relationship_type,
            }
            for lineage in list(parent_lineages) + list(child_lineages)
        ]


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


class AuditLogScanSerializer(serializers.ModelSerializer):
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
