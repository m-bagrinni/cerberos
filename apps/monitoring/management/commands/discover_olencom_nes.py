"""
Discovery Olencom NE-устройств через SNMP.

Для указанного шлюза (Device с profile.device_category='multiplexer')
находит все NE (ne_id 1..50) и создаёт/обновляет записи NeDevice.

Использование:
    python manage.py discover_olencom_nes 172.16.101.12
    python manage.py discover_olencom_nes 172.16.101.12 --verbose
"""

import re
import subprocess
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.devices.models import Device, NeDevice


OID_BASE = "1.3.6.1.4.1.3399.2.3.3"
OID_NE_STATUS = f"{OID_BASE}.2.8.1.5"
OID_NE_NAME   = f"{OID_BASE}.2.8.1.20"
OID_NE_IP     = f"{OID_BASE}.1.2.1.1"

_re_int = re.compile(r'INTEGER:\s*(-?\d+)')
_re_hex = re.compile(r'Hex-STRING:\s*([0-9A-Fa-f\s]+)')
_re_ip  = re.compile(r'IpAddress:\s*(\d+\.\d+\.\d+\.\d+)')

NE_ID_RANGE = range(1, 51)


def snmpget(ip, community, oid, timeout=2, retries=1):
    cmd = [
        "snmpget", "-v", "1", "-c", community,
        "-On", "-t", str(timeout), "-r", str(retries),
        ip, oid,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 3)
        out = r.stdout.strip()
        if "noSuchName" in out or "No Such" in out or "Timeout" in out:
            return ""
        return out
    except Exception:
        return ""


def parse_int(out):
    m = _re_int.search(out)
    return int(m.group(1)) if m else None


def parse_hex(out):
    m = _re_hex.search(out)
    if not m:
        return None
    try:
        return [int(b, 16) for b in m.group(1).split()]
    except ValueError:
        return None


def parse_ip(out):
    m = _re_ip.search(out)
    return m.group(1) if m else None


def parse_ne_name(hex_bytes):
    """
    Извлекает имя NE из hex.
    Возвращает (raw_str, clean_str):
      - raw_str   — как есть (для отладки)
      - clean_str — очищенное имя (без \\x00, без мусора)
    """
    if not hex_bytes:
        return "", ""
    # Убираем нулевой padding
    trimmed = []
    for b in hex_bytes:
        if b == 0:
            break
        trimmed.append(b)
    if not trimmed:
        return "", ""
    # Собираем как есть
    raw = bytes(trimmed)
    try:
        raw_str = raw.decode("latin-1").strip()
    except Exception:
        raw_str = ""
    # Очищенная версия — только печатные ASCII, без управляющих символов
    clean = "".join(chr(b) for b in trimmed if 32 <= b <= 126)
    return raw_str, clean


class Command(BaseCommand):
    help = "Discover Olencom NE devices via SNMP (creates/updates NeDevice)"

    def add_arguments(self, parser):
        parser.add_argument("gateway_ip", type=str)
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--ne-range", type=int, default=50,
                            help="До какого ne_id сканировать (default: 50)")

    def handle(self, *args, **opts):
        gw_ip = opts["gateway_ip"]
        verbose = opts["verbose"]

        try:
            gateway = Device.objects.get(ip=gw_ip)
        except Device.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Gateway {gw_ip} not found in DB"))
            return

        community = gateway.snmp_community or "public"
        discovered = []
        updated = []

        self.stdout.write(f"[DISCOVER] Scanning NE {gw_ip} (community={community})...")

        for ne_id in range(1, opts["ne_range"] + 1):
            # Status обязателен — если нет, пропускаем (NE не существует)
            s_out = snmpget(gw_ip, community, f"{OID_NE_STATUS}.{ne_id}")
            s_val = parse_int(s_out)
            if s_val is None:
                continue

            # Имя
            n_out = snmpget(gw_ip, community, f"{OID_NE_NAME}.{ne_id}")
            n_hex = parse_hex(n_out)
            raw_name, clean_name = parse_ne_name(n_hex) if n_hex else ("", "")

            # IP
            ip_out = snmpget(gw_ip, community, f"{OID_NE_IP}.{ne_id}")
            ne_ip = parse_ip(ip_out)

            # Статус для NeDevice
            status = NeDevice.STATUS_OK if s_val == 1 else NeDevice.STATUS_UNREACHABLE

            obj, created = NeDevice.objects.update_or_create(
                gateway=gateway,
                ne_id=ne_id,
                defaults={
                    # name — только при создании; иначе сохраняем ручное имя
                    **({"name": clean_name or f"NE-{ne_id}"} if not NeDevice.objects.filter(
                        gateway=gateway, ne_id=ne_id
                    ).exists() else {}),
                    "raw_name": raw_name,
                    "ne_ip": ne_ip or None,
                    "status": status,
                    "last_seen": timezone.now(),
                },
            )
            if created:
                discovered.append(obj)
                self.stdout.write(
                    f"  [+] NE-{ne_id}: name={clean_name!r} ip={ne_ip} status={status}"
                )
            else:
                updated.append(obj)
                if verbose:
                    self.stdout.write(f"  [=] NE-{ne_id} updated (status={status})")

        self.stdout.write(self.style.SUCCESS(
            f"Done. Discovered={len(discovered)}, updated={len(updated)}, "
            f"total NE in DB for {gateway.name}: {NeDevice.objects.filter(gateway=gateway).count()}"
        ))
