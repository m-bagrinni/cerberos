"""
Права доступа: Position (должность) + UserRole (роль) + кастомный миксин.
"""
from functools import wraps
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.contrib.auth.mixins import AccessMixin


# ── Уровень должности ──────────────────────────────────────────
def get_user_access_level(user) -> int:
    if not user.is_authenticated:
        return 0
    if user.is_superuser:
        return 1000
    pos = getattr(user, "position_ref", None)
    return getattr(pos, "access_level", 0) if pos else 0


# ── Список прав пользователя ──────────────────────────────────
def get_user_permissions(user) -> set:
    """
    Возвращает set прав (например, {'admin.access', 'user.manage_roles'}):
    - из Position.modules (local / servers / edocs);
    - из UserRole → Role.permissions.
    """
    if not user or not user.is_authenticated:
        return set()
    if user.is_superuser:
        return {"*"}  # всё

    perms = set()

    # 1. Из Position.modules
    pos = getattr(user, "position_ref", None)
    if pos:
        if pos.modules:
            for m in pos.modules.split(","):
                m = m.strip()
                if m:
                    perms.add(m)
        # Высокий уровень — можно добавить "admin.access"
        if pos.access_level >= 80:
            perms.add("admin.access")
            perms.add("user.manage_roles")

    # 2. Из UserRole
    try:
        from .models import UserRole
        for ur in UserRole.objects.filter(user=user).select_related("role"):
            rp = getattr(ur.role, "permissions", None) or []
            if isinstance(rp, list):
                perms.update(rp)
            elif isinstance(rp, str):
                perms.update(p.strip() for p in rp.split(",") if p.strip())
    except Exception:
        pass

    return perms


# ── has_perm ─────────────────────────────────────────────────
def has_perm(user, perm: str) -> bool:
    """
    Универсальная проверка:
    1) суперюзер — всё;
    2) access_level >= 80 — всё;
    3) perm — модуль (local/servers/edocs) → Position.modules;
    4) иначе — get_user_permissions.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    level = get_user_access_level(user)
    if level >= 80:
        return True

    if perm in ("local", "servers", "edocs"):
        return bool(getattr(user, "has_module_access", lambda m: False)(perm))

    return perm in get_user_permissions(user)


# ── Кастомный PermissionRequiredMixin ────────────────────────
class PermissionRequiredMixin(AccessMixin):
    """
    Миксин для Django CBV: проверяет has_perm(user, self.permission_required).
    Использует наш has_perm (Position + UserRole).
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


# ── Декораторы ────────────────────────────────────────────────
def require_access_level(min_level: int):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("/accounts/login/")
            if get_user_access_level(request.user) < min_level:
                raise PermissionDenied("Недостаточно прав.")
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator


def require_module(module: str):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("/accounts/login/")
            if not getattr(request.user, "has_module_access", lambda m: False)(module):
                raise PermissionDenied(f"Нет доступа к модулю {module}.")
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator
