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
    """Устройство не ответило — только статус и last_polled_at."""
    device.status = Device.STATUS_UNREACHABLE
    device.last_polled_at = timezone.now()
    device.save(update_fields=["status", "last_polled_at"])
