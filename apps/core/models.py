"""Модели платформы Cerberus (модули)."""
from django.db import models


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
