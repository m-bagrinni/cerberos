"""Станции: список со статусом, карточка (оборудование, линии, IP, алерты), избранное."""
from __future__ import annotations

from django.db.models import Q

from ..core import Ctx, route
from ..ui import DOT, KB, RULE, SEV_ICON, Notice, Screen, ago, cut, esc, header, num, paginate, site_url
from .common import alert_short, metric_label, station_states

FILTERS = {"all": "Все", "bad": "⚠️ С проблемами", "fav": "⭐ Избранные"}
LINK_DOT = {"ok": "🟢", "warn": "🟡", "degraded": "🟠", "crit": "🔴", "unknown": "⚪"}


def _fav_ids(user) -> set:
    from apps.accounts.models import UserPreferences
    prefs = UserPreferences.objects.filter(user=user).first()
    return set(prefs.favorite_stations.values_list("pk", flat=True)) if prefs else set()


@route("st.l", section="stations")
def station_list(ctx: Ctx, flt: str = "all", page: str = "0") -> Screen:
    from apps.stations.models import Station
    flt = flt if flt in FILTERS else "all"
    stations = list(Station.objects.all())
    states = station_states([s.pk for s in stations])
    fav = _fav_ids(ctx.user)
    bad = [s for s in stations if states[s.pk]["state"] != "ok"]
    rows = {"all": stations, "bad": bad, "fav": [s for s in stations if s.pk in fav]}[flt]
    rank = {"crit": 0, "warn": 1, "ok": 2}
    rows = sorted(rows, key=lambda s: (rank[states[s.pk]["state"]], s.sort_order, s.name))
    items, page, pages = paginate(rows, page, 12)

    crit = sum(states[s.pk]["state"] == "crit" for s in stations)
    sub = f"{len(stations)} станций · " + (f"🔴 {crit} · 🟡 {len(bad) - crit}" if bad else "все в норме")
    lines = [header("🗺", "Станции", sub), RULE]
    for s in items:
        c = states[s.pk]
        extra = " · ".join(x for x in (f"🔴 {c['crit']}" if c["crit"] else "", f"🟡 {c['warn']}" if c["warn"] else "") if x)
        lines.append(f"{DOT[c['state']]} {'⭐ ' if s.pk in fav else ''}<b>{esc(s.name)}</b>" + (f"  {extra}" if extra else ""))
    if not items:
        lines.append({"fav": "Избранных нет — отметьте ⭐ в карточке станции.", "bad": "Все станции в норме. ✅"}.get(flt, "Пусто."))
    kb = KB()
    kb.row(*[KB.b(("• " if k == flt else "") + label, f"st.l:{k}:0") for k, label in FILTERS.items()])
    kb.grid([KB.b(f"{DOT[states[s.pk]['state']]} {cut(s.name, 20)}", f"st.c:{s.pk}") for s in items], 2)
    kb.pager("st.l", page, pages, flt)
    kb.row(KB.url("🗺 Карта на сайте", site_url("/local/stations/")))
    kb.nav(refresh=f"st.l:{flt}:{page}")
    return Screen("\n".join(lines), kb)


@route("st.c", section="stations")
def station_card(ctx: Ctx, st_id: str, toast: str = "") -> Screen:
    from apps.alerts.models import Alert
    from apps.stations.models import OpticalLink, Station, StationDevice, StationNe
    from apps.subnets.models import IpAddress
    st = Station.objects.filter(pk=int(st_id)).first()
    if st is None:
        raise Notice("Станция не найдена.")
    state = station_states([st.pk])[st.pk]
    devs = [x.device for x in StationDevice.objects.filter(station=st).select_related("device")]
    nes = [x.ne for x in StationNe.objects.filter(station=st).select_related("ne")]
    ips = IpAddress.objects.filter(station=st)
    ip_total, ip_alive = ips.count(), ips.filter(is_alive=True).count()
    ip_down = list(ips.filter(is_monitored=True, is_alive=False)[:5])
    links = list(OpticalLink.objects.filter(is_active=True).filter(Q(station_a=st) | Q(station_b=st))
                 .select_related("station_a", "station_b")[:10])
    alerts = list(Alert.objects.filter(state="firing").filter(
        Q(device__in=devs) | Q(ne_device__in=nes) | Q(ip_address__station=st))
        .select_related("device", "ne_device", "ip_address", "metric")[:8])
    fav = st.pk in _fav_ids(ctx.user)

    lines = [f"{DOT[state['state']]} <b>{esc(st.name)}</b>{' ⭐' if fav else ''}"]
    if st.description:
        lines.append(f"<i>{esc(cut(st.description, 160))}</i>")
    lines.append(RULE)
    if devs:
        lines.append("📟 <b>Оборудование</b>")
        for d in devs[:8]:
            dot = {"ok": "🟢", "unreachable": "🔴"}.get(d.status, "⚪")
            lines.append(f"  {dot} {esc(cut(d.name, 28))} <code>{esc(d.ip)}</code>")
    if nes:
        lines.append("🔌 <b>NE</b>: " + ", ".join(f"NE-{n.ne_id}" for n in nes[:8]))
    if links:
        lines.append("〰 <b>Оптические линии</b>")
        for l in links:
            other = l.station_b if l.station_a_id == st.pk else l.station_a
            rx = l.rx_a_dbm if l.station_a_id == st.pk else l.rx_b_dbm
            lines.append(f"  {LINK_DOT.get(l.link_status, '⚪')} → {esc(cut(other.name if other else '?', 22))}"
                         + (f" · RX {num(rx, 'dBm')}" if rx is not None else ""))
    if ip_total:
        lines.append(f"🌐 IP: {ip_alive} из {ip_total} отвечают")
        for ip in ip_down:
            lines.append(f"  🔴 <code>{esc(ip.ip)}</code> {esc(cut(ip.hostname, 20))} — молчит " + (ago(ip.last_seen_at) if ip.last_seen_at else "давно"))
    if alerts:
        lines += ["", f"🚨 <b>Алерты: {len(alerts)}</b>"]
        for a in alerts[:5]:
            lines.append(f"{SEV_ICON.get(a.severity, '•')} {esc(cut(alert_short(a), 26))} · {esc(metric_label(a))}")
    if not (devs or nes or links or ip_total):
        lines.append("К станции пока ничего не привязано.")

    kb = KB()
    if ctx.acc.can_view("alerts"):
        kb.grid([KB.b(f"{SEV_ICON.get(a.severity, '•')} {cut(alert_short(a), 22)}", f"al.c:{a.pk}") for a in alerts[:4]], 2)
    if ctx.acc.can_view("devices"):
        kb.grid([KB.b(f"📟 {cut(d.name, 20)}", f"dv.c:{d.pk}") for d in devs[:4]], 2)
    kb.row(KB.b("☆ Убрать из избранного" if fav else "⭐ В избранное", f"st.f:{st.pk}"))
    kb.row(KB.url("🗺 На карте", site_url("/local/stations/")))
    kb.nav(back="st.l:all:0", refresh=f"st.c:{st.pk}")
    return Screen("\n".join(lines), kb, toast=toast)


@route("st.f", section="stations")
def station_fav(ctx: Ctx, st_id: str) -> Screen:
    """Избранное — личная настройка, хватает права на просмотр."""
    from apps.accounts.models import UserPreferences
    from apps.stations.models import Station
    st = Station.objects.filter(pk=int(st_id)).first()
    if st is None:
        raise Notice("Станция не найдена.")
    prefs = UserPreferences.for_user(ctx.user)
    if prefs.favorite_stations.filter(pk=st.pk).exists():
        prefs.favorite_stations.remove(st)
        toast = "☆ Убрано из избранного"
    else:
        prefs.favorite_stations.add(st)
        toast = "⭐ Добавлено в избранное — станция на главной сайта"
    return station_card(ctx, st_id, toast=toast)
