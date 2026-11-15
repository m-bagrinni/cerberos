from django.contrib.auth.models import AbstractUser
"""Модели прав и ролей (RBAC)."""
from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import timedelta


class Permission(models.Model):
    """Разрешение (атомарное право)."""
    code = models.SlugField(unique=True, db_index=True,
                            help_text="device.view, alert.resolve, ...")
    name = models.CharField(max_length=200)
    group = models.CharField(max_length=50, db_index=True,
                             help_text="device / profile / alert / subnet / ne / vendor / user / system")
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["group", "code"]
        verbose_name = "Разрешение"
        verbose_name_plural = "Разрешения"

    def __str__(self):
        return f"{self.code} — {self.name}"


class Role(models.Model):
    """Роль — набор разрешений."""
    slug = models.SlugField(unique=True, db_index=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    permissions = models.ManyToManyField(
        Permission, blank=True, related_name="roles"
    )
    is_system = models.BooleanField(
        default=False,
        help_text="Системную роль нельзя удалить (admin, operator, viewer)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Роль"
        verbose_name_plural = "Роли"

    def __str__(self):
        return self.name


class UserRole(models.Model):
    """Назначение роли пользователю."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="user_roles",
    )
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    assigned_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_roles",
    )

    class Meta:
        unique_together = ("user", "role")
        ordering = ["-assigned_at"]
        verbose_name = "Роль пользователя"
        verbose_name_plural = "Роли пользователей"

    def __str__(self):
        return f"{self.user.username} → {self.role.slug}"


class TelegramLinkCode(models.Model):
    """Одноразовый код для привязки Telegram → Cerberus (TTL 10 мин)."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tg_link_codes",
    )
    code = models.CharField(max_length=32, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Код привязки Telegram"
        verbose_name_plural = "Коды привязки Telegram"

    def is_valid(self):
        # код живёт 10 минут и одноразовый
        return self.used_at is None and (timezone.now() - self.created_at) < timedelta(minutes=10)

    def __str__(self):
        return f"{self.user.username} → {self.code}"


class TelegramAccount(models.Model):
    """Привязка Telegram-аккаунта к пользователю Cerberus."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="telegram_account",
    )
    tg_id = models.BigIntegerField(unique=True, db_index=True)
    username = models.CharField(max_length=64, blank=True)
    first_name = models.CharField(max_length=128, blank=True)
    linked_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    alerts_enabled = models.BooleanField(
        default=True,
        help_text="Получать уведомления об алертах",
    )

    class Meta:
        ordering = ["-linked_at"]
        verbose_name = "Telegram-аккаунт"
        verbose_name_plural = "Telegram-аккаунты"

    def __str__(self):
        return f"@{self.username or self.tg_id} → {self.user.username}"


class Position(models.Model):
    """Должность пользователя с уровнем допуска (100 — админ, 10 — наблюдатель)."""
    name = models.CharField(max_length=150, unique=True, verbose_name="Название должности")
    access_level = models.PositiveIntegerField(
        default=10, verbose_name="Уровень допуска",
        help_text="100 — полный доступ, 10 — только просмотр",
    )
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    description = models.TextField(blank=True, verbose_name="Описание")
    modules = models.CharField(
        max_length=255, blank=True, default="",
        verbose_name="Доступные модули",
        help_text="Через запятую: local,servers,edocs. Пусто = все модули.",
    )
    is_readonly = models.BooleanField(
        default=False,
        verbose_name="Только просмотр",
        help_text="Если включено, пользователь не может менять данные.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-access_level", "name")
        verbose_name = "Должность"
        verbose_name_plural = "Должности"

    def __str__(self):
        return f"{self.name} (ур. {self.access_level})"


class User(AbstractUser):
    """Кастомный пользователь — расширенный профиль."""
    telegram_id = models.BigIntegerField(
        null=True, blank=True, unique=True,
        help_text="Telegram chat_id для уведомлений",
    )
    middle_name = models.CharField(max_length=100, blank=True, verbose_name="Отчество")
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True, verbose_name="Аватар")
    position = models.CharField(max_length=150, blank=True, verbose_name="Должность (старое)")
    def has_module_access(self, module: str) -> bool:
        """Проверяет, есть ли у пользователя доступ к модулю."""
        if self.is_superuser:
            return True
        pos = getattr(self, "position_ref", None)
        if not pos:
            return False
        if not pos.modules:
            return True  # пусто = все модули
        return module in [m.strip() for m in pos.modules.split(",")]

    def is_readonly_for(self, module: str) -> bool:
        """Только просмотр для модуля?"""
        if self.is_superuser:
            return False
        pos = getattr(self, "position_ref", None)
        if not pos:
            return True
        return bool(pos.is_readonly)

    @property
    def can_local(self):
        return self.has_module_access('local')

    @property
    def can_servers(self):
        return self.has_module_access('servers')

    @property
    def can_edocs(self):
        return self.has_module_access('edocs')

    @property
    def is_admin_access(self):
        if self.is_superuser or self.is_staff:
            return True
        pos = getattr(self, "position_ref", None)
        return bool(pos and pos.access_level >= 80)

    position_ref = models.ForeignKey(
        "Position",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="users",
        verbose_name="Должность",
    )
    department = models.CharField(max_length=150, blank=True, verbose_name="Отдел")
    work_phone = models.CharField(max_length=50, blank=True, verbose_name="Рабочий телефон")
    mobile_phone = models.CharField(max_length=50, blank=True, verbose_name="Сотовый телефон")

    registration_status = models.CharField(
        max_length=16,
        choices=[
            ("pending", "Ожидает подтверждения"),
            ("approved", "Подтверждён"),
            ("rejected", "Отклонён"),
        ],
        default="approved",
        db_index=True,
        help_text="pending — новая регистрация, ждёт админа",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        "self", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="approved_users",
    )
    registration_note = models.TextField(blank=True)

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def __str__(self):
        return self.username

    @property
    def short_name(self):
        """Возвращает Фамилия И.О. (например, Багрин М.В.)"""
        last = self.last_name.strip() if self.last_name else self.username
        first = self.first_name.strip() if self.first_name else ""
        middle = self.middle_name.strip() if self.middle_name else ""
        
        if not first:
            return last
        if middle:
            return f"{last} {first[0]}.{middle[0]}."
        return f"{last} {first[0]}."
