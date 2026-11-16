"""Базовые настройки — общие для всех окружений.

Все параметры читаются из окружения (.env / docker env).
Никаких хардкодов секретов и путей к БД здесь быть не должно.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# BASE_DIR — корень проекта (три уровня от этого файла):
#   cerber/settings/base.py -> cerber/settings -> cerber -> <корень>
BASE_DIR = Path(__file__).resolve().parent.parent.parent


# ===== Безопасность =====
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "insecure-dev-key-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = [
    h.strip()
    for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]


# ===== Приложения =====
INSTALLED_APPS = [
    'apps.servers',
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # сторонние
    "rest_framework",
    "django_celery_beat",

    # свои
    "apps.accounts",
    "apps.adminui",
    "apps.core",
    "apps.devices",
    "apps.monitoring",
    "apps.mibs",
    "apps.alerts",
    "apps.stations",
    "apps.analytics",
    "apps.subnets",
    "apps.services",
    "apps.integrations",
    'apps.edocs',
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.ApiLoginRequiredMiddleware",   # вход обязателен для /api/ (см. модуль)
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.core.middleware.TelegramWebAppFrameMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.accounts.middleware.AccessMiddleware",
    "apps.accounts.middleware.SessionActivityMiddleware",
]

ROOT_URLCONF = "cerber.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "apps.core.context_processors.alert_counts",
                "apps.core.context_processors.ui_theme",
                "apps.core.context_processors.access",
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "cerber.wsgi.application"
ASGI_APPLICATION = "cerber.asgi.application"


# ===== Пароли =====
# Вход по паролю — через бэкенд с лимитом неудачных попыток (apps/accounts/backends.py)
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.ThrottledModelBackend"]
LOGIN_FAIL_WINDOW = 15 * 60
LOGIN_MAX_FAILS_USER = 10
LOGIN_MAX_FAILS_IP = 30

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ===== Локализация =====
# === Auth ===
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = os.getenv("DJANGO_TIME_ZONE", "Asia/Bishkek")
USE_I18N = True
USE_TZ = True


# ===== Статика / медиа =====
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ===== Кастомный пользователь =====
AUTH_USER_MODEL = "accounts.User"


# ===== Celery =====
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/1")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/2")
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
# Результаты задач никто не читает — не копим их в Redis (было 60k+ ключей).
CELERY_TASK_IGNORE_RESULT = True
# Лимиты по умолчанию; быстрые периодические задачи задают свои (см. apps/monitoring/tasks.py).
CELERY_TASK_SOFT_TIME_LIMIT = 10 * 60
CELERY_TASK_TIME_LIMIT = 15 * 60
# Долгие SNMP-опросы не должны «забирать» в префетч чужие задачи.
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"


# ===== Кэш =====
# Общий для всех процессов (web + все воркеры). LocMemCache был у каждого процесса свой —
# счётчики «3 неудачи подряд» и блокировки задач с ним не работали.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.getenv("CACHE_REDIS_URL", "redis://redis:6379/3"),
        "KEY_PREFIX": "cerber",
        "TIMEOUT": 300,
    }
}


# ===== Мониторинг: хранение истории =====
# UI показывает максимум 7 суток, API отдаёт до 30.
MONITORING_RETENTION = {
    # MetricValue: история метрик (CPU, память, DDM/SFP, трафик NE)
    "metric_value_days": int(os.getenv("RETENTION_METRIC_VALUE_DAYS", "8")),
    # MetricValue: служебные метрики, нужна только последняя точка
    "metric_value_short_hours": int(os.getenv("RETENTION_METRIC_SHORT_HOURS", "24")),
    "metric_value_short_keys": [
        "eth_pvid", "eth_mode", "eth_enable", "eth_status",
        "mem_dram_total", "mem_flash_total", "flash_total",
    ],
    # InterfaceSample: трафик портов коммутаторов (~300k строк/сутки)
    "interface_sample_days": int(os.getenv("RETENTION_INTERFACE_SAMPLE_DAYS", "30")),
    # TrafficSample: broadcast/multicast по подсетям
    "traffic_sample_days": int(os.getenv("RETENTION_TRAFFIC_SAMPLE_DAYS", "30")),
}


# ===== DRF =====
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        # Только для вошедших. Web публикуется наружу через Cloudflare-туннель (Telegram WebApp),
        # поэтому открытый API = доступ из интернета. Публичный эндпоинт — только явно и осознанно.
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 100,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}



# ===== Логирование =====
LOG_LEVEL = os.getenv("DJANGO_LOG_LEVEL", "INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": "{asctime} [{levelname}] {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django.db.backends": {
            "level": "WARNING",
            "handlers": ["console"],
            "propagate": False,
        },
    },
}


# ===== Собственные настройки Cerberus =====
SNMP_DEFAULT_TIMEOUT = int(os.getenv("SNMP_DEFAULT_TIMEOUT", "2"))
SNMP_DEFAULT_RETRIES = int(os.getenv("SNMP_DEFAULT_RETRIES", "1"))
SNMP_MAX_CONCURRENCY = int(os.getenv("SNMP_MAX_CONCURRENCY", "200"))

MIB_DIR = Path(os.getenv("MIB_DIR", str(BASE_DIR / "mibs")))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CERBER_WEBAPP_URL = os.getenv("CERBER_WEBAPP_URL", "https://col-note-jeans-budget.trycloudflare.com")


# ============================================================
# Django Channels (WebSocket)
# ============================================================
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [("redis", 6379)],
        },
    },
}

CSRF_TRUSTED_ORIGINS = [
    "https://revolution-managing-continent-hurricane.trycloudflare.com",
]

# --- Active Directory (DC) ---
AD_SERVER = os.environ.get("AD_SERVER", "192.168.7.15")
AD_USER = os.environ.get("AD_USER", "")
AD_PASSWORD = os.environ.get("AD_PASSWORD", "")
AD_BASE_DN = os.environ.get("AD_BASE_DN", "")
AD_USE_SSL = os.environ.get("AD_USE_SSL", "False").lower() in ("true", "1", "yes")
AD_CA_CERT_FILE = os.environ.get("AD_CA_CERT_FILE", "")  # PEM корневого CA домена → проверка LDAPS

# --- SSH для DC ---
DC_SSH_USER = os.environ.get("DC_SSH_USER", "")
DC_SSH_PASSWORD = os.environ.get("DC_SSH_PASSWORD", "")
DC_SSH_PORT = int(os.environ.get("DC_SSH_PORT", "22"))

# ===== Agent (DC) =====
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")


# Разрешаем встраивание в iframe (Telegram WebApp)
X_FRAME_OPTIONS = "SAMEORIGIN"


# Тесты: таблицы создаются прямо по моделям. История миграций пользователя (переезд
# devices.User → accounts.User) не собирается на пустой БД, а тестам она и не нужна.
import sys as _sys  # noqa: E402
if len(_sys.argv) > 1 and _sys.argv[1] == "test":
    class _NoMigrations(dict):
        def __contains__(self, item):
            return True

        def __getitem__(self, item):
            return None

    MIGRATION_MODULES = _NoMigrations()
