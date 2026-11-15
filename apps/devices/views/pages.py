"""Страницы устройств: дерево (dashboard), карточка устройства, вкладки, discovery."""
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from apps.devices.models import Device, DeviceDiscovery
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


def device_tab_partial(request, ip, tab):
    """
    AJAX: возвращает только контент вкладки (партиал, без base.html).
    """
    from django.shortcuts import render, get_object_or_404
    from apps.devices.models import Device
    from apps.monitoring.models import Interface, InterfaceSample, SfpModule, MetricValue, Vlan

    device = get_object_or_404(Device, ip=ip)

    # Общие данные для всех вкладок
    context = {"device": device}

    if tab == "overview":
        context["metrics_count"] = (
            len(device.effective_profile.get_effective_metrics())
            if device.effective_profile else 0
        )
        context["interfaces_count"] = Interface.objects.filter(
            device=device,
            if_type__in=Interface.PHYSICAL_IF_TYPES,
            if_index__lt=1024,
        ).count()
        return render(request, "core/_tab_overview.html", context)

    elif tab == "metrics":
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
        context["metrics"] = metrics
        context["metric_values"] = metric_values
        return render(request, "core/_tab_metrics.html", context)

    elif tab == "interfaces":
        from apps.monitoring.models import InterfaceStatusHistory
        interfaces = list(
            Interface.objects.filter(
                device=device,
                if_type__in=Interface.PHYSICAL_IF_TYPES,
                if_index__lt=1024,
            )
            .prefetch_related("vlan_links__vlan")
            .order_by("if_index")
        )
        # последние сэмплы трафика + последняя история
        samples_by_idx = {}
        history_by_idx = {}
        for iface in interfaces:
            last_sample = (
                InterfaceSample.objects.filter(interface=iface)
                .order_by("-ts")
                .first()
            )
            samples_by_idx[iface.if_index] = last_sample
            last_history = (
                InterfaceStatusHistory.objects.filter(interface=iface)
                .order_by("-changed_at")
                .first()
            )
            history_by_idx[iface.if_index] = last_history
        rows = [
            {
                "iface": i,
                "sample": samples_by_idx.get(i.if_index),
                "history": history_by_idx.get(i.if_index),
            }
            for i in interfaces
        ]
        context["interfaces"] = interfaces
        context["rows"] = rows
        return render(request, "core/_tab_interfaces.html", context)

    elif tab == "alerts":
        from apps.alerts.models import Alert
        alerts = list(
            Alert.objects.filter(device=device)
            .order_by("-started_at")
        )
        context["alerts"] = alerts
        return render(request, "core/_tab_alerts.html", context)

    elif tab == "vlans":
        vlans = list(
            Vlan.objects.filter(device=device).order_by("vlan_id")
        )
        context["vlans"] = vlans
        return render(request, "core/_tab_vlans.html", context)

    elif tab == "sfp":
        sfps = list(
            SfpModule.objects.filter(interface__device=device)
            .select_related("interface")
            .order_by("interface__if_index")
        )
        context["sfps"] = sfps
        return render(request, "core/_tab_sfp.html", context)

    else:
        return render(request, "core/_tab_overview.html", context)


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


def dashboard(request):
    """
    Tab 1 «Устройства» — дерево слева (категории → подкатегории → устройства) + детали справа.
    Состояние лампочек/счётчиков дальше обновляет JS через dashboard_status (тот же _build_tree).
    """
    from apps.devices.models import DeviceSubcategory

    state = _build_tree()

    # Список подкатегорий (для перемещения)
    subcategories = list(
        DeviceSubcategory.objects.filter(is_active=True)
        .select_related("category")
        .order_by("category__sort_order", "sort_order", "name")
    )

    context = {
        "tree": state["tree"],
        "stats": state["stats"],
        "ne_stats": state["ne_stats"],
        "vendors": state["vendors"],
        "categories": state["categories"],
        "subcategories": subcategories,
    }
    return render(request, "core/dashboard.html", context)


def dashboard_status(request):
    """
    JSON для автообновления /local/:
      stats, ne_stats — счётчики в шапке;
      devices: {ip: {lamp, status, alerts[], last_polled_at, last_ping_at}};
      categories: {slug: {lamp, count}}; subcategories: {"cat/sub": {lamp, count}}.
    """
    from django.http import JsonResponse

    state = _build_tree()
    tree = state["tree"]

    resp = JsonResponse({
        "generated_at": timezone.now().isoformat(),
        "stats": state["stats"],
        "ne_stats": state["ne_stats"],
        "devices": {
            d.ip: {
                "lamp": d.lamp,
                "status": d.status,
                "alerts": d.active_alerts,
                "last_polled_at": d.last_polled_at.isoformat() if d.last_polled_at else None,
                "last_ping_at": d.last_ping_at.isoformat() if d.last_ping_at else None,
            }
            for d in state["devices"]
        },
        "categories": {
            slug: {"lamp": c["status"], "count": c["count"]} for slug, c in tree.items()
        },
        "subcategories": {
            f"{slug}/{sub_slug}": {"lamp": sub["status"], "count": len(sub["devices"])}
            for slug, c in tree.items() for sub_slug, sub in c["subcategories"].items()
        },
    })
    resp["Cache-Control"] = "no-store"
    return resp


def _build_tree():
    """Собирает дерево категорий/подкатегорий, цвета лампочек и счётчики — одно место для страницы и JSON."""
    from apps.devices.models import DeviceCategory

    devices = _tree_devices()
    _apply_lamps(devices)

    categories = list(DeviceCategory.objects.filter(is_active=True).order_by("sort_order", "name"))
    tree = {
        cat.slug: {"label": cat.name, "icon": cat.icon, "devices": [], "subcategories": {}}
        for cat in categories
    }

    for d in devices:
        if not d.category or d.category.slug not in tree:
            continue
        cat_data = tree[d.category.slug]
        if d.subcategory:
            sub = cat_data["subcategories"].setdefault(d.subcategory.slug, {
                "label": d.subcategory.name,
                "slug": d.subcategory.slug,
                "devices": [],
                "sort_order": d.subcategory.sort_order,
            })
            sub["devices"].append(d)
        else:
            cat_data["devices"].append(d)

    for cat_data in tree.values():
        # Подкатегории и устройства без подкатегории — в одном списке по sort_order
        items = [
            {"type": "subcategory", "sort_order": sub.get("sort_order", 100), "data": sub}
            for sub in cat_data["subcategories"].values()
        ] + [
            {"type": "device", "sort_order": getattr(d, "sort_order", 0), "data": d}
            for d in cat_data["devices"]
        ]
        items.sort(key=lambda x: (x["sort_order"], x["type"]))
        cat_data["items"] = items

        all_devices = list(cat_data["devices"])
        for sub in cat_data["subcategories"].values():
            sub["status"] = _agg_status(sub["devices"])
            all_devices += sub["devices"]
        # Раньше: число и цвет только по устройствам без подкатегории — категория из одних
        # подкатегорий показывала «0», а проблемы в подкатегориях не красили категорию.
        cat_data["count"] = len(all_devices)
        cat_data["status"] = _agg_status(all_devices)

    stats = {
        "total": len(devices),
        "ok": sum(1 for d in devices if d.status == Device.STATUS_OK),
        "unreachable": sum(1 for d in devices if d.status == Device.STATUS_UNREACHABLE),
        "unknown": sum(1 for d in devices if d.status == Device.STATUS_UNKNOWN),
        "alerts": sum(1 for d in devices if d.active_alerts),
    }

    vendor_names = sorted({d.vendor.name for d in devices if d.vendor})

    return {
        "tree": tree,
        "devices": devices,
        "categories": categories,
        "stats": stats,
        "ne_stats": _ne_stats(),
        "vendors": vendor_names,
    }


def _tree_devices():
    """Устройства для дерева дашборда (шлюзы NE mux.* — не устройства дерева)."""
    return list(
        Device.objects
        .select_related("vendor", "profile", "profile_override", "category", "subcategory")
        .exclude(name__startswith="mux.")
        .order_by("sort_order", "name")
    )


def _apply_lamps(devices):
    """
    Ставит d.active_alerts, d.has_alert, d.lamp и d.lamp_title (подсказка).
    lamp — CSS-модификатор tree-*__dot--{ok|warn|crit|unknown}.
    d.status не трогаем: раньше сюда писали "alert", а для unreachable/alert CSS-класса нет —
    лампочка становилась прозрачной, и видно было только зелёные.
    """
    from apps.alerts.models import Alert

    by_device = {}
    for a in (
        Alert.objects.filter(state="firing", device__isnull=False)
        .order_by("-started_at")
        .values("device_id", "severity", "message", "started_at")
    ):
        by_device.setdefault(a["device_id"], []).append({
            "severity": a["severity"],
            "message": a["message"],
            "started_at": a["started_at"].isoformat() if a["started_at"] else None,
        })

    for d in devices:
        d.active_alerts = by_device.get(d.id, [])
        d.has_alert = bool(d.active_alerts)
        if d.status == Device.STATUS_UNREACHABLE:
            d.lamp = "crit"
        elif any(a["severity"] in ("crit", "power") for a in d.active_alerts):
            d.lamp = "crit"
        elif d.active_alerts:
            d.lamp = "warn"
        elif d.status == Device.STATUS_OK:
            d.lamp = "ok"
        else:
            d.lamp = "unknown"
        parts = [d.get_status_display()] + [a["message"] for a in d.active_alerts]
        d.lamp_title = " · ".join(parts)


def _ne_stats():
    """Мультиплексоры (NE Olencom): в дереве их нет, поэтому в шапке — сводка."""
    from apps.alerts.models import Alert
    from apps.devices.models import NeDevice

    statuses = list(NeDevice.objects.values_list("id", "status"))
    alert_ne = set(
        Alert.objects.filter(state="firing", ne_device__isnull=False).values_list("ne_device_id", flat=True)
    )
    return {
        "total": len(statuses),
        "unreachable": sum(1 for _, s in statuses if s == "unreachable"),
        "problem": sum(1 for nid, s in statuses if s != "ok" or nid in alert_ne),
    }


def _agg_status(devices):
    """Агрегированный цвет: crit, если есть красные; warn, если есть жёлтые или неизвестные; иначе ok."""
    if not devices:
        return "unknown"
    lamps = {getattr(d, "lamp", "unknown") for d in devices}
    if "crit" in lamps:
        return "crit"
    if lamps & {"warn", "unknown"}:
        return "warn"
    return "ok"
