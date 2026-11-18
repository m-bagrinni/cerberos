"""
Каркас бота: маршруты кнопок, права, ввод текста.

Кнопка = callback_data «ключ:арг1:арг2». Ключ ищется в ROUTES; там же раздел и уровень
права — те же, что на сайте (apps/accounts/access.py). Экранные функции синхронные
(Django ORM), вызываются в потоке и возвращают ui.Screen.

Ввод текста (комментарий, поиск): экран вызывает ask(); следующее текстовое сообщение
пользователя уходит в обработчик INPUTS[ключ]. Без ожидания текст — это поиск.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from django.core.cache import cache

from apps.accounts.access import EDIT, VIEW

from .ui import KB, Notice, Screen

logger = logging.getLogger(__name__)

WAIT_TTL = 15 * 60


@dataclass
class Ctx:
    user: object
    acc: object
    tg_id: int


@dataclass
class Route:
    fn: Callable
    section: str | None
    level: int
    cap: str | None


ROUTES: dict[str, Route] = {}
INPUTS: dict[str, Route] = {}


def route(key: str, section: str | None = None, edit: bool = False, cap: str | None = None):
    def deco(fn):
        ROUTES[key] = Route(fn, section, EDIT if edit else VIEW, cap)
        return fn
    return deco


def on_input(key: str, section: str | None = None, edit: bool = False, cap: str | None = None):
    def deco(fn):
        INPUTS[key] = Route(fn, section, EDIT if edit else VIEW, cap)
        return fn
    return deco


def allowed(r: Route, acc) -> bool:
    if r.cap and not acc.has_cap(r.cap):
        return False
    if r.section:
        return acc.can_edit(r.section) if r.level == EDIT else acc.can_view(r.section)
    return True


def deny_text(r: Route) -> str:
    if r.cap:
        return "Нет права на это действие."
    return "Нет права на изменение в этом разделе." if r.level == EDIT else "Нет доступа к этому разделу."


def load_ctx(tg_id: int) -> Ctx | None:
    """Пользователь по Telegram ID; None — не привязан или отключён."""
    from apps.accounts.access import Access
    from apps.accounts.models import TelegramAccount
    ta = (TelegramAccount.objects.select_related("user")
          .filter(tg_id=tg_id, is_active=True, user__is_active=True).first())
    if ta is None:
        return None
    return Ctx(user=ta.user, acc=Access.for_user(ta.user), tg_id=tg_id)


def run(key: str, ctx: Ctx, args: list[str]) -> Screen:
    r = ROUTES.get(key)
    if r is None:
        raise Notice("Кнопка устарела — откройте меню заново.")
    if not allowed(r, ctx.acc):
        raise Notice(deny_text(r))
    try:
        return r.fn(ctx, *args)
    except Notice:
        raise
    except (TypeError, ValueError):
        logger.exception("bot route %s %s", key, args)
        raise Notice("Кнопка устарела — откройте меню заново.")


# ── Ввод текста ───────────────────────────────────────────────────────────────
def _wait_key(tg_id: int) -> str:
    return f"tgbot:wait:{tg_id}"


def ask(ctx: Ctx, input_key: str, args: list, text: str, cancel: str, extra: KB | None = None) -> Screen:
    """Экран-приглашение: следующее сообщение пользователя уйдёт в INPUTS[input_key]."""
    cache.set(_wait_key(ctx.tg_id), {"key": input_key, "args": [str(a) for a in args]}, WAIT_TTL)
    kb = extra or KB()
    kb.row(KB.b("✖ Отмена", f"cancel:{cancel}"))
    return Screen(text + "\n\n<i>✍️ Напишите ответ сообщением.</i>", kb)


def pop_wait(tg_id: int) -> dict | None:
    data = cache.get(_wait_key(tg_id))
    if data:
        cache.delete(_wait_key(tg_id))
    return data


def clear_wait(tg_id: int) -> None:
    cache.delete(_wait_key(tg_id))


def run_input(ctx: Ctx, text: str) -> Screen:
    wait = pop_wait(ctx.tg_id)
    if wait and wait.get("key") in INPUTS:
        r = INPUTS[wait["key"]]
        if not allowed(r, ctx.acc):
            raise Notice(deny_text(r))
        return r.fn(ctx, text, *wait.get("args", []))
    return INPUTS["search"].fn(ctx, text)


@route("cancel")
def _cancel(ctx: Ctx, *back) -> Screen:
    clear_wait(ctx.tg_id)
    key = back[0] if back else "home"
    return run(key, ctx, list(back[1:]))


@route("noop")
def _noop(ctx: Ctx, *a) -> Screen:
    raise Notice("")
