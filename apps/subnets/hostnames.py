"""
Автоматическое определение hostname и MAC для живых IP.

Раньше имя узнавалось только по кнопке в интерфейсе (resolve_ip_info_view), а скан его не
собирал — у большинства адресов hostname пустой. Теперь задача subnets.resolve_hostnames
заполняет ПУСТЫЕ поля (ручные имена не перезаписываются) по цепочке источников:

  MAC  : ARP-таблица хоста (ip neigh, через ops-сервис) — одним вызовом на все IP
  имя  : 1) AD (компьютеры домена → A-запись)   — один LDAP-запрос на прогон
         2) обратный DNS (PTR)
         3) SNMP sysName (сетевое оборудование)
         4) SMB / NetBIOS через nmap (Windows)  — медленно, ограниченная порция за прогон

Ограничения: у камер, телефонов, принтеров и т.п. имени может не быть нигде — такие адреса
остаются пустыми, повторная попытка по каждому не чаще, чем раз в RETRY_HOURS.
"""
from __future__ import annotations

import logging
import re
import socket
from concurrent.futures import ThreadPoolExecutor

from django.core.cache import cache

logger = logging.getLogger(__name__)

RETRY_HOURS = 6          # как часто повторять поиск имени для одного IP
HEAVY_PER_RUN = 12       # сколько IP за прогон проверять медленным nmap (SMB/NetBIOS)
_NEIGH = re.compile(r"^(\d+\.\d+\.\d+\.\d+)\s+dev\s+\S+\s+lladdr\s+([0-9a-f:]{17})\b", re.I)


def _clean(name: str | None) -> str:
    name = (name or "").strip().strip('"')
    if not name or name.lower().startswith(("no such", "timeout")):
        return ""
    return name.split(".")[0][:255] if re.fullmatch(r"[\w.-]+", name) and "." in name and not name[0].isdigit() else name[:255]


def arp_macs() -> dict[str, str]:
    """ip → mac из ARP-таблицы хоста (ops-сервис, команда ip neigh show)."""
    from apps.core import ops_client
    out = ops_client.sniffer_run(["ip", "neigh", "show"], timeout=10).stdout or ""
    res = {}
    for line in out.splitlines():
        m = _NEIGH.match(line.strip())
        if m:
            res[m.group(1)] = m.group(2).lower()
    return res


def ad_names() -> dict[str, str]:
    """ip → имя компьютера для всех компьютеров домена (LDAP + DNS домена)."""
    try:
        from django.conf import settings
        from ldap3 import SUBTREE
        from apps.servers.ad_client import get_ad_connection
        if not (settings.AD_USER and settings.AD_BASE_DN):
            return {}
        conn = get_ad_connection()
        conn.search(settings.AD_BASE_DN, "(objectCategory=computer)", SUBTREE,
                    attributes=["name", "dNSHostName"], paged_size=500)
        rows = [(str(e.name.value), str(e.dNSHostName.value or "")) for e in conn.entries]
        conn.unbind()
    except Exception as e:
        logger.warning("resolve_hostnames: AD недоступен: %s", e)
        return {}

    def one(row):
        name, fqdn = row
        for q in filter(None, (fqdn, name)):
            try:
                return socket.gethostbyname(q), name
            except OSError:
                continue
        return None
    with ThreadPoolExecutor(8) as ex:
        return dict(r for r in ex.map(one, rows) if r)


def reverse_dns(ip: str) -> str:
    try:
        return _clean(socket.gethostbyaddr(ip)[0])
    except OSError:
        return ""


def snmp_sysname(ip: str) -> str:
    from apps.core import ops_client
    try:
        r = ops_client.sniffer_run(["snmpget", "-v2c", "-c", "public", "-Oqv", ip, "1.3.6.1.2.1.1.5.0"], timeout=5)
        return _clean(r.stdout)
    except Exception:
        return ""


def nmap_name(ip: str) -> str:
    """SMB (Windows computer name), затем NetBIOS."""
    from apps.core import ops_client
    for port, script, marker in (("445", "smb-os-discovery", "Computer name:"), ("137", "nbstat.nse", "NetBIOS name:")):
        try:
            out = ops_client.sniffer_run(["nmap", "-p", port, "--script", script, ip], timeout=25).stdout or ""
        except Exception:
            continue
        for line in out.splitlines():
            if marker in line:
                return _clean(line.split(marker, 1)[1].split(",")[0])
    return ""


def resolve_all() -> dict:
    from django.utils import timezone  # noqa: F401
    from apps.subnets.models import IpAddress

    alive = list(IpAddress.objects.filter(is_alive=True))
    stats = {"macs": 0, "ad": 0, "dns": 0, "snmp": 0, "nmap": 0}

    # 1) MAC
    try:
        macs = arp_macs()
    except Exception as e:
        logger.warning("resolve_hostnames: ARP недоступен: %s", e)
        macs = {}
    for obj in alive:
        if not obj.mac and obj.ip in macs:
            IpAddress.objects.filter(pk=obj.pk).update(mac=macs[obj.ip])
            stats["macs"] += 1

    # 2) имена — только для пустых и не пробованных недавно
    todo = [o for o in alive if not o.hostname and not cache.get(f"hn_try:{o.ip}")]
    if not todo:
        return stats

    ad = ad_names()
    rest = []
    for obj in todo:
        name = ad.get(obj.ip)
        if name:
            stats["ad"] += 1
            IpAddress.objects.filter(pk=obj.pk, hostname="").update(hostname=name)
        else:
            rest.append(obj)

    def light(obj):
        n = reverse_dns(obj.ip)
        if n:
            return obj, n, "dns"
        n = snmp_sysname(obj.ip)
        return (obj, n, "snmp") if n else (obj, "", "")
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(light, rest))

    heavy = []
    for obj, name, src in results:
        if name:
            stats[src] += 1
            IpAddress.objects.filter(pk=obj.pk, hostname="").update(hostname=name)
        else:
            heavy.append(obj)

    # медленный SMB/NetBIOS — небольшими порциями; остальные дойдут на следующих прогонах
    batch, tried_later = heavy[:HEAVY_PER_RUN], heavy[HEAVY_PER_RUN:]
    with ThreadPoolExecutor(4) as ex:
        names = list(ex.map(lambda o: nmap_name(o.ip), batch))
    for obj, name in zip(batch, names):
        if name:
            stats["nmap"] += 1
            IpAddress.objects.filter(pk=obj.pk, hostname="").update(hostname=name)
        cache.set(f"hn_try:{obj.ip}", 1, RETRY_HOURS * 3600)
    # IP, не дошедшие до nmap, но уже прошедшие лёгкие источники, помечаем как пробованные
    # только частично: оставляем без метки, чтобы следующий прогон взял их в nmap
    return stats
