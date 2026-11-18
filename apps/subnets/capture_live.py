"""
Непрерывный захват трафика (live) без потерь между опросами.

Схема:
  браузер ──POST start──▶ web: поток-читатель ──HTTP stream──▶ cerber-ops ──docker exec──▶ tcpdump
  браузер ──GET poll(cursor)──▶ web ◀── Redis (буфер пакетов + метаданные сессии)

• Поток-читатель разбирает строки tcpdump и пачками кладёт пакеты в Redis-список.
• Сторож (второй поток) раз в секунду: сбрасывает накопленное, пишет heartbeat,
  останавливает захват по кнопке «Стоп», по таймеру, по лимиту пакетов
  и если браузер перестал опрашивать (закрыли вкладку).
• Опрос читает из Redis — работает при любом числе воркеров web.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from typing import Any

from django.conf import settings

from .tcpdump_parser import Deduplicator, parse_line

logger = logging.getLogger(__name__)

MAX_DURATION = 30 * 60          # сек — дольше одна сессия не живёт
DEFAULT_DURATION = 5 * 60
MAX_PACKETS = 1_000_000
BUFFER_LIMIT = 20_000           # пакетов в Redis на сессию (старые вытесняются)
POLL_LIMIT = 2_000              # пакетов за один опрос
CLIENT_GONE_AFTER = 120         # сек без опроса → браузер ушёл (фоновая вкладка опрашивает ~раз в минуту)
HEARTBEAT_STALE = 6             # сек без heartbeat → поток умер (рестарт web)
MAX_RUNNING = 4                 # одновременных сессий на сервер
KEY_TTL = 15 * 60               # сколько хранить данные после остановки

_PREFIX = "cerber:capture"


class CaptureError(Exception):
    """Ошибка запуска/доступа к сессии — текст для пользователя."""


def _redis():
    import redis
    url = settings.CACHES["default"]["LOCATION"]
    if isinstance(url, (list, tuple)):
        url = url[0]
    return redis.Redis.from_url(url, decode_responses=True)


def _k(sid: str, part: str) -> str:
    return f"{_PREFIX}:{sid}:{part}"


# ─────────────────────────────────────────────────────────────────────────────
class _Session:
    def __init__(self, sid: str, argv: list[str], duration: int, max_packets: int, dedup: bool):
        self.sid = sid
        self.argv = argv
        self.duration = duration
        self.max_packets = max_packets
        self.dedup = Deduplicator() if dedup else None
        self.r = _redis()
        self.lock = threading.Lock()
        self.buf: list[str] = []
        self.count = 0
        self.bytes = 0
        self.seq = 0
        self.resp = None
        self.stop_reason: str | None = None
        self.started = time.time()
        self.done = threading.Event()

    # -- запись в Redis ---------------------------------------------------
    def flush(self) -> None:
        with self.lock:
            if not self.buf:
                return
            batch, self.buf = self.buf, []
        pkts_key = _k(self.sid, "pkts")
        meta_key = _k(self.sid, "meta")
        p = self.r.pipeline(transaction=True)
        p.rpush(pkts_key, *batch)
        mapping = {"count": self.count, "bytes": self.bytes}
        if self.dedup is not None:
            mapping["dup_dropped"] = self.dedup.dropped
        p.hset(meta_key, mapping=mapping)
        p.llen(pkts_key)
        res = p.execute()
        overflow = res[-1] - BUFFER_LIMIT
        if overflow > 0:
            p = self.r.pipeline(transaction=True)
            p.ltrim(pkts_key, overflow, -1)
            p.hincrby(meta_key, "trimmed", overflow)
            p.execute()

    def meta(self, **fields: Any) -> None:
        self.r.hset(_k(self.sid, "meta"), mapping={k: ("" if v is None else v) for k, v in fields.items()})

    def request_stop(self, reason: str) -> None:
        if self.stop_reason is None:
            self.stop_reason = reason
        if self.resp is not None:
            from apps.core import ops_client
            ops_client.abort_stream(self.resp)

    # -- служебные строки tcpdump -----------------------------------------
    def _service_line(self, line: str) -> None:
        low = line.lower()
        if "listening on" in low:
            m = re.search(r"link-type (\S+)", line)
            self.meta(status="running", linktype=m.group(1) if m else "")
            return
        m = re.match(r"^(\d+) packets? (captured|received by filter|dropped by kernel)", line)
        if m:
            field = {"captured": "k_captured", "received by filter": "k_received",
                     "dropped by kernel": "k_dropped"}[m.group(2)]
            self.meta(**{field: int(m.group(1))})
            return
        if line.startswith("tcpdump:") and "promiscuous" not in low and "verbose output" not in low:
            self.meta(error=line.replace("tcpdump: ", "", 1)[:300])

    # -- потоки ---------------------------------------------------------------
    def reader(self) -> None:
        from apps.core import ops_client
        try:
            self.resp = ops_client.sniffer_stream_open(self.argv, timeout=self.duration + 5)
            while True:
                raw = self.resp.readline()
                if not raw:
                    break
                line = raw.decode(errors="replace").rstrip("\r\n")
                if not line:
                    continue
                pkt = parse_line(line)
                if pkt is None:
                    self._service_line(line)
                    continue
                if self.dedup is not None and self.dedup.is_dup(pkt):
                    continue
                self.seq += 1
                pkt["n"] = self.seq
                with self.lock:
                    self.buf.append(json.dumps(pkt, ensure_ascii=False, separators=(",", ":")))
                    self.count += 1
                    self.bytes += pkt.get("length") or 0
                    big = len(self.buf) >= 500
                if big:
                    self.flush()
                if self.max_packets and self.count >= self.max_packets:
                    self.request_stop("limit")
                    break
        except Exception as e:  # обрыв по abort_stream тоже приходит сюда
            if self.stop_reason is None:
                logger.warning("capture %s reader failed: %s", self.sid, e)
                self.meta(error=f"Поток прерван: {e}"[:300])
        finally:
            if self.stop_reason is None:
                elapsed = time.time() - self.started
                self.stop_reason = "timeout" if elapsed >= self.duration - 1 else "ended"
            self.done.set()

    def watchdog(self) -> None:
        meta_key = _k(self.sid, "meta")
        deadline = self.started + self.duration
        while not self.done.wait(1.0):
            try:
                self.flush()
                now = time.time()
                p = self.r.pipeline()
                p.hset(meta_key, "hb", now)
                p.exists(_k(self.sid, "stop"))
                p.hget(meta_key, "seen")
                _, stop_flag, seen = p.execute()
                if stop_flag:
                    self.request_stop("user")
                elif now >= deadline + 2:
                    self.request_stop("timeout")
                elif seen and now - float(seen) > CLIENT_GONE_AFTER:
                    self.request_stop("client_gone")
            except Exception as e:
                logger.warning("capture %s watchdog: %s", self.sid, e)
        self.finish()

    def finish(self) -> None:
        try:
            self.flush()
            if self.dedup is not None:
                self.meta(dup_dropped=self.dedup.dropped)
            self.meta(status="stopped", stop_reason=self.stop_reason or "ended",
                      ended=time.time(), count=self.count, bytes=self.bytes)
            p = self.r.pipeline()
            for part in ("meta", "pkts", "stop"):
                p.expire(_k(self.sid, part), KEY_TTL)
            p.srem(f"{_PREFIX}:running", self.sid)
            p.execute()
        except Exception as e:
            logger.warning("capture %s finish: %s", self.sid, e)


# ─────────────────────────────────────────────────────────────────────────────
def _cleanup_running(r) -> int:
    """Убирает из множества «running» сессии, чей поток умер (нет heartbeat)."""
    alive = 0
    now = time.time()
    for sid in r.smembers(f"{_PREFIX}:running"):
        hb, status = r.hmget(_k(sid, "meta"), "hb", "status")
        started = r.hget(_k(sid, "meta"), "started")
        last = float(hb or started or 0)
        if status == "stopped" or now - last > HEARTBEAT_STALE + 5:
            r.srem(f"{_PREFIX}:running", sid)
        else:
            alive += 1
    return alive


def start(user_id: int, *, iface: str, bpf: str, duration: int, max_packets: int, dedup: bool = True) -> dict:
    """Запускает сессию. iface и bpf должны быть уже проверены вызывающим."""
    duration = max(5, min(int(duration or DEFAULT_DURATION), MAX_DURATION))
    max_packets = max(0, min(int(max_packets or 0), MAX_PACKETS))
    r = _redis()

    # одна сессия на пользователя: прежнюю останавливаем
    user_key = f"{_PREFIX}:user:{user_id}"
    prev = r.get(user_key)
    if prev:
        r.set(_k(prev, "stop"), 1, ex=KEY_TTL)

    if _cleanup_running(r) >= MAX_RUNNING:
        raise CaptureError(f"Сейчас идёт {MAX_RUNNING} захвата — дождитесь окончания одного из них")

    argv = ["timeout", f"{duration}s", "tcpdump", "-nn", "-tttt", "-e", "-l", "-i", iface]
    if bpf:
        argv.append(bpf)

    sid = uuid.uuid4().hex
    now = time.time()
    r.hset(_k(sid, "meta"), mapping={
        "user": user_id, "iface": iface, "bpf": bpf, "duration": duration,
        "max_packets": max_packets, "dedup": int(dedup), "status": "starting",
        "started": now, "hb": now, "seen": now, "count": 0, "bytes": 0, "trimmed": 0,
    })
    r.expire(_k(sid, "meta"), MAX_DURATION + KEY_TTL)
    r.sadd(f"{_PREFIX}:running", sid)
    r.set(user_key, sid, ex=MAX_DURATION + KEY_TTL)

    s = _Session(sid, argv, duration, max_packets, dedup)
    threading.Thread(target=s.reader, name=f"capture-{sid[:6]}-r", daemon=True).start()
    threading.Thread(target=s.watchdog, name=f"capture-{sid[:6]}-w", daemon=True).start()
    logger.info("capture %s started by user %s: iface=%s bpf=%r dur=%s", sid, user_id, iface, bpf, duration)
    return {"sid": sid, "iface": iface, "bpf": bpf, "duration": duration, "max_packets": max_packets}


def _own_meta(r, sid: str, user_id: int) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", sid or ""):
        raise CaptureError("Некорректный идентификатор сессии")
    meta = r.hgetall(_k(sid, "meta"))
    if not meta:
        raise CaptureError("Сессия не найдена или истекла")
    if str(meta.get("user")) != str(user_id):
        raise CaptureError("Это чужая сессия захвата")
    return meta


def poll(user_id: int, sid: str, cursor: int, limit: int = POLL_LIMIT) -> dict:
    """Пакеты с порядкового номера cursor (0-based) + состояние сессии."""
    r = _redis()
    meta = _own_meta(r, sid, user_id)
    now = time.time()
    if meta.get("status") != "stopped":
        r.hset(_k(sid, "meta"), "seen", now)
        if now - float(meta.get("hb") or 0) > HEARTBEAT_STALE:
            meta["status"] = "stopped"
            meta["stop_reason"] = "lost"
            r.hset(_k(sid, "meta"), mapping={"status": "stopped", "stop_reason": "lost"})
            r.srem(f"{_PREFIX}:running", sid)

    limit = max(1, min(int(limit), POLL_LIMIT))
    cursor = max(0, int(cursor))
    p = r.pipeline(transaction=True)
    p.hget(_k(sid, "meta"), "trimmed")
    p.llen(_k(sid, "pkts"))
    trimmed, length = p.execute()
    trimmed = int(trimmed or 0)
    start_idx = max(cursor - trimmed, 0)
    skipped = max(trimmed - cursor, 0)
    raw = r.lrange(_k(sid, "pkts"), start_idx, start_idx + limit - 1)
    packets = [json.loads(x) for x in raw]
    next_cursor = trimmed + start_idx + len(packets)

    def _num(key, cast=int):
        v = meta.get(key)
        try:
            return cast(v) if v not in (None, "") else None
        except ValueError:
            return None

    return {
        "ok": True,
        "sid": sid,
        "status": meta.get("status"),
        "stop_reason": meta.get("stop_reason") or None,
        "error": meta.get("error") or None,
        "iface": meta.get("iface"),
        "bpf": meta.get("bpf"),
        "linktype": meta.get("linktype") or None,
        "started": _num("started", float),
        "ended": _num("ended", float),
        "duration": _num("duration"),
        "max_packets": _num("max_packets"),
        "count": _num("count") or 0,
        "bytes": _num("bytes") or 0,
        "kernel_dropped": _num("k_dropped"),
        "dup_dropped": _num("dup_dropped"),
        "packets": packets,
        "cursor": next_cursor,
        "skipped": skipped,
        "more": (trimmed + length) > next_cursor,
    }


def stop(user_id: int, sid: str) -> dict:
    r = _redis()
    _own_meta(r, sid, user_id)
    r.set(_k(sid, "stop"), 1, ex=KEY_TTL)
    return {"ok": True, "sid": sid}
