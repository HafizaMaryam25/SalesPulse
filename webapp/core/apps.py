from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "SalesPulse"

    def ready(self):
        from . import signals  # noqa: F401  (registers login/logout audit hooks)
