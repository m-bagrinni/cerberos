"""Настройки уведомлений, отвязка Telegram, поиск."""
from __future__ import annotations

from django.db.models import Q

from ..core import Ctx, ask, on_input, route
from ..ui import DOT, KB, RULE, Notice, Screen, cut, esc, header, site_url

TOGGLES = {
    "res": ("notify_resolved", "Сообщать о восстановлении"),
    "bc": ("notify_broadcast", "Broadcast storm в подсетях"),
    "qt": ("quiet_enabled", "Тихие часы"),
    "qc": ("quiet_allow_crit", "Critical и в тихие часы"),
}


def _onoff(v: bool) -> str:
    return "✅" if v else "⬜"


@route("me.s")
def settings_screen(ctx: Ctx, toast: str = "") -> Screen:
    from apps.accounts.models import TelegramAccount, UserPreferences
    ta = TelegramAccount.objects.filter(user=ctx.user).first() or TelegramAccount(alerts_enabled=False)
    p = UserPreferences.for_user(ctx.user)
    stations = list(p.notify_stations.values_list("name", flat=True)[:5])
    lines = [header("🔔", "Уведомления"), RULE,
             f"{_onoff(ta.alerts_enabled)} Присылать алерты",
             f"Уровень: <b>{'только Critical' if p.notify_min_severity == 'crit' else 'все (Warning и Critical)'}</b>"]
    for key, (field, label) in TOGGLES.items():
        line = f"{_onoff(getattr(p, field))} {label}"
        if key == "qt" and p.quiet_enabled:
            line += f" ({p.quiet_from:%H:%M}–{p.quiet_to:%H:%M})"
        lines.append(line)
    lines.append("Станции: " + (esc(", ".join(stations)) if stations else "все"))
    lines += ["", "<i>Время тихих часов и станции — в профиле на сайте.</i>",
              "<i>Алерты приходят только по разделам, к которым у вас есть доступ.</i>"]
    kb = KB()
    kb.row(KB.b(f"{_onoff(ta.alerts_enabled)} Алерты", "me.t:on"),
           KB.b("Уровень: " + ("Critical" if p.notify_min_severity == "crit" else "все"), "me.v"))
    kb.grid([KB.b(f"{_onoff(getattr(p, f))} {label}", f"me.t:{k}") for k, (f, label) in TOGGLES.items()], 2)
    kb.row(KB.url("👤 Профиль на сайте", site_url("/accounts/profile/?tab=notify")))
    kb.row(KB.b("🔓 Отвязать Telegram", "me.u"))
    kb.nav()
    return Screen("\n".join(lines), kb, toast=toast)


@route("me.t")
def settings_toggle(ctx: Ctx, key: str) -> Screen:
    from apps.accounts.models import TelegramAccount, UserPreferences
    if key == "on":
        ta = TelegramAccount.objects.filter(user=ctx.user).first()
        if ta is None:
            raise Notice("Telegram не привязан.")
        ta.alerts_enabled = not ta.alerts_enabled
        ta.save(update_fields=["alerts_enabled"])
        return settings_screen(ctx, toast="🔔 Алерты включены" if ta.alerts_enabled else "🔕 Алерты выключены")
    if key not in TOGGLES:
        raise Notice("Неизвестная настройка.")
    field = TOGGLES[key][0]
    p = UserPreferences.for_user(ctx.user)
    setattr(p, field, not getattr(p, field))
    p.save(update_fields=[field])
    return settings_screen(ctx, toast="Сохранено")


@route("me.v")
def settings_severity(ctx: Ctx) -> Screen:
    from apps.accounts.models import UserPreferences
    p = UserPreferences.for_user(ctx.user)
    p.notify_min_severity = "warn" if p.notify_min_severity == "crit" else "crit"
    p.save(update_fields=["notify_min_severity"])
    return settings_screen(ctx, toast="Сохранено")


@route("me.u")
def unlink_ask(ctx: Ctx) -> Screen:
    kb = KB().row(KB.b("Да, отвязать", "me.U"), KB.b("Отмена", "me.s"))
    return Screen("❓ <b>Отвязать Telegram от Cerberus?</b>\n\n"
                  "Уведомления и документы перестанут приходить, бот перестанет работать до новой привязки.", kb)


@route("me.U")
def unlink(ctx: Ctx) -> Screen:
    from apps.accounts.models import TelegramAccount
    TelegramAccount.objects.filter(tg_id=ctx.tg_id).delete()
    type(ctx.user).objects.filter(pk=ctx.user.pk, telegram_id=ctx.tg_id).update(telegram_id=None)
    from .home import not_linked
    scr = not_linked()
    scr.text = "🔓 <b>Telegram отвязан.</b>\n\n" + scr.text
    return scr


# ── Поиск ─────────────────────────────────────────────────────────────────────
@route("find")
def find_ask(ctx: Ctx) -> Screen:
    return ask(ctx, "search", [], header("🔎", "Поиск") +
               "\n\nIP, имя устройства, станции, NE, сервера или тема документа.\n"
               "<i>Подсказка: искать можно и без кнопки — просто напишите текст боту.</i>",
               cancel="home")


@on_input("search")
def search(ctx: Ctx, text: str) -> Screen:
    q = (text or "").strip()[:64]
    if len(q) < 2:
        raise Notice("Введите хотя бы 2 символа.")
    acc = ctx.acc
    groups = []   # (заголовок, [(текст кнопки, callback)])
    if acc.can_view("devices"):
        from apps.devices.models import Device
        rows = Device.objects.filter(Q(name__icontains=q) | Q(ip__startswith=q))[:8]
        groups.append(("📟 Устройства", [(f"{DOT.get(d.status, '⚪')} {cut(d.name, 22)} {d.ip}", f"dv.c:{d.pk}") for d in rows]))
    if acc.can_view("stations"):
        from apps.stations.models import Station
        rows = Station.objects.filter(name__icontains=q)[:6]
        groups.append(("🗺 Станции", [(f"🗺 {cut(s.name, 30)}", f"st.c:{s.pk}") for s in rows]))
    if acc.can_view("mux"):
        from apps.devices.models import NeDevice
        qq = Q(name__icontains=q) | Q(raw_name__icontains=q) | Q(ne_ip__startswith=q)
        if q.isdigit():
            qq |= Q(ne_id=int(q))
        rows = list(NeDevice.objects.filter(qq).select_related("gateway")[:6])
        groups.append(("🔌 NE", [(f"🔌 NE-{n.ne_id} {cut(n.display_name, 22)}", f"ne.c:{n.pk}") for n in rows]))
    if acc.can_view("subnets"):
        from apps.subnets.models import IpAddress
        rows = IpAddress.objects.filter(Q(ip__startswith=q) | Q(hostname__icontains=q)).order_by("ip")[:8]
        groups.append(("🌐 IP", [(f"{'🟢' if i.is_alive else '⚪'} {i.ip} {cut(i.hostname, 16)}", f"ip.c:{i.pk}") for i in rows]))
    if acc.can_view("servers"):
        from apps.servers.models import Server
        rows = Server.objects.filter(Q(name__icontains=q) | Q(ip_address__startswith=q))[:5]
        groups.append(("🖥 Серверы", [(f"🖥 {cut(s.name, 24)}", f"sv.c:{s.pk}") for s in rows]))
    if acc.can_view("edocs"):
        from apps.edocs.models import Document
        u = ctx.user
        rows = (Document.objects.filter(Q(title__icontains=q) | Q(reg_number__icontains=q))
                .filter(Q(author=u) | Q(assignments__user=u)).distinct()
                .exclude(status="draft").order_by("-updated_at")[:5])
        groups.append(("📄 Документы", [(f"📄 {d.number} {cut(d.title, 24)}", f"dc.c:{d.pk}:inbox") for d in rows]))

    groups = [g for g in groups if g[1]]
    total = sum(len(g[1]) for g in groups)
    if total == 1:
        from ..core import run
        key, _, rest = groups[0][1][0][1].partition(":")
        return run(key, ctx, rest.split(":") if rest else [])
    lines = [header("🔎", f"«{q}»", f"найдено {total}" if total else "ничего не найдено"), RULE]
    kb = KB()
    for title, rows in groups:
        lines.append(f"{title}: {len(rows)}")
        kb.grid([KB.b(t, cb) for t, cb in rows], 1 if title in ("📄 Документы",) else 2)
    if not total:
        lines.append("Попробуйте часть IP (например <code>10.70.18</code>) или имени.")
    kb.row(KB.b("🔎 Искать ещё", "find"))
    kb.nav()
    return Screen("\n".join(lines), kb)
