"""
Привязка Telegram → пользователь Cerberus.

Токен привязки: 32 символа secrets.token_urlsafe (~190 бит), одноразовый, живёт 10 минут.
В базе хранится только SHA-256 токена — утечка таблицы не даёт привязать чужой аккаунт.
Токен передаётся боту deep-link'ом (t.me/<бот>?start=<токен>) или командой /link <токен>.
Попытки с неверным токеном ограничены (по Telegram ID).

Бот работает только для привязанных: без привязки — одна инструкция, никаких данных.
"""
import hashlib
import logging
import secrets

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

TOKEN_TTL_MIN = 10
MAX_FAILS = 5            # неверных попыток с одного Telegram ID…
FAIL_WINDOW = 60 * 60    # …за час


def _hash(token: str) -> str:
    return hashlib.sha256(token.strip().encode()).hexdigest()


def issue_token(user) -> str:
    """Новый токен для пользователя (старые неиспользованные удаляются). Возвращает сам токен."""
    from .models import TelegramLinkCode
    token = secrets.token_urlsafe(24)  # 32 символа [A-Za-z0-9_-] — подходит для ?start=
    TelegramLinkCode.objects.filter(user=user, used_at__isnull=True).delete()
    TelegramLinkCode.objects.create(user=user, code=_hash(token))
    return token


def _fail_key(tg_id: int) -> str:
    return f"tglink_fail:{tg_id}"


def is_blocked(tg_id: int) -> bool:
    return cache.get(_fail_key(tg_id), 0) >= MAX_FAILS


def _bump_fail(tg_id: int) -> None:
    key = _fail_key(tg_id)
    if not cache.add(key, 1, timeout=FAIL_WINDOW):
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=FAIL_WINDOW)


def consume_token(token: str, tg_id: int, tg_username: str = "", tg_first_name: str = ""):
    """Привязывает Telegram к владельцу токена. Возвращает (user | None, сообщение об ошибке)."""
    from .models import TelegramAccount, TelegramLinkCode

    if is_blocked(tg_id):
        return None, "Слишком много неверных попыток. Попробуйте через час."
    token = (token or "").strip()
    if not token or len(token) > 64:
        _bump_fail(tg_id)
        return None, "Код неверный или истёк."

    with transaction.atomic():
        code = (TelegramLinkCode.objects.select_for_update()
                .filter(code=_hash(token), used_at__isnull=True).select_related("user").first())
        if code is None or not code.is_valid() or not code.user.is_active:
            _bump_fail(tg_id)
            logger.warning("TG link: неверный/просроченный токен от tg_id=%s", tg_id)
            return None, "Код неверный или истёк."
        user = code.user
        # Один Telegram — один пользователь, один пользователь — один Telegram
        TelegramAccount.objects.filter(user=user).delete()
        TelegramAccount.objects.filter(tg_id=tg_id).delete()
        type(user).objects.filter(telegram_id=tg_id).exclude(pk=user.pk).update(telegram_id=None)
        TelegramAccount.objects.create(user=user, tg_id=tg_id, username=(tg_username or "")[:64],
                                       first_name=(tg_first_name or "")[:128])
        user.telegram_id = tg_id
        user.save(update_fields=["telegram_id"])
        code.used_at = timezone.now()
        code.save(update_fields=["used_at"])
    cache.delete(_fail_key(tg_id))
    logger.info("TG link: tg_id=%s → %s", tg_id, user.username)
    return user, ""


def bot_username() -> str:
    """Имя бота (для ссылки t.me/…). Берётся из getMe и кэшируется на сутки."""
    name = cache.get("tg_bot_username")
    if name:
        return name
    import json
    from .telegram_notify import _post
    ok, body = _post("getMe", {})
    if ok:
        try:
            name = json.loads(body)["result"]["username"]
            cache.set("tg_bot_username", name, 24 * 3600)
            return name
        except (ValueError, KeyError):
            pass
    return ""
