"""Context processors для всех шаблонов."""
from apps.alerts.models import Alert


def alert_counts(request):
    """
    Добавляет в контекст количество активных алертов.
    Используется в base.html для бейджа в sidebar.
    """
    try:
        firing = Alert.objects.filter(state=Alert.STATE_FIRING)
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
    value = request.COOKIES.get(UI_THEME_COOKIE, UI_THEME_DEFAULT)
    return value if value in UI_THEMES else UI_THEME_DEFAULT


def ui_theme(request):
    theme = get_ui_theme(request)
    return {"ui_theme": theme, "ui_theme_glass": theme == "glass"}
