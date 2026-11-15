"""Общие страницы портала: главная (плитки модулей) и legacy-редиректы.

Представления модулей живут в своих приложениях:
  devices/views/   — дерево, карточка устройства, модалки, мультиплексоры, JSON-действия
  monitoring/views — вкладки «Метрики» и «SFP/DDM»
  alerts/views     — алерты
  stations/views   — станции
  subnets/views    — подсети
"""
from django.shortcuts import redirect as _redirect
from django.shortcuts import render

from apps.accounts.models import User
from apps.edocs.models import Document


def home(request):
    """Главная страница — список модулей."""
    from apps.core.models import Module
    from apps.devices.models import Device
    from apps.alerts.models import Alert

    modules = Module.objects.filter(is_active=True).order_by("sort_order", "name")

    # Модули, к которым у пользователя есть доступ (по Position.modules)
    user = request.user
    allowed_slugs = set()
    if user.is_authenticated:
        if user.is_superuser:
            allowed_slugs = {"local", "servers", "edocs", "huawei"}
        else:
            for slug in ("local", "servers", "edocs", "huawei"):
                if user.has_module_access(slug):
                    allowed_slugs.add(slug)

    # Статистика per module
    modules_data = []
    for m in modules:
        if m.slug not in allowed_slugs:
            continue
        stats = {}
        if m.slug == "local":
            stats = {
                "devices": Device.objects.count(),
                "alerts": Alert.objects.filter(state="firing").count(),
                "alerts_crit": Alert.objects.filter(state="firing", severity="crit").count(),
            }
        modules_data.append({"module": m, "stats": stats})

    # Список пользователей для колонки на главной
    users_list = User.objects.filter(is_active=True).select_related("position_ref").order_by("first_name")

    unread_qs = Document.objects.filter(recipient=request.user, is_read=False)
    unread_docs = unread_qs.count()
    unread_docs_list = unread_qs.order_by("-created_at")[:5]

    return render(request, "core/home.html", {
        "modules_data": modules_data,
        "users_list": users_list,
        "unread_docs": unread_docs,
        "unread_docs_list": unread_docs_list,
    })


def legacy_classic(request):
    return _redirect("/local/", permanent=True)


def legacy_metrics(request):
    return _redirect("/local/metrics/", permanent=True)


def legacy_sfp(request):
    return _redirect("/local/sfp/", permanent=True)


def legacy_multiplexers(request):
    return _redirect("/local/multiplexers/", permanent=True)


def legacy_stations(request):
    return _redirect("/local/stations/", permanent=True)


def legacy_subnets(request):
    return _redirect("/local/subnets/", permanent=True)


def legacy_devices(request):
    return _redirect("/local/devices/", permanent=True)


def legacy_discoveries(request):
    return _redirect("/local/", permanent=True)


def set_ui_theme(request, name):
    """Переключает тему интерфейса (cookie) и возвращает на страницу, откуда пришли."""
    from django.utils.http import url_has_allowed_host_and_scheme
    from apps.core.context_processors import UI_THEME_COOKIE, UI_THEMES

    nxt = request.GET.get("next") or request.META.get("HTTP_REFERER") or "/"
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        nxt = "/"
    resp = _redirect(nxt)
    if name in UI_THEMES:
        resp.set_cookie(UI_THEME_COOKIE, name, max_age=365 * 24 * 3600, samesite="Lax")
    return resp
