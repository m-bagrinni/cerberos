"""
Захват трафика из веб-интерфейса: общие проверки и разовый снимок.

Безопасность:
- BPF проверяется apps.subnets.bpf (белый список слов/символов, без имён хостов),
  затем ещё раз белым списком cerber-ops.
- Интерфейс — только из реального списка интерфейсов хоста (или any).
- Жёсткие лимиты по времени и числу пакетов; subprocess без shell.
"""
from __future__ import annotations

import logging
import re
import subprocess
import time
from typing import Any

from . import bpf as bpf_mod
from .tcpdump_parser import Deduplicator, parse_tcpdump_output

logger = logging.getLogger(__name__)

MAX_DURATION = 60
DEFAULT_DURATION = 10

MAX_PACKETS = 5000
DEFAULT_PACKETS = 200

MAX_OUTPUT = 2 * 1024 * 1024

# Интерфейсы, которые не показываем: транзитные veth и docker-бриджи
_HIDDEN_IFACE = re.compile(r"^(veth|br-|docker)")


class TcpdumpError(Exception):
    """Ошибка валидации / запуска tcpdump (текст — для пользователя)."""


def _validate_bpf(bpf: str) -> str:
    try:
        return bpf_mod.validate(bpf)
    except bpf_mod.BpfError as e:
        raise TcpdumpError(f"Фильтр: {e}") from e


def list_ifaces() -> list[dict[str, Any]]:
    """
    Интерфейсы хоста (через sniffer) с IPv4-адресами и признаком VLAN.
    Кэш 30 с — список нужен на каждом запуске для проверки.
    """
    from django.core.cache import cache
    cached = cache.get("capture:ifaces")
    if cached is not None:
        return cached

    from apps.core import ops_client
    links = ops_client.sniffer_run(["ip", "-o", "link"], timeout=5).stdout
    addrs = ops_client.sniffer_run(["ip", "-o", "-4", "a"], timeout=5).stdout

    ips: dict[str, list[str]] = {}
    for line in addrs.splitlines():
        m = re.search(r"^\d+:\s+([^:@\s]+).*?inet\s+(\d+\.\d+\.\d+\.\d+/\d+)", line)
        if m:
            ips.setdefault(m.group(1), []).append(m.group(2))

    result = []
    for line in links.splitlines():
        m = re.match(r"^\d+:\s+([^:@]+)(?:@(\S+?))?:\s+<([^>]*)>", line)
        if not m:
            continue
        name, parent, flags = m.group(1).strip(), m.group(2), m.group(3).split(",")
        if _HIDDEN_IFACE.match(name):
            continue
        vlan = None
        vm = re.search(r"\.(\d+)$", name)
        if vm and parent:
            vlan = int(vm.group(1))
        result.append({
            "name": name,
            "ip": (ips.get(name) or [None])[0].split("/")[0] if ips.get(name) else None,
            "cidrs": ips.get(name, []),
            "up": "UP" in flags,
            "vlan": vlan,
            "parent": parent if parent and parent != "NONE" else None,
        })
    cache.set("capture:ifaces", result, 30)
    return result


def _get_ifaces() -> list[str]:
    try:
        return [i["name"] for i in list_ifaces()]
    except Exception as e:
        logger.warning("iface list failed: %s", e)
        return []


def _validate_iface(iface: str) -> str:
    iface = (iface or "any").strip()
    if iface == "any":
        return "any"
    allowed = _get_ifaces()
    if not allowed:
        raise TcpdumpError("Не удалось получить список интерфейсов (sniffer недоступен?)")
    if iface not in allowed:
        raise TcpdumpError(f"Интерфейс «{iface}» не найден. Доступны: any, {', '.join(sorted(allowed))}")
    return iface


def parse_summary(stderr: str) -> dict[str, int | None]:
    """'N packets captured / received by filter / dropped by kernel' из stderr tcpdump."""
    out: dict[str, int | None] = {"captured": None, "received": None, "kernel_dropped": None}
    for key, pat in (("captured", r"(\d+) packets? captured"),
                     ("received", r"(\d+) packets? received by filter"),
                     ("kernel_dropped", r"(\d+) packets? dropped by kernel")):
        m = re.search(pat, stderr or "")
        if m:
            out[key] = int(m.group(1))
    return out


def clean_stderr(stderr: str) -> str:
    """Убирает из stderr шум (promiscuous, verbose, listening, итоги)."""
    keep = []
    for line in (stderr or "").splitlines():
        low = line.lower()
        if not line.strip() or "promiscuous" in low or "verbose output" in low \
                or "listening on" in low or re.match(r"^\d+ packets? ", line):
            continue
        keep.append(line.replace("tcpdump: ", "", 1))
    return "\n".join(keep).strip()


def run_tcpdump(
    bpf: str = "",
    iface: str = "any",
    duration: int = DEFAULT_DURATION,
    packets: int = DEFAULT_PACKETS,
    dedup: bool = True,
) -> dict[str, Any]:
    """Разовый снимок: слушает до `duration` секунд или до `packets` пакетов."""
    bpf = _validate_bpf(bpf)
    iface = _validate_iface(iface)
    duration = max(1, min(int(duration), MAX_DURATION))
    packets = max(1, min(int(packets), MAX_PACKETS))

    cmd = ["tcpdump", "-nn", "-tttt", "-e", "-l", "-c", str(packets), "-i", iface]
    if bpf:
        cmd.append(bpf)
    wrapped = ["timeout", f"{duration}s"] + cmd

    from apps.core import ops_client
    started = time.monotonic()
    try:
        proc = ops_client.sniffer_run(wrapped, timeout=duration + 3)
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "bpf": bpf, "iface": iface, "duration": duration,
            "packets_requested": packets, "packets_captured": 0, "packets": [],
            "error": "tcpdump не завершился вовремя", "elapsed": round(time.monotonic() - started, 2),
            "timed_out": True,
        }
    elapsed = time.monotonic() - started
    stdout = proc.stdout or ""
    truncated = len(stdout) > MAX_OUTPUT
    if truncated:
        stdout = stdout[:MAX_OUTPUT].rsplit("\n", 1)[0]

    summary = parse_summary(proc.stderr)
    errors = clean_stderr(proc.stderr)
    started_ok = "listening on" in (proc.stderr or "")

    parsed = parse_tcpdump_output(stdout)
    dup = Deduplicator() if (dedup and iface == "any") else None
    if dup:
        parsed = dup.filter(parsed)
    for n, p in enumerate(parsed, 1):
        p["n"] = n

    ok = started_ok or bool(parsed)
    err_msg = None
    if not ok:
        err_msg = errors or f"tcpdump завершился с кодом {proc.returncode}"
    elif not parsed:
        err_msg = "За отведённое время пакетов не поймано"

    return {
        "ok": ok,
        "bpf": bpf,
        "iface": iface,
        "duration": duration,
        "packets_requested": packets,
        "packets_captured": summary["captured"] if summary["captured"] is not None else len(parsed),
        "kernel_dropped": summary["kernel_dropped"],
        "dup_dropped": dup.dropped if dup else 0,
        "packets": parsed,
        "error": err_msg,
        "elapsed": round(elapsed, 2),
        "truncated": truncated,
        "timed_out": proc.returncode == 124,
    }
