"""Общие выборки для экранов: видимые алерты, статусы станций, метки алертов."""
from __future__ import annotations

from django.db.models import Q


def visible_alerts_q(acc) -> Q:
    """Алерты только тех разделов, что пользователь может смотреть (как в рассылке)."""
    if acc.superuser:
        return Q()
    q = Q(pk__in=[])
    if acc.can_view("devices"):
        q |= Q(ip_address__isnull=True, ne_device__isnull=True)
    if acc.can_view("mux"):
        q |= Q(ne_device__isnull=False)
    if acc.can_view("subnets"):
        q |= Q(ip_address__isnull=False)
    return q


def firing_alerts(acc):
    from apps.alerts.models import Alert
    return (Alert.objects.filter(state=Alert.STATE_FIRING).filter(visible_alerts_q(acc))
            .select_related("device", "ne_device", "ne_device__gateway", "ip_address",
                            "ip_address__station", "metric", "acked_by"))


METRIC_LABELS = (
    ("sfp_temp", "Перегрев SFP"),
    ("sfp_rx_dbm", "Низкий уровень RX"),
    ("optical_link", "Низкий уровень RX"),
    ("if_oper_status", "Порт упал"),
    ("ping", "Не отвечает на ping"),
    ("icmp", "Не отвечает на ping"),
    ("power", "Нет питания"),
    ("cpu", "Высокая загрузка CPU"),
    ("temp", "Высокая температура"),
)


def metric_label(alert) -> str:
    key = (alert.metric_key or "").lower()
    for k, label in METRIC_LABELS:
        if k in key:
            return label
    if getattr(alert, "metric", None) and alert.metric.name:
        return alert.metric.name
    return alert.metric_key or "—"


def alert_target(alert) -> str:
    from apps.accounts.telegram_notify import _alert_target
    t = _alert_target(alert)
    if t == "?":
        msg = alert.message or ""
        if ":" in msg:   # «Деградация сигнала: laz → vchd (RX -21.0 dBm)» → «laz → vchd»
            return msg.split(":", 1)[1].split("(")[0].strip()[:60] or msg[:60]
        return (msg or alert.metric_key or "—")[:60]
    return t


def alert_short(alert) -> str:
    """Цель без IP в скобках — для кнопок и узких строк."""
    import re
    return re.sub(r"\s*\([\d.]+\)$", "", alert_target(alert))


def show_value(alert) -> bool:
    """Значение осмысленно для уровней/температур, не для кодов состояния порта."""
    return alert.value is not None and not (alert.metric_key or "").startswith(("if_oper_status", "ping", "icmp"))


def alert_unit(alert) -> str:
    from apps.accounts.telegram_notify import _unit
    u = (_unit(alert) or "").strip()
    if not u:
        key = (alert.metric_key or "").lower()
        if key.startswith(("optical_link", "sfp_rx", "sfp_tx")):
            u = "dBm"
        elif "temp" in key:
            u = "°C"
    return u


def station_states(station_ids=None) -> dict:
    """{station_id: {"crit": n, "warn": n, "state": ok|warn|crit}} по активным алертам."""
    from apps.alerts.models import Alert
    from apps.stations.models import Station, StationDevice, StationNe
    from apps.subnets.models import IpAddress

    ids = list(station_ids) if station_ids is not None else list(Station.objects.values_list("pk", flat=True))
    dev_map, ne_map, ip_map = {}, {}, {}
    for sid, did in StationDevice.objects.filter(station_id__in=ids).values_list("station_id", "device_id"):
        dev_map.setdefault(did, set()).add(sid)
    for sid, nid in StationNe.objects.filter(station_id__in=ids).values_list("station_id", "ne_id"):
        ne_map.setdefault(nid, set()).add(sid)
    for iid, sid in IpAddress.objects.filter(station_id__in=ids).values_list("id", "station_id"):
        ip_map.setdefault(iid, set()).add(sid)
    out = {sid: {"crit": 0, "warn": 0} for sid in ids}
    for sev, did, nid, iid in Alert.objects.filter(state="firing").values_list(
            "severity", "device_id", "ne_device_id", "ip_address_id"):
        for sid in dev_map.get(did, set()) | ne_map.get(nid, set()) | ip_map.get(iid, set()):
            out[sid]["crit" if sev in ("crit", "power") else "warn"] += 1
    for c in out.values():
        c["state"] = "crit" if c["crit"] else "warn" if c["warn"] else "ok"
    return out


def server_online(server) -> bool:
    from datetime import timedelta
    from django.utils import timezone
    return bool(server.last_seen and timezone.now() - server.last_seen < timedelta(minutes=5))
