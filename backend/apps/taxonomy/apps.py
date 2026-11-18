from django.apps import AppConfig


class TaxonomyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.taxonomy"
    verbose_name = "Taxonomy"

    def ready(self):
        from django.db.backends.signals import connection_created

        from .species_names import register_sqlite_name_key

        connection_created.connect(
            register_sqlite_name_key, dispatch_uid="taxonomy.species_name_key",
        )
