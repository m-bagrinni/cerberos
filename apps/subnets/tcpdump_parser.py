"""
Парсер вывода tcpdump в структурированный JSON для UI.
Формат строки (tcpdump -nn -tttt):
    2026-10-30 06:47:16.286135 IP 10.70.7.83.64815 > 239.255.255.250.1900: UDP, length 175
    2026-10-30 06:47:16.580334 ARP, Request who-has 10.70.7.1 tell 0.0.0.0, length 46
    2026-10-30 06:47:16.707717 STP 802.1d, Config, ...
    2026-10-30 06:47:16.085578 IP 10.70.7.77 > 10.70.7.244: ICMP echo request, ...
"""
from __future__ import annotations

import re
from typing import Any


# Регулярки для парсинга
RE_MAC_HEADER = re.compile(r"^([0-9a-f]{2}(?::[0-9a-f]{2}){5}) > ([0-9a-f]{2}(?::[0-9a-f]{2}){5}), ethertype")

RE_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\s+(.*)$")
RE_IP = re.compile(
    r"^(?:(?P<proto>IP6?)\s+)?"   # "IP " опционально (с -e его нет)
    r"(?P<src>[\w:.]+?)(?:\.(?P<sport>\d+))?\s+>\s+"
    r"(?P<dst>[\w:.]+?)(?:\.(?P<dport>\d+))?:"
    r"\s+(?P<info>.*)$"
)
RE_ICMP = re.compile(r"^(?P<proto>IP6?)\s+(?P<src>[\w:.]+)\s+>\s+(?P<dst>[\w:.]+):\s+(?P<info>ICMP.*)$")
RE_ARP = re.compile(r"^ARP,\s+(?P<info>.*?)(?:,\s+length\s+(?P<len>\d+))?$")
RE_STP = re.compile(r"^STP\s+(?P<info>.*)")
RE_LLDP = re.compile(r"^LLDP,\s+(?P<info>.*)")
RE_LEN = re.compile(r"length\s+(\d+)")


def _classify(proto: str, info: str, sport: str | None, dport: str | None) -> str:
    """Определяет категорию пакета для цвета/фильтра."""
    info_l = info.lower()
    if "icmp" in info_l:
        return "icmp"
    if proto == "ARP":
        return "arp"
    if "stp" in info_l:
        return "stp"
    if "lldp" in info_l:
        return "lldp"
    if "bootp" in info_l or "dhcp" in info_l:
        return "dhcp"
    if "ssdp" in info_l or (dport == "1900"):
        return "ssdp"
    if "mdns" in info_l or (dport == "5353"):
        return "mdns"
    if sport == "161" or dport == "161":
        return "snmp"
    if sport == "53" or dport == "53":
        return "dns"
    if "igmp" in info_l:
        return "igmp"
    if "tcp" in info_l:
        return "tcp"
    if "udp" in info_l:
        return "udp"
    if proto == "IP" or proto == "IP6":
        return "ip"
    return "other"


def parse_tcpdump_output(text: str) -> list[dict[str, Any]]:
    """
    Разбирает stdout tcpdump (-nn -tttt -e) в список пакетов.
    Формат с -e:
        ts  aa:bb:cc:dd:ee:ff > 11:22:33:44:55:66, ethertype IPv4 (0x0800), length 60: IP ...
        ts  aa:bb:cc:dd:ee:ff > ff:ff:ff:ff:ff:ff, ethertype ARP (0x0806), length 46: ARP, ...
    Без -e (fallback):
        ts  IP 1.2.3.4 > 5.6.7.8: ...
        ts  ARP, Request ...
    """
    packets: list[dict[str, Any]] = []

    # Полный формат MAC-заголовка с ethertype и (опционально) length:
    # "aa:bb:cc:dd:ee:ff > 11:22:33:44:55:66, ethertype IPv4 (0x0800), length 60: IP ..."
    # или "aa:bb:cc:dd:ee:ff > 11:22:33:44:55:66, ethertype ARP (0x0806), length 46: ARP, ..."
    re_mac = re.compile(
        r"^([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})\s*>\s*([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}),\s*"
        r"(?:ethertype\s+\S+\s+\(0x[0-9a-fA-F]+\),\s*)?"
        r"(?:length\s+\d+:\s*)?"
        r"(.*)$"
    )

    for line in text.splitlines():
        m = RE_TS.match(line)
        if not m:
            continue

        timestamp = m.group(1)
        rest = m.group(2)

        # Извлекаем MAC-заголовок (если -e)
        mac_src = None
        mac_dst = None
        mac_m = re_mac.match(rest)
        if mac_m:
            mac_src = mac_m.group(1)
            mac_dst = mac_m.group(2)
            rest = mac_m.group(3)

        # ARP
        if rest.startswith("ARP,"):
            lm = RE_LEN.search(rest)
            length = int(lm.group(1)) if lm else 0
            info = re.sub(r",?\s*length\s+\d+$", "", rest).replace("ARP,", "").strip()
            src = ""
            dst = ""
            mwho = re.search(r"who-has\s+([\d.]+)\s+tell\s+([\d.]+)", info)
            if mwho:
                dst = mwho.group(1)
                src = mwho.group(2)
            elif "Reply" in info:
                mrep = re.search(r"([\d.]+)\s+is-at", info)
                if mrep:
                    src = mrep.group(1)
            packets.append({
                "timestamp": timestamp,
                "src": src, "sport": None,
                "dst": dst, "dport": None,
                "proto": "ARP",
                "info": info,
                "length": length,
                "category": "arp",
                "mac_src": mac_src, "mac_dst": mac_dst,
                "bm": "broadcast",
            })
            continue

        # STP
        if rest.startswith("STP"):
            lm = RE_LEN.search(rest)
            length = int(lm.group(1)) if lm else 0
            packets.append({
                "timestamp": timestamp,
                "src": "", "sport": None,
                "dst": "01:80:c2:00:00:00", "dport": None,
                "proto": "STP",
                "info": rest[:120],
                "length": length,
                "category": "stp",
                "mac_src": mac_src, "mac_dst": mac_dst,
                "bm": "multicast",
            })
            continue

        # LLDP
        if rest.startswith("LLDP"):
            lm = RE_LEN.search(rest)
            length = int(lm.group(1)) if lm else 0
            packets.append({
                "timestamp": timestamp,
                "src": "", "sport": None,
                "dst": "01:80:c2:00:00:0e", "dport": None,
                "proto": "LLDP",
                "info": rest[:160],
                "length": length,
                "category": "lldp",
                "mac_src": mac_src, "mac_dst": mac_dst,
                "bm": "multicast",
            })
            continue

        # IP / IP6
        mip = RE_IP.match(rest)
        if mip:
            proto = mip.group("proto") or "IP"  # если -e убрал "IP "
            info = mip.group("info") or ""
            sport = mip.group("sport")
            dport = mip.group("dport")
            lm = RE_LEN.search(info)
            length = int(lm.group(1)) if lm else 0
            category = _classify(proto, info, sport, dport)
            bm = _is_broadcast_multicast(mip.group("dst"))
            if bm:
                category = bm
            packets.append({
                "timestamp": timestamp,
                "src": mip.group("src"),
                "sport": int(sport) if sport else None,
                "dst": mip.group("dst"),
                "dport": int(dport) if dport else None,
                "proto": proto,
                "info": info[:180],
                "length": length,
                "category": category,
                "mac_src": mac_src, "mac_dst": mac_dst,
                "bm": bm,
            })
            continue

        # Fallback — пакет без MAC и без IP (STP без -e или что-то ещё)
        packets.append({
            "timestamp": timestamp,
            "src": "", "sport": None, "dst": "", "dport": None,
            "proto": "?", "info": rest[:180], "length": 0,
            "category": "other",
            "mac_src": mac_src, "mac_dst": mac_dst,
            "bm": None,
        })

    return packets


def _is_broadcast_multicast(dst: str) -> str | None:
    """Возвращает 'broadcast' / 'multicast' / None по dst-адресу."""
    if not dst:
        return None
    d = dst.lower()
    # IPv4 broadcast
    if d == "255.255.255.255" or d.endswith(".255"):
        return "broadcast"
    # MAC broadcast
    if d == "ff:ff:ff:ff:ff:ff":
        return "broadcast"
    # IPv4 multicast 224.0.0.0/4
    if d.startswith("224.") or d.startswith("225.") or d.startswith("226.") or d.startswith("227.") \
       or d.startswith("228.") or d.startswith("229.") or d.startswith("230.") \
       or d.startswith("231.") or d.startswith("232.") or d.startswith("233.") \
       or d.startswith("234.") or d.startswith("235.") or d.startswith("236.") \
       or d.startswith("237.") or d.startswith("238.") or d.startswith("239."):
        return "multicast"
    # IPv6 multicast ff00::/8
    if d.startswith("ff"):
        return "multicast"
    # MAC multicast (бит 1 в первом байте == 1, и не broadcast)
    if ":" in d and len(d) == 17 and d != "ff:ff:ff:ff:ff:ff":
        try:
            first_octet = int(d.split(":")[0], 16)
            if first_octet & 0x01:
                return "multicast"
        except Exception:
            pass
    return None

