from django.apps import AppConfig


class AdminUIConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.adminui"
    verbose_name = "Админ-панель"
