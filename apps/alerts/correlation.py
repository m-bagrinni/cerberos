"""
Корреляция алертов: подавление каскадов по топологии сети.

Граф (узлы — строки):
    d<id>  устройство (Device)
    n<id>  NE мультиплексора (NeDevice)
    l<id>  оптическая линия (OpticalLink)

Родитель узла — то, через что он доступен с центра мониторинга:
    устройство  → Device.uplink (вручную, админка «Зависимости»), иначе линия, дальним концом которой оно является;
    NE          → линия, дальним концом которой он является; головной NE (без такой линии) → его шлюз;
    линия       → её ближний конец.
«Ближний/дальний» конец линии — по расстоянию станций от центра (Station.is_hub; не задан —
станция шлюза NE или станция с наибольшим числом линий), обход в ширину по оптическим линиям.

Причины (root) — алерты, означающие «дальше связи нет»:
    device_unreachable          — устройство недоступно (узел d);
    ne_status*  (crit)          — NE недоступен (узел n);
    optical_link:<id> crit/power — обрыв / нет питания на линии (узел l). Просадка RX (warn) — не причина.
Любой активный алерт, над которым в цепочке есть причина, получает suppressed_by = самая верхняя
из них: он остаётся активным, но без уведомлений и эскалации, а в списках свёрнут под причиной.

Гонка: причины появляются не мгновенно (несколько неудачных пингов / опросов), следствия — раньше.
Если в цепочке уже есть «подозрительный» узел (устройство не ответило на пинг, NE помечен
недоступным, линия в crit/power), уведомление придерживается (notify_held_at) до HOLD_MAX_SEC.

Точки входа:
    gate(alert)       — из telegram_notify.notify_alert_created: слать ли;
    on_created(alert) — post_save: новая причина забирает под себя всё ниже;
    tick()            — раз в минуту (Celery alerts.correlate): пересчёт всего, придержанные, «сироты».
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

ROOT_KEY = "device_unreachable"
LINK_PREFIX = "optical_link:"
NE_DOWN_PREFIX = "ne_status"
LINK_ROOT_SEVERITIES = ("crit", "power")
HOLD_MAX_SEC = 180       # дольше уведомление не держим, даже если причина так и не появилась
ORPHAN_GRACE_SEC = 120   # причина снята, а следствие горит — через столько считаем его самостоятельным
MAX_DEPTH = 128
FIRING = "firing"


# ── Ручной граф устройств (форма устройства в админке) ───────────────────────
def uplink_map() -> dict[int, int | None]:
    from apps.devices.models import Device
    return dict(Device.objects.values_list("id", "uplink_id"))


def chain_up(device_id, umap, include_self=True) -> list[int]:
    out, seen = [], set()
    cur = device_id if include_self else umap.get(device_id)
    while cur and cur not in seen and len(out) < MAX_DEPTH:
        seen.add(cur)
        out.append(cur)
        cur = umap.get(cur)
    return out


def descendants(device_id, umap) -> set[int]:
    children: dict[int, list[int]] = {}
    for did, up in umap.items():
        if up:
            children.setdefault(up, []).append(did)
    out, stack = set(), list(children.get(device_id, ()))
    while stack:
        d = stack.pop()
        if d in out or d == device_id:
            continue
        out.add(d)
        stack.extend(children.get(d, ()))
    return out


def would_cycle(device_id, new_uplink_id, umap=None) -> bool:
    if not device_id or not new_uplink_id:
        return False
    if device_id == new_uplink_id:
        return True
    umap = dict(umap or uplink_map())
    umap[device_id] = new_uplink_id
    return device_id in chain_up(new_uplink_id, umap)


# ── Полный граф: устройства + NE + оптические линии ──────────────────────────
class Graph:
    def __init__(self):
        self.parent: dict[str, str | None] = {}
        self.label: dict[str, str] = {}
        self.suspect: set[str] = set()       # узлы, которые уже «не отвечают», но причины ещё нет
        self.via_link: dict[str, str] = {}   # узел → линия, давшая ему родителя (для показа в админке)
        self.link_parent: dict[str, str] = {}  # устройство → его ближний сосед по линии (даже если задан вручную)
        self.hub_names: list[str] = []
        self._children: dict[str, list[str]] | None = None

    def chain(self, node, include_self=True) -> list[str]:
        """Цепочка вверх: ближний первым. Защита от циклов."""
        out, seen = [], set()
        cur = node if include_self else self.parent.get(node)
        while cur and cur not in seen and len(out) < MAX_DEPTH:
            seen.add(cur)
            out.append(cur)
            cur = self.parent.get(cur)
        return out

    def in_cycle(self, node) -> bool:
        cur, steps = self.parent.get(node), 0
        while cur and steps < MAX_DEPTH:
            if cur == node:
                return True
            cur, steps = self.parent.get(cur), steps + 1
        return False

    def depth(self, node) -> int:
        return len(self.chain(node, include_self=False))

    def below(self, node) -> set[str]:
        if self._children is None:
            self._children = {}
            for n, p in self.parent.items():
                if p:
                    self._children.setdefault(p, []).append(n)
        out, stack = set(), list(self._children.get(node, ()))
        while stack:
            x = stack.pop()
            if x in out or x == node:
                continue
            out.add(x)
            stack.extend(self._children.get(x, ()))
        return out


def _hub_stations(links, gateway_ids):
    from apps.stations.models import Station, StationDevice
    hubs = list(Station.objects.filter(is_hub=True).values_list("id", "name"))
    if not hubs and gateway_ids:
        hubs = list(StationDevice.objects.filter(device_id__in=gateway_ids)
                    .values_list("station_id", "station__name").distinct())
    if not hubs and links:
        degree = {}
        for l in links:
            for sid in (l["station_a_id"], l["station_b_id"]):
                degree[sid] = degree.get(sid, 0) + 1
        best = max(degree.items(), key=lambda kv: (kv[1], -kv[0]))[0]
        hubs = list(Station.objects.filter(pk=best).values_list("id", "name"))
    return hubs


_KEEP = object()


def manual_parent(d) -> str | None:
    """Ручной родитель устройства: Device.uplink главнее Device.uplink_ne."""
    if d.get("uplink_id"):
        return f"d{d['uplink_id']}"
    if d.get("uplink_ne_id"):
        return f"n{d['uplink_ne_id']}"
    return None


def build_graph(overrides: dict | None = None) -> Graph:
    """overrides: {device_id: "d<id>" | "n<id>" | None} — проверить граф до сохранения (админка)."""
    from apps.devices.models import Device, NeDevice
    from apps.stations.models import OpticalLink

    g = Graph()
    overrides = overrides or {}
    devices = list(Device.objects.values("id", "name", "uplink_id", "uplink_ne_id", "ping_fail_count"))
    nes = list(NeDevice.objects.values("id", "ne_id", "name", "gateway_id", "status"))
    links = list(OpticalLink.objects.filter(is_active=True).values(
        "id", "label", "link_status", "station_a_id", "station_b_id", "station_a__name", "station_b__name",
        "ne_a_id", "ne_b_id", "device_a_id", "device_b_id"))

    manual = set()
    for d in devices:
        node = f"d{d['id']}"
        mp = overrides.get(d["id"], _KEEP)
        mp = manual_parent(d) if mp is _KEEP else mp
        g.parent[node] = mp
        if mp:
            manual.add(node)
        g.label[node] = d["name"]
        if (d["ping_fail_count"] or 0) >= 1:
            g.suspect.add(node)
    for n in nes:
        node = f"n{n['id']}"
        g.parent[node] = None
        g.label[node] = n["name"] or f"NE-{n['ne_id']}"
        if n["status"] == "unreachable":
            g.suspect.add(node)

    # Расстояние станций от центра — обход в ширину по линиям
    hubs = _hub_stations(links, {n["gateway_id"] for n in nes if n["gateway_id"]})
    g.hub_names = [name for _id, name in hubs]
    adj: dict[int, list[int]] = {}
    for l in links:
        adj.setdefault(l["station_a_id"], []).append(l["station_b_id"])
        adj.setdefault(l["station_b_id"], []).append(l["station_a_id"])
    dist = {sid: 0 for sid, _ in hubs}
    q = deque(dist)
    while q:
        s = q.popleft()
        for t in adj.get(s, ()):
            if t not in dist:
                dist[t] = dist[s] + 1
                q.append(t)

    inf = 10 ** 6

    def end(l, side):
        if l[f"ne_{side}_id"]:
            return f"n{l[f'ne_{side}_id']}"
        if l[f"device_{side}_id"]:
            return f"d{l[f'device_{side}_id']}"
        return None

    # ближние линии — первыми: узел получает родителем самую короткую дорогу к центру
    def link_key(l):
        return (min(dist.get(l["station_a_id"], inf), dist.get(l["station_b_id"], inf)), l["id"])

    for l in sorted(links, key=link_key):
        a, b = end(l, "a"), end(l, "b")
        da, db = dist.get(l["station_a_id"], inf), dist.get(l["station_b_id"], inf)
        if not a or not b or a == b or (da == inf and db == inf):
            continue
        up, down = (a, b) if da <= db else (b, a)
        node = f"l{l['id']}"
        g.parent[node] = up
        g.label[node] = l["label"] or (f"{l['station_a__name']} → {l['station_b__name']}" if da <= db
                                       else f"{l['station_b__name']} → {l['station_a__name']}")
        if l["link_status"] in LINK_ROOT_SEVERITIES:
            g.suspect.add(node)
        if down[0] == "d" and up[0] == "d" and down not in g.link_parent:
            g.link_parent[down] = up
        if down in manual or g.parent.get(down):
            continue
        if down in g.chain(up):          # кольцо — не замыкаем
            continue
        g.parent[down] = node
        g.via_link[down] = node

    for n in nes:                        # головной NE — за своим шлюзом
        node = f"n{n['id']}"
        if not g.parent.get(node) and n["gateway_id"]:
            g.parent[node] = f"d{n['gateway_id']}"
    return g


# ── Алерт → узел графа ───────────────────────────────────────────────────────
def _link_id(key: str):
    try:
        return int(key[len(LINK_PREFIX):].split(":")[0])
    except (ValueError, IndexError):
        return None


def is_cause(alert) -> bool:
    """Означает ли алерт «дальше связи нет»."""
    key = alert.metric_key or ""
    if key == ROOT_KEY and alert.device_id:
        return True
    if key.startswith(LINK_PREFIX):
        return alert.severity in LINK_ROOT_SEVERITIES
    return bool(key.startswith(NE_DOWN_PREFIX) and alert.ne_device_id and alert.severity == "crit")


def cause_node(alert) -> str | None:
    """Узел, связь с которым потеряна (для алертов-причин)."""
    key = alert.metric_key or ""
    if key.startswith(LINK_PREFIX):
        lid = _link_id(key)
        return f"l{lid}" if lid else None
    if key.startswith(NE_DOWN_PREFIX) and alert.ne_device_id:
        return f"n{alert.ne_device_id}"
    return f"d{alert.device_id}" if alert.device_id else None


def _start(alert):
    """(узел, учитывать ли сам узел) — откуда искать причину вверх."""
    if is_cause(alert):
        return cause_node(alert), False
    key = alert.metric_key or ""
    if key.startswith(LINK_PREFIX):          # просадка RX на линии — причины выше линии
        lid = _link_id(key)
        return (f"l{lid}" if lid else None), False
    if alert.ip_address_id:
        sub = getattr(alert.ip_address, "subnet", None)
        return (f"d{sub.uplink_id}" if sub and sub.uplink_id else None), True
    if alert.ne_device_id:
        return f"n{alert.ne_device_id}", True
    if alert.device_id:
        return f"d{alert.device_id}", True
    return None, True


def _chain(alert, g) -> list[str]:
    node, include_self = _start(alert)
    return g.chain(node, include_self) if node else []


def _firing_causes():
    from .models import Alert
    return list(Alert.objects.filter(state=FIRING).filter(
        Q(metric_key=ROOT_KEY, device__isnull=False)
        | Q(metric_key__startswith=LINK_PREFIX, severity__in=LINK_ROOT_SEVERITIES)
        | Q(metric_key__startswith=NE_DOWN_PREFIX, ne_device__isnull=False, severity="crit")))


def _top_cause(alert, g, causes_by_node):
    for node in reversed(_chain(alert, g)):          # от самого верхнего к ближнему
        c = causes_by_node.get(node)
        if c is not None and c.pk != alert.pk:
            return c
    return None


def _by_node(causes):
    out = {}
    for c in causes:
        node = cause_node(c)
        if node and (node not in out or c.started_at < out[node].started_at):
            out[node] = c
    return out


def find_root(alert, g=None):
    g = g or build_graph()
    return _top_cause(alert, g, _by_node(_firing_causes()))


def _suppress(alert, root) -> None:
    from .models import Alert
    Alert.objects.filter(pk=alert.pk).update(suppressed_by=root, notify_held_at=None)
    alert.suppressed_by_id, alert.notify_held_at = root.pk, None
    logger.info("alert %s подавлен причиной %s (%s)", alert.pk, root.pk, root.message)


# ── Точки входа ──────────────────────────────────────────────────────────────
def gate(alert) -> bool:
    """Слать ли уведомление о новом алерте. Побочно: подавляет или придерживает."""
    from .models import Alert
    if getattr(alert, "_corr_release", False):
        return True
    if alert.state != FIRING or alert.suppressed_by_id:
        return False
    try:
        g = build_graph()
        root = _top_cause(alert, g, _by_node(_firing_causes()))
        if root:
            _suppress(alert, root)
            return False
        if alert.notify_held_at:
            return False                                 # ждёт tick()
        if set(_chain(alert, g)) & g.suspect:
            now = timezone.now()
            Alert.objects.filter(pk=alert.pk).update(notify_held_at=now)
            alert.notify_held_at = now
            logger.info("alert %s: уведомление придержано — выше по цепочке нет связи", alert.pk)
            return False
    except Exception:  # noqa: BLE001 — корреляция не должна глушить уведомления при сбое
        logger.exception("correlation gate %s", getattr(alert, "pk", "?"))
    return True


def should_announce_resolve(alert) -> bool:
    """О закрытии сообщаем только если об открытии сообщали."""
    return not alert.suppressed_by_id and not alert.notify_held_at


def on_created(alert) -> None:
    """post_save: новая причина — пересчитать, кто теперь под кем."""
    if not is_cause(alert):
        return
    try:
        recompute(notify=False)
    except Exception:  # noqa: BLE001
        logger.exception("correlation on_created %s", alert.pk)


def recompute(now=None, notify=True) -> dict:
    """
    Привести suppressed_by всех активных алертов в соответствие с графом и активными причинами.
    notify=True (tick): выпустить придержанные и «сирот» с уведомлением.
    """
    from apps.accounts import telegram_notify as tn

    from .models import Alert
    now = now or timezone.now()
    g = build_graph()
    firing = list(Alert.objects.filter(state=FIRING)
                  .select_related("device", "ne_device", "ip_address__subnet", "suppressed_by"))
    causes = _by_node([a for a in firing if is_cause(a)])
    stats = {"suppressed": 0, "moved": 0, "released": 0, "orphans": 0}
    border = now - timedelta(seconds=ORPHAN_GRACE_SEC)

    for a in firing:
        root = _top_cause(a, g, causes)
        if root:
            if a.suppressed_by_id != root.pk:
                stats["moved" if a.suppressed_by_id else "suppressed"] += 1
                _suppress(a, root)
            continue
        if a.suppressed_by_id:
            if not notify:
                continue                               # освобождение — только в tick(), с уведомлением
            cause = a.suppressed_by
            if cause is not None and cause.state != FIRING and cause.resolved_at and cause.resolved_at > border:
                continue                               # причину только что сняли — даём следствию подняться
            Alert.objects.filter(pk=a.pk).update(suppressed_by=None, notify_held_at=None)
            a.suppressed_by_id, a.notify_held_at = None, None
            stats["orphans"] += 1
            if cause is not None and cause.state != FIRING:
                text = ("⚠️ <b>Не восстановилось после устранения причины</b>\n"
                        f"Причина ({tn.escape(tn._alert_target(cause))}) снята, а это всё ещё горит:\n\n"
                        + tn._alert_text(a))
                tn._dispatch(text, tn._reply_markup(a), f"alert_orphan:{a.pk}", tn._alert_meta(a, "alert"))
            else:
                a._corr_release = True
                tn.notify_alert_created(a)
            continue
        if a.notify_held_at and notify:
            if set(_chain(a, g)) & g.suspect and (now - a.notify_held_at).total_seconds() < HOLD_MAX_SEC:
                continue
            Alert.objects.filter(pk=a.pk).update(notify_held_at=None)
            a.notify_held_at = None
            a._corr_release = True
            tn.notify_alert_created(a)
            stats["released"] += 1
    if any(stats.values()):
        logger.info("correlation: %s", stats)
    return stats


def tick(now=None) -> dict:
    return recompute(now=now, notify=True)


# ── Для уведомлений, админки и задач опроса ──────────────────────────────────
def dependents_of(alert) -> dict:
    """Что зависит от узла причины: устройства, NE, линии, подсети."""
    from apps.subnets.models import Subnet
    node = cause_node(alert)
    if not node:
        return {"devices": 0, "ne": 0, "links": 0, "subnets": 0}
    g = build_graph()
    below = g.below(node)
    dev_ids = {int(x[1:]) for x in below if x[0] == "d"}
    if node[0] == "d":
        dev_ids.add(int(node[1:]))
    return {"devices": sum(1 for x in below if x[0] == "d"),
            "ne": sum(1 for x in below if x[0] == "n"),
            "links": sum(1 for x in below if x[0] == "l"),
            "subnets": Subnet.objects.filter(uplink_id__in=dev_ids).count() if dev_ids else 0}


def dependents(device_id) -> dict:
    from apps.alerts.models import Alert
    return dependents_of(Alert(metric_key=ROOT_KEY, device_id=device_id))


def cycles_with(overrides: dict) -> list[int]:
    """Устройства, которые окажутся в цикле, если применить такие ручные родители."""
    g = build_graph(overrides)
    return sorted(int(n[1:]) for n in g.parent if n[0] == "d" and g.in_cycle(n))


def ne_at_stations() -> dict[int, list]:
    """Станция → NE на ней (StationNe и концы оптических линий)."""
    from apps.stations.models import OpticalLink, StationNe
    out: dict[int, dict] = {}
    for sid, ne_id in StationNe.objects.values_list("station_id", "ne_id"):
        out.setdefault(sid, {})[ne_id] = True
    for l in OpticalLink.objects.values("station_a_id", "station_b_id", "ne_a_id", "ne_b_id"):
        if l["ne_a_id"]:
            out.setdefault(l["station_a_id"], {})[l["ne_a_id"]] = True
        if l["ne_b_id"]:
            out.setdefault(l["station_b_id"], {})[l["ne_b_id"]] = True
    return {sid: list(nes) for sid, nes in out.items()}


def ne_suggestions(g: Graph | None = None) -> dict[int, int]:
    """
    Устройство → NE его станции, если устройство, вероятно, подключено через этот мультиплексор:
    стоит на станции с NE (не на центральной), к нему не заведена оптическая линия,
    и сейчас этот NE не в его цепочке. Без циклов.
    """
    from apps.stations.models import Station, StationDevice
    g = g or build_graph()
    hub_ids = set(Station.objects.filter(name__in=g.hub_names).values_list("id", flat=True))
    ne_at = ne_at_stations()
    out = {}
    for did, sid in StationDevice.objects.values_list("device_id", "station_id"):
        node = f"d{did}"
        nes = ne_at.get(sid) or []
        if sid in hub_ids or len(nes) != 1 or node in g.via_link:
            continue
        ne_node = f"n{nes[0]}"
        if ne_node in g.chain(node, include_self=False) or node in g.chain(ne_node):
            continue
        out[did] = nes[0]
    return out


def topo_order(items, prefix="d", g=None):
    """Сначала вышестоящие: при одновременном падении причина создаётся раньше следствий."""
    g = g or build_graph()
    return sorted(items, key=lambda x: (g.depth(f"{prefix}{x.pk}"), x.pk))
