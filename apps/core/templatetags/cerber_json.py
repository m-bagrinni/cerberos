"""
{{ value|js_json }} — безопасная вставка JSON внутрь <script>.

`{{ x|safe }}` с JSON опасен: строка «</script><script>…» из данных (имя NE,
sysDescr, описание порта) закрывает тег и исполняется. Здесь <, >, & и
разделители строк экранируются как \\uXXXX — JSON остаётся тем же для JS.
"""
import json

from django import template
from django.core.serializers.json import DjangoJSONEncoder
from django.utils.safestring import mark_safe

register = template.Library()

_ESC = {
    ord("<"): "\\u003C", ord(">"): "\\u003E", ord("&"): "\\u0026",
    ord(" "): "\\u2028", ord(" "): "\\u2029",
}


@register.filter
def js_json(value, default="null"):
    """Строку считаем готовым JSON; прочее сериализуем."""
    if value is None or value == "":
        text = default
    elif isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, cls=DjangoJSONEncoder, ensure_ascii=False)
    return mark_safe(text.translate(_ESC))
