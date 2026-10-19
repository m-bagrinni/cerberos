"""API для управления службами."""
import subprocess
import json
from django.db import connection
from django.core.cache import cache
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django_celery_beat.models import PeriodicTask

from .models import ServiceEvent


# ═══════════════════════════════════════════════════════════
# Контейнеры
# ═══════════════════════════════════════════════════════════

def _docker_ps():
    """Возвращает список контейнеров с их статусом."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}|{{.State}}"],
            capture_output=True, text=True, timeout=10,
        )
        containers = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|")
            if len(parts) < 3:
                continue
            name, status, state = parts[0], parts[1], parts[2]
            # Только наши
            if not name.startswith("cerber-"):
                continue
            containers.append({
                "name": name,
                "short_name": name.replace("cerber-", "").rstrip("-1"),
                "status": status,
                "state": state,
                "is_up": "Up" in status,
            })
        # Сортируем: сначала важные (web, worker, beat, postgres, redis)
        order = {"web": 0, "worker": 1, "beat": 2, "postgres": 3, "redis": 4}
        containers.sort(key=lambda c: order.get(c["short_name"], 99))
        return containers
    except Exception as e:
        return []


@require_GET
def containers_list(request):
    """Список всех контейнеров."""
    return JsonResponse({"containers": _docker_ps()})


@require_POST
def container_action(request, name, action):
    """Действие над контейнером (start/stop/restart)."""
    if action not in ("start", "stop", "restart"):
        return JsonResponse({"ok": False, "error": "invalid action"}, status=400)
    
    try:
        result = subprocess.run(
            ["docker", action, name],
            capture_output=True, text=True, timeout=30,
        )
        success = result.returncode == 0
        error = result.stderr.strip() if not success else ""
        
        # Логируем
        ServiceEvent.objects.create(
            target_type=ServiceEvent.TYPE_CONTAINER,
            target_name=name,
            action=action,
            success=success,
            error=error[:500],
        )
        
        return JsonResponse({
            "ok": success,
            "action": action,
            "name": name,
            "output": result.stdout.strip(),
            "error": error,
        })
    except Exception as e:
        ServiceEvent.objects.create(
            target_type=ServiceEvent.TYPE_CONTAINER,
            target_name=name,
            action=action,
            success=False,
            error=str(e),
        )
        return JsonResponse({"ok": False, "error": str(e)}, status=500)


# ═══════════════════════════════════════════════════════════
# Beat-задачи
# ═══════════════════════════════════════════════════════════

@require_GET
def tasks_list(request):
    """Список всех Beat-задач с last_run."""
    from django.utils import timezone
    
    tasks = []
    for t in PeriodicTask.objects.all().order_by("name"):
        last_age = None
        if t.last_run_at:
            last_age = (timezone.now() - t.last_run_at).total_seconds()
        
        interval_sec = None
        interval_str = None
        if t.interval:
            interval_str = str(t.interval)
            # Считаем interval в секундах
            every = t.interval.every
            period = t.interval.period
            if period == "seconds":
                interval_sec = every
            elif period == "minutes":
                interval_sec = every * 60
            elif period == "hours":
                interval_sec = every * 3600
            elif period == "days":
                interval_sec = every * 86400
        
        tasks.append({
            "id": t.id,
            "name": t.name,
            "task": t.task,
            "enabled": t.enabled,
            "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
            "last_age_sec": last_age,
            "total_run": t.total_run_count,
            "interval": interval_str,
            "interval_sec": interval_sec,
        })
    
    return JsonResponse({"tasks": tasks})


@require_POST
def task_action(request, task_id, action):
    """Включить/выключить/запустить задачу."""
    try:
        task = PeriodicTask.objects.get(pk=task_id)
    except PeriodicTask.DoesNotExist:
        return JsonResponse({"ok": False, "error": "task not found"}, status=404)
    
    if action == "enable":
        task.enabled = True
        task.save(update_fields=["enabled"])
    elif action == "disable":
        task.enabled = False
        task.save(update_fields=["enabled"])
    elif action == "run_now":
        # Отправляем в Celery
        from celery import current_app
        try:
            current_app.send_task(task.task)
            task.last_run_at = timezone.now()
            task.save(update_fields=["last_run_at"])
        except Exception as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=500)
    else:
        return JsonResponse({"ok": False, "error": "invalid action"}, status=400)
    
    ServiceEvent.objects.create(
        target_type=ServiceEvent.TYPE_TASK,
        target_name=task.name,
        action=action,
        success=True,
    )
    
    return JsonResponse({"ok": True, "action": action, "task": task.name})


# ═══════════════════════════════════════════════════════════
# Health-метрики
# ═══════════════════════════════════════════════════════════

@require_GET
def health(request):
    """Метрики здоровья системы."""
    from apps.alerts.models import Alert
    from apps.monitoring.models import MetricValue
    from django.db.models import Count
    
    # 1. DB size
    with connection.cursor() as cur:
        cur.execute("SELECT pg_database_size(current_database())")
        db_size = cur.fetchone()[0]
    
    # 2. Redis used memory
    try:
        import redis
        from urllib.parse import urlparse
        from django.conf import settings
        
        url = urlparse(settings.CELERY_BROKER_URL)
        r = redis.Redis(
            host=url.hostname or "redis",
            port=url.port or 6379,
            db=0,
            socket_timeout=2,
        )
        info = r.info("memory")
        redis_used = info.get("used_memory", 0)
        redis_peak = info.get("used_memory_peak", 0)
        queue_len = r.llen("celery")
    except Exception as e:
        print(f"[health] redis error: {e}")
        redis_used = None
        redis_peak = None
        queue_len = None
    
    # 3. Алерты
    firing = Alert.objects.filter(state=Alert.STATE_FIRING).count()
    crit = Alert.objects.filter(state=Alert.STATE_FIRING, severity=Alert.SEVERITY_CRIT).count()
    warn = Alert.objects.filter(state=Alert.STATE_FIRING, severity=Alert.SEVERITY_WARN).count()
    
    # 4. MetricValue — размер
    mv_count = MetricValue.objects.count()
    
    # 5. Postgres connections
    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM pg_stat_activity")
        pg_connections = cur.fetchone()[0]
    
    # 6. Последний опрос устройств
    from apps.devices.models import Device
    last_device_poll = Device.objects.order_by("-last_polled_at").first()
    
    return JsonResponse({
        "db_size_bytes": db_size,
        "redis_used_bytes": redis_used,
        "redis_peak_bytes": redis_peak,
        "queue_len": queue_len,
        "alerts_firing": firing,
        "alerts_crit": crit,
        "alerts_warn": warn,
        "metric_value_count": mv_count,
        "pg_connections": pg_connections,
        "last_device_poll_at": last_device_poll.last_polled_at.isoformat() if last_device_poll and last_device_poll.last_polled_at else None,
    })


@require_GET
def events_list(request):
    """Последние 50 событий."""
    events = []
    for e in ServiceEvent.objects.all()[:50]:
        events.append({
            "id": e.id,
            "target_type": e.target_type,
            "target_name": e.target_name,
            "action": e.action,
            "success": e.success,
            "created_at": e.created_at.isoformat(),
        })
    return JsonResponse({"events": events})
