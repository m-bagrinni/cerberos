"""Алерты: список с фильтрами, карточка, «взять / решено / отказаться», история."""
from __future__ import annotations

from ..core import Ctx, ask, on_input, route
from ..ui import (KB, RULE, SEV_ICON, SEV_LABEL, Notice, Screen, ago, cut, esc, header, num,
                  paginate, plural, site_url, when)
from .common import alert_short, alert_target, alert_unit, firing_alerts, metric_label, show_value, visible_alerts_q

FILTERS = {
    "all": "Все",
    "crit": "🔴 Критичные",
    "mine": "✋ Мои",
    "free": "🙋 Ничьи",
}


def _sev_rank(a) -> int:
    return 0 if a.severity in ("crit", "power") else 1


@route("al.l", section="alerts")
def alert_list(ctx: Ctx, flt: str = "all", page: str = "0") -> Screen:
    flt = flt if flt in FILTERS else "all"
    everything = list(firing_alerts(ctx.acc))
    # Следствия каскада (suppressed_by) — внутри карточки причины, не в общем списке
    alerts = [a for a in everything if not a.suppressed_by_id]
    folded = len(everything) - len(alerts)
    counts = {
        "all": len(alerts),
        "crit": sum(a.severity in ("crit", "power") for a in alerts),
        "mine": sum(a.acked_by_id == ctx.user.pk for a in alerts),
        "free": sum(not a.acked_by_id for a in alerts),
    }
    if flt == "crit":
        alerts = [a for a in alerts if a.severity in ("crit", "power")]
    elif flt == "mine":
        alerts = [a for a in alerts if a.acked_by_id == ctx.user.pk]
    elif flt == "free":
        alerts = [a for a in alerts if not a.acked_by_id]
    alerts.sort(key=lambda a: (_sev_rank(a), -a.started_at.timestamp()))
    items, page, pages = paginate(alerts, page)

    if counts["all"]:
        sub = " · ".join(x for x in (
            plural(counts["crit"], "критичный", "критичных", "критичных") if counts["crit"] else "",
            plural(counts["all"] - counts["crit"], "предупреждение", "предупреждения", "предупреждений")
            if counts["all"] - counts["crit"] else "",
        ) if x)
    else:
        sub = "активных нет"
    if folded:
        sub += f" · ⛓ +{folded} свёрнуто"
    lines = [header("🚨", "Алерты", sub), RULE]
    if not items:
        lines.append({"mine": "У вас нет алертов в работе.", "free": "Все алерты кем-то взяты. 👍",
                      "crit": "Критичных нет."}.get(flt, "✅ Всё в норме."))
    for a in items:
        owner = "✋" if a.acked_by_id == ctx.user.pk else "👤" if a.acked_by_id else "🙋"
        lines.append(f"{SEV_ICON.get(a.severity, '•')} <b>{esc(cut(alert_target(a), 34))}</b>\n"
                     f"      {esc(metric_label(a))} · {ago(a.started_at)} {owner}")

    kb = KB()
    kb.grid([KB.b(("• " if k == flt else "") + f"{label} {counts[k]}", f"al.l:{k}:0")
             for k, label in FILTERS.items()], 2)
    for a in items:
        kb.row(KB.b(f"{SEV_ICON.get(a.severity, '•')} {cut(alert_short(a), 24)} · {cut(metric_label(a), 20)}",
                    f"al.c:{a.pk}"))
    kb.pager("al.l", page, pages, flt)
    kb.row(KB.b("🕘 История", "al.h:0"))
    kb.nav(refresh=f"al.l:{flt}:{page}")
    return Screen("\n".join(lines), kb)


def _get(ctx: Ctx, alert_id) -> object:
    from apps.alerts.models import Alert
    a = (Alert.objects.filter(pk=int(alert_id)).filter(visible_alerts_q(ctx.acc))
         .select_related("device", "ne_device", "ne_device__gateway", "ip_address", "ip_address__station",
                         "metric", "acked_by", "resolved_by", "suppressed_by", "suppressed_by__device").first())
    if a is None:
        raise Notice("Алерт не найден или нет доступа.")
    return a


def _who(u) -> str:
    return esc((u.get_full_name() or u.username) if u else "—")


@route("al.c", section="alerts")
def alert_card(ctx: Ctx, alert_id: str, toast: str = "") -> Screen:
    a = _get(ctx, alert_id)
    firing = a.state == "firing"
    unit = alert_unit(a)
    icon = SEV_ICON.get(a.severity, "•") if firing else "✅"
    title = SEV_LABEL.get(a.severity, a.severity) if firing else "Закрыт"
    lines = [f"{icon} <b>{title}</b> · {ago(a.started_at) if firing else when(a.resolved_at)}", RULE,
             f"🎯 <b>{esc(alert_target(a))}</b>",
             f"📊 {esc(metric_label(a))}  <code>{esc(a.metric_key)}</code>"]
    if show_value(a):
        s = f"📈 Значение: <b>{num(a.value, unit, 2)}</b>"
        if a.threshold is not None:
            s += f" · порог {num(a.threshold, unit, 2)}"
        lines.append(s)
    lines.append(f"🕒 Начало: {when(a.started_at)}")
    if a.message:
        lines.append(f"💬 {esc(cut(a.message, 300))}")
    cascade = []
    if firing and a.suppressed_by_id:
        cause = a.suppressed_by
        lines += ["", f"⛓ <b>Следствие</b>: недоступно вышестоящее — {esc(alert_target(cause))}. "
                      f"Уведомления по этому алерту не отправляются."]
    elif firing:
        cascade = list(a.suppressed.filter(state="firing").filter(visible_alerts_q(ctx.acc))
                       .select_related("device", "ne_device", "ip_address")[:30])
        if cascade:
            lines += ["", f"⛓ <b>Свёрнуто следствий: {len(cascade)}</b>"]
            for c in cascade[:8]:
                lines.append(f"   {SEV_ICON.get(c.severity, '•')} {esc(cut(alert_target(c), 30))} · {esc(metric_label(c))}")
            if len(cascade) > 8:
                lines.append(f"   …и ещё {len(cascade) - 8}")
    lines.append("")
    if firing:
        if a.acked_by_id:
            me = a.acked_by_id == ctx.user.pk
            lines.append(f"✋ В работе: <b>{'вы' if me else _who(a.acked_by)}</b> · с {when(a.acked_at, '%H:%M')}")
        else:
            lines.append("🙋 <b>Никто не взял в работу</b>")
            if a.escalated_at:
                lines.append(f"⏰ Напоминание отправлено в {when(a.escalated_at, '%H:%M')}")
    else:
        if a.resolved_by_id:
            lines.append(f"✅ Закрыл: <b>{_who(a.resolved_by)}</b>")
            if a.resolution:
                lines.append(f"📝 {esc(a.resolution)}")
        else:
            lines.append("✅ Восстановилось само" + (f" · занимался {_who(a.acked_by)}" if a.acked_by_id else ""))

    kb = KB()
    if firing and ctx.acc.can_edit("alerts"):
        boss = ctx.user.is_superuser or ctx.acc.has_cap("users_manage")
        if not a.acked_by_id:
            kb.row(KB.b("✋ Взять в работу", f"al.a:{a.pk}"))
        if not a.acked_by_id or a.acked_by_id == ctx.user.pk or boss:
            row = [KB.b("✅ Решено", f"al.r:{a.pk}")]
            if a.acked_by_id:
                row.append(KB.b("↩ Отказаться", f"al.x:{a.pk}"))
            kb.row(*row)
    go = []
    if a.device_id and ctx.acc.can_view("devices"):
        go.append(KB.b("📟 Устройство", f"dv.c:{a.device_id}"))
    if a.ne_device_id and ctx.acc.can_view("mux"):
        go.append(KB.b("🔌 NE", f"ne.c:{a.ne_device_id}"))
    if a.ip_address_id and ctx.acc.can_view("subnets"):
        go.append(KB.b("🌐 IP", f"ip.c:{a.ip_address_id}"))
    kb.row(*go)
    if firing and a.suppressed_by_id:
        kb.row(KB.b("⛓ К причине", f"al.c:{a.suppressed_by_id}"))
    kb.row(KB.url("🌐 На сайте", site_url("/alerts/")))
    kb.nav(back="al.l:all:0" if firing else "al.h:0", refresh=f"al.c:{a.pk}")
    return Screen("\n".join(lines), kb, toast=toast)


@route("al.C", section="alerts")
def alert_card_new(ctx: Ctx, alert_id: str) -> Screen:
    """«Подробнее» под уведомлением: карточка новым сообщением, уведомление остаётся."""
    scr = alert_card(ctx, alert_id)
    scr.new = True
    return scr


@route("al.a", section="alerts", edit=True)
def alert_ack(ctx: Ctx, alert_id: str) -> Screen:
    from apps.alerts.ack import acknowledge
    _get(ctx, alert_id)
    _, err = acknowledge(int(alert_id), ctx.user)
    if err:
        raise Notice(err)
    return alert_card(ctx, alert_id, toast="✋ Взято в работу — остальные получат уведомление")


@route("al.x", section="alerts", edit=True)
def alert_release(ctx: Ctx, alert_id: str) -> Screen:
    from apps.alerts.ack import release
    _get(ctx, alert_id)
    _, err = release(int(alert_id), ctx.user)
    if err:
        raise Notice(err)
    return alert_card(ctx, alert_id, toast="↩ Вы отказались — алерт снова без ответственного")


@route("al.r", section="alerts", edit=True)
def alert_resolve_ask(ctx: Ctx, alert_id: str) -> Screen:
    a = _get(ctx, alert_id)
    if a.state != "firing":
        raise Notice("Алерт уже закрыт.")
    extra = KB().row(KB.b("✅ Закрыть без комментария", f"al.R:{a.pk}"))
    return ask(ctx, "al.note", [a.pk],
               f"✅ <b>Закрыть алерт</b>\n🎯 {esc(alert_target(a))}\n\n"
               "Что сделано? Комментарий увидят коллеги и он сохранится в истории.\n"
               "<i>Если проблема осталась — следующий опрос откроет алерт снова.</i>",
               cancel=f"al.c:{a.pk}", extra=extra)


def _resolve(ctx: Ctx, alert_id, note: str) -> Screen:
    from apps.alerts.ack import resolve
    from ..core import clear_wait
    clear_wait(ctx.tg_id)
    _get(ctx, alert_id)
    _, err = resolve(int(alert_id), ctx.user, note)
    if err:
        raise Notice(err)
    return alert_card(ctx, alert_id, toast="✅ Алерт закрыт")


@route("al.R", section="alerts", edit=True)
def alert_resolve_now(ctx: Ctx, alert_id: str) -> Screen:
    return _resolve(ctx, alert_id, "")


@on_input("al.note", section="alerts", edit=True)
def alert_resolve_note(ctx: Ctx, text: str, alert_id: str) -> Screen:
    scr = _resolve(ctx, alert_id, text)
    scr.text = "✅ <b>Алерт закрыт</b>\n\n" + scr.text
    return scr


@route("al.h", section="alerts")
def alert_history(ctx: Ctx, page: str = "0") -> Screen:
    from apps.alerts.models import Alert
    qs = (Alert.objects.filter(state=Alert.STATE_OK).filter(visible_alerts_q(ctx.acc))
          .select_related("device", "ne_device", "ip_address", "metric", "resolved_by")
          .order_by("-resolved_at")[:80])
    items, page, pages = paginate(qs, page)
    lines = [header("🕘", "История алертов", "последние закрытые"), RULE]
    for a in items:
        who = f" · {_who(a.resolved_by)}" if a.resolved_by_id else ""
        lines.append(f"✅ <b>{esc(cut(alert_target(a), 34))}</b>\n"
                     f"      {esc(metric_label(a))} · {when(a.resolved_at)}{who}")
    if not items:
        lines.append("Пока пусто.")
    kb = KB()
    for a in items:
        kb.row(KB.b(f"✅ {cut(alert_short(a), 26)} · {when(a.resolved_at, '%d.%m %H:%M')}", f"al.c:{a.pk}"))
    kb.pager("al.h", page, pages)
    kb.nav(back="al.l:all:0")
    return Screen("\n".join(lines), kb)
