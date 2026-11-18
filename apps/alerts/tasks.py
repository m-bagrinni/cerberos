from celery import shared_task


@shared_task(name="alerts.escalate_unacked", ignore_result=True)
def escalate_unacked_task() -> int:
    from .ack import escalate_unacked
    return escalate_unacked()


@shared_task(name="alerts.correlate", ignore_result=True)
def correlate_task() -> dict:
    """Придержанные уведомления и следствия без причины (apps/alerts/correlation.py)."""
    from .correlation import tick
    return tick()
