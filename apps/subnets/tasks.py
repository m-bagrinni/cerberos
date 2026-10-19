"""
Celery-задачи приложения subnets:

  • scan_subnet_task(subnet_id)  — сканирование одной подсети через fping
  • scan_all_subnets()           — запуск scan_subnet_task для всех активных
  • ping_monitored_ips()         — пинг всех is_monitored=True, запись в PingSample
"""

import ipaddress
import logging
import time
from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import IpAddress, PingSample, Subnet, SubnetScan
from .scanner import ping_one, scan_subnet

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Сканирование одной подсети
# ─────────────────────────────────────────────────────────────

@shared_task(name="subnets.scan_subnet_task", bind=True, max_retries=1)
def scan_subnet_task(self, subnet_id: int):
    """
    Сканирует подсеть через fping:
      1. Создаёт запись SubnetScan(status=running)
      2. Запускает scanner.scan_subnet(cidr)
      3. Обновляет IpAddress:
         - живые → is_alive=True, state=used, last_seen_at=now, rtt
         - ранее живые, но не ответившие → is_alive=False
      4. Завершает SubnetScan
    """
    try:
        subnet = Subnet.objects.get(pk=subnet_id)
    except Subnet.DoesNotExist:
        logger.warning("scan_subnet_task: подсеть id=%s не найдена", subnet_id)
        return {"ok": False, "error": "subnet not found"}

    if not subnet.is_active:
        logger.info("scan_subnet_task: подсеть %s неактивна, пропуск", subnet.cidr)
        return {"ok": False, "error": "subnet inactive"}

    # Помечаем «идёт сканирование»
    Subnet.objects.filter(pk=subnet.pk).update(
        scan_status=Subnet.SCAN_RUNNING, last_scan_error="",
    )

    started = timezone.now()
    t0 = time.monotonic()

    scan = SubnetScan.objects.create(
        subnet=subnet, started_at=started, status=SubnetScan.STATUS_RUNNING,
    )

    # Запускаем сканер
    try:
        result = scan_subnet(subnet.cidr)
    except Exception as e:
        logger.exception("scan_subnet_task: ошибка сканера для %s", subnet.cidr)
        _finish_scan_error(scan, subnet, str(e))
        raise

    if result.error:
        logger.warning("scan_subnet_task: %s — %s", subnet.cidr, result.error)
        _finish_scan_error(scan, subnet, result.error)
        return {"ok": False, "error": result.error}

    alive_set = set(result.alive_ips)
    rtt_map = result.rtt_map

    # Обновляем IpAddress в транзакции
    updated_alive = 0
    updated_lost = 0

    with transaction.atomic():
        existing = {
            ip_obj.ip: ip_obj
            for ip_obj in IpAddress.objects.select_for_update().filter(subnet=subnet)
        }

        for ip_str in result.alive_ips:
            obj = existing.get(ip_str)
            if not obj:
                # Хост живой, но в БД его нет — создаём (на случай /31, /32 или ручных CIDR)
                obj = IpAddress.objects.create(
                    subnet=subnet, ip=ip_str,
                    state=IpAddress.STATE_USED, is_alive=True,
                )
            obj.mark_seen(rtt_map.get(ip_str))
            updated_alive += 1

        # Все, кто не ответил:
        #   - если раньше был жив → is_alive=False, state=used (мог быть выключен)
        #   - если никогда не отвечал и не reserved → state=free
        for ip_str, obj in existing.items():
            if ip_str in alive_set:
                continue
            if obj.is_alive:
                obj.mark_lost()
                updated_lost += 1
            if obj.is_reserved:
                continue
            if not obj.last_seen_at and obj.state == IpAddress.STATE_UNKNOWN:
                # Никогда не был живым — помечаем свободным
                IpAddress.objects.filter(pk=obj.pk).update(state=IpAddress.STATE_FREE)
            elif obj.state == IpAddress.STATE_UNKNOWN:
                # Когда-то был жив, но сейчас молчит — оставляем used
                IpAddress.objects.filter(pk=obj.pk).update(state=IpAddress.STATE_USED)

    # Финализируем SubnetScan
    finished = timezone.now()
    duration_ms = int((time.monotonic() - t0) * 1000)
    scan.finished_at = finished
    scan.status = SubnetScan.STATUS_OK
    scan.hosts_total = result.hosts_total
    scan.hosts_alive = len(result.alive_ips)
    scan.duration_ms = duration_ms
    scan.save(update_fields=[
        "finished_at", "status", "hosts_total", "hosts_alive", "duration_ms",
    ])

    Subnet.objects.filter(pk=subnet.pk).update(
        last_scan_at=finished,
        scan_status=Subnet.SCAN_OK,
        last_scan_error="",
    )

    logger.info(
        "scan_subnet_task: %s → %d живых из %d за %d мс (обновлено: +%d alive, %d lost)",
        subnet.cidr, len(result.alive_ips), result.hosts_total,
        duration_ms, updated_alive, updated_lost,
    )

    return {
        "ok": True,
        "cidr": subnet.cidr,
        "alive": len(result.alive_ips),
        "total": result.hosts_total,
        "duration_ms": duration_ms,
    }


def _finish_scan_error(scan: SubnetScan, subnet: Subnet, error: str):
    """Помечает SubnetScan и Subnet как error."""
    now = timezone.now()
    scan.finished_at = now
    scan.status = SubnetScan.STATUS_ERROR
    scan.error = error[:2000]
    scan.save(update_fields=["finished_at", "status", "error"])

    Subnet.objects.filter(pk=subnet.pk).update(
        last_scan_at=now,
        scan_status=Subnet.SCAN_ERROR,
        last_scan_error=error[:2000],
    )


# ─────────────────────────────────────────────────────────────
# Сканирование всех активных подсетей
# ─────────────────────────────────────────────────────────────

@shared_task(name="subnets.scan_all_subnets")
def scan_all_subnets():
    """
    Beat-задача: находит все активные подсети и запускает
    scan_subnet_task для каждой. Параллельно (через .delay).
    """
    qs = Subnet.objects.filter(is_active=True)

    # Пропускаем те, которые сейчас сканируются (защита от наложения)
    qs = qs.exclude(scan_status=Subnet.SCAN_RUNNING)

    # Пропускаем те, у которых последний скан был недавно (защита от ручного запуска)
    now = timezone.now()
    launched = 0
    for subnet in qs:
        if subnet.last_scan_at:
            age = (now - subnet.last_scan_at).total_seconds()
            if age < subnet.scan_interval_min * 60 - 30:
                # Ещё рано — интервал не истёк
                continue
        scan_subnet_task.delay(subnet.pk)
        launched += 1

    logger.info("scan_all_subnets: запущено %d задач сканирования", launched)
    return {"launched": launched}


# ─────────────────────────────────────────────────────────────
# Пинг всех отмеченных IP (мониторинг)
# ─────────────────────────────────────────────────────────────

@shared_task(name="subnets.ping_monitored_ips")
def ping_monitored_ips():
    """
    Beat-задача: пингует все IpAddress с is_monitored=True.
    Для каждого:
      - ping_one(ip)
      - создаёт PingSample (для графика)
      - обновляет IpAddress.is_alive, last_ping_at, last_seen_at, last_rtt_ms
    """
    qs = IpAddress.objects.filter(is_monitored=True).select_related("subnet")
    total = qs.count()
    if total == 0:
        return {"checked": 0, "alive": 0, "dead": 0}

    now = timezone.now()
    samples_bulk = []
    ips_to_update = []

    alive = 0
    dead = 0

    for ip_obj in qs:
        result = ping_one(ip_obj.ip, retries=2)

        samples_bulk.append(PingSample(
            ip_address=ip_obj,
            rtt_ms=result.rtt_ms,
            is_alive=result.alive,
        ))

        if result.alive:
            alive += 1
            ip_obj.is_alive = True
            ip_obj.last_seen_at = now
            ip_obj.last_ping_at = now
            ip_obj.last_rtt_ms = result.rtt_ms
            ip_obj.packet_loss_pct = result.packet_loss_pct
            if ip_obj.state == IpAddress.STATE_UNKNOWN:
                ip_obj.state = IpAddress.STATE_USED
        else:
            dead += 1
            ip_obj.is_alive = False
            ip_obj.last_ping_at = now
            ip_obj.packet_loss_pct = result.packet_loss_pct
            ip_obj.last_rtt_ms = result.rtt_ms

        ips_to_update.append(ip_obj)

    # Bulk insert/update
    with transaction.atomic():
        if samples_bulk:
            PingSample.objects.bulk_create(samples_bulk, batch_size=500)
        if ips_to_update:
            IpAddress.objects.bulk_update(
                ips_to_update,
                ["is_alive", "last_seen_at", "last_ping_at", "last_rtt_ms",
                 "packet_loss_pct", "state"],
                batch_size=500,
            )

    # Очистка старых PingSample (retention: > 7 дней)
    _cleanup_old_ping_samples(days=7)

    logger.info(
        "ping_monitored_ips: проверено %d (живых %d, мёртвых %d)",
        total, alive, dead,
    )
    return {"checked": total, "alive": alive, "dead": dead}


def _cleanup_old_ping_samples(days: int = 7):
    """
    Удаляет PingSample старше N дней.
    Простейшая retention-политика: не даём таблице расти бесконечно.
    """
    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = PingSample.objects.filter(ts__lt=cutoff).delete()
    if deleted:
        logger.info("ping_monitored_ips: удалено %d старых PingSample", deleted)


@shared_task(name="subnets.check_ip_alerts")
def check_ip_alerts():
    """Периодическая задача: check_alerts (IP + NE + Device)."""
    from django.core.management import call_command
    call_command("check_alerts")
    return {"ok": True}
