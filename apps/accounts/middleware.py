"""
Middleware для контроля доступа по модулям и readonly-режима.
"""
from django.http import HttpResponseForbidden
from django.utils.deprecation import MiddlewareMixin


class ReadOnlyMiddleware(MiddlewareMixin):
    """
    Блокирует POST/PUT/DELETE запросы для пользователей с is_readonly=True.
    Исключения:
      - /accounts/ (профиль, telegram-link, logout)
      - /admin/ (Django admin)
    """
    SAFE_PATHS = (
        '/accounts/',
        '/admin/',
        '/edocs/',          # E-Docs разрешен для readonly (создание документов)
        '/api/v1/telegram/',
    )
    SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS', 'TRACE')

    def process_request(self, request):
        if request.method in self.SAFE_METHODS:
            return None

        if not request.user.is_authenticated:
            return None

        if request.user.is_superuser:
            return None

        pos = getattr(request.user, 'position_ref', None)
        if not pos or not getattr(pos, 'is_readonly', False):
            return None

        for safe in self.SAFE_PATHS:
            if request.path.startswith(safe):
                return None

        return HttpResponseForbidden("Доступ запрещен: ваша должность подразумевает только просмотр (Read-Only).")
