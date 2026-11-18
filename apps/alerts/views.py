"""Страница алертов и массовое закрытие."""
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import has_perm


def alerts_list(request):
    """Страница алертов — активные + история."""
    from apps.alerts.models import Alert

    all_firing = list(
        Alert.objects
        .filter(state=Alert.STATE_FIRING)
        .select_related("device", "ne_device", "ne_device__gateway", "ip_address", "metric", "acked_by")
        .order_by("-severity", "-started_at")
    )
    # Каскады: следствия (suppressed_by) сворачиваются под алерт-причину (apps/alerts/correlation.py)
    children = {}
    for a in all_firing:
        if a.suppressed_by_id:
            children.setdefault(a.suppressed_by_id, []).append(a)
    firing = [a for a in all_firing if not a.suppressed_by_id or a.suppressed_by_id not in
              {x.pk for x in all_firing}]
    for a in firing:
        a.cascade = children.get(a.pk, [])
    suppressed_total = len(all_firing) - len(firing)
    resolved = list(
        Alert.objects
        .filter(state=Alert.STATE_OK)
        .select_related("device", "ne_device", "ne_device__gateway", "metric", "resolved_by", "acked_by")
        .order_by("-resolved_at")[:200]
    )

    firing_crit = sum(1 for a in firing if a.severity == Alert.SEVERITY_CRIT)
    firing_warn = sum(1 for a in firing if a.severity == Alert.SEVERITY_WARN)

    context = {
        "firing": firing,
        "resolved": resolved,
        "counts": {
            "firing_total": len(firing),
            "suppressed_total": suppressed_total,
            "firing_crit": firing_crit,
            "firing_warn": firing_warn,
            "resolved_total": len(resolved),
        },
    }
    return render(request, "core/alerts_list.html", context)


@require_POST
def clear_alerts(request):
    """Закрывает все активные алерты (раздел «Алерты» на изменение)."""
    from apps.alerts.models import Alert
    
    if not request.user.access.can_edit('alerts'):
        return JsonResponse({'ok': False, 'error': 'Нет прав'}, status=403)
    
    active_alerts = Alert.objects.filter(state=Alert.STATE_FIRING)
    count = active_alerts.count()
    
    now = timezone.now()
    for alert in active_alerts:
        alert.state = Alert.STATE_OK
        alert.resolved_at = now
        alert.save(update_fields=['state', 'resolved_at', 'updated_at'])
    
    return JsonResponse({'ok': True, 'message': f'Закрыто алертов: {count}'})
