"""
Celery-таски для мониторинга трафика (broadcast storm).
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from apps.monitoring.locks import singleton

logger = logging.getLogger(__name__)

STORM_CONFIRM = 2  # замеров подряд (интервал beat 30 с) до создания события


@shared_task(queue="traffic", soft_time_limit=90, time_limit=110)
@singleton(ttl=120, key=lambda subnet_id, duration=10: subnet_id)
def poll_broadcast_rate(subnet_id: int, duration: int = 10):
    """
    Снимает метрики, пишет TrafficSample, обновляет BroadcastStormEvent.
    Логика:
      - если storm (WARN/CRIT) и активного события нет → создать
      - если storm и активное событие есть → обновить (не дублировать)
      - если НЕ storm и активное событие есть → resolve
    """
    from apps.subnets.models import Subnet
    from apps.subnets.models_traffic import TrafficSample, BroadcastStormEvent
    from apps.subnets.traffic_monitor import (
        find_iface_for_subnet, count_traffic, detect_storm,
    )

    try:
        subnet = Subnet.objects.get(pk=subnet_id)
    except Subnet.DoesNotExist:
        return {"error": f"subnet {subnet_id} not found"}

    iface = find_iface_for_subnet(subnet.cidr)
    if not iface:
        return {"error": f"iface not found for {subnet.cidr}"}

    metrics = count_traffic(iface=iface, duration=duration, cidr=subnet.cidr)
    if not metrics.get("ok"):
        return {"error": metrics.get("error", "count failed")}

    # Снимок
    sample = TrafficSample.objects.create(
        subnet=subnet,
        iface=iface,
        interval_sec=duration,
        broadcast=metrics["broadcast"],
        multicast=metrics["multicast"],
        unicast=metrics["unicast"],
        total=metrics["total"],
        bcast_pct=metrics["bcast_pct"],
        mcast_pct=metrics["mcast_pct"],
        bps_total=metrics["bps_total"],
        pps_total=metrics["pps_total"],
        top_src_macs=metrics["top_src_macs"][:10],
    )

    # Проверка storm + обновление/создание события
    storm = detect_storm(metrics)
    # Гистерезис: одиночный всплеск (чаще всего одно устройство на ~30 с) — не шторм.
    # Новое событие создаём после STORM_CONFIRM тиков подряд; уже открытое — обновляем сразу.
    from django.core.cache import cache
    _ck = f"storm_ticks_{subnet_id}"
    if storm:
        ticks = (cache.get(_ck) or 0) + 1
        cache.set(_ck, ticks, timeout=120)
    else:
        ticks = 0
        cache.delete(_ck)
    active = BroadcastStormEvent.objects.filter(
        subnet=subnet, state=BroadcastStormEvent.STATE_FIRING,
    ).first()

    created_new = False
    updated = False
    resolved = False

    if storm:
        severity = storm["severity"].lower()  # 'warn' / 'crit'
        top_macs = metrics["top_src_macs"][:5]
        if active:
            # Обновляем существующее
            active.severity = severity  # обновляем severity при изменении
            active.bcast_pct_now = metrics["bcast_pct"]
            active.mcast_pct_now = metrics["mcast_pct"]
            active.total_now = metrics["total"]
            active.bcast_pct_peak = max(active.bcast_pct_peak, metrics["bcast_pct"])
            active.bcast_per_sec_peak = max(active.bcast_per_sec_peak, metrics["bcast_per_sec"])
            active.mcast_pct_peak = max(active.mcast_pct_peak, metrics["mcast_pct"])
            active.top_macs = top_macs
            active.reason = storm["reason"]
            active.save(update_fields=[
                "severity", "bcast_pct_now", "mcast_pct_now", "total_now",
                "bcast_pct_peak", "bcast_per_sec_peak", "mcast_pct_peak",
                "top_macs", "reason", "updated_at",
            ])
            updated = True
        elif ticks < STORM_CONFIRM:
            pass  # ждём подтверждения следующим замером
        else:
            new_event = BroadcastStormEvent.objects.create(
                subnet=subnet,
                iface=iface,
                severity=severity,
                state=BroadcastStormEvent.STATE_FIRING,
                bcast_pct_peak=metrics["bcast_pct"],
                bcast_per_sec_peak=metrics["bcast_per_sec"],
                mcast_pct_peak=metrics["mcast_pct"],
                bcast_pct_now=metrics["bcast_pct"],
                mcast_pct_now=metrics["mcast_pct"],
                total_now=metrics["total"],
                top_macs=top_macs,
                reason=storm["reason"],
            )
            created_new = True
            try:
                from apps.accounts.telegram_notify import notify_broadcast_storm
                notify_broadcast_storm(new_event, created=True)
            except Exception as _e:
                logger.warning("TG notify (broadcast created) failed: %s", _e)
    else:
        # Нет storm — если было активное, resolve
        if active:
            active.state = BroadcastStormEvent.STATE_OK
            active.resolved_at = timezone.now()
            active.bcast_pct_now = metrics["bcast_pct"]
            active.mcast_pct_now = metrics["mcast_pct"]
            active.total_now = metrics["total"]
            active.save(update_fields=[
                "state", "resolved_at", "bcast_pct_now", "mcast_pct_now",
                "total_now", "updated_at",
            ])
            resolved = True
            try:
                from apps.accounts.telegram_notify import notify_broadcast_resolved
                notify_broadcast_resolved(active)
            except Exception as _e:
                logger.warning("TG notify (broadcast resolved) failed: %s", _e)

    # Auto-resolve "зависших" storm (не обновлялись > 10 мин)
    from datetime import timedelta
    stale = BroadcastStormEvent.objects.filter(
        state=BroadcastStormEvent.STATE_FIRING,
        updated_at__lt=timezone.now() - timedelta(minutes=10),
    )
    stale_count = stale.update(
        state=BroadcastStormEvent.STATE_OK,
        resolved_at=timezone.now(),
    )
    if stale_count:
        logger.info("Auto-resolved %d stale storms", stale_count)

    return {
        "ok": True,
        "subnet": subnet.cidr,
        "iface": iface,
        "bcast_pct": metrics["bcast_pct"],
        "mcast_pct": metrics["mcast_pct"],
        "total": metrics["total"],
        "storm": storm,
        "event_created": created_new,
        "event_updated": updated,
        "event_resolved": resolved,
        "sample_id": sample.pk,
    }


@shared_task(queue="traffic", soft_time_limit=60, time_limit=80)
@singleton(ttl=90)
def poll_all_subnets_broadcast(duration: int = 10):
    """Beat-таска: по всем подсетям, у которых можно найти iface."""
    from apps.subnets.models import Subnet
    from apps.subnets.traffic_monitor import find_iface_for_subnet

    results = []
    for subnet in Subnet.objects.all():
        iface = find_iface_for_subnet(subnet.cidr)
        if not iface:
            continue
        try:
            # expires=30 (= интервал beat): воркер traffic загружен почти полностью —
            # если не успел взять задачу до следующего цикла, старая уже не нужна.
            r = poll_broadcast_rate.apply_async(args=[subnet.pk, duration], expires=30)
            results.append({"subnet": subnet.cidr, "task_id": r.id})
        except Exception as e:
            logger.exception("Enqueue failed for %s: %s", subnet.cidr, e)

    return {"enqueued": len(results), "results": results}
