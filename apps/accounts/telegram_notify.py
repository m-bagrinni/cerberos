"""
Отправка уведомлений в Telegram из синхронного кода (Celery).
Используем urllib, чтобы не поднимать asyncio loop в воркере.
"""
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.utils import timezone
from django.utils.html import escape

logger = logging.getLogger(__name__)


def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"


def _post(method: str, payload: dict, timeout: int = 5) -> tuple[bool, str]:
    """Низкоуровневый POST. Возвращает (ok, тело_ответа_или_описание_ошибки)."""
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    if not token:
        return False, "TELEGRAM_BOT_TOKEN пуст"
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(_api_url(method), data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            body = ""
        return False, f"HTTP {e.code} {e.reason} | {body}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _is_network_error(info: str) -> bool:
    """URLError / ConnectionError / TimeoutError — сетевые сбои, стоит ретраить."""
    markers = (
        "URLError", "ConnectionError", "ConnectionResetError",
        "TimeoutError", "socket.timeout", "Connection reset",
        "Connection refused", "Temporary failure",
    )
    return any(m in info for m in markers)


def send_telegram_message(tg_id: int, text: str, reply_markup: dict | None = None) -> bool:
    """Отправляет сообщение. Логирует код и тело при ошибке.

    Ретраи: 429 (с учётом Retry-After), сетевые ошибки (URLError/ConnectionReset/Timeout).
    """
    if not getattr(settings, "TELEGRAM_ALERTS_ENABLED", True):
        return False

    payload = {
        "chat_id": tg_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)

    ok, info = _post("sendMessage", payload)
    if ok:
        logger.debug("TG sendMessage ok tg_id=%s", tg_id)
        return True

    # Ретрай на 429
    if "HTTP 429" in info:
        retry_after = 1
        try:
            body = info.split("|", 1)[1].strip()
            retry_after = int(json.loads(body).get("parameters", {}).get("retry_after", 1))
        except Exception:
            pass
        sleep_for = min(max(retry_after, 1), 5)
        logger.warning("TG 429 for tg_id=%s, retry in %ss", tg_id, sleep_for)
        time.sleep(sleep_for)
        ok, info = _post("sendMessage", payload)
        if ok:
            logger.debug("TG sendMessage ok (retry 429) tg_id=%s", tg_id)
            return True

    # Ретрай на сетевые сбои (Connection reset by peer и т.п.) — 1 попытка
    if _is_network_error(info):
        logger.warning("TG network error for tg_id=%s, retry in 1s: %s", tg_id, info)
        time.sleep(1)
        ok, info = _post("sendMessage", payload)
        if ok:
            logger.debug("TG sendMessage ok (retry net) tg_id=%s", tg_id)
            return True

    logger.warning("TG sendMessage failed for tg_id=%s: %s", tg_id, info)
    return False


def _fmt_num(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{v:g}"
    except Exception:
        return str(v)


def _unit(alert) -> str:
    metric = getattr(alert, "metric", None)
    unit = getattr(metric, "unit", "") if metric else ""
    return f" {unit}" if unit else ""


def _alert_target(alert) -> str:
    """Человекочитаемая цель. Эскейп — на вызывающей стороне.

    NE не имеет своего IP — опрашивается по SNMP через шлюз (Device).
    Поэтому для NE показываем шлюз, чтобы дежурный знал, куда идти.
    """
    if getattr(alert, "ip_address_id", None):
        ip = getattr(alert, "ip_address", None)
        if ip is None:
            return f"IP #{alert.ip_address_id}"
        station = getattr(getattr(ip, "station", None), "name", None)
        s = f"IP {ip.ip}"
        if station:
            s += f" · {station}"
        return s
    if getattr(alert, "ne_device_id", None):
        ne = getattr(alert, "ne_device", None)
        if ne is None:
            return f"NE #{alert.ne_device_id}"
        name = (ne.display_name or "").strip()
        label = f"NE-{ne.ne_id}"
        if name:
            label += f" {name}"
        return label
    if getattr(alert, "device_id", None):
        dev = getattr(alert, "device", None)
        if dev is None:
            return f"Device #{alert.device_id}"
        return f"{dev.name} ({dev.ip})"
    return "?"


def _webapp_url_for(alert) -> str | None:
    """Опциональная ссылка на карточку в web-UI (если задан CERBER_PUBLIC_BASE_URL).

    Для NE ссылка ведёт на карточку шлюза (Device) — сам NE без IP и без своей
    страницы; на странице шлюза видно список NE и их DDM.
    """
    base = getattr(settings, "CERBER_PUBLIC_BASE_URL", "").rstrip("/")
    if not base:
        return None
    ip = getattr(alert, "ip_address", None)
    if ip is not None and getattr(ip, "subnet_id", None):
        return f"{base}/subnets/{ip.subnet_id}/"
    ne = getattr(alert, "ne_device", None)
    if ne is not None:
        gw = getattr(ne, "gateway", None)
        if gw is not None and gw.pk:
            return f"{base}/admin-ui/devices/{gw.pk}/"
        return f"{base}/admin-ui/ne/"
    if getattr(alert, "device_id", None):
        return f"{base}/admin-ui/devices/{alert.device_id}/"
    return None


def _alert_text(alert) -> str:
    sev = (alert.severity or "").lower()
    icon = {"crit": "🚨", "warn": "⚠️"}.get(sev, "•")
    sev_label = {"crit": "CRIT", "warn": "WARN"}.get(sev, (alert.severity or "").upper())

    target = escape(_alert_target(alert))
    metric = escape(alert.metric_key or "")
    started = timezone.localtime(alert.started_at).strftime("%d.%m.%Y %H:%M") if alert.started_at else "—"
    unit = _unit(alert)

    lines = [
        f"{icon} <b>Cerberus · {escape(sev_label)}</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"🎯 <b>{target}</b>",
        "",
        f"📊 <code>{metric}</code>",
        f"📈 Значение: <b>{_fmt_num(alert.value)}{unit}</b>"
        + (f"  (порог {_fmt_num(alert.threshold)}{unit})" if alert.threshold is not None else ""),
        f"🕒 {started}",
    ]
    if alert.message:
        lines += ["", escape(alert.message)]
    return "\n".join(lines)


def _resolved_text(alert) -> str:
    target = escape(_alert_target(alert))
    metric = escape(alert.metric_key or "")
    started = timezone.localtime(alert.started_at).strftime("%d.%m %H:%M") if alert.started_at else "—"
    resolved = timezone.localtime(alert.resolved_at).strftime("%d.%m %H:%M") if alert.resolved_at else "—"
    return (
        "✅ <b>Cerberus · Восстановлено</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>{target}</b>\n"
        f"📊 <code>{metric}</code>\n"
        f"🕒 {started} → {resolved}"
    )


def _reply_markup(alert) -> dict | None:
    url = _webapp_url_for(alert)
    if not url:
        return None
    return {"inline_keyboard": [[{"text": "🔗 Открыть в Cerberus", "url": url}]]}



# ── Рассылка ──────────────────────────────────────────────────
# Текст собирается сразу (в момент события), а сетевую отправку делает задача в очереди
# "high": медленный api.telegram.org больше не тормозит опрос оптики и алерты.

def _send_to_all(text: str, reply_markup: dict | None = None, tag: str = "") -> int:
    from .models import TelegramAccount
    sent = 0
    for tg_id in TelegramAccount.objects.filter(
        is_active=True, alerts_enabled=True
    ).values_list("tg_id", flat=True):
        if send_telegram_message(tg_id, text, reply_markup=reply_markup):
            sent += 1
    if tag:
        logger.info("telegram %s sent=%s", tag, sent)
    return sent


def _dispatch(text: str, reply_markup: dict | None = None, tag: str = "") -> int:
    """Ставит рассылку в очередь. Возвращает -1 (поставлено) или число отправленных (sync-фолбэк)."""
    if getattr(settings, "TELEGRAM_NOTIFY_SYNC", False):
        return _send_to_all(text, reply_markup, tag)
    try:
        from .tasks import send_telegram_broadcast
        send_telegram_broadcast.apply_async(
            args=[text, reply_markup, tag], queue="high", expires=6 * 3600,
        )
        return -1
    except Exception as e:
        logger.warning("telegram %s: не удалось поставить в очередь (%s) — шлём сразу", tag, e)
        return _send_to_all(text, reply_markup, tag)


def notify_alert_created(alert) -> int:
    if alert is None:
        logger.warning("notify_alert_created: alert is None — пропускаем")
        return 0
    text = _alert_text(alert)
    kb = _reply_markup(alert)
    sent = _dispatch(text, kb, f"alert_created:{getattr(alert, 'id', '?')}")
    logger.info("notify_alert_created alert=%s sent=%s", getattr(alert, "id", "?"), sent)
    return sent


def notify_alert_resolved(alert) -> int:
    if alert is None:
        logger.warning("notify_alert_resolved: alert is None — пропускаем")
        return 0
    text = _resolved_text(alert)
    sent = _dispatch(text, None, f"alert_resolved:{getattr(alert, 'id', '?')}")
    logger.info("notify_alert_resolved alert=%s sent=%s", getattr(alert, "id", "?"), sent)
    return sent


# ── Broadcast storm (BroadcastStormEvent) ─────────────────────
def _broadcast_target(event) -> str:
    subnet = getattr(event, "subnet", None)
    if subnet is None:
        return f"Подсеть #{event.subnet_id}"
    cidr = getattr(subnet, "cidr", "") or f"#{event.subnet_id}"
    name = getattr(subnet, "name", "") or ""
    s = f"Подсеть {cidr}"
    if name:
        s += f" · {name}"
    if getattr(event, "iface", ""):
        s += f" · iface {event.iface}"
    return s


def _broadcast_text(event) -> str:
    sev = (event.severity or "").lower()
    label = {"crit": "CRIT", "warn": "WARN"}.get(sev, sev.upper())
    started = timezone.localtime(event.started_at).strftime("%d.%m.%Y %H:%M") if event.started_at else "—"
    pct_now = event.bcast_pct_now or 0.0
    pps_now = event.bcast_per_sec_peak or 0.0
    pct_peak = event.bcast_pct_peak or 0.0
    pps_peak = event.bcast_per_sec_peak or 0.0
    lines = [
        f"🌩 <b>Cerberus · BROADCAST STORM · {escape(label)}</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"🎯 <b>{escape(_broadcast_target(event))}</b>",
        "",
        f"📊 Сейчас: <b>{pct_now:.2f}%</b> · <b>{int(pps_now)} pps</b> · всего {event.total_now}",
        f"📈 Пик: <b>{pct_peak:.2f}%</b> · <b>{int(pps_peak)} pps</b>",
        f"🕒 {started}",
    ]
    if event.reason:
        lines += ["", escape(event.reason)]
    return "\n".join(lines)


def _broadcast_resolved_text(event) -> str:
    started = timezone.localtime(event.started_at).strftime("%d.%m %H:%M") if event.started_at else "—"
    resolved = timezone.localtime(event.resolved_at).strftime("%d.%m %H:%M") if event.resolved_at else "—"
    pct_peak = event.bcast_pct_peak or 0.0
    pps_peak = event.bcast_per_sec_peak or 0.0
    return (
        "✅ <b>Cerberus · Broadcast storm восстановлен</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>{escape(_broadcast_target(event))}</b>\n"
        f"📊 Пик: {pct_peak:.2f}% · {int(pps_peak)} pps\n"
        f"🕒 {started} → {resolved}"
    )


def notify_broadcast_storm(event, created: bool = False) -> int:
    if event is None:
        logger.warning("notify_broadcast_storm: event is None — пропускаем")
        return 0
    text = _broadcast_text(event)
    sent = _dispatch(text, None, f"broadcast:{getattr(event, 'id', '?')}")
    logger.info(
        "notify_broadcast_storm event=%s created=%s sent=%s",
        getattr(event, "id", "?"), created, sent,
    )
    return sent


def notify_broadcast_resolved(event) -> int:
    if event is None:
        logger.warning("notify_broadcast_resolved: event is None — пропускаем")
        return 0
    text = _broadcast_resolved_text(event)
    sent = _dispatch(text, None, f"broadcast_resolved:{getattr(event, 'id', '?')}")
    logger.info("notify_broadcast_resolved event=%s sent=%s", getattr(event, "id", "?"), sent)
    return sent

def send_telegram_document(chat_id: int, document_path: str, caption: str = "", filename: str = None) -> bool:
    """
    Отправляет файл в Telegram.
    document_path — путь к файлу на диске (например, doc.pdf_file.path).
    """
    import requests
    from django.conf import settings
    
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', None)
    if not token or not chat_id:
        logger.warning("TG_DOC: No token or chat_id")
        return False
    
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    
    try:
        with open(document_path, 'rb') as f:
            files = {'document': (filename or 'document.pdf', f)}
            data = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'HTML'}
            resp = requests.post(url, data=data, files=files, timeout=30)
            
            if resp.status_code == 200:
                logger.info(f"TG_DOC: Sent to {chat_id}")
                return True
            else:
                logger.error(f"TG_DOC: HTTP {resp.status_code} | {resp.text[:200]}")
                return False
    except Exception as e:
        logger.error(f"TG_DOC: Error sending to {chat_id}: {e}")
        return False
