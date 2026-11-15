"""
SSE-стрим пакетов tcpdump в реальном времени.

Запускает `docker exec cerber-sniffer tcpdump -l` (line-buffered),
читает stdout построчно, парсит через tcpdump_parser и шлёт в SSE.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Generator

from .tcpdump import (
    MAX_DURATION,
    TcpdumpError,
    _validate_bpf,
    _validate_iface,
)

logger = logging.getLogger(__name__)

SNIFFER_CONTAINER = "cerber-sniffer"


def stream_tcpdump(
    bpf: str = "ip",
    iface: str = "any",
    duration: int = 60,
) -> Generator[str, None, None]:
    """
    Генератор SSE-событий. Каждое событие = JSON-пакет.
    Завершается по duration (макс 60 сек) или при ошибке.
    """
    from .tcpdump_parser import parse_tcpdump_output

    if not bpf:
        bpf = "ip"
    try:
        bpf = _validate_bpf(bpf)
        iface = _validate_iface(iface)
    except TcpdumpError as e:
        yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
        return

    # 0 = бесконечный (только по таймауту / закрытию клиента)
    duration = int(duration) if duration else 0
    if duration < 0:
        duration = 0

    cmd = [
        "docker", "exec", SNIFFER_CONTAINER,
        "tcpdump",
        "-nn", "-tttt", "-U", "-l", "-q",
        "-i", iface,
        bpf,
    ]
    # Оборачиваем в timeout — только если duration > 0
    if duration > 0:
        wrapped = ["timeout", f"{duration}s"] + cmd
    else:
        wrapped = cmd

    started = time.monotonic()

    # Padding 2 KB — daphne буферизует до ~1KB
    yield ": " + "x" * 2048 + "\n\n"
    yield f"event: start\ndata: {json.dumps({'bpf': bpf, 'iface': iface, 'duration': duration or None})}\n\n"

    try:
        proc = subprocess.Popen(
            wrapped,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
        )
    except FileNotFoundError as e:
        yield f"event: error\ndata: {json.dumps({'error': f'docker не найден: {e}'})}\n\n"
        return
    except Exception as e:
        yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
        return

    buffer = []
    buffer_size = 5  # батчим по 5 пакетов — меньше SSE-событий, проще
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            if not line:
                continue
            # tcpdump печатает многострочно с -A, но мы без -A — одна строка = пакет
            pkts = parse_tcpdump_output(line)
            buffer.extend(pkts)
            if len(buffer) >= buffer_size:
                payload = json.dumps({"packets": buffer})
                yield f"event: packets\ndata: {payload}\n\n"
                buffer = []

            # Проверим, не пора ли завершиться
            if duration > 0 and time.monotonic() - started > duration + 1:
                break
    except GeneratorExit:
        # Клиент закрыл соединение — прибиваем tcpdump
        pass
    finally:
        # Прибиваем процесс, если ещё жив
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # Остаток — только если клиент ещё подключён
    import json as _json  # локальный импорт — защита от closed-module при GeneratorExit
    try:
        if buffer:
            yield f"event: packets\ndata: {_json.dumps({'packets': buffer})}\n\n"
        elapsed = round(time.monotonic() - started, 2)
        yield f"event: done\ndata: {_json.dumps({'elapsed': elapsed})}\n\n"
    except (GeneratorExit, RuntimeError, StopIteration):
        return
