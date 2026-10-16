"""Celery-задачи мониторинга."""
import logging

from celery import shared_task
from django.core.management import call_command
from django.utils import timezone

from apps.devices.models import Device

log = logging.getLogger(__name__)


@shared_task(name="monitoring.poll_all_devices")
def poll_all_devices():
    """Ставит в очередь задачи на опрос метрик всех устройств."""
    log.info("poll_all_devices: запуск")
    started = timezone.now()

    devices = Device.objects.all()
    total = devices.count()

    if total == 0:
        log.info("poll_all_devices: устройств нет")
        return {"total": 0}

    for device in devices:
        poll_device_task.delay(device.ip)

    duration = (timezone.now() - started).total_seconds()
    log.info("poll_all_devices: поставлено %d задач за %.2f сек", total, duration)
    return {"queued": total}


@shared_task(
    name="monitoring.poll_device_task",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 2},
)
def poll_device_task(self, ip: str):
    """Опрос одного устройства (метрики)."""
    try:
        call_command("poll_device", ip)
        log.info("poll_device_task: %s OK", ip)
        return {"ip": ip, "status": "ok"}
    except Exception as e:
        log.error("poll_device_task: %s ошибка: %s", ip, e)
        raise


@shared_task(name="monitoring.poll_interfaces_all")
def poll_interfaces_all():
    """Ставит в очередь задачи на опрос интерфейсов всех активных устройств."""
    log.info("poll_interfaces_all: запуск")
    devices = Device.objects.exclude(status=Device.STATUS_UNREACHABLE)

    for device in devices:
        poll_interfaces_task.delay(device.ip)

    count = devices.count()
    log.info("poll_interfaces_all: поставлено %d задач", count)
    return {"queued": count}


@shared_task(
    name="monitoring.poll_interfaces_task",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=120,
    retry_kwargs={"max_retries": 1},
)
def poll_interfaces_task(self, ip: str):
    """Опрос интерфейсов одного устройства."""
    try:
        call_command("poll_interfaces", ip)
        log.info("poll_interfaces_task: %s OK", ip)
        return {"ip": ip, "status": "ok"}
    except Exception as e:
        log.error("poll_interfaces_task: %s ошибка: %s", ip, e)
        raise


@shared_task(name="monitoring.poll_traffic_all")
def poll_traffic_all():
    """Ставит задачи на опрос трафика всех активных устройств."""
    log.info("poll_traffic_all: запуск")
    devices = Device.objects.exclude(status=Device.STATUS_UNREACHABLE)
    for device in devices:
        poll_traffic_task.delay(device.ip)
    count = devices.count()
    log.info("poll_traffic_all: поставлено %d задач", count)
    return {"queued": count}


@shared_task(
    name="monitoring.poll_traffic_task",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 2},
)
def poll_traffic_task(self, ip: str):
    """Опрос трафика одного устройства."""
    try:
        call_command("poll_interfaces_traffic", ip)
        log.info("poll_traffic_task: %s OK", ip)
        return {"ip": ip, "status": "ok"}
    except Exception as e:
        log.error("poll_traffic_task: %s ошибка: %s", ip, e)
        raise


@shared_task(name="monitoring.poll_sfp_all")
def poll_sfp_all():
    """Ставит задачи на опрос SFP всех активных устройств."""
    log.info("poll_sfp_all: запуск")
    devices = Device.objects.exclude(status=Device.STATUS_UNREACHABLE)
    for device in devices:
        poll_sfp_task.delay(device.ip)
    count = devices.count()
    log.info("poll_sfp_all: поставлено %d задач", count)
    return {"queued": count}


@shared_task(
    name="monitoring.poll_sfp_task",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=120,
    retry_kwargs={"max_retries": 1},
)
def poll_sfp_task(self, ip: str):
    """Опрос SFP одного устройства."""
    try:
        call_command("poll_sfp", ip)
        log.info("poll_sfp_task: %s OK", ip)
        return {"ip": ip, "status": "ok"}
    except Exception as e:
        log.error("poll_sfp_task: %s ошибка: %s", ip, e)
        raise


# ============================================================
# Olencom RP1000 (PDH Multiplexer)
# ============================================================

@shared_task(
    name="monitoring.poll_olencom_task",
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 2},
)
def poll_olencom_task(ip):
    """Опрос одного Olencom-шасси (шлюз + все его NE через SNMP)."""
    from django.core.management import call_command
    call_command("poll_olencom", ip)
    return f"poll_olencom {ip} done"


@shared_task(name="monitoring.poll_olencom_all")
def poll_olencom_all():
    """Найти все Olencom-устройства в БД и запустить опрос каждого."""
    from apps.devices.models import Device
    qs = Device.objects.filter(
        effective_profile__slug="olencom-rp1000",
    ).exclude(status="unreachable")
    count = 0
    for d in qs:
        poll_olencom_task.delay(d.ip)
        count += 1
    return f"queued poll_olencom for {count} devices"


@shared_task(name="monitoring.check_alerts_all")
def check_alerts_all():
    """Проверка метрик на пороги (D-Link, Eltex, Olencom NE)."""
    from django.core.management import call_command
    call_command("check_alerts")
    return "check_alerts done"


@shared_task(name="monitoring.discover_olencom_nes_all")
def discover_olencom_nes_all():
    """Discovery NE на всех Olencom-шлюзах."""
    from django.core.management import call_command
    from apps.devices.models import Device
    from django.db.models import Q

    gws = Device.objects.filter(
        Q(profile__device_category="multiplexer") |
        Q(profile_override__device_category="multiplexer")
    ).values_list("ip", flat=True)

    for ip in gws:
        call_command("discover_olencom_nes", ip)
    return f"discovered on {len(gws)} gateways"


# ============================================================
# OpticalLink — обновление статуса из уже собранных MetricValue
# ============================================================

def _last_metric_for_ne(ne_device, key, instance):
    """Последнее значение MetricValue для NE по (key, instance)."""
    from apps.monitoring.models import MetricValue
    mv = (
        MetricValue.objects
        .filter(ne_device=ne_device, metric__key=key, instance=str(instance))
        .order_by("-ts")
        .first()
    )
    return mv.value if mv else None


def _sfp_state_for_ne(ne_device, port_num):
    """
    Возвращает dict {link_up, rx_dbm} для SFP-порта NE.
    link_up: True / False / None (нет данных).
    rx_dbm: float / None.
    """
    if ne_device is None:
        return {"link_up": None, "rx_dbm": None}

    link_val = _last_metric_for_ne(ne_device, "sfp_link", port_num)
    rx_val   = _last_metric_for_ne(ne_device, "sfp_rx_dbm", port_num)

    link_up = None
    if link_val is not None:
        link_up = (link_val >= 0.5)  # 1.0 = up, 0.0 = down

    return {"link_up": link_up, "rx_dbm": rx_val}


def _end_state(link, side):
    """side = 'a' | 'b'. Возвращает dict {link_up, rx_dbm}."""
    ne = getattr(link, f"ne_{side}", None)
    port_num = getattr(link, f"port_{side}_num")

    if ne is not None:
        return _sfp_state_for_ne(ne, port_num)
    # Device (D-Link/Eltex) — пока не поддерживаем
    return {"link_up": None, "rx_dbm": None}


def _calc_link_status(state_a, state_b):
    """
    Правила:
      - если хотя бы один link_up == False → "crit"  (реальный обрыв)
      - если оба link_up == True:
          RX: min(rx_a, rx_b):
            < -27       → degraded  (сигнал почти не читается, но линия жива)
            < -23       → warn      (деградация)
            < -20       → warn      (предупреждение)
            иначе       → ok
      - если нет данных → unknown
    """
    la = state_a["link_up"]
    lb = state_b["link_up"]
    rxa = state_a["rx_dbm"]
    rxb = state_b["rx_dbm"]

    # Явный обрыв — link down на любом конце
    if la is False or lb is False:
        return "crit"

    # Оба link up — смотрим на RX
    if la is True and lb is True:
        if rxa is None or rxb is None:
            # link up, RX неизвестен — считаем "ok" (худшее не предполагаем)
            return "ok"
        worst = min(rxa, rxb)
        if worst < -27:
            return "degraded"    # 🟠 линия жива, но сигнал очень слабый
        if worst < -20:
            return "warn"        # 🟡 предупреждение
        return "ok"              # 🟢 норма

    # Не хватает данных
    return "unknown"


def poll_one_optical_link(link_id):
    """Обновляет OpticalLink (rx_a/b, link_a/b_up, link_status) из MetricValue."""
    from apps.stations.models import OpticalLink

    try:
        link = OpticalLink.objects.get(pk=link_id)
    except OpticalLink.DoesNotExist:
        return {"ok": False, "error": "not_found"}

    sa = _end_state(link, "a")
    sb = _end_state(link, "b")
    status = _calc_link_status(sa, sb)

    link.rx_a_dbm  = sa["rx_dbm"]
    link.rx_b_dbm  = sb["rx_dbm"]
    link.link_a_up = sa["link_up"]
    link.link_b_up = sb["link_up"]
    link.link_status = status
    link.updated_at = timezone.now()
    link.save(update_fields=[
        "rx_a_dbm", "rx_b_dbm", "link_a_up", "link_b_up",
        "link_status", "updated_at",
    ])

    return {
        "ok": True,
        "link_id": link_id,
        "status": status,
        "a": sa,
        "b": sb,
    }


@shared_task(name="monitoring.poll_optical_links")
def poll_optical_links():
    """
    Периодическая задача: обходит все активные OpticalLink
    и обновляет статус из уже собранных MetricValue.
    """
    from apps.stations.models import OpticalLink

    links = OpticalLink.objects.filter(is_active=True).values_list("id", flat=True)
    total = 0
    by_status = {"ok": 0, "warn": 0, "crit": 0, "unknown": 0}

    for lid in links:
        result = poll_one_optical_link(lid)
        if result.get("ok"):
            total += 1
            by_status[result["status"]] = by_status.get(result["status"], 0) + 1

    return {"updated": total, "by_status": by_status}


# ============================================================
# Быстрый poll оптики — каждые 10 сек
# Опрашивает NE: доступность + sfp_link.
# Если NE не отвечает — сразу crit.
# ============================================================

import subprocess  # noqa: E402
import re as _re  # noqa: E402
from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: E402


def _snmpget_raw(host, community, version, oid, timeout=2, retries=1):
    """
    Быстрый snmpget. Возвращает (ok: bool, raw_value: str | None).
    ok=False → timeout или ошибка.
    retries=1 по умолчанию — SNMP flaky у Olencom.
    """
    try:
        cmd = [
            "snmpget",
            "-v", str(version),
            "-c", community,
            "-t", str(timeout),
            "-r", str(retries),
            "-Oqv",         # только значение
            host, oid,
        ]
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=(timeout * (retries + 1)) + 1,
        )
        if r.returncode != 0:
            return False, None
        return True, r.stdout.strip()
    except Exception:
        return False, None


# OID для быстрой проверки NE:
OID_SYS_DESCR  = "1.3.6.1.2.1.1.1.0"       # sysDescr — есть у любого SNMP
OID_SFP_LINK   = "1.3.6.1.4.1.3399.2.3.3.2.11.1.2"   # присутствие SFP


def _ne_alive(ne):
    """Проверяет, отвечает ли NE на snmpget."""
    gateway = ne.gateway
    oid = f"1.3.6.1.4.1.3399.2.3.3.2.8.1.5.{ne.ne_id}"  # .2.8.1.5 status NE
    ok, _ = _snmpget_raw(
        gateway.ip, gateway.snmp_community,
        gateway.snmp_version or 1, oid,
        timeout=2,
    )
    return ok


def _sfp_link_up(ne, port_num):
    """
    Возвращает True / False / None:
      True  — SFP активен
      False — SFP не активен
      None  — не смогли получить
    """
    gateway = ne.gateway
    oid = f"1.3.6.1.4.1.3399.2.3.3.2.11.1.2.{ne.ne_id}.{port_num}"
    ok, raw = _snmpget_raw(
        gateway.ip, gateway.snmp_community,
        gateway.snmp_version or 1, oid,
        timeout=2,
    )
    if not ok:
        return None
    # Ожидаем hex "01 01" (present + active) или пусто
    if not raw:
        return None
    digits = _re.sub(r"[^0-9A-Fa-f]", "", raw)
    if not digits:
        return False
    return digits != "00"  # всё, кроме "00" — считаем up


def _check_ne_fast(ne_id):
    """
    Один SNMP-вызов: доступность NE.
    Возвращает (ne_id, is_alive).
    """
    from apps.devices.models import NeDevice
    try:
        ne = NeDevice.objects.select_related("gateway").get(pk=ne_id)
    except NeDevice.DoesNotExist:
        return ne_id, False
    return ne_id, _ne_alive(ne)


@shared_task(name="monitoring.poll_optical_links_fast")
def poll_optical_links_fast():
    """
    Быстрая проверка доступности NE + статуса SFP.
    Сначала — доступность всех NE (параллельно, 3 потока).
    Потом — по каждой линии решаем статус.
    """
    from apps.devices.models import NeDevice
    from apps.stations.models import OpticalLink
    from django.utils import timezone

    log = logging.getLogger(__name__)

    # 1) Все NE, которые задействованы в активных линиях
    links = list(
        OpticalLink.objects.filter(is_active=True)
        .select_related("ne_a__gateway", "ne_b__gateway")
    )

    ne_ids = set()
    for l in links:
        if l.ne_a_id: ne_ids.add(l.ne_a_id)
        if l.ne_b_id: ne_ids.add(l.ne_b_id)

    # 2) Опрос доступности NE (параллельно, осторожно)
    ne_alive = {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {ex.submit(_check_ne_fast, nid): nid for nid in ne_ids}
        for fut in as_completed(futs):
            try:
                nid, alive = fut.result()
                ne_alive[nid] = alive
            except Exception as e:
                log.error(f"poll fast: {e}")

    # 3) Опрос SFP link (только у «живых» NE)
    sfp_cache = {}  # (ne_id, port_num) → True/False/None

    def _fetch_sfp(ne_id, port_num):
        from apps.devices.models import NeDevice
        try:
            ne = NeDevice.objects.select_related("gateway").get(pk=ne_id)
        except NeDevice.DoesNotExist:
            return ne_id, port_num, None
        return ne_id, port_num, _sfp_link_up(ne, port_num)

    sfp_jobs = set()
    for l in links:
        if l.ne_a_id and ne_alive.get(l.ne_a_id) and l.port_a_type == "sfp":
            sfp_jobs.add((l.ne_a_id, l.port_a_num))
        if l.ne_b_id and ne_alive.get(l.ne_b_id) and l.port_b_type == "sfp":
            sfp_jobs.add((l.ne_b_id, l.port_b_num))

    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {ex.submit(_fetch_sfp, nid, pnum): (nid, pnum) for nid, pnum in sfp_jobs}
        for fut in as_completed(futs):
            try:
                nid, pnum, val = fut.result()
                sfp_cache[(nid, pnum)] = val
            except Exception as e:
                log.error(f"poll fast sfp: {e}")

    # 4) Обновляем линии
    updated = 0
    by_status = {"ok": 0, "warn": 0, "crit": 0, "unknown": 0}

    for l in links:
        new_status = "unknown"
        a_alive = ne_alive.get(l.ne_a_id) if l.ne_a_id else None
        b_alive = ne_alive.get(l.ne_b_id) if l.ne_b_id else None

        # Если NE-сторона не отвечает → crit
        if a_alive is False or b_alive is False:
            new_status = "crit"
        else:
            a_link = sfp_cache.get((l.ne_a_id, l.port_a_num)) if l.ne_a_id else None
            b_link = sfp_cache.get((l.ne_b_id, l.port_b_num)) if l.ne_b_id else None

            if a_link is False or b_link is False:
                new_status = "crit"
            elif a_link is True and b_link is True:
                # По RX — берём из MetricValue (последние значения)
                from apps.monitoring.tasks import _end_state, _calc_link_status
                sa = _end_state(l, "a")
                sb = _end_state(l, "b")
                new_status = _calc_link_status(sa, sb)
                # rx/link cache обновим через полную задачу (5 мин)
            else:
                new_status = "unknown"

        # Сглаживание: если новый статус unknown, а старый был ok/warn/degraded/crit —
        # НЕ сбрасываем (SNMP flaky). Оставляем старый.
        prev_status = l.link_status
        if new_status == "unknown" and prev_status in ("ok", "warn", "degraded", "crit"):
            new_status = prev_status  # сглаживаем

        # Логируем изменения + создаём/закрываем алерт
        if new_status != prev_status:
            log.warning(
                f"[link {l.id}] {prev_status} → {new_status} "
                f"({l.station_a.slug}→{l.station_b.slug})"
            )
            OpticalLink.objects.filter(pk=l.id).update(
                link_status=new_status,
                updated_at=timezone.now(),
            )
            updated += 1

            # Обновим объект для алертов
            l.link_status = new_status

            # ── создаём/закрываем алерт ──
            if new_status == "crit":
                msg = f"Обрыв оптической линии: {l.station_a.slug} → {l.station_b.slug}"
                _upsert_link_alert(l, "crit", msg)
            elif new_status == "degraded":
                worst_rx = min(
                    (x for x in (l.rx_a_dbm, l.rx_b_dbm) if x is not None),
                    default=None,
                )
                msg = f"Деградация сигнала: {l.station_a.slug} → {l.station_b.slug}"
                if worst_rx is not None:
                    msg += f" (RX {worst_rx:.1f} dBm)"
                _upsert_link_alert(l, "warn", msg, value=worst_rx, threshold=-27)
            elif new_status == "warn":
                worst_rx = min(
                    (x for x in (l.rx_a_dbm, l.rx_b_dbm) if x is not None),
                    default=None,
                )
                msg = f"Низкий RX: {l.station_a.slug} → {l.station_b.slug}"
                if worst_rx is not None:
                    msg += f" (RX {worst_rx:.1f} dBm)"
                _upsert_link_alert(l, "warn", msg, value=worst_rx, threshold=-20)
            elif new_status == "ok":
                # восстановление — закрываем активный алерт
                _close_link_alert(l)
        else:
            # Даже если статус не изменился, обновим updated_at
            OpticalLink.objects.filter(pk=l.id).update(updated_at=timezone.now())

        by_status[new_status] = by_status.get(new_status, 0) + 1

    return {"updated": updated, "total": len(links), "by_status": by_status}


# ============================================================
# Быстрая проверка доступности всех NE (15 сек)
# ============================================================

def _check_ne_alive_fast(ne_id):
    """Проверка одного NE. Возвращает (ne_id, is_alive)."""
    from apps.devices.models import NeDevice
    try:
        ne = NeDevice.objects.select_related("gateway").get(pk=ne_id)
    except NeDevice.DoesNotExist:
        return ne_id, False, None

    gateway = ne.gateway
    oid = f"1.3.6.1.4.1.3399.2.3.3.2.8.1.5.{ne.ne_id}"  # status NE
    ok, raw = _snmpget_raw(
        gateway.ip, gateway.snmp_community,
        gateway.snmp_version or 1, oid,
        timeout=2, retries=1,
    )
    return ne_id, ok, raw


@shared_task(name="monitoring.poll_ne_availability_fast")
def poll_ne_availability_fast():
    """
    Быстрая проверка доступности всех NE.
    Обновляет NeDevice.status (ok / unreachable).
    Сглаживание: если 3 цикла подряд timeout → unreachable.
    """
    from apps.devices.models import NeDevice
    from django.utils import timezone
    from django.core.cache import cache

    log = logging.getLogger(__name__)

    nes = list(NeDevice.objects.select_related("gateway").all())
    total = len(nes)

    ne_ids = [ne.id for ne in nes]

    results = {}  # ne_id → is_alive

    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {ex.submit(_check_ne_alive_fast, nid): nid for nid in ne_ids}
        for fut in as_completed(futs):
            try:
                nid, ok, raw = fut.result()
                results[nid] = ok
            except Exception as e:
                log.error(f"poll_ne_availability: {e}")

    # Сглаживание: считаем подряд неудачные попытки в кэше
    now = timezone.now()
    updated = 0
    by_status = {"ok": 0, "unreachable": 0}

    for ne in nes:
        is_alive = results.get(ne.id, False)
        cache_key = f"ne_fail_count_{ne.id}"

        if is_alive:
            # Сброс счётчика + status ok
            cache.delete(cache_key)
            new_status = "ok"
        else:
            # Инкремент счётчика; unreachable после 3 подряд неудач
            fail_count = (cache.get(cache_key) or 0) + 1
            cache.set(cache_key, fail_count, timeout=120)
            if fail_count >= 3:
                new_status = "unreachable"
            else:
                # Ещё не считаем аварией — оставляем предыдущий статус
                new_status = ne.status

        # Обновляем только при изменении
        if new_status != ne.status:
            log.warning(f"[NE-{ne.ne_id}] {ne.status} → {new_status}")
            NeDevice.objects.filter(pk=ne.id).update(
                status=new_status,
                last_seen=now if is_alive else ne.last_seen,
                last_polled_at=now,
            )
            updated += 1
        else:
            # даже если не изменился — обновим last_polled_at
            NeDevice.objects.filter(pk=ne.id).update(last_polled_at=now)

        by_status[new_status] = by_status.get(new_status, 0) + 1

    return {"updated": updated, "total": total, "by_status": by_status}


# ============================================================
# Алерты для OpticalLink / NeDevice (инлайн в fast-poll)
# ============================================================

def _upsert_link_alert(link, severity, message, value=None, threshold=None):
    """
    Создаёт/обновляет Alert по линии. Если уже есть активный — обновляет.
    state='firing'.
    """
    from apps.alerts.models import Alert
    from django.utils import timezone

    metric_key = f"optical_link:{link.id}"
    alert, created = Alert.objects.get_or_create(
        metric_key=metric_key,
        state="firing",
        defaults={
            "device": link.ne_a.gateway if link.ne_a else None,
            "ne_device": link.ne_a,
            "severity": severity,
            "value": value,
            "threshold": threshold,
            "message": message,
            "started_at": timezone.now(),
        },
    )
    if not created:
        # обновим severity/value/message, если изменились
        changed = False
        if alert.severity != severity:
            alert.severity = severity
            changed = True
        if alert.value != value:
            alert.value = value
            changed = True
        if alert.message != message:
            alert.message = message
            changed = True
        if changed:
            alert.save(update_fields=["severity", "value", "message", "updated_at"])

    return alert, created


def _close_link_alert(link):
    """Закрывает активный Alert по линии (если был)."""
    from apps.alerts.models import Alert
    from django.utils import timezone

    metric_key = f"optical_link:{link.id}"
    updated = Alert.objects.filter(
        metric_key=metric_key, state="firing"
    ).update(state="ok", resolved_at=timezone.now())
    return updated


def _upsert_ne_alert(ne, severity, message):
    """Создаёт/обновляет Alert по NE."""
    from apps.alerts.models import Alert
    from django.utils import timezone

    metric_key = f"ne_status:{ne.id}"
    alert, created = Alert.objects.get_or_create(
        metric_key=metric_key,
        state="firing",
        defaults={
            "device": ne.gateway,
            "ne_device": ne,
            "severity": severity,
            "message": message,
            "started_at": timezone.now(),
        },
    )
    if not created and alert.message != message:
        alert.message = message
        alert.save(update_fields=["message", "updated_at"])
    return alert, created


def _close_ne_alert(ne):
    """Закрывает активный Alert по NE."""
    from apps.alerts.models import Alert
    from django.utils import timezone

    metric_key = f"ne_status:{ne.id}"
    updated = Alert.objects.filter(
        metric_key=metric_key, state="firing"
    ).update(state="ok", resolved_at=timezone.now())
    return updated
