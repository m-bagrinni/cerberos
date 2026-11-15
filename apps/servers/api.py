import logging
import os
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt
from .models import AdUser
from .ad_client import toggle_user_block, update_user_attributes, reset_user_password, create_user
import json
logger = logging.getLogger(__name__)

@require_GET
def ad_users_api(request):
    users = AdUser.objects.all().order_by('login')
    data = []
    for u in users:
        data.append({
            'login': u.login,
            'name': u.name,
            'email': u.email,
            'is_active': u.is_active,
        })
    return JsonResponse({'ok': True, 'users': data})

@require_POST
def ad_user_toggle_api(request, login):
    try:
        payload = json.loads(request.body)
        block = payload.get('block', True)
    except:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)
    
    result = toggle_user_block(login, block)
    if result['ok']:
        AdUser.objects.filter(login=login).update(is_active=not block)
        return JsonResponse({'ok': True})
    return JsonResponse({'ok': False, 'error': result['error']}, status=500)

@require_POST
def ad_user_update_api(request, login):
    """Обновление ФИО и email."""
    try:
        payload = json.loads(request.body)
        name = payload.get('name')
        email = payload.get('email')
    except:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)
    
    result = update_user_attributes(login, name, email)
    if result['ok']:
        AdUser.objects.filter(login=login).update(name=name, email=email)
        return JsonResponse({'ok': True})
    return JsonResponse({'ok': False, 'error': result['error']}, status=500)

@require_POST
def ad_user_reset_password_api(request, login):
    """Сброс пароля."""
    try:
        payload = json.loads(request.body)
        password = payload.get('password')
        if not password:
            return JsonResponse({'ok': False, 'error': 'Пароль не указан'}, status=400)
    except:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)
    
    result = reset_user_password(login, password)
    if result['ok']:
        return JsonResponse({'ok': True})
    return JsonResponse({'ok': False, 'error': result['error']}, status=500)

@require_POST
def ad_user_create_api(request):
    """Создание нового пользователя в AD."""
    try:
        payload = json.loads(request.body)
        login = payload.get('login')
        name = payload.get('name')
        password = payload.get('password')
        email = payload.get('email', '')
        
        if not login or not name or not password:
            return JsonResponse({'ok': False, 'error': 'Заполните все обязательные поля'}, status=400)
            
    except:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)
    
    result = create_user(login, name, password, email)
    if result['ok']:
        # Сразу синхронизируем этого пользователя в БД
        AdUser.objects.update_or_create(
            login=login,
            defaults={'name': name, 'email': email, 'is_active': True}
        )
        return JsonResponse({'ok': True})
    return JsonResponse({'ok': False, 'error': result['error']}, status=500)

@require_GET
def ad_groups_api(request):
    from .ad_client import get_ad_groups
    result = get_ad_groups()
    if result['ok']:
        return JsonResponse({'ok': True, 'groups': result['groups']})
    return JsonResponse({'ok': False, 'error': result['error']}, status=500)

@require_POST
def ad_group_add_user_api(request, group_name):
    from .ad_client import add_user_to_group
    try:
        payload = json.loads(request.body)
        login = payload.get('login')
    except:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)
    result = add_user_to_group(login, group_name)
    if result['ok']:
        return JsonResponse({'ok': True})
    return JsonResponse({'ok': False, 'error': result['error']}, status=500)

from .ad_client import get_group_members, remove_user_from_group

@require_GET
def ad_group_members_api(request, group_name):
    result = get_group_members(group_name)
    if result['ok']:
        return JsonResponse({'ok': True, 'members': result['members']})
    return JsonResponse({'ok': False, 'error': result['error']}, status=500)

@require_POST
def ad_group_remove_user_api(request, group_name):
    try:
        payload = json.loads(request.body)
        login = payload.get('login')
    except:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)
    result = remove_user_from_group(login, group_name)
    if result['ok']:
        return JsonResponse({'ok': True})
    return JsonResponse({'ok': False, 'error': result['error']}, status=500)

@require_POST
def ad_group_create_api(request):
    from .ad_client import create_group
    try:
        payload = json.loads(request.body)
        group_name = payload.get('name')
        description = payload.get('description', '')
        group_type = payload.get('type', 'global')
        if not group_name:
            return JsonResponse({'ok': False, 'error': 'Название группы обязательно'}, status=400)
    except:
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)
    
    result = create_group(group_name, description, group_type)
    if result['ok']:
        return JsonResponse({'ok': True})
    return JsonResponse({'ok': False, 'error': result['error']}, status=500)

from .models import Server




@csrf_exempt
@require_POST
def agent_report_api(request):
    """Приём метрик от агента на DC. Защищён X-Agent-Token."""
    from django.conf import settings
    from django.utils import timezone
    from .models import Server, ServerMetric

    # --- Проверка токена ---
    expected = getattr(settings, "AGENT_TOKEN", "") or ""
    provided = request.headers.get("X-Agent-Token", "")
    if not expected:
        logger.error("AGENT_TOKEN не настроен в settings — agent_report_api отклоняет запросы")
        return JsonResponse({'ok': False, 'error': 'Server misconfigured'}, status=500)
    import hmac
    if not hmac.compare_digest(provided.encode(), expected.encode()):  # без утечки по времени
        logger.warning(f"agent_report_api: неверный X-Agent-Token с {request.META.get('REMOTE_ADDR')}")
        return JsonResponse({'ok': False, 'error': 'Forbidden'}, status=403)

    try:
        payload = json.loads(request.body)
        ip_address = request.META.get('REMOTE_ADDR')

        server, _ = Server.objects.get_or_create(
            ip_address=ip_address,
            defaults={'name': payload.get('hostname', 'Unknown')}
        )

        server.last_seen = timezone.now()
        server.save()

        # Сохраняем метрики
        metrics = payload.get('metrics', [])
        for m in metrics:
            ServerMetric.objects.create(
                server=server,
                metric_key=m.get('key'),
                value=m.get('value'),
                unit=m.get('unit', '')
            )

        # Сохраняем онлайн-пользователей и последние входы в кэш-файл
        cache_dir = '/tmp/cerber_cache'
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f'server_{server.id}_users.json')

        cache_data = {
            'online_users': payload.get('online_users', []),
            'last_logins': payload.get('last_logins', []),
            'timestamp': timezone.now().isoformat(),
        }
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f)

        return JsonResponse({'ok': True, 'message': 'Данные приняты'})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)

@require_GET
def server_metrics_api(request, server_id):
    """Возвращает последние значения ключевых метрик для сервера."""
    try:
        server = Server.objects.get(id=server_id)
    except Server.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Сервер не найден'}, status=404)
    
    # Получаем последние значения для CPU, RAM, Disk
    metrics = {}
    for key in ['cpu_load', 'ram_used_pct', 'disk_used_pct']:
        latest = ServerMetric.objects.filter(
            server=server, metric_key=key
        ).order_by('-created_at').first()
        if latest:
            metrics[key] = {
                'value': latest.value,
                'unit': latest.unit,
                'created_at': latest.created_at.isoformat(),
            }
    
    # Получаем историю за последние 24 часа (для графика)
    from django.utils import timezone
    from datetime import timedelta
    since = timezone.now() - timedelta(hours=24)
    
    history = {}
    for key in ['cpu_load', 'ram_used_pct', 'disk_used_pct']:
        points = ServerMetric.objects.filter(
            server=server, metric_key=key, created_at__gte=since
        ).order_by('created_at').values_list('created_at', 'value')
        history[key] = [
            {'time': t.strftime('%H:%M'), 'value': v}
            for t, v in points
        ]
    
    return JsonResponse({'ok': True, 'metrics': metrics, 'history': history})

from django.db.models import Max
from .models import ServerMetric

@require_GET
def server_services_api(request, server_id):
    """Возвращает последние статусы служб (из метрик агента)."""
    try:
        server = Server.objects.get(id=server_id)
    except Server.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Сервер не найден'}, status=404)
    
    # Получаем последние метрики, которые начинаются с 'svc_'
    services = []
    # Ищем уникальные ключи метрик, начинающиеся с 'svc_'
    from django.db.models import Max
    svc_metrics = ServerMetric.objects.filter(
        server=server, metric_key__startswith='svc_'
    ).values('metric_key').annotate(last_time=Max('created_at'))
    
    for m in svc_metrics:
        key = m['metric_key']
        # Получаем последнее значение для этого ключа
        latest = ServerMetric.objects.filter(
            server=server, metric_key=key
        ).order_by('-created_at').first()
        if latest:
            service_name = key.replace('svc_', '')
            services.append({
                'name': service_name,
                'state': 'Running' if latest.value == 1.0 else 'Stopped',
                'last_update': latest.created_at.isoformat(),
            })
    
    # Сортируем по имени
    services.sort(key=lambda x: x['name'])
    
    return JsonResponse({'ok': True, 'services': services})

import datetime

@require_GET
def server_user_stats_api(request, server_id):
    import json
    import os
    
    try:
        server = Server.objects.get(id=server_id)
    except Server.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Сервер не найден'}, status=404)
    
    cache_dir = '/tmp/cerber_cache'
    cache_file = os.path.join(cache_dir, f'server_{server.id}_users.json')
    
    if not os.path.exists(cache_file):
        return JsonResponse({
            'ok': True,
            'online_users': [],
            'last_logins': [],
            'message': 'Агент ещё не отправлял данные'
        })
    
    try:
        with open(cache_file, 'r') as f:
            data = json.load(f)
        return JsonResponse({
            'ok': True,
            'online_users': data.get('online_users', []),
            'last_logins': data.get('last_logins', []),
        })
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=500)
