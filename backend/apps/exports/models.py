from django.conf import settings
from django.db import models


class ExcelImport(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        VALIDATED = "validated", "Validated"
        IMPORTED = "imported", "Imported"
        ERROR = "error", "Error"

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="excel_imports")
    file_name = models.CharField(max_length=220)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.DRAFT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-imported_at"]

    def __str__(self):
        return self.file_name


class ExcelImportRow(models.Model):
    excel_import = models.ForeignKey(ExcelImport, on_delete=models.CASCADE, related_name="rows")
    row_number = models.PositiveIntegerField()
    raw_data = models.JSONField(default=dict)
    is_valid = models.BooleanField(default=False)
    errors = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["row_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["excel_import", "row_number"],
                name="unique_row_per_excel_import",
            )
        ]

    def __str__(self):
        return f"{self.excel_import} row {self.row_number}"


class DataExport(models.Model):
    class FileFormat(models.TextChoices):
        CSV = "csv", "CSV"
        XLSX = "xlsx", "XLSX"

    class ExportType(models.TextChoices):
        INVENTORY = "inventory", "Inventory"
        MEASUREMENTS = "measurements", "Measurements"
        TEMPERATURES = "temperatures", "Temperatures"
        REGULATORY = "regulatory", "Regulatory"
        TAXONOMY = "taxonomy", "Taxonomy"

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="data_exports")
    export_type = models.CharField(max_length=40, choices=ExportType.choices)
    file_format = models.CharField(max_length=10, choices=FileFormat.choices)
    filters = models.JSONField(default=dict, blank=True)
    file_name = models.CharField(max_length=220, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    exported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-exported_at"]

    def __str__(self):
        return f"{self.get_export_type_display()} {self.exported_at:%Y-%m-%d}"
