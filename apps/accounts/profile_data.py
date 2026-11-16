"""Данные для личного кабинета: избранные станции, доступ, сессии, активность."""
import io
import re
import uuid

from django.utils import timezone


# ── Избранные станции: статус по активным алертам ──────────────────────────────
def favorite_stations_status(user):
    """[{station, crit, warn, state}] для избранных станций пользователя."""
    from apps.alerts.models import Alert
    from apps.stations.models import StationDevice, StationNe
    from apps.subnets.models import IpAddress
    from .models import UserPreferences

    prefs = UserPreferences.objects.filter(user=user).first()
    if prefs is None:
        return []
    stations = list(prefs.favorite_stations.all().order_by("sort_order", "name"))
    if not stations:
        return []
    ids = [s.pk for s in stations]

    dev_map, ne_map, ip_map = {}, {}, {}
    for sid, did in StationDevice.objects.filter(station_id__in=ids).values_list("station_id", "device_id"):
        dev_map.setdefault(did, set()).add(sid)
    for sid, nid in StationNe.objects.filter(station_id__in=ids).values_list("station_id", "ne_id"):
        ne_map.setdefault(nid, set()).add(sid)
    for iid, sid in IpAddress.objects.filter(station_id__in=ids).values_list("id", "station_id"):
        ip_map.setdefault(iid, set()).add(sid)

    counts = {sid: {"crit": 0, "warn": 0} for sid in ids}
    firing = Alert.objects.filter(state="firing").values_list("severity", "device_id", "ne_device_id", "ip_address_id")
    for sev, did, nid, iid in firing:
        hit = dev_map.get(did, set()) | ne_map.get(nid, set()) | ip_map.get(iid, set())
        key = "crit" if sev in ("crit", "power") else "warn"
        for sid in hit:
            counts[sid][key] += 1

    out = []
    for s in stations:
        c = counts[s.pk]
        state = "crit" if c["crit"] else "warn" if c["warn"] else "ok"
        out.append({"station": s, "crit": c["crit"], "warn": c["warn"], "state": state})
    return out


# ── «Мои права» ────────────────────────────────────────────────────────────────
def access_summary(user):
    from .access import CAPABILITIES, GROUP_LABELS, LEVEL_LABELS, SECTIONS, Access
    acc = Access.for_user(user)
    groups = []
    for g, label in GROUP_LABELS.items():
        rows = [{"name": s.name, "hint": s.hint, "level": acc.level(s.key),
                 "label": LEVEL_LABELS[acc.level(s.key)], "url": s.url if acc.can_view(s.key) else ""}
                for s in SECTIONS if s.group == g]
        groups.append({"label": label, "rows": rows})
    return {
        "is_superuser": user.is_superuser,
        "groups": groups,
        "caps": [{"name": c.name, "hint": c.hint, "on": acc.has_cap(c.key)} for c in CAPABILITIES],
        "admin_entry": acc.admin_entry,
    }


def access_brief(user) -> list:
    """Короткие метки для шапки профиля: «Администратор» или разделы с правом изменения."""
    from .access import SECTIONS, Access
    if user.is_superuser:
        return ["Суперпользователь"]
    acc = Access.for_user(user)
    edit = [s.name for s in SECTIONS if acc.can_edit(s.key)]
    view = [s.name for s in SECTIONS if acc.can_view(s.key) and not acc.can_edit(s.key)]
    if len(edit) >= len(SECTIONS) - 1:
        return ["Полный доступ"]
    out = edit[:4] + (["+ ещё " + str(len(edit) - 4)] if len(edit) > 4 else [])
    if not out and view:
        out = ["Только просмотр"]
    return out


# ── Сессии ────────────────────────────────────────────────────────────────────
_UA_RULES = [
    (r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"), (r"YaBrowser", "Яндекс"), (r"Firefox/", "Firefox"),
    (r"Chrome/", "Chrome"), (r"Safari/", "Safari"), (r"TelegramBot|Telegram", "Telegram"),
]
_OS_RULES = [
    (r"Windows", "Windows"), (r"Android", "Android"), (r"iPhone|iPad|iOS", "iOS"),
    (r"Mac OS X|Macintosh", "macOS"), (r"Linux", "Linux"),
]


def describe_ua(ua: str) -> str:
    if not ua:
        return "Неизвестное устройство"
    br = next((n for p, n in _UA_RULES if re.search(p, ua)), "Браузер")
    os_ = next((n for p, n in _OS_RULES if re.search(p, ua)), "")
    return f"{br} · {os_}" if os_ else br


def active_sessions(user, current_key):
    """Живые сессии пользователя (запись есть и в UserSession, и в django_session)."""
    from django.contrib.sessions.models import Session
    from .models import UserSession

    now = timezone.now()
    rows = list(UserSession.objects.filter(user=user))
    alive = set(Session.objects.filter(
        session_key__in=[r.session_key for r in rows], expire_date__gt=now,
    ).values_list("session_key", flat=True))
    stale = [r.pk for r in rows if r.session_key not in alive]
    if stale:
        UserSession.objects.filter(pk__in=stale).delete()
    out = []
    for r in rows:
        if r.session_key not in alive:
            continue
        out.append({
            "id": r.pk, "ip": r.ip, "device": describe_ua(r.user_agent), "ua": r.user_agent,
            "created_at": r.created_at, "last_seen": r.last_seen,
            "current": r.session_key == current_key,
        })
    out.sort(key=lambda x: (not x["current"], -x["last_seen"].timestamp()))
    return out


def end_sessions(user, current_key, only_id=None) -> int:
    """Завершить другие сессии пользователя (или одну по id). Текущую не трогаем."""
    from django.contrib.sessions.models import Session
    from .models import UserSession

    qs = UserSession.objects.filter(user=user).exclude(session_key=current_key)
    if only_id is not None:
        qs = qs.filter(pk=only_id)
    keys = list(qs.values_list("session_key", flat=True))
    Session.objects.filter(session_key__in=keys).delete()
    qs.delete()
    return len(keys)


# ── Активность ────────────────────────────────────────────────────────────────
def recent_activity(user, limit=30):
    """Смешанная лента: действия с устройствами, документы, комментарии."""
    items = []
    try:
        from apps.devices.models import ActionLog
        for a in ActionLog.objects.filter(user=user).select_related("device")[:limit]:
            items.append({
                "at": a.created_at, "icon": "bi-hdd-network", "kind": "device",
                "title": f"{a.get_action_display()} {a.target}".strip(),
                "meta": a.device.name if a.device_id else "",
                "status": a.status,
            })
    except Exception:
        pass
    try:
        from apps.edocs.models import Comment, Document
        for d in Document.objects.filter(author=user).order_by("-created_at")[:limit]:
            items.append({
                "at": d.created_at, "icon": "bi-send", "kind": "doc",
                "title": d.title, "meta": "Отправлен документ", "url": f"/edocs/document/{d.pk}/",
            })
        for c in Comment.objects.filter(author=user).select_related("document").order_by("-created_at")[:limit]:
            items.append({
                "at": c.created_at, "icon": "bi-chat-left-text", "kind": "comment",
                "title": c.document.title, "meta": "Комментарий", "url": f"/edocs/document/{c.document_id}/",
            })
    except Exception:
        pass
    items.sort(key=lambda x: x["at"], reverse=True)
    return items[:limit]


# ── Аватар ────────────────────────────────────────────────────────────────────
AVATAR_MAX_BYTES = 3 * 1024 * 1024
AVATAR_SIZE = 256


def process_avatar(upload):
    """Проверяет картинку и возвращает (ContentFile, имя) — квадрат 256×256 JPEG. Ошибка → ValueError."""
    from django.core.files.base import ContentFile
    from PIL import Image, ImageOps

    if upload.size > AVATAR_MAX_BYTES:
        raise ValueError("Файл больше 3 МБ.")
    try:
        img = Image.open(upload)
        img.verify()
        upload.seek(0)
        img = Image.open(upload)
        if img.format not in ("JPEG", "PNG", "WEBP", "GIF"):
            raise ValueError
        img = ImageOps.exif_transpose(img).convert("RGB")
    except ValueError:
        raise ValueError("Нужна картинка JPG, PNG, WEBP или GIF.")
    except Exception:
        raise ValueError("Не удалось прочитать картинку.")
    img = ImageOps.fit(img, (AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return ContentFile(buf.getvalue()), f"u{uuid.uuid4().hex[:12]}.jpg"
