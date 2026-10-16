"""Context processors для всех шаблонов."""
from apps.alerts.models import Alert


def alert_counts(request):
    """
    Добавляет в контекст количество активных алертов.
    Используется в base.html для бейджа в sidebar.
    """
    try:
        firing = Alert.objects.filter(state=Alert.STATE_FIRING)
        total = firing.count()
        crit = firing.filter(severity=Alert.SEVERITY_CRIT).count()
        warn = firing.filter(severity=Alert.SEVERITY_WARN).count()
        return {
            "alerts_firing_total": total,
            "alerts_firing_crit": crit,
            "alerts_firing_warn": warn,
        }
    except Exception:
        # На случай миграций / пустой БД
        return {
            "alerts_firing_total": 0,
            "alerts_firing_crit": 0,
            "alerts_firing_warn": 0,
        }
