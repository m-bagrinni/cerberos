"""Журнал входов и учёт сессий (вкладка «Безопасность» в профиле)."""
import logging

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _meta(request):
    from .backends import client_ip
    if request is None:
        return "", ""
    return client_ip(request)[:64], request.META.get("HTTP_USER_AGENT", "")[:255]


@receiver(user_logged_in)
def _on_login(sender, request, user, **kwargs):
    from .models import LoginEvent, UserSession
    ip, ua = _meta(request)
    try:
        LoginEvent.objects.create(user=user, username=user.get_username()[:150], ip=ip, user_agent=ua, success=True)
        if request is not None and hasattr(request, "session"):
            if not request.session.session_key:
                request.session.save()
            UserSession.objects.update_or_create(
                session_key=request.session.session_key,
                defaults={"user": user, "ip": ip, "user_agent": ua},
            )
    except Exception:  # журнал не должен ломать вход
        logger.exception("login journal failed")


@receiver(user_login_failed)
def _on_login_failed(sender, credentials, request=None, **kwargs):
    from django.contrib.auth import get_user_model
    from .models import LoginEvent
    ip, ua = _meta(request)
    username = (credentials or {}).get("username", "") or ""
    try:
        user = get_user_model().objects.filter(username=username).first() if username else None
        LoginEvent.objects.create(user=user, username=username[:150], ip=ip, user_agent=ua, success=False)
    except Exception:
        logger.exception("login journal failed")


@receiver(user_logged_out)
def _on_logout(sender, request, user, **kwargs):
    from .models import UserSession
    key = getattr(getattr(request, "session", None), "session_key", None)
    if key:
        UserSession.objects.filter(session_key=key).delete()
