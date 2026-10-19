from django.apps import AppConfig


class SubnetsConfig(AppConfig):
    """Учёт подсетей, сканирование хостов, пинг-мониторинг."""
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.subnets"
    verbose_name = "Подсети"
