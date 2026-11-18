"""
Бизнес-логика СЭД. Единственное место, где меняется состояние документа:
сайт (views.py) и Telegram-бот (apps/accounts/bot/screens/edocs.py) вызывают эти функции.

Каждая операция:
  - проверяет право пользователя (иначе EdocsError с понятным текстом);
  - меняет данные в одной транзакции и пишет событие в DocumentRoute;
  - после коммита — живое обновление карточки (WebSocket) и уведомления в Telegram.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Assignment, Attachment, Comment, Document, DocumentRoute, RegCounter

logger = logging.getLogger(__name__)
User = get_user_model()

MAX_FILE_MB = 50
MAX_FILES = 20
MAX_TEXT = 5000
# Белый список: HTML/SVG/JS и прочее, что браузер может исполнить, не принимаем.
ALLOWED_EXT = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".rtf",
    ".txt", ".csv", ".zip", ".rar", ".7z",
}
ACTIVE = ("pending", "done", "rejected")   # всё, кроме forwarded


class EdocsError(Exception):
    """Действие недопустимо; текст показывается пользователю."""


# ── Права ─────────────────────────────────────────────────────────────────────
def is_supervisor(user) -> bool:
    """Видит все документы (журнал): суперпользователь или возможность edocs_journal."""
    if user.is_superuser:
        return True
    acc = getattr(user, "access", None)
    return bool(acc and acc.has_cap("edocs_journal"))


def can_access(user, doc) -> bool:
    if not user.is_authenticated:
        return False
    if user.pk == doc.author_id or is_supervisor(user):
        return True
    if doc.status == "draft":
        return False
    return doc.assignments.filter(user=user).exists() or \
        doc.routes.filter(Q(sender=user) | Q(recipient=user)).exists()


def my_assignment(user, doc) -> Assignment | None:
    """Действующее назначение пользователя (не переданное дальше)."""
    if not user.is_authenticated:
        return None
    return doc.assignments.filter(user=user, status__in=ACTIVE).first()


@dataclass
class Allowed:
    """Что пользователь может сделать с документом сейчас — для кнопок и проверок."""
    edit_draft: bool = False
    send: bool = False
    delete: bool = False
    decide: bool = False          # исполнить / согласовать / отклонить
    acknowledge: bool = False     # ознакомиться
    forward: bool = False
    revision: bool = False        # вернуть на доработку (автор)
    add_assignee: bool = False    # добавить адресата (автор)
    comment: bool = False

    @property
    def any_primary(self) -> bool:
        return self.decide or self.acknowledge or self.send or self.revision


def allowed(user, doc, asg: Assignment | None = None) -> Allowed:
    a = Allowed()
    if not user.is_authenticated:
        return a
    is_author = user.pk == doc.author_id
    if doc.status == "draft":
        a.edit_draft = a.send = a.delete = is_author
        return a
    asg = asg if asg is not None else my_assignment(user, doc)
    pending = bool(asg and asg.status == "pending" and doc.is_open)
    a.decide = pending and doc.is_decision
    a.acknowledge = pending and not doc.is_decision
    a.forward = pending
    a.revision = is_author and doc.is_decision and doc.status in ("approved", "rejected")
    a.add_assignee = is_author and doc.is_open
    a.comment = is_author or asg is not None or is_supervisor(user)
    return a


# ── Файлы ─────────────────────────────────────────────────────────────────────
def check_files(files) -> None:
    files = [f for f in files if f]
    if len(files) > MAX_FILES:
        raise EdocsError(f"Не больше {MAX_FILES} файлов за раз.")
    for f in files:
        ext = os.path.splitext(f.name)[1].lower()
        if ext not in ALLOWED_EXT:
            raise EdocsError(f"«{f.name}»: тип файла {ext or 'без расширения'} не разрешён.")
        if f.size > MAX_FILE_MB * 1024 * 1024:
            raise EdocsError(f"«{f.name}» больше {MAX_FILE_MB} МБ.")


def _attach(doc, user, files, kind="main", asg=None) -> list[Attachment]:
    out = []
    for f in files:
        if not f:
            continue
        name = os.path.basename(f.name)[:255]
        out.append(Attachment.objects.create(document=doc, assignment=asg, kind=kind, file=f,
                                             name=name, size=f.size or 0, uploaded_by=user))
    return out


def ensure_pdf(att: Attachment) -> str | None:
    """Путь к файлу для просмотра в браузере: PDF/картинка как есть, офис — конвертация (один раз)."""
    if att.pdf_file:
        try:
            if os.path.exists(att.pdf_file.path):
                return att.pdf_file.path
        except Exception:  # noqa: BLE001
            pass
    if att.ext in Attachment.PREVIEW_EXT:
        return att.file.path
    if att.ext not in Attachment.CONVERT_EXT:
        return None
    from django.core.files import File

    from .utils import convert_to_pdf
    pdf = convert_to_pdf(att.file.path)
    if not pdf:
        return None
    try:
        with open(pdf, "rb") as fh:
            att.pdf_file.save(os.path.splitext(att.name)[0][:200] + ".pdf", File(fh), save=False)
        Attachment.objects.filter(pk=att.pk).update(pdf_file=att.pdf_file.name)
    finally:
        _rm_tree(os.path.dirname(pdf))
    return att.pdf_file.path


def _rm_tree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


# ── Регистрационный номер ────────────────────────────────────────────────────
def next_reg_number(doc_type: str) -> str:
    """ИСП-2026/0001. Счётчик под блокировкой строки — номера не повторяются при параллельной отправке."""
    key = f"{Document.REG_PREFIX.get(doc_type, 'ДОК')}-{timezone.localdate().year}"
    with transaction.atomic():
        RegCounter.objects.get_or_create(key=key)
        c = RegCounter.objects.select_for_update().get(key=key)
        c.value += 1
        c.save(update_fields=["value"])
    return f"{key}/{c.value:04d}"


# ── Создание и отправка ──────────────────────────────────────────────────────
def _clean_text(s, limit=MAX_TEXT) -> str:
    return (s or "").strip()[:limit]


def _users(ids, exclude=None) -> list:
    """Активные пользователи по id в порядке выбора, без повторов."""
    seen, order = set(), []
    for raw in ids:
        try:
            i = int(raw)
        except (TypeError, ValueError):
            continue
        if i not in seen and i != exclude:
            seen.add(i)
            order.append(i)
    found = {u.pk: u for u in User.objects.filter(pk__in=order, is_active=True)}
    return [found[i] for i in order if i in found]


def save_document(author, *, doc=None, title, message="", document_type="execution", priority="normal",
                  due_date=None, main_id=None, co_ids=(), files=(), remove_files=(), send=False) -> Document:
    """Создать документ или изменить черновик; send=True — сразу зарегистрировать и направить."""
    title = _clean_text(title, 255)
    if not title:
        raise EdocsError("Укажите тему документа.")
    if document_type not in dict(Document.TYPE_CHOICES):
        raise EdocsError("Неизвестный вид документа.")
    if priority not in dict(Document.PRIORITY_CHOICES):
        priority = "normal"
    if due_date and due_date < timezone.localdate():
        raise EdocsError("Срок исполнения не может быть в прошлом.")
    check_files(files)

    main = _users([main_id], exclude=author.pk)
    co = [u for u in _users(co_ids, exclude=author.pk) if not main or u.pk != main[0].pk]
    if send and not main:
        raise EdocsError("Выберите адресата.")

    with transaction.atomic():
        if doc is None:
            doc = Document(author=author)
        elif doc.status != "draft" or doc.author_id != author.pk:
            raise EdocsError("Изменять можно только свой черновик.")
        doc.title, doc.message = title, _clean_text(message)
        doc.document_type, doc.priority, doc.due_date = document_type, priority, due_date
        doc.recipient = main[0] if main else None
        doc.status = "draft"
        is_new = doc.pk is None
        doc.save()
        if is_new:
            DocumentRoute.objects.create(document=doc, action="created", sender=author, status_at_moment="draft")

        # Черновые назначения пересоздаём целиком
        doc.assignments.all().delete()
        for i, u in enumerate(main + co):
            Assignment.objects.create(document=doc, user=u, role="main" if i == 0 and main else "co",
                                      assigned_by=author)
        if remove_files:
            for att in doc.attachments.filter(pk__in=list(remove_files), kind="main"):
                att.file.delete(save=False)
                if att.pdf_file:
                    att.pdf_file.delete(save=False)
                att.delete()
        _attach(doc, author, files)

        if send:
            _send(doc, author)
    if send:
        _after_commit(doc, notify=[(a.user, "new") for a in doc.assignments.select_related("user")])
    return doc


def send_draft(user, doc) -> Document:
    if doc.status != "draft" or doc.author_id != user.pk:
        raise EdocsError("Отправить можно только свой черновик.")
    if not doc.assignments.exists():
        raise EdocsError("В черновике не выбран адресат.")
    if doc.due_date and doc.due_date < timezone.localdate():
        raise EdocsError("Срок исполнения уже прошёл — измените черновик.")
    with transaction.atomic():
        doc = Document.objects.select_for_update().get(pk=doc.pk)
        if doc.status != "draft":
            raise EdocsError("Документ уже отправлен.")
        _send(doc, user)
    _after_commit(doc, notify=[(a.user, "new") for a in doc.assignments.select_related("user")])
    return doc


def _send(doc, user) -> None:
    doc.reg_number = doc.reg_number or next_reg_number(doc.document_type)
    doc.status = "review"
    doc.sent_at = timezone.now()
    doc.save()
    names = ", ".join(a.user.short_name for a in doc.assignments.select_related("user"))
    DocumentRoute.objects.create(document=doc, action="sent", sender=user, recipient=doc.recipient,
                                 status_at_moment="review", comment=f"Адресаты: {names}" if names else "")


def delete_draft(user, doc) -> None:
    if doc.status != "draft" or doc.author_id != user.pk:
        raise EdocsError("Удалить можно только свой черновик.")
    for att in doc.attachments.all():
        att.file.delete(save=False)
        if att.pdf_file:
            att.pdf_file.delete(save=False)
    doc.delete()


# ── Действия адресата ────────────────────────────────────────────────────────
def mark_read(user, doc) -> None:
    Assignment.objects.filter(document=doc, user=user, is_read=False).update(is_read=True, read_at=timezone.now())


def _locked(doc, user):
    """Документ и назначение пользователя под блокировкой; проверка, что решение ещё за ним."""
    doc = Document.objects.select_for_update().get(pk=doc.pk)
    asg = (Assignment.objects.select_for_update()
           .filter(document=doc, user=user, status="pending").first())
    if asg is None or not doc.is_open:
        raise EdocsError("Решение по этому документу уже принято или принимать его не вам.")
    return doc, asg


def decide(user, doc, outcome: str, report: str = "", files=()) -> Document:
    """outcome: done (исполнено / согласовано) | rejected."""
    if outcome not in ("done", "rejected"):
        raise EdocsError("Неизвестное решение.")
    report = _clean_text(report)
    if outcome == "rejected" and not report:
        raise EdocsError("Укажите причину отклонения — автор её увидит.")
    check_files(files)
    with transaction.atomic():
        doc, asg = _locked(doc, user)
        if not doc.is_decision:
            raise EdocsError("Этот документ не требует решения — нажмите «Ознакомлен».")
        asg.status, asg.report, asg.decided_at = outcome, report, timezone.now()
        asg.is_read = True
        asg.read_at = asg.read_at or asg.decided_at
        asg.save()
        _attach(doc, user, files, kind="reply", asg=asg)
        action = "rejected" if outcome == "rejected" else ("approved" if doc.document_type == "approval" else "done")
        DocumentRoute.objects.create(document=doc, action=action, sender=user, recipient=doc.author,
                                     status_at_moment=doc.status, comment=report)
        recompute_status(doc, user)
    _after_commit(doc, notify=[(doc.author, action)], actor=user, comment=report)
    return doc


def acknowledge(user, doc) -> Document:
    with transaction.atomic():
        doc, asg = _locked(doc, user)
        if doc.is_decision:
            raise EdocsError("Этот документ требует решения, а не ознакомления.")
        now = timezone.now()
        asg.status, asg.decided_at, asg.is_read = "done", now, True
        asg.read_at = asg.read_at or now
        asg.save()
        DocumentRoute.objects.create(document=doc, action="acknowledged", sender=user, recipient=doc.author,
                                     status_at_moment=doc.status)
        recompute_status(doc, user)
    _after_commit(doc, notify=[(doc.author, "completed")] if doc.status == "approved" else [], actor=user)
    return doc


def forward(user, doc, to_id, comment: str = "") -> Document:
    """Передать своё поручение другому сотруднику (переадресация). Своё назначение → forwarded."""
    comment = _clean_text(comment)
    with transaction.atomic():
        doc, asg = _locked(doc, user)
        target = _users([to_id])
        if not target:
            raise EdocsError("Выберите сотрудника.")
        target = target[0]
        if target.pk in (user.pk, doc.author_id):
            raise EdocsError("Нельзя передать документ себе или автору.")
        if doc.assignments.filter(user=target, status__in=ACTIVE).exists():
            raise EdocsError(f"{target.short_name} уже среди адресатов.")
        asg.status, asg.decided_at = "forwarded", timezone.now()
        asg.save()
        new = Assignment.objects.create(document=doc, user=target, role=asg.role, assigned_by=user,
                                        forwarded_from=asg, report="")
        if asg.role == "main":
            doc.recipient = target
            doc.save(update_fields=["recipient", "updated_at"])
        DocumentRoute.objects.create(document=doc, action="forwarded", sender=user, recipient=target,
                                     status_at_moment=doc.status, comment=comment)
    _after_commit(doc, notify=[(new.user, "new"), (doc.author, "forwarded")], actor=user, comment=comment,
                  target=target)
    return doc


# ── Действия автора ──────────────────────────────────────────────────────────
def return_for_revision(user, doc, comment: str, due_date=None) -> Document:
    comment = _clean_text(comment)
    if not comment:
        raise EdocsError("Опишите, что нужно доработать.")
    if due_date and due_date < timezone.localdate():
        raise EdocsError("Новый срок не может быть в прошлом.")
    with transaction.atomic():
        doc = Document.objects.select_for_update().get(pk=doc.pk)
        if not allowed(user, doc).revision:
            raise EdocsError("Вернуть на доработку может автор завершённого документа.")
        asgs = list(doc.assignments.filter(status__in=("done", "rejected")).select_related("user"))
        Assignment.objects.filter(pk__in=[a.pk for a in asgs]).update(status="pending", decided_at=None)
        doc.status, doc.completed_at = "revision", None
        if due_date:
            doc.due_date = due_date
        doc.save()
        DocumentRoute.objects.create(document=doc, action="revision", sender=user, recipient=doc.recipient,
                                     status_at_moment="revision", comment=comment)
    _after_commit(doc, notify=[(a.user, "revision") for a in asgs], actor=user, comment=comment)
    return doc


def add_assignee(user, doc, user_id) -> Document:
    with transaction.atomic():
        doc = Document.objects.select_for_update().get(pk=doc.pk)
        if not allowed(user, doc).add_assignee:
            raise EdocsError("Добавлять адресатов может автор документа в работе.")
        target = _users([user_id], exclude=user.pk)
        if not target:
            raise EdocsError("Выберите сотрудника.")
        target = target[0]
        if doc.assignments.filter(user=target, status__in=ACTIVE).exists():
            raise EdocsError(f"{target.short_name} уже среди адресатов.")
        Assignment.objects.create(document=doc, user=target, role="co", assigned_by=user)
        DocumentRoute.objects.create(document=doc, action="assigned", sender=user, recipient=target,
                                     status_at_moment=doc.status)
        recompute_status(doc, user)
    _after_commit(doc, notify=[(target, "new")], actor=user)
    return doc


def add_comment(user, doc, text: str) -> Comment:
    text = _clean_text(text, 2000)
    if not text:
        raise EdocsError("Пустой комментарий.")
    if not allowed(user, doc).comment:
        raise EdocsError("Комментировать могут автор и адресаты документа.")
    c = Comment.objects.create(document=doc, author=user, text=text)
    Document.objects.filter(pk=doc.pk).update(updated_at=timezone.now())
    others = {doc.author_id} | set(doc.assignments.filter(status__in=ACTIVE).values_list("user_id", flat=True))
    others.discard(user.pk)
    _after_commit(doc, notify=[(u, "comment") for u in User.objects.filter(pk__in=others)],
                  actor=user, comment=text)
    return c


# ── Статус документа из статусов адресатов ───────────────────────────────────
def recompute_status(doc, actor=None) -> None:
    if doc.status == "draft":
        return
    states = list(doc.assignments.filter(status__in=ACTIVE).values_list("status", flat=True))
    if not states:
        return
    before = doc.status
    if "rejected" in states and "pending" not in states:
        doc.status = "rejected"
    elif all(s == "done" for s in states):
        doc.status = "approved"
    elif doc.status not in ("review", "revision"):
        doc.status = "review"
    if doc.status != before:
        doc.completed_at = timezone.now() if doc.status in ("approved", "rejected") else None
        if doc.status == "approved" and actor is not None:
            DocumentRoute.objects.create(document=doc, action="completed", sender=actor, recipient=doc.author,
                                         status_at_moment="approved")
    doc.save()


# ── После коммита: живое обновление и уведомления ────────────────────────────
def _after_commit(doc, notify=(), actor=None, comment="", target=None) -> None:
    pairs = [(u.pk, kind) for u, kind in notify if u is not None and (actor is None or u.pk != actor.pk)]
    doc_id = doc.pk
    actor_name = (actor.get_full_name() or actor.username) if actor else ""
    target_name = target.short_name if target else ""

    def run():
        broadcast(doc_id)
        if pairs:
            try:
                from .tasks import notify_users
                notify_users.delay(doc_id, pairs, actor_name, comment[:500], target_name)
            except Exception:  # noqa: BLE001 — брокер недоступен: документ всё равно сохранён
                logger.exception("edocs notify enqueue %s", doc_id)

    transaction.on_commit(run)


def broadcast(doc_id) -> None:
    """Сообщить открытым карточкам документа, что он изменился (они перезагрузят историю)."""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        layer = get_channel_layer()
        if layer is not None:
            async_to_sync(layer.group_send)(f"doc_{doc_id}", {"type": "doc_changed"})
    except Exception:  # noqa: BLE001 — живое обновление необязательно
        logger.exception("edocs broadcast %s", doc_id)
