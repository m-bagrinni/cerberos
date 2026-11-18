"""
Парсер однострочного вывода tcpdump (-nn -tttt [-e]) в структуры для UI и мониторинга.

Поддерживаемые формы строки (после временной метки):

  Конкретный интерфейс, -e (EN10MB):
    b4:2e:..:bc > ff:ff:..:ff, ethertype ARP (0x0806), length 42: Request who-has 10.70.7.22 tell 10.70.7.77, length 28
    b4:2e:..:bc > 00:17:..:3b, ethertype IPv4 (0x0800), length 88: 10.70.7.77.58592 > 10.70.7.97.161:  GetRequest(31) ...
    40:01:..:90 > b4:2e:..:bc, ethertype 802.1Q (0x8100), length 64: vlan 8, p 0, ethertype IPv4 (0x0800), 192.168.8.109 > ...
    50:1c:..:13 > 01:00:0c:cc:cc:cd, 802.3, length 50: LLC, ... STP 802.1d, Config, ...

  Интерфейс any, -e (LINUX_SLL2):
    enp4s0.8 Out ifindex 4 b4:2e:..:bc ethertype IPv4 (0x0800), length 148: 192.168.8.254.35126 > 192.168.8.4.4700: Flags [P.], ...

  Без -e (pcap, старый вывод):
    IP 10.70.7.77 > 10.70.7.253: ICMP echo request, id 1, seq 2, length 64
    ARP, Request who-has 10.70.7.1 tell 10.70.7.77, length 28

Ключи пакета (стабильный контракт, их читает traffic_monitor):
    timestamp, src, sport, dst, dport, proto, info, length, category,
    mac_src, mac_dst, bm
Дополнительно: l4, app, flags, vlan, iface, direction, ethertype.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Any, Iterable

_MAC = r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}"

RE_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\s+(.*)$")
# -i any: "<iface> <In|Out|B|M|P> ifindex N <mac> <l2>"
RE_SLL = re.compile(
    rf"^(?P<iface>\S+)\s+(?P<dir>In|Out|B|M|P)\s+ifindex\s+\d+\s+(?P<mac>{_MAC})\s+(?P<l2>.*)$"
)
# конкретный интерфейс: "<mac> > <mac>, <l2>"
RE_ETH = re.compile(rf"^(?P<src>{_MAC})\s+>\s+(?P<dst>{_MAC}),\s+(?P<l2>.*)$")
# "ethertype IPv4 (0x0800), length 148: <payload>" | "802.3, length 50: <payload>"
RE_L2 = re.compile(
    r"^(?:ethertype\s+(?P<et>[^\s,]+)\s+\(0x[0-9a-fA-F]+\)|802\.3),\s+length\s+(?P<flen>\d+):\s*(?P<payload>.*)$"
)
# "vlan 8, p 0, ethertype IPv4 (0x0800), <payload>"
RE_VLAN = re.compile(
    r"^vlan\s+(?P<vid>\d+),\s+p\s+\d+,\s+ethertype\s+(?P<et>[^\s,]+)\s+\(0x[0-9a-fA-F]+\),\s*(?P<payload>.*)$"
)
# dst заканчивается на ":" + пробел — иначе IPv6 (ff02::1:2.547) обрежется на первом ":"
RE_ADDRS = re.compile(r"^(?P<src>\S+?)\s+>\s+(?P<dst>\S+?):(?:\s+|$)(?P<info>.*)$")
RE_LEN = re.compile(r"length\s+(\d+)")
RE_TCP_FLAGS = re.compile(r"Flags\s+\[([^\]]*)\]")
RE_ARP_REQ = re.compile(r"who-has\s+(\S+)(?:\s+\([^)]*\))?\s+tell\s+([\d.]+)")
RE_ARP_REP = re.compile(rf"Reply\s+(\S+)\s+is-at\s+({_MAC})")

# Хорошо известные порты → протокол прикладного уровня
PORT_APPS: dict[int, str] = {
    20: "FTP-DATA", 21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS",
    67: "DHCP", 68: "DHCP", 69: "TFTP", 80: "HTTP", 102: "S7comm", 110: "POP3",
    123: "NTP", 137: "NetBIOS", 138: "NetBIOS", 139: "NetBIOS", 143: "IMAP",
    161: "SNMP", 162: "SNMP-trap", 389: "LDAP", 443: "HTTPS", 445: "SMB",
    502: "Modbus", 514: "Syslog", 546: "DHCPv6", 547: "DHCPv6", 636: "LDAPS",
    1812: "RADIUS", 1813: "RADIUS", 1900: "SSDP", 2404: "IEC-104", 3389: "RDP",
    4789: "VXLAN", 5060: "SIP", 5353: "mDNS", 5355: "LLMNR", 8080: "HTTP",
    20000: "DNP3", 47808: "BACnet",
}

# app → категория (цвет/фильтр в UI)
_APP_CATEGORY = {
    "DNS": "dns", "mDNS": "mdns", "LLMNR": "dns", "DHCP": "dhcp", "DHCPv6": "dhcp",
    "SNMP": "snmp", "SNMP-trap": "snmp", "SSDP": "ssdp", "NTP": "ntp", "Syslog": "syslog",
}

TCP_FLAG_NAMES = {"S": "SYN", "F": "FIN", "R": "RST", "P": "PSH", ".": "ACK", "U": "URG", "E": "ECE", "W": "CWR"}


# ─────────────────────────────────────────────────────────────────────────────
def _split_host_port(addr: str) -> tuple[str, int | None]:
    """'10.0.0.1.161' → ('10.0.0.1', 161); 'fe80::1.546' → ('fe80::1', 546); '10.0.0.1' → (…, None)."""
    try:
        ipaddress.ip_address(addr)
        return addr, None
    except ValueError:
        pass
    host, sep, port = addr.rpartition(".")
    if sep and port.isdigit():
        try:
            ipaddress.ip_address(host)
            return host, int(port)
        except ValueError:
            pass
    return addr, None


def _mac_kind(mac: str | None) -> str | None:
    if not mac:
        return None
    m = mac.lower()
    if m == "ff:ff:ff:ff:ff:ff":
        return "broadcast"
    try:
        if int(m[:2], 16) & 0x01:
            return "multicast"
    except ValueError:
        pass
    return None


def _is_broadcast_multicast(dst: str | None) -> str | None:
    """'broadcast' / 'multicast' / None по IP- или MAC-адресу назначения."""
    if not dst:
        return None
    d = dst.lower()
    if re.fullmatch(_MAC, d):
        return _mac_kind(d)
    try:
        ip = ipaddress.ip_address(d)
    except ValueError:
        return None
    if ip.is_multicast:
        return "multicast"
    if ip.version == 4 and (d == "255.255.255.255" or d.endswith(".255")):
        return "broadcast"
    return None


def _app_for(sport: int | None, dport: int | None) -> str | None:
    known = [p for p in (sport, dport) if p is not None and p in PORT_APPS]
    if not known:
        return None
    return PORT_APPS[min(known)]


def _base(timestamp: str) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "src": "", "sport": None, "dst": "", "dport": None,
        "proto": "?", "l4": None, "app": None, "flags": None,
        "info": "", "length": 0, "category": "other",
        "mac_src": None, "mac_dst": None, "bm": None,
        "vlan": None, "iface": None, "direction": None, "ethertype": None,
    }


def _strip_trailing_len(info: str) -> str:
    return re.sub(r",?\s*length\s+\d+\s*$", "", info).strip()


# ─────────────────────────────────────────────────────────────────────────────
def _decode_arp(pkt: dict[str, Any], payload: str) -> None:
    body = payload[4:].strip() if payload.startswith("ARP,") else payload
    pkt["proto"] = "ARP"
    pkt["category"] = "arp"
    pkt["info"] = _strip_trailing_len(body)[:200]
    m = RE_ARP_REQ.search(body)
    if m:
        pkt["dst"], pkt["src"] = m.group(1), m.group(2)
    else:
        m = RE_ARP_REP.search(body)
        if m:
            pkt["src"] = m.group(1)
            pkt["dst"] = ""


def _decode_ip(pkt: dict[str, Any], payload: str, v6: bool) -> bool:
    if payload.startswith("IP6 "):
        payload, v6 = payload[4:], True
    elif payload.startswith("IP "):
        payload = payload[3:]
    m = RE_ADDRS.match(payload)
    if not m:
        return False
    src, sport = _split_host_port(m.group("src"))
    dst, dport = _split_host_port(m.group("dst"))
    info = m.group("info").strip()
    if ":" in src or ":" in dst:
        v6 = True
    pkt.update(src=src, sport=sport, dst=dst, dport=dport, info=info[:220])

    lm = None
    for lm in RE_LEN.finditer(info):
        pass
    if lm and not pkt["length"]:
        pkt["length"] = int(lm.group(1))

    il = info.lower()
    fm = RE_TCP_FLAGS.search(info)
    if fm:
        pkt["l4"] = "TCP"
        pkt["flags"] = fm.group(1)
    elif il.startswith("icmp6"):
        pkt["l4"] = "ICMPv6"
    elif il.startswith("icmp"):
        pkt["l4"] = "ICMP"
    elif il.startswith("igmp"):
        pkt["l4"] = "IGMP"
    elif il.startswith("vrrp"):
        pkt["l4"] = "VRRP"
    elif il.startswith("ospf"):
        pkt["l4"] = "OSPF"
    elif il.startswith("gre"):
        pkt["l4"] = "GRE"
    elif il.startswith("pim"):
        pkt["l4"] = "PIM"
    elif sport is not None or dport is not None:
        pkt["l4"] = "UDP"
    else:
        pkt["l4"] = "IPv6" if v6 else "IP"

    app = None
    if pkt["l4"] in ("TCP", "UDP"):
        app = _app_for(sport, dport)
        if app == "DHCP" and pkt["l4"] == "TCP":
            app = None
    pkt["app"] = app
    pkt["proto"] = app or pkt["l4"]

    if app in _APP_CATEGORY:
        pkt["category"] = _APP_CATEGORY[app]
    elif pkt["l4"] in ("ICMP", "ICMPv6"):
        pkt["category"] = "icmp"
    elif pkt["l4"] == "IGMP":
        pkt["category"] = "igmp"
    elif pkt["l4"] == "TCP":
        pkt["category"] = "tcp"
    elif pkt["l4"] == "UDP":
        pkt["category"] = "udp"
    else:
        pkt["category"] = "ip6" if v6 else "ip"
    return True


def _decode_other_l2(pkt: dict[str, Any], payload: str, ethertype: str | None) -> None:
    pl = payload
    if "STP 802.1" in pl or pl.startswith("STP"):
        pkt["proto"], pkt["category"] = "STP", "stp"
        pkt["info"] = pl[pl.find("STP"):][:200]
    elif pl.startswith("LLDP") or ethertype == "LLDP":
        pkt["proto"], pkt["category"] = "LLDP", "lldp"
        pkt["info"] = pl[:200]
    elif pl.startswith("CDPv") or "CDPv" in pl:
        pkt["proto"], pkt["category"] = "CDP", "lldp"
        pkt["info"] = pl[pl.find("CDPv"):][:200]
    elif pl.startswith("LACPv") or ethertype == "Slow":
        pkt["proto"], pkt["category"] = "LACP", "other"
        pkt["info"] = pl[:200]
    elif ethertype == "Loopback":
        pkt["proto"], pkt["category"] = "LOOP", "other"
        pkt["info"] = pl[:200]
    else:
        pkt["proto"] = ethertype or (pl.split(",", 1)[0].split()[0] if pl.strip() else "?")
        pkt["info"] = pl[:200]


def parse_line(line: str) -> dict[str, Any] | None:
    """Разбирает одну строку tcpdump. None — строка не является пакетом (служебная/продолжение)."""
    m = RE_TS.match(line)
    if not m:
        return None
    pkt = _base(m.group(1))
    rest = m.group(2)
    ethertype: str | None = None
    payload = rest

    ms = RE_SLL.match(rest)
    if ms:
        pkt["iface"] = ms.group("iface")
        pkt["direction"] = ms.group("dir")
        pkt["mac_src"] = ms.group("mac").lower()
        payload = ms.group("l2")
    else:
        me = RE_ETH.match(rest)
        if me:
            pkt["mac_src"] = me.group("src").lower()
            pkt["mac_dst"] = me.group("dst").lower()
            payload = me.group("l2")

    ml = RE_L2.match(payload)
    if ml:
        ethertype = ml.group("et")
        pkt["length"] = int(ml.group("flen"))
        payload = ml.group("payload")
    mv = RE_VLAN.match(payload)
    if mv:
        pkt["vlan"] = int(mv.group("vid"))
        ethertype = mv.group("et")
        payload = mv.group("payload")
    pkt["ethertype"] = ethertype

    if ethertype == "ARP" or payload.startswith("ARP,"):
        _decode_arp(pkt, payload)
        if not pkt["length"]:
            lm = RE_LEN.search(payload)
            pkt["length"] = int(lm.group(1)) if lm else 0
    elif ethertype in ("IPv4", "IPv6") or payload.startswith(("IP ", "IP6 ")) or ethertype is None:
        if not _decode_ip(pkt, payload, v6=(ethertype == "IPv6")):
            _decode_other_l2(pkt, payload, ethertype)
    else:
        _decode_other_l2(pkt, payload, ethertype)

    if not pkt["length"]:
        lm = RE_LEN.search(payload)
        if lm:
            pkt["length"] = int(lm.group(1))

    # broadcast / multicast: сначала по MAC назначения, затем по IP, затем по направлению SLL
    bm = _mac_kind(pkt["mac_dst"]) or _is_broadcast_multicast(pkt["dst"])
    if not bm and pkt["direction"] == "B":
        bm = "broadcast"
    elif not bm and pkt["direction"] == "M":
        bm = "multicast"
    if not bm and pkt["proto"] == "ARP" and pkt["mac_dst"] is None and "who-has" in pkt["info"]:
        bm = "broadcast"   # ARP-запрос без -e — всегда широковещательный
    pkt["bm"] = bm
    return pkt


def parse_tcpdump_output(text: str) -> list[dict[str, Any]]:
    """Разбирает весь stdout tcpdump в список пакетов (служебные строки пропускаются)."""
    out = []
    for line in text.splitlines():
        p = parse_line(line)
        if p is not None:
            out.append(p)
    return out


# Интерфейсы Docker на хосте: их трафик — внутренний (контейнеры Cerber, NAT-копии)
_DOCKER_IFACE = re.compile(r"^(veth|br-|docker)")


class Deduplicator:
    """
    Чистка вывода `-i any`:
      • пакет VLAN-подинтерфейса виден дважды (enp4s0 и enp4s0.N) — повтор с тем же
        содержимым в пределах `window_us` микросекунд отбрасываем;
      • пакеты на интерфейсах Docker (veth*, br-*, docker*) — внутренний трафик, отбрасываем.
    """

    def __init__(self, window_us: int = 2000, size: int = 256, skip_docker: bool = True):
        self.window_us = window_us
        self.size = size
        self.skip_docker = skip_docker
        self._recent: dict[str, int] = {}
        self.dropped = 0

    @staticmethod
    def _us(ts: str) -> int:
        # "2026-11-17 15:57:43.159988" → микросекунды от начала суток (достаточно для окна)
        hms, _, frac = ts.partition(" ")[2].partition(".")
        h, m, s = (int(x) for x in hms.split(":"))
        return ((h * 60 + m) * 60 + s) * 1_000_000 + int((frac + "000000")[:6])

    def is_dup(self, pkt: dict[str, Any]) -> bool:
        iface = pkt.get("iface")
        if not iface:
            return False
        if self.skip_docker and _DOCKER_IFACE.match(iface):
            self.dropped += 1
            return True
        key = "|".join(str(pkt.get(k)) for k in ("src", "sport", "dst", "dport", "proto", "info", "mac_src"))
        now = self._us(pkt["timestamp"])
        prev = self._recent.get(key)
        if prev is not None and 0 <= now - prev <= self.window_us:
            self.dropped += 1
            return True
        self._recent[key] = now
        if len(self._recent) > self.size:
            # выкидываем самые старые записи
            for k in sorted(self._recent, key=self._recent.get)[: self.size // 2]:
                del self._recent[k]
        return False

    def filter(self, packets: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return [p for p in packets if not self.is_dup(p)]
