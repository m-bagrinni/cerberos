"""
Совместимость: действия с документом для Telegram-бота. Вся логика — в services.py;
здесь тонкие обёртки, возвращающие текст ошибки вместо исключения (так их зовёт бот).
"""
from . import services as svc
from .services import can_access, mark_read  # noqa: F401 — реэкспорт для бота


def can_decide(user, doc) -> bool:
    return svc.allowed(user, doc).decide


def can_acknowledge(user, doc) -> bool:
    return svc.allowed(user, doc).acknowledge


def _run(fn, *args) -> str:
    try:
        fn(*args)
    except svc.EdocsError as e:
        return str(e)
    return ""


def set_status(user, doc, status: str, comment: str = "") -> str:
    """'' — успех, иначе текст ошибки. status: approved | rejected (как раньше)."""
    if status not in ("approved", "rejected"):
        return "Неверный статус."
    return _run(svc.decide, user, doc, "done" if status == "approved" else "rejected", comment)


def acknowledge(user, doc) -> str:
    return _run(svc.acknowledge, user, doc)


def add_comment(user, doc, text: str) -> str:
    return _run(svc.add_comment, user, doc, text)
