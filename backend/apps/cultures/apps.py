from django.apps import AppConfig


class CulturesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cultures"
    # Keep the historical Django app label while the first migrations still
    # refer to "core". A later migration cleanup can rename the label safely.
    label = "core"
    verbose_name = "Cultures"
