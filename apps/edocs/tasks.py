"""Фоновые задачи СЭД: уведомления в Telegram и напоминания о сроках."""
import logging
from datetime import timedelta
from html import escape

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

KIND_HEAD = {
    "new": "📄 <b>Новый документ</b>",
    "revision": "↩️ <b>Возвращён на доработку</b>",
    "done": "✅ <b>Исполнено</b>",
    "approved": "✅ <b>Согласовано</b>",
    "rejected": "❌ <b>Отклонено</b>",
    "completed": "🏁 <b>Документ завершён</b>",
    "forwarded": "➡️ <b>Документ передан</b>",
    "comment": "💬 <b>Комментарий</b>",
}


def _link(doc_id) -> str:
    base = getattr(settings, "CERBER_WEBAPP_URL", "").rstrip("/")
    return f"{base}/edocs/document/{doc_id}/" if base else ""


def doc_text(doc, head: str, actor: str = "", comment: str = "", extra: str = "") -> str:
    lines = [head, "", f"<b>{escape(doc.number)}</b> · {escape(doc.title)}",
             f"Вид: {escape(doc.get_document_type_display())}"
             + (" · ⚡ " + escape(doc.get_priority_display()) if doc.priority != "normal" else "")]
    if doc.due_date:
        lines.append(f"Срок: {doc.due_date:%d.%m.%Y}")
    if actor:
        lines.append(f"👤 {escape(actor)}")
    if extra:
        lines.append(extra)
    if comment:
        lines.append(f"💬 {escape(comment)}")
    link = _link(doc.pk)
    if link:
        lines += ["", f"<a href='{link}'>Открыть в Cerberus</a>"]
    return "\n".join(lines)


@shared_task(name="edocs.notify", ignore_result=True)
def notify_users(doc_id, pairs, actor_name="", comment="", target_name=""):
    """pairs: [(user_id, kind)] — kind из KIND_HEAD."""
    from apps.accounts.models import User
    from apps.accounts.telegram_notify import send_telegram_document, send_telegram_message

    from .models import Document
    doc = Document.objects.filter(pk=doc_id).first()
    if doc is None:
        return
    users = {u.pk: u for u in User.objects.filter(pk__in=[p[0] for p in pairs], is_active=True)}
    for uid, kind in pairs:
        u = users.get(uid)
        if u is None or not u.telegram_id:
            continue
        extra = f"Кому: {escape(target_name)}" if kind == "forwarded" and target_name else ""
        text = doc_text(doc, KIND_HEAD.get(kind, "📄 <b>Документ</b>"), actor_name or doc.author.get_full_name()
                        or doc.author.username, comment, extra)
        try:
            send_telegram_message(u.telegram_id, text)
            if kind == "new":
                # Файлы документа — следом, чтобы прочитать прямо в Telegram
                for att in doc.attachments.filter(kind="main")[:5]:
                    if att.size <= 49 * 1024 * 1024:
                        send_telegram_document(u.telegram_id, att.file.path, caption=escape(att.name),
                                               filename=att.name)
        except Exception:  # noqa: BLE001
            logger.exception("edocs notify user=%s doc=%s", uid, doc_id)


@shared_task(name="edocs.remind_due", ignore_result=True)
def remind_due():
    """Раз в день: адресатам — о документах со сроком завтра/сегодня и просроченных."""
    from apps.accounts.telegram_notify import send_telegram_message

    from .models import Assignment
    today = timezone.localdate()
    rows = (Assignment.objects.filter(status="pending", document__status__in=("review", "revision"),
                                      document__due_date__lte=today + timedelta(days=1),
                                      user__is_active=True)
            .exclude(user__telegram_id=None)
            .select_related("document", "user").order_by("user_id", "document__due_date"))
    by_user = {}
    for a in rows:
        by_user.setdefault(a.user, []).append(a.document)
    for u, docs in by_user.items():
        lines = ["⏰ <b>Сроки по документам</b>", ""]
        for d in docs[:15]:
            left = (d.due_date - today).days
            when = "просрочен на %d дн." % -left if left < 0 else ("сегодня" if left == 0 else "завтра")
            lines.append(f"• <b>{escape(d.number)}</b> {escape(d.title[:60])} — {when}")
        if len(docs) > 15:
            lines.append(f"…и ещё {len(docs) - 15}")
        base = getattr(settings, "CERBER_WEBAPP_URL", "").rstrip("/")
        if base:
            lines += ["", f"<a href='{base}/edocs/?f=due'>Открыть входящие</a>"]
        try:
            send_telegram_message(u.telegram_id, "\n".join(lines))
        except Exception:  # noqa: BLE001
            logger.exception("edocs remind user=%s", u.pk)
