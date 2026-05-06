from django.contrib import admin

from .models import (
    BiologicalMeasurement,
    DailyTemperature,
    Observation,
    Probe,
    SalinityMeasurement,
    TemperatureMeasurement,
    ThermalAnomaly,
)


@admin.register(BiologicalMeasurement)
class BiologicalMeasurementAdmin(admin.ModelAdmin):
    list_display = (
        "box",
        "measured_on",
        "polyp_count",
        "ephyrae_count",
        "strobila_count",
        "culture_status",
        "needs_attention",
        "user",
    )
    list_filter = ("culture_status", "needs_attention", "measured_on")
    search_fields = ("box__global_code", "box__box_number")


@admin.register(Observation)
class ObservationAdmin(admin.ModelAdmin):
    list_display = ("box", "observed_on", "observation_type", "user")
    list_filter = ("observation_type", "observed_on")
    search_fields = ("box__global_code", "notes")


@admin.register(Probe)
class ProbeAdmin(admin.ModelAdmin):
    list_display = ("code", "organization", "thermal_zone", "probe_type", "is_active")
    list_filter = ("organization", "probe_type", "is_active")
    search_fields = ("code", "thermal_zone__name")


@admin.register(TemperatureMeasurement)
class TemperatureMeasurementAdmin(admin.ModelAdmin):
    list_display = ("probe", "measured_at", "temperature_c", "source")
    list_filter = ("probe__thermal_zone", "source")
    date_hierarchy = "measured_at"
    search_fields = ("probe__code",)


@admin.register(DailyTemperature)
class DailyTemperatureAdmin(admin.ModelAdmin):
    list_display = (
        "thermal_zone",
        "date",
        "min_temperature_c",
        "average_temperature_c",
        "max_temperature_c",
        "measurement_count",
    )
    list_filter = ("thermal_zone",)
    date_hierarchy = "date"


@admin.register(SalinityMeasurement)
class SalinityMeasurementAdmin(admin.ModelAdmin):
    list_display = ("thermal_zone", "measured_on", "salinity_psu", "user")
    list_filter = ("thermal_zone", "measured_on")
    search_fields = ("thermal_zone__name",)


@admin.register(ThermalAnomaly)
class ThermalAnomalyAdmin(admin.ModelAdmin):
    list_display = ("thermal_zone", "starts_at", "ends_at", "reference_temperature_c", "max_deviation_c", "level")
    list_filter = ("thermal_zone", "level")
    date_hierarchy = "starts_at"
