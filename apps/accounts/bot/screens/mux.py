"""Мультиплексоры (NE): шлюзы → список NE → карточка с DDM по портам, опрос."""
from __future__ import annotations

import logging
import threading

from ..core import Ctx, route
from ..ui import DOT, KB, RULE, SEV_ICON, Notice, Screen, ago, ago_back, cut, esc, header, num, paginate, site_url
from .common import metric_label

logger = logging.getLogger(__name__)


def _ne_alerts() -> dict:
    from apps.alerts.models import Alert
    out = {}
    for nid, sev in Alert.objects.filter(state="firing", ne_device__isnull=False).values_list("ne_device_id", "severity"):
        c = out.setdefault(nid, [0, 0])
        c[0 if sev in ("crit", "power") else 1] += 1
    return out


def _st(c) -> str:
    return "crit" if c and c[0] else "warn" if c and c[1] else "ok"


@route("ne.g", section="mux")
def gateways(ctx: Ctx) -> Screen:
    from django.db.models import Count
    from apps.devices.models import Device
    alerts = _ne_alerts()
    gws = list(Device.objects.filter(ne_devices__isnull=False).annotate(n=Count("ne_devices", distinct=True))
               .order_by("name").distinct())
    lines = [header("🔌", "Оптика (NE)", f"{len(gws)} шлюзов"), RULE]
    kb = KB()
    for g in gws:
        ids = list(g.ne_devices.values_list("pk", flat=True))
        crit = sum(alerts.get(i, [0, 0])[0] for i in ids)
        warn = sum(alerts.get(i, [0, 0])[1] for i in ids)
        st = "crit" if crit else "warn" if warn else "ok"
        extra = f" · 🔴 {crit}" if crit else f" · 🟡 {warn}" if warn else ""
        lines.append(f"{DOT[st]} <b>{esc(g.name)}</b>  <code>{esc(g.ip)}</code> · {g.n} NE{extra}")
        kb.row(KB.b(f"{DOT[st]} {cut(g.name, 26)} · {g.n} NE", f"ne.l:{g.pk}:0"))
    if not gws:
        lines.append("Шлюзов с NE нет.")
    kb.nav(refresh="ne.g")
    return Screen("\n".join(lines), kb)


@route("ne.l", section="mux")
def ne_list(ctx: Ctx, gw_id: str, page: str = "0") -> Screen:
    from apps.devices.models import Device, NeDevice
    gw = Device.objects.filter(pk=int(gw_id)).first()
    if gw is None:
        raise Notice("Шлюз не найден.")
    alerts = _ne_alerts()
    nes = list(NeDevice.objects.filter(gateway=gw).order_by("ne_id"))
    rank = {"crit": 0, "warn": 1, "ok": 2}
    nes.sort(key=lambda n: rank[_st(alerts.get(n.pk))])
    items, page, pages = paginate(nes, page, 12)
    lines = [header("🔌", gw.name, f"{len(nes)} NE · <code>{esc(gw.ip)}</code>"), RULE]
    for n in items:
        lines.append(f"{DOT[_st(alerts.get(n.pk))]} NE-{n.ne_id} <b>{esc(cut(n.display_name, 32))}</b>")
    kb = KB()
    kb.grid([KB.b(f"{DOT[_st(alerts.get(n.pk))]} NE-{n.ne_id} {cut(n.display_name, 14)}", f"ne.c:{n.pk}")
             for n in items], 2)
    kb.pager("ne.l", page, pages, gw.pk)
    if ctx.acc.can_edit("mux"):
        kb.row(KB.b("📡 Опросить все NE шлюза", f"ne.P:{gw.pk}"))
    kb.nav(back="ne.g", refresh=f"ne.l:{gw.pk}:{page}")
    return Screen("\n".join(lines), kb)


@route("ne.c", section="mux")
def ne_card(ctx: Ctx, ne_id: str, toast: str = "") -> Screen:
    from apps.alerts.models import Alert
    from apps.devices.models import NeDevice
    from apps.monitoring.models import MetricValue
    from apps.stations.models import StationNe
    ne = NeDevice.objects.select_related("gateway").filter(pk=int(ne_id)).first()
    if ne is None:
        raise Notice("NE не найден.")
    keys = ("sfp_rx_dbm", "sfp_tx_dbm", "sfp_temp")
    vals, last = {}, None
    for key, inst, v, ts in (MetricValue.objects.filter(ne_device=ne, metric__key__in=keys)
                             .order_by("-ts").values_list("metric__key", "instance", "value", "ts")[:400]):
        vals.setdefault((str(inst), key), v)
        last = last or ts
    alerts = list(Alert.objects.filter(state="firing", ne_device=ne).select_related("metric")[:6])
    crit = sum(a.severity in ("crit", "power") for a in alerts)
    st = "crit" if crit else "warn" if alerts else "ok"

    lines = [f"{DOT[st]} <b>NE-{ne.ne_id} {esc(ne.display_name)}</b>",
             f"Шлюз: {esc(ne.gateway.name)} <code>{esc(ne.gateway.ip)}</code>",
             f"Данные: {ago_back(last)}" if last else "Данных DDM пока нет", RULE]
    ports = sorted({p for p, _k in vals}, key=lambda p: (len(p), p))
    if ports:
        tbl = ["Порт    RX      TX     °C"]
        for p in ports[:8]:
            tbl.append(f"{p:<5}{num(vals.get((p, 'sfp_rx_dbm'))):>7} {num(vals.get((p, 'sfp_tx_dbm'))):>7} "
                       f"{num(vals.get((p, 'sfp_temp')), digits=0):>5}")
        lines.append("<pre>" + esc("\n".join(tbl)) + "</pre>")
    stations = list(StationNe.objects.filter(ne=ne).select_related("station")[:2])
    if stations:
        lines.append("🗺 Станция: " + ", ".join(esc(s.station.name) for s in stations))
    if alerts:
        lines += ["", f"🚨 <b>Алерты: {len(alerts)}</b>"]
        for a in alerts[:4]:
            lines.append(f"{SEV_ICON.get(a.severity, '•')} {esc(metric_label(a))} · {ago(a.started_at)}")

    kb = KB()
    if ctx.acc.can_edit("mux"):
        kb.row(KB.b("📡 Опросить NE", f"ne.p:{ne.pk}"))
    kb.grid([KB.b(f"{SEV_ICON.get(a.severity, '•')} {cut(metric_label(a), 24)}", f"al.c:{a.pk}") for a in alerts[:4]], 2)
    if stations and ctx.acc.can_view("stations"):
        kb.row(KB.b(f"🗺 {cut(stations[0].station.name, 24)}", f"st.c:{stations[0].station_id}"))
    kb.row(KB.url("🌐 На сайте", site_url("/local/multiplexers/")))
    kb.nav(back=f"ne.l:{ne.gateway_id}:0", refresh=f"ne.c:{ne.pk}")
    return Screen("\n".join(lines), kb, toast=toast)


def _bg_poll(gateway_ip: str, ne_ids: list | None, label: str, tg_id: int) -> None:
    def work():
        from django.core.management import call_command
        from django.db import close_old_connections
        from apps.accounts.telegram_notify import send_telegram_message
        ok = True
        try:
            if ne_ids:
                call_command("poll_olencom", gateway_ip, ne=ne_ids)
            else:
                call_command("poll_olencom", gateway_ip)
        except Exception:  # noqa: BLE001
            ok = False
            logger.exception("bot ne poll %s", gateway_ip)
        finally:
            close_old_connections()
        send_telegram_message(tg_id, f"📡 Опрос {esc(label)} " + ("завершён — обновите карточку." if ok else "завершился ошибкой."))
    threading.Thread(target=work, daemon=True).start()


@route("ne.p", section="mux", edit=True)
def ne_poll(ctx: Ctx, ne_id: str) -> Screen:
    from apps.devices.models import NeDevice
    ne = NeDevice.objects.select_related("gateway").filter(pk=int(ne_id)).first()
    if ne is None:
        raise Notice("NE не найден.")
    _bg_poll(ne.gateway.ip, [ne.ne_id], f"NE-{ne.ne_id}", ctx.tg_id)
    return ne_card(ctx, ne_id, toast="📡 Опрос запущен")


@route("ne.P", section="mux", edit=True)
def gw_poll(ctx: Ctx, gw_id: str) -> Screen:
    from apps.devices.models import Device
    gw = Device.objects.filter(pk=int(gw_id)).first()
    if gw is None:
        raise Notice("Шлюз не найден.")
    _bg_poll(gw.ip, None, gw.name, ctx.tg_id)
    scr = ne_list(ctx, gw_id)
    scr.toast = "📡 Опрос всех NE запущен"
    return scr
