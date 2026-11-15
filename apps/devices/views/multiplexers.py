"""Мультиплексоры (Olencom RP1000): список NE, модалка NE, переименование."""
from django.shortcuts import render

from apps.devices.models import Device


def multiplexers_list(request):
    """
    Мультиплексоры — дерево шлюзов + NE слева, детали NE справа.
    """
    from apps.devices.models import NeDevice
    from django.db.models import Q

    # Все шлюзы-мультиплексоры
    gateways = list(
        Device.objects.filter(
            Q(profile__device_category="multiplexer") |
            Q(profile_override__device_category="multiplexer")
        ).order_by("name")
    )

    # Все NE этих шлюзов
    nes = list(
        NeDevice.objects
        .filter(gateway__in=gateways)
        .select_related("gateway")
        .order_by("sort_order", "ne_id")
    )

    # Дерево: шлюз → NE
    tree = []
    for gw in gateways:
        gw_nes = [ne for ne in nes if ne.gateway_id == gw.id]
        if gw_nes:
            tree.append({
                "gateway": gw,
                "nes": gw_nes,
            })

    # Карточки (для деталей)
    cards = []
    for ne in nes:
        # Определяем все SFP-порты (по наличию sfp_rx_dbm)
        from apps.monitoring.models import MetricValue
        sfp_instances = set(
            MetricValue.objects.filter(
                ne_device=ne, metric__key="sfp_rx_dbm"
            ).values_list("instance", flat=True).distinct()
        )

        sfp = {}
        for inst in sorted(sfp_instances, key=lambda x: int(x) if x.isdigit() else 999):
            sfp[inst] = {
                "temp": _last_metric(ne, "sfp_temp", inst),
                "rx": _last_metric(ne, "sfp_rx_dbm", inst),
                "tx": _last_metric(ne, "sfp_tx_dbm", inst),
                "link": _last_metric(ne, "sfp_link", inst),
            }

        # LAN метрики портов 1..4
        MODE_DISPLAY = {0: "access", 1: "trunk", 2: "hybrid"}
        lan = {}
        for idx, port in enumerate(("1", "2", "3", "4"), start=1):
            pvid_val = _last_metric(ne, "eth_pvid", port)
            mode_val = _last_metric(ne, "eth_mode", port)
            mode_int = int(mode_val) if mode_val is not None else None
            rx_bytes = _last_metric(ne, "eth_rx_bytes", port) or 0
            link = "up" if rx_bytes > 1024 else "down"

            lan[port] = {
                "idx": idx,
                "pvid": int(pvid_val) if pvid_val is not None else None,
                "mode": mode_int,
                "mode_display": MODE_DISPLAY.get(mode_int, "—"),
                "link": link,
            }

        # Агрегатный статус
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
        else:
            agg_status = "ok"

        cards.append({
            "ne": ne,
            "sfp": sfp,
            "lan": lan,
            "agg_status": agg_status,
        })

    # Статистика
    total = len(nes)
    online = sum(1 for ne in nes if ne.status == "ok")

    # Сериализуем cards в JSON (для JS)
    import json
    cards_json = []
    for c in cards:
        ne = c["ne"]
        cards_json.append({
            "ne_pk": ne.id,
            "ne_id": ne.ne_id,
            "ne_name": ne.display_name,
            "sfp": c.get("sfp", {}),
            "lan": c.get("lan", {}),
            "agg_status": c.get("agg_status", "unknown"),
        })

    context = {
        "tree": tree,
        "cards": cards,
        "cards_json": json.dumps(cards_json),
        "total": total,
        "online": online,
    }
    return render(request, "core/multiplexers_list.html", context)


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


def ne_modal(request, ne_id):
    """Модалка NE: SFP + Ethernet порты."""
    from django.shortcuts import get_object_or_404
    from apps.devices.models import NeDevice
    from apps.monitoring.models import MetricValue

    ne = get_object_or_404(NeDevice.objects.select_related("gateway"), ne_id=ne_id)

    # Получаем оптические алармы (OPTA и OPTB)
    alarms = {}
    for key in ['opt_alarm_opta', 'opt_alarm_optb']:
        mv = MetricValue.objects.filter(ne_device=ne, metric__key=key).order_by('-ts').first()
        if mv:
            alarms[key] = mv.raw_value

    # Получаем SFP_PRESENT для OPTA (порт 1) и OPTB (порт 2)
    sfp_present_a = ""
    sfp_present_b = ""
    mv_a = MetricValue.objects.filter(ne_device=ne, metric__key="sfp_present_active", instance="1").order_by('-ts').first()
    if mv_a:
        sfp_present_a = mv_a.raw_value
    mv_b = MetricValue.objects.filter(ne_device=ne, metric__key="sfp_present_active", instance="2").order_by('-ts').first()
    if mv_b:
        sfp_present_b = mv_b.raw_value

    # Разбор байта 1 на биты (каждый бит = аларм)
    ALARM_BITS = {
        7: "OCV",
        0: "NOP",
        # остальные биты — пока не знаем, добавим позже
    }
    for key in ['opt_alarm_opta', 'opt_alarm_optb']:
        if key in alarms:
            try:
                parts = alarms[key].split()
                if len(parts) >= 2:
                    byte1 = int(parts[1], 16)
                    active = [name for bit, name in ALARM_BITS.items() if byte1 & (1 << bit)]
                    alarms[key + '_active'] = active
            except Exception:
                alarms[key + '_active'] = []

    # Расшифровка маски (4 байта: OPTA = байты 0-1, OPTB = байты 2-3)
    ALARM_NAMES = [
        "NOP", "RPD", "LOF", "1E-6", "OCV", "TF", "TD", "RPL",
        "RPH", "LPL", "LTH", "15MIN-EB-TCA", "15MIN-ES-TCA",
        "24HOUR-EB-TCA", "24HOUR-ES-TCA"
    ]
    alarm_list = []
    if alarms.get('opt_local_alarm'):
        try:
            # Парсим строку "00 01 00 01" -> [0, 1, 0, 1]
            parts = alarms['opt_local_alarm'].split()
            if len(parts) == 4:
                opta_mask = (int(parts[0], 16) << 8) | int(parts[1], 16)
                optb_mask = (int(parts[2], 16) << 8) | int(parts[3], 16)
                
                for i, name in enumerate(ALARM_NAMES):
                    bit = 1 << i
                    if opta_mask & bit:
                        alarm_list.append(f"{name} (OPTA)")
                    if optb_mask & bit:
                        alarm_list.append(f"{name} (OPTB)")
        except Exception as e:
            print(f"Ошибка расшифровки алармов: {e}")
    alarms['list'] = alarm_list

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

    return render(request, "core/_ne_modal_body.html", {"sfp_present_a": sfp_present_a, "sfp_present_b": sfp_present_b, "alarm_names": ["NOP", "RPD", "LOF", "1E-6", "OCV", "TF", "TD", "RPL", "RPH", "LPL", "LTH", "15MIN-EB-TCA", "15MIN-ES-TCA", "24HOUR-EB-TCA", "24HOUR-ES-TCA"], "alarms": alarms, "ne": ne, "sfp": sfp, "lan": lan})


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
