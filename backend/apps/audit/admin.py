from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "organization", "user", "action", "object_type", "object_id")
    list_filter = ("organization", "action")
    search_fields = ("description", "object_type", "object_id", "user__username")
    date_hierarchy = "created_at"
