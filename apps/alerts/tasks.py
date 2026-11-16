from celery import shared_task


@shared_task(name="alerts.escalate_unacked", ignore_result=True)
def escalate_unacked_task() -> int:
    from .ack import escalate_unacked
    return escalate_unacked()
