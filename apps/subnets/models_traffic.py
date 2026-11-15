"""
Модель для хранения метрик трафика (broadcast storm detection).
"""
from django.db import models


class TrafficSample(models.Model):
    """Снимок трафика за интервал (broadcast/multicast/unicast)."""

    subnet = models.ForeignKey(
        "subnets.Subnet",
        on_delete=models.CASCADE,
        related_name="traffic_samples",
        verbose_name="Подсеть",
    )
    iface = models.CharField(max_length=32, verbose_name="Интерфейс")
    ts = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Время")

    interval_sec = models.IntegerField(default=5, verbose_name="Окно (сек)")

    # Счётчики пакетов
    broadcast = models.IntegerField(default=0, verbose_name="Broadcast")
    multicast = models.IntegerField(default=0, verbose_name="Multicast")
    unicast = models.IntegerField(default=0, verbose_name="Unicast")
    total = models.IntegerField(default=0, verbose_name="Всего")

    # Проценты
    bcast_pct = models.FloatField(default=0.0, verbose_name="Broadcast %")
    mcast_pct = models.FloatField(default=0.0, verbose_name="Multicast %")

    # Скорость
    bps_total = models.BigIntegerField(default=0, verbose_name="bits/sec")
    pps_total = models.FloatField(default=0.0, verbose_name="packets/sec")

    # Топ-MAC источников broadcast/multicast
    top_src_macs = models.JSONField(default=list, blank=True, verbose_name="Топ MAC")

    class Meta:
        ordering = ["-ts"]
        indexes = [
            models.Index(fields=["subnet", "-ts"]),
            models.Index(fields=["-ts"]),
        ]
        verbose_name = "Снимок трафика"
        verbose_name_plural = "Снимки трафика"

    def __str__(self):
        return f"{self.subnet.cidr} @ {self.ts:%Y-%m-%d %H:%M:%S} bcast={self.bcast_pct:.1f}%"


class BroadcastStormEvent(models.Model):
    """
    Событие broadcast/multicast storm на подсети.
    Дедупликация: пока storm активен (state=firing) — обновляем existing,
    не создаём новый. Когда bcast_pct падает ниже порога — state=ok, resolved_at.
    """
    SEVERITY_WARN = "warn"
    SEVERITY_CRIT = "crit"
    SEVERITY_CHOICES = [(SEVERITY_WARN, "Warning"), (SEVERITY_CRIT, "Critical")]

    STATE_FIRING = "firing"
    STATE_OK = "ok"
    STATE_CHOICES = [(STATE_FIRING, "Firing"), (STATE_OK, "OK")]

    subnet = models.ForeignKey(
        "subnets.Subnet",
        on_delete=models.CASCADE,
        related_name="storm_events",
    )
    iface = models.CharField(max_length=32)

    severity = models.CharField(max_length=8, choices=SEVERITY_CHOICES, default=SEVERITY_WARN)
    state = models.CharField(max_length=8, choices=STATE_CHOICES, default=STATE_FIRING)

    # Пиковые значения за время события
    bcast_pct_peak = models.FloatField(default=0.0)
    bcast_per_sec_peak = models.FloatField(default=0.0)
    mcast_pct_peak = models.FloatField(default=0.0)

    # Текущие (последние)
    bcast_pct_now = models.FloatField(default=0.0)
    mcast_pct_now = models.FloatField(default=0.0)
    total_now = models.IntegerField(default=0)

    top_macs = models.JSONField(default=list, blank=True)
    reason = models.CharField(max_length=255, blank=True)

    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["subnet", "state"]),
            models.Index(fields=["-started_at"]),
        ]
        verbose_name = "Broadcast storm event"
        verbose_name_plural = "Broadcast storm events"

    def __str__(self):
        return f"[{self.severity}/{self.state}] {self.subnet.cidr} bcast={self.bcast_pct_peak:.1f}%"
