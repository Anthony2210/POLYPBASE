from django.contrib import admin

from .models import (
    Box,
    BoxLineage,
    BoxLocation,
    BoxMovement,
    BoxTransfer,
    BoxTransferImport,
    IdentificationTag,
    SubcultureEvent,
    ThermalZone,
)


@admin.register(ThermalZone)
class ThermalZoneAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "zone_type", "target_temperature_c", "is_active")
    list_filter = ("organization", "zone_type", "is_active")
    search_fields = ("name", "organization__name")


@admin.register(Box)
class BoxAdmin(admin.ModelAdmin):
    list_display = ("global_code", "local_code", "box_number", "strain", "thermal_zone", "status", "entered_on")
    list_filter = ("status", "organization", "thermal_zone")
    search_fields = ("global_code", "local_code", "box_number", "strain__code", "strain__species__scientific_name")


@admin.register(BoxLocation)
class BoxLocationAdmin(admin.ModelAdmin):
    list_display = ("box", "thermal_zone", "starts_at", "ends_at")
    list_filter = ("thermal_zone",)
    search_fields = ("box__global_code", "thermal_zone__name")


@admin.register(BoxMovement)
class BoxMovementAdmin(admin.ModelAdmin):
    list_display = ("box", "from_thermal_zone", "to_thermal_zone", "moved_at", "user")
    list_filter = ("from_thermal_zone", "to_thermal_zone")
    search_fields = ("box__global_code", "notes")


class BoxLineageInline(admin.TabularInline):
    model = BoxLineage
    fk_name = "subculture_event"
    extra = 0


@admin.register(SubcultureEvent)
class SubcultureEventAdmin(admin.ModelAdmin):
    list_display = ("parent_box", "event_date", "user", "reason")
    list_filter = ("event_date",)
    search_fields = ("parent_box__global_code", "reason", "notes")
    inlines = (BoxLineageInline,)


@admin.register(BoxLineage)
class BoxLineageAdmin(admin.ModelAdmin):
    list_display = ("parent_box", "child_box", "relationship_type", "subculture_event")
    list_filter = ("relationship_type",)
    search_fields = ("parent_box__global_code", "child_box__global_code")


@admin.register(IdentificationTag)
class IdentificationTagAdmin(admin.ModelAdmin):
    list_display = ("code", "tag_type", "box", "thermal_zone", "is_active")
    list_filter = ("tag_type", "is_active")
    search_fields = ("code", "box__global_code", "thermal_zone__name")


@admin.register(BoxTransfer)
class BoxTransferAdmin(admin.ModelAdmin):
    list_display = ("box", "from_organization", "to_organization", "transfer_date", "status", "user")
    list_filter = ("status", "from_organization", "to_organization")
    search_fields = ("box__global_code", "notes")


@admin.register(BoxTransferImport)
class BoxTransferImportAdmin(admin.ModelAdmin):
    list_display = ("source_global_code", "created_box", "destination_organization", "imported_by", "imported_at")
    search_fields = ("source_global_code", "created_box__global_code", "source_organization_name")
