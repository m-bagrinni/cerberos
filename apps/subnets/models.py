"""
Модели приложения "Подсети".

Задача: учёт подсетей, сканирование хостов (fping), пинг-мониторинг
выбранных IP, история пингов и лог сканирований.
"""

import ipaddress

from django.db import models
from django.utils import timezone


# ─────────────────────────────────────────────────────────────
# Subnet — подсеть
# ─────────────────────────────────────────────────────────────

class Subnet(models.Model):
    """
    Подсеть (напр. 192.168.7.0/24).
    Сканируется Celery-задачей scan_subnet() каждые scan_interval_min минут,
    если is_active=True.
    """

    SCAN_IDLE = "idle"
    SCAN_RUNNING = "running"
    SCAN_OK = "ok"
    SCAN_ERROR = "error"
    SCAN_STATUS_CHOICES = (
        (SCAN_IDLE, "Ожидает"),
        (SCAN_RUNNING, "Сканируется"),
        (SCAN_OK, "OK"),
        (SCAN_ERROR, "Ошибка"),
    )

    cidr = models.CharField(
        max_length=64, unique=True,
        help_text="Например: 192.168.7.0/24",
    )
    name = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    gateway_ip = models.GenericIPAddressField(null=True, blank=True)
    vlan_id = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    # Сканирование
    last_scan_at = models.DateTimeField(null=True, blank=True)
    scan_status = models.CharField(
        max_length=16, choices=SCAN_STATUS_CHOICES, default=SCAN_IDLE,
    )
    scan_interval_min = models.PositiveIntegerField(
        default=15,
        help_text="Как часто пересканировать подсеть (минуты).",
    )
    last_scan_error = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["cidr"]
        verbose_name = "Подсеть"
        verbose_name_plural = "Подсети"

    def __str__(self):
        if self.name:
            return f"{self.name} ({self.cidr})"
        return self.cidr

    # ─── Свойства ───────────────────────────────────────────

    @property
    def network(self):
        """ipaddress.IPv4Network или None при ошибке."""
        try:
            return ipaddress.ip_network(self.cidr, strict=False)
        except ValueError:
            return None

    @property
    def hosts_total_expected(self):
        """Сколько хостов в подсети (без network/broadcast)."""
        net = self.network
        if not net:
            return 0
        return max(0, net.num_addresses - 2)

    def stats(self):
        """
        Быстрая агрегированная статистика для карточек.
        Возвращает dict.
        """
        qs = self.ips.all()
        total = qs.count()
        alive = qs.filter(is_alive=True).count()
        used = qs.filter(state=IpAddress.STATE_USED).count()
        reserved = qs.filter(state=IpAddress.STATE_RESERVED).count()
        monitored = qs.filter(is_monitored=True).count()
        return {
            "total": total,
            "alive": alive,
            "used": used,
            "reserved": reserved,
            "free": max(0, total - used - reserved),
            "monitored": monitored,
        }


# ─────────────────────────────────────────────────────────────
# IpAddress — конкретный хост
# ─────────────────────────────────────────────────────────────

class IpAddress(models.Model):
    """
    Один IP-адрес в подсети.
    Для /24 создаются все 254 адреса заранее со state=unknown;
    после сканирования обновляется is_alive и state.
    """

    STATE_FREE = "free"
    STATE_USED = "used"
    STATE_RESERVED = "reserved"
    STATE_UNKNOWN = "unknown"
    STATE_CHOICES = (
        (STATE_FREE, "Свободен"),
        (STATE_USED, "Занят"),
        (STATE_RESERVED, "Зарезервирован"),
        (STATE_UNKNOWN, "Неизвестно"),
    )

    subnet = models.ForeignKey(
        Subnet, on_delete=models.CASCADE, related_name="ips",
    )
    ip = models.GenericIPAddressField(db_index=True)
    hostname = models.CharField(max_length=255, blank=True, default="")
    mac = models.CharField(max_length=32, blank=True, default="")

    state = models.CharField(
        max_length=16, choices=STATE_CHOICES, default=STATE_UNKNOWN,
        db_index=True,
    )
    is_alive = models.BooleanField(default=False, db_index=True)
    is_monitored = models.BooleanField(
        default=False, db_index=True,
        help_text="Пинговать ли этот IP в фоне (Celery).",
    )
    is_reserved = models.BooleanField(default=False)

    # Метрики последнего пинга
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_ping_at = models.DateTimeField(null=True, blank=True)
    last_rtt_ms = models.FloatField(null=True, blank=True)
    packet_loss_pct = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("subnet", "ip")]
        ordering = ["ip"]
        indexes = [
            models.Index(fields=["subnet", "state"]),
            models.Index(fields=["subnet", "is_monitored"]),
            models.Index(fields=["subnet", "is_alive"]),
        ]
        verbose_name = "IP-адрес"
        verbose_name_plural = "IP-адреса"

    def __str__(self):
        return f"{self.ip} ({self.subnet.cidr})"

    def ip_int(self):
        """IPv4 → int. Для сортировки в сетке карты."""
        try:
            return int(ipaddress.IPv4Address(self.ip))
        except Exception:
            return 0

    def display_name(self):
        """Для UI: hostname, если есть, иначе IP."""
        return self.hostname or self.ip

    def mark_seen(self, rtt_ms: float | None):
        """Обновить поля после успешного пинга."""
        now = timezone.now()
        self.is_alive = True
        self.last_seen_at = now
        self.last_ping_at = now
        self.last_rtt_ms = rtt_ms
        self.packet_loss_pct = 0.0
        if self.state == self.STATE_UNKNOWN:
            self.state = self.STATE_USED
        self.save(update_fields=[
            "is_alive", "last_seen_at", "last_ping_at",
            "last_rtt_ms", "packet_loss_pct", "state", "updated_at",
        ])

    def mark_lost(self):
        """Обновить поля после неудачного пинга."""
        now = timezone.now()
        self.is_alive = False
        self.last_ping_at = now
        self.packet_loss_pct = 100.0
        # state НЕ меняем на free — хост мог быть занят, но сейчас недоступен
        self.save(update_fields=[
            "is_alive", "last_ping_at", "packet_loss_pct", "updated_at",
        ])


# ─────────────────────────────────────────────────────────────
# PingSample — история пингов (для графиков)
# ─────────────────────────────────────────────────────────────

class PingSample(models.Model):
    """
    Одна проба пинга. Пишется Celery-задачей ping_monitored_ips()
    для всех is_monitored=True.
    """

    ip_address = models.ForeignKey(
        IpAddress, on_delete=models.CASCADE, related_name="pings",
    )
    ts = models.DateTimeField(auto_now_add=True, db_index=True)
    rtt_ms = models.FloatField(null=True, blank=True)
    is_alive = models.BooleanField(default=False)

    class Meta:
        ordering = ["-ts"]
        indexes = [
            models.Index(fields=["ip_address", "-ts"]),
        ]
        verbose_name = "Проба пинга"
        verbose_name_plural = "Пробы пинга"


# ─────────────────────────────────────────────────────────────
# SubnetScan — лог сканирований
# ─────────────────────────────────────────────────────────────

class SubnetScan(models.Model):
    """Один запуск сканирования подсети."""

    STATUS_RUNNING = "running"
    STATUS_OK = "ok"
    STATUS_ERROR = "error"
    STATUS_CHOICES = (
        (STATUS_RUNNING, "Идёт"),
        (STATUS_OK, "Успех"),
        (STATUS_ERROR, "Ошибка"),
    )

    subnet = models.ForeignKey(
        Subnet, on_delete=models.CASCADE, related_name="scans",
    )
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_RUNNING,
    )
    hosts_total = models.PositiveIntegerField(default=0)
    hosts_alive = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True, default="")
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "Сканирование подсети"
        verbose_name_plural = "Сканирования подсетей"
