"""
Celery-приложение для проекта Cerberus.
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cerber.settings")

app = Celery("cerber")

# ============================================================
# Приоритеты задач по очередям (мгновенная реакция)
# ============================================================
app.conf.task_routes = {
    # CRITICAL — оптика + доступность + алерты
    "monitoring.poll_optical_links_fast":    {"queue": "critical"},
    "monitoring.poll_optical_links":         {"queue": "critical"},
    "monitoring.poll_ne_availability_fast":  {"queue": "critical"},
    "monitoring.check_alerts_all":           {"queue": "critical"},
    "monitoring.check_alerts_task":          {"queue": "critical"},
    "subnets.check_ip_alerts":               {"queue": "critical"},
    "alerts.escalate_unacked":               {"queue": "critical"},
    "alerts.correlate":                      {"queue": "critical"},

    # HIGH — traffic_light + ping
    "monitoring.poll_traffic_light":         {"queue": "high"},
    "monitoring.ping_all_devices":           {"queue": "high"},
    "monitoring.ping_all_devices_task":      {"queue": "high"},
    "subnets.ping_monitored_ips":            {"queue": "high"},

    # TRAFFIC — broadcast storm
    "apps.monitoring.tasks_traffic.poll_broadcast_rate":      {"queue": "traffic"},
    "apps.monitoring.tasks_traffic.poll_all_subnets_broadcast": {"queue": "traffic"},

    # LOW — SFP полный, discovery, scan, analytics
    "monitoring.poll_sfp_all":               {"queue": "low"},
    "monitoring.poll_sfp_task":              {"queue": "low"},
    "monitoring.discover_olencom_nes_all":   {"queue": "low"},
    "monitoring.discover_olencom_nes_task":  {"queue": "low"},
    "subnets.scan_all_subnets":              {"queue": "low"},
    "subnets.scan_subnet_task":              {"queue": "low"},
    "subnets.fingerprint_subnet_task":       {"queue": "low"},
    "analytics.run_network_analysis":        {"queue": "low"},
    "monitoring.poll_fdb":                   {"queue": "low"},

    # SERVERS — retention метрик (раз в сутки, 03:00)
    "servers.cleanup_metrics":               {"queue": "low"},
    # Ретенция истории мониторинга (раз в час)
    "monitoring.cleanup_old_metric_values":  {"queue": "low"},

    # Лёгкие частые задачи — не в "default": там долгие SNMP-опросы (до 3 мин) на 2 слотах
    "servers.ping_all":                      {"queue": "high"},
    # Отправка Telegram — быстро и отдельно от опросов
    "accounts.send_telegram_broadcast":      {"queue": "high"},
    "edocs.notify":                          {"queue": "high"},
    "edocs.remind_due":                      {"queue": "high"},
}

app.conf.task_default_queue = "default"

# Пульс для сторожевого сигнала (apps/core/watchdog.py): по задаче в каждую очередь раз в минуту.
# DatabaseScheduler django_celery_beat переносит эти записи в БД при старте beat.
app.conf.beat_schedule = {
    f"heartbeat-{q}": {
        "task": "core.heartbeat",
        "schedule": 60.0,
        "args": (q,),
        "options": {"queue": q, "expires": 55},
    }
    for q in ("critical", "high", "default", "low", "traffic")
}
# Напоминание о Critical, которые никто не взял в работу (apps/alerts/ack.py)
app.conf.beat_schedule["alerts-escalate-unacked"] = {
    "task": "alerts.escalate_unacked", "schedule": 300.0, "options": {"expires": 280},
}

# Корреляция алертов: придержанные уведомления и следствия без причины (apps/alerts/correlation.py)
app.conf.beat_schedule["alerts-correlate"] = {
    "task": "alerts.correlate", "schedule": 60.0, "options": {"expires": 55},
}
# FDB коммутаторов (MAC ↔ порт) — для «Подключён к …» в подсетях (apps/monitoring/fdb.py).
# Раньше задачи не было в расписании: таблица собиралась вручную и устаревала.
app.conf.beat_schedule["monitoring-poll-fdb"] = {
    "task": "monitoring.poll_fdb", "schedule": 900.0, "options": {"queue": "low", "expires": 840},
}
# СЭД: утреннее напоминание о сроках (apps/edocs/tasks.py)
from celery.schedules import crontab  # noqa: E402
app.conf.beat_schedule["edocs-remind-due"] = {
    "task": "edocs.remind_due", "schedule": crontab(hour=9, minute=0, day_of_week="mon-fri"),
    "options": {"expires": 3600},
}

app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")