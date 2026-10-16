"""Настройки для разработки."""
import os

from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]


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
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
