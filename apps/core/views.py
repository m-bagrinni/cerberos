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
    """Главная — личная страница сотрудника: документы, объявления, коллеги, модули."""
    from datetime import timedelta

    from django.db.models import Q
    from django.utils import timezone

    from apps.accounts.models import TelegramAccount
    from apps.alerts.models import Alert
    from apps.core.models import Announcement, Module
    from apps.devices.models import Device
    from apps.edocs.models import Comment
    from apps.edocs.queries import INBOX_FILTERS, pending_counts, pending_qs, unread_qs

    user = request.user
    now = timezone.localtime()

    # ── Модули (как раньше: только доступные пользователю) ──
    allowed_slugs = set()
    if user.is_superuser:
        allowed_slugs = {"local", "servers", "edocs", "huawei"}
    else:
        for slug in ("local", "servers", "edocs", "huawei"):
            if user.has_module_access(slug):
                allowed_slugs.add(slug)
    modules_data = []
    for m in Module.objects.filter(is_active=True).order_by("sort_order", "name"):
        if m.slug not in allowed_slugs:
            continue
        stats = {}
        if m.slug == "local":
            stats = {
                "devices": Device.objects.count(),
                "alerts": Alert.objects.filter(state="firing").count(),
            }
        modules_data.append({"module": m, "stats": stats})

    # ── Документы: что ждёт меня ──
    counts = pending_counts(user)
    pending_tiles = [
        {"key": key, "label": label, "count": counts[key]}
        for key, (label, _q) in INBOX_FILTERS.items()
    ]
    from django.db.models import Exists, OuterRef

    from apps.edocs.models import Assignment
    unread_mine = Assignment.objects.filter(document=OuterRef("pk"), user=user, status="pending", is_read=False)
    pending_list = (pending_qs(user).annotate(is_read=~Exists(unread_mine)).select_related("author")
                    .order_by("-updated_at")[:6])

    # ── Мои отправленные: где сейчас документ ──
    sent_list = (Document.objects.filter(author=user).exclude(status="draft")
                 .select_related("recipient").order_by("-updated_at")[:5])

    # ── Новые комментарии к моим документам (7 дней) ──
    comments_list = (Comment.objects
                     .filter(Q(document__author=user) | Q(document__assignments__user=user))
                     .distinct()
                     .exclude(author=user)
                     .filter(created_at__gte=now - timedelta(days=7))
                     .select_related("author", "document").order_by("-created_at")[:5])

    # ── Колокольчик: непрочитанные ──
    unread = unread_qs(user)
    unread_docs = unread.count()
    unread_docs_list = unread.select_related("author").order_by("-created_at")[:5]

    # ── Сотрудники, сгруппированные по должности ──
    users_list = (User.objects.filter(is_active=True)
                  .select_related("position_ref", "telegram_account")
                  .order_by("position_ref__name", "last_name", "first_name"))

    # ── Подсказки по профилю ──
    has_tg = TelegramAccount.objects.filter(user=user, is_active=True).exists()
    setup_hints = []
    if not has_tg:
        setup_hints.append({"icon": "bi-telegram", "text": "Привяжите Telegram — уведомления о документах будут приходить в мессенджер",
                            "href": "/accounts/profile/#tg-card", "action": "Привязать"})
    if not user.avatar:
        setup_hints.append({"icon": "bi-person-bounding-box", "text": "Загрузите фото — коллегам проще узнать вас в списке",
                            "href": "/accounts/profile/", "action": "Загрузить"})
    if not (user.first_name and user.last_name):
        setup_hints.append({"icon": "bi-pencil-square", "text": "Укажите имя и фамилию в профиле",
                            "href": "/accounts/profile/", "action": "Заполнить"})

    def _age(dt):
        sec = max(0, (now - dt).total_seconds())
        if sec < 3600:
            return f"{max(1, int(sec // 60))} мин"
        if sec < 86400:
            return f"{int(sec // 3600)} ч"
        return f"{int(sec // 86400)} дн"

    pending_list, sent_list, comments_list = list(pending_list), list(sent_list), list(comments_list)
    for obj in pending_list + sent_list:
        obj.age = _age(obj.updated_at)
    for c in comments_list:
        c.age = _age(c.created_at)

    hour = now.hour
    greeting = ("Доброй ночи" if hour < 5 else "Доброе утро" if hour < 12
                else "Добрый день" if hour < 18 else "Добрый вечер")

    return render(request, "core/home.html", {
        "modules_data": modules_data,
        "users_list": users_list,
        "unread_docs": unread_docs,
        "unread_docs_list": unread_docs_list,
        "pending_total": counts["total"],
        "pending_tiles": pending_tiles,
        "pending_list": pending_list,
        "sent_list": sent_list,
        "comments_list": comments_list,
        "announcements": list(Announcement.visible()[:5]),
        "favorite_stations": _favorite_stations(user),
        "setup_hints": setup_hints,
        "greeting": greeting,
        "today": now,
    })


def _favorite_stations(user):
    try:
        from apps.accounts.profile_data import favorite_stations_status
        return favorite_stations_status(user)
    except Exception:  # блок избранного не должен ронять главную
        import logging
        logging.getLogger(__name__).exception("favorite stations failed")
        return []


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
        if request.user.is_authenticated:
            from apps.accounts.models import UserPreferences
            UserPreferences.objects.update_or_create(user=request.user, defaults={"ui_theme": name})
    return resp
