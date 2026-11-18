"""
Отпечаток узла — признаки для определения типа устройства (devtype.py).

Пассивно-лёгкие проверки живых IP, без сканирования портов:
  • TTL ответа на ping → исходный TTL ОС: 64 (Linux, принтеры, камеры, MikroTik),
    128 (Windows), 255 (Cisco и большинство коммутаторов);
  • NetBIOS Node Status (UDP 137) — отвечают Windows и Samba: имя, домен/рабочая
    группа, MAC сетевой карты, признак контроллера домена.

Запускается Celery-задачей после сканирования подсети (tasks.fingerprint_subnet_task),
пишет IpAddress.fingerprint. Пустые hostname/MAC дополняет из NetBIOS.
"""
from __future__ import annotations

import logging
import re
import socket
import struct
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

from django.utils import timezone

logger = logging.getLogger(__name__)

FRESH_FOR = timedelta(hours=6)     # не переснимать чаще
WORKERS = 32


# ── TTL ──────────────────────────────────────────────────────────────────────
def ping_ttl(ip: str, timeout_s: int = 1) -> int | None:
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", str(timeout_s), "-n", ip],
                           capture_output=True, text=True, timeout=timeout_s + 2)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"ttl=(\d+)", r.stdout)
    return int(m.group(1)) if m else None


def initial_ttl(observed: int | None) -> int | None:
    """Исходный TTL ОС: наблюдаемый, округлённый вверх до 32/64/128/255."""
    if observed is None:
        return None
    for base in (32, 64, 128, 255):
        if observed <= base:
            return base
    return None


# ── NetBIOS Node Status ──────────────────────────────────────────────────────
_NB_QUERY = (
    struct.pack(">HHHHHH", 0xCE7B, 0, 1, 0, 0, 0)
    + b"\x20" + b"".join(bytes([0x41 + (c >> 4), 0x41 + (c & 0xF)]) for c in b"*" + b"\x00" * 15) + b"\x00"
    + struct.pack(">HH", 0x21, 1)    # NBSTAT, IN
)


def parse_nbstat(data: bytes) -> dict[str, Any] | None:
    """Разбор ответа NBSTAT: имена (15 символов + суффикс + флаги) и MAC."""
    try:
        i = 12
        if data[i] & 0xC0 == 0xC0:   # сжатое имя (указатель)
            i += 2
        else:
            while data[i]:
                i += 1 + data[i]
            i += 1
        i += 10                      # type, class, ttl, rdlength
        n = data[i]
        i += 1
        names = []
        for _ in range(n):
            raw = data[i:i + 15].decode("latin-1").rstrip(" \x00")
            suffix = data[i + 15]
            flags = struct.unpack(">H", data[i + 16:i + 18])[0]
            names.append((raw, suffix, bool(flags & 0x8000)))
            i += 18
        mac = ":".join(f"{b:02x}" for b in data[i:i + 6]) if len(data) >= i + 6 else None
    except (IndexError, struct.error):
        return None
    if not names:
        return None
    host = next((nm for nm, suf, grp in names if suf == 0x00 and not grp), None)
    domain = next((nm for nm, suf, grp in names if suf == 0x00 and grp and not nm.startswith("__")), None)
    return {
        "name": host,
        "domain": domain,
        "mac": mac if mac and mac != "00:00:00:00:00:00" else None,
        "server": any(suf == 0x20 for _, suf, _ in names),            # файловый сервис (обычный Windows)
        "dc": any(suf in (0x1B, 0x1C) for _, suf, _ in names),         # контроллер домена
        "samba": any(nm == "__SAMBA__" for nm, _, _ in names),
    }


def nbstat(ip: str, timeout: float = 1.0) -> dict[str, Any] | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(_NB_QUERY, (ip, 137))
        data, _ = s.recvfrom(2048)
    except OSError:
        return None
    finally:
        s.close()
    return parse_nbstat(data)


# ── сбор ─────────────────────────────────────────────────────────────────────
def probe(ip: str) -> dict[str, Any]:
    ttl = ping_ttl(ip)
    nb = nbstat(ip)
    return {"ttl": ttl, "ttl0": initial_ttl(ttl), "nb": nb, "at": timezone.now().isoformat()}


def is_fresh(fp: dict | None) -> bool:
    if not fp or not fp.get("at"):
        return False
    try:
        from datetime import datetime
        return timezone.now() - datetime.fromisoformat(fp["at"]) < FRESH_FOR
    except (TypeError, ValueError):
        return False


def fingerprint_ips(ip_objs, force: bool = False) -> int:
    """Снимает отпечатки живых IP (параллельно) и сохраняет. Возвращает число обновлённых."""
    from .identify import normalize_mac

    todo = [o for o in ip_objs if o.is_alive and (force or not is_fresh(o.fingerprint))]
    if not todo:
        return 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda o: probe(o.ip), todo))
    for o, fp in zip(todo, results):
        fields = ["fingerprint"]
        o.fingerprint = fp
        nb = fp.get("nb") or {}
        if nb.get("name") and not o.hostname:
            o.hostname = nb["name"][:255]
            fields.append("hostname")
        if nb.get("mac") and not o.mac:
            o.mac = normalize_mac(nb["mac"]) or ""
            fields.append("mac")
        o.save(update_fields=fields)
    return len(todo)
