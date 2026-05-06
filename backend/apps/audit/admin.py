from django.contrib import admin

from .models import Alert, AuditLog


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("message", "organization", "alert_type", "level", "box", "thermal_zone", "created_at", "resolved_at")
    list_filter = ("organization", "alert_type", "level", "resolved_at")
    search_fields = ("message", "box__global_code", "thermal_zone__name")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "organization", "user", "action", "object_type", "object_id")
    list_filter = ("organization", "action")
    search_fields = ("description", "object_type", "object_id", "user__username")
    date_hierarchy = "created_at"
