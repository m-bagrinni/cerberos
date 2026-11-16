"""
Сторожевой сигнал: живы ли beat и воркеры Celery.

Как работает:
- beat раз в минуту кладёт задачу core.heartbeat в каждую очередь (CELERY_BEAT_SCHEDULE в cerber/celery.py);
- воркер, выполнив её, пишет в Redis время: hb:<очередь>;
- процесс Telegram-бота (отдельный контейнер) раз в 2 минуты вызывает check_and_notify():
  если очередь молчит дольше STALE_SEC — сообщение администраторам, после восстановления — ещё одно.

Если молчат все очереди сразу — скорее всего упал beat (или Redis/брокер).
"""
import logging
import time

from django.core.cache import cache

logger = logging.getLogger(__name__)

QUEUES = ("critical", "high", "default", "low", "traffic")
STALE_SEC = 5 * 60
STATE_KEY = "hb:down"           # множество очередей, о которых уже сообщили
STARTED_KEY = "hb:watch_started"


def beat_key(queue: str) -> str:
    return f"hb:{queue}"


def mark(queue: str) -> None:
    cache.set(beat_key(queue), time.time(), timeout=24 * 3600)


def status(now: float | None = None) -> dict:
    """{очередь: секунд с последнего пульса или None, если пульса не было}."""
    now = now or time.time()
    out = {}
    for q in QUEUES:
        ts = cache.get(beat_key(q))
        out[q] = None if ts is None else int(now - ts)
    return out


def _recipients():
    from apps.accounts.access import Access
    from apps.accounts.models import User
    qs = User.objects.filter(is_active=True, telegram_id__isnull=False)
    return [u.telegram_id for u in qs if u.is_superuser or Access.for_user(u).has_cap("users_manage")]


def _send(text: str) -> None:
    from apps.accounts.telegram_notify import send_telegram_message
    for tg_id in _recipients():
        send_telegram_message(tg_id, text)


def check_and_notify(now: float | None = None) -> set:
    """Возвращает множество «молчащих» очередей. Сообщает только об изменениях."""
    now = now or time.time()
    # Не поднимаем тревогу первые STALE_SEC после старта наблюдателя (пульсы ещё не пришли)
    cache.add(STARTED_KEY, now, timeout=None)
    if now - (cache.get(STARTED_KEY) or now) < STALE_SEC:
        return set()

    st = status(now)
    down = {q for q, age in st.items() if age is None or age > STALE_SEC}
    was = set(cache.get(STATE_KEY) or [])
    new_down, recovered = down - was, was - down

    if new_down:
        if down == set(QUEUES):
            text = ("🔴 <b>Мониторинг остановлен</b>\n"
                    "Ни одна очередь Celery не отвечает больше 5 минут — скорее всего не работает "
                    "<code>beat</code> или брокер Redis. Алерты сейчас не формируются.")
        else:
            text = ("🟠 <b>Не отвечают воркеры Celery</b>\nОчереди: "
                    + ", ".join(f"<code>{q}</code>" for q in sorted(new_down))
                    + "\nЗадачи этих очередей не выполняются больше 5 минут.")
        logger.error("watchdog: молчат очереди %s", sorted(down))
        _send(text)
    if recovered:
        logger.warning("watchdog: восстановились %s", sorted(recovered))
        _send("🟢 <b>Воркеры Celery снова работают</b>\nОчереди: "
              + ", ".join(f"<code>{q}</code>" for q in sorted(recovered)))
    cache.set(STATE_KEY, sorted(down), timeout=None)
    return down
