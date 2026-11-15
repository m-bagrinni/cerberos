"""
Management-команда запуска Telegram-бота Cerberus (long-polling).

Запуск:
    python manage.py run_telegram_bot
или в отдельном сервисе/терминале.
"""
import asyncio
import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Запустить Telegram-бот Cerberus (aiogram 3, long-polling)"

    def handle(self, *args, **options) -> None:
        # Импорт внутри handle — чтобы Django успел загрузить настройки/приложения
        from apps.accounts.telegram_bot import main

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logger.info("Telegram bot: stopped by user")
