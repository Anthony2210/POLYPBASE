from django.apps import AppConfig


class CulturesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cultures"
    verbose_name = "Cultures"

    def ready(self):
        from django.db.models.signals import post_migrate, pre_migrate

        from config.rollback_preflight import end_rollback, refuse_lossy_rollback_plan

        # pre_migrate/post_migrate are sent once per app; bind to this app only
        # so the whole-plan preflight runs once per migrate command. It covers
        # protected migrations of every app (cultures, measurements, taxonomy).
        pre_migrate.connect(refuse_lossy_rollback_plan, sender=self, dispatch_uid="cultures.rollback_preflight")
        post_migrate.connect(end_rollback, sender=self, dispatch_uid="cultures.rollback_preflight_end")
