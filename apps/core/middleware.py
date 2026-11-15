"""
Снятие X-Frame-Options для путей, которые открываются как Telegram WebApp.

Telegram рендерит WebApp в <iframe> с домена web.telegram.org — значит
X-Frame-Options: DENY/SAMEORIGIN сломает встраивание. Снимаем заголовок
ТОЛЬКО на нужных путях (не глобально), чтобы не ослаблять безопасность.
"""
from django.utils.deprecation import MiddlewareMixin

# Пути, которые должны открываться внутри Telegram WebApp
WEBAPP_PREFIXES = ("/local/", "/api/", "/accounts/", "/static/")


class TelegramWebAppFrameMiddleware(MiddlewareMixin):
    def process_response(self, request, response):
        path = request.path or ""
        if any(path.startswith(p) for p in WEBAPP_PREFIXES):
            response.headers.pop("X-Frame-Options", None)
            response.headers.pop("x-frame-options", None)
            # если CSP где-то задан и запрещает frame — тоже снимаем
            csp = response.headers.get("Content-Security-Policy")
            if csp and "frame-ancestors" in csp:
                import re
                csp = re.sub(r"frame-ancestors[^;]*;?", "", csp)
                response.headers["Content-Security-Policy"] = csp
        return response


# ── Обязательный вход для всех JSON-API ─────────────────────────────────────
# Web опубликован в интернет через Cloudflare-туннель (Telegram WebApp), поэтому
# любое «забытое» представление без проверки входа = доступ для всех.
# Здесь — страховка на уровне путей; права на конкретные действия — во вьюхах.
from django.http import JsonResponse  # noqa: E402

API_PREFIXES = ("/api/", "/analytics/api/", "/servers/api/")
# Эндпоинты без сессии — каждый со своей защитой:
API_PUBLIC_PATHS = {
    "/servers/api/agent/report/",   # агент DC, проверяет X-Agent-Token
}


class ApiLoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if (
            path.startswith(API_PREFIXES)
            and path not in API_PUBLIC_PATHS
            and not request.user.is_authenticated
        ):
            return JsonResponse({"ok": False, "error": "authentication required"}, status=401)
        return self.get_response(request)
