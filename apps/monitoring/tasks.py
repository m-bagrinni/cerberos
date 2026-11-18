"""Celery-задачи мониторинга."""
import logging

from celery import shared_task
from django.core.management import call_command
from django.utils import timezone

from apps.devices.models import Device
from apps.monitoring.locks import singleton

log = logging.getLogger(__name__)

# Лимиты для быстрых периодических задач (интервал 60 с).
# soft — бросает SoftTimeLimitExceeded, hard — убивает процесс; замок живёт дольше hard.
FAST = dict(soft_time_limit=240, time_limit=270)
FAST_LOCK = 300
DEVICE_PING_FAILS = 4  # при интервале 30 с ≈ 2 минуты тишины (ICMP + SNMP)
NE_CONFIRM_FAILS = 2  # подряд проваленных циклов до аварии «нет питания/связи»


def _fanout(task, ip, expires):
    """Ставит задачу на устройство. expires: если воркер не взял её за интервал — выбросить,
    вместо того чтобы после затора выполнять пачку устаревших опросов."""
    task.apply_async(args=[ip], expires=expires)


@shared_task(name="monitoring.poll_all_devices")
def poll_all_devices():
    """Ставит в очередь задачи на опрос метрик всех устройств."""
    log.info("poll_all_devices: запуск")
    started = timezone.now()

    # Исключаем Olencom (multiplexer) — у них отдельный poll_olencom.
    # poll_device_task для Olencom зависает (SNMP v1 + OID другой вендор),
    # блокирует worker и не даёт выполниться poll_olencom_all.
    devices = Device.objects.exclude(
        profile__device_category="multiplexer"
    ).exclude(
        profile_override__device_category="multiplexer"
    )
    total = devices.count()

    if total == 0:
        log.info("poll_all_devices: устройств нет")
        return {"total": 0}

    for device in devices:
        _fanout(poll_device_task, device.ip, expires=170)

    duration = (timezone.now() - started).total_seconds()
    log.info("poll_all_devices: поставлено %d задач за %.2f сек", total, duration)
    return {"queued": total}


@shared_task(
    name="monitoring.poll_device_task",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 2},
    soft_time_limit=120, time_limit=150,
)
@singleton(ttl=180, key=lambda ip: ip)
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
        _fanout(poll_interfaces_task, device.ip, expires=290)

    count = devices.count()
    log.info("poll_interfaces_all: поставлено %d задач", count)
    return {"queued": count}


@shared_task(
    name="monitoring.poll_interfaces_task",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=120,
    retry_kwargs={"max_retries": 1},
    soft_time_limit=280, time_limit=300,
)
@singleton(ttl=330, key=lambda ip: ip)
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
        _fanout(poll_traffic_task, device.ip, expires=290)
    count = devices.count()
    log.info("poll_traffic_all: поставлено %d задач", count)
    return {"queued": count}


@shared_task(
    name="monitoring.poll_traffic_task",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 2},
    soft_time_limit=120, time_limit=150,
)
@singleton(ttl=180, key=lambda ip: ip)
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
        _fanout(poll_sfp_task, device.ip, expires=890)
    count = devices.count()
    log.info("poll_sfp_all: поставлено %d задач", count)
    return {"queued": count}


@shared_task(
    name="monitoring.poll_sfp_task",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=120,
    retry_kwargs={"max_retries": 1},
    soft_time_limit=280, time_limit=300,
)
@singleton(ttl=330, key=lambda ip: ip)
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
    soft_time_limit=280, time_limit=300,
)
@singleton(ttl=330, key=lambda ip: ip)
def poll_olencom_task(ip):
    """Опрос одного Olencom-шасси (шлюз + все его NE через SNMP)."""
    from django.core.management import call_command
    call_command("poll_olencom", ip)
    return f"poll_olencom {ip} done"


@shared_task(name="monitoring.poll_olencom_all")
def poll_olencom_all():
    """Найти все Olencom-устройства в БД и запустить опрос каждого."""
    from apps.devices.models import Device
    from django.db.models import Q
    qs = Device.objects.filter(
        Q(profile__slug="olencom-rp1000") |
        Q(profile_override__slug="olencom-rp1000")
    )
    count = 0
    for d in qs:
        _fanout(poll_olencom_task, d.ip, expires=290)
        count += 1
    return f"queued poll_olencom for {count} devices"


@shared_task(name="monitoring.check_alerts_all", soft_time_limit=240, time_limit=270)
@singleton(ttl=300)
def check_alerts_all():
    """Проверка метрик на пороги (D-Link, Eltex, Olencom NE)."""
    from django.core.management import call_command
    call_command("check_alerts")
    return "check_alerts done"


@shared_task(name="monitoring.discover_olencom_nes_all", soft_time_limit=1500, time_limit=1700)
@singleton(ttl=1800)
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


def _sfp_state_for_ne(ne_device, port_num, link=None, side=None):
    """
    Возвращает dict {link_up, rx_dbm} для SFP-порта NE.
    Fallback на кэш OpticalLink (link_X_up, rx_X_dbm), если MetricValue нет.
    """
    if ne_device is None:
        return {"link_up": None, "rx_dbm": None}

    link_val = _last_metric_for_ne(ne_device, "sfp_link", port_num)
    rx_val   = _last_metric_for_ne(ne_device, "sfp_rx_dbm", port_num)

    link_up = None
    if link_val is not None:
        link_up = (link_val >= 0.5)  # 1.0 = up, 0.0 = down

    # Fallback: если MetricValue нет — берём кэш из OpticalLink
    if link is not None and side is not None:
        if link_up is None:
            link_up = getattr(link, f"link_{side}_up", None)
        if rx_val is None:
            rx_val = getattr(link, f"rx_{side}_dbm", None)

    # Fallback 2: если всё ещё None, но NE доступен (status='ok'), считаем link_up=True
    if link_up is None and ne_device.status == 'ok':
        link_up = True

    return {"link_up": link_up, "rx_dbm": rx_val}


def _end_state(link, side):
    """side = 'a' | 'b'. Возвращает dict {link_up, rx_dbm}."""
    ne = getattr(link, f"ne_{side}", None)
    device = getattr(link, f"device_{side}", None)
    port_num = getattr(link, f"port_{side}_num")

    if ne is not None:
        # Если NE недоступен — не доверяем кэшу (данные устарели)
        if ne.status == 'unreachable':
            return {"link_up": None, "rx_dbm": None}
        return _sfp_state_for_ne(ne, port_num, link=link, side=side)
    
    # Device (D-Link/Eltex) — берём DDM из SfpModule
    if device is not None:
        from apps.monitoring.models import Interface, SfpModule
        # Ищем Interface по device и port_num (это if_index или номер порта?)
        # port_num для Device — это if_index (например, 62 для порта 14)
        # Но в OpticalLink port_a_num может быть номером порта (14), а не if_index
        # Нужно найти Interface по имени (gi1/0/14) или по if_index (62)
        iface = None
        # Сначала пробуем найти по if_index = port_num
        iface = Interface.objects.filter(device=device, if_index=port_num).first()
        if not iface:
            # Пробуем найти по имени (например, "gi1/0/14")
            iface = Interface.objects.filter(device=device, name__endswith=f"/{port_num}").first()
        
        if iface:
            sfp = SfpModule.objects.filter(interface=iface).first()
            if sfp:
                # Преобразуем rx_state в True/False/None
                rx_state = sfp.rx_state
                if rx_state == "ok":
                    link_up = True
                elif rx_state in ("down", "fail", "no_signal"):
                    link_up = False
                else:
                    link_up = None
                return {
                    "link_up": link_up,
                    "rx_dbm": sfp.rx_power_dbm,
                    "tx_dbm": sfp.tx_power_dbm,
                    "temp_c": sfp.temperature_c,
                    "bias_ma": sfp.bias_current_ma,
                    "volt_v": sfp.voltage_v,
                }
    
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

    # Оба link up — смотрим на RX (даже если один None)
    if la is True and lb is True:
        # Собираем все доступные RX
        rx_values = [x for x in (rxa, rxb) if x is not None]
        if not rx_values:
            return "ok"          # нет DDM — считаем ok
        worst = min(rx_values)    # худший из доступных
        if worst < -27:
            return "degraded"
        if worst < -20:
            return "warn"
        return "ok"

    # Если link_up = None, но rx_dbm есть — считаем по RX
    if rxa is not None and rxb is not None:
        worst = min(rxa, rxb)
        if worst < -27:
            return "degraded"
        if worst < -20:
            return "warn"
        return "ok"

    # Если одна сторона None (нет DDM), но другая есть — считаем по одной
    if rxa is not None or rxb is not None:
        rx = rxa if rxa is not None else rxb
        if rx < -27:
            return "degraded"
        if rx < -20:
            return "warn"
        return "ok"

    # Ничего не известно — unknown
    return "unknown"

    # Ничего не известно — unknown

    # Не хватает данных


def poll_one_optical_link(link_id):
    """Обновляет OpticalLink (rx_a/b, link_a/b_up, link_status) из MetricValue."""
    from apps.stations.models import OpticalLink

    try:
        link = OpticalLink.objects.get(pk=link_id)
    except OpticalLink.DoesNotExist:
        return {"ok": False, "error": "not_found"}

    sa = _end_state(link, "a")
    sb = _end_state(link, "b")
    
    # 1. Сначала считаем базовый статус из DDM
    status = _calc_link_status(sa, sb)
    
    # 2. Проверяем доступность устройств ВСЕГДА
    if link.ne_a and (link.ne_a.status == "unreachable" or (link.ne_a.last_seen and (timezone.now() - link.ne_a.last_seen).total_seconds() > 300)):
        status = "power"
    elif link.ne_b and (link.ne_b.status == "unreachable" or (link.ne_b.last_seen and (timezone.now() - link.ne_b.last_seen).total_seconds() > 300)):
        status = "power"
    elif link.device_a and not link.device_a.is_reachable:
        status = "power"
    elif link.device_b and not link.device_b.is_reachable:
        status = "power"

    # Не перезаписывать None-значения (сохраняем кэш)
    if sa["rx_dbm"] is not None:
        link.rx_a_dbm = sa["rx_dbm"]
    if sb["rx_dbm"] is not None:
        link.rx_b_dbm = sb["rx_dbm"]
    if sa.get("tx_dbm") is not None:
        link.tx_a_dbm = sa["tx_dbm"]
    if sb.get("tx_dbm") is not None:
        link.tx_b_dbm = sb["tx_dbm"]
    if sa.get("temp_c") is not None:
        link.temp_a_c = sa["temp_c"]
    if sb.get("temp_c") is not None:
        link.temp_b_c = sb["temp_c"]
    if sa.get("bias_ma") is not None:
        link.bias_a_ma = sa["bias_ma"]
    if sb.get("bias_ma") is not None:
        link.bias_b_ma = sb["bias_ma"]
    if sa.get("volt_v") is not None:
        link.volt_a_v = sa["volt_v"]
    if sb.get("volt_v") is not None:
        link.volt_b_v = sb["volt_v"]
    if sa["link_up"] is not None:
        link.link_a_up = sa["link_up"]
    if sb["link_up"] is not None:
        link.link_b_up = sb["link_up"]
    link.link_status = status
    link.updated_at = timezone.now()
    link.save(update_fields=[
        "rx_a_dbm", "rx_b_dbm", "tx_a_dbm", "tx_b_dbm",
        "temp_a_c", "temp_b_c", "bias_a_ma", "bias_b_ma",
        "volt_a_v", "volt_b_v", "link_a_up", "link_b_up",
        "link_status", "updated_at",
    ])

    return {
        "ok": True,
        "link_id": link_id,
        "status": status,
        "a": sa,
        "b": sb,
    }


@shared_task(name="monitoring.poll_optical_links", **FAST)
@singleton(ttl=FAST_LOCK)
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
from channels.db import database_sync_to_async

import logging
logger = logging.getLogger(__name__)




def _snmpget_raw(host, community, version, oid, timeout=4, retries=2):
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
        timeout=4, retries=2,
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
        timeout=4, retries=2,
    )
    if not ok:
        return None
    # Ожидаем hex "01 01" (present + active) или пусто
    if not raw:
        return None
    digits = _re.sub(r"[^0-9A-Fa-f]", "", raw)
    if not digits:
        return None   # пустой ответ — нет данных, не down
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


@shared_task(name="monitoring.poll_optical_links_fast", **FAST)
@singleton(ttl=FAST_LOCK)
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
    # Ближние к центру — первыми: обрыв выше по цепочке фиксируется раньше «нет связи» за ним,
    # и алерты дальних линий сворачиваются под него (apps/alerts/correlation.py)
    try:
        from apps.alerts.correlation import topo_order
        links = topo_order(links, prefix="l")
    except Exception as e:  # noqa: BLE001 — порядок не критичен для самого опроса
        log.warning(f"poll fast: topo_order: {e}")

    ne_ids = set()
    for l in links:
        if l.ne_a_id: ne_ids.add(l.ne_a_id)
        if l.ne_b_id: ne_ids.add(l.ne_b_id)

    # 2) Доступность NE. Её уже проверяет poll_ne_availability_fast (тот же OID, тот же интервал)
    #    и кладёт сырой результат в кэш — не дёргаем хрупкие шлюзы Olencom второй раз.
    #    SNMP-проверку делаем только для NE, по которым в кэше ничего нет.
    from django.core.cache import cache
    ne_alive = {}
    try:
        cached = cache.get_many([f"ne_alive_raw_{nid}" for nid in ne_ids])
    except Exception:
        cached = {}
    # Одиночный сбой SNMP у хрупких шлюзов Olencom — норма. «Нет питания» объявляем,
    # только когда NE не ответил NE_CONFIRM_FAILS циклов подряд (счётчик ведёт poll_ne_availability_fast).
    try:
        fails = cache.get_many([f"ne_fail_count_{nid}" for nid in ne_ids])
    except Exception:
        fails = {}
    for nid in ne_ids:
        v = cached.get(f"ne_alive_raw_{nid}")
        if v is not None:
            alive = bool(v)
            if not alive and (fails.get(f"ne_fail_count_{nid}") or 0) < NE_CONFIRM_FAILS:
                alive = True  # пока не подтверждено — считаем живым (статус линии не меняем)
            ne_alive[nid] = alive
    missing = [nid for nid in ne_ids if nid not in ne_alive]
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {ex.submit(_check_ne_fast, nid): nid for nid in missing}
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

    # Определения DDM-метрик — один раз на прогон (раньше: запрос на каждую линию × метрику)
    from apps.monitoring.models import MetricValue
    from apps.devices.models import MetricDefinition
    from datetime import timedelta
    _ddm_keys = ("sfp_rx_dbm", "sfp_tx_dbm", "sfp_temp", "sfp_bias", "sfp_voltage")
    ddm_defs = {}
    for md in MetricDefinition.objects.filter(key__in=_ddm_keys).order_by("id"):
        ddm_defs.setdefault(md.key, md)
    cutoff = timezone.now() - timedelta(minutes=5)

    # 4) Обновляем линии
    updated = 0
    by_status = {"ok": 0, "warn": 0, "crit": 0, "unknown": 0}

    for l in links:
        new_status = "unknown"
        a_alive = ne_alive.get(l.ne_a_id) if l.ne_a_id else None
        b_alive = ne_alive.get(l.ne_b_id) if l.ne_b_id else None

        # Если NE-сторона не отвечает — это "power" (нет питания/связи)
        if a_alive is False or b_alive is False:
            new_status = "power"
        else:
            a_link = sfp_cache.get((l.ne_a_id, l.port_a_num)) if l.ne_a_id else None
            b_link = sfp_cache.get((l.ne_b_id, l.port_b_num)) if l.ne_b_id else None

            # Для Device (D-Link/Eltex) — через _end_state
            # И для NE, если a_link/b_link = None (SNMP timeout, NE не пингуется)
            if l.device_a_id or l.device_b_id or a_link is None or b_link is None:
                sa = _end_state(l, "a")
                sb = _end_state(l, "b")
                new_status = _calc_link_status(sa, sb)
            else:
                if a_link is False or b_link is False:
                    new_status = "crit"
                elif a_link is True and b_link is True:
                    sa = _end_state(l, "a")
                    sb = _end_state(l, "b")
                    new_status = _calc_link_status(sa, sb)
                else:
                    new_status = "unknown"

        # Сглаживание: если новый статус unknown, а старый был ok/warn/degraded —
        # НЕ сбрасываем (SNMP flaky). НО crit — сбрасываем в unknown (не подтверждён).
        prev_status = l.link_status
        if new_status == "unknown" and prev_status in ("ok", "warn", "degraded", "power"):
            new_status = prev_status  # сглаживаем, но НЕ crit/power

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
            if new_status == "power":
                msg = f"Нет питания/связи: {l.station_a.slug} → {l.station_b.slug}"
                _upsert_link_alert(l, "power", msg)
            elif new_status == "crit":
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

        # ── Запись DDM-метрик в MetricValue (для истории графиков) ──
        # Пишем не чаще раза в 5 минут (троттлинг)
        def _save_ddm(link, side):
            device = link.device_a if side == 'a' else link.device_b
            port = link.port_a_num if side == 'a' else link.port_b_num
            rx = link.rx_a_dbm if side == 'a' else link.rx_b_dbm
            tx = link.tx_a_dbm if side == 'a' else link.tx_b_dbm
            temp = link.temp_a_c if side == 'a' else link.temp_b_c
            bias = link.bias_a_ma if side == 'a' else link.bias_b_ma
            volt = link.volt_a_v if side == 'a' else link.volt_b_v
            
            if not device or not port:
                return
            
            # Проверяем, есть ли свежая запись (за последние 5 минут)
            has_recent = MetricValue.objects.filter(
                device=device,
                instance=port,
                metric__key='sfp_rx_dbm',
                ts__gte=cutoff,
            ).exists()
            if has_recent:
                return
            
            rows = [
                MetricValue(device=device, metric=ddm_defs[key], instance=port, value=value)
                for key, value in [('sfp_rx_dbm', rx), ('sfp_tx_dbm', tx), ('sfp_temp', temp),
                                   ('sfp_bias', bias), ('sfp_voltage', volt)]
                if value is not None and key in ddm_defs
            ]
            if rows:
                MetricValue.objects.bulk_create(rows)
        
        _save_ddm(l, 'a')
        _save_ddm(l, 'b')

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
        timeout=4, retries=2,
    )
    return ne_id, ok, raw


@shared_task(name="monitoring.poll_ne_availability_fast", **FAST)
@singleton(ttl=FAST_LOCK)
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
        # Сырой результат этого цикла — для poll_optical_links_fast (живёт ~2 интервала)
        cache.set(f"ne_alive_raw_{ne.id}", 1 if is_alive else 0, timeout=150)

        if is_alive:
            # Сброс счётчика + status ok
            cache.delete(cache_key)
            new_status = "ok"
        else:
            # Инкремент счётчика; unreachable после 3 подряд неудач
            fail_count = (cache.get(cache_key) or 0) + 1
            # 10 минут: счётчик не должен «протухать» между циклами, если прогон задержался
            cache.set(cache_key, fail_count, timeout=600)
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
    Создаёт/обновляет Alert по линии. Дедупликация:
    - если есть активный firing — обновляем, не создаём новый;
    - если firing нет, но был ok < 15 мин назад — НЕ создаём новый (линия "моргнула");
    - иначе создаём firing и уведомляем.
    """
    from apps.alerts.models import Alert
    from django.utils import timezone
    from datetime import timedelta

    metric_key = f"optical_link:{link.id}"
    now = timezone.now()

    # 1. Активный firing — обновляем
    active = Alert.objects.filter(metric_key=metric_key, state="firing").first()
    if active:
        old_severity = active.severity
        changed = False
        if active.severity != severity:
            active.severity = severity
            changed = True
        if active.value != value:
            active.value = value
            changed = True
        if active.message != message:
            active.message = message
            changed = True
        if changed:
            active.save(update_fields=["severity", "value", "message", "updated_at"])
        # Уведомлять для power/crit (важно), если severity изменился
        if severity in ("crit", "power") and old_severity != severity:
            try:
                from apps.accounts.telegram_notify import notify_alert_created
                notify_alert_created(active)
            except Exception as _e:
                logger.warning("TG notify (link active) failed: %s", _e)
        return active, False

    # 2. Был ok недавно (линия «моргнула») — не плодим новый алерт, а ПЕРЕОТКРЫВАЕМ прежний.
    #    Раньше он оставался закрытым: линия стояла в crit, а в активных алертах её не было.
    #    Правило уведомлений прежнее (только при смене severity) — чтобы не спамить на флапах.
    recent_ok = Alert.objects.filter(
        metric_key=metric_key, state="ok",
        resolved_at__gte=now - timedelta(minutes=15),
    ).order_by("-resolved_at").first()
    if recent_ok:
        old_severity = recent_ok.severity
        recent_ok.state = "firing"
        recent_ok.resolved_at = None
        recent_ok.severity = severity
        recent_ok.message = message
        recent_ok.value = value
        recent_ok.threshold = threshold
        recent_ok.save(update_fields=["state", "resolved_at", "severity", "message", "value",
                                      "threshold", "updated_at"])
        # Уведомлять для power/crit (важно), даже если был ok < 15 мин
        if severity in ("crit", "power") and old_severity != severity:
            try:
                from apps.accounts.telegram_notify import notify_alert_created
                notify_alert_created(recent_ok)
            except Exception as _e:
                logger.warning("TG notify (link recent_ok) failed: %s", _e)
        return recent_ok, False

    # 3. Создаём новый firing и уведомляем
    alert = Alert.objects.create(
        device=link.ne_a.gateway if link.ne_a else None,
        ne_device=link.ne_a,
        metric_key=metric_key,
        severity=severity,
        state="firing",
        value=value,
        threshold=threshold,
        message=message,
        started_at=now,
    )
    try:
        from apps.accounts.telegram_notify import notify_alert_created
        notify_alert_created(alert)
    except Exception as _e:
        logger.warning("TG notify (link created) failed: %s", _e)
    return alert, True


def _close_link_alert(link):
    """Закрывает активный Alert по линии (если был) + notify resolved."""
    from apps.alerts.models import Alert
    from django.utils import timezone

    metric_key = f"optical_link:{link.id}"
    qs = Alert.objects.filter(metric_key=metric_key, state="firing")
    n = 0
    for a in qs:
        a.state = "ok"
        a.resolved_at = timezone.now()
        a.save(update_fields=["state", "resolved_at", "updated_at"])
        try:
            from apps.accounts.telegram_notify import notify_alert_resolved
            notify_alert_resolved(a)
        except Exception as _e:
            logger.warning("TG notify (link resolved) failed: %s", _e)
        n += 1
    return n


@shared_task(name="monitoring.poll_traffic_light", **FAST)
@singleton(ttl=FAST_LOCK)
def poll_traffic_light():
    """
    Лёгкий сбор eth_rx_bytes (только порты 1 и 5) для всех Olencom NE.
    Запускается раз в 10 секунд — для плавного графика трафика.
    """
    from apps.devices.models import Device, NeDevice, MetricDefinition
    from apps.monitoring.models import MetricValue
    from apps.monitoring.management.commands.poll_olencom import (
        snmpget, parse_hex, OID_ETH_CNT2,
    )

    gateways = Device.objects.filter(
        profile__device_category="multiplexer",
    )
    if not gateways.exists():
        return {"ok": True, "saved": 0, "reason": "no gateways"}

    metric = MetricDefinition.objects.filter(key="eth_rx_bytes").first()
    if not metric:
        return {"ok": False, "error": "no metric eth_rx_bytes"}

    batch = []
    ports = ["1", "5"]  # Только активные: WAN (1) и LAN (5)
    
    for gw in gateways:
        community = gw.snmp_community or "public"
        nes = NeDevice.objects.filter(gateway=gw)
        for ne in nes:
            for port in ports:
                try:
                    oid = f"{OID_ETH_CNT2}.{ne.ne_id}.{port}"
                    raw = snmpget(gw.ip, community, oid, timeout=2, retries=0)
                    if not raw:
                        continue
                    
                    c_hex = parse_hex(raw)
                    if not c_hex or len(c_hex) < 8:
                        continue
                    
                    rx_bytes = int.from_bytes(bytes(c_hex[0:8]), "little")
                    batch.append(MetricValue(
                        device=gw,  # device_id NOT NULL
                        ne_device=ne, metric=metric,
                        instance=port, value=float(rx_bytes),
                    ))
                except Exception as e:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.error(f"[poll_traffic_light] NE={ne.ne_id} port={port} ERROR: {e}")
                    continue

    # Одна вставка на прогон вместо INSERT на каждый порт.
    # Очистка старых значений — в monitoring.cleanup_old_metric_values (по MONITORING_RETENTION);
    # раньше здесь удалялось ВСЁ старше 2 часов, и графики «за 24 ч / 7 дней» были пустыми.
    if batch:
        MetricValue.objects.bulk_create(batch)
    saved = len(batch)

    return {"ok": True, "saved": saved}

def _delete_in_batches(qs, batch=20000, max_batches=200):
    """DELETE порциями по id — без многоминутной блокировки и раздувания WAL одним запросом."""
    total = 0
    for _ in range(max_batches):
        ids = list(qs.values_list("id", flat=True)[:batch])
        if not ids:
            break
        n, _ = qs.model.objects.filter(id__in=ids).delete()
        total += n
    return total


@shared_task(name="monitoring.cleanup_old_metric_values", soft_time_limit=1500, time_limit=1700)
@singleton(ttl=1800)
def cleanup_old_metric_values():
    """
    Ретенция истории (settings.MONITORING_RETENTION). Запускается beat'ом раз в час.
      - MetricValue: metric_value_days; служебные ключи — metric_value_short_hours
      - InterfaceSample: interface_sample_days
      - TrafficSample: traffic_sample_days
    """
    from datetime import timedelta
    from django.conf import settings
    from apps.monitoring.models import InterfaceSample, MetricValue

    cfg = getattr(settings, "MONITORING_RETENTION", {})
    now = timezone.now()
    res = {}

    short_keys = cfg.get("metric_value_short_keys") or []
    if short_keys:
        res["metric_value_short"] = _delete_in_batches(MetricValue.objects.filter(
            metric__key__in=short_keys,
            ts__lt=now - timedelta(hours=cfg.get("metric_value_short_hours", 24)),
        ))
    res["metric_value"] = _delete_in_batches(MetricValue.objects.filter(
        ts__lt=now - timedelta(days=cfg.get("metric_value_days", 8)),
    ))
    res["interface_sample"] = _delete_in_batches(InterfaceSample.objects.filter(
        ts__lt=now - timedelta(days=cfg.get("interface_sample_days", 30)),
    ))
    try:
        from apps.subnets.models_traffic import TrafficSample
        ts_field = "ts" if any(f.name == "ts" for f in TrafficSample._meta.fields) else "created_at"
        res["traffic_sample"] = _delete_in_batches(TrafficSample.objects.filter(**{
            f"{ts_field}__lt": now - timedelta(days=cfg.get("traffic_sample_days", 30)),
        }))
    except Exception as e:
        log.warning("cleanup TrafficSample: %s", e)

    log.info("cleanup_old_metric_values: %s", res)
    return res


@shared_task(**FAST)
@singleton(ttl=FAST_LOCK)
def poll_device_links_task():
    """
    Опрос портов для линий с device_a/device_b (D-Link/Eltex).
    Обновляет link_status, link_a_up, link_b_up, rx_a_dbm, rx_b_dbm.
    """
    from django.core.management import call_command
    call_command('poll_device_links')


@shared_task(name="monitoring.ping_all_devices", **FAST)
@singleton(ttl=FAST_LOCK)
def ping_all_devices():
    """Пингует все устройства. Создает алерт при 3 неудачах подряд, закрывает при первом успехе."""
    from apps.devices.models import Device
    from apps.alerts.models import Alert
    from apps.subnets.scanner import ping_one
    from apps.accounts.telegram_notify import notify_alert_created
    from apps.alerts.correlation import topo_order
    from django.utils import timezone

    # Сначала вышестоящие (Device.uplink): если узел и всё за ним упали разом, алерт узла
    # создаётся первым и алерты остальных сразу сворачиваются под него (apps/alerts/correlation.py)
    devices = topo_order(Device.objects.all())
    for dev in devices:
        try:
            result = ping_one(dev.ip, retries=1)
            alive = result.alive
        except Exception as e:
            logger.warning(f"Ping error for {dev.name} ({dev.ip}): {e}")
            alive = False

        now = timezone.now()
        dev.last_ping_at = now

        # Если пинг не прошёл — проверяем SNMP (может, ICMP заблокирован)
        if not alive:
            try:
                import asyncio
                from apps.monitoring.snmp import SnmpClient, SnmpTarget
                target = SnmpTarget(
                    host=dev.ip,
                    community=dev.snmp_community or 'public',
                    port=dev.snmp_port or 161,
                    timeout=2,
                    retries=0,
                )
                client = SnmpClient(target)
                result = asyncio.run(client.get_one('1.3.6.1.2.1.1.3.0'))  # sysUpTime
                if result:
                    alive = True
                    logger.info(f"SNMP alive for {dev.name} ({dev.ip})")
            except Exception as e:
                logger.warning(f"SNMP check failed for {dev.name} ({dev.ip}): {e}")

        if alive:
            dev.ping_ok_count += 1
            dev.ping_fail_count = 0
            
            # Проверяем, есть ли активный алерт (независимо от is_reachable)
            active = Alert.objects.filter(
                device=dev, metric_key="device_unreachable", state=Alert.STATE_FIRING
            ).first()
            if active:
                # Закрываем алерт
                active.state = Alert.STATE_OK
                active.resolved_at = now
                active.save()
                # Уведомляем, что устройство снова доступно
                try:
                    from apps.accounts.telegram_notify import notify_alert_resolved
                    notify_alert_resolved(active)
                except Exception as e:
                    logger.warning('TG notify (resolved) failed: %s', e)
                logger.info(f"Device {dev.name} ({dev.ip}) is reachable again (alert closed)")
            
            # Сбрасываем is_reachable (если был недоступен)
            dev.is_reachable = True
            dev.status = Device.STATUS_OK
        else:
            dev.ping_fail_count += 1
            dev.ping_ok_count = 0
            # Если 3 неудачи подряд и устройство считается доступным — создаем алерт
            # Переоткрываем алерт, если 3+ фейла подряд, даже если is_reachable уже False
            # (иначе после "моргания" устройства алерт закрывается и больше не открывается).
            if dev.ping_fail_count >= DEVICE_PING_FAILS:
                dev.is_reachable = False
                dev.status = Device.STATUS_UNREACHABLE
                active = Alert.objects.filter(
                    device=dev, metric_key="device_unreachable", state=Alert.STATE_FIRING
                ).first()
                if not active:
                    alert = Alert.objects.create(
                        device=dev,
                        metric_key="device_unreachable",
                        severity=Alert.SEVERITY_CRIT,
                        state=Alert.STATE_FIRING,
                        value=dev.ping_fail_count,
                        threshold=DEVICE_PING_FAILS,
                        message=f"Устройство {dev.name} ({dev.ip}) недоступно ({DEVICE_PING_FAILS} неудачных проверок подряд)"
                    )
                    try:
                        notify_alert_created(alert)
                    except Exception as e:
                        logger.warning('TG notify failed: %s', e)
                logger.warning(f"Device {dev.name} ({dev.ip}) is UNREACHABLE")
        
        dev.save(update_fields=["ping_fail_count", "ping_ok_count", "last_ping_at", "is_reachable", "status"])




@shared_task(name="monitoring.poll_arp", soft_time_limit=800, time_limit=850)
@singleton(ttl=900)
def poll_arp():
    """Собирает ARP-таблицы с устройств (D-Link, Eltex) через SNMP."""
    from apps.devices.models import Device
    import asyncio
    import logging
    
    devices = Device.objects.exclude(vendor__name__icontains='Olencom')
    for dev in devices:
        if not dev.snmp_community:
            continue
        try:
            asyncio.run(_poll_arp_device(dev))
        except Exception as e:
            logger.warning(f"poll_arp {dev.name} ({dev.ip}): {e}")


async def _poll_arp_device(device):
    """Асинхронно собирает ARP с одного устройства."""
    from apps.monitoring.snmp import SnmpClient, SnmpTarget
    
    target = SnmpTarget(
        host=device.ip,
        community=device.snmp_community or "public",
        port=device.snmp_port or 161,
    )
    client = SnmpClient(target)
    
    # OID для ARP-таблицы (ipNetToMediaTable): 1.3.6.1.2.1.4.22.1
    mac_rows = await client.walk("1.3.6.1.2.1.4.22.1.2")
    ip_rows = await client.walk("1.3.6.1.2.1.4.22.1.3")
    
    # Строим словарь: if_index + ip → mac
    arp_data = {}
    for oid, mac_val in mac_rows:
        parts = oid.split('.')
        if len(parts) >= 15:
            if_index = int(parts[10])
            ip = '.'.join(parts[11:15])
            mac_hex = mac_val.asOctets().hex() if hasattr(mac_val, 'asOctets') else str(mac_val)
            mac = ':'.join(mac_hex[i:i+2] for i in range(0, 12, 2)).upper()
            # Пропускаем broadcast и multicast
            if mac == 'FF:FF:FF:FF:FF:FF' or mac.startswith('01:'):
                continue
            arp_data[(if_index, ip)] = mac
    
    # Сохраняем в БД (синхронно, через database_sync_to_async)
    await _save_arp(device, arp_data)
    
    import logging
    logger.info(f"poll_arp {device.name} ({device.ip}): {len(arp_data)} записей")


@database_sync_to_async
def _save_arp(device, arp_data):
    """Синхронно сохраняет ARP-записи в БД."""
    from apps.monitoring.models import ArpEntry, Interface
    
    ArpEntry.objects.filter(device=device).delete()
    
    for (if_index, ip), mac in arp_data.items():
        iface = Interface.objects.filter(device=device, if_index=if_index).first()
        iface_name = iface.name if iface else ''
        
        ArpEntry.objects.create(
            device=device,
            ip=ip,
            mac=mac,
            interface_index=if_index,
            interface_name=iface_name,
        )


@shared_task(name="monitoring.poll_fdb", soft_time_limit=800, time_limit=850)
@singleton(ttl=900)
def poll_fdb():
    """Собирает FDB-таблицы с устройств (D-Link, Eltex) через SNMP."""
    from apps.devices.models import Device
    import asyncio
    
    devices = Device.objects.exclude(vendor__name__icontains='Olencom')
    for dev in devices:
        if not dev.snmp_community:
            continue
        try:
            asyncio.run(_poll_fdb_device(dev))
        except Exception as e:
            logger.warning(f"poll_fdb {dev.name} ({dev.ip}): {e}")


async def _poll_fdb_device(device):
    """Асинхронно собирает FDB с одного устройства."""
    from apps.monitoring.snmp import SnmpClient, SnmpTarget
    
    target = SnmpTarget(
        host=device.ip,
        community=device.snmp_community or "public",
        port=device.snmp_port or 161,
    )
    client = SnmpClient(target)
    
    # OID для FDB: dot1dTpFdbPort (1.3.6.1.2.1.17.4.3.1.2)
    fdb_rows = await client.walk("1.3.6.1.2.1.17.4.3.1.2")
    
    # OID для сопоставления bridge_port → if_index: dot1dBasePortIfIndex (1.3.6.1.2.1.17.1.4.1.2)
    port_if_rows = await client.walk("1.3.6.1.2.1.17.1.4.1.2")
    port_to_ifindex = {}
    for oid, ifindex_val in port_if_rows:
        parts = oid.split('.')
        bridge_port = int(parts[-1])
        port_to_ifindex[bridge_port] = int(ifindex_val)
    
    # Строим словарь: bridge_port → MAC
    fdb_data = {}
    for oid, port_val in fdb_rows:
        parts = oid.split('.')
        if len(parts) >= 6:
            mac_parts = parts[-6:]
            mac = ':'.join(f'{int(p):02X}' for p in mac_parts)
            bridge_port = int(port_val)
            if bridge_port > 0:  # 0 = нет порта (multicast)
                fdb_data[mac] = bridge_port
    
    # Сохраняем в БД
    await _save_fdb(device, fdb_data, port_to_ifindex)
    
    logger.info(f"poll_fdb {device.name} ({device.ip}): {len(fdb_data)} записей")


@database_sync_to_async
def _save_fdb(device, fdb_data, port_to_ifindex):
    """Синхронно сохраняет FDB-записи в БД."""
    from apps.monitoring.models import FdbEntry, Interface
    
    FdbEntry.objects.filter(device=device).delete()
    
    for mac, bridge_port in fdb_data.items():
        # Пытаемся найти if_index по bridge_port (dot1dBasePortIfIndex)
        # Пока просто сохраняем bridge_port
        if_index = port_to_ifindex.get(bridge_port)
        iface_name = ''
        if if_index:
            from apps.monitoring.models import Interface
            iface = Interface.objects.filter(device=device, if_index=if_index).first()
            iface_name = iface.name if iface else ''
        
        FdbEntry.objects.create(
            device=device,
            mac=mac,
            bridge_port=bridge_port,
            if_index=if_index,
            interface_name=iface_name,
        )

# Регистрация задач traffic_monitor
from .tasks_traffic import poll_broadcast_rate, poll_all_subnets_broadcast  # noqa: F401
