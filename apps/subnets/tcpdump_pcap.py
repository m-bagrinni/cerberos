"""
Захват трафика в .pcap через sniffer-контейнер (для Wireshark).

Файлы пишутся в общий volume /tmp/cerber_pcap (pcap_data), доступный и web,
и sniffer. TTL — 1 час (удаляет cerber-ops фиксированной командой).
"""
from __future__ import annotations

import logging
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .tcpdump import _validate_bpf, _validate_iface, clean_stderr, parse_summary

logger = logging.getLogger(__name__)

PCAP_DIR = Path("/tmp/cerber_pcap")
PCAP_DIR.mkdir(parents=True, exist_ok=True)
TTL_SECONDS = 3600

MAX_DURATION = 300
DEFAULT_DURATION = 30
MAX_PACKETS = 200_000
DEFAULT_PACKETS = 10_000
PCAP_HEADER = 24


def _cleanup_old_files() -> None:
    """Файлы создаёт root внутри sniffer — удаляем тоже через cerber-ops (фиксированная команда)."""
    from apps.core import ops_client
    try:
        ops_client.sniffer_run(
            ["find", "/tmp/cerber_pcap", "-name", "*.pcap", "-mmin", "+60", "-delete"], timeout=15,
        )
    except Exception as e:  # очистка не должна ломать захват
        logger.warning("pcap cleanup failed: %s", e)
    # .meta пишет сам web — их удаляем здесь
    cutoff = time.time() - TTL_SECONDS
    for meta in PCAP_DIR.glob("*.meta"):
        try:
            if meta.stat().st_mtime < cutoff:
                meta.unlink()
        except OSError:
            pass


def download_name(file_id: str) -> str:
    """Имя для скачивания: capture_<iface>_<время>.pcap (метаданные — в .meta рядом)."""
    meta = PCAP_DIR / f"{file_id}.meta"
    try:
        return meta.read_text().strip() or f"capture_{file_id}.pcap"
    except OSError:
        return f"capture_{file_id}.pcap"


def run_tcpdump_pcap(
    bpf: str = "",
    iface: str = "any",
    duration: int = DEFAULT_DURATION,
    packets: int = DEFAULT_PACKETS,
) -> dict[str, Any]:
    """Пишет трафик в .pcap до `duration` секунд или `packets` пакетов."""
    _cleanup_old_files()

    bpf = _validate_bpf(bpf)
    iface = _validate_iface(iface)
    duration = max(1, min(int(duration), MAX_DURATION))
    packets = max(1, min(int(packets), MAX_PACKETS))

    file_id = uuid.uuid4().hex[:16]
    pcap_path = PCAP_DIR / f"{file_id}.pcap"
    remote_path = f"/tmp/cerber_pcap/{file_id}.pcap"   # тот же volume внутри sniffer

    cmd = ["tcpdump", "-nn", "-U", "-i", iface, "-w", remote_path, "-c", str(packets)]
    if bpf:
        cmd.append(bpf)
    wrapped = ["timeout", f"{duration}s"] + cmd

    from apps.core import ops_client
    started = time.monotonic()
    try:
        proc = ops_client.sniffer_run(wrapped, timeout=duration + 5)
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "file_id": None, "bpf": bpf, "iface": iface,
            "duration": duration, "packets_requested": packets, "packets_captured": 0,
            "file_size": 0, "error": "tcpdump не завершился вовремя",
            "elapsed": round(time.monotonic() - started, 2), "timed_out": True,
        }
    elapsed = time.monotonic() - started

    summary = parse_summary(proc.stderr)
    errors = clean_stderr(proc.stderr)
    started_ok = "listening on" in (proc.stderr or "")
    file_size = pcap_path.stat().st_size if pcap_path.exists() else 0
    has_packets = file_size > PCAP_HEADER

    err_msg = None
    if not started_ok:
        err_msg = errors or f"tcpdump завершился с кодом {proc.returncode}"
    elif not has_packets:
        err_msg = "Пакетов не поймано — файл пустой"

    if has_packets:
        safe_iface = iface.replace("/", "_")
        name = f"capture_{safe_iface}_{datetime.now():%Y%m%d_%H%M%S}.pcap"
        try:
            (PCAP_DIR / f"{file_id}.meta").write_text(name)
        except OSError:
            pass  # имя по умолчанию тоже годится

    return {
        "ok": started_ok and has_packets,
        "file_id": file_id if has_packets else None,
        "bpf": bpf,
        "iface": iface,
        "duration": duration,
        "packets_requested": packets,
        "packets_captured": summary["captured"] or 0,
        "kernel_dropped": summary["kernel_dropped"],
        "file_size": file_size,
        "error": err_msg,
        "elapsed": round(elapsed, 2),
        "timed_out": proc.returncode == 124,
    }
