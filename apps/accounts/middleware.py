"""
Middleware прав доступа (AccessMiddleware) и учёта сессий.
"""


from django.http import JsonResponse  # noqa: E402


def _forbid(request, msg):
    if request.path.startswith(("/api/", "/servers/api/", "/analytics/api/")) \
            or request.headers.get("X-Requested-With") == "XMLHttpRequest" \
            or "application/json" in request.headers.get("Accept", ""):
        return JsonResponse({"ok": False, "error": msg}, status=403)
    from django.shortcuts import render
    return render(request, "403.html", {"reason": msg}, status=403)


class AccessMiddleware:
    """
    Единая проверка прав: адрес → раздел → нужный уровень (GET — просмотр, запись — изменение)
    или возможность. Реестр разделов и адресов — apps/accounts/access.py.
    Заменяет ReadOnlyMiddleware и ModuleAccessMiddleware.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not user.is_superuser:
            from .access import Access, rule_for, LEVEL_LABELS
            rule = rule_for(request.path, request.method)
            acc = Access.for_user(user)
            if rule is not None and rule.any_local and not acc.allows(rule) and acc.any_local \
                    and request.method in ("GET", "HEAD"):
                from django.shortcuts import redirect
                return redirect(acc.first_url("local"))
            if rule is not None and not acc.allows(rule):
                if rule.cap:
                    msg = f"Нет права «{rule.reason}»."
                elif rule.section:
                    msg = f"Нет доступа: «{rule.reason}» ({LEVEL_LABELS[rule.level].lower()})."
                else:
                    msg = f"Нет доступа: {rule.reason}."
                return _forbid(request, msg)
        return self.get_response(request)


# Старые имена — на случай, если на них где-то ссылаются
ReadOnlyMiddleware = AccessMiddleware
ModuleAccessMiddleware = AccessMiddleware


class SessionActivityMiddleware:
    """Отмечает активность сессии для вкладки «Безопасность» (не чаще раза в 2 минуты)."""
    INTERVAL = 120

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            user = getattr(request, "user", None)
            key = getattr(getattr(request, "session", None), "session_key", None)
            if user is not None and user.is_authenticated and key:
                from django.core.cache import cache
                if cache.add(f"sess_touch:{key}", 1, timeout=self.INTERVAL):
                    from django.utils import timezone
                    from .backends import client_ip
                    from .models import UserSession
                    UserSession.objects.update_or_create(
                        session_key=key,
                        defaults={
                            "user": user,
                            "last_seen": timezone.now(),
                            "ip": client_ip(request)[:64],
                            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:255],
                        },
                    )
        except Exception:  # учёт сессий не должен ронять запрос
            pass
        return response
