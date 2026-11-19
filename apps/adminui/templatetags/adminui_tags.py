"""Теги админ-панели: активный раздел меню, ссылки пагинации, счётчики в меню."""
from django import template
from django.utils.http import urlencode

register = template.Library()

# url_name → раздел меню. Порядок важен: более длинные префиксы раньше.
_SECTIONS = (
    ("dependencies", "dependencies"),
    ("subcategor", "categories"), ("categor", "categories"),
    ("device", "devices"),
    ("profile", "profiles"), ("metric", "profiles"),
    ("vendor", "vendors"),
    ("closure", "closures"),
    ("station", "stations"),
    ("link", "links"),
    ("ne", "ne"),
    ("subnet", "subnets"),
    ("alert", "alerts"),
    ("user", "users"),
    ("preset", "presets"), ("role", "presets"),
    ("announcement", "announcements"),
    ("module", "modules"),
    ("dashboard", "dashboard"),
)


@register.simple_tag(takes_context=True)
def aui_section(context):
    """Раздел меню для текущей страницы (devices, profiles, …) — и для списка, и для карточек/форм."""
    match = getattr(context.get("request"), "resolver_match", None)
    name = (match.url_name or "") if match else ""
    for prefix, section in _SECTIONS:
        if name.startswith(prefix):
            return section
    return ""


# раздел → (подпись, имя URL списка) для хлебных крошек
_LISTS = {
    "devices": ("Устройства", "devices"), "categories": ("Категории", "categories"),
    "profiles": ("Профили SNMP", "profiles"), "vendors": ("Вендоры", "vendors"),
    "stations": ("Станции", "stations"), "links": ("Оптические линии", "links"),
    "closures": ("Муфты", "closures"), "ne": ("NE", "ne"), "subnets": ("Подсети", "subnets"),
    "alerts": ("Алерты", "alerts"), "users": ("Пользователи", "users"),
    "presets": ("Шаблоны доступа", "presets"), "announcements": ("Объявления", "announcements"),
    "modules": ("Модули главной", "modules"),
}


@register.inclusion_tag("adminui/_crumb.html", takes_context=True)
def aui_crumb(context):
    """Ссылка на список раздела — на карточках и формах (на самом списке не нужна)."""
    from django.urls import reverse
    match = getattr(context.get("request"), "resolver_match", None)
    name = (match.url_name or "") if match else ""
    item = _LISTS.get(aui_section(context))
    if not item or name == item[1]:
        return {"crumb": None}
    return {"crumb": {"label": item[0], "url": reverse("adminui:" + item[1])}}


@register.simple_tag(takes_context=True)
def aui_list_url(context):
    """URL списка текущего раздела (для кнопок «Отмена»/«Назад»)."""
    from django.urls import reverse
    item = _LISTS.get(aui_section(context))
    return reverse("adminui:" + item[1]) if item else reverse("adminui:dashboard")


@register.simple_tag(takes_context=True)
def page_url(context, page):
    """?page=N с сохранением остальных параметров запроса (фильтры, поиск)."""
    params = context["request"].GET.copy()
    params["page"] = page
    return "?" + urlencode(sorted(params.lists()), doseq=True)


@register.simple_tag
def aui_pending_users():
    from apps.accounts.models import User
    return User.objects.filter(registration_status="pending").count()


@register.simple_tag
def aui_firing_alerts():
    from apps.alerts.models import Alert
    return Alert.objects.filter(state="firing").count()


@register.filter
def widget_kind(bound_field):
    """Тип виджета поля для раскладки: checkbox / textarea / multi / select / input."""
    w = bound_field.field.widget
    name = type(w).__name__.lower()
    if getattr(w, "input_type", "") == "checkbox" and "multiple" not in name:
        return "checkbox"
    if "textarea" in name:
        return "textarea"
    if "multiple" in name or getattr(w, "allow_multiple_selected", False):
        return "multi"
    if "select" in name:
        return "select"
    return "input"
