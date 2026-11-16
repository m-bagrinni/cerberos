"""
Захват трафика в .pcap через sniffer-контейнер (для скачивания и Wireshark).

Файлы пишутся в общий volume /tmp/cerber_pcap (pcap_data),
доступный и в web, и в sniffer. TTL — 1 час.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from .tcpdump import (
    DEFAULT_DURATION,
    DEFAULT_PACKETS,
    MAX_DURATION,
    MAX_PACKETS,
    TcpdumpError,
    _validate_bpf,
    _validate_iface,
)

logger = logging.getLogger(__name__)

PCAP_DIR = Path("/tmp/cerber_pcap")
PCAP_DIR.mkdir(parents=True, exist_ok=True)
TTL_SECONDS = 3600

SNIFFER_CONTAINER = "cerber-sniffer"


def _cleanup_old_files() -> None:
    """
    Удаляет pcap-файлы старше часа. Файлы создаёт root внутри sniffer —
    web (uid 1000) удалить их не может, поэтому удаление тоже через cerber-ops.
    Команда фиксированная (TTL = 60 мин) — так её пропускает белый список ops.
    """
    from apps.core import ops_client
    try:
        ops_client.sniffer_run(
            ["find", "/tmp/cerber_pcap", "-name", "*.pcap", "-mmin", "+60", "-delete"], timeout=15,
        )
    except Exception as e:  # очистка не должна ломать захват
        logger.warning("pcap cleanup failed: %s", e)


def run_tcpdump_pcap(
    bpf: str = "ip",
    iface: str = "any",
    duration: int = DEFAULT_DURATION,
    packets: int = DEFAULT_PACKETS,
) -> dict[str, Any]:
    """
    Захватывает трафик в .pcap через sniffer-контейнер.
    Возвращает dict с file_id, file_size, packets_captured, download_url.
    """

    _cleanup_old_files()

    bpf = _validate_bpf(bpf)
    iface = _validate_iface(iface)
    duration = max(1, min(int(duration), MAX_DURATION))
    packets = max(1, min(int(packets), MAX_PACKETS))

    file_id = uuid.uuid4().hex[:16]
    pcap_path = PCAP_DIR / f"{file_id}.pcap"

    # Файл пишется ВНУТРИ sniffer (в общий volume /tmp/cerber_pcap)
    remote_path = f"/tmp/cerber_pcap/{file_id}.pcap"

    cmd = [
        "tcpdump",
        "-nn", "-U",
        "-i", iface,
        "-w", remote_path,
    ]
    if packets:
        cmd.extend(["-c", str(packets)])
    cmd.append(bpf)

    # Гарантированно убиваем через duration секунд
    wrapped = ["timeout", f"{duration}s"] + cmd

    started = time.monotonic()
    try:
        from apps.core import ops_client
        proc = ops_client.sniffer_run(wrapped, timeout=duration + 3)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        timed_out = (proc.returncode == 124)
    except subprocess.TimeoutExpired as e:
        elapsed = time.monotonic() - started
        return {
            "ok": False,
            "file_id": None,
            "bpf": bpf, "iface": iface,
            "duration": duration, "packets_requested": packets,
            "packets_captured": 0, "file_size": 0,
            "error": "timeout(1) не завершил tcpdump",
            "elapsed": round(elapsed, 2),
            "timed_out": True,
        }

    elapsed = time.monotonic() - started

    # Считаем пакеты из stderr
    captured = 0
    m = re.search(r"(\d+)\s+packets?\s+captured", stderr)
    if m:
        captured = int(m.group(1))

    # Фильтр WARNING
    if stderr:
        stderr = "\n".join(
            line for line in stderr.splitlines()
            if "doesn't support promiscuous mode" not in line
            and "Promiscuous mode not supported" not in line
        ).strip()

    started_ok = "listening on" in stderr or "listening on" in stdout
    file_size = pcap_path.stat().st_size if pcap_path.exists() else 0
    has_packets = file_size > 24  # pcap-заголовок = 24 байта

    ok = has_packets or (started_ok and not timed_out) or started_ok

    err_msg = None
    if not ok and not started_ok:
        err_msg = stderr or f"tcpdump завершился с кодом {proc.returncode}"
    elif file_size <= 24:
        err_msg = "Пакетов не поймано (pcap содержит только заголовок)"

    return {
        "ok": ok,
        "file_id": file_id if has_packets else None,
        "bpf": bpf,
        "iface": iface,
        "duration": duration,
        "packets_requested": packets,
        "packets_captured": captured,
        "file_size": file_size,
        "error": err_msg,
        "elapsed": round(elapsed, 2),
        "timed_out": timed_out,
    }
