from django.contrib import admin

from .models import DataExport, ExcelImport, ExcelImportRow


class ExcelImportRowInline(admin.TabularInline):
    model = ExcelImportRow
    extra = 0
    readonly_fields = ("row_number", "raw_data", "is_valid", "errors")


@admin.register(ExcelImport)
class ExcelImportAdmin(admin.ModelAdmin):
    list_display = ("file_name", "organization", "status", "user", "imported_at")
    list_filter = ("organization", "status")
    search_fields = ("file_name", "notes")
    inlines = (ExcelImportRowInline,)


@admin.register(ExcelImportRow)
class ExcelImportRowAdmin(admin.ModelAdmin):
    list_display = ("excel_import", "row_number", "is_valid")
    list_filter = ("is_valid",)
    search_fields = ("excel_import__file_name",)


@admin.register(DataExport)
class DataExportAdmin(admin.ModelAdmin):
    list_display = ("export_type", "file_format", "organization", "user", "exported_at", "file_name")
    list_filter = ("organization", "export_type", "file_format")
    search_fields = ("file_name", "user__username")
