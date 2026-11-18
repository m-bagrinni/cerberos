"""Совместимость: бот переехал в пакет apps/accounts/bot/."""
from apps.accounts.bot import main  # noqa: F401
from apps.accounts.bot.avatar import fetch_and_save_tg_avatar  # noqa: F401
