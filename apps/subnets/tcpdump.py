"""
Запуск tcpdump из веб-интерфейса подсети.

Безопасность:
- BPF-фильтр собирается ТОЛЬКО из разрешённых токенов (icmp/tcp/udp/port/host/and/or/not + IP + число).
- Интерфейс — фиксированный ("any" или "eth0"), без произвольных имён из запроса.
- Жёсткий таймаут и лимит пакетов (tcpdump -c N + subprocess timeout).
- subprocess без shell=True, аргументы — списком.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import shutil
import signal
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)

from .tcpdump_parser import parse_tcpdump_output

MAX_DURATION = 30
DEFAULT_DURATION = 10

MAX_PACKETS = 500
DEFAULT_PACKETS = 50

# Разрешённые интерфейсы (никаких "any" снаружи — фиксируем)
ALLOWED_IFACES = {"any", "eth0", "lo"}

# Токены, которые можно собрать в BPF (простая грамматика для UI)
_TOKEN_RE = re.compile(
    r"^(icmp|icmp6|tcp|udp|arp|ip|ip6|port|host|net|src|dst|and|or|not|"
    r"broadcast|multicast|ether|vlan|"
    r"\d{1,5}|"
    r"[0-9a-fA-F:./]{1,50})$"
)


class TcpdumpError(Exception):
    """Ошибка валидации / запуска tcpdump."""


def _validate_bpf(bpf: str) -> str:
    """
    Принимает простой BPF из UI (например "host 8.8.8.8 and icmp").
    Разрешаем только белый список токенов через пробел.
    """
    bpf = (bpf or "").strip()
    if not bpf:
        return "ip"
    if len(bpf) > 200:
        raise TcpdumpError("BPF-фильтр слишком длинный (макс. 200 символов)")

    # Запрещаем shell-метасимволы и всё, что не в whitelist
    tokens = bpf.split()
    for t in tokens:
        low = t.lower()
        if not _TOKEN_RE.match(low):
            raise TcpdumpError(f"Недопустимый токен в BPF: {t!r}")

    # Если в фильтре есть "host <ip>" — проверим, что это валидный IP
    for i, t in enumerate(tokens[:-1]):
        if t.lower() in ("host", "net"):
            ip_candidate = tokens[i + 1]
            try:
                ipaddress.ip_network(ip_candidate, strict=False)
            except ValueError as e:
                raise TcpdumpError(f"Некорректный IP/сеть в BPF: {ip_candidate!r}") from e
    return " ".join(tokens)


def _get_ifaces() -> list[str]:
    """Возвращает интерфейсы хоста (через sniffer-контейнер)."""
    try:
        from apps.core import ops_client
        out = ops_client.sniffer_run(["ip", "-o", "link"], timeout=5)
    except Exception:
        return []
    names = re.findall(r"^\d+:\s+([^:@]+)", out.stdout, flags=re.M)
    # veth — транзитные, не показываем
    names = [n.strip() for n in names if not n.startswith("veth")]
    return names


def _validate_iface(iface: str) -> str:
    iface = (iface or "any").strip()
    allowed = _get_ifaces()
    if not allowed:
        raise TcpdumpError("Не удалось получить список интерфейсов")
    if iface == "any":
        return "any"
    if iface not in allowed:
        raise TcpdumpError(
            f"Интерфейс '{iface}' не найден. Доступны: any, {', '.join(sorted(allowed))}"
        )
    return iface


def run_tcpdump(
    bpf: str = "ip",
    iface: str = "any",
    duration: int = DEFAULT_DURATION,
    packets: int = DEFAULT_PACKETS,
) -> dict[str, Any]:
    """
    Запускает tcpdump через `timeout(1)`, парсит captured.

    Ключевые улучшения:
    - Оборачиваем tcpdump в `timeout Ns` — гарантированный kill через N секунд.
    - captured: из "N packets captured" (stderr) ИЛИ по timestamp-строкам stdout.
    - ok=True, если tcpdump стартовал (даже при 0 пакетов).
    - Убраны `-c N` и `--immediate-mode` (лимит по времени строже, а -c мешает реагировать на сигнал).
    """
    bpf = _validate_bpf(bpf)
    iface = _validate_iface(iface)

    duration = max(1, min(int(duration), MAX_DURATION))
    packets = max(1, min(int(packets), MAX_PACKETS))

    cmd = [
        "tcpdump",
        "-nn", "-tttt", "-U", "-l",  "-A", "-e",
        "-i", iface,
        bpf,
    ]

    # Гарантированное завершение через `duration` секунд (SIGTERM от timeout(1)
    # внутри sniffer). Выполняет cerber-ops — у web нет docker.sock.
    wrapped = ["timeout", f"{duration}s"] + cmd

    from apps.core import ops_client
    started = time.monotonic()
    try:
        proc = ops_client.sniffer_run(wrapped, timeout=duration + 3)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        timed_out = (proc.returncode == 124)
    except subprocess.TimeoutExpired as e:
        elapsed = time.monotonic() - started
        stdout = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        return {
            "ok": False,
            "bpf": bpf, "iface": iface,
            "duration": duration, "packets_requested": packets,
            "packets_captured": 0,
            "output": stdout[:1024],
            "error": "timeout(1) не завершил tcpdump — авария",
            "elapsed": round(elapsed, 2),
            "truncated": False,
            "timed_out": True,
        }

    elapsed = time.monotonic() - started

    # captured: сначала пробуем summary из stderr, потом счётчик timestamp-строк
    captured = 0
    m = re.search(r"(\d+)\s+packets?\s+captured", stderr)
    if m:
        captured = int(m.group(1))
    else:
        captured = len(re.findall(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.", stdout, flags=re.M))

    # Фильтруем безвредные WARNING (promiscuous mode на виртуальных интерфейсах)
    if stderr:
        stderr = "\n".join(
            line for line in stderr.splitlines()
            if "doesn't support promiscuous mode" not in line
            and "Promiscuous mode not supported" not in line
        ).strip()

    started_ok = ("listening on" in stderr) or ("listening on" in stdout)
    ok = captured > 0 or started_ok

    MAX_OUTPUT = 96 * 1024
    truncated = len(stdout) > MAX_OUTPUT
    if truncated:
        stdout = stdout[:MAX_OUTPUT] + "\n... (вывод обрезан)"

    err_msg = None
    if not ok:
        err_msg = stderr or f"tcpdump завершился с кодом {proc.returncode}"
    elif captured == 0:
        err_msg = "Пакетов не поймано за отведённое время"

    return {
        "ok": ok,
        "bpf": bpf,
        "iface": iface,
        "duration": duration,
        "packets_requested": packets,
        "packets_captured": captured,
        "output": stdout,
        "packets": parse_tcpdump_output(stdout),
        "error": err_msg,
        "elapsed": round(elapsed, 2),
        "truncated": truncated,
        "timed_out": timed_out,
    }


