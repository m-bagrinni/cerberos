from celery import shared_task

from .watchdog import mark


@shared_task(name="core.heartbeat", ignore_result=True)
def heartbeat(queue: str) -> None:
    """Пульс для сторожевого сигнала (apps/core/watchdog.py)."""
    mark(queue)
