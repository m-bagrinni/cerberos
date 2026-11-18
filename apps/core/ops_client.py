"""
Клиент к сервису cerber-ops (ops/agent.py) — вместо прямого `docker ...`.

web и воркеры больше не видят docker.sock: всё, что раньше делалось через
`docker exec cerber-sniffer ...` и `docker ps/restart`, идёт сюда по HTTP
внутри docker-сети. ops сам проверяет argv по белому списку.

    ops_client.sniffer_run(["ip", "-o", "-4", "a"], timeout=5)  -> Result(.returncode/.stdout/.stderr)
    ops_client.sniffer_stream(["tcpdump", ...], timeout=60)     -> итератор строк
    ops_client.containers()                                     -> [{"name","status","state"}, ...]
    ops_client.container_action(name, "restart")                -> dict
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterator

logger = logging.getLogger(__name__)

OPS_URL = os.environ.get("OPS_URL", "http://ops:9100").rstrip("/")
# Запас поверх времени самой команды: сеть + docker exec
_HTTP_SLACK = 10


class OpsError(RuntimeError):
    pass


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def _token() -> str:
    tok = os.environ.get("OPS_TOKEN", "")
    if not tok:
        raise OpsError("OPS_TOKEN не задан")
    return tok


def _request(method: str, path: str, payload: dict | None, timeout: float):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        OPS_URL + path, data=data, method=method,
        headers={"X-Ops-Token": _token(), "Content-Type": "application/json"},
    )
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read() or b"{}").get("error") or str(e)
        except ValueError:
            msg = str(e)
        raise OpsError(msg) from e
    except (urllib.error.URLError, OSError) as e:
        raise OpsError(f"cerber-ops недоступен: {e}") from e


def _json(method: str, path: str, payload: dict | None = None, timeout: float = 15) -> dict:
    with _request(method, path, payload, timeout) as r:
        return json.loads(r.read() or b"{}")


def sniffer_run(argv: list[str], timeout: int = 30) -> Result:
    """
    Выполнить команду в sniffer. При превышении timeout — как subprocess:
    бросает subprocess.TimeoutExpired (с тем, что успели прочитать).
    """
    d = _json("POST", "/sniffer/run", {"argv": argv, "timeout": int(timeout)},
              timeout=timeout + _HTTP_SLACK)
    res = Result(d.get("returncode", -1), d.get("stdout", ""), d.get("stderr", ""), d.get("timed_out", False))
    if res.timed_out:
        raise subprocess.TimeoutExpired(argv, timeout, output=res.stdout, stderr=res.stderr)
    return res


def sniffer_stream(argv: list[str], timeout: int = 3600) -> Iterator[str]:
    """Построчный stdout+stderr команды. Закрытие итератора обрывает соединение → ops убивает процесс."""
    resp = _request("POST", "/sniffer/stream", {"argv": argv, "timeout": int(timeout)},
                    timeout=timeout + _HTTP_SLACK)
    try:
        for raw in resp:
            yield raw.decode(errors="replace")
    finally:
        resp.close()


def sniffer_stream_open(argv: list[str], timeout: int = 3600):
    """
    Как sniffer_stream, но отдаёт сам HTTP-ответ: построчное чтение — `resp.readline()`,
    а оборвать чтение из другого потока можно через `abort_stream(resp)`.
    """
    return _request("POST", "/sniffer/stream", {"argv": argv, "timeout": int(timeout)},
                    timeout=timeout + _HTTP_SLACK)


def abort_stream(resp) -> None:
    """Рвёт соединение потока (из любого потока): ops видит обрыв и убивает процесс."""
    import socket
    sock = None
    try:
        sock = resp.fp.raw._sock          # http.client.HTTPResponse → SocketIO → socket
    except AttributeError:
        pass
    try:
        if sock is not None:
            sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        resp.close()
    except Exception:
        pass


def containers() -> list[dict]:
    return _json("GET", "/containers").get("containers", [])


def container_action(name: str, action: str) -> dict:
    return _json("POST", "/containers/action", {"name": name, "action": action}, timeout=75)
