"""
Совместимость со старыми кодами прав: has_perm() и PermissionRequiredMixin
переводят их в новую модель (apps/accounts/access.py, legacy_allows).
"""
from functools import wraps
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.contrib.auth.mixins import AccessMixin


# ── Уровень должности ──────────────────────────────────────────
# ── has_perm ─────────────────────────────────────────────────
def has_perm(user, perm: str) -> bool:
    """
    Проверка права по новой модели доступа (apps/accounts/access.py).
    Старые коды ("device.update", "subnet.view", "user.manage_roles", "admin.access", "local")
    переводятся в раздел/уровень или возможность — см. access.legacy_allows.
    """
    if not user or not user.is_authenticated:
        return False
    from .access import Access, legacy_allows
    return legacy_allows(Access.for_user(user), perm)


# ── Кастомный PermissionRequiredMixin ────────────────────────
class PermissionRequiredMixin(AccessMixin):
    """
    Миксин для Django CBV: проверяет has_perm(user, self.permission_required).
    Использует has_perm → новая модель доступа (apps/accounts/access.py).
    """
    permission_required = None
    raise_exception = True  # 403 вместо редиректа (по умолчанию)

    def get_permission_required(self):
        if self.permission_required is None:
            return []
        if isinstance(self.permission_required, str):
            return [self.permission_required]
        return list(self.permission_required)

    def has_permission(self):
        perms = self.get_permission_required()
        if not perms:
            return True
        user = self.request.user
        if not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        # Если в кластере прав есть любой из модулей - все равно проверяем по нашему has_perm
        return all(has_perm(user, p) for p in perms)

    def dispatch(self, request, *args, **kwargs):
        if not self.has_permission():
            if self.raise_exception or request.user.is_authenticated:
                raise PermissionDenied("Недостаточно прав.")
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)
