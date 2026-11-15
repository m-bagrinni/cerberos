"""
Сбор статистики трафика (broadcast/multicast) и детектор broadcast storm.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import subprocess
import time
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

SNIFFER_CONTAINER = "cerber-sniffer"

# MAC-адреса, исключаемые из анализа (наш сервер мониторинга и т.п.)
# Из .env: STORM_EXCLUDED_MACS=b4:2e:99:15:13:bc,aa:bb:cc:dd:ee:ff
import os as _os
_excluded_env = _os.getenv("STORM_EXCLUDED_MACS", "").strip()
EXCLUDED_SRC_MACS = {
    m.strip().lower()
    for m in _excluded_env.split(",")
    if m.strip()
} if _excluded_env else {
    "b4:2e:99:15:13:bc",  # наш сервер по умолчанию
}

# IP-адреса, исключаемые из анализа (наш сервер мониторинга и т.п.)
# Из .env: STORM_EXCLUDED_IPS=192.168.7.160,10.70.18.118
_excluded_ips_env = _os.getenv("STORM_EXCLUDED_IPS", "").strip()
EXCLUDED_SRC_IPS = {
    ip.strip()
    for ip in _excluded_ips_env.split(",")
    if ip.strip()
} if _excluded_ips_env else {
    "192.168.7.160",  # наш сервер по умолчанию
}

# Пороги
THRESHOLDS = {
    "min_total_packets": 100,      # минимум пакетов (меньше — игнор)
    "bcast_pct_warn":   15.0,      # broadcast > 25% → WARN
    "bcast_pct_crit":   30.0,      # broadcast > 50% → CRIT
    "bcast_per_sec_warn":  300,    # broadcast > 300 pps → WARN
    "bcast_per_sec_crit":  1000,   # broadcast > 1000 pps → CRIT
    "mcast_per_sec_warn":  1000,   # multicast > 1000 pps → WARN
    "mcast_per_sec_crit":  5000,   # multicast > 5000 pps → CRIT
    "consecutive_required": 1,     # гистерезис отключён
}


def find_iface_for_subnet(cidr: str) -> str | None:
    """Находит интерфейс хоста, в котором лежит подсеть."""
    try:
        target = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return None
    try:
        out = subprocess.run(
            ["docker", "exec", SNIFFER_CONTAINER, "ip", "-o", "-4", "a"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except Exception as e:
        logger.error("iface lookup failed: %s", e)
        return None
    for line in out.stdout.splitlines():
        m = re.search(r"^\d+:\s+([^:@]+).*?inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", line)
        if not m:
            continue
        name, ip, mask = m.group(1).strip(), m.group(2), int(m.group(3))
        if name.startswith("veth") or name.startswith("br-"):
            continue
        try:
            net = ipaddress.ip_network(f"{ip}/{mask}", strict=False)
        except ValueError:
            continue
        if net == target:
            return name
    return None


def _cleanup_old_pcaps() -> bool:
    """Удаляет pcap старше 30 мин."""
    try:
        subprocess.run(
            ["docker", "exec", SNIFFER_CONTAINER, "find", "/tmp/cerber_pcap",
             "-name", "count_*.pcap", "-mmin", "+30", "-delete"],
            capture_output=True, timeout=10, check=False,
        )
        return True
    except Exception:
        return False


def count_traffic(iface: str, duration: int = 5, cidr: str | None = None) -> dict[str, Any]:
    """tcpdump N сек → pcap → tcpdump -r -tttt → парсинг → счётчики."""
    from .tcpdump_parser import parse_tcpdump_output

    duration = max(2, min(int(duration), 30))
    import uuid as _uuid
    pcap_remote = f"/tmp/cerber_pcap/count_{int(time.time())}_{_uuid.uuid4().hex[:8]}.pcap"

    # pkill -9 tcpdump убран: он убивал чужие tcpdump (параллельные poll_broadcast_rate).
    # Свой tcpdump ограничен timeout — этого достаточно.

    # 1) CAPTURE
    cmd_capture = [
        "docker", "exec", SNIFFER_CONTAINER,
        "timeout", f"{duration + 2}s",
        "tcpdump", "-nn", "-e", "-U",
        "-w", pcap_remote, "-i", iface,
    ]
    if cidr:
        # Родительский интерфейс (enp4s0) видит все VLAN.
        # net X — только IP, ARP не ловит. Поэтому: IP в подсети + весь ARP
        # (ARP потом отфильтруем по src/dst в Python).
        cmd_capture.append(f"(ip and net {cidr}) or arp")
    try:
        subprocess.run(
            cmd_capture, capture_output=True, text=True,
            timeout=duration + 5, check=False,
        )
    except subprocess.TimeoutExpired:
        pass
    except Exception as e:
        logger.error("tcpdump capture failed: %s", e)
        return {"ok": False, "error": str(e)}

    # 2) READ — через sniffer-контейнер: pcap лежит в его /tmp, web его не видит
    cmd_read = [
        "docker", "exec", SNIFFER_CONTAINER,
        "tcpdump", "-nn", "-e", "-tttt", "-r", pcap_remote,
    ]
    try:
        proc = subprocess.run(
            cmd_read, capture_output=True, text=True, timeout=15, check=False,
        )
        stdout = proc.stdout or ""
        if not stdout and proc.stderr:
            logger.warning("tcpdump read stderr: %s", proc.stderr[:300])
    except Exception as e:
        logger.error("tcpdump read failed: %s", e)
        return {"ok": False, "error": str(e)}

    # 3) CLEANUP pcap (оставляем при STORM_DEBUG=1)
    import os as _os_dbg
    if _os_dbg.getenv("STORM_DEBUG", "").strip() not in ("1", "true", "yes"):
        try:
            subprocess.run(
                ["docker", "exec", SNIFFER_CONTAINER, "rm", "-f", pcap_remote],
                capture_output=True, timeout=3, check=False,
            )
        except Exception:
            pass
    else:
        logger.info("STORM_DEBUG: pcap saved at %s", pcap_remote)

    packets = parse_tcpdump_output(stdout)

    # Python-фильтр по подсети: для IP и ARP проверяем src/dst.
    # (BPF "(ip and net X) or arp" ловит ARP из всех VLAN — надо отсечь чужие.)
    if cidr:
        from ipaddress import ip_network, ip_address as _ipaddr
        try:
            _net = ip_network(cidr, strict=False)
        except ValueError:
            _net = None
        if _net is not None:
            before = len(packets)
            filtered = []
            for pkt in packets:
                in_net = False
                for key in ("src", "dst"):
                    a = (pkt.get(key) or "").strip()
                    if not a:
                        continue
                    try:
                        if _ipaddr(a) in _net:
                            in_net = True
                            break
                    except ValueError:
                        pass
                if in_net:
                    filtered.append(pkt)
            packets = filtered
            logger.debug("cidr filter %s: %d -> %d packets", cidr, before, len(packets))

    broadcast = 0
    multicast = 0
    unicast = 0
    bps = 0
    bcast_mac = Counter()
    mcast_mac = Counter()
    bcast_arp = 0  # ARP-запросы (норма)

    for p in packets:
        mac_dst = (p.get("mac_dst") or "").lower()
        mac_src = (p.get("mac_src") or "").lower()
        ip_src = (p.get("ip_src") or p.get("src") or "").strip()
        bm = p.get("bm")
        cat = p.get("category", "")

        # Исключаем пакеты от нашего сервера мониторинга (по MAC и по IP)
        if mac_src in EXCLUDED_SRC_MACS:
            continue
        if ip_src and ip_src in EXCLUDED_SRC_IPS:
            continue

        is_bcast = (mac_dst == "ff:ff:ff:ff:ff:ff") or (bm == "broadcast")
        is_mcast = False
        if not is_bcast and mac_dst:
            if mac_dst.startswith("01:00:5e") or mac_dst.startswith("33:33") or mac_dst.startswith("01:80:c2"):
                is_mcast = True
            elif bm == "multicast":
                is_mcast = True

        if is_bcast:
            broadcast += 1
            if mac_src: bcast_mac[mac_src] += 1
            if cat == "arp":
                bcast_arp += 1
        elif is_mcast:
            multicast += 1
            if mac_src: mcast_mac[mac_src] += 1
        else:
            unicast += 1
        bps += (p.get("length") or 0) * 8

    total = len(packets)
    bcast_pct = round(broadcast / total * 100, 2) if total else 0.0
    mcast_pct = round(multicast / total * 100, 2) if total else 0.0
    arp_pct = round(bcast_arp / total * 100, 2) if total else 0.0

    return {
        "ok": True,
        "iface": iface,
        "interval_sec": duration,
        "broadcast": broadcast,
        "multicast": multicast,
        "unicast": unicast,
        "total": total,
        "bcast_pct": bcast_pct,
        "mcast_pct": mcast_pct,
        "arp_pct": arp_pct,
        "bps_total": bps // duration if duration else bps,
        "pps_total": round(total / duration, 2) if duration else 0,
        "bcast_per_sec": round(broadcast / duration, 2) if duration else 0,
        "multicast_per_sec": round(multicast / duration, 2) if duration else 0,
        "top_src_macs": [
            {"mac": mac, "count": count}
            for mac, count in (bcast_mac + mcast_mac).most_common(10)
        ],
    }


def detect_storm(metrics: dict[str, Any], consecutive: int = 1) -> dict[str, Any] | None:
    """
    Проверяет, является ли снимок storm.
    - минимум пакетов (чтобы не ловить шум)
    - bcast_pct / pps — порог
    - гистерезис (consecutive — кол-во тиков подряд)
    - ARP-only broadcast — норма, не storm
    """
    total = metrics.get("total", 0)
    if total < THRESHOLDS["min_total_packets"]:
        return None

    bcast_pct = metrics.get("bcast_pct", 0)
    bcast_ps = metrics.get("bcast_per_sec", 0)
    mcast_ps = metrics.get("multicast_per_sec", 0)
    arp_pct = metrics.get("arp_pct", 0)

    # Если broadcast — только ARP, это нормально (не storm)
    # Но если bcast_pct > 60% — ARP тоже storm
    if arp_pct > 0 and arp_pct >= bcast_pct * 0.95 and bcast_pct < 60:
        return None

    # Гистерезис
    if consecutive < THRESHOLDS["consecutive_required"]:
        return None

    # Storm требует реального потока (не просто высокий % на тихом интерфейсе)
    if bcast_ps < 30 and mcast_ps < 100:
        return None

    # CRIT
    if bcast_pct >= THRESHOLDS["bcast_pct_crit"]:
        return {"severity": "CRIT", "reason": f"Broadcast {bcast_pct:.1f}% > {THRESHOLDS['bcast_pct_crit']}%"}
    if bcast_ps >= THRESHOLDS["bcast_per_sec_crit"]:
        return {"severity": "CRIT", "reason": f"Broadcast {bcast_ps:.0f} pps > {THRESHOLDS['bcast_per_sec_crit']} pps"}
    if mcast_ps >= THRESHOLDS["mcast_per_sec_crit"]:
        return {"severity": "CRIT", "reason": f"Multicast {mcast_ps:.0f} pps > {THRESHOLDS['mcast_per_sec_crit']} pps"}

    # WARN
    if bcast_pct >= THRESHOLDS["bcast_pct_warn"]:
        return {"severity": "WARN", "reason": f"Broadcast {bcast_pct:.1f}% > {THRESHOLDS['bcast_pct_warn']}%"}
    if bcast_ps >= THRESHOLDS["bcast_per_sec_warn"]:
        return {"severity": "WARN", "reason": f"Broadcast {bcast_ps:.0f} pps > {THRESHOLDS['bcast_per_sec_warn']} pps"}
    if mcast_ps >= THRESHOLDS["mcast_per_sec_warn"]:
        return {"severity": "WARN", "reason": f"Multicast {mcast_ps:.0f} pps > {THRESHOLDS['mcast_per_sec_warn']} pps"}

    return None
