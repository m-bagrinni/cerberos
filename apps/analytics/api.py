import json
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.db.models import Sum, Avg, Count, Q, Min, Max
from django.utils import timezone
from datetime import timedelta

from apps.devices.models import Device
from apps.alerts.models import Alert
from apps.stations.models import OpticalLink
from apps.monitoring.models import InterfaceSample, MetricValue

@require_GET
def analytics_data(request):
    """Отдает JSON для графиков и таблиц аналитики."""
    days = int(request.GET.get('days', 7))
    since = timezone.now() - timedelta(days=days)
    mid_time = since + timedelta(days=days/2)

    # 1. История алертов по дням (исключая CPU и шлюз mux.LAZ)
    alerts_history = (
        Alert.objects.filter(started_at__gte=since)
        .exclude(metric__key__icontains='cpu')
        .exclude(metric__key__icontains='load')
        .exclude(device__name__icontains='mux.LAZ')
        .extra(select={'day': 'date(started_at)'})
        .values('day')
        .annotate(
            crit=Count('id', filter=Q(severity='crit')),
            warn=Count('id', filter=Q(severity='warn'))
        )
        .order_by('day')
    )

    # 2. Трафик по дням
    traffic_history = (
        InterfaceSample.objects.filter(ts__gte=since)
        .extra(select={'day': 'date(ts)'})
        .values('day')
        .annotate(
            total_in=Avg('in_bps'),
            total_out=Avg('out_bps')
        )
        .order_by('day')
    )

    # 3. Статусы оптики
    optics_summary = OpticalLink.objects.filter(is_active=True).aggregate(
        ok=Count('id', filter=Q(link_status='ok')),
        warn=Count('id', filter=Q(link_status='warn')),
        degraded=Count('id', filter=Q(link_status='degraded')),
        crit=Count('id', filter=Q(link_status='crit')),
        no_signal=Count('id', filter=Q(link_status='no_signal')),
    )

    # 4. Статистика устройств
    devices_stats = {
        "total": Device.objects.count(),
        "online": Device.objects.filter(status='ok').count(),
        "offline": Device.objects.filter(status='unreachable').count(),
    }

    # 5. История деградации/восстановления DDM (Сглаженная по часам)
    ddm_history = []
    
    metrics = MetricValue.objects.filter(
        ts__gte=since, 
        metric__key__in=['sfp_rx_dbm', 'sfp_temp']
    ).exclude(value__isnull=True).exclude(value=0)
    
    device_groups = {}
    for m in metrics:
        name = m.ne_device.name if m.ne_device else (m.device.name if m.device else 'Unknown')
        key = f"{name} ({m.metric.key})"
        if key not in device_groups:
            device_groups[key] = {'name': name, 'metric': m.metric.key, 'points': []}
        device_groups[key]['points'].append({'t': m.ts, 'v': m.value})

    problem_devices_ddm = []
    for key, data in device_groups.items():
        points = sorted(data['points'], key=lambda x: x['t'])
        if len(points) < 2:
            continue
            
        # Фильтрация выбросов
        filtered_points = []
        for p in points:
            if data['metric'] == 'sfp_rx_dbm' and (p['v'] < -40 or p['v'] > 5):
                continue
            if data['metric'] == 'sfp_temp' and (p['v'] < 0 or p['v'] > 100):
                continue
            filtered_points.append(p)
            
        if len(filtered_points) < 2:
            continue

        # Группировка по часам
        hourly_data = {}
        for p in filtered_points:
            hour_str = p['t'].strftime('%Y-%m-%d %H:00')
            if hour_str not in hourly_data:
                hourly_data[hour_str] = []
            hourly_data[hour_str].append(p['v'])
            
        smoothed_points = []
        for hour_str, vals in sorted(hourly_data.items()):
            avg_val = sum(vals) / len(vals)
            smoothed_points.append({'t': hour_str, 'v': round(avg_val, 2)})

        mid_idx = len(smoothed_points) // 2
        first_half = [p['v'] for p in smoothed_points[:mid_idx]]
        second_half = [p['v'] for p in smoothed_points[mid_idx:]]
        
        avg_first = sum(first_half) / len(first_half) if first_half else 0
        avg_second = sum(second_half) / len(second_half) if second_half else 0
        trend = avg_second - avg_first
        
        is_degradation = False
        if data['metric'] == 'sfp_rx_dbm':
            is_degradation = trend < 0
        elif data['metric'] == 'sfp_temp':
            is_degradation = trend > 0

        if True:  # Показываем все устройства (даже если нет деградации)
            problem_devices_ddm.append({
                'name': key,
                'trend': round(trend, 2),
                'last': smoothed_points[-1]['v'],
                'type': 'degradation' if is_degradation else 'recovery',
                'is_temp': data['metric'] == 'sfp_temp',
                'history': smoothed_points
            })
    
    problem_devices_ddm.sort(key=lambda x: abs(x['trend']), reverse=True)
    ddm_history = problem_devices_ddm

    # 6. Техническая справка (Детализация)
    
    # 6.1 Активные алерты (исключая CPU, load и mux.LAZ)
    active_alerts = []
    alerts_qs = Alert.objects.filter(state='firing').exclude(
        metric__key__icontains='cpu'
    ).exclude(
        metric__key__icontains='load'
    ).exclude(
        device__name__icontains='mux.LAZ'
    ).order_by('-severity', '-started_at')[:10]
    
    for a in alerts_qs:
        # Приоритет имени: ne_device -> device
        name = a.ne_device.name if a.ne_device else (a.device.name if a.device else "Unknown")
        
        # Приоритет метрики: message -> metric.name -> metric_key
        metric_name = "Unknown"
        if a.message:
            metric_name = a.message
        elif a.metric:
            metric_name = a.metric.name
        elif a.metric_key:
            metric_name = a.metric_key
            
        active_alerts.append({
            'device': name,
            'metric': metric_name,
            'severity': a.severity,
            'value': round(a.value, 2) if a.value is not None else 0,
            'threshold': round(a.threshold, 2) if a.threshold is not None else 0,
            'started': a.started_at.strftime('%d.%m %H:%M')
        })

    # 6.2 Проблемы с оптикой
    optics_issues = []
    for l in OpticalLink.objects.filter(is_active=True).exclude(link_status__in=['ok', 'warn']):
        optics_issues.append({
            'label': l.label or f"Линия #{l.id}",
            'status': l.link_status,
            'end_a': l.end_a_label,
            'end_b': l.end_b_label,
        })

    # 6.3 Проблемные устройства (и Device, и NeDevice)
    from apps.devices.models import NeDevice
    
    problem_devices = []
    seen_names = set()
    
    # 1. Обычные устройства (Device), у которых статус не 'ok'
    for d in Device.objects.exclude(status='ok').exclude(name__icontains='mux.LAZ')[:10]:
        problem_devices.append({
            'name': d.name,
            'ip': d.ip,
            'status': d.status,
            'type': 'Device',
            'last_polled': d.last_polled_at.strftime('%d.%m %H:%M') if d.last_polled_at else 'Никогда'
        })
        seen_names.add(d.name)

    # 2. NE-устройства (NeDevice), у которых статус не 'ok'
    for ne in NeDevice.objects.exclude(status='ok')[:10]:
        if ne.name not in seen_names:
            problem_devices.append({
                'name': ne.name,
                'ip': ne.ne_ip or '—',
                'status': ne.status,
                'type': 'NE',
                'last_polled': ne.last_polled_at.strftime('%d.%m %H:%M') if ne.last_polled_at else 'Никогда'
            })
            seen_names.add(ne.name)

    # 3. Устройства, у которых есть активные алерты (даже если статус 'ok')
    alert_devices = Alert.objects.filter(state='firing').exclude(device__isnull=True).exclude(device__name__icontains='mux.LAZ').values_list('device__name', flat=True).distinct()
    for name in alert_devices:
        if name not in seen_names:
            d = Device.objects.filter(name=name).first()
            if d:
                problem_devices.append({
                    'name': d.name,
                    'ip': d.ip,
                    'status': 'alert', # Показываем, что есть проблема
                    'type': 'Device (Alert)',
                    'last_polled': d.last_polled_at.strftime('%d.%m %H:%M') if d.last_polled_at else 'Никогда'
                })
                seen_names.add(name)

    return JsonResponse({
        'alerts': list(alerts_history),
        'traffic': list(traffic_history),
        'optics': optics_summary,
        'devices': devices_stats,
        'ddm_history': ddm_history,
        'active_alerts': active_alerts,
        'optics_issues': optics_issues,
        'problem_devices': problem_devices,
    })
