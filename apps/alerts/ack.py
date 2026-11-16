"""
«Взять в работу» → «Решено» / «Отказаться», и эскалация алертов.

- acknowledge(): отмечает, кто занялся алертом, и сообщает остальным получателям в Telegram.
  Нужно право «Алерты: изменение».
- escalate_unacked(): Critical / «Нет питания», которые висят дольше ESCALATE_AFTER_MIN
  и никем не взяты, — одно напоминание всем получателям (задача beat раз в 5 минут).
"""
import logging
from datetime import timedelta
from html import escape

from django.db import transaction
from django.utils import timezone

from .models import Alert

logger = logging.getLogger(__name__)

ESCALATE_AFTER_MIN = 15
ESCALATE_SEVERITIES = (Alert.SEVERITY_CRIT, Alert.SEVERITY_POWER)


def user_label(user) -> str:
    return (user.get_full_name() or user.username) if user else "—"


def acknowledge(alert_id: int, user):
    """Возвращает (alert | None, ошибка). Повторное нажатие тем же пользователем — не ошибка."""
    if not user.access.can_edit("alerts"):
        return None, "Нет права «Алерты: изменение»."
    with transaction.atomic():
        alert = (Alert.objects.select_for_update(of=("self",))
                 .select_related("acked_by").filter(pk=alert_id).first())
        if alert is None:
            return None, "Алерт не найден."
        if alert.state != Alert.STATE_FIRING:
            return alert, "Алерт уже закрыт."
        if alert.acked_by_id and alert.acked_by_id != user.pk:
            return alert, f"Уже в работе: {user_label(alert.acked_by)}."
        if alert.acked_by_id == user.pk:
            return alert, ""
        alert.acked_by = user
        alert.acked_at = timezone.now()
        alert.save(update_fields=["acked_by", "acked_at", "updated_at"])
    _notify_ack(alert, user)
    logger.info("alert %s взят в работу: %s", alert.pk, user.username)
    return alert, ""


def _can_manage_others(user) -> bool:
    return user.is_superuser or user.access.has_cap("users_manage")


def resolve(alert_id: int, user, note: str = ""):
    """«Решено»: закрыть вручную. Если проблема осталась — следующий опрос откроет новый алерт."""
    if not user.access.can_edit("alerts"):
        return None, "Нет права «Алерты: изменение»."
    note = (note or "").strip()[:1000]
    with transaction.atomic():
        alert = (Alert.objects.select_for_update(of=("self",))
                 .select_related("acked_by").filter(pk=alert_id).first())
        if alert is None:
            return None, "Алерт не найден."
        if alert.state != Alert.STATE_FIRING:
            return alert, "Алерт уже закрыт."
        if alert.acked_by_id and alert.acked_by_id != user.pk and not _can_manage_others(user):
            return alert, f"Алерт в работе у {user_label(alert.acked_by)} — закрыть может только он."
        now = timezone.now()
        if not alert.acked_by_id:
            alert.acked_by, alert.acked_at = user, now
        alert.state = Alert.STATE_OK
        alert.resolved_at = now
        alert.resolved_by = user
        alert.resolution = note
        alert.save(update_fields=["state", "resolved_at", "resolved_by", "resolution",
                                  "acked_by", "acked_at", "updated_at"])
    _notify(alert, "alert_closed",
            f"✅ <b>Решено</b> — {escape(user_label(user))}"
            + (f"\n💬 {escape(note)}" if note else ""))
    logger.info("alert %s закрыт вручную: %s", alert.pk, user.username)
    return alert, ""


def release(alert_id: int, user):
    """«Отказаться»: снять ответственного, алерт снова ждёт, и эскалация снова работает."""
    with transaction.atomic():
        alert = (Alert.objects.select_for_update(of=("self",))
                 .select_related("acked_by").filter(pk=alert_id).first())
        if alert is None:
            return None, "Алерт не найден."
        if alert.state != Alert.STATE_FIRING:
            return alert, "Алерт уже закрыт."
        if not alert.acked_by_id:
            return alert, "Алерт и так без ответственного."
        if alert.acked_by_id != user.pk and not _can_manage_others(user):
            return alert, f"Алерт в работе у {user_label(alert.acked_by)}."
        was = alert.acked_by
        alert.acked_by = None
        alert.acked_at = None
        alert.escalated_at = None
        alert.save(update_fields=["acked_by", "acked_at", "escalated_at", "updated_at"])
    from apps.accounts import telegram_notify as tn
    _notify(alert, "alert_released",
            f"↩ <b>Снова без ответственного</b> — {escape(user_label(user))} "
            + ("отказался" if was.pk == user.pk else f"снял с {escape(user_label(was))}"),
            tn._reply_markup(alert))
    return alert, ""


def _notify(alert, kind: str, head: str, markup=None) -> None:
    from apps.accounts import telegram_notify as tn
    text = (f"{head}\n🎯 {escape(tn._alert_target(alert))}\n"
            f"📊 <code>{escape(alert.metric_key or '')}</code>")
    try:
        tn._dispatch(text, markup, f"{kind}:{alert.pk}", tn._alert_meta(alert, kind))
    except Exception:  # noqa: BLE001
        logger.exception("alert notify %s", kind)


def _notify_ack(alert, user) -> None:
    from apps.accounts import telegram_notify as tn
    text = (f"🛠 <b>Взято в работу</b> — {escape(user_label(user))}\n"
            f"🎯 {escape(tn._alert_target(alert))}\n"
            f"📊 <code>{escape(alert.metric_key or '')}</code>")
    try:
        tn._dispatch(text, None, f"alert_ack:{alert.pk}", tn._alert_meta(alert, "alert_ack"))
    except Exception:  # noqa: BLE001 — уведомление не должно ломать сам факт «взял»
        logger.exception("alert ack notify")


def escalate_unacked(now=None) -> int:
    from apps.accounts import telegram_notify as tn
    now = now or timezone.now()
    border = now - timedelta(minutes=ESCALATE_AFTER_MIN)
    qs = (Alert.objects.filter(state=Alert.STATE_FIRING, severity__in=ESCALATE_SEVERITIES,
                               acked_by__isnull=True, escalated_at__isnull=True, started_at__lte=border)
          .select_related("device", "ne_device", "ip_address", "ne_device__gateway"))
    n = 0
    for alert in qs[:50]:
        # отметка до отправки: при сбое Telegram не будет повторов каждые 5 минут
        if not Alert.objects.filter(pk=alert.pk, escalated_at__isnull=True).update(escalated_at=now):
            continue
        mins = int((now - alert.started_at).total_seconds() // 60)
        text = (f"⏰ <b>Никто не взял в работу</b> ({mins} мин)\n\n" + tn._alert_text(alert))
        tn._dispatch(text, tn._reply_markup(alert), f"alert_escalation:{alert.pk}",
                     tn._alert_meta(alert, "alert_escalation"))
        n += 1
    if n:
        logger.warning("эскалация: %s алертов без ответственного", n)
    return n
