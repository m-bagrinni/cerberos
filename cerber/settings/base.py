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
    "apps.core",
    "apps.devices",
    "apps.monitoring",
    "apps.mibs",
    "apps.alerts",
        "apps.stations",
    "apps.integrations",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
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
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ===== Локализация =====
LANGUAGE_CODE = "ru-ru"
TIME_ZONE = os.getenv("DJANGO_TIME_ZONE", "Asia/Bishkek")
USE_I18N = True
USE_TZ = True


# ===== Статика / медиа =====
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ===== Кастомный пользователь =====
AUTH_USER_MODEL = "devices.User"


# ===== Celery =====
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/1")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/2")
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"


# ===== DRF =====
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        # Пока API открыт для чтения — чтобы отлаживать фронт без логина.
        # На проде заменить на IsAuthenticated.
        "rest_framework.permissions.AllowAny",
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
