"""
Обёртка над fping для сканирования подсетей и мониторинга IP.

ВАЖНО: fping 5.x пишет xmt/rcv/%loss = ... в STDERR (не stdout!).
Поэтому парсим именно stderr. Флаг -a/-q не нужен — они только
запутывают (и -q вообще подавляет stdout).

Формат строки:
    172.16.101.2 : xmt/rcv/%loss = 1/1/0%, min/avg/max = 1.36/1.36/1.36
    → живой: %loss = 0
    → мёртвый: %loss = 100  (или "is unreachable")
"""

import ipaddress
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)

FPING_BIN = "fping"
DEFAULT_TIMEOUT_MS = 1000     # -t 1000 мс ожидание ответа
DEFAULT_INTERVAL_MS = 200     # -p 200 мс между пакетами
DEFAULT_RETRIES = 1           # -r 1


# Строка вида: "1.2.3.4 : xmt/rcv/%loss = 1/1/0%, min/avg/max = 0.5/0.5/0.5"
_LOSS_LINE_RE = re.compile(
    r"^(?P<ip>\d+\.\d+\.\d+\.\d+)\s*:\s*"
    r"xmt/rcv/%loss\s*=\s*(?P<xmt>\d+)/(?P<rcv>\d+)/(?P<loss>\d+)%"
    r"(?:,\s*min/avg/max\s*=\s*(?P<min>[\d.]+)/(?P<avg>[\d.]+)/(?P<max>[\d.]+))?"
)

# Строка "1.2.3.4 is unreachable" (fping иногда так пишет)
_UNREACHABLE_RE = re.compile(r"^(?P<ip>\d+\.\d+\.\d+\.\d+)\s+is\s+unreachable")


@dataclass
class ScanResult:
    """Результат сканирования подсети."""
    alive_ips: list[str]
    rtt_map: dict[str, float]
    hosts_total: int
    command: str
    error: str = ""


@dataclass
class PingResult:
    """Результат одиночного пинга."""
    ip: str
    alive: bool
    rtt_ms: float | None = None
    packet_loss_pct: float = 100.0
    error: str = ""


# ─────────────────────────────────────────────────────────────
# Проверка наличия fping
# ─────────────────────────────────────────────────────────────

def fping_available() -> bool:
    """Проверяет, что fping есть в PATH."""
    return shutil.which(FPING_BIN) is not None


# ─────────────────────────────────────────────────────────────
# Парсер stderr fping
# ─────────────────────────────────────────────────────────────

def _parse_fping_output(output: str) -> dict[str, dict]:
    """
    Парсит вывод fping.
    Возвращает: {ip: {"alive": bool, "rtt_ms": float|None, "loss_pct": float}}
    """
    result: dict[str, dict] = {}

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue

        m = _LOSS_LINE_RE.match(line)
        if m:
            ip = m.group("ip")
            xmt = int(m.group("xmt"))
            rcv = int(m.group("rcv"))
            loss = float(m.group("loss"))
            avg = m.group("avg")
            result[ip] = {
                "alive": rcv > 0,
                "rtt_ms": float(avg) if avg else None,
                "loss_pct": loss,
            }
            continue

        m = _UNREACHABLE_RE.match(line)
        if m:
            ip = m.group("ip")
            result.setdefault(ip, {
                "alive": False,
                "rtt_ms": None,
                "loss_pct": 100.0,
            })

    return result


# ─────────────────────────────────────────────────────────────
# Сканирование подсети
# ─────────────────────────────────────────────────────────────

def scan_subnet(
    cidr: str,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    interval_ms: int = DEFAULT_INTERVAL_MS,
    retries: int = DEFAULT_RETRIES,
) -> ScanResult:
    """
    Сканирует подсеть через fping.

    Возвращает ScanResult: список живых IP + RTT + общее число хостов.
    """
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError as e:
        return ScanResult(
            alive_ips=[], rtt_map={}, hosts_total=0,
            command="", error=f"Неверный CIDR: {e}",
        )

    hosts = [str(ip) for ip in net.hosts()]
    if not hosts:
        return ScanResult(
            alive_ips=[], rtt_map={}, hosts_total=0,
            command="", error="В подсети нет хостов (слишком маленькая маска)",
        )

    if not fping_available():
        return ScanResult(
            alive_ips=[], rtt_map={}, hosts_total=len(hosts),
            command="", error="fping не найден в PATH",
        )

    cmd = [
        FPING_BIN,
        "-c", str(retries),
        "-t", str(timeout_ms),
        "-p", str(interval_ms),
        "-r", "1",
        *hosts,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(60, len(hosts) * (timeout_ms + interval_ms) / 1000 + 10),
        )
    except subprocess.TimeoutExpired:
        return ScanResult(
            alive_ips=[], rtt_map={}, hosts_total=len(hosts),
            command=" ".join(cmd), error="fping не уложился в таймаут",
        )
    except Exception as e:
        return ScanResult(
            alive_ips=[], rtt_map={}, hosts_total=len(hosts),
            command=" ".join(cmd), error=f"Ошибка запуска fping: {e}",
        )

    # ВАЖНО: fping пишет всё в stderr
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    parsed = _parse_fping_output(combined)

    alive_ips = []
    rtt_map = {}
    for ip, info in parsed.items():
        if info["alive"]:
            alive_ips.append(ip)
            if info["rtt_ms"] is not None:
                rtt_map[ip] = info["rtt_ms"]

    return ScanResult(
        alive_ips=sorted(alive_ips, key=lambda x: ipaddress.ip_address(x)),
        rtt_map=rtt_map,
        hosts_total=len(hosts),
        command=" ".join(cmd),
        error="",
    )


# ─────────────────────────────────────────────────────────────
# Одиночный пинг (для мониторинга)
# ─────────────────────────────────────────────────────────────

def ping_one(
    ip: str,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    retries: int = 2,
) -> PingResult:
    """
    Пингует один IP через fping.
    Для мониторинга: retries=2 → чуть медленнее, но меньше ложных срабатываний.
    """
    if not fping_available():
        return PingResult(ip=ip, alive=False, error="fping не найден в PATH")

    cmd = [
        FPING_BIN,
        "-c", str(retries),
        "-t", str(timeout_ms),
        "-r", "1",
        ip,
    ]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=(timeout_ms * retries + 500) / 1000 + 3,
        )
    except subprocess.TimeoutExpired:
        return PingResult(ip=ip, alive=False, error="fping timeout")
    except Exception as e:
        return PingResult(ip=ip, alive=False, error=f"fping error: {e}")

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    parsed = _parse_fping_output(combined)

    info = parsed.get(ip)
    if not info:
        return PingResult(ip=ip, alive=False, packet_loss_pct=100.0)

    return PingResult(
        ip=ip,
        alive=info["alive"],
        rtt_ms=info.get("rtt_ms"),
        packet_loss_pct=info.get("loss_pct", 100.0),
    )
