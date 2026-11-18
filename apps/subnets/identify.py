"""
«Кто это?» — опознание узла по MAC и/или IP для панели захвата трафика.

Источники (от быстрых к медленным):
  • OUI-база производителей (nmap-mac-prefixes из sniffer, кэш в Redis на неделю);
  • база Cerber: IpAddress (hostname, станция, описание), Device;
  • ARP-таблицы коммутаторов (ArpEntry) и ARP сервера (ip neigh);
  • FDB коммутаторов (FdbEntry) — на каком порту сидит MAC;
  • обратный DNS;
  • по запросу (deep): NetBIOS/SMB через nmap и имя из DHCP-запроса (tcpdump -v).
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
import threading
from typing import Any

from django.core.cache import cache
from django.db.models import Count

logger = logging.getLogger(__name__)

OUI_CACHE_KEY = "capture:oui:v1"
OUI_PATH = "/usr/share/nmap/nmap-mac-prefixes"
_oui_mem: dict[str, str] | None = None
_oui_lock = threading.Lock()

MAX_IPS = 8


# ── MAC ──────────────────────────────────────────────────────────────────────
def normalize_mac(value: str | None) -> str | None:
    """aa:bb:.. / AA-BB-.. / aabb.ccdd.eeff / aabbccddeeff → 'aa:bb:cc:dd:ee:ff'."""
    hexs = re.sub(r"[^0-9a-fA-F]", "", value or "")
    if len(hexs) != 12 or not re.fullmatch(r"[0-9a-fA-F:.\-\s]+", value or ""):
        return None
    hexs = hexs.lower()
    return ":".join(hexs[i:i + 2] for i in range(0, 12, 2))


# Служебные адреса назначения: OUI-база их не знает
WELL_KNOWN_MACS = {
    "ff:ff:ff:ff:ff:ff": "Broadcast — всем в сегменте",
    "01:80:c2:00:00:00": "STP/RSTP/MSTP (IEEE 802.1D)",
    "01:80:c2:00:00:02": "LACP / Slow Protocols",
    "01:80:c2:00:00:0e": "LLDP",
    "01:00:0c:cc:cc:cc": "Cisco CDP / VTP / DTP / PAgP",
    "01:00:0c:cc:cc:cd": "Cisco PVST+",
    "01:1b:19:00:00:00": "PTP (IEEE 1588)",
}
WELL_KNOWN_PREFIXES = (
    ("01:00:5e", "IPv4 multicast"),
    ("33:33", "IPv6 multicast"),
    ("01:80:c2", "IEEE 802.1 служебный"),
)


def well_known(mac: str) -> str | None:
    if mac in WELL_KNOWN_MACS:
        return WELL_KNOWN_MACS[mac]
    for prefix, name in WELL_KNOWN_PREFIXES:
        if mac.startswith(prefix):
            return name
    return None


def mac_kind(mac: str) -> dict[str, bool]:
    first = int(mac[:2], 16)
    return {
        "broadcast": mac == "ff:ff:ff:ff:ff:ff",
        "multicast": bool(first & 0x01) and mac != "ff:ff:ff:ff:ff:ff",
        # бит U/L: «локально администрируемый» — случайный MAC телефона/ноутбука, ВМ, контейнер
        "local": bool(first & 0x02) and not first & 0x01,
    }


def _load_oui() -> dict[str, str]:
    global _oui_mem
    if _oui_mem is not None:
        return _oui_mem
    with _oui_lock:
        if _oui_mem is not None:
            return _oui_mem
        table = cache.get(OUI_CACHE_KEY)
        if table is None:
            table = {}
            try:
                from apps.core import ops_client
                out = ops_client.sniffer_run(["cat", OUI_PATH], timeout=10).stdout
                for line in out.splitlines():
                    if not line or line[0] == "#":
                        continue
                    prefix, _, name = line.partition(" ")
                    if prefix and name:
                        table[prefix.upper()] = name.strip()
            except Exception as e:  # нет базы — работаем без производителя
                logger.warning("OUI table load failed: %s", e)
            cache.set(OUI_CACHE_KEY, table, 7 * 86400 if table else 600)
        _oui_mem = table or None   # пустую не запоминаем в процессе — попробуем позже
        return table


def vendor_of(mac: str) -> str | None:
    table = _load_oui()
    if not table:
        return None
    hexs = mac.replace(":", "").upper()
    for n in (9, 7, 6):                # MA-S, MA-M, MA-L
        v = table.get(hexs[:n])
        if v:
            return v
    return None


# ── коммутаторы ──────────────────────────────────────────────────────────────
def switch_ports(mac: str) -> list[dict[str, Any]]:
    """
    Где MAC в FDB коммутаторов. MAC виден и на аплинках, поэтому для каждого
    порта считаем, сколько MAC за ним: порт доступа — тот, где их меньше всего.
    """
    from apps.monitoring.models import FdbEntry

    entries = list(FdbEntry.objects.filter(mac__iexact=mac).select_related("device"))
    if not entries:
        return []
    counts = {
        (r["device_id"], r["bridge_port"]): r["n"]
        for r in FdbEntry.objects.filter(
            device_id__in={e.device_id for e in entries},
            bridge_port__in={e.bridge_port for e in entries},
        ).values("device_id", "bridge_port").annotate(n=Count("mac", distinct=True))
    }
    out = []
    for e in entries:
        n = counts.get((e.device_id, e.bridge_port), 1)
        out.append({
            "device": e.device.name, "device_ip": e.device.ip,
            "port": e.interface_name or f"порт {e.bridge_port}",
            "vlan": e.vlan_id, "macs_on_port": n, "access": n <= 3,
            "updated": e.updated_at.isoformat() if e.updated_at else None,
        })
    out.sort(key=lambda x: (x["macs_on_port"], x["device"]))
    return out[:5]


def _host_neigh() -> dict[str, str]:
    """ARP/ND-таблица сервера (через sniffer): ip → mac."""
    out: dict[str, str] = {}
    try:
        from apps.core import ops_client
        for line in ops_client.sniffer_run(["ip", "neigh", "show"], timeout=5).stdout.splitlines():
            parts = line.split()
            if "lladdr" in parts:
                m = normalize_mac(parts[parts.index("lladdr") + 1])
                if m:
                    out[parts[0]] = m
    except Exception as e:
        logger.info("ip neigh: %s", e)
    return out


# ── DNS ──────────────────────────────────────────────────────────────────────
def _reverse_dns(ips: list[str], timeout: float = 1.5) -> dict[str, str]:
    """PTR-записи параллельно, с общим таймаутом (gethostbyaddr своего таймаута не имеет)."""
    res: dict[str, str] = {}

    def one(ip):
        try:
            name = socket.gethostbyaddr(ip)[0]
            if name and name != ip:
                res[ip] = name
        except (OSError, UnicodeError):
            pass

    threads = [threading.Thread(target=one, args=(ip,), daemon=True) for ip in ips]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout)
    return dict(res)


# ── глубокий поиск ───────────────────────────────────────────────────────────
def _netbios(ip: str) -> dict[str, str]:
    from apps.core import ops_client
    out: dict[str, str] = {}
    try:
        r = ops_client.sniffer_run(["nmap", "-p", "137", "--script", "nbstat.nse", ip], timeout=20)
        m = re.search(r"NetBIOS name:\s*([^,\s]+)", r.stdout)
        if m:
            out["netbios"] = m.group(1)
        m = re.search(r"NetBIOS user:\s*([^,\s]+)", r.stdout)
        if m and m.group(1) != "<unknown>":
            out["user"] = m.group(1)
        if "netbios" not in out:
            r = ops_client.sniffer_run(["nmap", "-p", "445", "--script", "smb-os-discovery", ip], timeout=20)
            for key, pat in (("netbios", r"Computer name:\s*(.+)"), ("os", r"OS:\s*(.+)"),
                             ("domain", r"Domain name:\s*(.+)")):
                m = re.search(pat, r.stdout)
                if m:
                    out[key] = m.group(1).strip()
    except Exception as e:
        logger.info("netbios %s: %s", ip, e)
    return out


def _dhcp_sniff(mac: str, iface: str, seconds: int = 6) -> dict[str, str]:
    """Ловим DHCP-запрос от MAC: Hostname (опция 12), Vendor-Class (60), запрошенный IP (50)."""
    from apps.core import ops_client
    out: dict[str, str] = {}
    try:
        r = ops_client.sniffer_run(
            ["timeout", f"{seconds}s", "tcpdump", "-i", iface, "-c", "2", "-nn", "-v",
             f"ether src {mac} and udp port 67"], timeout=seconds + 4)
        text = r.stdout
        for key, pat in (("hostname", r'Hostname[^"]*"([^"]+)"'),
                         ("vendor_class", r'Vendor-Class[^"]*"([^"]+)"'),
                         ("requested_ip", r"Requested-IP[^:]*:\s*([\d.]+)")):
            m = re.search(pat, text)
            if m:
                out[key] = m.group(1)
    except Exception as e:
        logger.info("dhcp sniff %s: %s", mac, e)
    return out


# ── главное ──────────────────────────────────────────────────────────────────
def identify(mac: str | None = None, ips: list[str] | None = None,
             deep: bool = False, iface: str = "any") -> dict[str, Any]:
    from apps.devices.models import Device
    from apps.monitoring.models import ArpEntry
    from apps.subnets.models import IpAddress

    ip_list: list[str] = []
    for ip in ips or []:
        try:
            v = str(ipaddress.ip_address(ip))
        except ValueError:
            continue
        if v not in ip_list and not ipaddress.ip_address(v).is_unspecified:
            ip_list.append(v)
    sources: dict[str, set[str]] = {ip: {"захват"} for ip in ip_list}

    def add_ip(ip: str, src: str):
        if ip not in sources:
            if len(sources) >= MAX_IPS:
                return
            sources[ip] = set()
            ip_list.append(ip)
        sources[ip].add(src)

    result: dict[str, Any] = {"mac": mac, "vendor": None, "well_known": None, "kind": None, "names": [],
                              "ips": [], "ports": [], "deep": None}

    neigh = _host_neigh()   # ip → mac из ARP сервера

    # MAC неизвестен — берём из ARP сервера, базы или ARP коммутаторов по IP
    if not mac and ip_list:
        ip_obj = IpAddress.objects.filter(ip=ip_list[0]).exclude(mac="").first()
        arp = ArpEntry.objects.filter(ip=ip_list[0]).order_by("-updated_at").first()
        mac = normalize_mac(neigh.get(ip_list[0]) or (ip_obj.mac if ip_obj else None) or (arp.mac if arp else None))
        result["mac"] = mac
        result["mac_from_ip"] = bool(mac)

    if mac:
        result["kind"] = mac_kind(mac)
        result["vendor"] = vendor_of(mac)
        result["well_known"] = well_known(mac)
        for o in IpAddress.objects.filter(mac__iexact=mac).values_list("ip", flat=True)[:MAX_IPS]:
            add_ip(o, "база Cerber")
        for a in ArpEntry.objects.filter(mac__iexact=mac).select_related("device").order_by("-updated_at")[:MAX_IPS]:
            add_ip(a.ip, f"ARP {a.device.name}")
        for ip, m in neigh.items():
            if m == mac:
                add_ip(ip, "ARP сервера")
        if not result["kind"]["multicast"] and not result["kind"]["broadcast"]:
            result["ports"] = switch_ports(mac)

    names: list[dict[str, str]] = []
    ip_objs = {o.ip: o for o in IpAddress.objects.filter(ip__in=ip_list).select_related("subnet", "station")}
    devices = {d.ip: d for d in Device.objects.filter(ip__in=ip_list)}
    for ip in ip_list:
        o, d = ip_objs.get(ip), devices.get(ip)
        entry = {"ip": ip, "sources": sorted(sources.get(ip, ())), "subnet": None, "hostname": None,
                 "station": None, "description": None, "is_alive": None, "is_monitored": None,
                 "last_rtt_ms": None, "device": None}
        if o:
            entry.update(subnet=str(o.subnet.cidr) if o.subnet_id else None, hostname=o.hostname or None,
                         station=o.station.name if o.station_id else None, description=o.description or None,
                         is_alive=o.is_alive, is_monitored=o.is_monitored, last_rtt_ms=o.last_rtt_ms)
            if o.hostname:
                names.append({"name": o.hostname, "source": "база Cerber", "ip": ip})
        if d:
            entry["device"] = {"name": d.name, "url": f"/local/devices/{d.ip}/"}
            names.append({"name": d.name, "source": "устройство Cerber", "ip": ip})
        result["ips"].append(entry)

    for ip, name in _reverse_dns([e["ip"] for e in result["ips"]][:4]).items():
        names.append({"name": name, "source": "DNS", "ip": ip})

    if deep:
        # DHCP-ожидание и NetBIOS по каждому IP — параллельно
        deep_res: dict[str, Any] = {"dhcp": {}, "netbios": {}}
        jobs = []
        if mac and not result["kind"]["multicast"]:
            jobs.append(threading.Thread(
                target=lambda: deep_res.__setitem__("dhcp", _dhcp_sniff(mac, iface)), daemon=True))
        for ip in [e["ip"] for e in result["ips"]][:3]:
            jobs.append(threading.Thread(
                target=lambda ip=ip: deep_res["netbios"].__setitem__(ip, _netbios(ip)), daemon=True))
        for j in jobs:
            j.start()
        for j in jobs:
            j.join(45)
        deep_res["netbios"] = {ip: v for ip, v in deep_res["netbios"].items() if v}
        if deep_res["dhcp"].get("hostname"):
            names.append({"name": deep_res["dhcp"]["hostname"], "source": "DHCP"})
        for ip, nb in deep_res["netbios"].items():
            if nb.get("netbios"):
                names.append({"name": nb["netbios"], "source": "NetBIOS", "ip": ip})
        result["deep"] = deep_res

    seen = set()
    for n in names:
        key = n["name"].lower()
        if key not in seen:
            seen.add(key)
            result["names"].append(n)
    return result
