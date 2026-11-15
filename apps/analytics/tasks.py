from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from .collector import collect_network_data, generate_conclusion
from .models import NetworkReport

@shared_task(name="analytics.run_network_analysis")
def run_network_analysis():
    """Собирает данные и создает отчет."""
    metrics = collect_network_data()
    conclusion, health_status = generate_conclusion(metrics)
    
    period_end = timezone.now()
    period_start = period_end - timedelta(days=2)
    
    report = NetworkReport.objects.create(
        period_start=period_start,
        period_end=period_end,
        metrics=metrics,
        conclusion=conclusion,
        health_status=health_status
    )
    
    return f"Отчет #{report.id} создан. Статус: {health_status}"
