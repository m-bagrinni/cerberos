import secrets
"""Вьюхи регистрации, логина, подтверждения."""
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, ListView, View

from .forms import CerberusLoginForm, RegistrationForm
from .models import Role, UserRole
from .permissions import has_perm

User = get_user_model()


class RegisterView(CreateView):
    """Регистрация нового пользователя."""
    form_class = RegistrationForm
    template_name = "accounts/register.html"
    success_url = reverse_lazy("accounts:login")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        # Автоматически назначаем viewer (но неактивен)
        try:
            viewer = Role.objects.get(slug="viewer")
            UserRole.objects.create(user=user, role=viewer)
        except Role.DoesNotExist:
            pass
        messages.success(
            self.request,
            "Регистрация отправлена. Дождитесь подтверждения администратора.",
        )
        return redirect("accounts:login")


class CerberusLoginView(LoginView):
    """Красивый login (не Django-admin)."""
    form_class = CerberusLoginForm
    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        next_url = self.request.GET.get("next")
        if next_url:
            return next_url
        if has_perm(self.request.user, "admin.access"):
            return "/admin-ui/"
        return "/"


class CerberusLogoutView(View):
    """Кастомный logout — чистит сессию и редиректит на login."""
    http_method_names = ["get", "post"]

    def get(self, request, *args, **kwargs):
        from django.contrib.auth import logout
        logout(request)
        return redirect("accounts:login")

    def post(self, request, *args, **kwargs):
        from django.contrib.auth import logout
        logout(request)
        return redirect("accounts:login")



# === Управление pending-регистрациями ===

class PendingUsersListView(LoginRequiredMixin, ListView):
    """Список пользователей, ожидающих подтверждения."""
    template_name = "adminui/users/pending.html"
    context_object_name = "pending_users"
    login_url = "/accounts/login/"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not has_perm(request.user, "user.manage_roles"):
            raise PermissionDenied("Нет разрешения: user.manage_roles")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return User.objects.filter(
            registration_status="pending"
        ).order_by("-date_joined")


class ApproveUserView(LoginRequiredMixin, View):
    """Подтвердить пользователя."""
    login_url = "/accounts/login/"

    def post(self, request, pk):
        if not has_perm(request.user, "user.manage_roles"):
            raise PermissionDenied("Нет разрешения: user.manage_roles")

        user = get_object_or_404(User, pk=pk)
        user.is_active = True
        user.registration_status = "approved"
        user.approved_at = timezone.now()
        user.approved_by = request.user
        user.save(update_fields=[
            "is_active", "registration_status", "approved_at", "approved_by",
        ])

        try:
            viewer = Role.objects.get(slug="viewer")
            UserRole.objects.get_or_create(user=user, role=viewer)
        except Role.DoesNotExist:
            pass

        messages.success(request, f"Пользователь {user.username} подтверждён")
        return redirect("adminui:users_pending")


class RejectUserView(LoginRequiredMixin, View):
    """Отклонить регистрацию."""
    login_url = "/accounts/login/"

    def post(self, request, pk):
        if not has_perm(request.user, "user.manage_roles"):
            raise PermissionDenied("Нет разрешения: user.manage_roles")

        user = get_object_or_404(User, pk=pk)
        user.registration_status = "rejected"
        user.is_active = False
        user.registration_note = request.POST.get("note", "")[:1000]
        user.save(update_fields=[
            "registration_status", "is_active", "registration_note",
        ])

        messages.warning(request, f"Регистрация {user.username} отклонена")
        return redirect("adminui:users_pending")

# ── Личный профиль пользователя ────────────────────────────────
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from .models import UserRole, Position

@login_required
def profile_view(request):
    user = request.user
    roles = UserRole.objects.filter(user=user).select_related("role")
    
    if request.method == "POST":
        # Генерация кода для Telegram
        if "gen_tg_code" in request.POST:
            from .models import TelegramLinkCode
            import secrets
            alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
            raw = "".join(secrets.choice(alphabet) for _ in range(8))
            code = TelegramLinkCode.objects.create(user=request.user, code=raw)
            # Очищаем старые коды, чтобы не копились
            TelegramLinkCode.objects.filter(user=request.user, used_at__isnull=True).exclude(pk=code.pk).delete()
            
            return render(request, "accounts/profile.html", {
                "user_obj": request.user,
                "roles": roles,
                "form": PasswordChangeForm(request.user),
                "tg_code": code.code,
                "positions": Position.objects.filter(is_active=True).order_by("-access_level"),
                "user_access_level": __import__("apps.accounts.permissions", fromlist=["get_user_access_level"]).get_user_access_level(request.user),
            })

        # Обработка смены пароля
        if "change_password" in request.POST:
            form = PasswordChangeForm(user, request.POST)
            if form.is_valid():
                user = form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Пароль успешно изменён!")
                return redirect("profile")
        # Обработка редактирования профиля
        elif "update_profile" in request.POST:
            user.first_name = request.POST.get("first_name", "")
            user.last_name = request.POST.get("last_name", "")
            user.middle_name = request.POST.get("middle_name", "")
            user.department = request.POST.get("department", "")
            # Должность — только если пользователь имеет access_level >= 80
            from .permissions import get_user_access_level
            pos_id = request.POST.get("position_ref", "").strip()
            if get_user_access_level(user) >= 80:
                if pos_id:
                    try:
                        user.position_ref = Position.objects.get(pk=pos_id, is_active=True)
                        user.position = user.position_ref.name  # Синхронизация старого поля
                    except Position.DoesNotExist:
                        user.position_ref = None
                else:
                    user.position_ref = None
            user.work_phone = request.POST.get("work_phone", "")
            user.mobile_phone = request.POST.get("mobile_phone", "")
            user.email = request.POST.get("email", "")
            tg_id = request.POST.get("telegram_id", "").strip()
            user.telegram_id = int(tg_id) if tg_id else None
            user.save()
            messages.success(request, "Профиль обновлён!")
            return redirect("profile")
    else:
        form = PasswordChangeForm(user)
    
    # Telegram-аккаунт (для отображения состояния в шаблоне)
    try:
        from .models import TelegramAccount
        tg_account = TelegramAccount.objects.filter(user=user, is_active=True).first()
    except Exception:
        tg_account = None

    from .permissions import get_user_access_level
    return render(request, "accounts/profile.html", {
        "user_obj": user,
        "roles": roles,
        "form": form,
        "tg_account": tg_account,
        "positions": Position.objects.filter(is_active=True).order_by("-access_level"),
        "user_access_level": get_user_access_level(user),
    })


# ── Telegram-привязка ──────────────────────────────────────────
from .models import TelegramAccount, TelegramLinkCode


@login_required
def telegram_link_code(request):
    """POST: сгенерировать/вернуть свежий одноразовый код для привязки Telegram."""
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)

    fresh = (TelegramLinkCode.objects
             .filter(user=request.user, used_at__isnull=True)
             .order_by("-created_at").first())

    if fresh and fresh.is_valid():
        code = fresh
    else:
        # короткий читаемый код: без похожих символов
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        raw = "".join(secrets.choice(alphabet) for _ in range(8))
        code = TelegramLinkCode.objects.create(user=request.user, code=raw)

    # считаем остаток жизни в секундах
    ttl_left = max(0, 600 - int((timezone.now() - code.created_at).total_seconds()))
    return JsonResponse({"code": code.code, "expires_in": ttl_left})


@login_required
def telegram_unlink(request):
    """POST: отвязать текущий Telegram-аккаунт."""
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    TelegramAccount.objects.filter(user=request.user).delete()
    # заодно чистим legacy-поле в User, чтобы UI не рассинхронизировался
    if getattr(request.user, "telegram_id", None):
        request.user.telegram_id = None
        request.user.save(update_fields=["telegram_id"])
    return JsonResponse({"ok": True})


# ── Telegram WebApp login ──────────────────────────────────────
import json as _json


@csrf_exempt
@require_POST
def telegram_webapp_login(request):
    """
    Вход в WebApp по initData от Telegram.
    Тело: JSON {"initData": "..."}.
    Ответ: 200 {"ok": true, "username": "..."} + Django-сессия.
           401 {"error": "..."} — не привязан / подпись плохая / истёк.
    """
    from django.contrib.auth import login
    from .telegram_auth import validate_init_data, InitDataError
    from .models import TelegramAccount

    try:
        payload = _json.loads(request.body or "{}")
    except _json.JSONDecodeError:
        return JsonResponse({"error": "bad json"}, status=400)

    init_data = payload.get("initData") or ""
    try:
        data = validate_init_data(init_data, max_age_seconds=86400)
    except InitDataError as e:
        return JsonResponse({"error": str(e)}, status=401)

    tg_user = data.get("user") or {}
    tg_id = tg_user.get("id")
    if not tg_id:
        return JsonResponse({"error": "no tg user id"}, status=401)

    acc = (TelegramAccount.objects
           .filter(tg_id=tg_id, is_active=True)
           .select_related("user")
           .first())
    if not acc:
        return JsonResponse({"error": "not_linked"}, status=401)

    # Логин через стандартный backend
    login(request, acc.user)

    return JsonResponse({
        "ok": True,
        "username": acc.user.username,
        "tg_username": acc.username,
    })
