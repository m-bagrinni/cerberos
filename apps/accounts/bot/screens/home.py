"""Главный экран, помощь, привязка."""
from __future__ import annotations

from django.utils import timezone

from ..core import Ctx, route
from ..ui import KB, RULE, Screen, esc, header, plural, site_url
from .common import firing_alerts, server_online, station_states

NOT_LINKED = (
    "🛡 <b>Cerberus</b>\n"
    f"{RULE}\n"
    "Бот работает только для сотрудников с привязанным аккаунтом.\n\n"
    "<b>Как привязать</b>\n"
    "1. Откройте сайт Cerberus → <b>Профиль</b> → <b>Telegram</b>.\n"
    "2. Нажмите <b>«Привязать Telegram»</b>, затем <b>«Открыть бота»</b>.\n"
    "   Или отправьте сюда: <code>/link КОД</code>\n\n"
    "<i>Код одноразовый и действует 10 минут.</i>"
)


def not_linked() -> Screen:
    kb = KB()
    kb.row(KB.url("🌐 Открыть профиль на сайте", site_url("/accounts/profile/?tab=profile")))
    return Screen(NOT_LINKED, kb if kb.rows else None)


def _greeting() -> str:
    h = timezone.localtime().hour
    return "Доброй ночи" if h < 5 else "Доброе утро" if h < 12 else "Добрый день" if h < 18 else "Добрый вечер"


@route("home")
def home(ctx: Ctx) -> Screen:
    acc, user = ctx.acc, ctx.user
    name = user.first_name or user.get_full_name() or user.username
    lines = [f"🛡 <b>Cerberus</b>",
             f"<i>{_greeting()}, {esc(name)} · {timezone.localtime():%H:%M}</i>", RULE]

    # Алерты
    if acc.can_view("alerts"):
        alerts = list(firing_alerts(acc).select_related(None).only("severity", "acked_by_id"))
        crit = sum(a.severity in ("crit", "power") for a in alerts)
        warn = len(alerts) - crit
        mine = sum(a.acked_by_id == user.pk for a in alerts)
        free = sum(not a.acked_by_id for a in alerts)
        if alerts:
            s = f"🚨 Алерты: " + " · ".join(x for x in (
                f"🔴 {crit}" if crit else "", f"🟡 {warn}" if warn else "") if x)
            s += f"\n      ✋ у меня {mine} · 🙋 без ответственного {free}"
        else:
            s = "✅ Алертов нет — всё в норме"
        lines.append(s)

    # Станции
    if acc.can_view("stations"):
        st = station_states()
        bad = sum(v["state"] == "crit" for v in st.values())
        warn = sum(v["state"] == "warn" for v in st.values())
        ok = len(st) - bad - warn
        lines.append(f"🗺 Станции: 🟢 {ok}" + (f" · 🟡 {warn}" if warn else "") + (f" · 🔴 {bad}" if bad else ""))

    # Серверы
    if acc.can_view("servers"):
        from apps.servers.models import Server
        servers = list(Server.objects.filter(is_active=True).only("last_seen"))
        if servers:
            on = sum(server_online(s) for s in servers)
            icon = "🟢" if on == len(servers) else "🔴"
            lines.append(f"🖥 Серверы: {icon} {on} из {len(servers)} на связи")

    # Документы
    if acc.can_view("edocs"):
        from apps.edocs.queries import pending_counts
        n = pending_counts(user)["total"]
        lines.append(f"📄 Документы: {'ждут меня ' + str(n) if n else 'новых нет'}")

    # Службы — сторож Celery
    if acc.can_view("services"):
        from apps.core.watchdog import STALE_SEC, status
        st = status()
        down = [q for q, age in st.items() if age is None or age > STALE_SEC]
        lines.append("⚙️ Службы: " + ("🟢 опросы работают" if not down else f"🔴 молчат очереди: {', '.join(down)}"))

    kb = KB()
    btns = []
    if acc.can_view("alerts"):
        btns.append(KB.b("🚨 Алерты", "al.l:all:0"))
        btns.append(KB.b("✋ Мои алерты", "al.l:mine:0"))
    if acc.can_view("stations"):
        btns.append(KB.b("🗺 Станции", "st.l:all:0"))
    if acc.can_view("devices"):
        btns.append(KB.b("📟 Устройства", "dv.l:all:0"))
    if acc.can_view("mux"):
        btns.append(KB.b("🔌 Оптика (NE)", "ne.g"))
    if acc.can_view("subnets"):
        btns.append(KB.b("🌐 Подсети", "sn.l"))
    if acc.can_view("servers"):
        btns.append(KB.b("🖥 Серверы", "sv.l"))
    if acc.can_view("edocs"):
        btns.append(KB.b("📄 Документы", "dc.h"))
    if acc.can_view("services"):
        btns.append(KB.b("⚙️ Службы", "sy.l"))
    btns.append(KB.b("🔎 Поиск", "find"))
    kb.grid(btns, 2)
    kb.row(KB.b("🔔 Уведомления", "me.s"), KB.b("❓ Помощь", "help"))
    kb.row(KB.url("🌐 Открыть Cerberus", site_url("/")))
    if len(lines) == 3:
        lines.append("Нет доступа к разделам — обратитесь к администратору.")
    return Screen("\n".join(lines), kb)


@route("help")
def help_screen(ctx: Ctx) -> Screen:
    text = "\n".join([
        header("❓", "Как пользоваться"),
        RULE,
        "• Всё управление — кнопками; экран обновляется на месте.",
        "• <b>Поиск</b>: просто напишите IP, имя устройства, станции или NE.",
        "• Под уведомлением об алерте — <b>✋ Взять в работу</b>, затем",
        "  <b>✅ Решено</b> или <b>↩ Отказаться</b>.",
        "• Critical без ответственного через 15 минут придёт напоминание.",
        "",
        "<b>Команды</b>",
        "/menu — главный экран",
        "/alerts — активные алерты · /mine — мои",
        "/stations — станции · /docs — документы",
        "/find <i>текст</i> — поиск · /settings — уведомления",
        "",
        f"<i>Права — те же, что на сайте ({esc(ctx.user.username)}).</i>",
    ])
    return Screen(text, KB().nav())
