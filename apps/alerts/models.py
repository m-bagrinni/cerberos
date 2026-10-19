"""Модели алертов."""
from django.db import models

from apps.devices.models import Device, MetricDefinition


class Alert(models.Model):
    """
    Алерт — срабатывание порога метрики на устройстве.
    
    Жизненный цикл:
      firing  → активен, значение за пределами порога
      ok      → разрешён, значение вернулось в норму (resolved_at заполнено)
    
    Дубли не создаются: один Alert на (device, metric_key) в состоянии firing.
    При повторном срабатывании обновляется value/threshold/started_at.
    """

    SEVERITY_WARN = "warn"
    SEVERITY_CRIT = "crit"
    SEVERITY_CHOICES = [
        (SEVERITY_WARN, "Warning"),
        (SEVERITY_CRIT, "Critical"),
    ]

    STATE_FIRING = "firing"
    STATE_OK = "ok"
    STATE_CHOICES = [
        (STATE_FIRING, "Firing"),
        (STATE_OK, "OK"),
    ]

    device = models.ForeignKey(
        Device,
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name="alerts",
        help_text="Устройство (заполнено для D-Link/Eltex-алертов)",
    )
    ip_address = models.ForeignKey(
        "subnets.IpAddress",
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name="alerts",
        help_text="IP-адрес (заполнено для пинг-алертов подсетей)",
    )
    ne_device = models.ForeignKey(
        "devices.NeDevice",
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name="alerts",
        help_text="Заполнено, если алерт относится к NE мультиплексора",
    )
    metric = models.ForeignKey(
        MetricDefinition,
        on_delete=models.CASCADE,
        related_name="alerts",
        null=True,
        blank=True,
        help_text="Метрика, по которой сработал алерт",
    )
    metric_key = models.CharField(max_length=64, db_index=True)

    severity = models.CharField(max_length=8, choices=SEVERITY_CHOICES)
    state = models.CharField(max_length=8, choices=STATE_CHOICES, default=STATE_FIRING)

    value = models.FloatField(null=True, blank=True, help_text="Значение метрики в момент срабатывания")
    threshold = models.FloatField(null=True, blank=True, help_text="Порог, который превышен")

    message = models.CharField(max_length=255, blank=True)

    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["state", "-started_at"]),
            models.Index(fields=["device", "metric_key", "state"]),
        ]

    def __str__(self):
        target = (
            self.ip_address.ip if self.ip_address
            else (self.device.name if self.device else "?")
        )
        return f"[{self.severity.upper()}] {target} · {self.metric_key} = {self.value}"

    @property
    def is_firing(self):
        return self.state == self.STATE_FIRING

    @property
    def severity_emoji(self):
        return {"warn": "⚠️", "crit": "🚨"}.get(self.severity, "•")
