"""Устройства: список, карточка, порты (вкл/выкл с правом), опрос."""
from __future__ import annotations

import logging
import threading

from django.db.models import Q

from ..core import Ctx, route
from ..ui import (DOT, KB, RULE, SEV_ICON, Notice, Screen, ago, ago_back, cut, esc, header, num, paginate,
                  site_url, when)
from .common import metric_label

logger = logging.getLogger(__name__)

PHYS_TYPES = (6, 62, 71, 117)


def _dev_state(dev, crit: int, warn: int) -> str:
    if dev.status == "unreachable" or crit:
        return "crit"
    if warn:
        return "warn"
    return "ok" if dev.status == "ok" else "unknown"


def _alert_counts() -> dict:
    from apps.alerts.models import Alert
    out = {}
    for did, gid, sev in Alert.objects.filter(state="firing").values_list(
            "device_id", "ne_device__gateway_id", "severity"):
        for d in {did, gid} - {None}:
            c = out.setdefault(d, [0, 0])
            c[0 if sev in ("crit", "power") else 1] += 1
    return out


@route("dv.l", section="devices")
def device_list(ctx: Ctx, flt: str = "all", page: str = "0") -> Screen:
    from apps.devices.models import Device
    counts = _alert_counts()
    devs = list(Device.objects.only("id", "name", "ip", "status").order_by("sort_order", "name"))
    rows = []
    for d in devs:
        c = counts.get(d.pk, [0, 0])
        rows.append((d, _dev_state(d, *c)))
    bad = [r for r in rows if r[1] in ("crit", "warn")]
    if flt == "bad":
        rows = bad
    rank = {"crit": 0, "warn": 1, "unknown": 2, "ok": 3}
    rows.sort(key=lambda r: rank[r[1]])
    items, page, pages = paginate(rows, page)
    sub = f"{len(devs)} всего · " + (f"с проблемами {len(bad)}" if bad else "все в норме")
    lines = [header("📟", "Устройства", sub), RULE]
    for d, st in items:
        lines.append(f"{DOT[st]} <b>{esc(cut(d.name, 30))}</b>  <code>{esc(d.ip)}</code>")
    if not items:
        lines.append("Пусто.")
    kb = KB()
    kb.row(KB.b(("• " if flt != "bad" else "") + "Все", "dv.l:all:0"),
           KB.b(("• " if flt == "bad" else "") + f"⚠️ С проблемами {len(bad)}", "dv.l:bad:0"))
    kb.grid([KB.b(f"{DOT[st]} {cut(d.name, 22)}", f"dv.c:{d.pk}") for d, st in items], 2)
    kb.pager("dv.l", page, pages, flt)
    kb.row(KB.b("🔎 Найти", "find"))
    kb.nav(refresh=f"dv.l:{flt}:{page}")
    return Screen("\n".join(lines), kb)


def _latest_metrics(dev) -> dict:
    from apps.monitoring.models import MetricValue
    seen = {}
    for mv in MetricValue.objects.filter(device=dev).select_related("metric").order_by("-ts")[:150]:
        if not mv.metric:
            continue
        k = (mv.metric.key, mv.instance or "")
        if k not in seen:
            seen[k] = mv.value
    return seen


def _uptime(sec) -> str:
    try:
        sec = int(float(sec))
    except (TypeError, ValueError):
        return "—"
    if sec > 10 ** 8:   # timeticks (сотые доли секунды)
        sec //= 100
    d, rem = divmod(sec, 86400)
    h, m = rem // 3600, rem % 3600 // 60
    return f"{d} д {h} ч" if d else f"{h} ч {m} мин"


@route("dv.c", section="devices")
def device_card(ctx: Ctx, dev_id: str, toast: str = "") -> Screen:
    from apps.alerts.models import Alert
    from apps.devices.models import Device
    from apps.stations.models import StationDevice
    dev = Device.objects.select_related("vendor").filter(pk=int(dev_id)).first()
    if dev is None:
        raise Notice("Устройство не найдено.")
    alerts = list(Alert.objects.filter(state="firing").filter(Q(device=dev) | Q(ne_device__gateway=dev))
                  .select_related("ne_device", "metric").order_by("-started_at")[:6])
    crit = sum(a.severity in ("crit", "power") for a in alerts)
    st = _dev_state(dev, crit, len(alerts) - crit)
    m = _latest_metrics(dev)
    status = {"ok": "доступно", "unreachable": "недоступно"}.get(dev.status, "неизвестно")

    lines = [f"{DOT[st]} <b>{esc(dev.name)}</b>",
             f"<code>{esc(dev.ip)}</code> · {esc(dev.vendor.name if dev.vendor else '—')} {esc(dev.ent_model or '')}",
             RULE,
             f"📶 {status} · опрос {ago_back(dev.last_polled_at)}"]
    up = m.get(("sys_uptime", "")) or next((v for (k, _i), v in m.items() if k == "sys_uptime"), None)
    if up is not None:
        lines.append(f"⏱ Аптайм: {_uptime(up)}")
    cpu = next((m[(k, i)] for (k, i) in m if k in ("cpu_5m", "cpu_1m", "cpu_5s", "cpu_load")), None)
    if cpu is not None:
        lines.append(f"🖥 CPU: <b>{num(cpu, '%', 0)}</b>")
    mu = next((v for (k, i), v in m.items() if k == "mem_dram_used"), None)
    mt = next((v for (k, i), v in m.items() if k == "mem_dram_total"), None)
    if mu and mt:
        lines.append(f"💾 RAM: <b>{mu / mt * 100:.0f}%</b> из {mt / 1024:.0f} MB")
    t = next((v for (k, i), v in m.items() if k == "temp_current"), None)
    if t is not None:
        lines.append(f"🌡 Температура: <b>{num(t, '°C', 0)}</b>")

    sfp = {}
    for (k, inst), v in m.items():
        if k in ("sfp_rx_dbm", "sfp_tx_dbm", "sfp_temp") and inst:
            sfp.setdefault(inst, {})[k] = v
    if sfp:
        tbl = ["Порт   RX      TX      °C"]
        for port in sorted(sfp, key=lambda p: (len(p), p))[:10]:
            v = sfp[port]
            tbl.append(f"{port:<5}{num(v.get('sfp_rx_dbm')):>7} {num(v.get('sfp_tx_dbm')):>7} {num(v.get('sfp_temp'), digits=0):>5}")
        lines += ["", "🔌 <b>SFP</b>", "<pre>" + esc("\n".join(tbl)) + "</pre>"]

    stations = list(StationDevice.objects.filter(device=dev).select_related("station")[:3])
    if stations:
        lines.append("🗺 Станция: " + ", ".join(esc(s.station.name) for s in stations))

    if alerts:
        lines += ["", f"🚨 <b>Алерты: {len(alerts)}</b>"]
        for a in alerts[:4]:
            lines.append(f"{SEV_ICON.get(a.severity, '•')} {esc(metric_label(a))} · {ago(a.started_at)}")

    kb = KB()
    kb.row(KB.b("🖧 Порты", f"dv.i:{dev.pk}:0"),
           KB.b("📡 Опросить", f"dv.p:{dev.pk}") if ctx.acc.can_edit("devices") else None)
    kb.grid([KB.b(f"{SEV_ICON.get(a.severity, '•')} {cut(metric_label(a), 24)}", f"al.c:{a.pk}")
             for a in alerts[:4]], 2)
    if stations and ctx.acc.can_view("stations"):
        kb.row(KB.b(f"🗺 {cut(stations[0].station.name, 24)}", f"st.c:{stations[0].station_id}"))
    kb.row(KB.url("🌐 На сайте", site_url(f"/local/devices/{dev.ip}/")))
    kb.nav(back="dv.l:all:0", refresh=f"dv.c:{dev.pk}")
    return Screen("\n".join(lines), kb, toast=toast)


def _poll_in_background(dev_id: int, ip: str, name: str, tg_id: int) -> None:
    def work():
        from django.core.management import call_command
        from django.db import close_old_connections
        from apps.accounts.telegram_notify import send_telegram_message
        ok = True
        try:
            call_command("poll_device", ip)
        except Exception:  # noqa: BLE001
            ok = False
            logger.exception("bot poll %s", ip)
        finally:
            close_old_connections()
        send_telegram_message(tg_id, (f"📡 Опрос <b>{esc(name)}</b> завершён — обновите карточку."
                                      if ok else f"⚠️ Опрос <b>{esc(name)}</b> завершился ошибкой."))
    threading.Thread(target=work, daemon=True, name=f"bot-poll-{dev_id}").start()


@route("dv.p", section="devices", edit=True)
def device_poll(ctx: Ctx, dev_id: str) -> Screen:
    from apps.devices.models import Device
    dev = Device.objects.filter(pk=int(dev_id)).first()
    if dev is None:
        raise Notice("Устройство не найдено.")
    _poll_in_background(dev.pk, dev.ip, dev.name, ctx.tg_id)
    return device_card(ctx, dev_id, toast="📡 Опрос запущен — пришлю сообщение, когда закончится")


# ── Порты ─────────────────────────────────────────────────────────────────────
def _port_dot(i) -> str:
    if i.admin_status == 2:
        return "⚫"
    return "🟢" if i.oper_status == 1 else "🔴"


def _short(p) -> str:
    """eth1/0/12 → 12; Gi0/1 → Gi0/1 (если не похоже на слот/порт)."""
    name = p.name or f"if{p.if_index}"
    return name.rsplit("/", 1)[-1] if name.count("/") >= 2 else cut(name, 9)


def _speed(mbps) -> str:
    if not mbps:
        return "—"
    return f"{mbps // 1000}G" if mbps >= 1000 else f"{mbps}M"


@route("dv.i", section="devices")
def device_ports(ctx: Ctx, dev_id: str, page: str = "0") -> Screen:
    from apps.devices.models import Device
    dev = Device.objects.filter(pk=int(dev_id)).first()
    if dev is None:
        raise Notice("Устройство не найдено.")
    ports = list(dev.interfaces.filter(if_type__in=PHYS_TYPES, if_index__lt=1000).order_by("if_index"))
    up = sum(p.oper_status == 1 for p in ports)
    items, page, pages = paginate(ports, page, 32)
    down_admin = sum(p.admin_status == 2 for p in ports)
    sub = f"🟢 {up} в работе · 🔴 {len(ports) - up - down_admin} без линка" + (f" · ⚫ {down_admin} выключено" if down_admin else "")
    lines = [header("🖧", f"Порты · {dev.name}", sub), RULE]
    described = [p for p in items if p.alias]
    for p in described:
        lines.append(f"{_port_dot(p)} {esc(_short(p))} · {esc(cut(p.alias, 30))}")
    if not ports:
        lines.append("Порты не найдены.")
    elif not described:
        lines.append("<i>Нажмите порт, чтобы открыть его карточку.</i>")
    kb = KB()
    kb.grid([KB.b(f"{_port_dot(p)} {_short(p)}", f"pt.c:{p.pk}") for p in items], 4)
    kb.pager("dv.i", page, pages, dev.pk)
    kb.nav(back=f"dv.c:{dev.pk}", refresh=f"dv.i:{dev.pk}:{page}")
    return Screen("\n".join(lines), kb)


def _port(pid):
    from apps.monitoring.models import Interface
    p = Interface.objects.select_related("device").filter(pk=int(pid)).first()
    if p is None:
        raise Notice("Порт не найден.")
    return p


@route("pt.c", section="devices")
def port_card(ctx: Ctx, pid: str, toast: str = "") -> Screen:
    p = _port(pid)
    admin = "включён" if p.admin_status == 1 else "выключен администратором"
    oper = "линк есть" if p.oper_status == 1 else "линка нет"
    lines = [f"{_port_dot(p)} <b>{esc(p.name or 'if' + str(p.if_index))}</b> · {esc(p.device.name)}", RULE,
             f"Состояние: <b>{oper}</b> · {admin}",
             f"Скорость: {_speed(p.speed_mbps)}",
             f"Описание: {esc(p.alias) or '—'}"]
    if p.descr:
        lines.append(f"<i>{esc(cut(p.descr, 80))}</i>")
    kb = KB()
    if ctx.acc.has_cap("port_control") and p.is_physical:
        kb.row(KB.b("⛔ Выключить порт", f"pt.t:{p.pk}:down") if p.admin_status == 1
               else KB.b("✅ Включить порт", f"pt.t:{p.pk}:up"))
    kb.nav(back=f"dv.i:{p.device_id}:0", refresh=f"pt.c:{p.pk}")
    return Screen("\n".join(lines), kb, toast=toast)


@route("pt.t", section="devices", cap="port_control")
def port_toggle_ask(ctx: Ctx, pid: str, state: str) -> Screen:
    p = _port(pid)
    verb = "ВЫКЛЮЧИТЬ" if state == "down" else "включить"
    warn = ("\n\n⚠️ Если через этот порт идёт ваше подключение или аплинк — связь пропадёт."
            if state == "down" else "")
    kb = KB().row(KB.b(f"Да, {verb.lower()}", f"pt.T:{p.pk}:{state}"), KB.b("Отмена", f"pt.c:{p.pk}"))
    return Screen(f"❓ <b>{verb} порт {esc(p.name)}</b> на {esc(p.device.name)}?{warn}", kb)


@route("pt.T", section="devices", cap="port_control")
def port_toggle(ctx: Ctx, pid: str, state: str) -> Screen:
    from apps.devices.models import ActionLog
    from apps.monitoring.api import IF_ADMIN_DOWN, IF_ADMIN_UP, InterfaceViewSet
    if state not in ("up", "down"):
        raise Notice("Неверное действие.")
    p = _port(pid)
    if not p.is_physical:
        raise Notice("Можно управлять только физическими портами.")
    log = ActionLog.objects.create(
        device=p.device, user=ctx.user,
        action=ActionLog.ACTION_PORT_UP if state == "up" else ActionLog.ACTION_PORT_DOWN,
        target=(p.name or f"if{p.if_index}") + " (Telegram)", status=ActionLog.STATUS_PENDING)
    try:
        InterfaceViewSet()._set_admin_status(p.device, p.if_index, state)
    except Exception as e:  # noqa: BLE001
        log.status, log.message = ActionLog.STATUS_ERROR, str(e)[:500]
        log.save(update_fields=["status", "message"])
        raise Notice(f"Не удалось: {cut(str(e), 150)}")
    p.admin_status = IF_ADMIN_UP if state == "up" else IF_ADMIN_DOWN
    p.save(update_fields=["admin_status"])
    log.status, log.message = ActionLog.STATUS_SUCCESS, f"Порт {p.name} → {state} (Telegram)"
    log.save(update_fields=["status", "message"])
    return port_card(ctx, pid, toast="✅ Порт включён" if state == "up" else "⛔ Порт выключен")
