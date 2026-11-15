"""Вкладки модального окна устройства (HTMX-фрагменты)."""
from django.shortcuts import get_object_or_404, render

from apps.devices.models import Device
from apps.monitoring.display import fmt_metric_value, group_metrics, metric_icon
from apps.monitoring.models import Interface, MetricValue


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

            fval, pct = fmt_metric_value(mv, m)
            metrics_view.append({
                "key": m.key,
                "name": m.name,
                "unit": m.unit,
                "icon": metric_icon(m.key),
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
        metric_groups = group_metrics(metrics)

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
