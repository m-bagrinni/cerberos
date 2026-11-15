"""
Защита периодических задач от наложения.

Beat ставит задачу по расписанию независимо от того, закончился ли прошлый прогон.
Если прогон дольше интервала (например, шлюз Olencom не отвечает и каждый snmpget
ждёт таймаут), прогоны накладываются, долбят и без того хрупкое устройство и
пишут в БД конкурирующие статусы.

    @shared_task(name="monitoring.poll_x")
    @singleton(ttl=300)
    def poll_x(): ...

    @shared_task(name="monitoring.poll_y_task")
    @singleton(ttl=600, key=lambda ip: ip)       # отдельный замок на каждое устройство
    def poll_y_task(ip): ...

Замок — ключ в общем Redis-кэше (cache.add атомарен). TTL — страховка на случай,
если воркер умер посреди задачи: ставьте его больше time_limit задачи.
"""
from __future__ import annotations

import functools
import logging
import uuid
from contextlib import contextmanager

from django.core.cache import cache

log = logging.getLogger(__name__)


@contextmanager
def single_run(name: str, ttl: int):
    """Контекст-менеджер: yield True, если замок взят; False — уже кто-то работает."""
    key = f"lock:{name}"
    token = uuid.uuid4().hex
    try:
        acquired = cache.add(key, token, ttl)
    except Exception as e:  # Redis недоступен — лучше выполнить, чем молча пропустить
        log.warning("lock %s: кэш недоступен (%s) — выполняем без замка", name, e)
        yield True
        return
    try:
        yield acquired
    finally:
        if acquired:
            try:
                if cache.get(key) == token:
                    cache.delete(key)
            except Exception:
                pass


def singleton(ttl: int, key=None):
    """Декоратор: пропускает запуск, если предыдущий с тем же ключом ещё идёт."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # bind=True задачи получают self первым аргументом — его в ключ не берём
            call_args = args[1:] if args and hasattr(args[0], "request") else args
            suffix = f":{key(*call_args, **kwargs)}" if key else ""
            name = f"{fn.__module__}.{fn.__name__}{suffix}"
            with single_run(name, ttl) as ok:
                if not ok:
                    log.warning("%s: предыдущий запуск ещё идёт — пропуск", name)
                    return {"skipped": "already running"}
                return fn(*args, **kwargs)
        return wrapper
    return deco
