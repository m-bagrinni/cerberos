"""
cerber-ops — единственный контейнер с доступом к docker.sock.

Зачем: раньше web (опубликован в интернет) был privileged, с pid: host и
docker.sock — любая уязвимость в Django = root на хосте. Теперь web и воркеры
просят ops выполнить ОДНУ ИЗ ЗАРАНЕЕ ОПИСАННЫХ операций:

  GET  /health                       — проверка живости (без токена)
  GET  /containers                   — контейнеры проекта (cerber-*)
  POST /containers/action            — {"name": "...", "action": start|stop|restart}
  POST /sniffer/run                  — {"argv": [...], "timeout": N} → rc/stdout/stderr
  POST /sniffer/stream               — {"argv": [...], "timeout": N} → поток строк stdout

argv выполняется как `docker exec cerber-sniffer <argv>` только если проходит
белый список (validate_argv): фиксированные программы, ключи и форматы значений.
Произвольной команды, shell, `docker run`, чужих контейнеров — нет.

Все запросы, кроме /health, — с заголовком X-Ops-Token (OPS_TOKEN из .env).
Только stdlib: никаких зависимостей, код короткий и читается целиком.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
import re
import select
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("cerber-ops")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)

TOKEN = os.environ.get("OPS_TOKEN", "")
SNIFFER = "cerber-sniffer"
PROJECT_PREFIX = "cerber-"
MAX_TIMEOUT = 3600          # один вызов дольше часа не живёт
MAX_OUTPUT = 4 * 1024 * 1024
MAX_BODY = 64 * 1024


class Reject(ValueError):
    pass


# ── Белый список команд в sniffer ─────────────────────────────────────────
_IFACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,31}$")
_PCAP = re.compile(r"^/tmp/cerber_pcap/[A-Za-z0-9_]{1,80}\.pcap$")
_OID = re.compile(r"^\.?[0-9]+(\.[0-9]+){1,40}$")
_COMMUNITY = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
# BPF: буквы/цифры/операторы; в tcpdump это один аргумент-выражение, не ключ.
_BPF = re.compile(r"^[A-Za-z0-9 .:/_()!&|<>=\[\]-]{1,300}$")

_EXACT = {
    ("ip", "-o", "link"),
    ("ip", "-o", "-4", "a"),
    ("ip", "neigh", "show"),
    ("find", "/tmp/cerber_pcap", "-name", "count_*.pcap", "-mmin", "+30", "-delete"),
    ("find", "/tmp/cerber_pcap", "-name", "*.pcap", "-mmin", "+60", "-delete"),
    ("cat", "/usr/share/nmap/nmap-mac-prefixes"),   # база производителей по MAC (OUI)
}
_TCPDUMP_FLAGS = {"-nn", "-tttt", "-U", "-l", "-A", "-e", "-q", "-v"}
_NMAP_SCRIPTS = {("445", "smb-os-discovery"), ("137", "nbstat.nse")}


def _host(v: str) -> str:
    try:
        ip = ipaddress.ip_address(v)
    except ValueError:
        raise Reject(f"not an IP: {v!r}")
    return str(ip)


def _validate_tcpdump(args: list[str]) -> None:
    i, bpf_seen = 0, False
    while i < len(args):
        a = args[i]
        if a in _TCPDUMP_FLAGS:
            i += 1
            continue
        if a in ("-i", "-c", "-w", "-r"):
            if i + 1 >= len(args):
                raise Reject(f"{a} needs a value")
            v = args[i + 1]
            if a == "-i" and not _IFACE.match(v):
                raise Reject(f"bad iface {v!r}")
            if a == "-c" and not (v.isdigit() and 1 <= int(v) <= 100000):
                raise Reject(f"bad count {v!r}")
            if a in ("-w", "-r") and not _PCAP.match(v):
                raise Reject(f"bad pcap path {v!r}")
            i += 2
            continue
        # иначе — BPF-выражение: ровно одно, последним аргументом
        if bpf_seen or i != len(args) - 1 or a.startswith("-") or not _BPF.match(a):
            raise Reject(f"bad tcpdump arg {a!r}")
        bpf_seen = True
        i += 1


def validate_argv(argv: list) -> list[str]:
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise Reject("argv must be a non-empty list of strings")
    if len(argv) > 40:
        raise Reject("argv too long")
    if any("\x00" in a or "\n" in a for a in argv):
        raise Reject("control chars in argv")

    if argv[0] == "timeout":  # timeout Ns <команда>; вложенный timeout не нужен
        if len(argv) < 3 or not re.fullmatch(r"[0-9]{1,4}s", argv[1]) or not 1 <= int(argv[1][:-1]) <= MAX_TIMEOUT:
            raise Reject("bad timeout")
        if argv[2] == "timeout":
            raise Reject("nested timeout")
        return argv[:2] + validate_argv(argv[2:])

    if tuple(argv) in _EXACT:
        return argv
    prog, args = argv[0], argv[1:]
    if prog == "tcpdump":
        _validate_tcpdump(args)
        return argv
    if prog == "rm" and len(args) == 2 and args[0] == "-f" and _PCAP.match(args[1]):
        return argv
    if prog == "nmap" and len(args) == 5 and args[0] == "-p" and args[2] == "--script" \
            and (args[1], args[3]) in _NMAP_SCRIPTS:
        _host(args[4])
        return argv
    if prog == "snmpget" and len(args) == 6 and args[0] == "-v2c" and args[1] == "-c" \
            and _COMMUNITY.match(args[2]) and args[3] == "-Oqv" and _OID.match(args[5]):
        _host(args[4])
        return argv
    raise Reject(f"command not allowed: {prog!r}")


# Фиксированный скрипт: печатает PID и заменяется командой из позиционных параметров.
_PID_WRAPPER = 'echo "CERBER_PID $$"; exec "$@"'


def _docker_exec(argv: list[str], timeout: int) -> list[str]:
    # Убийство клиента `docker exec` НЕ завершает процесс внутри контейнера,
    # поэтому всё, что запускаем в sniffer, ограничено timeout(1) там же.
    if argv[0] != "timeout":
        argv = ["timeout", f"{timeout}s", *argv]
    return ["docker", "exec", SNIFFER, *argv]


# ── Контейнеры проекта ───────────────────────────────────────────────────
def list_containers() -> list[dict]:
    r = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}|{{.State}}"],
        capture_output=True, text=True, timeout=10,
    )
    out = []
    for line in r.stdout.splitlines():
        parts = line.split("|")
        if len(parts) >= 3 and parts[0].startswith(PROJECT_PREFIX):
            out.append({"name": parts[0], "status": parts[1], "state": parts[2]})
    return out


# ── HTTP ─────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "cerber-ops"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    # helpers
    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        got = self.headers.get("X-Ops-Token", "")
        if TOKEN and hmac.compare_digest(got.encode(), TOKEN.encode()):
            return True
        self._json(401, {"ok": False, "error": "bad token"})
        return False

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise Reject("body too large")
        data = json.loads(self.rfile.read(n) or b"{}")
        if not isinstance(data, dict):
            raise Reject("body must be an object")
        return data

    @staticmethod
    def _timeout(data: dict, default: int = 30) -> int:
        try:
            t = int(data.get("timeout", default))
        except (TypeError, ValueError):
            raise Reject("bad timeout")
        return max(1, min(t, MAX_TIMEOUT))

    # routes
    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"ok": True})
        if not self._authed():
            return
        if self.path == "/containers":
            try:
                return self._json(200, {"ok": True, "containers": list_containers()})
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"ok": False, "error": str(e)})
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self._authed():
            return
        try:
            data = self._body()
            if self.path == "/containers/action":
                return self._container_action(data)
            if self.path == "/sniffer/run":
                return self._sniffer_run(data)
            if self.path == "/sniffer/stream":
                return self._sniffer_stream(data)
            self._json(404, {"ok": False, "error": "not found"})
        except Reject as e:
            log.warning("rejected %s: %s", self.path, e)
            self._json(400, {"ok": False, "error": f"rejected: {e}"})
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "error": "bad json"})

    def _container_action(self, data: dict):
        name, action = data.get("name"), data.get("action")
        if action not in ("start", "stop", "restart"):
            raise Reject("bad action")
        if name not in {c["name"] for c in list_containers()}:
            raise Reject("unknown container")
        log.info("container %s %s", action, name)
        r = subprocess.run(["docker", action, name], capture_output=True, text=True, timeout=60)
        self._json(200, {"ok": r.returncode == 0, "returncode": r.returncode,
                         "stdout": r.stdout.strip(), "stderr": r.stderr.strip()})

    def _sniffer_run(self, data: dict):
        argv = validate_argv(data.get("argv"))
        timeout = self._timeout(data)
        try:
            r = subprocess.run(_docker_exec(argv, timeout), capture_output=True, timeout=timeout + 2)
            rc, out, err, timed_out = r.returncode, r.stdout, r.stderr, False
        except subprocess.TimeoutExpired as e:
            rc, out, err, timed_out = -1, e.stdout or b"", e.stderr or b"", True
        self._json(200, {
            "ok": True, "returncode": rc, "timed_out": timed_out,
            "stdout": out[:MAX_OUTPUT].decode(errors="replace"),
            "stderr": err[:MAX_OUTPUT].decode(errors="replace"),
        })

    def _sniffer_stream(self, data: dict):
        """
        Построчно отдаёт stdout. Обрыв соединения клиентом → процесс в sniffer убивается.

        Убить клиента `docker exec` мало — процесс в контейнере продолжит жить.
        Поэтому запускаем через фиксированный sh-скрипт, который печатает свой PID
        первой строкой и делает exec команды (argv — позиционные параметры, не код).
        BusyBox timeout выполняет команду в том же процессе, так что этот PID —
        PID самого tcpdump. Его и убиваем при обрыве.
        """
        argv = validate_argv(data.get("argv"))
        timeout = self._timeout(data, default=MAX_TIMEOUT)
        deadline = time.monotonic() + timeout + 2
        cmd = _docker_exec(argv, timeout)            # docker exec SNIFFER timeout Ns ...
        cmd = cmd[:3] + ["sh", "-c", _PID_WRAPPER, "sh", *cmd[3:]]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
        watchdog = threading.Timer(max(0.0, deadline - time.monotonic()), proc.kill)
        watchdog.daemon = True
        watchdog.start()
        inner_pid = None
        first = proc.stdout.readline()
        m = re.fullmatch(rb"CERBER_PID (\d{1,9})\s*", first)
        if m:
            inner_pid = m.group(1).decode()
            first = b""
        killed = threading.Lock()

        def stop_inner():
            """Убивает tcpdump в sniffer и клиента docker exec (идемпотентно)."""
            if not killed.acquire(blocking=False):
                return
            if proc.poll() is None and inner_pid:
                # docker exec ещё жив → процесс в контейнере тоже, PID не переиспользован
                try:
                    subprocess.run(["docker", "exec", SNIFFER, "kill", "-TERM", inner_pid],
                                   capture_output=True, timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            if proc.poll() is None:
                proc.kill()

        def watch_client():
            """При тихом фильтре строк нет и BrokenPipe не случится — следим за сокетом сами."""
            sock = self.connection
            while proc.poll() is None:
                try:
                    r, _, _ = select.select([sock], [], [], 0.5)
                    if r and not sock.recv(1, socket.MSG_PEEK):
                        stop_inner()
                        return
                except OSError:
                    stop_inner()
                    return

        threading.Thread(target=watch_client, daemon=True).start()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            if first:
                self.wfile.write(b"%x\r\n%s\r\n" % (len(first), first))
            for line in iter(proc.stdout.readline, b""):
                self.wfile.write(b"%x\r\n%s\r\n" % (len(line), line))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            watchdog.cancel()
            stop_inner()
            proc.wait(timeout=5)
            self.close_connection = True


def main() -> None:
    if not TOKEN or len(TOKEN) < 32:
        log.error("OPS_TOKEN не задан или короче 32 символов — отказываюсь запускаться")
        sys.exit(1)
    port = int(os.environ.get("OPS_PORT", "9100"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    log.info("cerber-ops listening on :%s", port)
    srv.serve_forever()


if __name__ == "__main__":
    main()
