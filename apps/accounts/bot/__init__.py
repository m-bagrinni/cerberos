"""
Telegram-бот Cerberus (aiogram 3).

Устройство:
- ui.py       — оформление экранов, клавиатуры, Screen;
- core.py     — маршруты кнопок (ключ:аргументы), права как на сайте, ввод текста;
- screens/*   — разделы: главная, алерты, устройства и порты, NE, станции, подсети и IP,
                серверы, службы, документы, уведомления, поиск.

Навигация — в одном сообщении: кнопка редактирует его. Уведомления об алертах
(apps/accounts/telegram_notify.py) приходят отдельными сообщениями с кнопками
ack:/res:/rel: — их обрабатывает on_alert_button и меняет только клавиатуру.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (BotCommand, CallbackQuery, FSInputFile, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)
from asgiref.sync import sync_to_async
from django.conf import settings

from . import core
from .screens import alerts, devices, edocs, home, infra, me, mux, stations  # noqa: F401 — регистрируют маршруты
from .ui import Notice, Screen

logger = logging.getLogger(__name__)
router = Router(name="cerberus")

_db = lambda fn: sync_to_async(fn, thread_sensitive=True)  # noqa: E731

COMMANDS = [
    ("menu", "Главный экран"),
    ("alerts", "Активные алерты"),
    ("mine", "Мои алерты в работе"),
    ("stations", "Станции"),
    ("docs", "Документы"),
    ("find", "Поиск: IP, устройство, станция"),
    ("settings", "Уведомления"),
    ("help", "Помощь"),
]
COMMAND_ROUTES = {
    "menu": ("home", []), "alerts": ("al.l", ["all", "0"]), "mine": ("al.l", ["mine", "0"]),
    "stations": ("st.l", ["all", "0"]), "docs": ("dc.h", []), "settings": ("me.s", []),
    "help": ("help", []), "find": ("find", []),
}


# ── Показ экрана ──────────────────────────────────────────────────────────────
async def _send(message: Message, scr: Screen) -> None:
    if scr.file:
        path, name, caption = scr.file
        await message.answer_document(FSInputFile(path, filename=name), caption=caption, parse_mode="HTML")
        return
    await message.answer(scr.text, reply_markup=scr.kb.markup() if scr.kb else None,
                         parse_mode="HTML", disable_web_page_preview=True)


async def _show(call: CallbackQuery, scr: Screen) -> None:
    if scr.file:
        await call.answer(scr.toast or None)
        await _send(call.message, scr)
        return
    markup = scr.kb.markup() if scr.kb else None
    try:
        if scr.new or not call.message.text:
            await _send(call.message, scr)
        else:
            await call.message.edit_text(scr.text, reply_markup=markup, parse_mode="HTML",
                                         disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            if "message can't be edited" in str(e) or "message to edit not found" in str(e):
                await _send(call.message, scr)
            else:
                raise
    await call.answer(scr.toast or None)


# ── Команды ───────────────────────────────────────────────────────────────────
async def _do_link(message: Message, token: str) -> None:
    from apps.accounts.telegram_link import consume_token
    from .avatar import fetch_and_save_tg_avatar
    u = message.from_user
    user, err = await _db(consume_token)(token, u.id, u.username or "", u.first_name or "")
    if user is None:
        await message.answer(f"❌ {err}\n\nПолучите новый код в профиле на сайте.", parse_mode="HTML")
        return
    try:
        await _db(fetch_and_save_tg_avatar)(user, u.id)
    except Exception:  # noqa: BLE001
        logger.warning("TG avatar: не удалось получить", exc_info=True)
    await message.answer(f"✅ Аккаунт <b>{user.get_full_name() or user.username}</b> привязан.", parse_mode="HTML")
    await _open(message, "home", [])


async def _open(message: Message, key: str, args: list) -> None:
    ctx = await _db(core.load_ctx)(message.from_user.id)
    if ctx is None:
        await _send(message, home.not_linked())
        return
    try:
        scr = await _db(core.run)(key, ctx, args)
    except Notice as n:
        await message.answer(f"⚠️ {n}")
        return
    await _send(message, scr)


@router.message(CommandStart(deep_link=True))
async def cmd_start_link(message: Message, command: CommandObject) -> None:
    """t.me/<бот>?start=<токен> — привязка в одно касание из профиля."""
    await _do_link(message, command.args or "")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await _open(message, "home", [])


@router.message(Command("link"))
async def cmd_link(message: Message, command: CommandObject) -> None:
    token = (command.args or "").strip()
    if not token:
        await message.answer("Использование: <code>/link КОД</code>\nКод — в профиле на сайте, раздел «Telegram».",
                             parse_mode="HTML")
        return
    await _do_link(message, token)


@router.message(Command("find"))
async def cmd_find(message: Message, command: CommandObject) -> None:
    q = (command.args or "").strip()
    if not q:
        return await _open(message, "find", [])
    ctx = await _db(core.load_ctx)(message.from_user.id)
    if ctx is None:
        return await _send(message, home.not_linked())
    try:
        await _send(message, await _db(me.search)(ctx, q))
    except Notice as n:
        await message.answer(f"⚠️ {n}")


@router.message(Command(*[c for c in COMMAND_ROUTES if c != "find"]))
async def cmd_route(message: Message, command: CommandObject) -> None:
    key, args = COMMAND_ROUTES[command.command]
    await _open(message, key, list(args))


# ── Кнопки под уведомлениями об алертах (ack:/res:/rel:) ──────────────────────
def _alert_button_sync(tg_id: int, action: str, alert_id: int) -> str:
    from apps.alerts import ack
    ctx = core.load_ctx(tg_id)
    if ctx is None:
        return "Сначала привяжите аккаунт Cerberus."
    if not ctx.acc.can_view("alerts"):
        return "Нет доступа к алертам."
    fn = {"ack": ack.acknowledge, "res": ack.resolve, "rel": ack.release}[action]
    _, err = fn(alert_id, ctx.user)
    return err


@router.callback_query(F.data.regexp(r"^(ack|res|rel):\d+$"))
async def on_alert_button(call: CallbackQuery) -> None:
    action, raw = call.data.split(":", 1)
    err = await _db(_alert_button_sync)(call.from_user.id, action, int(raw))
    if err:
        return await call.answer(err, show_alert=True)
    rows = [r for r in (call.message.reply_markup.inline_keyboard if call.message.reply_markup else [])
            if not any((b.callback_data or "").startswith(("ack:", "res:", "rel:", "al.c:", "al.C:")) for b in r)]
    if action == "ack":
        rows.insert(0, [InlineKeyboardButton(text="✅ Решено", callback_data=f"res:{raw}"),
                        InlineKeyboardButton(text="↩ Отказаться", callback_data=f"rel:{raw}")])
    rows.append([InlineKeyboardButton(text="📋 Подробнее", callback_data=f"al.C:{raw}")])
    try:
        await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except TelegramBadRequest:
        pass
    await call.answer({"ack": "✋ Взято в работу — остальные получат уведомление",
                       "res": "✅ Алерт закрыт. Если проблема осталась — он откроется снова",
                       "rel": "↩ Вы отказались — алерт снова без ответственного"}[action])


# ── Все остальные кнопки ──────────────────────────────────────────────────────
@router.callback_query()
async def on_button(call: CallbackQuery) -> None:
    key, _, rest = (call.data or "").partition(":")
    args = rest.split(":") if rest else []
    ctx = await _db(core.load_ctx)(call.from_user.id)
    if ctx is None:
        await _show(call, home.not_linked())
        return
    if key != "cancel":
        await _db(core.clear_wait)(ctx.tg_id)   # ушли с экрана ввода — ожидание текста снимаем
    try:
        scr = await _db(core.run)(key, ctx, args)
    except Notice as n:
        return await call.answer(str(n) or None, show_alert=bool(str(n)))
    await _show(call, scr)


# ── Текст: ответ на вопрос бота или поиск ─────────────────────────────────────
@router.message(F.text)
async def on_text(message: Message) -> None:
    if message.text.startswith("/"):
        return await message.answer("Не знаю такой команды. /help — список команд.")
    ctx = await _db(core.load_ctx)(message.from_user.id)
    if ctx is None:
        return await _send(message, home.not_linked())
    try:
        scr = await _db(core.run_input)(ctx, message.text)
    except Notice as n:
        return await message.answer(f"⚠️ {n}")
    await _send(message, scr)


@router.message()
async def on_other(message: Message) -> None:
    await message.answer("Я понимаю только текст и кнопки. /menu — главный экран.")


# ── Запуск ────────────────────────────────────────────────────────────────────
async def _watchdog_loop() -> None:
    """Сторожевой сигнал Celery (apps/core/watchdog.py): бот живёт отдельно от воркеров и beat."""
    from apps.core.watchdog import check_and_notify
    while True:
        await asyncio.sleep(120)
        try:
            await _db(check_and_notify)()
        except Exception:  # noqa: BLE001
            logger.exception("watchdog: ошибка проверки")


async def main() -> None:
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    try:
        await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMANDS])
    except Exception:  # noqa: BLE001
        logger.warning("set_my_commands не удалось", exc_info=True)
    logger.info("Cerberus Telegram bot: start polling")
    watch = asyncio.create_task(_watchdog_loop())
    try:
        await dp.start_polling(bot)
    finally:
        watch.cancel()
