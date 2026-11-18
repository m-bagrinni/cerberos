"""Подсети и IP, серверы, службы (контейнеры и очереди Celery)."""
from __future__ import annotations

from django.utils import timezone

from ..core import Ctx, ask, on_input, route
from ..ui import (DOT, KB, RULE, Notice, Screen, ago, ago_back, bar, cut, esc, header, num, paginate, plural,
                  site_url, when)
from .common import server_online


# ── Подсети ───────────────────────────────────────────────────────────────────
@route("sn.l", section="subnets")
def subnet_list(ctx: Ctx) -> Screen:
    from django.db.models import Count, Q
    from apps.subnets.models import Subnet
    subnets = list(Subnet.objects.annotate(
        total=Count("ips"), alive=Count("ips", filter=Q(ips__is_alive=True)),
        down=Count("ips", filter=Q(ips__is_monitored=True, ips__is_alive=False))).order_by("cidr"))
    lines = [header("🌐", "Подсети", plural(len(subnets), "подсеть", "подсети", "подсетей")), RULE]
    kb = KB()
    for s in subnets:
        frac = s.alive / s.total if s.total else 0
        warn = f" · 🔴 {s.down}" if s.down else ""
        lines.append(f"<b>{esc(s.cidr)}</b> {esc(cut(s.name, 18))}\n"
                     f"<code>{bar(frac)}</code> {s.alive} живых{warn}")
        kb.row(KB.b(f"{'🔴' if s.down else '🟢'} {s.cidr} · {cut(s.name, 14)}", f"sn.c:{s.pk}"))
    if not subnets:
        lines.append("Подсетей нет.")
    kb.row(KB.b("🔎 Найти IP", "ip.f"))
    kb.nav(refresh="sn.l")
    return Screen("\n".join(lines), kb)


@route("sn.c", section="subnets")
def subnet_card(ctx: Ctx, sid: str) -> Screen:
    from apps.subnets.models import Subnet
    s = Subnet.objects.filter(pk=int(sid)).first()
    if s is None:
        raise Notice("Подсеть не найдена.")
    st = s.stats()
    down = list(s.ips.filter(is_monitored=True, is_alive=False).order_by("ip")[:10])
    lines = [header("🌐", s.cidr, esc(s.name)), RULE,
             f"Живых: <b>{st['alive']}</b> из {st['total']}  <code>{bar(st['alive'] / st['total'] if st['total'] else 0)}</code>",
             f"Занято {st['used']} · резерв {st['reserved']} · свободно {st['free']}",
             f"На мониторинге: {st['monitored']}" + (f" · 🔴 молчат {len(down)}" if down else " · все отвечают")]
    if down:
        lines += ["", "🔴 <b>Не отвечают</b>"]
        for ip in down:
            lines.append(f"  <code>{esc(ip.ip)}</code> {esc(cut(ip.hostname, 18))} · {ago(ip.last_seen_at)}")
    kb = KB()
    kb.grid([KB.b(f"🔴 {ip.ip}", f"ip.c:{ip.pk}") for ip in down], 2)
    kb.row(KB.b("🔎 Найти IP", "ip.f"), KB.url("🌐 Карта IP", site_url("/local/subnets/")))
    kb.nav(back="sn.l", refresh=f"sn.c:{s.pk}")
    return Screen("\n".join(lines), kb)


def _ip(iid):
    from apps.subnets.models import IpAddress
    ip = IpAddress.objects.select_related("subnet", "station").filter(pk=int(iid)).first()
    if ip is None:
        raise Notice("IP не найден.")
    return ip


@route("ip.c", section="subnets")
def ip_card(ctx: Ctx, iid: str, toast: str = "") -> Screen:
    ip = _ip(iid)
    dot = "🟢" if ip.is_alive else ("🔴" if ip.is_monitored else "⚪")
    lines = [f"{dot} <b>{esc(ip.ip)}</b>" + (f" · {esc(ip.hostname)}" if ip.hostname else ""), RULE,
             f"Подсеть: {esc(ip.subnet.cidr)} {esc(ip.subnet.name)}",
             f"Состояние: <b>{'отвечает' if ip.is_alive else 'не отвечает'}</b>"
             + (f" · {num(ip.last_rtt_ms, 'мс')}" if ip.is_alive and ip.last_rtt_ms is not None else ""),
             f"Последний ответ: {when(ip.last_seen_at)} ({ago_back(ip.last_seen_at)})" if ip.last_seen_at else "Ни разу не отвечал",
             f"Мониторинг: {'включён 👁' if ip.is_monitored else 'выключен'}"]
    if ip.mac:
        lines.append(f"MAC: <code>{esc(ip.mac)}</code>")
    if ip.station_id:
        lines.append(f"🗺 Станция: {esc(ip.station.name)}")
    if ip.description:
        lines.append(f"<i>{esc(cut(ip.description, 120))}</i>")
    kb = KB()
    if ctx.acc.can_edit("subnets"):
        kb.row(KB.b("📡 Пинговать", f"ip.p:{ip.pk}"),
               KB.b("👁 Снять с мониторинга" if ip.is_monitored else "👁 На мониторинг", f"ip.m:{ip.pk}"))
    if ip.station_id and ctx.acc.can_view("stations"):
        kb.row(KB.b(f"🗺 {cut(ip.station.name, 24)}", f"st.c:{ip.station_id}"))
    kb.nav(back=f"sn.c:{ip.subnet_id}", refresh=f"ip.c:{ip.pk}")
    return Screen("\n".join(lines), kb, toast=toast)


@route("ip.p", section="subnets", edit=True)
def ip_ping(ctx: Ctx, iid: str) -> Screen:
    from apps.subnets.models import IpAddress
    from apps.subnets.scanner import ping_one
    ip = _ip(iid)
    r = ping_one(ip.ip, retries=2)
    now = timezone.now()
    ip.is_alive, ip.last_ping_at, ip.last_rtt_ms, ip.packet_loss_pct = r.alive, now, r.rtt_ms, r.packet_loss_pct
    if r.alive:
        ip.last_seen_at = now
        if ip.state == IpAddress.STATE_UNKNOWN:
            ip.state = IpAddress.STATE_USED
    ip.save()
    return ip_card(ctx, iid, toast=f"🟢 Отвечает · {num(r.rtt_ms, 'мс')}" if r.alive else "🔴 Не отвечает")


@route("ip.m", section="subnets", edit=True)
def ip_monitor(ctx: Ctx, iid: str) -> Screen:
    ip = _ip(iid)
    ip.is_monitored = not ip.is_monitored
    ip.save(update_fields=["is_monitored", "updated_at"])
    return ip_card(ctx, iid, toast="👁 На мониторинге" if ip.is_monitored else "Снят с мониторинга")


@route("ip.f", section="subnets")
def ip_find_ask(ctx: Ctx) -> Screen:
    return ask(ctx, "ip.find", [], header("🔎", "Найти IP") + "\n\nВведите IP-адрес, его часть или имя хоста.",
               cancel="sn.l")


@on_input("ip.find", section="subnets")
def ip_find(ctx: Ctx, text: str) -> Screen:
    from django.db.models import Q
    from apps.subnets.models import IpAddress
    q = text.strip()[:64]
    found = list(IpAddress.objects.filter(Q(ip__startswith=q) | Q(hostname__icontains=q) | Q(mac__icontains=q))
                 .order_by("ip")[:16]) if q else []
    if len(found) == 1:
        return ip_card(ctx, str(found[0].pk))
    lines = [header("🔎", f"IP: {q}", f"найдено {len(found)}"), RULE]
    lines += [f"{'🟢' if i.is_alive else '⚪'} <code>{esc(i.ip)}</code> {esc(cut(i.hostname, 24))}" for i in found] or ["Ничего не найдено."]
    kb = KB().grid([KB.b(f"{'🟢' if i.is_alive else '⚪'} {i.ip}", f"ip.c:{i.pk}") for i in found], 2)
    kb.row(KB.b("🔎 Искать ещё", "ip.f"))
    kb.nav(back="sn.l")
    return Screen("\n".join(lines), kb)


# ── Серверы ───────────────────────────────────────────────────────────────────
@route("sv.l", section="servers")
def server_list(ctx: Ctx) -> Screen:
    from apps.servers.models import Server
    servers = list(Server.objects.filter(is_active=True).select_related("category").order_by("category__name", "name"))
    on = sum(server_online(s) for s in servers)
    lines = [header("🖥", "Серверы", f"{on} из {len(servers)} на связи"), RULE]
    kb = KB()
    cat = None
    for s in sorted(servers, key=lambda s: (server_online(s), s.category.name if s.category else "", s.name)):
        c = s.category.name if s.category else "Без категории"
        if c != cat:
            lines.append(f"<b>{esc(c)}</b>")
            cat = c
        dot = "🟢" if server_online(s) else "🔴"
        lines.append(f"  {dot} {esc(s.name)} <code>{esc(s.ip_address)}</code>"
                     + ("" if server_online(s) else f" · нет связи {ago(s.last_seen)}"))
    kb.grid([KB.b(f"{'🟢' if server_online(s) else '🔴'} {cut(s.name, 20)}", f"sv.c:{s.pk}") for s in servers], 2)
    kb.row(KB.url("🌐 Серверы на сайте", site_url("/servers/")))
    kb.nav(refresh="sv.l")
    return Screen("\n".join(lines), kb)


@route("sv.c", section="servers")
def server_card(ctx: Ctx, sid: str) -> Screen:
    from apps.servers.models import Server
    s = Server.objects.select_related("category").filter(pk=int(sid)).first()
    if s is None:
        raise Notice("Сервер не найден.")
    latest = {}
    for m in s.metrics.order_by("-created_at")[:300]:
        latest.setdefault(m.metric_key, m)
    online = server_online(s)
    lines = [f"{'🟢' if online else '🔴'} <b>{esc(s.name)}</b>  <code>{esc(s.ip_address)}</code>",
             f"{esc(s.category.name) if s.category else 'Без категории'} · {esc(s.os_type or 'ОС неизвестна')}", RULE,
             f"Связь: {'есть' if online else 'нет'} · последний ответ {ago_back(s.last_seen)}"]
    if latest:
        lines += ["", "📊 <b>Метрики</b>"]
        for key, m in sorted(latest.items())[:14]:
            pct = m.value / 100 if (m.unit or "").strip() == "%" else None
            b = f"  <code>{bar(pct, 8)}</code>" if pct is not None else ""
            lines.append(f"{esc(key)}: <b>{num(m.value, m.unit or '')}</b>{b} · {ago(m.created_at)}")
    if s.description:
        lines += ["", f"<i>{esc(cut(s.description, 200))}</i>"]
    kb = KB().row(KB.url("🌐 На сайте", site_url(f"/servers/{s.pk}/")))
    kb.nav(back="sv.l", refresh=f"sv.c:{s.pk}")
    return Screen("\n".join(lines), kb)


# ── Службы ────────────────────────────────────────────────────────────────────
QUEUE_LABELS = {"critical": "оптика и алерты", "high": "пинг, Telegram", "default": "SNMP-опросы",
                "low": "SFP, сканирование", "traffic": "broadcast"}


def _containers() -> list:
    from apps.services.api import _docker_ps
    return _docker_ps()


@route("sy.l", section="services")
def services(ctx: Ctx, toast: str = "") -> Screen:
    from apps.core.watchdog import STALE_SEC, status
    lines = [header("⚙️", "Службы"), RULE, "<b>Очереди опросов</b>"]
    for q, age in status().items():
        ok = age is not None and age <= STALE_SEC
        lines.append(f"  {'🟢' if ok else '🔴'} {q} — {QUEUE_LABELS.get(q, '')}"
                     + ("" if ok else f" · {'нет пульса' if age is None else f'молчит {age // 60} мин'}"))
    cs = _containers()
    lines += ["", "<b>Контейнеры</b>"]
    if not cs:
        lines.append("  ⚠️ Нет ответа от cerber-ops")
    for c in cs:
        lines.append(f"  {'🟢' if c['is_up'] else '🔴'} {esc(c['short_name'])} · {esc(cut(c['status'], 24))}")
    kb = KB()
    if ctx.acc.can_edit("services"):
        kb.grid([KB.b(f"{'🟢' if c['is_up'] else '🔴'} {c['short_name']}", f"sy.c:{c['name']}") for c in cs], 3)
    kb.row(KB.url("🌐 Службы на сайте", site_url("/services/")))
    kb.nav(refresh="sy.l")
    return Screen("\n".join(lines), kb, toast=toast)


def _container(name: str) -> dict:
    c = next((c for c in _containers() if c["name"] == name), None)
    if c is None:
        raise Notice("Контейнер не найден.")
    return c


WARN = {
    "cloudflared": "Адрес туннеля сменится — ссылки бота и сайта извне перестанут работать до обновления.",
    "telegram_bot": "Бот перезапустится и пару секунд не будет отвечать.",
    "web": "Сайт будет недоступен несколько секунд.",
    "postgres": "Все службы потеряют базу на время перезапуска.",
    "beat": "На время перезапуска опросы не будут ставиться в очередь.",
}


@route("sy.c", section="services", edit=True)
def container_card(ctx: Ctx, name: str) -> Screen:
    c = _container(name)
    lines = [f"{'🟢' if c['is_up'] else '🔴'} <b>{esc(c['short_name'])}</b>", RULE,
             f"Статус: {esc(c['status'])}"]
    kb = KB()
    if c["is_up"]:
        kb.row(KB.b("↻ Перезапустить", f"sy.a:{name}:restart"), KB.b("⏹ Остановить", f"sy.a:{name}:stop"))
    else:
        kb.row(KB.b("▶ Запустить", f"sy.a:{name}:start"))
    kb.nav(back="sy.l")
    return Screen("\n".join(lines), kb)


@route("sy.a", section="services", edit=True)
def container_ask(ctx: Ctx, name: str, action: str) -> Screen:
    c = _container(name)
    verb = {"restart": "Перезапустить", "stop": "Остановить", "start": "Запустить"}.get(action)
    if not verb:
        raise Notice("Неверное действие.")
    warn = WARN.get(c["short_name"], "")
    text = f"❓ <b>{verb} {esc(c['short_name'])}?</b>" + (f"\n\n⚠️ {esc(warn)}" if warn and action != "start" else "")
    kb = KB().row(KB.b(f"Да, {verb.lower()}", f"sy.A:{name}:{action}"), KB.b("Отмена", f"sy.c:{name}"))
    return Screen(text, kb)


@route("sy.A", section="services", edit=True)
def container_do(ctx: Ctx, name: str, action: str) -> Screen:
    from apps.core import ops_client
    from apps.services.models import ServiceEvent
    if action not in ("start", "stop", "restart"):
        raise Notice("Неверное действие.")
    _container(name)
    try:
        r = ops_client.container_action(name, action)
        ok, err = bool(r.get("ok")), (r.get("stderr") or r.get("error") or "")
    except Exception as e:  # noqa: BLE001
        ok, err = False, str(e)
    ServiceEvent.objects.create(target_type=ServiceEvent.TYPE_CONTAINER, target_name=name,
                                action=action, success=ok, error=("" if ok else err)[:500])
    if not ok:
        raise Notice(f"Не удалось: {cut(err, 150)}")
    return services(ctx, toast="✅ Готово")
