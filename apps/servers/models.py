from django.db import models
from django.utils import timezone

class ServerCategory(models.Model):
    """Категория серверов (DC, DB, Billing, Производственные и т.д.)"""
    name = models.CharField(max_length=64, unique=True)
    slug = models.SlugField(max_length=64, unique=True)
    description = models.TextField(blank=True, null=True)

    class Meta:
        verbose_name = "Категория серверов"
        verbose_name_plural = "Категории серверов"
        ordering = ['name']

    def __str__(self):
        return self.name


class Server(models.Model):
    """Сервер"""
    name = models.CharField(max_length=128)
    ip_address = models.GenericIPAddressField(unique=True)
    category = models.ForeignKey(ServerCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='servers')
    
    is_active = models.BooleanField(default=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    description = models.TextField(blank=True, null=True)

    # Поля для мониторинга (можно расширять)
    os_type = models.CharField(max_length=64, blank=True, null=True)  # Linux/Windows
    
    class Meta:
        verbose_name = "Сервер"
        verbose_name_plural = "Серверы"
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.ip_address})"


class ServerMetric(models.Model):
    """
    Метрика сервера. Универсальная модель.
    Для одного сервера может быть много метрик (cpu, ram, disk, custom).
    """
    server = models.ForeignKey(Server, on_delete=models.CASCADE, related_name='metrics')
    metric_key = models.CharField(max_length=64)  # 'cpu_load', 'db_connections', 'billing_queue'
    value = models.FloatField()
    unit = models.CharField(max_length=16, blank=True, null=True)  # '%', 'GB', 'ms'
    created_at = models.DateTimeField(default=timezone.now)
    
    class Meta:
        verbose_name = "Метрика сервера"
        verbose_name_plural = "Метрики серверов"
        ordering = ['-created_at']
        # Индекс для быстрого поиска последней метрики по ключу
        indexes = [
            models.Index(fields=['server', 'metric_key', '-created_at']),
            models.Index(fields=['-created_at']),
        ]

    def __str__(self):
        return f"{self.server.name} - {self.metric_key}: {self.value} {self.unit or ''}"

class AdUser(models.Model):
    """Пользователь Active Directory"""
    login = models.CharField(max_length=128, unique=True, verbose_name="Логин")
    name = models.CharField(max_length=256, blank=True, null=True, verbose_name="ФИО")
    email = models.EmailField(blank=True, null=True, verbose_name="Email")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    
    last_sync = models.DateTimeField(auto_now=True, verbose_name="Последняя синхронизация")
    
    class Meta:
        verbose_name = "Пользователь AD"
        verbose_name_plural = "Пользователи AD"
        ordering = ['login']

    def __str__(self):
        return f"{self.name} ({self.login})"
