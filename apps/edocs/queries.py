"""Общие выборки СЭД: «что ждёт моего действия».

Используются и на главной (счётчики), и во входящих (?f=<ключ>),
чтобы цифра на главной всегда совпадала со списком по клику.
"""
from django.db.models import Count, Q

# Ознакомление/информация создаются сразу со статусом approved
# (apps/edocs/views.create_document), поэтому «ждёт» = ещё не прочитан.
_FAMILIARIZE = Q(document_type__in=("review", "info"), is_read=False)

INBOX_FILTERS = {
    "execution": ("На исполнение", Q(document_type="execution", status="review")),
    "approval": ("На согласование", Q(document_type="approval", status="review")),
    "familiarize": ("На ознакомление", _FAMILIARIZE),
    "revision": ("На доработке", Q(status="revision")),
}

PENDING_Q = Q()
for _label, _q in INBOX_FILTERS.values():
    PENDING_Q |= _q


def inbox_qs(user):
    from .models import Document
    return Document.objects.filter(recipient=user).exclude(status="draft")


def pending_counts(user):
    """{ключ: число} по INBOX_FILTERS + total — одним запросом."""
    agg = inbox_qs(user).aggregate(
        total=Count("id", filter=PENDING_Q),
        **{key: Count("id", filter=q) for key, (_l, q) in INBOX_FILTERS.items()},
    )
    return agg
