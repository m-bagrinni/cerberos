"""Документы (СЭД): что ждёт меня, списки, карточка, файл, решение, комментарий."""
from __future__ import annotations

import os

from ..core import Ctx, ask, clear_wait, on_input, route
from ..ui import KB, RULE, Notice, Screen, ago, ago_back, cut, esc, header, paginate, site_url, when

TYPE_ICON = {"execution": "🛠", "approval": "✍️", "review": "👁", "info": "ℹ️"}
STATUS_ICON = {"draft": "📝", "review": "⏳", "revision": "↩️", "approved": "✅", "rejected": "❌"}


def _lists(user):
    from apps.edocs.models import Document
    from apps.edocs.queries import INBOX_FILTERS, inbox_qs, pending_qs
    out = {k: (label, pending_qs(user, k)) for k, (label, _q) in INBOX_FILTERS.items()}
    out["overdue"] = ("Просрочено", pending_qs(user, "overdue"))
    out["inbox"] = ("Все входящие", inbox_qs(user))
    out["sent"] = ("Отправленные", Document.objects.filter(author=user).exclude(status="draft"))
    return out


@route("dc.h", section="edocs")
def docs_home(ctx: Ctx) -> Screen:
    from apps.edocs.queries import pending_counts
    c = pending_counts(ctx.user)
    lines = [header("📄", "Документы", f"ждут меня: {c['total']}" if c["total"] else "новых нет"), RULE]
    from apps.edocs.queries import INBOX_FILTERS
    for key, (label, _q) in INBOX_FILTERS.items():
        lines.append(f"{'•' if c[key] else '·'} {label}: <b>{c[key]}</b>")
    kb = KB()
    if c["overdue"]:
        lines.append(f"🔴 Просрочено: <b>{c['overdue']}</b>")
    kb.grid([KB.b(f"{label} · {c[key]}", f"dc.l:{key}:0") for key, (label, _q) in INBOX_FILTERS.items()], 2)
    if c["overdue"]:
        kb.row(KB.b(f"🔴 Просрочено · {c['overdue']}", "dc.l:overdue:0"))
    kb.row(KB.b("📥 Все входящие", "dc.l:inbox:0"), KB.b("📤 Отправленные", "dc.l:sent:0"))
    kb.row(KB.url("✏️ Написать (на сайте)", site_url("/edocs/create/")))
    kb.nav(refresh="dc.h")
    return Screen("\n".join(lines), kb)


@route("dc.l", section="edocs")
def docs_list(ctx: Ctx, key: str, page: str = "0") -> Screen:
    lists = _lists(ctx.user)
    if key not in lists:
        raise Notice("Список не найден.")
    label, qs = lists[key]
    from apps.edocs.models import Assignment
    docs = list(qs.select_related("author", "recipient").order_by("-updated_at")[:120])
    items, page, pages = paginate(docs, page)
    unread = set(Assignment.objects.filter(document__in=items, user=ctx.user, status="pending", is_read=False)
                 .values_list("document_id", flat=True))
    lines = [header("📄", label, f"{len(docs)}"), RULE]
    for d in items:
        who = d.recipient if key == "sent" else d.author
        new = "🆕 " if d.pk in unread else ""
        due = f" · ⏰ {d.due_date:%d.%m}" if d.due_date and d.is_open else ""
        late = " 🔴" if d.is_overdue else ""
        lines.append(f"{STATUS_ICON.get(d.status, '•')} {new}<b>{esc(cut(d.title, 40))}</b>{late}\n"
                     f"      {TYPE_ICON.get(d.document_type, '')} {esc(d.number)} · {'→' if key == 'sent' else 'от'} "
                     f"{_name(who)}{due} · {ago(d.updated_at)}")
    if not items:
        lines.append("Пусто. 👌")
    kb = KB()
    for d in items:
        kb.row(KB.b(f"{STATUS_ICON.get(d.status, '•')} {cut(d.title, 34)}", f"dc.c:{d.pk}:{key}"))
    kb.pager("dc.l", page, pages, key)
    kb.nav(back="dc.h", refresh=f"dc.l:{key}:{page}")
    return Screen("\n".join(lines), kb)


def _doc(ctx: Ctx, pk):
    from apps.edocs.actions import can_access
    from apps.edocs.models import Document
    d = Document.objects.select_related("author", "recipient").filter(pk=int(pk)).first()
    if d is None or not can_access(ctx.user, d):
        raise Notice("Документ не найден или нет доступа.")
    return d


def _name(u) -> str:
    return esc(u.get_full_name() or u.username) if u else "—"


@route("dc.c", section="edocs")
def doc_card(ctx: Ctx, pk: str, back: str = "inbox", toast: str = "") -> Screen:
    from apps.edocs.actions import mark_read
    from apps.edocs.services import allowed
    d = _doc(ctx, pk)
    mark_read(ctx.user, d)
    can = allowed(ctx.user, d) if ctx.acc.can_edit("edocs") else None
    people = list(d.assignments.exclude(status="forwarded").select_related("user").order_by("id"))
    comments = list(d.comments.select_related("author").order_by("-created_at")[:3])[::-1]
    prio = " · ⚡ " + esc(d.get_priority_display()) if d.priority != "normal" else ""
    lines = [f"{TYPE_ICON.get(d.document_type, '📄')} <b>{esc(d.title)}</b>",
             f"<i>{esc(d.number)} · {esc(d.get_document_type_display())}{prio} · "
             f"{STATUS_ICON.get(d.status, '')} {esc(d.status_label)}</i>",
             RULE,
             f"👤 Автор: {_name(d.author)}",
             f"🕒 Зарегистрирован {when(d.sent_at or d.created_at)}"]
    if d.due_date:
        late = f" — 🔴 просрочен на {d.overdue_days} дн." if d.is_overdue else ""
        lines.append(f"⏰ Срок: {d.due_date:%d.%m.%Y}{late}")
    if people:
        lines += ["", "<b>Адресаты</b>"]
        for a in people:
            mark = {"done": "✅", "rejected": "❌"}.get(a.status, "⏳" if a.is_read else "✉️")
            lines.append(f"{mark} {_name(a.user)} — {esc(a.status_label)}"
                         + (f"\n      💬 {esc(cut(a.report, 150))}" if a.report else ""))
    if d.message:
        lines += ["", esc(cut(d.message, 700))]
    files = list(d.attachments.order_by("kind", "id")[:9])
    if comments:
        lines += ["", f"💬 <b>Комментарии</b> ({d.comments.count()})"]
        for c in comments:
            lines.append(f"<b>{_name(c.author)}</b>, {when(c.created_at)}:\n{esc(cut(c.text, 200))}")

    kb = KB()
    if can and can.decide:
        ok_label = "✅ Согласовать" if d.document_type == "approval" else "✅ Исполнено"
        kb.row(KB.b(ok_label, f"dc.s:{d.pk}:approved"), KB.b("❌ Отклонить", f"dc.s:{d.pk}:rejected"))
    if can and can.acknowledge:
        kb.row(KB.b("👁 Ознакомлен", f"dc.a:{d.pk}"))
    kb.grid([KB.b(f"📎 {cut(f.name, 22)}", f"dc.f:{d.pk}:{f.pk}") for f in files], 2)
    if can and can.comment:
        kb.row(KB.b("💬 Комментарий", f"dc.m:{d.pk}"))
    kb.row(KB.url("🌐 На сайте", site_url(f"/edocs/document/{d.pk}/")))
    kb.nav(back=f"dc.l:{back}:0", refresh=f"dc.c:{d.pk}:{back}")
    return Screen("\n".join(lines), kb, toast=toast)


@route("dc.f", section="edocs")
def doc_file(ctx: Ctx, pk: str, att_pk: str) -> Screen:
    d = _doc(ctx, pk)
    f = d.attachments.filter(pk=int(att_pk)).first() if att_pk.isdigit() else None
    if f is None:
        raise Notice("Файл не найден.")
    if not os.path.exists(f.file.path):
        raise Notice("Файл отсутствует на сервере.")
    if os.path.getsize(f.file.path) > 49 * 1024 * 1024:
        raise Notice("Файл больше 50 МБ — скачайте на сайте.")
    return Screen("", file=(f.file.path, f.name, f"📄 {esc(d.number)} · {esc(d.title)}"), toast="📎 Отправляю файл…")


@route("dc.a", section="edocs", edit=True)
def doc_ack(ctx: Ctx, pk: str) -> Screen:
    from apps.edocs.actions import acknowledge
    d = _doc(ctx, pk)
    err = acknowledge(ctx.user, d)
    if err:
        raise Notice(err)
    return doc_card(ctx, pk, toast="👁 Отмечено: ознакомлен")


@route("dc.s", section="edocs", edit=True)
def doc_decide_ask(ctx: Ctx, pk: str, status: str) -> Screen:
    from apps.edocs.actions import can_decide
    d = _doc(ctx, pk)
    if status not in ("approved", "rejected") or not can_decide(ctx.user, d):
        raise Notice("Решение уже принято или принимать его не вам.")
    verb = "Отклонить" if status == "rejected" else ("Согласовать" if d.document_type == "approval" else "Отметить выполненным")
    extra = KB()
    if status == "approved":
        extra.row(KB.b("✅ Без комментария", f"dc.S:{d.pk}:{status}"))
    # отклонение без причины не принимается (services.decide) — кнопки «без комментария» нет
    hint = "Укажите причину — автор её увидит." if status == "rejected" else "Можно добавить отчёт для автора."
    return ask(ctx, "dc.decide", [d.pk, status], f"❓ <b>{verb}</b>\n📄 {esc(d.title)}\n\n{hint}",
               cancel=f"dc.c:{d.pk}", extra=extra)


def _decide(ctx: Ctx, pk, status, comment) -> Screen:
    from apps.edocs.actions import set_status
    clear_wait(ctx.tg_id)
    d = _doc(ctx, pk)
    err = set_status(ctx.user, d, status, comment)
    if err:
        raise Notice(err)
    return doc_card(ctx, pk, toast="✅ Готово — автор получит уведомление")


@route("dc.S", section="edocs", edit=True)
def doc_decide_now(ctx: Ctx, pk: str, status: str) -> Screen:
    return _decide(ctx, pk, status, "")


@on_input("dc.decide", section="edocs", edit=True)
def doc_decide_text(ctx: Ctx, text: str, pk: str, status: str) -> Screen:
    scr = _decide(ctx, pk, status, text)
    scr.text = ("❌ <b>Отклонено</b>\n\n" if status == "rejected" else "✅ <b>Готово</b>\n\n") + scr.text
    return scr


@route("dc.m", section="edocs", edit=True)
def doc_comment_ask(ctx: Ctx, pk: str) -> Screen:
    d = _doc(ctx, pk)
    return ask(ctx, "dc.comment", [d.pk], f"💬 <b>Комментарий</b>\n📄 {esc(d.title)}", cancel=f"dc.c:{d.pk}")


@on_input("dc.comment", section="edocs", edit=True)
def doc_comment(ctx: Ctx, text: str, pk: str) -> Screen:
    from apps.edocs.actions import add_comment
    d = _doc(ctx, pk)
    err = add_comment(ctx.user, d, text)
    if err:
        raise Notice(err)
    scr = doc_card(ctx, pk)
    scr.text = "💬 <b>Комментарий добавлен</b>\n\n" + scr.text
    return scr
