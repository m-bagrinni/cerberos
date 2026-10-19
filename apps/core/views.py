"""Views для веб-интерфейса."""
from django.db.models import Count
from django.shortcuts import get_object_or_404, render

from apps.devices.models import Device, DeviceDiscovery, Vendor
from apps.monitoring.models import Interface, InterfaceSample, MetricValue, Vlan


def dashboard_classic(request):
    """Master-detail: слева список устройств, справа — детали выбранного."""
    devices = list(
        Device.objects.select_related("vendor", "profile", "profile_override")
        .order_by("name")
    )

    total = len(devices)
    ok = sum(1 for d in devices if d.status == Device.STATUS_OK)
    unreachable = sum(1 for d in devices if d.status == Device.STATUS_UNREACHABLE)
    unknown = sum(1 for d in devices if d.status == Device.STATUS_UNKNOWN)

    # выбранное устройство: из query ?ip=... или первое в списке
    selected_ip = request.GET.get("ip")
    selected_device = None
    if selected_ip:
        selected_device = next((d for d in devices if d.ip == selected_ip), None)
    if not selected_device and devices:
        selected_device = devices[0]

    context = {
        "devices": devices,
        "selected_device": selected_device,
        "stats": {
            "total": total,
            "ok": ok,
            "unreachable": unreachable,
            "unknown": unknown,
        },
    }
    return render(request, "core/dashboard.html", context)


def device_panel(request, ip):
    """HTML-фрагмент правой панели — детали устройства. Для HTMX-обновлений."""
    device = get_object_or_404(
        Device.objects.select_related("vendor", "profile", "profile_override"),
        ip=ip,
    )

    profile = device.effective_profile
    metrics = []
    latest = {}
    if profile:
        metrics = list(profile.get_effective_metrics().values())
        for m in metrics:
            latest[m.key] = (
                MetricValue.objects.filter(device=device, metric=m)
                .order_by("-ts")
                .first()
            )

    iface_count = Interface.objects.filter(
        device=device,
        if_type__in=Interface.PHYSICAL_IF_TYPES,
        if_index__lt=1024,
    ).count()

    top_ports = []
    if iface_count:
        from django.db.models import F
        recent_ts = (
            InterfaceSample.objects
            .filter(interface__device=device)
            .order_by("-ts")
            .values_list("ts", flat=True)
            .first()
        )
        if recent_ts:
            top_ports = list(
                InterfaceSample.objects
                .filter(interface__device=device, ts=recent_ts)
                .select_related("interface")
                .order_by(F("in_bps").desc(nulls_last=True))[:5]
            )

    context = {
        "device": device,
        "metrics": metrics,
        "latest": latest,
        "iface_count": iface_count,
        "top_ports": top_ports,
    }
    return render(request, "core/_device_panel.html", context)



def devices_list(request):
    devices = Device.objects.select_related(
        "vendor", "profile", "profile_override"
    ).order_by("name")
    return render(request, "core/devices_list.html", {"devices": devices})


def device_detail(request, ip):
    device = get_object_or_404(
        Device.objects.select_related("vendor", "profile", "profile_override"),
        ip=ip,
    )
    context = {
        "device": device,
        "metrics_count": (
            len(device.effective_profile.get_effective_metrics())
            if device.effective_profile else 0
        ),
        "interfaces_count": Interface.objects.filter(
            device=device,
            if_type__in=Interface.PHYSICAL_IF_TYPES,
            if_index__lt=1024,
        ).count(),
        "tab": "overview",
    }
    return render(request, "core/device_detail.html", context)


def device_metrics(request, ip):
    device = get_object_or_404(Device, ip=ip)
    metrics = []
    metric_values = {}
    if device.effective_profile:
        metrics = list(device.effective_profile.get_effective_metrics().values())
        for m in metrics:
            metric_values[m.key] = (
                MetricValue.objects.filter(device=device, metric=m)
                .order_by("-ts")
                .first()
            )
    context = {
        "device": device,
        "metrics": metrics,
        "metric_values": metric_values,
        "tab": "metrics",
    }
    return render(request, "core/device_metrics.html", context)


def device_interfaces(request, ip):
    device = get_object_or_404(Device, ip=ip)

    # Только физические порты (ifType=6 ethernetCsmacd, ifIndex < 1024)
    interfaces = list(
        Interface.objects.filter(
            device=device,
            if_type__in=Interface.PHYSICAL_IF_TYPES,
            if_index__lt=1024,
        )
        .prefetch_related("vlan_links__vlan")
        .order_by("if_index")
    )

    # последний сэмпл трафика для каждого интерфейса
    samples_by_idx = {}
    for i in interfaces:
        s = i.samples.order_by("-ts").first()
        if s:
            samples_by_idx[i.if_index] = s

    # есть ли SFP-модули
    sfp_by_idx = {}
    for i in interfaces:
        try:
            sfp_by_idx[i.if_index] = i.sfp
        except Exception:
            pass

    rows = [
        {
            "iface": i,
            "sample": samples_by_idx.get(i.if_index),
            "sfp": sfp_by_idx.get(i.if_index),
        }
        for i in interfaces
    ]

    context = {
        "device": device,
        "interfaces": interfaces,
        "rows": rows,
        "tab": "interfaces",
    }
    return render(request, "core/device_interfaces.html", context)



def device_sfp(request, ip):
    """Страница SFP-модулей (DDM)."""
    device = get_object_or_404(Device, ip=ip)
    interfaces = Interface.objects.filter(device=device).order_by("if_index")

    sfps = []
    for i in interfaces:
        try:
            sfps.append(i.sfp)
        except Exception:
            pass

    context = {
        "device": device,
        "sfps": sfps,
        "tab": "sfp",
    }
    return render(request, "core/device_sfp.html", context)



def device_vlans(request, ip):
    device = get_object_or_404(Device, ip=ip)
    vlans = Vlan.objects.filter(device=device).order_by("vlan_id")
    context = {"device": device, "vlans": vlans, "tab": "vlans"}
    return render(request, "core/device_vlans.html", context)


def discoveries_list(request):
    discoveries = (
        DeviceDiscovery.objects.select_related("device").order_by("-created_at")[:100]
    )
    return render(request, "core/discoveries_list.html", {"discoveries": discoveries})


def discovery_detail(request, pk):
    discovery = get_object_or_404(
        DeviceDiscovery.objects.select_related("device"), pk=pk
    )
    return render(request, "core/discovery_detail.html", {
        "discovery": discovery,
        "rows": discovery.snapshot or [],
    })


# ============================================================
# Глобальные табы (верхний уровень навигации)
# ============================================================

def tab_metrics(request):
    """
    Tab 2 «Метрики» — все устройства, все графики.
    Включает псевдо-группу «traffic» — суммарный трафик по устройству.
    """
    devices = list(
        Device.objects.select_related("vendor", "profile", "profile_override")
        .order_by("name")
    )

    all_group_ids = set()
    device_cards = []
    for d in devices:
        profile = d.effective_profile
        if not profile:
            continue
        metrics = list(profile.get_effective_metrics().values())
        groups = _group_metrics(metrics)

        # Добавляем псевдо-группу «Трафик» — суммарный трафик устройства
        # (данные из InterfaceSample, не из MetricValue)
        groups.append({
            "id": "traffic",
            "title": "Трафик портов",
            "metrics": [],   # не используется для traffic — данные через отдельный API
            "is_traffic": True,
            "device_ip": d.ip,
        })

        for g in groups:
            all_group_ids.add(g["id"])
        device_cards.append({"device": d, "groups": groups})

    group_titles = {
        "cpu": "Процессор", "mem": "Память DRAM", "flash": "Flash",
        "temp": "Температура", "uptime": "Uptime", "power": "Питание",
        "traffic": "Трафик портов", "other": "Прочее",
    }
    filter_groups = [
        {"id": gid, "title": group_titles.get(gid, gid)}
        for gid in ["cpu", "mem", "flash", "temp", "uptime", "power", "traffic", "other"]
        if gid in all_group_ids
    ]

    context = {
        "device_cards": device_cards,
        "filter_groups": filter_groups,
        "total_devices": len(devices),
    }
    return render(request, "core/tab_metrics.html", context)


def tab_sfp(request):
    """
    Tab 3 «SFP/DDM» — плитки всех SFP-модулей всех устройств.
    Группировка по устройству. Пустые группы скрываются.
    """
    devices = list(
        Device.objects.select_related("vendor", "profile", "profile_override")
        .order_by("name")
    )

    groups = []
    total_sfp = 0
    for d in devices:
        interfaces = Interface.objects.filter(device=d).order_by("if_index")
        sfps = []
        for i in interfaces:
            try:
                sfps.append(i.sfp)
            except Exception:
                pass
        if sfps:
            groups.append({"device": d, "sfps": sfps})
            total_sfp += len(sfps)

    # для фильтра — список устройств с SFP
    filter_devices = [g["device"] for g in groups]

    context = {
        "groups": groups,
        "total_sfp": total_sfp,
        "total_devices": len(devices),
        "filter_devices": filter_devices,
    }
    return render(request, "core/tab_sfp.html", context)



# Маппинг универсальных метрик (разные ключи у разных вендоров)
_CPU_KEYS = ("cpu_5m", "cpu_5s", "cpu_1m")
_MEM_KEYS = ("mem_dram_pct", "mem_free_pct", "mem_dram_used", "mem_free")
_TEMP_KEYS = ("temp_current", "temp_sensor")

def _pick_metric(device, keys):
    """
    Возвращает (key, value, unit) — первое доступное значение из списка keys.
    Если ничего нет — (None, None, None).
    """
    for key in keys:
        mv = (
            MetricValue.objects
            .filter(device=device, metric__key=key)
            .select_related("metric")
            .order_by("-ts")
            .first()
        )
        if mv is not None and mv.value is not None:
            return key, mv.value, mv.metric.unit
    return None, None, None


def _device_card_data(device):
    """
    Собирает данные для плитки устройства:
    - cpu / mem / temp (последние значения)
    - порты: up / total (только физические)
    """
    cpu_key, cpu_val, cpu_unit = _pick_metric(device, _CPU_KEYS)
    mem_key, mem_val, mem_unit = _pick_metric(device, _MEM_KEYS)
    temp_key, temp_val, temp_unit = _pick_metric(device, _TEMP_KEYS)

    physical = Interface.objects.filter(
        device=device,
        if_type__in=Interface.PHYSICAL_IF_TYPES,
        if_index__lt=150,
    )
    ports_total = physical.count()
    ports_up = physical.filter(admin_status=1).count()

    return {
        "device": device,
        "cpu": {"value": cpu_val, "unit": cpu_unit, "key": cpu_key},
        "mem": {"value": mem_val, "unit": mem_unit, "key": mem_key},
        "temp": {"value": temp_val, "unit": temp_unit, "key": temp_key},
        "ports": {"up": ports_up, "total": ports_total},
    }


def dashboard(request):
    """
    Tab 1 «Устройства» — плитки, сгруппированные по статусу.
    Порядок групп: Недоступны → Неизвестно → Онлайн.
    Пустые группы скрываются.
    """
    devices = list(
        Device.objects.select_related("vendor", "profile", "profile_override")
        .order_by("name")
    )

    by_status = {Device.STATUS_UNREACHABLE: [], Device.STATUS_UNKNOWN: [], Device.STATUS_OK: []}
    for d in devices:
        card = _device_card_data(d)
        by_status.setdefault(d.status, []).append(card)

    groups = []
    group_meta = [
        (Device.STATUS_UNREACHABLE, "Недоступны", "crit"),
        (Device.STATUS_UNKNOWN, "Неизвестно", "unknown"),
        (Device.STATUS_OK, "Онлайн", "ok"),
    ]
    for status, label, css in group_meta:
        items = by_status.get(status, [])
        if items:
            groups.append({"status": status, "label": label, "css": css, "devices": items})

    stats = {
        "total": len(devices),
        "ok": sum(1 for d in devices if d.status == Device.STATUS_OK),
        "unreachable": sum(1 for d in devices if d.status == Device.STATUS_UNREACHABLE),
        "unknown": sum(1 for d in devices if d.status == Device.STATUS_UNKNOWN),
    }

    context = {
        "groups": groups,
        "stats": stats,
    }
    return render(request, "core/dashboard.html", context)


# ============================================================
# Вкладки модального окна устройства
# ============================================================

def device_modal_overview(request, ip):
    """Вкладка «Обзор» в модалке: инфо + все метрики карточками."""
    device = get_object_or_404(
        Device.objects.select_related("vendor", "profile", "profile_override"),
        ip=ip,
    )
    profile = device.effective_profile
    metrics_view = []
    if profile:
        metrics = list(profile.get_effective_metrics().values())
        for m in metrics:
            qs = MetricValue.objects.filter(device=device, metric=m).order_by("-ts")
            if m.scale == "walk":
                mv = qs.filter(value__gt=0).first() or qs.first()
            else:
                mv = qs.first()

            fval, pct = _fmt_metric_value(mv, m)
            metrics_view.append({
                "key": m.key,
                "name": m.name,
                "unit": m.unit,
                "icon": _metric_icon(m.key),
                "value": fval,
                "percent": pct,
                "has_value": mv is not None and mv.value is not None,
            })

    context = {
        "device": device,
        "metrics_view": metrics_view,
    }
    return render(request, "core/_modal_tab_overview.html", context)


def device_modal_interfaces(request, ip):
    """Вкладка «Интерфейсы» в модалке: таблица портов + управление."""
    device = get_object_or_404(Device, ip=ip)

    interfaces = list(
        Interface.objects.filter(
            device=device,
            if_type__in=Interface.PHYSICAL_IF_TYPES,
            if_index__lt=150,
        )
        .prefetch_related("vlan_links__vlan")
        .order_by("if_index")
    )

    samples_by_idx = {}
    for i in interfaces:
        s = i.samples.order_by("-ts").first()
        if s:
            samples_by_idx[i.if_index] = s

    rows = [
        {"iface": i, "sample": samples_by_idx.get(i.if_index)}
        for i in interfaces
    ]

    context = {
        "device": device,
        "interfaces": interfaces,
        "rows": rows,
    }
    return render(request, "core/_modal_tab_interfaces.html", context)


def device_modal_charts(request, ip):
    """Вкладка «Графики» в модалке: сгруппированные метрики."""
    device = get_object_or_404(Device, ip=ip)
    profile = device.effective_profile
    metric_groups = []
    if profile:
        metrics = list(profile.get_effective_metrics().values())
        metric_groups = _group_metrics(metrics)

    context = {
        "device": device,
        "metric_groups": metric_groups,
    }
    return render(request, "core/_modal_tab_charts.html", context)


def device_modal_ddm(request, ip):
    """Вкладка «DDM» в модалке: SFP-модули."""
    device = get_object_or_404(Device, ip=ip)
    interfaces = Interface.objects.filter(device=device).order_by("if_index")

    sfps = []
    for i in interfaces:
        try:
            sfps.append(i.sfp)
        except Exception:
            pass

    context = {
        "device": device,
        "sfps": sfps,
    }
    return render(request, "core/_modal_tab_ddm.html", context)


# ============================================================
# Форматирование метрик для UI
# ============================================================

import re as _re


def _fmt_metric_value(mv, metric):
    """
    Возвращает кортеж (formatted_value, percent_or_None) для карточки метрики.
    percent — число 0..100 для прогресс-бара (только для %-метрик), иначе None.
    """
    if mv is None or mv.value is None:
        return "—", None

    v = mv.value
    key = metric.key
    unit = (metric.unit or "").lower()

    # --- Проценты ---
    if unit == "%" or "pct" in key or "percent" in key:
        pct = max(0.0, min(100.0, float(v)))
        return f"{pct:.1f} %", pct

    # --- Температура ---
    if "temp" in key:
        return f"{float(v):.1f} °C", None

    # --- Uptime (секунды) ---
    if "uptime" in key:
        sec = int(v)
        days, rem = divmod(sec, 86400)
        hours, rem = divmod(rem, 3600)
        minutes = rem // 60
        parts = []
        if days: parts.append(f"{days} дн")
        if hours: parts.append(f"{hours} ч")
        if minutes and not days: parts.append(f"{minutes} мин")
        return " ".join(parts) or f"{sec} с", None

    # --- Байты (mem_total, mem_free, mem_used и т.п.) ---
    if unit in ("b", "byte", "bytes") or key.endswith("_total") or key.endswith("_free") or key.endswith("_used"):
        b = float(v)
        for suf, div in (("TB", 1024**4), ("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
            if abs(b) >= div:
                return f"{b/div:.2f} {suf}", None
        return f"{b:.0f} B", None

    # --- KB (D-Link отдаёт в KB) ---
    if unit == "kb":
        kb = float(v)
        if kb >= 1024 * 1024:
            return f"{kb/(1024*1024):.2f} GB", None
        if kb >= 1024:
            return f"{kb/1024:.2f} MB", None
        return f"{kb:.0f} KB", None

    # --- dBm / V / mA / прочее — просто округляем ---
    if isinstance(v, (int, float)):
        return f"{float(v):.2f}{(' ' + metric.unit) if metric.unit else ''}", None

    return str(v), None


def _metric_icon(key):
    """Иконка Bootstrap Icons по ключу метрики."""
    k = key.lower()
    if "cpu" in k: return "bi-cpu"
    if "mem" in k or "dram" in k or "flash" in k or "ram" in k: return "bi-memory"
    if "temp" in k: return "bi-thermometer-half"
    if "uptime" in k: return "bi-clock-history"
    if "power" in k: return "bi-plug"
    if "voltage" in k: return "bi-lightning"
    return "bi-activity"


# ============================================================
# Группировка метрик для графиков (вкладка «Графики»)
# ============================================================

def _group_metrics(metrics):
    """
    Группирует метрики по префиксу ключа.
    Возвращает список групп: [{ id, title, color, metrics: [...] }]
    """
    def cls(key):
        k = key.lower()
        if k.startswith("cpu"): return "cpu"
        if "flash" in k: return "flash"
        if k.startswith("mem") or k.startswith("dram") or "ram" in k: return "mem"
        if "temp" in k: return "temp"
        if "uptime" in k: return "uptime"
        if "power" in k: return "power"
        return "other"

    groups_map = {
        "cpu":    {"id": "cpu",    "title": "Процессор",     "order": 1},
        "mem":    {"id": "mem",    "title": "Память DRAM",   "order": 2},
        "flash":  {"id": "flash",  "title": "Flash",         "order": 3},
        "temp":   {"id": "temp",   "title": "Температура",   "order": 4},
        "uptime": {"id": "uptime", "title": "Uptime",        "order": 5},
        "power":  {"id": "power",  "title": "Питание",       "order": 6},
        "other":  {"id": "other",  "title": "Прочее",        "order": 99},
    }

    grouped = {}
    for m in metrics:
        c = cls(m.key)
        grouped.setdefault(c, {"meta": groups_map[c], "metrics": []})["metrics"].append(m)

    # Внутри группы — сортируем метрики по ключу (5s, 1m, 5m)
    for g in grouped.values():
        g["metrics"].sort(key=lambda m: m.key)

    # Формируем итоговый список, сортируя группы по order
    result = []
    for c in sorted(grouped.keys(), key=lambda k: grouped[k]["meta"]["order"]):
        g = grouped[c]
        # id canvas: chart-<group>-<ip> (безопасный)
        result.append({
            "id": g["meta"]["id"],
            "title": g["meta"]["title"],
            "metrics": [
                {"key": m.key, "name": m.name, "unit": m.unit}
                for m in g["metrics"]
            ],
        })
    return result


# ============================================================
# Алерты
# ============================================================

def alerts_list(request):
    """Страница алертов — активные + история."""
    from apps.alerts.models import Alert

    firing = list(
        Alert.objects
        .filter(state=Alert.STATE_FIRING)
        .select_related("device", "ne_device", "ne_device__gateway", "metric")
        .order_by("-severity", "-started_at")
    )
    resolved = list(
        Alert.objects
        .filter(state=Alert.STATE_OK)
        .select_related("device", "ne_device", "ne_device__gateway", "metric")
        .order_by("-resolved_at")[:200]
    )

    firing_crit = sum(1 for a in firing if a.severity == Alert.SEVERITY_CRIT)
    firing_warn = sum(1 for a in firing if a.severity == Alert.SEVERITY_WARN)

    context = {
        "firing": firing,
        "resolved": resolved,
        "counts": {
            "firing_total": len(firing),
            "firing_crit": firing_crit,
            "firing_warn": firing_warn,
            "resolved_total": len(resolved),
        },
    }
    return render(request, "core/alerts_list.html", context)


# ============================================================
def _mux_summary(cards):
    """
    Сводка по всем NE: количество онлайн, SFP up, средний RX, температуры.
    Берём данные из уже собранных cards (без доп. запросов к БД).
    """
    total = len(cards)
    online = sum(1 for c in cards if c["ne"].status == "ok")

    rx_values = []
    temp_values = []
    sfp_up = 0
    sfp_total = 0

    for c in cards:
        for inst, d in (c.get("sfp") or {}).items():
            sfp_total += 1
            link = d.get("link")
            rx = d.get("rx")
            temp = d.get("temp")
            if link is not None and link >= 0.5:
                sfp_up += 1
            if rx is not None:
                rx_values.append(rx)
            if temp is not None:
                temp_values.append(temp)

    avg_rx   = (sum(rx_values) / len(rx_values))   if rx_values   else None
    min_rx   = min(rx_values)                      if rx_values   else None
    avg_temp = (sum(temp_values) / len(temp_values)) if temp_values else None
    max_temp = max(temp_values)                    if temp_values else None
    bad_rx   = sum(1 for v in rx_values if v is not None and v < -25)

    return {
        "total": total,
        "online": online,
        "offline": total - online,
        "sfp_up": sfp_up,
        "sfp_total": sfp_total,
        "avg_rx": round(avg_rx, 2) if avg_rx is not None else None,
        "min_rx": round(min_rx, 2) if min_rx is not None else None,
        "avg_temp": round(avg_temp, 1) if avg_temp is not None else None,
        "max_temp": round(max_temp, 1) if max_temp is not None else None,
        "bad_rx_count": bad_rx,
    }


# Мультиплексоры (Olencom RP1000)
# ============================================================

def multiplexers_list(request):
    """
    Вкладка «Мультиплексоры» — карточки NeDevice (каждое NE = отдельный мультиплексор).
    """
    from apps.devices.models import NeDevice
    from django.db.models import Q

    # Все шлюзы-мультиплексоры
    gateways = Device.objects.filter(
        Q(profile__device_category="multiplexer") |
        Q(profile_override__device_category="multiplexer")
    )

    # Все NE этих шлюзов
    nes = (
        NeDevice.objects
        .filter(gateway__in=gateways)
        .select_related("gateway")
        .order_by("sort_order", "ne_id")
    )

    cards = []
    for ne in nes:
        # SFP метрики: последние значения
        sfp = {}
        for inst in ("1", "2"):
            sfp[inst] = {
                "temp": _last_metric(ne, "sfp_temp",    inst),
                "rx":   _last_metric(ne, "sfp_rx_dbm",  inst),
                "tx":   _last_metric(ne, "sfp_tx_dbm",  inst),
                "link": _last_metric(ne, "sfp_link",    inst),
            }

        # LAN метрики портов 3..6
        # Внимание: SNMP-счётчики eth_status и eth_counter не соответствуют
        # реальному состоянию порта (проверено на NE-1: web показывает LAN4 активным,
        # SNMP отдаёт counter=0.5 MB на порт 6 и 2.1 MB на порт 4).
        # Показываем ТОЛЬКО PVID и Mode — без индикации link/down.
        MODE_DISPLAY = {0: "access", 1: "trunk", 2: "hybrid"}
        lan = {}
        for idx, port in enumerate(("1", "2", "3", "4"), start=1):
            pvid_val = _last_metric(ne, "eth_pvid", port)
            mode_val = _last_metric(ne, "eth_mode", port)
            mode_int = int(mode_val) if mode_val is not None else None
            rx_bytes = _last_metric(ne, "eth_rx_bytes", port) or 0
            link = "up" if rx_bytes > 1024 else "down"

            lan[port] = {
                "idx":          idx,
                "pvid":         int(pvid_val) if pvid_val is not None else None,
                "mode":         mode_int,
                "mode_display": MODE_DISPLAY.get(mode_int, "—"),
                "link":         link,
            }

        # Агрегатный статус для цветной полосы карточки
        agg_status = "unknown"
        sfp_links = [d.get("link") for d in sfp.values() if d.get("link") is not None]
        sfp_rxs = [d.get("rx") for d in sfp.values() if d.get("rx") is not None]
        up_links = [x for x in sfp_links if x == 1]
        down_links = [x for x in sfp_links if x == 0]

        if ne.status != "ok":
            agg_status = "crit"
        elif not up_links:
            agg_status = "unknown"
        elif down_links:
            agg_status = "crit"
        elif sfp_rxs and min(sfp_rxs) < -25:
            agg_status = "crit"
        elif sfp_rxs and min(sfp_rxs) < -20:
            agg_status = "warn"
        elif up_links:
            agg_status = "ok"

        cards.append({
            "ne": ne,
            "sfp": sfp,
            "lan": lan,
            "agg_status": agg_status,
        })

    online = sum(1 for c in cards if c["ne"].status == "ok")
    summary = _mux_summary(cards)

    # Если ?partial=1 — рендерим только grid (для AJAX-обновления)
    if request.GET.get("partial"):
        return render(request, "core/_mux_grid_partial.html", {
            "cards": cards,
        })

    return render(request, "core/multiplexers_list.html", {
        "cards": cards,
        "summary": summary,
        "total": len(cards),
        "online": online,
    })




# ============================================================
# Станции и оптические линии
# ============================================================

def stations_list(request):
    """
    Вкладка «Станции» — список станций с оборудованием (NE + Device).
    ?partial=1 → только партиал (для AJAX-обновления без F5).
    """
    from apps.stations.models import OpticalLink, Station

    stations = (
        Station.objects
        .prefetch_related("nes__ne__gateway", "devices__device__vendor")
        .order_by("sort_order", "name")
    )

    links = (
        OpticalLink.objects
        .select_related("station_a", "station_b")
        .filter(is_active=True)
    )

    # Группируем связи по станции — быстро, без N+1
    links_by_station = {}
    for l in links:
        links_by_station.setdefault(l.station_a_id, []).append(l)
        links_by_station.setdefault(l.station_b_id, []).append(l)

    cards = []
    for st in stations:
        st_links = links_by_station.get(st.id, [])

        nes = list(st.nes.all())
        devices = list(st.devices.all())

        # Агрегат статуса
        statuses = [sn.ne.status for sn in nes] + [sd.device.status for sd in devices]
        if not statuses:
            agg_status = "unknown"
        elif "unreachable" in statuses:
            agg_status = "crit"
        elif all(s == "ok" for s in statuses):
            agg_status = "ok"
        elif any(s == "ok" for s in statuses):
            agg_status = "warn"
        else:
            agg_status = "unknown"

        cards.append({
            "station": st,
            "links": st_links,
            "links_count": len(st_links),
            "ne_count": len(nes),
            "device_count": len(devices),
            "agg_status": agg_status,
        })

    if request.GET.get("partial"):
        return render(request, "core/_stations_list_partial.html", {
            "cards": cards,
        })

    return render(request, "core/stations_list.html", {
        "cards": cards,
        "total": len(cards),
    })


def _last_metric(ne_device, key, instance=""):
    """Последнее значение метрики NE по (key, instance)."""
    from apps.monitoring.models import MetricValue
    mv = (
        MetricValue.objects
        .filter(ne_device=ne_device, metric__key=key, instance=instance)
        .order_by("-ts")
        .first()
    )
    return mv.value if mv else None


# ============================================================
# Модалка NE (мультиплексоры)
# ============================================================

def ne_modal(request, ne_id):
    """Модалка NE: SFP + Ethernet порты."""
    from django.shortcuts import get_object_or_404
    from apps.devices.models import NeDevice
    from apps.monitoring.models import MetricValue

    ne = get_object_or_404(NeDevice.objects.select_related("gateway"), id=ne_id)

    def last(key, instance=""):
        mv = (MetricValue.objects
              .filter(ne_device=ne, metric__key=key, instance=instance)
              .order_by("-ts").first())
        return mv.value if mv else None

    def last_raw(key, instance=""):
        mv = (MetricValue.objects
              .filter(ne_device=ne, metric__key=key, instance=instance)
              .order_by("-ts").first())
        return mv.raw_value if mv else ""

    sfp = {}
    for port in ("1", "2", "3"):
        sfp[port] = {
            "temp":    last("sfp_temp",    port),
            "rx":      last("sfp_rx_dbm",  port),
            "tx":      last("sfp_tx_dbm",  port),
            "bias":    last("sfp_bias",    port),
            "voltage": last("sfp_voltage", port),
            "link":    last("sfp_link",    port),
            "vendor":  last_raw("sfp_vendor", port),
            "pn":      last_raw("sfp_pn",     port),
            "sn":      last_raw("sfp_sn",     port),
        }

    # LAN порты — SNMP индексы 1..4 = LAN1-LAN4.
    # SNMP 5, 6 = WAN1, WAN2.
    # Link из .11: 0=down, 1=link (100M), 2=link (1G/SFP).
    MODE_DISPLAY = {0: "access", 1: "trunk", 2: "hybrid"}
    lan = {}
    for idx, port in enumerate(("1", "2", "3", "4"), start=1):
        pvid_val = last("eth_pvid", port)
        mode_val = last("eth_mode", port)
        enable_val = last("eth_enable", port)
        mode_int = int(mode_val) if mode_val is not None else None
        rx_bytes = last("eth_rx_bytes", port) or 0

        # link = есть трафик (Rx > 1 KB)
        link = "up" if rx_bytes > 1024 else "down"

        lan[port] = {
            "idx":          idx,
            "pvid":         int(pvid_val) if pvid_val is not None else None,
            "mode":         mode_int,
            "mode_display": MODE_DISPLAY.get(mode_int, "—"),
            "enable":       enable_val,
            "link":         link,
        }

    return render(request, "core/_ne_modal_body.html", {"ne": ne, "sfp": sfp, "lan": lan})


def ne_rename_api(request, ne_id):
    """POST {"name": "..."} — переименовать NE."""
    import json
    from django.http import JsonResponse
    from apps.devices.models import NeDevice

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "method not allowed"}, status=405)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid json"}, status=400)

    name = (payload.get("name") or "").strip()[:128]

    try:
        ne = NeDevice.objects.get(id=ne_id)
    except NeDevice.DoesNotExist:
        return JsonResponse({"ok": False, "error": "not found"}, status=404)

    ne.name = name
    ne.save(update_fields=["name"])
    return JsonResponse({"ok": True, "name": ne.display_name, "id": ne.id})


# ─────────────────────────────────────────────────────────────
# Подсети (apps/subnets)
# ─────────────────────────────────────────────────────────────

def subnets_list(request):
    """Список подсетей."""
    from apps.subnets.models import Subnet
    subnets = Subnet.objects.all().order_by("cidr")

    # Сводка
    total = subnets.count()
    active = subnets.filter(is_active=True).count()
    total_ips = sum(s.ips.count() for s in subnets)
    total_alive = sum(s.ips.filter(is_alive=True).count() for s in subnets)

    return render(request, "core/subnets_list.html", {
        "subnets": subnets,
        "summary": {
            "total": total,
            "active": active,
            "total_ips": total_ips,
            "total_alive": total_alive,
        },
    })


def subnet_detail(request, subnet_id):
    """Детали подсети — карта IP + таблица."""
    from django.shortcuts import get_object_or_404
    from apps.subnets.models import Subnet

    subnet = get_object_or_404(Subnet, pk=subnet_id)
    return render(request, "core/subnet_detail.html", {
        "subnet": subnet,
    })

