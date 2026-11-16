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
    code = models.CharField(max_length=64, unique=True, db_index=True,
                            help_text="SHA-256 токена привязки (сам токен не хранится)")
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
        return self.name  # уровень больше не влияет на права (см. access.py)


class User(AbstractUser):
    """Кастомный пользователь — расширенный профиль."""
    telegram_id = models.BigIntegerField(
        null=True, blank=True, unique=True, editable=False,
        help_text="Заполняется привязкой Telegram-бота (вручную не меняется)",
    )
    middle_name = models.CharField(max_length=100, blank=True, verbose_name="Отчество")
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True, verbose_name="Аватар")
    position = models.CharField(max_length=150, blank=True, verbose_name="Должность (старое)")
    # ── Доступ: всё вычисляется из access_sections / access_caps (apps/accounts/access.py) ──
    @property
    def access(self):
        from .access import Access
        return Access.for_user(self)

    def has_module_access(self, module: str) -> bool:
        """Есть ли хоть один раздел модуля (local / servers / edocs) с доступом на просмотр."""
        from .access import VIEW
        return self.access.group_level(module) >= VIEW

    def is_readonly_for(self, module: str) -> bool:
        """В модуле нет ни одного раздела с правом изменения."""
        from .access import EDIT
        return self.access.group_level(module) < EDIT

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
    def access_summary(self):
        """Коротко для списков: ['Устройства', 'Подсети (просмотр)', ...]."""
        from .access import SECTIONS, CAP_BY_KEY, VIEW, EDIT, clean_caps, clean_sections
        sec = clean_sections(self.access_sections)
        out = [s.name if sec[s.key] == EDIT else f"{s.name} (просмотр)"
               for s in SECTIONS if sec[s.key] >= VIEW]
        out += [CAP_BY_KEY[c].name for c in clean_caps(self.access_caps)]
        return out

    @property
    def is_admin_access(self):
        """Есть что открыть в админ-панели."""
        return self.access.admin_entry

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

    # Права доступа (см. apps/accounts/access.py): {раздел: 0|1|2} и список возможностей
    access_sections = models.JSONField(default=dict, blank=True, verbose_name="Доступ к разделам")
    access_caps = models.JSONField(default=list, blank=True, verbose_name="Дополнительные возможности")

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


# ── Личный кабинет: настройки, журнал входов, сессии ─────────────────────────

class UserPreferences(models.Model):
    """Личные настройки пользователя (уведомления, интерфейс, избранное)."""
    SEV_WARN = "warn"
    SEV_CRIT = "crit"
    SEVERITY_CHOICES = [(SEV_WARN, "Warning и Critical"), (SEV_CRIT, "Только Critical")]

    START_CHOICES = [
        ("", "По умолчанию (админам — админ-панель, остальным — главная)"),
        ("/", "Главная"),
        ("/local/", "Локальная сеть — обзор"),
        ("/local/stations/", "Диспетчерская (карта станций)"),
        ("/alerts/", "Алерты"),
        ("/local/subnets/", "Подсети"),
        ("/edocs/", "Документы (СЭД)"),
    ]
    THEME_CHOICES = [("glass", "Стекло (тёмная)"), ("classic", "Старый вид")]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="prefs",
    )

    # Уведомления в Telegram
    notify_min_severity = models.CharField(max_length=8, choices=SEVERITY_CHOICES, default=SEV_WARN)
    notify_resolved = models.BooleanField(default=True, help_text="Сообщать о восстановлении")
    notify_broadcast = models.BooleanField(default=True, help_text="Broadcast storm в подсетях")
    notify_stations = models.ManyToManyField(
        "stations.Station", blank=True, related_name="+",
        help_text="Только по этим станциям. Пусто — по всем.",
    )
    quiet_enabled = models.BooleanField(default=False)
    quiet_from = models.TimeField(default="23:00")
    quiet_to = models.TimeField(default="07:00")
    quiet_allow_crit = models.BooleanField(default=True, help_text="Critical приходят и в тихие часы")

    # Интерфейс
    ui_theme = models.CharField(max_length=16, choices=THEME_CHOICES, blank=True, default="")
    start_page = models.CharField(max_length=64, choices=START_CHOICES, blank=True, default="")

    # Избранное
    favorite_stations = models.ManyToManyField("stations.Station", blank=True, related_name="+")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Настройки пользователя"
        verbose_name_plural = "Настройки пользователей"

    def __str__(self):
        return f"prefs:{self.user_id}"

    @classmethod
    def for_user(cls, user):
        obj, _ = cls.objects.get_or_create(user=user)
        return obj

    def in_quiet_hours(self, now=None) -> bool:
        if not self.quiet_enabled:
            return False
        t = (now or timezone.localtime()).time()
        a, b = self.quiet_from, self.quiet_to
        return (a <= t < b) if a <= b else (t >= a or t < b)


class LoginEvent(models.Model):
    """Журнал входов (успешных и неудачных)."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.CASCADE, related_name="login_events",
    )
    username = models.CharField(max_length=150, blank=True)
    ip = models.CharField(max_length=64, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    success = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["user", "-created_at"])]
        verbose_name = "Вход"
        verbose_name_plural = "Журнал входов"


class UserSession(models.Model):
    """Сессия пользователя: кто, откуда, когда последний раз был активен."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ui_sessions",
    )
    session_key = models.CharField(max_length=64, unique=True)
    ip = models.CharField(max_length=64, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-last_seen",)
        verbose_name = "Сессия"
        verbose_name_plural = "Сессии"


class AccessPreset(models.Model):
    """Шаблон доступа: быстро заполняет права пользователя. Пользователь с шаблоном не связан."""
    name = models.CharField(max_length=100, unique=True, verbose_name="Название")
    description = models.CharField(max_length=255, blank=True, verbose_name="Описание")
    sections = models.JSONField(default=dict, blank=True)
    caps = models.JSONField(default=list, blank=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "name")
        verbose_name = "Шаблон доступа"
        verbose_name_plural = "Шаблоны доступа"

    def __str__(self):
        return self.name


class AccessChangeLog(models.Model):
    """Журнал изменений прав: кто, кому, когда и что поменял."""
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                              related_name="+", verbose_name="Кто")
    target = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name="access_log", verbose_name="Кому")
    action = models.CharField(max_length=32)  # approve / update / create
    changes = models.JSONField(default=list)  # ["Подсети: Нет → Просмотр", ...]
    ip = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "Изменение прав"
        verbose_name_plural = "Журнал изменений прав"
