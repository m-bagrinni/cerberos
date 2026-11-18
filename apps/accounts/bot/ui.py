"""
Оформление бота: единый вид экранов.

Экран = заголовок + подзаголовок + блоки + клавиатура. Навигация — в одном сообщении
(кнопки редактируют его), внизу всегда «назад» и «домой».
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape as _esc

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from django.conf import settings
from django.utils import timezone

DOT = {"ok": "🟢", "warn": "🟡", "crit": "🔴", "power": "🟣", "off": "⚫", "unknown": "⚪"}
SEV_ICON = {"crit": "🔴", "power": "🟣", "warn": "🟡"}
SEV_LABEL = {"crit": "Критично", "power": "Нет питания", "warn": "Внимание"}
RULE = "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"
PAGE = 8


def esc(v) -> str:
    return _esc("" if v is None else str(v))


def cut(s, n: int) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def plural(n: int, one: str, few: str, many: str) -> str:
    n10, n100 = n % 10, n % 100
    w = one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many
    return f"{n} {w}"


def ago(dt) -> str:
    if not dt:
        return "—"
    sec = max(0, (timezone.now() - dt).total_seconds())
    if sec < 60:
        return "только что"
    if sec < 3600:
        return f"{int(sec // 60)} мин"
    if sec < 86400:
        h, m = int(sec // 3600), int(sec % 3600 // 60)
        return f"{h} ч {m} мин" if m and h < 6 else f"{h} ч"
    return f"{int(sec // 86400)} дн"


def ago_back(dt) -> str:
    """«5 мин назад» / «только что» / «—»."""
    a = ago(dt)
    return a if a in ("—", "только что") else f"{a} назад"


def when(dt, fmt: str = "%d.%m %H:%M") -> str:
    return timezone.localtime(dt).strftime(fmt) if dt else "—"


def bar(frac: float, width: int = 10) -> str:
    frac = max(0.0, min(1.0, frac or 0.0))
    n = round(frac * width)
    return "▰" * n + "▱" * (width - n)


def num(v, unit: str = "", digits: int = 1) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and not v.is_integer():
        s = f"{v:.{digits}f}"
    else:
        s = f"{v:g}"
    return f"{s}{(' ' + unit) if unit else ''}"


def header(icon: str, title: str, sub: str = "") -> str:
    s = f"{icon} <b>{esc(title)}</b>"
    if sub:
        s += f"\n<i>{sub}</i>"
    return s


def site_url(path: str = "") -> str:
    base = (getattr(settings, "CERBER_WEBAPP_URL", "") or "").rstrip("/")
    return f"{base}{path}" if base else ""


@dataclass
class Screen:
    text: str
    kb: "KB | None" = None
    toast: str = ""            # всплывающая подсказка у нажатой кнопки
    file: tuple | None = None  # (путь, имя файла, подпись) — отправить документ
    new: bool = False          # отправить новым сообщением, а не редактировать


class KB:
    def __init__(self):
        self.rows: list[list[InlineKeyboardButton]] = []

    @staticmethod
    def b(text: str, cb: str) -> InlineKeyboardButton:
        assert len(cb.encode()) <= 64, cb
        return InlineKeyboardButton(text=text, callback_data=cb)

    @staticmethod
    def url(text: str, url: str) -> InlineKeyboardButton | None:
        if not url:
            return None
        if url.startswith("https://"):
            return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))
        return InlineKeyboardButton(text=text, url=url)

    def row(self, *btns) -> "KB":
        btns = [x for x in btns if x is not None]
        if btns:
            self.rows.append(list(btns))
        return self

    def grid(self, btns, cols: int = 2) -> "KB":
        btns = [x for x in btns if x is not None]
        for i in range(0, len(btns), cols):
            self.rows.append(btns[i:i + cols])
        return self

    def pager(self, key: str, page: int, pages: int, *args) -> "KB":
        if pages <= 1:
            return self
        pre = ":".join(str(a) for a in args)
        pre = f"{pre}:" if pre else ""
        row = [self.b("◀", f"{key}:{pre}{page - 1}") if page > 0 else self.b("·", "noop"),
               self.b(f"{page + 1} / {pages}", "noop"),
               self.b("▶", f"{key}:{pre}{page + 1}") if page < pages - 1 else self.b("·", "noop")]
        self.rows.append(row)
        return self

    def nav(self, back: str | None = None, refresh: str | None = None) -> "KB":
        row = []
        if back:
            row.append(self.b("⬅ Назад", back))
        if refresh:
            row.append(self.b("🔄", refresh))
        row.append(self.b("🏠 Меню", "home"))
        self.rows.append(row)
        return self

    def markup(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=self.rows)


def paginate(items, page: int, size: int = PAGE):
    items = list(items)
    pages = max(1, (len(items) + size - 1) // size)
    page = max(0, min(int(page or 0), pages - 1))
    return items[page * size:(page + 1) * size], page, pages


class Notice(Exception):
    """Сообщение пользователю во всплывающем окне (ошибка, нет прав и т.п.)."""
