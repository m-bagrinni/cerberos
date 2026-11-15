"""Фоновые задачи accounts: отправка Telegram-уведомлений."""
from celery import shared_task


@shared_task(name="accounts.send_telegram_broadcast", ignore_result=True,
             soft_time_limit=120, time_limit=150)
def send_telegram_broadcast(text: str, reply_markup: dict | None = None, tag: str = "") -> int:
    """Рассылает готовый текст всем подписчикам алертов (вызывается из telegram_notify._dispatch)."""
    from .telegram_notify import _send_to_all
    return _send_to_all(text, reply_markup, tag)
