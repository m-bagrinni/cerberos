"""
Telegram-бот Cerberus (aiogram 3).
Открывает WebApp + просмотр устройств, мультиплексоров (NE), алертов.
"""
from __future__ import annotations
import logging
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery, WebAppInfo,
)
import asyncio
from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)
router = Router(name="cerberus")

# ── Клавиатуры ─────────────────────────────────────────────────
def _main_kb(is_linked=False, acc=None) -> InlineKeyboardMarkup:
    """Главное меню. Кнопки — только по правам пользователя (acc: apps.accounts.access.Access).
    Без привязки — только «Привязать аккаунт»: данные сети боту без входа не показываем."""
    base = (settings.CERBER_WEBAPP_URL or "").rstrip("/")
    buttons = []
    if not is_linked or acc is None:
        buttons.append([InlineKeyboardButton(text="🔗 Привязать аккаунт", callback_data="link_help")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    can = lambda key: acc.can_view(key)  # noqa: E731

    if base.startswith("https://"):
        if can("stations"):
            buttons.append([InlineKeyboardButton(text="🌐 Диспетчерская", web_app=WebAppInfo(url=f"{base}/local/stations/"))])
        if can("alerts"):
            buttons.append([InlineKeyboardButton(text="🚨 Алерты", web_app=WebAppInfo(url=f"{base}/alerts/"))])
    elif base:
        if can("stations"):
            buttons.append([InlineKeyboardButton(text="🌐 Диспетчерская", url=f"{base}/local/stations/")])
        if can("alerts"):
            buttons.append([InlineKeyboardButton(text="🚨 Алерты", url=f"{base}/alerts/")])

    row = []
    if can("devices"):
        row.append(InlineKeyboardButton(text="📊 Устройства", callback_data="dev_list:0"))
    if can("mux"):
        row.append(InlineKeyboardButton(text="🔌 Оптика (NE)", callback_data="ne_gateways"))
    if row:
        buttons.append(row)
    if not base and any(can(k) for k in ("devices", "mux", "subnets")):
        buttons.append([InlineKeyboardButton(text="🚨 Активные алерты", callback_data="alerts_list")])
    if not buttons:
        buttons.append([InlineKeyboardButton(text="ℹ️ Нет доступа к разделам — обратитесь к администратору", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ── Права: тот же доступ, что на сайте (apps/accounts/access.py) ───────────────
def _access_for_tg_sync(tg_id: int):
    """Access пользователя по Telegram ID или None (не привязан / отключён)."""
    from .access import Access
    from .models import TelegramAccount
    acc_row = (TelegramAccount.objects.filter(tg_id=tg_id, is_active=True, user__is_active=True)
               .select_related("user").first())
    return Access.for_user(acc_row.user) if acc_row else None


# callback_data → раздел, на который нужен «Просмотр»
_CB_SECTIONS = (("dev_", "devices"), ("ne_", "mux"), ("ack:", "alerts"), ("res:", "alerts"), ("rel:", "alerts"))
_CB_OPEN = ("main_menu", "link_help")


async def _cb_access_middleware(handler, event, data):
    """Проверка прав для всех кнопок бота. Без привязки — только меню и подсказка."""
    cb = event.data or ""
    acc = await sync_to_async(_access_for_tg_sync, thread_sensitive=True)(event.from_user.id)
    data["acc"] = acc
    if cb in _CB_OPEN:
        return await handler(event, data)
    if acc is None:
        await event.answer("Сначала привяжите аккаунт Cerberus.", show_alert=True)
        return None
    for prefix, section in _CB_SECTIONS:
        if cb.startswith(prefix) and not acc.can_view(section):
            await event.answer("Нет доступа к этому разделу.", show_alert=True)
            return None
    return await handler(event, data)


router.callback_query.outer_middleware(_cb_access_middleware)


# ── Хелперы для получения данных (sync) ─────────────────────────
def _is_linked_sync(tg_id: int) -> bool:
    from .models import TelegramAccount
    return TelegramAccount.objects.filter(tg_id=tg_id, is_active=True).exists()

def _get_devices_sync(offset: int = 0, limit: int = 5) -> dict:
    from apps.devices.models import Device
    qs = Device.objects.all().order_by("name")
    total = qs.count()
    devices = list(qs[offset:offset+limit])
    return {"total": total, "devices": devices, "offset": offset}

def _get_device_detail_sync(device_id: int) -> dict:
    """Карточка устройства: статус, uptime, CPU/RAM/Temp, SFP, алерты, интерфейсы."""
    from django.db.models import Q
    from apps.devices.models import Device
    from apps.monitoring.models import MetricValue
    from apps.alerts.models import Alert

    dev = Device.objects.select_related("vendor").filter(pk=device_id).first()
    if not dev:
        return {}

    # Последние уникальные метрики по key (самые свежие)
    qs = MetricValue.objects.filter(device=dev).select_related("metric").order_by("-ts")[:80]
    seen = {}
    for mv in qs:
        if not mv.metric:
            continue
        key = mv.metric.key
        if key in seen:
            continue
        seen[key] = {
            "key": key,
            "name": mv.metric.name,
            "value": mv.value,
            "unit": getattr(mv.metric, "unit", "") or "",
            "instance": mv.instance or "",
        }
        if len(seen) >= 20:
            break

    # firing-алерты: напрямую по устройству + по его NE (gateway=dev)
    alerts = list(
        Alert.objects.filter(state=Alert.STATE_FIRING)
        .filter(Q(device=dev) | Q(ne_device__gateway=dev))
        .order_by("-started_at")[:10]
    )

    # физические интерфейсы (для кнопки «Интерфейсы»)
    ifaces = list(
        dev.interfaces.filter(if_type__in=(6, 62, 71, 117), if_index__lt=150)
        .order_by("if_index")[:48]
    )
    iface_list = [
        {
            "if_index": i.if_index,
            "name": i.name or f"if{i.if_index}",
            "descr": i.descr,
            "speed_mbps": i.speed_mbps,
            "oper": i.oper_status,
            "admin": i.admin_status,
        }
        for i in ifaces
    ]

    return {
        "id": dev.id,
        "name": dev.name,
        "ip": dev.ip,
        "vendor": dev.vendor.name if dev.vendor else "—",
        "model": dev.ent_model or "—",
        "status": dev.status,
        "last_seen": dev.last_seen,
        "last_polled_at": dev.last_polled_at,
        "metrics": list(seen.values()),
        "alerts": alerts,
        "interfaces": iface_list,
    }


def _get_device_interfaces_sync(device_id: int) -> dict:
    """Список физических интерфейсов с up/down."""
    from apps.devices.models import Device
    dev = Device.objects.filter(pk=device_id).first()
    if not dev:
        return {}
    ifaces = list(
        dev.interfaces.filter(if_type__in=(6, 62, 71, 117), if_index__lt=150)
        .order_by("if_index")
    )
    return {
        "id": dev.id,
        "name": dev.name,
        "interfaces": [
            {
                "if_index": i.if_index,
                "name": i.name or f"if{i.if_index}",
                "alias": i.alias or "",
                "descr": i.descr,
                "speed_mbps": i.speed_mbps,
                "oper": i.oper_status,
                "admin": i.admin_status,
            }
            for i in ifaces
        ],
    }

def _get_ne_gateways_sync() -> list:
    """Возвращает список NE-шлюзов (Device, у которых есть NeDevice)."""
    from apps.devices.models import NeDevice
    # Берем уникальные gateway (Device)
    gateways = NeDevice.objects.values_list('gateway', flat=True).distinct()
    from apps.devices.models import Device
    return list(Device.objects.filter(id__in=gateways).order_by("name"))

def _get_nes_sync(gateway_id: int, offset: int = 0, limit: int = 5) -> dict:
    from apps.devices.models import NeDevice
    qs = NeDevice.objects.filter(gateway_id=gateway_id).order_by("ne_id")
    total = qs.count()
    nes = list(qs[offset:offset+limit])
    return {"total": total, "nes": nes, "offset": offset, "gateway_id": gateway_id}

def _get_ne_detail_sync(ne_id: int) -> dict:
    """Возвращает последние значения DDM для всех портов (1-6)."""
    from apps.devices.models import NeDevice
    from apps.monitoring.models import MetricValue
    ne = NeDevice.objects.filter(pk=ne_id).select_related("gateway").first()
    if not ne: return {}
    
    ports_data = {}
    keys = ["sfp_rx_dbm", "sfp_tx_dbm", "sfp_temp", "sfp_bias"]
    
    # Проходим по портам 1..6
    for instance in range(1, 7):
        ports_data[instance] = {}
        for key in keys:
            # Ищем метрику для конкретного порта (instance)
            mv = MetricValue.objects.filter(
                ne_device=ne, metric__key=key, instance=instance
            ).order_by("-ts").first()
            if mv:
                ports_data[instance][key] = mv.value
                
    return {
        "ne_id": ne.ne_id,
        "display_name": ne.display_name,
        "gateway_id": ne.gateway_id,
        "gateway_name": ne.gateway.name,
        "ports": ports_data,
    }

def _get_active_alerts_sync(acc=None) -> list:
    """Активные алерты только тех разделов, что пользователь может смотреть."""
    from django.db.models import Q
    from apps.alerts.models import Alert
    qs = Alert.objects.filter(state=Alert.STATE_FIRING).select_related("ip_address", "device", "ne_device")
    if acc is not None and not acc.superuser:
        q = Q(pk__in=[])
        if acc.can_view("subnets"):
            q |= Q(ip_address__isnull=False)
        if acc.can_view("mux"):
            q |= Q(ip_address__isnull=True, ne_device__isnull=False)
        if acc.can_view("devices"):
            q |= Q(ip_address__isnull=True, ne_device__isnull=True)
        qs = qs.filter(q)
    return list(qs.order_by("-started_at")[:10])


# ── Хендлеры команд ───────────────────────────────────────────
NOT_LINKED_TEXT = (
    "🔒 <b>Cerberus</b> — бот только для сотрудников.\n\n"
    "Чтобы начать работу, привяжите аккаунт:\n"
    "1. Войдите на сайт Cerberus → <b>Профиль</b>.\n"
    "2. В блоке «Telegram» нажмите <b>«Привязать Telegram»</b>.\n"
    "3. Нажмите «Открыть бота» — привязка пройдёт автоматически."
)


async def _do_link(message: Message, token: str) -> None:
    from .telegram_link import consume_token
    u = message.from_user
    user, err = await sync_to_async(consume_token, thread_sensitive=True)(
        token, u.id, u.username or "", u.first_name or "")
    if user is None:
        await message.answer(f"❌ {err}", parse_mode="HTML")
        return
    try:
        await sync_to_async(fetch_and_save_tg_avatar, thread_sensitive=True)(user, u.id)
    except Exception as e:
        logger.warning("TG_AVATAR: Failed to fetch avatar during link: %s", e)
    acc = await sync_to_async(_access_for_tg_sync, thread_sensitive=True)(u.id)
    await message.answer(
        f"✅ Аккаунт <b>{user.get_full_name() or user.username}</b> привязан.\nТеперь бот работает для вас.",
        reply_markup=_main_kb(acc is not None, acc), parse_mode="HTML")


@router.message(CommandStart(deep_link=True))
async def cmd_start_link(message: Message, command: CommandObject) -> None:
    """t.me/<бот>?start=<токен> — привязка в одно касание из профиля."""
    await _do_link(message, command.args or "")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    acc = await sync_to_async(_access_for_tg_sync, thread_sensitive=True)(message.from_user.id)
    if acc is None:
        await message.answer(NOT_LINKED_TEXT, parse_mode="HTML")
        return
    await message.answer("👋 <b>Cerberus</b> — мониторинг сети.\n\nВыберите действие:",
                         reply_markup=_main_kb(True, acc), parse_mode="HTML")


@router.message(Command("link"))
async def cmd_link(message: Message, command: CommandObject) -> None:
    token = (command.args or "").strip()
    if not token:
        await message.answer("Использование: <code>/link КОД</code>\nКод — в профиле на сайте, блок «Telegram».",
                             parse_mode="HTML")
        return
    await _do_link(message, token)


# ── Устройства ────────────────────────────────────────────────
@router.callback_query(F.data.startswith("dev_list:"))
async def cb_devices_list(call: CallbackQuery, acc=None) -> None:
    offset = int(call.data.split(":")[1])
    data = await sync_to_async(_get_devices_sync, thread_sensitive=True)(offset, 5)
    if not data["devices"]:
        await call.message.edit_text("Устройства не найдены.", reply_markup=_main_kb(True, acc))
        return await call.answer()

    kb = []
    for d in data["devices"]:
        kb.append([InlineKeyboardButton(text=f"🔹 {d.name} ({d.ip})", callback_data=f"dev_info:{d.id}")])
    
    nav = []
    if offset > 0: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"dev_list:{max(0, offset-5)}"))
    if offset + 5 < data["total"]: nav.append(InlineKeyboardButton(text="➡️", callback_data=f"dev_list:{offset+5}"))
    if nav: kb.append(nav)
    kb.append([InlineKeyboardButton(text="🏠 В меню", callback_data="main_menu")])

    text = f"📊 <b>Устройства</b> ({data['total']}):"
    try:
        await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    except Exception as e:
        if "message is not modified" in str(e):
            await call.answer("Сообщение не изменилось", show_alert=False)
        else:
            raise
    await call.answer()

def _fmt_uptime(sec) -> str:
    try:
        sec = int(float(sec))
    except Exception:
        return "—"
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


@router.callback_query(F.data.startswith("dev_info:"))
async def cb_device_info(call: CallbackQuery) -> None:
    from html import escape
    d = await sync_to_async(_get_device_detail_sync, thread_sensitive=True)(int(call.data.split(":")[1]))
    if not d:
        return await call.answer("Устройство не найдено", show_alert=True)

    status_map = {"ok": "🟢 Доступно", "unreachable": "🔴 Недоступно"}
    status = status_map.get(d.get("status") or "", "⚪ Неизвестно")
    polled = d["last_polled_at"].strftime("%H:%M") if d.get("last_polled_at") else "—"

    # uptime из sys_uptime (в секундах)
    uptime = "—"
    for m in d["metrics"]:
        if m["key"] == "sys_uptime" and m["value"] is not None:
            uptime = _fmt_uptime(m["value"])
            break

    lines = [
        f"📊 <b>{escape(d['name'])}</b>",
        f"IP: <code>{escape(d['ip'])}</code>",
        f"{escape(d['vendor'])} · {escape(d['model'])}",
        f"{status} · опрос: {polled} · uptime: {uptime}",
        "",
    ]

    # CPU
    cpu_parts = []
    for k, label in (("cpu_5s", "5s"), ("cpu_1m", "1m"), ("cpu_5m", "5m")):
        mm = next((m for m in d["metrics"] if m["key"] == k), None)
        if mm and mm["value"] is not None:
            cpu_parts.append(f"{mm['value']:g}% ({label})")
    if cpu_parts:
        lines.append(f"🖥 CPU: {' · '.join(cpu_parts)}")

    # RAM (instance 1.1)
    mu = next((m for m in d["metrics"] if m["key"] == "mem_dram_used" and m["instance"] == "1.1"), None)
    mt = next((m for m in d["metrics"] if m["key"] == "mem_dram_total" and m["instance"] == "1.1"), None)
    if mu and mt and mt["value"]:
        pct = mu["value"] / mt["value"] * 100
        lines.append(f"💾 RAM: {mu['value']/1024:.0f} MB / {mt['value']/1024:.0f} MB ({pct:.0f}%)")

    # Temp
    tm = next((m for m in d["metrics"] if m["key"] == "temp_current"), None)
    if tm and tm["value"] is not None:
        lines.append(f"🌡 Temp: {tm['value']:g}°C")

    # SFP по портам (instance)
    sfp = {}
    for m in d["metrics"]:
        if m["key"] in ("sfp_rx_dbm", "sfp_tx_dbm", "sfp_temp") and m["instance"]:
            sfp.setdefault(m["instance"], {})[m["key"]] = m["value"]
    for port, vals in sorted(sfp.items(), key=lambda kv: (len(kv[0]), kv[0])):
        parts = []
        if vals.get("sfp_rx_dbm") is not None: parts.append(f"RX {vals['sfp_rx_dbm']:g} dBm")
        if vals.get("sfp_tx_dbm") is not None: parts.append(f"TX {vals['sfp_tx_dbm']:g} dBm")
        if vals.get("sfp_temp") is not None: parts.append(f"{vals['sfp_temp']:g}°C")
        if parts:
            lines.append(f"🔌 SFP {port}: {' · '.join(parts)}")

    if d["alerts"]:
        lines.append("")
        lines.append(f"🚨 <b>Алерты: {len(d['alerts'])}</b>")
        for a in d["alerts"][:3]:
            icon = "🔴" if a.severity == "crit" else "⚠️"
            if a.value is not None:
                lines.append(f"  {icon} {escape(a.metric_key)} = {a.value:g}")
            else:
                lines.append(f"  {icon} {escape(a.metric_key)}")
        if len(d["alerts"]) > 3:
            lines.append(f"  … и ещё {len(d['alerts']) - 3}")

    kb = []
    if d["alerts"] or d["interfaces"]:
        row = []
        if d["interfaces"]:
            row.append(InlineKeyboardButton(text="🖧 Интерфейсы", callback_data=f"dev_ifaces:{d['id']}"))
        if d["alerts"]:
            row.append(InlineKeyboardButton(text="🚨 Алерты", callback_data=f"dev_alerts:{d['id']}"))
        if row:
            kb.append(row)
    kb.append([InlineKeyboardButton(text="🔄 Обновить", callback_data=f"dev_info:{d['id']}")])
    kb.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data="dev_list:0"),
        InlineKeyboardButton(text="🏠 В меню", callback_data="main_menu"),
    ])

    try:
        await call.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    except Exception as e:
        if "message is not modified" in str(e):
            await call.answer("Сообщение не изменилось", show_alert=False)
        else:
            raise
    await call.answer()

# ── Мультиплексоры (NE) ───────────────────────────────────────
@router.callback_query(F.data == "ne_gateways")
async def cb_ne_gateways(call: CallbackQuery) -> None:
    gateways = await sync_to_async(_get_ne_gateways_sync, thread_sensitive=True)()
    kb = []
    for g in gateways:
        kb.append([InlineKeyboardButton(text=f"📡 {g.name} ({g.ip})", callback_data=f"ne_list:{g.id}:0")])
    kb.append([InlineKeyboardButton(text="🏠 В меню", callback_data="main_menu")])
    
    text = "🔌 <b>Мультиплексоры (шлюзы)</b>\nВыберите шлюз для просмотра NE:"
    try:
        await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    except Exception as e:
        if "message is not modified" in str(e):
            await call.answer("Сообщение не изменилось", show_alert=False)
        else:
            raise
    await call.answer()

@router.callback_query(F.data.startswith("ne_list:"))
async def cb_ne_list(call: CallbackQuery) -> None:
    _, gw_id, offset_str = call.data.split(":")
    offset = int(offset_str)
    data = await sync_to_async(_get_nes_sync, thread_sensitive=True)(int(gw_id), offset, 5)
    
    kb = []
    for ne in data["nes"]:
        kb.append([InlineKeyboardButton(text=f"🔹 NE-{ne.ne_id} {ne.display_name}", callback_data=f"ne_info:{ne.id}")])
        
    nav = []
    if offset > 0: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"ne_list:{gw_id}:{max(0, offset-5)}"))
    if offset + 5 < data["total"]: nav.append(InlineKeyboardButton(text="➡️", callback_data=f"ne_list:{gw_id}:{offset+5}"))
    if nav: kb.append(nav)
    kb.append([InlineKeyboardButton(text="🔙 К шлюзам", callback_data="ne_gateways")])

    text = f"📡 <b>Список NE</b> (всего {data['total']}):"
    try:
        await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    except Exception as e:
        if "message is not modified" in str(e):
            await call.answer("Сообщение не изменилось", show_alert=False)
        else:
            raise
    await call.answer()

@router.callback_query(F.data.startswith("ne_info:"))
async def cb_ne_info(call: CallbackQuery) -> None:
    ne_id = int(call.data.split(":")[1])
    try:
        d = await sync_to_async(_get_ne_detail_sync, thread_sensitive=True)(ne_id)
    except Exception as e:
        logger.error(f"Ошибка при получении данных NE {ne_id}: {e}", exc_info=True)
        return await call.answer(f"Ошибка БД: {e}", show_alert=True)

    if not d:
        return await call.answer("NE не найдено в БД", show_alert=True)

    def fmt(val, unit=""):
        return f"{val:.2f}{unit}" if isinstance(val, (int, float)) else "—"

    lines = [
        f"📡 <b>NE-{d['ne_id']}</b> ({d['display_name']})",
        f"<i>Шлюз: {d['gateway_name']}</i>",
        ""
    ]

    has_data = False
    for port, m in d["ports"].items():
        if not m:
            continue
        has_data = True
        
        rx = m.get("sfp_rx_dbm")
        tx = m.get("sfp_tx_dbm")
        temp = m.get("sfp_temp")
        bias = m.get("sfp_bias")
        
        # Статус по RX
        icon = "⚪"
        if rx is not None:
            if rx > -10: icon = "🟢"
            elif rx > -20: icon = "🟡"
            else: icon = "🔴"

        lines.append(f"{icon} <b>Порт {port}</b>")
        lines.append(f"   📉 RX: <code>{fmt(rx, ' dBm')}</code> | 📈 TX: <code>{fmt(tx, ' dBm')}</code>")
        lines.append(f"   🌡 Temp: <code>{fmt(temp, '°C')}</code> | ⚡ Bias: <code>{fmt(bias, ' mA')}</code>")
        lines.append("")

    if not has_data:
        lines.append("<i>Нет данных DDM для этого NE.</i>")

    text = chr(10).join(lines)

    kb = [
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"ne_info:{ne_id}")],
        [InlineKeyboardButton(text="🔙 К списку", callback_data=f"ne_list:{d['gateway_id']}:0"), InlineKeyboardButton(text="🏠 В меню", callback_data="main_menu")],
    ]
    try:
        await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    except Exception as e:
        if "message is not modified" in str(e):
            await call.answer("Сообщение не изменилось", show_alert=False)
        else:
            raise
    await call.answer()

def _alert_action_sync(tg_id: int, action: str, alert_id: int):
    from .models import User
    from apps.alerts import ack
    user = User.objects.filter(telegram_id=tg_id, is_active=True).first()
    if user is None:
        return "Сначала привяжите аккаунт Cerberus."
    fn = {"ack": ack.acknowledge, "res": ack.resolve, "rel": ack.release}[action]
    _, err = fn(alert_id, user)
    return err


def _strip_alert_rows(kb):
    """Убрать кнопки действий с алертом, оставить ссылку на сайт."""
    if not kb:
        return []
    return [r for r in kb.inline_keyboard
            if not any((b.callback_data or "").startswith(("ack:", "res:", "rel:")) for b in r)]


@router.callback_query(F.data.regexp(r"^(ack|res|rel):\d+$"))
async def cb_alert_action(call: CallbackQuery) -> None:
    action, raw_id = call.data.split(":", 1)
    alert_id = int(raw_id)
    err = await sync_to_async(_alert_action_sync, thread_sensitive=True)(call.from_user.id, action, alert_id)
    if err:
        return await call.answer(err, show_alert=True)
    rows = _strip_alert_rows(call.message.reply_markup)
    if action == "ack":
        # Взял — теперь у себя кнопки «Решено» / «Отказаться»
        rows.insert(0, [InlineKeyboardButton(text="✅ Решено", callback_data=f"res:{alert_id}"),
                        InlineKeyboardButton(text="↩ Отказаться", callback_data=f"rel:{alert_id}")])
    try:
        await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)
    except Exception:  # noqa: BLE001
        pass
    await call.answer({"ack": "✋ Взято в работу. Остальные получат уведомление.",
                       "res": "✅ Алерт закрыт. Если проблема осталась — он откроется снова.",
                       "rel": "↩ Вы отказались — алерт снова без ответственного."}[action])


@router.callback_query(F.data == "alerts_list")
async def cb_alerts_list(call: CallbackQuery, acc=None) -> None:
    alerts = await sync_to_async(_get_active_alerts_sync, thread_sensitive=True)(acc)
    if not alerts:
        await call.message.edit_text("✅ <b>Активных алертов нет.</b>", reply_markup=_main_kb(True, acc), parse_mode="HTML")
        return await call.answer()

    lines = ["🚨 <b>Активные алерты</b>\n"]
    for a in alerts:
        icon = "🔴" if a.severity == "crit" else "🟡"
        target = a.ip_address.ip if a.ip_address else (a.device.name if a.device else (a.ne_device.display_name if a.ne_device else "?"))
        lines.append(f"{icon} <b>{target}</b>: {a.metric_key} = {a.value}")
    
    kb = [[InlineKeyboardButton(text="🔄 Обновить", callback_data="alerts_list")], [InlineKeyboardButton(text="🏠 В меню", callback_data="main_menu")]]
    await call.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("dev_ifaces:"))
async def cb_device_ifaces(call: CallbackQuery) -> None:
    from html import escape
    dev_id = int(call.data.split(":")[1])
    d = await sync_to_async(_get_device_interfaces_sync, thread_sensitive=True)(dev_id)
    if not d:
        return await call.answer("Устройство не найдено", show_alert=True)

    ifaces = d["interfaces"]
    if not ifaces:
        text = f"🖧 <b>{escape(d['name'])}</b>\n\n<i>Физических интерфейсов нет в БД.</i>"
    else:
        up = sum(1 for i in ifaces if i["oper"] == 1)
        lines = [f"🖧 <b>{escape(d['name'])}</b> — {len(ifaces)} портов, up: {up}", ""]
        for i in ifaces[:40]:
            icon = "🟢" if i["oper"] == 1 else ("🔴" if i["oper"] == 2 else "⚪")
            speed = f"{i['speed_mbps']} Mbps" if i["speed_mbps"] else ""
            alias = (i.get("alias") or "").strip()
            ifname = i["name"] or f"if{i['if_index']}"
            # name + alias: name всегда, alias в скобках, если есть
            if alias:
                label = f"{escape(ifname)} <i>({escape(alias)})</i>"
            else:
                label = escape(ifname)
            lines.append(f"{icon} <b>{label}</b> {speed}".rstrip())
        if len(ifaces) > 40:
            lines.append(f"… и ещё {len(ifaces) - 40}")
        text = "\n".join(lines)

    kb = [[InlineKeyboardButton(text="🔙 К устройству", callback_data=f"dev_info:{d['id']}")]]
    try:
        await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    except Exception as e:
        if "message is not modified" in str(e):
            await call.answer("Сообщение не изменилось")
        else:
            raise
    await call.answer()


@router.callback_query(F.data.startswith("dev_alerts:"))
async def cb_device_alerts(call: CallbackQuery) -> None:
    from html import escape
    from django.db.models import Q
    from apps.devices.models import Device
    from apps.alerts.models import Alert

    dev_id = int(call.data.split(":")[1])

    def _fetch():
        dev = Device.objects.filter(pk=dev_id).first()
        if not dev:
            return {}
        alerts = list(
            Alert.objects.filter(state=Alert.STATE_FIRING)
            .filter(Q(device=dev) | Q(ne_device__gateway=dev))
            .order_by("-started_at")[:25]
        )
        return {"id": dev.id, "name": dev.name, "alerts": alerts}

    d = await sync_to_async(_fetch, thread_sensitive=True)()
    if not d:
        return await call.answer("Устройство не найдено", show_alert=True)

    if not d["alerts"]:
        text = f"✅ <b>{escape(d['name'])}</b>\n\nАктивных алертов нет."
    else:
        lines = [f"🚨 <b>{escape(d['name'])}</b> — алертов: {len(d['alerts'])}", ""]
        for a in d["alerts"]:
            icon = "🔴" if a.severity == "crit" else "⚠️"
            target = ""
            if a.ne_device_id:
                target = f" NE-{a.ne_device.ne_id}"
            val = f" = {a.value:g}" if a.value is not None else ""
            lines.append(f"{icon} {escape(a.metric_key)}{val}{target}")
        text = "\n".join(lines)

    kb = [[InlineKeyboardButton(text="🔄 Обновить", callback_data=f"dev_alerts:{d['id']}")],
          [InlineKeyboardButton(text="🔙 К устройству", callback_data=f"dev_info:{d['id']}")]]
    try:
        await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    except Exception as e:
        if "message is not modified" in str(e):
            await call.answer("Сообщение не изменилось")
        else:
            raise
    await call.answer()


# ── Навигация ─────────────────────────────────────────────────
@router.callback_query(F.data == "main_menu")
async def cb_main_menu(call: CallbackQuery, acc=None) -> None:
    try:
        await call.message.edit_text("👋 <b>Главное меню</b>", reply_markup=_main_kb(acc is not None, acc), parse_mode="HTML")
    except Exception as e:  # «message is not modified» при повторном нажатии
        if "message is not modified" not in str(e):
            raise
    await call.answer()

@router.callback_query(F.data == "link_help")
async def cb_link_help(call: CallbackQuery) -> None:
    try:
        await call.message.edit_text(NOT_LINKED_TEXT, parse_mode="HTML")
    except Exception as e:
        if "message is not modified" not in str(e):
            raise
    await call.answer()


# Любые другие сообщения: непривязанным — инструкция, привязанным — меню.
# Регистрируется последним, чтобы не перехватывать команды выше.
@router.message()
async def any_message(message: Message) -> None:
    acc = await sync_to_async(_access_for_tg_sync, thread_sensitive=True)(message.from_user.id)
    if acc is None:
        await message.answer(NOT_LINKED_TEXT, parse_mode="HTML")
        return
    await message.answer("Выберите действие:", reply_markup=_main_kb(True, acc))


# ── Запуск ────────────────────────────────────────────────────
async def main() -> None:
    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    logger.info("Cerberus Telegram bot: start polling")
    watch = asyncio.create_task(_watchdog_loop())
    try:
        await dp.start_polling(bot)
    finally:
        watch.cancel()


async def _watchdog_loop() -> None:
    """Сторожевой сигнал Celery (apps/core/watchdog.py): бот живёт отдельно от воркеров и beat."""
    from apps.core.watchdog import check_and_notify
    while True:
        await asyncio.sleep(120)
        try:
            await sync_to_async(check_and_notify, thread_sensitive=True)()
        except Exception:
            logger.exception("watchdog: ошибка проверки")

# ── Аватарка из Telegram ──────────────────────────────────────
import requests
from django.core.files.base import ContentFile
from django.conf import settings

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
