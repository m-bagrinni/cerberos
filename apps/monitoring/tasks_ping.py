import logging
from datetime import datetime, timezone
from celery import shared_task
from django.db import close_old_connections

from apps.devices.models import Device
from apps.subnets.scanner import ping_one

logger = logging.getLogger(__name__)

@shared_task
def ping_all_devices():
    """
    Пингует все устройства из БД и обновляет status / last_seen.
    Использует fping через ping_one из subnets/scanner.py.
    """
    close_old_connections()
    devices = Device.objects.all()
    now = datetime.now(timezone.utc)
    alive_count = 0
    
    for dev in devices:
        try:
            # retries=2, чтобы избежать ложных срабатываний (Olencom отвечает ~30 мс)
            res = ping_one(dev.ip, retries=2)
            
            if res.alive:
                # Пинг прошел — устройство доступно
                dev.status = Device.STATUS_OK
                dev.last_seen = now
                alive_count += 1
            else:
                # Пинг не прошел — но если SNMP недавно ответил (last_seen < 2 мин),
                # не спешим ставить unreachable, чтобы не было ложных срабатываний
                if dev.last_seen and (now - dev.last_seen).total_seconds() < 120:
                    continue
                dev.status = Device.STATUS_UNREACHABLE
                
            dev.save(update_fields=['status', 'last_seen'])
        except Exception as e:
            logger.error(f"Ping error for {dev.ip}: {e}")
            
    return f"Pinged {devices.count()} devices (alive: {alive_count})"
