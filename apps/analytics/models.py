from django.db import models

class NetworkReport(models.Model):
    """Снимок состояния сети и аналитическое заключение."""
    
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    period_start = models.DateTimeField(help_text="Начало периода сбора данных")
    period_end = models.DateTimeField(help_text="Конец периода сбора данных")
    
    # Сырые метрики в JSON
    metrics = models.JSONField(default=dict, help_text="Сырые данные: устройства, алерты, оптика")
    
    # Текстовое заключение
    conclusion = models.TextField(blank=True, help_text="Автоматическое заключение о работе сети")
    health_status = models.CharField(max_length=32, default="unknown")
    
    # Новые поля для детального отчета
    incidents = models.JSONField(default=list, help_text="Список инцидентов (алерты)")
    uptime_percent = models.FloatField(null=True, blank=True, help_text="Доступность сети в %")
    
    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Отчёт по сети"
        verbose_name_plural = "Отчёты по сети"
    
    def __str__(self):
        return f"Отчёт от {self.created_at.strftime('%d.%m.%Y %H:%M')} ({self.health_status})"
