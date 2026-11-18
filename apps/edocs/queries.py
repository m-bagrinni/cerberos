"""Общие выборки СЭД: «что ждёт моего действия».

Используются на главной (счётчики), во входящих (?f=<ключ>) и в Telegram-боте,
чтобы цифра на главной всегда совпадала со списком по клику.

«Ждёт меня» = моё назначение (Assignment) в статусе pending у отправленного документа.
Фильтры описаны на Assignment, документы выбираются через pk__in — так условия
по пользователю и статусу гарантированно относятся к одной строке назначения.
"""
from datetime import timedelta

from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

_OPEN = Q(document__status__in=("review", "revision"))

# Плитки на главной и в боте (порядок важен)
INBOX_FILTERS = {
    "execution": ("На исполнение", Q(document__document_type="execution", document__status="review")),
    "approval": ("На согласование", Q(document__document_type="approval", document__status="review")),
    "familiarize": ("На ознакомление", Q(document__document_type__in=("review", "info"))),
    "revision": ("На доработке", Q(document__status="revision")),
}


def _extra_filters():
    today = timezone.localdate()
    return {
        "unread": ("Непрочитанные", Q(is_read=False)),
        "due": ("Срок до завтра", Q(document__due_date__lte=today + timedelta(days=1))),
        "overdue": ("Просрочено", Q(document__due_date__lt=today)),
    }


def filter_label(key: str) -> str:
    if key in INBOX_FILTERS:
        return INBOX_FILTERS[key][0]
    return _extra_filters().get(key, ("",))[0]


def my_pending(user):
    from .models import Assignment
    return Assignment.objects.filter(user=user, status="pending").filter(_OPEN)


def inbox_qs(user):
    """Все документы, где пользователь был адресатом (включая переданные дальше)."""
    from .models import Assignment, Document
    mine = Assignment.objects.filter(document=OuterRef("pk"), user=user)
    return Document.objects.exclude(status="draft").filter(Exists(mine))


def pending_qs(user, key: str = ""):
    """Документы, ждущие действия пользователя; key — один из INBOX_FILTERS / дополнительных."""
    from .models import Document
    rows = my_pending(user)
    q = INBOX_FILTERS.get(key) or _extra_filters().get(key)
    if q:
        rows = rows.filter(q[1])
    return Document.objects.filter(pk__in=rows.values("document_id"))


def unread_qs(user):
    return pending_qs(user, "unread")


def pending_counts(user):
    """{ключ: число} по INBOX_FILTERS + total + overdue — одним запросом."""
    today = timezone.localdate()
    return my_pending(user).aggregate(
        total=Count("id"),
        overdue=Count("id", filter=Q(document__due_date__lt=today)),
        unread=Count("id", filter=Q(is_read=False)),
        **{key: Count("id", filter=q) for key, (_l, q) in INBOX_FILTERS.items()},
    )
