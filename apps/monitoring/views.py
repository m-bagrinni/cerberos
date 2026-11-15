"""Глобальные вкладки «Метрики» и «SFP/DDM» модуля «Локальная сеть»."""
from django.shortcuts import render

from apps.devices.models import Device
from apps.monitoring.display import group_metrics
from apps.monitoring.models import Interface


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
        groups = group_metrics(metrics)

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
