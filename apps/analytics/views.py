from django.shortcuts import render
from apps.accounts.access import EDIT, section_required
from django.utils import timezone
from datetime import timedelta
from .models import NetworkReport
from apps.alerts.models import Alert
from apps.devices.models import Device
from apps.stations.models import OpticalLink

@section_required("analytics")
def analytics_dashboard(request):
    reports = NetworkReport.objects.all()[:20]
    latest = reports[0] if reports else None
    return render(request, "analytics/dashboard.html", {"reports": reports, "latest": latest})

@section_required("analytics", EDIT)
def generate_report(request):
    """Создает новый отчет и переадресует на его просмотр."""
    period_end = timezone.now()
    period_start = period_end - timedelta(days=7)

    # Собираем статистику
    devices = Device.objects.all()
    total = devices.count()
    offline = devices.filter(status='unreachable').count()
    uptime = ((total - offline) / total * 100) if total > 0 else 100.0

    # Собираем инциденты за период (исключая CPU, load и mux.LAZ)
    alerts = Alert.objects.filter(started_at__gte=period_start).exclude(
        metric__key__icontains='cpu'
    ).exclude(
        metric__key__icontains='load'
    ).exclude(
        device__name__icontains='mux.LAZ'
    )

    incidents = []
    for a in alerts:
        # Приоритет метрики: message -> metric.name -> metric_key
        metric_name = "Unknown"
        if a.message:
            metric_name = a.message
        elif a.metric:
            metric_name = a.metric.name
        elif a.metric_key:
            metric_name = a.metric_key

        # Подмена критичности: NO LINK — это WARN
        severity = a.severity
        if a.metric_key and 'if_oper_status' in a.metric_key:
            severity = 'warn'
        elif a.message and ('NO LINK' in a.message or 'потерял связь' in a.message):
            severity = 'warn'

        # Форматируем значение
        val = round(a.value, 2) if a.value is not None else 0
        
        # Для NO LINK — DOWN
        if a.metric_key and 'if_oper_status' in a.metric_key:
            val = 'DOWN'
        # Для недоступных устройств — OFFLINE
        elif a.message and 'недоступно' in a.message:
            val = 'OFFLINE'

        incidents.append({
            'started': a.started_at.strftime('%d.%m %H:%M'),
            'device': a.device.name if a.device else (a.ne_device.name if a.ne_device else "Unknown"),
            'metric': metric_name,
            'severity': severity,
            'value': val,
        })

    # Формируем заключение
    conclusion = "Сеть работала стабильно. Критических инцидентов не выявлено."
    health_status = "stable"
    if len(incidents) > 0:
        crit_count = sum(1 for i in incidents if i['severity'] == 'crit')
        warn_count = sum(1 for i in incidents if i['severity'] == 'warn')
        conclusion = f"Зафиксировано {len(incidents)} инцидентов (критических: {crit_count}, предупреждений: {warn_count})."
        health_status = "critical" if crit_count > 0 else "warning"

    optics = OpticalLink.objects.filter(is_active=True)

    report = NetworkReport.objects.create(
        period_start=period_start,
        period_end=period_end,
        metrics={
            "devices": {"total": total, "online": total - offline, "offline": offline},
            "optics": {
                "total": optics.count(),
                "ok": optics.filter(link_status='ok').count(),
                "degraded": optics.filter(link_status='degraded').count(),
                "crit": optics.filter(link_status='crit').count(),
            }
        },
        conclusion=conclusion,
        health_status=health_status,
        incidents=incidents,
        uptime_percent=round(uptime, 2)
    )

    return render(request, "analytics/report_print.html", {"report": report, "user": request.user})
