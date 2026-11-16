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
        # Только свой хост: иначе ?next=https://фишинг уводит после входа (open redirect)
        from django.utils.http import url_has_allowed_host_and_scheme
        next_url = self.request.POST.get("next") or self.request.GET.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return next_url
        # Личная стартовая страница из профиля (только из белого списка)
        from .models import UserPreferences
        start = (UserPreferences.objects.filter(user=self.request.user)
                 .values_list("start_page", flat=True).first() or "")
        if start and start in dict(UserPreferences.START_CHOICES):
            return start
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
from datetime import timedelta

PROFILE_TABS = [
    ("profile", "Профиль", "bi-person"),
    ("notify", "Уведомления", "bi-bell"),
    ("ui", "Интерфейс", "bi-sliders"),
    ("security", "Безопасность", "bi-shield-lock"),
    ("access", "Мои права", "bi-key"),
    ("activity", "Активность", "bi-clock-history"),
]


def _profile_redirect(tab):
    from django.urls import reverse
    return redirect(f"{reverse('profile')}?tab={tab}")


def _tg_deeplink(token):
    if not token:
        return ""
    from .telegram_link import bot_username
    name = bot_username()
    return f"https://t.me/{name}?start={token}" if name else ""


def _parse_time(value, default):
    from datetime import datetime
    try:
        return datetime.strptime((value or "").strip(), "%H:%M").time()
    except ValueError:
        return default


@login_required
def profile_view(request):
    from .models import TelegramAccount, TelegramLinkCode, UserPreferences, LoginEvent
    from .permissions import get_user_access_level
    from . import profile_data
    from apps.stations.models import Station

    user = request.user
    prefs = UserPreferences.for_user(user)
    tab = request.GET.get("tab") or request.POST.get("tab") or "profile"
    if tab not in {t[0] for t in PROFILE_TABS}:
        tab = "profile"
    form = PasswordChangeForm(user)
    tg_code = None

    if request.method == "POST":
        action = request.POST.get("action", "")
        # совместимость со старой формой
        if "gen_tg_code" in request.POST:
            action = "tg_code"
        elif "change_password" in request.POST:
            action = "password"
        elif "update_profile" in request.POST:
            action = "profile"

        if action == "tg_code":
            from .telegram_link import issue_token
            tg_code = issue_token(user)  # показываем один раз; в базе — только хэш
            tab = "profile"

        elif action == "password":
            form = PasswordChangeForm(user, request.POST)
            if form.is_valid():
                user = form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Пароль успешно изменён!")
                return _profile_redirect("profile")
            tab = "profile"

        elif action == "profile":
            user.first_name = request.POST.get("first_name", "")
            user.last_name = request.POST.get("last_name", "")
            user.middle_name = request.POST.get("middle_name", "")
            user.department = request.POST.get("department", "")
            # Поле есть в форме только у кого есть «Пользователи и права»; без него должность не трогаем
            # (раньше пустое значение молча снимало должность — и вместе с ней права)
            pos_id = request.POST.get("position_ref", "").strip()
            if "position_ref" in request.POST and user.access.has_cap("users_manage"):
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
            # telegram_id меняется только привязкой бота (apps/accounts/telegram_link.py)
            user.save()
            messages.success(request, "Профиль обновлён!")
            return _profile_redirect("profile")

        elif action == "avatar":
            upload = request.FILES.get("avatar")
            if not upload:
                messages.error(request, "Выберите файл.")
                return _profile_redirect("profile")
            try:
                content, name = profile_data.process_avatar(upload)
            except ValueError as e:
                messages.error(request, str(e))
                return _profile_redirect("profile")
            old = user.avatar.name if user.avatar else ""
            user.avatar.save(name, content, save=True)
            if old and old != user.avatar.name:
                user.avatar.storage.delete(old)
            messages.success(request, "Аватар обновлён.")
            return _profile_redirect("profile")

        elif action == "avatar_delete":
            if user.avatar:
                user.avatar.delete(save=True)
            messages.success(request, "Аватар удалён.")
            return _profile_redirect("profile")

        elif action == "notify":
            p = request.POST
            prefs.notify_min_severity = p.get("notify_min_severity") if p.get("notify_min_severity") in ("warn", "crit") else "warn"
            prefs.notify_resolved = bool(p.get("notify_resolved"))
            prefs.notify_broadcast = bool(p.get("notify_broadcast"))
            prefs.quiet_enabled = bool(p.get("quiet_enabled"))
            prefs.quiet_from = _parse_time(p.get("quiet_from"), prefs.quiet_from)
            prefs.quiet_to = _parse_time(p.get("quiet_to"), prefs.quiet_to)
            prefs.quiet_allow_crit = bool(p.get("quiet_allow_crit"))
            prefs.save()
            ids = [int(x) for x in p.getlist("notify_stations") if x.isdigit()]
            prefs.notify_stations.set(Station.objects.filter(pk__in=ids))
            acc = TelegramAccount.objects.filter(user=user).first()
            if acc is not None:
                acc.alerts_enabled = bool(p.get("alerts_enabled"))
                acc.save(update_fields=["alerts_enabled"])
            messages.success(request, "Настройки уведомлений сохранены.")
            return _profile_redirect("notify")

        elif action == "notify_test":
            from .telegram_notify import send_telegram_message
            acc = TelegramAccount.objects.filter(user=user, is_active=True).first()
            if acc is None:
                messages.error(request, "Telegram не привязан — сначала привяжите его на вкладке «Профиль».")
            elif send_telegram_message(acc.tg_id, "🔔 <b>Cerberus</b>\nТестовое уведомление из профиля. Если вы это видите — всё работает."):
                messages.success(request, "Тестовое сообщение отправлено в Telegram.")
            else:
                messages.error(request, "Telegram не принял сообщение. Проверьте, что бот не заблокирован.")
            return _profile_redirect("notify")

        elif action == "ui":
            p = request.POST
            theme = p.get("ui_theme", "")
            prefs.ui_theme = theme if theme in dict(UserPreferences.THEME_CHOICES) else ""
            start = p.get("start_page", "")
            prefs.start_page = start if start in dict(UserPreferences.START_CHOICES) else ""
            prefs.save()
            ids = [int(x) for x in p.getlist("favorite_stations") if x.isdigit()]
            prefs.favorite_stations.set(Station.objects.filter(pk__in=ids))
            messages.success(request, "Настройки интерфейса сохранены.")
            resp = _profile_redirect("ui")
            if prefs.ui_theme:
                from apps.core.context_processors import UI_THEME_COOKIE
                resp.set_cookie(UI_THEME_COOKIE, prefs.ui_theme, max_age=365 * 24 * 3600, samesite="Lax")
            return resp

        elif action == "end_sessions":
            only = request.POST.get("session_id", "")
            n = profile_data.end_sessions(user, request.session.session_key,
                                         only_id=int(only) if only.isdigit() else None)
            messages.success(request, f"Завершено сеансов: {n}." if n else "Других сеансов нет.")
            return _profile_redirect("security")

    ctx = {
        "user_obj": user,
        "tab": tab,
        "tabs": PROFILE_TABS,
        "prefs": prefs,
        "roles": UserRole.objects.filter(user=user).select_related("role"),
        "form": form,
        "tg_code": tg_code,
        "tg_deeplink": _tg_deeplink(tg_code),
        "tg_account": TelegramAccount.objects.filter(user=user, is_active=True).first(),
        "positions": Position.objects.filter(is_active=True).order_by("-access_level"),
        "user_access_level": get_user_access_level(user),
    }
    if tab in ("notify", "ui"):
        ctx["stations"] = Station.objects.all().order_by("sort_order", "name")
        ctx["notify_station_ids"] = set(prefs.notify_stations.values_list("pk", flat=True))
        ctx["favorite_ids"] = set(prefs.favorite_stations.values_list("pk", flat=True))
        ctx["theme_choices"] = UserPreferences.THEME_CHOICES
        ctx["start_choices"] = UserPreferences.START_CHOICES
    if tab == "security":
        ctx["sessions"] = profile_data.active_sessions(user, request.session.session_key)
        events = list(LoginEvent.objects.filter(user=user)[:25])
        for e in events:
            e.device = profile_data.describe_ua(e.user_agent)
        ctx["login_events"] = events
        ctx["failed_24h"] = LoginEvent.objects.filter(
            user=user, success=False,
            created_at__gte=timezone.now() - timedelta(hours=24),
        ).count()
    if tab == "access":
        ctx["access"] = profile_data.access_summary(user)
    if tab == "activity":
        ctx["activity"] = profile_data.recent_activity(user)
    return render(request, "accounts/profile.html", ctx)


@login_required
def telegram_link_code(request):
    """POST: новый одноразовый токен привязки Telegram (10 мин). В базе — только его хэш."""
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    from .telegram_link import TOKEN_TTL_MIN, issue_token
    token = issue_token(request.user)
    return JsonResponse({"code": token, "deeplink": _tg_deeplink(token), "expires_in": TOKEN_TTL_MIN * 60})


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
