"""
Права доступа: раздел × уровень + отдельные возможности. Хранятся у каждого пользователя
(User.access_sections / User.access_caps) и задаются администратором при подтверждении
регистрации или в карточке пользователя. Шаблоны (AccessPreset) только заполняют форму.

Уровни раздела:
  0 — нет доступа (раздел скрыт, адреса → 403);
  1 — просмотр (GET);
  2 — изменение (POST/PUT/PATCH/DELETE и админ-панель раздела).

Проверка — одна, в AccessMiddleware: адрес → раздел → нужный уровень (по методу).
Старые проверки has_perm("device.update") и т.п. переведены на эти же данные (LEGACY_PERMS).
"""
import re
from dataclasses import dataclass, field

NONE, VIEW, EDIT = 0, 1, 2
LEVEL_LABELS = {NONE: "Нет", VIEW: "Просмотр", EDIT: "Изменение"}


@dataclass(frozen=True)
class Section:
    key: str
    name: str
    group: str            # local / servers / edocs / system — для плиток модулей на главной
    hint: str = ""
    prefixes: tuple = ()  # адреса сайта и API
    admin: tuple = ()     # разделы админ-панели /admin-ui/<seg>/
    url: str = ""         # главная страница раздела (для меню и перенаправлений)


SECTIONS = [
    Section("devices", "Устройства", "local", "Обзор, дерево и карточки устройств, профили, вендоры",
            prefixes=("/local/devices/", "/local/subcategories/", "/local/tree-status/", "/local/classic/",
                      "/devices/", "/panel/", "/modal/", "/discoveries/", "/classic/",
                      "/api/v1/devices", "/api/v1/vendors", "/api/v1/profiles", "/api/v1/metrics",
                      "/api/v1/metric-values", "/api/v1/interfaces", "/api/v1/vlans", "/api/v1/gateway/"),
            admin=("devices", "profiles", "vendors", "categories", "subcategories", "api"), url="/local/"),
    Section("alerts", "Алерты", "local", "Список алертов, закрытие",
            prefixes=("/alerts/", "/api/v1/alerts/"), admin=("alerts",), url="/alerts/"),
    Section("stations", "Диспетчерская и станции", "local", "Карта станций, оптические линии, муфты",
            prefixes=("/local/stations/", "/stations/", "/api/v1/stations", "/api/v1/optical-links",
                      "/api/v1/route/", "/tiles/"),
            admin=("stations", "links", "closures"), url="/local/stations/"),
    Section("metrics", "Метрики и SFP/DDM", "local", "Графики метрик, оптические модули",
            prefixes=("/local/metrics/", "/local/sfp/", "/metrics/", "/sfp/"), url="/local/metrics/"),
    Section("mux", "Мультиплексоры (NE)", "local", "Olencom NE, их порты и DDM",
            prefixes=("/local/multiplexers/", "/multiplexers/", "/modal/ne/", "/api/v1/ne/"),
            admin=("ne",), url="/local/multiplexers/"),
    Section("subnets", "Подсети", "local", "Карта IP, сканирование, привязка IP к станциям",
            prefixes=("/local/subnets/", "/subnets/", "/api/v1/subnets", "/api/v1/ip-addresses",
                      "/api/v1/subnet-scans"),
            admin=("subnets",), url="/local/subnets/"),
    Section("analytics", "Аналитика", "local", "Отчёты по сети",
            prefixes=("/analytics/",), url="/analytics/"),
    Section("servers", "Серверы и AD", "servers", "Серверы, пользователи и группы Active Directory",
            prefixes=("/servers/",), url="/servers/"),
    Section("edocs", "Документы (СЭД)", "edocs", "Входящие, исходящие, создание документов",
            prefixes=("/edocs/",), url="/edocs/"),
    Section("services", "Службы", "system", "Контейнеры, фоновые задачи, состояние системы",
            prefixes=("/services/", "/api/v1/services/"), url="/services/"),
]
SECTION_BY_KEY = {s.key: s for s in SECTIONS}
GROUP_LABELS = {"local": "Локальная сеть", "servers": "Серверы", "edocs": "Документы", "system": "Система"}


@dataclass(frozen=True)
class Capability:
    key: str
    name: str
    hint: str
    needs: str = ""       # раздел, без права изменения в котором возможность бессмысленна


CAPABILITIES = [
    Capability("port_control", "Управление портами", "Включение/выключение портов и смена описания (SNMP SET)", "devices"),
    Capability("traffic_capture", "Захват трафика", "tcpdump/pcap на сервере — видно весь трафик подсети", "subnets"),
    Capability("snmp_secrets", "SNMP community", "Видеть community устройств"),
    Capability("announcements", "Объявления", "Публиковать объявления на главной"),
    Capability("users_manage", "Пользователи и права", "Подтверждать регистрации, менять права, шаблоны, модули"),
]
CAP_BY_KEY = {c.key: c for c in CAPABILITIES}

# Разделы админ-панели, закрытые возможностями, а не разделами
ADMIN_CAP_SEGMENTS = {
    "users": "users_manage", "roles": "users_manage", "presets": "users_manage", "modules": "users_manage",
    "announcements": "announcements",
}
# Адреса, требующие возможность (поверх раздела)
CAP_PATHS = [
    (re.compile(r"^/api/v1/interfaces/\d+/(set-admin-status|set-alias)/"), "port_control"),
    (re.compile(r"^/api/v1/subnets/tcpdump/"), "traffic_capture"),
]
# POST, который ничего не меняет (расчёт маршрута) — достаточно просмотра
VIEW_POSTS = ("/api/v1/route/",)
# Свои проверки / без сессии
EXEMPT = ("/servers/api/agent/report/", "/api/v1/telegram/")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

# Обзор /local/ — это дерево устройств (раздел «Устройства»). Без него, но с другими
# разделами сети — перенаправляем на первый доступный (см. AccessMiddleware).
LOCAL_HOME = "/local/"


def _norm_level(v) -> int:
    try:
        v = int(v)
    except (TypeError, ValueError):
        return NONE
    return v if v in (NONE, VIEW, EDIT) else NONE


def clean_sections(data) -> dict:
    data = data if isinstance(data, dict) else {}
    return {s.key: _norm_level(data.get(s.key, NONE)) for s in SECTIONS}


def clean_caps(data) -> list:
    data = data if isinstance(data, (list, tuple, set)) else []
    return [c.key for c in CAPABILITIES if c.key in data]


@dataclass
class Rule:
    """Что нужно для запроса: раздел+уровень, либо возможность, либо «любой раздел группы»."""
    section: str = ""
    level: int = NONE
    cap: str = ""
    any_local: bool = False
    admin_entry: bool = False
    deny: bool = False
    reason: str = ""


def rule_for(path: str, method: str) -> Rule | None:
    """None — адрес не ограничивается правами (главная, профиль, медиа и т.п.)."""
    if path.startswith(EXEMPT):
        return None
    write = method.upper() not in SAFE_METHODS
    need = EDIT if write else VIEW

    # Админ-панель: только изменение (это настройка), разделы — по первому сегменту
    if path.startswith("/admin-ui/"):
        seg = path[len("/admin-ui/"):].split("/", 1)[0]
        if not seg:
            return Rule(admin_entry=True, reason="админ-панель")
        if seg in ADMIN_CAP_SEGMENTS:
            return Rule(cap=ADMIN_CAP_SEGMENTS[seg], reason=CAP_BY_KEY[ADMIN_CAP_SEGMENTS[seg]].name)
        for s in SECTIONS:
            if seg in s.admin:
                return Rule(section=s.key, level=EDIT, reason=f"{s.name} (настройка)")
        return Rule(deny=True, reason="неизвестный раздел админ-панели")

    if path == LOCAL_HOME:
        return Rule(section="devices", level=need, any_local=True, reason="Устройства")

    rule = None
    best = -1
    for s in SECTIONS:
        for p in s.prefixes:
            if path.startswith(p) and len(p) > best:
                best = len(p)
                lvl = VIEW if (write and path.startswith(VIEW_POSTS)) else need
                rule = Rule(section=s.key, level=lvl, reason=s.name)
    for rx, cap in CAP_PATHS:
        if rx.match(path):
            return Rule(section=rule.section if rule else "", level=EDIT if rule else NONE,
                        cap=cap, reason=CAP_BY_KEY[cap].name)
    if rule is None and path.startswith("/api/"):
        # Неописанный API — закрыт: новый эндпоинт без записи в реестре не должен быть открыт всем
        return Rule(deny=True, reason="API без раздела")
    return rule


@dataclass
class Access:
    superuser: bool = False
    sections: dict = field(default_factory=dict)
    caps: set = field(default_factory=set)

    @classmethod
    def for_user(cls, user):
        if user is None or not getattr(user, "is_authenticated", False):
            return cls()
        cached = getattr(user, "_access_cache", None)
        if cached is not None:
            return cached
        acc = cls(
            superuser=bool(user.is_superuser),
            sections=clean_sections(getattr(user, "access_sections", None)),
            caps=set(clean_caps(getattr(user, "access_caps", None))),
        )
        user._access_cache = acc
        return acc

    # ── базовые вопросы ──
    def level(self, key: str) -> int:
        return EDIT if self.superuser else self.sections.get(key, NONE)

    def can_view(self, key: str) -> bool:
        return self.level(key) >= VIEW

    def can_edit(self, key: str) -> bool:
        return self.level(key) >= EDIT

    def has_cap(self, key: str) -> bool:
        if self.superuser:
            return True
        cap = CAP_BY_KEY.get(key)
        if cap is None or key not in self.caps:
            return False
        return not cap.needs or self.can_edit(cap.needs)

    def group_level(self, group: str) -> int:
        return max((self.level(s.key) for s in SECTIONS if s.group == group), default=NONE)

    @property
    def any_local(self) -> bool:
        return self.group_level("local") >= VIEW

    def first_url(self, group: str = "") -> str:
        """Первая доступная страница (всей системы или группы)."""
        for s in SECTIONS:
            if (not group or s.group == group) and self.can_view(s.key) and s.url:
                return s.url
        return "/"

    @property
    def admin_entry(self) -> bool:
        """Есть что открыть в админ-панели."""
        if self.superuser:
            return True
        return any(self.can_edit(s.key) for s in SECTIONS if s.admin) or \
            any(self.has_cap(c) for c in set(ADMIN_CAP_SEGMENTS.values()))

    def allows(self, rule: Rule | None) -> bool:
        if rule is None or self.superuser:
            return True
        if rule.deny:
            return False
        if rule.admin_entry:
            return self.admin_entry
        if rule.cap and not self.has_cap(rule.cap):
            return False
        if rule.section and self.level(rule.section) < rule.level:
            return False
        return True

    # для шаблонов: {{ acc.v.subnets }} / {{ acc.e.devices }} / {{ acc.c.port_control }}
    @property
    def v(self):
        return {s.key: self.can_view(s.key) for s in SECTIONS}

    @property
    def e(self):
        return {s.key: self.can_edit(s.key) for s in SECTIONS}

    @property
    def c(self):
        return {c.key: self.has_cap(c.key) for c in CAPABILITIES}


# ── Совместимость со старыми проверками has_perm("device.update") ─────────────
_PREFIX_SECTION = {
    "device": "devices", "profile": "devices", "vendor": "devices", "metric": "devices",
    "ne": "mux", "subnet": "subnets", "alert": "alerts", "services": "services",
}
_LEGACY_CAPS = {
    "user": "users_manage", "system.admin": "users_manage", "announcement": "announcements",
}


def legacy_allows(acc: Access, perm: str) -> bool:
    if acc.superuser:
        return True
    if perm in ("local", "servers", "edocs"):
        return acc.group_level(perm) >= VIEW
    if perm == "admin.access":
        return acc.admin_entry
    for pref, cap in _LEGACY_CAPS.items():
        if perm.startswith(pref + "."):
            return acc.has_cap(cap)
    head, _, action = perm.partition(".")
    sec = _PREFIX_SECTION.get(head)
    if sec is None:
        return False
    return acc.can_view(sec) if action == "view" else acc.can_edit(sec)


# ── Шаблоны по умолчанию (создаются миграцией, правятся в админ-панели) ──────
_ALL_LOCAL = ("devices", "alerts", "stations", "metrics", "mux", "subnets", "analytics")


def _mk(view=(), edit=(), caps=()):
    d = {s.key: NONE for s in SECTIONS}
    for k in view:
        d[k] = VIEW
    for k in edit:
        d[k] = EDIT
    return d, list(caps)


DEFAULT_PRESETS = [
    ("Без доступа", "Только главная и профиль. Для новых регистраций по умолчанию.", *_mk()),
    ("Секретарь", "Только документы.", *_mk(edit=("edocs",))),
    ("Дежурный", "Видит всю сеть, закрывает алерты, работает с документами.",
     *_mk(view=_ALL_LOCAL, edit=("alerts", "edocs"))),
    ("Наблюдатель", "Только просмотр сети и документов.", *_mk(view=_ALL_LOCAL + ("edocs",))),
    ("Инженер связи", "Сеть: устройства, станции, мультиплексоры, метрики. Подсети — просмотр.",
     *_mk(view=("subnets", "analytics", "services"),
          edit=("devices", "alerts", "stations", "metrics", "mux", "edocs"),
          caps=("port_control",))),
    ("Инженер ЛВС", "Вся локальная сеть с подсетями и захватом трафика.",
     *_mk(view=("services",), edit=_ALL_LOCAL + ("edocs",),
          caps=("port_control", "traffic_capture", "snmp_secrets"))),
    ("Администратор", "Всё, включая пользователей и права.",
     *_mk(edit=tuple(s.key for s in SECTIONS), caps=tuple(c.key for c in CAPABILITIES))),
]


# ── Декоратор для view: явная проверка раздела (в дополнение к AccessMiddleware) ──
def section_required(key: str, level: int | None = None):
    """level=None — по методу: GET → просмотр, иначе → изменение."""
    import functools

    def deco(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            from django.http import JsonResponse
            from django.shortcuts import redirect
            user = request.user
            if not user.is_authenticated:
                if request.path.startswith(("/api/", "/servers/api/")):
                    return JsonResponse({"ok": False, "error": "authentication required"}, status=401)
                return redirect(f"/accounts/login/?next={request.path}")
            need = level if level is not None else (VIEW if request.method in SAFE_METHODS else EDIT)
            if Access.for_user(user).level(key) < need:
                from apps.accounts.middleware import _forbid
                return _forbid(request, f"Нет доступа: «{SECTION_BY_KEY[key].name}» ({LEVEL_LABELS[need].lower()}).")
            return view(request, *args, **kwargs)
        return wrapper
    return deco


def cap_required(key: str):
    import functools

    def deco(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated or not Access.for_user(user).has_cap(key):
                from apps.accounts.middleware import _forbid
                return _forbid(request, f"Нет права «{CAP_BY_KEY[key].name}».")
            return view(request, *args, **kwargs)
        return wrapper
    return deco
