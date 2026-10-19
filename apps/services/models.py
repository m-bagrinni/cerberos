"""Модели для управления службами."""
from django.db import models


class ServiceEvent(models.Model):
    """Лог действий над службами (Start/Stop/Restart/Enable/Disable)."""
    
    ACTION_START = "start"
    ACTION_STOP = "stop"
    ACTION_RESTART = "restart"
    ACTION_ENABLE = "enable"
    ACTION_DISABLE = "disable"
    ACTION_RUN_NOW = "run_now"
    
    ACTION_CHOICES = (
        (ACTION_START, "Запуск"),
        (ACTION_STOP, "Остановка"),
        (ACTION_RESTART, "Перезапуск"),
        (ACTION_ENABLE, "Включение"),
        (ACTION_DISABLE, "Выключение"),
        (ACTION_RUN_NOW, "Запуск сейчас"),
    )
    
    TYPE_CONTAINER = "container"
    TYPE_TASK = "task"
    TYPE_CHOICES = (
        (TYPE_CONTAINER, "Контейнер"),
        (TYPE_TASK, "Beat-задача"),
    )
    
    target_type = models.CharField(max_length=16, choices=TYPE_CHOICES)
    target_name = models.CharField(max_length=128)
    action = models.CharField(max_length=16, choices=ACTION_CHOICES)
    success = models.BooleanField(default=True)
    error = models.TextField(blank=True, default="")
    
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    
    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Событие службы"
        verbose_name_plural = "События служб"
    
    def __str__(self):
        return f"[{self.created_at:%H:%M:%S}] {self.action} {self.target_type}/{self.target_name}"
