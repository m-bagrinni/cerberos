"""Модели устройств, вендоров, профилей и метрик."""
from django.contrib.auth.models import AbstractUser
from django.db import models

# ============================================================
# Пользователь
# ============================================================

class Vendor(models.Model):
    """
    Производитель сетевого оборудования.

    enterprise_ids — список IANA enterprise-номеров (SNMP prefix
    после 1.3.6.1.4.1.), по которым опознаём вендора в sysObjectID.
        MikroTik = 14988
        D-Link   = 171
        Eltex    = 35265
    """
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=64)
    enterprise_ids = models.JSONField(
        default=list, blank=True,
        help_text='Например: ["171"] для D-Link, ["14988"] для MikroTik',
    )

    class Meta:
        verbose_name = "Производитель"
        verbose_name_plural = "Производители"
        ordering = ("name",)

    def __str__(self):
        return self.name

# ============================================================
# Профили устройств
# ============================================================

class DeviceProfile(models.Model):
    """
    Профиль устройства — набор метрик с правилами сопоставления.

    Иерархия через parent:
        VendorGeneric (parent=None)
          └── Family  (напр. "DGS-1210")
                └── Model (напр. "DGS-1210-28 rev.B")

    Метрики наследуются по цепочке: у ребёнка свои + у родителя.
    """
    vendor = models.ForeignKey(
        Vendor, on_delete=models.CASCADE, related_name="profiles",
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="children",
        help_text="Родительский профиль (для наследования метрик)",
    )
    slug = models.SlugField()
    name = models.CharField(max_length=128)

    match_sysobject_prefixes = models.JSONField(
        default=list, blank=True,
        help_text='Список префиксов sysObjectID: ["1.3.6.1.4.1.171.10.75."]',
    )
    match_sysdescr_regex = models.CharField(
        max_length=255, blank=True,
        help_text="Regex по sysDescr (регистронезависимо)",
    )
    match_model_regex = models.CharField(
        max_length=255, blank=True,
        help_text="Regex по entPhysicalModelName (регистронезависимо)",
    )
    priority = models.IntegerField(
        default=0,
        help_text="Больше = специфичнее, выбирается первым при матчинге",
    )

    # Категория устройства (для разделения UI: коммутаторы, мультиплексоры, ...)
    CATEGORY_SWITCH = "switch"
    CATEGORY_MULTIPLEXER = "multiplexer"
    CATEGORY_ROUTER = "router"
    CATEGORY_OTHER = "other"
    CATEGORY_CHOICES = [
        (CATEGORY_SWITCH, "Коммутатор"),
        (CATEGORY_MULTIPLEXER, "Мультиплексор"),
        (CATEGORY_ROUTER, "Маршрутизатор"),
        (CATEGORY_OTHER, "Другое"),
    ]
    device_category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default=CATEGORY_SWITCH,
        db_index=True,
        verbose_name="Категория устройства",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("vendor", "slug")
        ordering = ("-priority", "name")
        verbose_name = "Профиль устройства"
        verbose_name_plural = "Профили устройств"

    def __str__(self):
        return f"{self.vendor.name} / {self.name}"

    def get_effective_metrics(self):
        """Метрики с учётом наследования (специфичные перекрывают общие)."""
        chain = []
        p = self
        seen = set()
        while p and p.pk not in seen:
            seen.add(p.pk)
            chain.append(p)
            p = p.parent

        metrics = {}
        for profile in reversed(chain):
            for m in profile.metrics.all():
                metrics[m.key] = m
        return metrics

# ============================================================
# Метрики
# ============================================================

class MetricDefinition(models.Model):
    """Описание метрики — ДАННЫЕ, не код."""
    VALUE_TYPES = [
        ("gauge", "Gauge"),
        ("counter", "Counter"),
        ("string", "String"),
        ("enum", "Enum"),
    ]
    SCALES = [
        ("single", "Single OID (scalar)"),
        ("walk", "Walk (таблица)"),
    ]

    profile = models.ForeignKey(
        DeviceProfile, on_delete=models.CASCADE, related_name="metrics",
    )
    key = models.CharField(
        max_length=64,
        help_text="Уникальный ключ внутри профиля, напр. cpu_load_5min",
    )
    name = models.CharField(max_length=128)
    oid = models.CharField(max_length=255)
    value_type = models.CharField(
        max_length=16, choices=VALUE_TYPES, default="gauge",
    )
    scale = models.CharField(
        max_length=16, choices=SCALES, default="single",
    )
    unit = models.CharField(max_length=16, blank=True)

    transform = models.CharField(
        max_length=64, default="identity",
        help_text="Имя функции из apps.monitoring.transforms.TRANSFORMS",
    )
    transform_params = models.JSONField(default=dict, blank=True)

    warn = models.FloatField(null=True, blank=True)
    crit = models.FloatField(null=True, blank=True)

    enabled = models.BooleanField(default=True)

    class Meta:
        unique_together = ("profile", "key")
        verbose_name = "Метрика"
        verbose_name_plural = "Метрики"
        ordering = ("profile", "key")

    def __str__(self):
        return f"{self.profile.slug}.{self.key}"

# ============================================================
# Устройство
# ============================================================

class DeviceCategory(models.Model):
    """
    Категория устройств (core, access_line, access_branch, ...).
    """
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=128)
    icon = models.CharField(max_length=64, blank=True, default="bi-hdd-network")
    sort_order = models.IntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Категория устройств"
        verbose_name_plural = "Категории устройств"
        ordering = ("sort_order", "name")

    def __str__(self):
        return self.name


class DeviceSubcategory(models.Model):
    """
    Подкатегория устройств (например, филиал внутри категории «Доступ — филиалы»).
    """
    category = models.ForeignKey(
        DeviceCategory,
        on_delete=models.CASCADE,
        related_name="subcategories",
        verbose_name="Категория",
    )
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=128)
    sort_order = models.IntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Подкатегория устройств"
        verbose_name_plural = "Подкатегории устройств"
        ordering = ("sort_order", "name")

    def __str__(self):
        return f"{self.category.name} / {self.name}"


class Device(models.Model):
    """Сетевое устройство."""
    STATUS_UNKNOWN = "unknown"
    STATUS_OK = "ok"
    STATUS_UNREACHABLE = "unreachable"
    STATUS_CHOICES = [
        (STATUS_UNKNOWN, "Неизвестно"),
        (STATUS_OK, "Доступно"),
        (STATUS_UNREACHABLE, "Недоступно"),
    ]

    name = models.CharField(max_length=128)
    ip = models.GenericIPAddressField(unique=True)
    sort_order = models.IntegerField(default=0, verbose_name="Порядок сортировки")
    category = models.ForeignKey(
        DeviceCategory,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="devices",
        verbose_name="Категория",
    )
    subcategory = models.ForeignKey(
        DeviceSubcategory,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="devices",
        verbose_name="Подкатегория",
    )
    description = models.TextField(blank=True)
    # Зависимость: через какое устройство это доступно (коммутатор/маршрутизатор выше по сети).
    # Если вышестоящее недоступно, алерты этого устройства подавляются (apps/alerts/correlation.py).
    uplink = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="downlinks",
        verbose_name="Вышестоящее устройство",
        help_text="Через какое устройство это доступно. Если оно упадёт — алерты этого устройства "
                  "не будут слать уведомлений, а свернутся под алертом вышестоящего.",
    )
    # …или NE мультиплексора, через канал которого подключено устройство (LAN-порт Olencom).
    # Задаётся вместо uplink: если указаны оба, главнее uplink.
    uplink_ne = models.ForeignKey(
        "devices.NeDevice", null=True, blank=True, on_delete=models.SET_NULL, related_name="dependent_devices",
        verbose_name="Вышестоящий NE",
        help_text="NE мультиплексора, через который подключено устройство. Обрыв оптики до этого NE "
                  "свернёт и алерты устройства.",
    )

    snmp_version = models.CharField(
        max_length=8, default="2c",
        choices=[("1", "v1"), ("2c", "v2c"), ("3", "v3")],
    )
    snmp_community = models.CharField(
        max_length=128, blank=True,
        help_text="Read-only community для опроса (v1/v2c).",
    )
    snmp_write_community = models.CharField(
        max_length=128, blank=True,
        help_text=(
            "Community для SNMP SET (write). "
            "Если пусто — write-операции отключены."
        ),
    )
    snmp_port = models.IntegerField(default=161)

    vendor = models.ForeignKey(
        Vendor, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="devices",
    )
    profile = models.ForeignKey(
        DeviceProfile, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="devices",
        help_text="Профиль, определённый автоматически",
    )
    profile_override = models.ForeignKey(
        DeviceProfile, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
        help_text="Ручное переопределение профиля",
    )

    sys_object_id = models.CharField(max_length=255, blank=True)
    sys_descr = models.TextField(blank=True)
    ent_model = models.CharField(max_length=255, blank=True)

    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_UNKNOWN,
        db_index=True,
    )
    last_seen = models.DateTimeField(null=True, blank=True)
    last_polled_at = models.DateTimeField(null=True, blank=True)
    ne_id = models.IntegerField(
        null=True, blank=True, db_index=True,
        verbose_name="Номер NE",
        help_text="Заполнено, если это виртуальный Device для NE мультиплексора",
    )
    ne_gateway_ip = models.GenericIPAddressField(
        null=True, blank=True,
        verbose_name="IP шлюза",
        help_text="IP шлюза, через который опрашивается этот NE",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    ping_fail_count = models.IntegerField(default=0, help_text="Счетчик неудачных пингов подряд")
    ping_ok_count = models.IntegerField(default=0, help_text="Счетчик успешных пингов подряд")
    last_ping_at = models.DateTimeField(null=True, blank=True, help_text="Время последнего пинга")
    is_reachable = models.BooleanField(default=True, help_text="Доступно ли устройство (по ping)")

    class Meta:
        verbose_name = "Устройство"
        verbose_name_plural = "Устройства"
        ordering = ("name",)
        indexes = [
            models.Index(fields=["status", "last_seen"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.ip})"

    @property
    def effective_profile(self):
        return self.profile_override or self.profile

    @property
    def status_emoji(self):
        return {
            self.STATUS_OK: "🟢",
            self.STATUS_UNREACHABLE: "🔴",
            self.STATUS_UNKNOWN: "⚪",
        }.get(self.status, "⚪")

# ============================================================
# Discovery-снимки
# ============================================================

class DeviceDiscovery(models.Model):
    """Снимок SNMP-обхода — для ручного заведения профиля."""
    device = models.ForeignKey(
        Device, on_delete=models.CASCADE, related_name="discoveries",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=255, blank=True)
    snapshot = models.JSONField(default=list)

    class Meta:
        verbose_name = "Discovery-снимок"
        verbose_name_plural = "Discovery-снимки"
        ordering = ("-created_at",)

    def __str__(self):
        return f"Discovery {self.device} @ {self.created_at:%Y-%m-%d %H:%M}"

# ============================================================
# Аудит действий
# ============================================================

class ActionLog(models.Model):
    """Лог операций управления устройствами (audit trail)."""
    ACTION_PORT_UP = "port_up"
    ACTION_PORT_DOWN = "port_down"
    ACTION_REBOOT = "reboot"
    ACTION_CHOICES = [
        (ACTION_PORT_UP, "Включить порт"),
        (ACTION_PORT_DOWN, "Выключить порт"),
        (ACTION_REBOOT, "Перезагрузить"),
    ]

    STATUS_PENDING = "pending"
    STATUS_SUCCESS = "success"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Выполняется"),
        (STATUS_SUCCESS, "Успешно"),
        (STATUS_ERROR, "Ошибка"),
    ]

    device = models.ForeignKey(
        Device, on_delete=models.CASCADE, related_name="actions",
    )
    user = models.ForeignKey(
        "accounts.User", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="actions",
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    target = models.CharField(
        max_length=128, blank=True,
        help_text="Что меняли: имя порта, ifIndex, и т.д.",
    )
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING,
    )
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Действие"
        verbose_name_plural = "Действия"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["device", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.get_action_display()} {self.target} на {self.device.name} [{self.status}]"

# ============================================================
# NeDevice — удалённые NE (мультиплексоры за шлюзом)
# ============================================================

class NeDevice(models.Model):
    """
    Подчинённое NE-устройство (Network Element) на Olencom-мультиплексоре.

    Один шлюз (Device) → много NE. Опрос NE идёт через SNMP шлюза.
    Каждое NE — это отдельная сущность со своими SFP/Ethernet портами,
    метриками и статусом (как обычное Device).
    """

    STATUS_UNKNOWN = "unknown"
    STATUS_OK = "ok"
    STATUS_UNREACHABLE = "unreachable"
    STATUS_CHOICES = [
        (STATUS_UNKNOWN, "Неизвестно"),
        (STATUS_OK, "Онлайн"),
        (STATUS_UNREACHABLE, "Недоступно"),
    ]

    gateway = models.ForeignKey(
        Device, on_delete=models.CASCADE,
        related_name="ne_devices",
        verbose_name="Шлюз",
    )
    ne_id = models.IntegerField(
        db_index=True,
        verbose_name="Номер NE",
        help_text="1 = сам шлюз, 2+ = подчинённые NE",
    )
    sort_order = models.IntegerField(
        default=0, db_index=True,
        verbose_name="Порядок сортировки",
        help_text="Пользовательский порядок отображения (меньше = выше)",
    )
    name = models.CharField(
        max_length=128, blank=True,
        verbose_name="Имя",
    )
    raw_name = models.CharField(
        max_length=128, blank=True,
        verbose_name="Имя (raw hex/ascii)",
        help_text="Сырое имя из SNMP, если name не установлено вручную",
    )
    ne_ip = models.GenericIPAddressField(
        null=True, blank=True,
        verbose_name="IP адрес NE",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_UNKNOWN,
        db_index=True, verbose_name="Статус",
    )
    last_seen = models.DateTimeField(null=True, blank=True)
    last_polled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("gateway", "ne_id")
        ordering = ("sort_order", "ne_id")
        verbose_name = "NE-устройство"
        verbose_name_plural = "NE-устройства"
        indexes = [
            models.Index(fields=["gateway", "ne_id"]),
            models.Index(fields=["status", "last_seen"]),
        ]

    def __str__(self):
        label = self.name or f"NE-{self.ne_id}"
        return f"{self.gateway.name} / {label}"

    @property
    def status_emoji(self):
        return {"ok": "🟢", "unreachable": "🔴"}.get(self.status, "⚪")

    @property
    def display_name(self):
        return self.name or f"NE-{self.ne_id}"
