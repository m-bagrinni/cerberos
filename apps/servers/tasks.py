from celery import shared_task
from django.utils import timezone
from .models import Server
from apps.subnets.scanner import ping_one
import logging

logger = logging.getLogger(__name__)

@shared_task(name="servers.ping_all")
def ping_all_servers():
    """Пингует все активные серверы, используя ping_one из apps.subnets.scanner."""
    servers = Server.objects.filter(is_active=True)
    if not servers:
        return "Нет активных серверов"

    for server in servers:
        try:
            # Передаем timeout_ms=1000 (1 сек) и retries=1 (одна попытка)
            result = ping_one(str(server.ip_address), timeout_ms=1000, retries=1)
            
            if result and result.alive:
                server.last_seen = timezone.now()
                server.save(update_fields=['last_seen'])
                logger.info(f"Server {server.ip_address} is UP (RTT: {result.rtt_ms} ms)")
            else:
                logger.warning(f"Server {server.ip_address} is DOWN")
                
        except Exception as e:
            logger.error(f"Error pinging {server.ip_address}: {e}")

    return f"Проверено {servers.count()} серверов"


from datetime import timedelta
from .models import ServerMetric


@shared_task(name="servers.cleanup_metrics")
def cleanup_server_metrics(days=7):
    """Удаляет ServerMetric старше N дней. Раз в сутки."""
    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = ServerMetric.objects.filter(created_at__lt=cutoff).delete()
    logger.info(f"[cleanup_server_metrics] deleted {deleted} (older than {days}d)")
    return f"deleted {deleted} ServerMetric (older than {days}d)"

# --- Регистрация AD-тасков (apps/servers/tasks_ad.py) ---
# autodiscover_tasks() ищет только tasks.py, поэтому tasks_ad.py
# нужно импортировать явно, иначе servers.sync_ad_users не регистрируется
# в Celery и worker падает с KeyError при получении задачи от beat.
from apps.servers import tasks_ad  # noqa: E402,F401
