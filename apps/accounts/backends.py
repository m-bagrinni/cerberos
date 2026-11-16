"""
Защита входа от перебора паролей.

Сайт опубликован в интернет (Cloudflare-туннель), поэтому /accounts/login/ и
/admin/login/ нельзя оставлять без ограничения попыток. Бэкенд оборачивает
стандартный ModelBackend, так что лимит действует для любого входа по паролю.

Лимиты (Redis-кэш, общий для всех процессов):
  - логин:  LOGIN_MAX_FAILS_USER неудач за окно → логин блокируется на окно;
  - адрес:  LOGIN_MAX_FAILS_IP неудач за окно → адрес блокируется на окно.
Успешный вход сбрасывает счётчик логина.
"""
import hashlib
import ipaddress
import logging

from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from django.core.cache import cache
from django.core.exceptions import PermissionDenied

logger = logging.getLogger(__name__)

WINDOW = getattr(settings, "LOGIN_FAIL_WINDOW", 15 * 60)
MAX_FAILS_USER = getattr(settings, "LOGIN_MAX_FAILS_USER", 10)
MAX_FAILS_IP = getattr(settings, "LOGIN_MAX_FAILS_IP", 30)

# Запросы из интернета приходят от контейнера cloudflared (docker-сеть) —
# настоящий адрес клиента в CF-Connecting-IP. Заголовку верим только отсюда.
_PROXY_NETS = [ipaddress.ip_network("172.16.0.0/12")]


def client_ip(request) -> str:
    if request is None:
        return ""
    remote = request.META.get("REMOTE_ADDR", "")
    try:
        from_proxy = any(ipaddress.ip_address(remote) in n for n in _PROXY_NETS)
    except ValueError:
        from_proxy = False
    if from_proxy:
        cf = request.META.get("HTTP_CF_CONNECTING_IP", "").strip()
        if cf:
            return cf
    return remote


def _key(kind: str, value: str) -> str:
    h = hashlib.sha256(value.lower().encode()).hexdigest()[:32]
    return f"loginfail:{kind}:{h}"


def _fails(key: str) -> int:
    return cache.get(key, 0)


def _bump(key: str) -> int:
    if cache.add(key, 1, timeout=WINDOW):
        return 1
    try:
        return cache.incr(key)
    except ValueError:  # ключ истёк между add и incr
        cache.set(key, 1, timeout=WINDOW)
        return 1


def is_locked(request, username) -> bool:
    ip = client_ip(request)
    return (_fails(_key("user", username or "")) >= MAX_FAILS_USER
            or bool(ip and _fails(_key("ip", ip)) >= MAX_FAILS_IP))


class ThrottledModelBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(self._username_field(), "")
        ip = client_ip(request)
        ukey, ikey = _key("user", username or ""), _key("ip", ip)

        if is_locked(request, username):
            logger.warning("login throttled: user=%r ip=%s", username, ip)
            # PermissionDenied прекращает перебор бэкендов — пароль даже не проверяем
            raise PermissionDenied("too many failed logins")

        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is None:
            n = _bump(ukey)
            if ip:
                _bump(ikey)
            if n in (MAX_FAILS_USER // 2, MAX_FAILS_USER):
                logger.warning("login failures: user=%r ip=%s count=%s", username, ip, n)
        else:
            cache.delete(ukey)
        return user

    @staticmethod
    def _username_field():
        from django.contrib.auth import get_user_model
        return get_user_model().USERNAME_FIELD
