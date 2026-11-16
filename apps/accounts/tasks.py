"""Фоновые задачи accounts: отправка Telegram-уведомлений."""
from celery import shared_task


@shared_task(name="accounts.send_telegram_broadcast", ignore_result=True,
             soft_time_limit=120, time_limit=150)
def send_telegram_broadcast(text: str, reply_markup: dict | None = None, tag: str = "", meta: dict | None = None) -> int:
    """Рассылает готовый текст подписчикам алертов с учётом их личных настроек (meta: kind/severity/stations)."""
    from .telegram_notify import _send_to_all
    return _send_to_all(text, reply_markup, tag, meta)
