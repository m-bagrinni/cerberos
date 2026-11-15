from django.utils import timezone
from datetime import timedelta
from apps.devices.models import Device
from apps.alerts.models import Alert
from apps.stations.models import OpticalLink

def collect_network_data():
    """Собирает все метрики и возвращает словарь."""
    total_devices = Device.objects.count()
    online_devices = Device.objects.filter(status='ok').count()
    offline_devices = Device.objects.filter(status='unreachable').count()
    
    active_alerts = Alert.objects.filter(state='firing')
    crit_alerts = active_alerts.filter(severity='crit').count()
    warn_alerts = active_alerts.filter(severity='warn').count()
    
    total_links = OpticalLink.objects.filter(is_active=True).count()
    links_ok = OpticalLink.objects.filter(is_active=True, link_status='ok').count()
    links_no_signal = OpticalLink.objects.filter(is_active=True, link_status='no_signal').count()
    links_crit = OpticalLink.objects.filter(is_active=True, link_status='crit').count()
    links_degraded = OpticalLink.objects.filter(is_active=True, link_status='degraded').count()
    
    return {
        "devices": {
            "total": total_devices,
            "online": online_devices,
            "offline": offline_devices,
        },
        "alerts": {
            "crit": crit_alerts,
            "warn": warn_alerts,
        },
        "optics": {
            "total": total_links,
            "ok": links_ok,
            "no_signal": links_no_signal,
            "crit": links_crit,
            "degraded": links_degraded,
        }
    }

def generate_conclusion(metrics):
    """Генерирует текстовое заключение на основе метрик."""
    conclusion = []
    health_status = "stable"
    
    # Анализ устройств
    total = metrics['devices']['total']
    offline = metrics['devices']['offline']
    if total > 0:
        offline_percent = (offline / total) * 100
        if offline_percent > 10:
            conclusion.append(f"⚠️ Критическая ситуация с доступностью: {offline} из {total} устройств офлайн ({offline_percent:.1f}%).")
            health_status = "critical"
        elif offline_percent > 0:
            conclusion.append(f"🔸 Зафиксированы проблемы доступности: {offline} устройств офлайн.")
            if health_status == "stable": health_status = "warning"
            
    # Анализ алертов
    crit = metrics['alerts']['crit']
    warn = metrics['alerts']['warn']
    if crit > 0:
        conclusion.append(f"🔴 Активных критических алертов: {crit}. Требуется немедленное вмешательство.")
        health_status = "critical"
    elif warn > 0:
        conclusion.append(f"🟡 Активных предупреждений: {warn}. Рекомендуется проверить.")
        if health_status == "stable": health_status = "warning"
        
    # Анализ оптики
    opt = metrics['optics']
    opt_problems = opt['no_signal'] + opt['crit'] + opt['degraded']
    if opt_problems > 0:
        conclusion.append(f"⚡ Проблемы с оптикой: {opt['no_signal']} без сигнала, {opt['crit']} аварий, {opt['degraded']} деградаций.")
        if health_status == "stable": health_status = "warning"
        
    if not conclusion:
        conclusion.append("✅ Сеть работает стабильно. Критических проблем не выявлено.")
        
    return "\n".join(conclusion), health_status
