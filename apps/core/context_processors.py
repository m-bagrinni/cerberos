"""Context processors для всех шаблонов."""
from apps.alerts.models import Alert


def alert_counts(request):
    """
    Добавляет в контекст количество активных алертов.
    Используется в base.html для бейджа в sidebar.
    """
    try:
        # Следствия каскадов (suppressed_by) не считаем — они свёрнуты под причину
        firing = Alert.objects.filter(state=Alert.STATE_FIRING, suppressed_by__isnull=True)
        total = firing.count()
        crit = firing.filter(severity=Alert.SEVERITY_CRIT).count()
        warn = firing.filter(severity=Alert.SEVERITY_WARN).count()
        return {
            "alerts_firing_total": total,
            "alerts_firing_crit": crit,
            "alerts_firing_warn": warn,
        }
    except Exception:
        # На случай миграций / пустой БД
        return {
            "alerts_firing_total": 0,
            "alerts_firing_crit": 0,
            "alerts_firing_warn": 0,
        }


# ── Тема интерфейса ─────────────────────────────────────────────
# «glass» — новая тема (theme.css, тёмное стекло), «classic» — прежняя (glass.css).
# Выбор хранится в cookie на 1 год; переключение — /ui-theme/<имя>/ (ссылка в меню).
# Откат для всех сразу: UI_THEME_DEFAULT = "classic".
UI_THEMES = ("glass", "classic")
UI_THEME_DEFAULT = "glass"
UI_THEME_COOKIE = "ui_theme"


def get_ui_theme(request):
    value = request.COOKIES.get(UI_THEME_COOKIE)
    if value is None:
        # Нет cookie (новый браузер/компьютер) — берём тему из профиля
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            prefs = getattr(user, "_cached_prefs_theme", None)
            if prefs is None:
                from apps.accounts.models import UserPreferences
                prefs = (UserPreferences.objects.filter(user=user)
                         .values_list("ui_theme", flat=True).first() or "")
                user._cached_prefs_theme = prefs
            value = prefs
    return value if value in UI_THEMES else UI_THEME_DEFAULT


def ui_theme(request):
    theme = get_ui_theme(request)
    return {"ui_theme": theme, "ui_theme_glass": theme == "glass"}


def access(request):
    """{{ acc }} — права текущего пользователя (apps/accounts/access.py)."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    from apps.accounts.access import Access
    return {"acc": Access.for_user(user)}
