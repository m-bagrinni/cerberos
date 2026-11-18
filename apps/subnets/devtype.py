"""
Тип устройства по IP: ПК, сетевое, принтер, камера, сервер/ВМ …

Определение — по сумме независимых признаков, без сканирования портов:
  • решающие: устройство заведено в Cerber, шлюз подсети, сам сервер Cerber;
  • имя хоста (из базы или NetBIOS): PC-067, DC1, ac/ag, prn-…, cam-…;
  • NetBIOS ответил → Windows/Samba (fingerprint.py);
  • TTL ответа на ping → ОС: 128 Windows, 64 Linux/встраиваемые, 255 сетевое;
  • производитель по MAC (OUI) — с весом: однозначные (Hikvision, Kyocera, MikroTik)
    весят много, неоднозначные (TP-Link, D-Link, Realtek, HP — делают и сетевые карты
    для ПК, и коммутаторы, и принтеры) — мало и только вместе с другими признаками;
  • случайный (локально администрируемый) MAC → скорее телефон/ноутбук.

Тип ставится, только если у лидера есть явный перевес; иначе — «не определён»
с перечнем кандидатов и причиной (поле dcand). Каждое решение несёт «почему».
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from .identify import mac_kind, normalize_mac, vendor_of

# тип → (подпись, иконка bootstrap-icons)
TYPES: dict[str, tuple[str, str]] = {
    "network": ("Сетевое оборудование", "bi-diagram-3"),
    "gateway": ("Шлюз", "bi-signpost-split"),
    "pc": ("Компьютер", "bi-pc-display"),
    "server": ("Сервер / ВМ", "bi-hdd-rack"),
    "printer": ("Принтер / МФУ", "bi-printer"),
    "camera": ("Камера / видеорегистратор", "bi-camera-video"),
    "voip": ("IP-телефон", "bi-telephone"),
    "mobile": ("Телефон / ноутбук", "bi-phone"),
    "ups": ("ИБП", "bi-battery-charging"),
    "nas": ("Сетевое хранилище", "bi-device-hdd"),
    "embedded": ("Встраиваемое / АСУ", "bi-cpu"),
}

# Производитель (подстрока OUI, без регистра) → веса типов и пояснение для неоднозначных.
# Первое совпадение побеждает.
VENDOR_RULES: list[tuple[str, dict[str, float], str]] = [
    # однозначные
    *[(v, {"camera": 3}, "") for v in ("hikvision", "dahua", "uniview", "axis communications", "hanwha",
                                        "tiandy", "milesight", "reolink", "shenzhen tvt", "ezviz")],
    *[(v, {"printer": 3}, "") for v in ("kyocera", "ricoh", "xerox", "konica minolta", "lexmark", "pantum",
                                         "brother industries", "seiko epson", "oki electric", "zebra technologies",
                                         "sharp corporation")],
    ("canon", {"printer": 2.5, "camera": 0.5}, ""),
    *[(v, {"voip": 3}, "") for v in ("yealink", "grandstream", "polycom", "fanvil", "snom", "avaya", "mitel")],
    *[(v, {"ups": 3}, "") for v in ("american power conversion", "eaton", "powercom", "ippon")],
    *[(v, {"nas": 3}, "") for v in ("synology", "qnap")],
    *[(v, {"server": 3}, "") for v in ("proxmox", "vmware", "xensource", "nutanix")],
    *[(v, {"server": 2}, "") for v in ("super micro", "supermicro")],
    *[(v, {"network": 3}, "") for v in ("routerboard", "mikrotik", "juniper", "sphera telecom", "olencom",
                                         "hirschmann", "moxa", "edgecore", "edge-core", "accton", "bdcom",
                                         "raisecom", "qtech", "nag llc", "ubiquiti", "aruba", "extreme networks",
                                         "allied telesis", "h3c", "ruijie", "fortinet", "planet technology")],
    ("cisco", {"network": 2.5, "voip": 0.5}, ""),
    ("eltex", {"network": 2.5, "voip": 0.5}, ""),
    *[(v, {"mobile": 2.5}, "") for v in ("xiaomi", "oppo", "vivo mobile", "huawei device", "honor device",
                                          "realme", "oneplus")],
    *[(v, {"embedded": 2.5}, "") for v in ("siemens", "wago", "phoenix contact", "advantech", "beckhoff",
                                            "raspberry pi", "espressif", "schneider electric")],
    # материнские платы — почти всегда ПК (реже сервер)
    *[(v, {"pc": 2, "server": 0.5}, "") for v in ("giga-byte", "asrock", "biostar", "micro-star", "asiarock",
                                                    "elitegroup")],
    ("asustek", {"pc": 2, "network": 0.5}, "ASUS делает и ПК, и роутеры"),
    *[(v, {"pc": 2, "mobile": 0.5}, "") for v in ("lenovo", "lcfc", "quanta", "pegatron", "wistron", "compal",
                                                   "acer", "fujitsu", "hon hai", "foxconn")],
    ("dell", {"pc": 1.5, "server": 1}, "Dell — ПК или сервер"),
    # неоднозначные: одной марки мало
    ("hewlett packard enterprise", {"server": 1.5, "network": 1}, "HPE — сервер или коммутатор"),
    ("hewlett packard", {"pc": 1, "printer": 1, "server": 0.5}, "HP делает и ПК, и принтеры"),
    ("tp-link", {"network": 1, "pc": 1}, "TP-Link — сетевое устройство или сетевая карта/Wi-Fi-адаптер ПК"),
    ("d-link", {"network": 1.2, "pc": 0.8}, "D-Link — коммутатор или сетевая карта ПК"),
    ("tenda", {"network": 1, "pc": 0.8}, "Tenda — роутер или адаптер ПК"),
    ("netgear", {"network": 1.5, "pc": 0.3}, ""),
    ("zyxel", {"network": 1.5, "pc": 0.3}, ""),
    ("keenetic", {"network": 2}, ""),
    ("huawei technologies", {"network": 1.5, "mobile": 0.5}, ""),
    ("zte", {"network": 1, "mobile": 0.5}, ""),
    ("intel corporate", {"pc": 1, "server": 0.7}, "Intel — сетевая карта ПК или сервера"),
    ("realtek", {"pc": 1, "network": 0.4, "embedded": 0.4}, "Realtek — чип сетевой карты, ставится куда угодно"),
    ("liteon", {"pc": 1, "mobile": 1}, ""),
    ("azurewave", {"pc": 1, "mobile": 1}, ""),
    ("texas instruments", {"embedded": 1.5, "network": 0.5}, "Texas Instruments — чип во встраиваемых устройствах"),
    ("broadcom", {"network": 0.5, "pc": 0.5}, ""),
    ("apple", {"mobile": 1.2, "pc": 1}, "Apple — Mac или iPhone/iPad"),
    ("samsung electronics", {"mobile": 1.2, "printer": 0.5}, ""),
    ("microsoft", {"pc": 1, "server": 1}, ""),
]

VM_PREFIXES = {"52:54:00": "QEMU/KVM", "00:15:5d": "Hyper-V", "00:50:56": "VMware",
               "00:0c:29": "VMware", "08:00:27": "VirtualBox", "bc:24:11": "Proxmox"}

HOSTNAME_RULES: list[tuple[re.Pattern, str]] = [
    # PC-067, PC_12, DESKTOP-ABC, LAPTOP-1, WS-05, ARM-3, WORKS_PC (но не «PCHE»)
    (re.compile(r"^(pc|ws|arm|nb)[-_ ]?\d|^(desktop|laptop|notebook)[-_]|[-_]pc\d*$", re.I), "pc"),
    (re.compile(r"^(srv|server|sv|dc|vm|esx|proxmox|pve|hv)\d*([-_.]|$)|[-_.](srv|server|ser)\d*$", re.I), "server"),
    (re.compile(r"^(idrac|ilo|ipmi|bmc|imm)([-_.\d]|$)", re.I), "server"),
    (re.compile(r"(^|[-_.])(prn|print|printer|mfp|mfu|npi|kyocera|km|canon|hp-?lj)", re.I), "printer"),
    (re.compile(r"(^|[-_.])(cam|ipc|ipcam|camera|nvr|dvr|hik)", re.I), "camera"),
    (re.compile(r"(^|[-_.])(ups|ibp)", re.I), "ups"),
    (re.compile(r"(^|[-_.])(nas|storage)", re.I), "nas"),
    (re.compile(r"(^|[-_.])(sip|voip|ipphone)", re.I), "voip"),
    (re.compile(r"^(ac|ag|as|sw|switch|core|gw|gateway|trunk|router|rtr|olt|mux|opt)([-_.\d ]|$)", re.I), "network"),
]

# TTL (исходный, после округления) → веса
TTL_WEIGHTS = {
    128: ({"pc": 2.5, "server": 1.2, "network": -1.5, "printer": -1.5, "camera": -2, "embedded": -1.5,
           "mobile": -1, "voip": -1.5, "ups": -1.5, "nas": -1}, "Windows"),
    64: ({"pc": -1.5, "server": 0.3, "network": 0.5, "embedded": 0.5, "printer": 0.3, "camera": 0.3,
          "mobile": 0.3, "nas": 0.3}, "Linux / встраиваемая ОС"),
    255: ({"network": 2.5, "printer": 0.5, "pc": -2.5, "server": -1, "camera": -0.5, "mobile": -1},
          "сетевая ОС (Cisco, коммутаторы)"),
}


def _result(t: str, why: str, conf: str, vendor: str | None) -> dict[str, Any]:
    label, icon = TYPES[t]
    return {"dtype": t, "dlabel": label, "dicon": icon, "dwhy": why, "dconf": conf, "vendor": vendor}


def _vendor_rule(vendor: str) -> tuple[dict[str, float], str] | None:
    low = vendor.lower()
    for needle, weights, note in VENDOR_RULES:
        if needle in low:
            return weights, note
    return None


def classify(*, ip: str, hostname: str = "", mac: str | None = None, device: Any = None,
             gateway_ip: str | None = None, fingerprint: dict | None = None) -> dict[str, Any] | None:
    """Тип одного узла. dtype=None + dcand — если признаки противоречат друг другу."""
    mac = normalize_mac(mac) if mac else None
    vendor = vendor_of(mac) if mac else None
    fp = fingerprint or {}
    nb = fp.get("nb") or {}

    # ── решающие ──
    if gateway_ip and ip == gateway_ip:
        return _result("gateway", "указан шлюзом подсети", "high", vendor)
    if device is not None:
        cat = (getattr(device, "category", None) and device.category.name) or ""
        t = "network"
        for needle, tt in (("камер", "camera"), ("видео", "camera"), ("принтер", "printer"),
                           ("сервер", "server"), ("ибп", "ups"), ("ups", "ups")):
            if needle in cat.lower():
                t = tt
                break
        return _result(t, f"устройство Cerber «{device.name}»" + (f", категория «{cat}»" if cat else ""), "high", vendor)
    if mac:
        for prefix, name in VM_PREFIXES.items():
            if mac.startswith(prefix):
                return _result("server", f"MAC виртуальной машины ({name})", "high", vendor or name)

    # ── сумма признаков ──
    score: dict[str, float] = {}
    why: dict[str, list[str]] = {}
    notes: list[str] = []

    def add(weights: dict[str, float], reason: str):
        for t, w in weights.items():
            score[t] = score.get(t, 0) + w
            if w > 0:
                why.setdefault(t, []).append(reason)

    names = list({n.lower(): n for n in (hostname, nb.get("name")) if n}.values())   # без дублей
    for name in names:
        for rx, t in HOSTNAME_RULES:
            if rx.search(name):
                add({t: 3}, f"имя «{name}»")
                break

    if nb:
        if nb.get("dc"):
            add({"server": 4}, "NetBIOS: контроллер домена")
        elif nb.get("samba"):
            add({"server": 2, "nas": 1}, "NetBIOS: Samba (Linux)")
        else:
            add({"pc": 2.5, "server": 1}, f"отвечает по NetBIOS как Windows ({nb.get('name') or '?'})")

    ttl0 = fp.get("ttl0")
    if ttl0 in TTL_WEIGHTS:
        weights, os_name = TTL_WEIGHTS[ttl0]
        add(weights, f"TTL {fp.get('ttl')} — {os_name}")

    if vendor:
        rule = _vendor_rule(vendor)
        if rule:
            weights, note = rule
            add(weights, f"производитель {vendor}")
            if note:
                notes.append(note)
    elif mac and mac_kind(mac)["local"]:
        add({"mobile": 1.5, "pc": 0.5}, "случайный MAC (так делают телефоны и ноутбуки)")

    if not score:
        return None
    ranked = sorted(score.items(), key=lambda kv: kv[1], reverse=True)
    (t1, s1), (t2, s2) = ranked[0], (ranked[1] if len(ranked) > 1 else (None, 0.0))
    margin = s1 - max(s2, 0)
    reason = " · ".join(why.get(t1, []))
    if s1 >= 3.3 and margin >= 1.5:
        return _result(t1, reason, "high", vendor)
    if s1 >= 1.5 and margin >= 1:
        return _result(t1, reason, "medium", vendor)

    # спорно — не утверждаем
    cands = [TYPES[t][0] for t, s in ranked[:2] if s > 0]
    hint = notes[0] if notes else "признаков недостаточно"
    if not fp:
        hint += "; TTL и NetBIOS ещё не сняты — уточнится после следующего сканирования"
    return {"dtype": None, "dcand": " или ".join(cands) + "?", "dwhy": hint, "vendor": vendor}


def _host_neigh_cached() -> dict[str, str]:
    from django.core.cache import cache
    from .identify import _host_neigh
    data = cache.get("capture:neigh")
    if data is None:
        data = _host_neigh()
        cache.set("capture:neigh", data, 60)
    return data


def classify_many(ip_objs: Iterable[Any], gateway_ip: str | None = None) -> dict[str, dict[str, Any]]:
    """
    Типы для набора IpAddress (сетка подсети) — пачкой, без N+1:
    MAC берётся из IpAddress.mac → ARP коммутаторов → ARP сервера.
    """
    from apps.devices.models import Device
    from apps.monitoring.models import ArpEntry

    objs = list(ip_objs)
    ips = [o.ip for o in objs]
    devices = {d.ip: d for d in Device.objects.filter(ip__in=ips).select_related("category")}
    arp: dict[str, str] = {}
    for a in ArpEntry.objects.filter(ip__in=ips).order_by("updated_at").values("ip", "mac"):
        arp[a["ip"]] = a["mac"]          # самая свежая запись перезаписывает старые
    neigh = _host_neigh_cached() if any(not o.mac and o.ip not in arp for o in objs if o.is_alive) else {}

    own: set[str] = set()
    try:  # IP самого сервера Cerber (интерфейсы хоста, кэш 30 с)
        from .tcpdump import list_ifaces
        own = {i["ip"] for i in list_ifaces() if i.get("ip")}
    except Exception:
        pass

    out: dict[str, dict[str, Any]] = {}
    for o in objs:
        if o.ip in own:
            out[o.ip] = {**_result("server", "это сервер Cerber", "high", None), "mac": None}
            continue
        mac = o.mac or arp.get(o.ip) or neigh.get(o.ip)
        res = classify(ip=o.ip, hostname=o.hostname, mac=mac, device=devices.get(o.ip),
                       gateway_ip=gateway_ip, fingerprint=getattr(o, "fingerprint", None))
        if res:
            res["mac"] = normalize_mac(mac) if mac else None
            fp = getattr(o, "fingerprint", None) or {}
            if fp:
                nb = fp.get("nb") or {}
                res["fp"] = {"ttl": fp.get("ttl"), "nb": nb.get("name"), "domain": nb.get("domain")}
            out[o.ip] = res
    return out
