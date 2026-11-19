"""
Где физически подключён узел (MAC) — по FDB коммутаторов.

MAC виден не только на порту подключения, но и на всех портах «в его сторону» на пути от ядра.
Алгоритм (топологический, без схемы сети):
  1. Кандидаты — все (коммутатор S, порт p), где виден MAC.
  2. За портом p стоят те наши устройства, чьи MAC S видит на p (MAC устройств — из ARP по IP).
  3. Если за p стоит другой кандидат S' и видит узел НЕ через S (на порту S' нет MAC S) —
     узел дальше, S не конечный. Конечный — самый глубокий: таких кандидатов за портом нет.
  4. Уверенно — если за портом конечного коммутатора нет вообще никаких наших устройств
     (кроме самого узла и, если узел — коммутатор, его собственных нижестоящих).
     Иначе узел за коммутатором, который не опрашивается (или его FDB неполная) — sure=False.
  5. Узел-коммутатор «конечен» для обоих соседей; вышестоящий — сосед на его порту в сторону
     шлюза (устройство с самой большой ARP-таблицей), иначе — тот, за чьим портом меньше наших
     устройств. Сам шлюз — корень: места подключения у него нет.

MAC «с тем же началом» (первые 5 байт) считаем одним устройством: D-Link даёт L3-интерфейсам
в VLAN соседние MAC (управление …48:00, интерфейс VLAN 70 …48:01).

Используют: apps/subnets/serializers.py (IP → «Подключён»), apps/subnets/identify.py («Кто это?»).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Port:
    device_id: int
    device: str
    device_ip: str
    port: str            # имя интерфейса или «порт N»
    bridge_port: int
    vlan: int | None
    macs_on_port: int
    final: bool          # самый глубокий: за портом нет других коммутаторов, видящих узел
    uplink: bool         # за портом есть другие наши устройства (не объяснённые узлом)
    behind: int = 0      # сколько наших устройств за портом (кроме самого узла)
    updated: object = None


def _norm(mac: str | None) -> str:
    return (mac or "").strip().upper().replace("-", ":")


def _chassis(mac: str) -> str:
    return mac[:14]      # первые 5 байт


class Infra:
    """MAC наших устройств (по ARP их IP) → device_id; с поиском «соседних» MAC того же шасси."""

    def __init__(self, mac_to_dev: dict[str, int], root: int | None = None):
        self.by_mac = {_norm(m): d for m, d in mac_to_dev.items()}
        # префикс шасси — только если он не общий у двух разных устройств (MAC из одной партии)
        owners: dict[str, set[int]] = {}
        for m, d in self.by_mac.items():
            owners.setdefault(_chassis(m), set()).add(d)
        self.by_chassis = {c: next(iter(ds)) for c, ds in owners.items() if len(ds) == 1}
        self.root = root     # шлюз — точка отсчёта «вверх» (см. infra_macs)

    def get(self, mac: str) -> int | None:
        return self.by_mac.get(mac) or self.by_chassis.get(_chassis(mac))

    def macs_of(self, dev_id: int) -> set[str]:
        return {m for m, d in self.by_mac.items() if d == dev_id}

    def chassis_of(self, dev_id: int) -> set[str]:
        return {c for c, d in self.by_chassis.items() if d == dev_id}


def infra_macs() -> Infra:
    from apps.devices.models import Device
    from apps.monitoring.models import ArpEntry

    from django.db.models import Count

    ip_to_dev = dict(Device.objects.values_list("ip", "id"))
    # Шлюз — устройство с самой большой ARP-таблицей (маршрутизирует подсети). От него считаем
    # «вверх»: коммутатор подключён к соседу на своём порту в сторону шлюза.
    # При равенстве (два устройства с одинаково большой таблицей) корень неизвестен — иначе
    # «верх» выбирался бы случайно от запроса к запросу.
    top = list(ArpEntry.objects.values("device_id").annotate(n=Count("id")).order_by("-n")
               .values_list("device_id", "n")[:2])
    root = top[0][0] if top and (len(top) == 1 or top[0][1] > top[1][1]) else None
    return Infra({mac: ip_to_dev[ip] for ip, mac in
                  ArpEntry.objects.filter(ip__in=list(ip_to_dev)).values_list("ip", "mac")}, root)


def ports_for(mac: str, infra: Infra | None = None) -> list[Port]:
    """Все порты, где виден MAC; первым — место подключения."""
    from apps.monitoring.models import FdbEntry

    mac = _norm(mac)
    if not mac:
        return []
    infra = infra or infra_macs()
    target_dev = infra.get(mac)          # узел — сам наш коммутатор?
    qs = FdbEntry.objects.filter(mac__iexact=mac)
    if target_dev:
        # коммутатор общается через MAC своих VLAN-интерфейсов (…EC:81, …EC:82), не только через
        # MAC управления — соседи видят именно их
        for c in infra.chassis_of(target_dev):
            qs = qs | FdbEntry.objects.filter(mac__istartswith=c)
    entries = [e for e in qs.select_related("device") if infra.get(_norm(e.mac)) in (None, target_dev)
               or _norm(e.mac) == mac]
    if not entries:
        return []

    keys = {(e.device_id, e.bridge_port) for e in entries}
    on_port: dict[tuple[int, int], set[str]] = {}
    for dev_id, bp, m in (FdbEntry.objects
                          .filter(device_id__in={k[0] for k in keys}, bridge_port__in={k[1] for k in keys})
                          .values_list("device_id", "bridge_port", "mac")):
        if (dev_id, bp) in keys:
            on_port.setdefault((dev_id, bp), set()).add(_norm(m))

    candidates = {e.device_id for e in entries} - {target_dev}
    target_fdb: dict[str, int] = {}      # его FDB: MAC → порт (для «его нижестоящих»)
    if target_dev:
        target_fdb = {_norm(m): bp for m, bp in
                      FdbEntry.objects.filter(device_id=target_dev).values_list("mac", "bridge_port")}

    def downstream_of_target(towards_dev: int) -> set[int]:
        """Устройства, которые узел-коммутатор видит НЕ на порту в сторону towards_dev."""
        if not target_fdb:
            return set()
        up_ports = {target_fdb[m] for m in infra.macs_of(towards_dev) if m in target_fdb}
        return {d for m, bp in target_fdb.items() if bp not in up_ports and (d := infra.get(m))}

    # Узел — коммутатор с известной FDB: по ней видно, кто выше (на его порту к шлюзу), кто ниже.
    # Нижестоящие кандидаты (они видят узел на своих аплинках) в выборе вышестоящего не участвуют —
    # иначе вышестоящий «перестаёт быть конечным», если нижний не видит его MAC (вышел из таблицы).
    up_side: set[int] = set()
    down_side: set[int] = set()
    if target_fdb and infra.root and infra.root != target_dev:
        up_ports = {target_fdb[m] for m in infra.macs_of(infra.root) if m in target_fdb}
        if up_ports:
            for m, bp in target_fdb.items():
                if (d := infra.get(m)) and d != target_dev:
                    (up_side if bp in up_ports else down_side).add(d)

    # где каждый кандидат видит узел (если в нескольких VLAN на разных портах — все порты)
    cand_ports: dict[int, set[int]] = {}
    for e in entries:
        cand_ports.setdefault(e.device_id, set()).add(e.bridge_port)

    def sees_beyond(other: int, me: int) -> bool:
        """Кандидат other видит узел не через me — значит узел дальше, за other."""
        return any(all(infra.get(m) != me for m in on_port.get((other, bp), ()))
                   for bp in cand_ports.get(other, ()))

    seen: set[tuple[int, int]] = set()
    out: list[Port] = []
    for e in entries:
        k = (e.device_id, e.bridge_port)
        if k in seen:                    # тот же порт в нескольких VLAN
            continue
        seen.add(k)
        if e.device_id == target_dev:      # свой же MAC в своей FDB (CPU/VLAN-интерфейс)
            continue
        macs = on_port.get(k, {mac})
        behind = {d for m in macs if (d := infra.get(m))} - {e.device_id, target_dev}
        final = (e.device_id not in down_side   # нижестоящий сосед — не место подключения
                 and not any(sees_beyond(c, e.device_id) for c in (behind & candidates) - down_side))
        uplink = bool(behind - downstream_of_target(e.device_id))
        out.append(Port(
            device_id=e.device_id, device=e.device.name, device_ip=e.device.ip,
            port=e.interface_name or f"порт {e.bridge_port}", bridge_port=e.bridge_port,
            vlan=e.vlan_id, macs_on_port=len(macs), final=final, uplink=uplink, behind=len(behind),
            updated=e.updated_at,
        ))
    # Узел — коммутатор: «конечны» оба соседа (вышестоящий и нижестоящий). Вышестоящий — из up_side;
    # если направление к шлюзу неизвестно — тот, за чьим портом меньше наших устройств
    # (только поддерево узла, а не вся сеть с ядром).
    out.sort(key=lambda p: (not p.final, p.device_id not in up_side, p.uplink, p.behind,
                            p.macs_on_port, p.device))
    return out


def locate(mac: str, infra: Infra | None = None) -> tuple[Port | None, bool]:
    """(порт подключения, уверенно ли)."""
    infra = infra or infra_macs()
    if infra.root and infra.get(_norm(mac)) == infra.root:
        return None, False          # сам шлюз — корень, ни к чему не «подключён»
    ports = ports_for(mac, infra)
    if not ports:
        return None, False
    best = ports[0]
    return best, best.final and not best.uplink
