from celery import shared_task
from .ad_client import get_ad_users
from .models import AdUser
import logging

logger = logging.getLogger(__name__)

@shared_task(name="servers.sync_ad_users")
def sync_ad_users():
    """Синхронизация пользователей из AD в базу данных."""
    logger.info("Начинаем синхронизацию пользователей AD...")
    result = get_ad_users()
    
    if not result.get('ok'):
        logger.error(f"Ошибка AD: {result.get('error')}")
        return f"Ошибка: {result.get('error')}"

    users = result.get('users', [])
    updated_count = 0
    created_count = 0

    for u in users:
        login = u.get('login')
        if not login:
            continue
            
        obj, created = AdUser.objects.update_or_create(
            login=login,
            defaults={
                'name': u.get('name') or '',
                'email': u.get('email') or None,
                'is_active': u.get('is_active', True),
            }
        )
        if created:
            created_count += 1
        else:
            updated_count += 1

    logger.info(f"AD Sync: создано {created_count}, обновлено {updated_count}")
    return f"Создано: {created_count}, обновлено: {updated_count}"
