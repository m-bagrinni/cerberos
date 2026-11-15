"""Обновление статуса устройства после поллинга."""
from django.utils import timezone

from apps.devices.models import Device


def mark_ok(device: Device) -> None:
    """Устройство ответило — обновляем статус, last_seen, last_polled_at."""
    now = timezone.now()
    device.status = Device.STATUS_OK
    device.last_seen = now
    device.last_polled_at = now
    device.save(update_fields=["status", "last_seen", "last_polled_at"])


def mark_unreachable(device: Device, reason: str = "") -> None:
    """
    Устройство не ответило по SNMP — ставим unreachable,
    НО только если оно не пинговалось успешно в последние 2 минуты.
    Это нужно, чтобы пинг (ICMP) мог поддерживать статус 'ok',
    даже если SNMP-агент молчит (например, Olencom).
    """
    now = timezone.now()
    
    # Если устройство недавно отвечало (пинг или SNMP) — не трогаем статус
    if device.last_seen:
        age_seconds = (now - device.last_seen).total_seconds()
        if age_seconds < 120:  # 2 минуты
            # Устройство живое, просто SNMP не ответил. Оставляем статус как есть.
            device.last_polled_at = now
            device.save(update_fields=["last_polled_at"])
            return
    
    device.status = Device.STATUS_UNREACHABLE
    device.last_polled_at = now
    device.save(update_fields=["status", "last_polled_at"])
