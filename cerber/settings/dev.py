"""Настройки для разработки."""
import os

from .base import *  # noqa: F401,F403

# DEBUG выключен по умолчанию: сервер опубликован в интернет через Cloudflare-туннель,
# а страница ошибки DEBUG показывает настройки, SQL и переменные окружения.
# Включить для отладки: CERBER_DEBUG=1 (отдельная переменная — DJANGO_DEBUG=1 уже стоит в .env).
DEBUG = os.getenv("CERBER_DEBUG", "0") == "1"

# Хосты: из DJANGO_ALLOWED_HOSTS (.env) + любые *.trycloudflare.com —
# адрес quick-tunnel меняется при каждом перезапуске cloudflared.
ALLOWED_HOSTS = [
    h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()
] + [".trycloudflare.com"]

# Статика как раньше (из static/ без collectstatic, с подхватом правок) — при DEBUG=False
# whitenoise по умолчанию отдавал бы устаревшую копию из staticfiles/.
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True

# ===== База данных (PostgreSQL из Docker) =====
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "cerber"),
        "USER": os.getenv("POSTGRES_USER", "cerber"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "cerber"),
        "HOST": os.getenv("POSTGRES_HOST", "postgres"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}

# ===== Доверенные origin-ы для CSRF =====
CSRF_TRUSTED_ORIGINS = [
    "https://*.trycloudflare.com",   # quick-tunnel: адрес меняется при перезапуске
    "https://revolution-managing-continent-hurricane.trycloudflare.com",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "https://stolen-carl-abs-variations.trycloudflare.com",
]

# За Cloudflare Tunnel Django должен знать, что оригинальный запрос был HTTPS
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --- Настройки кук для Telegram WebApp (iframe) ---


# Отключаем кэширование HTML в dev
CACHE_MIDDLEWARE_SECONDS = 0
