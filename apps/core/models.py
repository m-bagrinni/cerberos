"""Модели платформы Cerberus (модули)."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class Module(models.Model):
    """Модуль Cerberus (Локальная сеть, Huawei CC08, ...)."""
    slug = models.SlugField(unique=True, db_index=True,
                            help_text="local, hwu, ...")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, default="bi-box",
                            help_text="Bootstrap Icons (bi-globe2, bi-telephone)")
    color = models.CharField(max_length=20, default="#ff780a",
                             help_text="HEX-цвет акцента")
    url_prefix = models.CharField(max_length=50,
                                  help_text="/local/, /hwu/")
    is_active = models.BooleanField(default=True)
    is_installed = models.BooleanField(default=True,
                                       help_text="False = в разработке")
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Модуль"
        verbose_name_plural = "Модули"

    def __str__(self):
        return f"{self.name} ({self.slug})"

    def url(self):
        return self.url_prefix


class Announcement(models.Model):
    """Объявление на главной: плановые работы, изменения регламента и т.п."""

    LEVEL_CHOICES = [
        ("info", "Информация"),
        ("warn", "Важно"),
        ("crit", "Срочно"),
    ]

    title = models.CharField("Заголовок", max_length=200)
    text = models.TextField("Текст", blank=True)
    level = models.CharField("Уровень", max_length=10, choices=LEVEL_CHOICES, default="info")
    is_pinned = models.BooleanField("Закрепить", default=False,
                                    help_text="Закреплённое нельзя скрыть, оно всегда сверху")
    is_active = models.BooleanField("Активно", default=True)
    starts_at = models.DateTimeField("Показывать с", default=timezone.now)
    ends_at = models.DateTimeField("Показывать до", null=True, blank=True,
                                   help_text="Пусто — бессрочно")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name="announcements",
                                   verbose_name="Автор")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_pinned", "-starts_at"]
        verbose_name = "Объявление"
        verbose_name_plural = "Объявления"

    def __str__(self):
        return self.title

    @classmethod
    def visible(cls):
        now = timezone.now()
        return (cls.objects.filter(is_active=True, starts_at__lte=now)
                .filter(models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=now))
                .select_related("created_by"))
