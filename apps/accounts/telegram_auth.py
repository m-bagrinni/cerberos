"""
Валидация Telegram WebApp initData (HMAC-SHA256).

Алгоритм (официальный):
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

Кратко:
  1) Из initData (query-string) убрать hash.
  2) Собрать check_string: отсортированные по ключу key=value, склеенные через \n.
  3) secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
  4) computed   = HMAC_SHA256(key=secret_key, msg=check_string).hexdigest()
  5) Сравнить computed и hash (constant-time).
  6) Проверить auth_date не старше max_age_seconds.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from django.conf import settings


class InitDataError(ValueError):
    """Невалидный initData."""


def validate_init_data(init_data: str, max_age_seconds: int = 86400) -> dict:
    """
    Проверяет подпись initData и возвращает распарсенные данные (dict).
    Бросает InitDataError при любой проблеме.

    Возвращаемый dict: user (dict), auth_date (int), query_id (str|None), hash
    (уже вынут, но оставлен в исходной строке для отладки — здесь не возвращаем).
    """
    if not init_data:
        raise InitDataError("empty initData")

    bot_token = settings.TELEGRAM_BOT_TOKEN
    if not bot_token:
        raise InitDataError("TELEGRAM_BOT_TOKEN not set")

    # parse_qsl: раскодирует %XX и сохраняет порядок (порядок нам не важен — сортируем сами)
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    data = dict(pairs)

    received_hash = data.pop("hash", None)
    if not received_hash:
        raise InitDataError("hash missing")

    # check_string: key=value по алфавиту, через \n
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))

    # secret_key = HMAC(key="WebAppData", msg=bot_token)
    secret_key = hmac.new(
        key=b"WebAppData",
        msg=bot_token.encode(),
        digestmod=hashlib.sha256,
    ).digest()

    # computed = HMAC(key=secret_key, msg=check_string)
    computed = hmac.new(
        key=secret_key,
        msg=check_string.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(computed, received_hash):
        raise InitDataError("bad signature")

    # auth_date
    try:
        auth_date = int(data.get("auth_date", "0"))
    except (TypeError, ValueError):
        raise InitDataError("bad auth_date")
    if max_age_seconds and (time.time() - auth_date) > max_age_seconds:
        raise InitDataError("initData expired")

    # user — JSON-строка
    user_raw = data.get("user")
    if user_raw:
        try:
            data["user"] = json.loads(user_raw)
        except json.JSONDecodeError:
            raise InitDataError("bad user json")
    else:
        data["user"] = {}

    data["auth_date"] = auth_date
    return data
