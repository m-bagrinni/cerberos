"""Аватар из Telegram при привязке (если у пользователя ещё нет фото)."""
import logging

import requests
from django.conf import settings
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)


def fetch_and_save_tg_avatar(user, tg_user_id: int) -> bool:
    """
    Скачивает аватарку пользователя из Telegram и сохраняет в user.avatar.
    Возвращает True при успехе.
    """
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', None)
    if not token:
        logger.warning("TG_AVATAR: TELEGRAM_BOT_TOKEN not set")
        return False

    try:
        # 1. Запрос списка фото
        url = f"https://api.telegram.org/bot{token}/getUserProfilePhotos"
        resp = requests.get(url, params={"user_id": tg_user_id, "limit": 1}, timeout=5)
        data = resp.json()
        
        if not data.get("ok") or data.get("result", {}).get("total_count", 0) == 0:
            logger.info(f"TG_AVATAR: No photos for user {tg_user_id} (or hidden)")
            return False

        # 2. Берем file_id самого большого размера (последний в списке photos[0])
        photos = data["result"]["photos"][0]
        file_id = photos[-1]["file_id"]

        # 3. Получаем file_path
        url_file = f"https://api.telegram.org/bot{token}/getFile"
        resp_file = requests.get(url_file, params={"file_id": file_id}, timeout=5)
        file_path = resp_file.json()["result"]["file_path"]

        # 4. Скачиваем
        url_download = f"https://api.telegram.org/file/bot{token}/{file_path}"
        resp_img = requests.get(url_download, timeout=10)
        
        if resp_img.status_code == 200:
            filename = f"tg_{tg_user_id}.jpg"
            user.avatar.save(filename, ContentFile(resp_img.content), save=True)
            logger.info(f"TG_AVATAR: Saved avatar for {user.username} ({tg_user_id})")
            return True
        else:
            logger.warning(f"TG_AVATAR: Download failed HTTP {resp_img.status_code}")
            return False

    except Exception as e:
        logger.error(f"TG_AVATAR: Error fetching avatar for {tg_user_id}: {e}")
        return False
