"""Админка для моделей устройств."""
import json

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from apps.accounts.models import User
from django.utils.html import format_html

from apps.monitoring.models import Interface

from .models import (
    Device,
    DeviceDiscovery,
    DeviceProfile,
    MetricDefinition,
    Vendor,
)


# ============================================================
# Пользователь
# ============================================================

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Telegram", {"fields": ("telegram_id",)}),
    )
    readonly_fields = ("telegram_id",)  # заполняется только привязкой бота
    list_display = ("username", "email", "telegram_id", "is_staff", "is_active")
    search_fields = ("username", "email", "telegram_id")


# ============================================================
# Вендор
# ============================================================

@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "enterprise_ids", "profiles_count")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}

    @admin.display(description="Профилей")
    def profiles_count(self, obj):
        return obj.profiles.count()


# ============================================================
# Профиль + метрики
# ============================================================

class MetricDefinitionInline(admin.TabularInline):
    model = MetricDefinition
    extra = 0
    fields = (
        "enabled", "key", "name", "oid", "value_type", "scale",
        "unit", "transform", "warn", "crit",
    )
    ordering = ("key",)
    show_change_link = True


@admin.register(DeviceProfile)
class DeviceProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "vendor", "parent", "priority", "metrics_count")
    list_filter = ("vendor", "priority")
    search_fields = ("name", "slug")
    autocomplete_fields = ("parent",)
    inlines = [MetricDefinitionInline]

    fieldsets = (
        (None, {
            "fields": ("vendor", "parent", "slug", "name", "priority"),
        }),
        ("Правила сопоставления", {
            "fields": (
                "match_sysobject_prefixes",
                "match_sysdescr_regex",
                "match_model_regex",
            ),
            "description": (
                "Используются при автоопределении профиля. "
                "Профили с большим priority проверяются первыми."
            ),
        }),
        ("Служебное", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Метрик")
    def metrics_count(self, obj):
        return obj.metrics.count()


@admin.register(MetricDefinition)
class MetricDefinitionAdmin(admin.ModelAdmin):
    list_display = (
        "key", "name", "profile", "oid", "value_type", "scale",
        "unit", "warn", "crit", "enabled",
    )
    list_filter = ("profile__vendor", "value_type", "scale", "enabled")
    search_fields = ("key", "name", "oid", "profile__name", "profile__slug")
    autocomplete_fields = ("profile",)


# ============================================================
# Устройство + inline интерфейсов
# ============================================================

class InterfaceInline(admin.TabularInline):
    """Показывает интерфейсы устройства прямо на странице Device."""
    model = Interface
    extra = 0
    can_delete = False
    show_change_link = True
    fields = (
        "if_index", "name", "alias", "if_type",
        "speed_mbps", "oper_status", "admin_status", "pvid",
    )
    readonly_fields = fields
    ordering = ("if_index",)
    max_num = 0
    verbose_name = "Интерфейс"
    verbose_name_plural = "Интерфейсы"


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = (
        "name", "ip", "vendor", "effective_profile_display",
        "status", "last_seen",
    )
    list_filter = ("status", "vendor", "profile")
    search_fields = ("name", "ip", "sys_descr", "ent_model")
    autocomplete_fields = ("vendor", "profile", "profile_override")
    inlines = [InterfaceInline]

    fieldsets = (
        (None, {
            "fields": ("name", "ip", "description"),
        }),
        ("SNMP", {
            "fields": ("snmp_version", "snmp_community", "snmp_port"),
        }),
        ("Профиль", {
            "fields": ("vendor", "profile", "profile_override"),
            "description": (
                "profile — определяется автоматически. "
                "profile_override — ручное переопределение (приоритет)."
            ),
        }),
        ("Определено автоматически", {
            "fields": ("sys_object_id", "sys_descr", "ent_model"),
            "classes": ("collapse",),
        }),
        ("Состояние", {
            "fields": ("status", "last_seen", "created_at"),
        }),
    )
    readonly_fields = (
        "sys_object_id", "sys_descr", "ent_model",
        "status", "last_seen", "created_at",
    )

    @admin.display(description="Профиль (эффективный)")
    def effective_profile_display(self, obj):
        return obj.effective_profile or "—"


# ============================================================
# Discovery
# ============================================================

@admin.register(DeviceDiscovery)
class DeviceDiscoveryAdmin(admin.ModelAdmin):
    list_display = ("device", "created_at", "note", "rows_count")
    list_filter = ("device__vendor",)
    search_fields = ("device__name", "device__ip", "note")
    readonly_fields = ("device", "created_at", "snapshot_pretty")
    autocomplete_fields = ("device",)

    @admin.display(description="Строк")
    def rows_count(self, obj):
        return len(obj.snapshot or [])

    @admin.display(description="Снимок")
    def snapshot_pretty(self, obj):
        data = json.dumps(obj.snapshot or [], ensure_ascii=False, indent=2)
        return format_html(
            "<pre style='max-height:600px;overflow:auto'>{}</pre>", data,
        )

# ── NeDevice (Olencom NE) ──────────────────────────────────
from .models import NeDevice  # noqa: E402


@admin.register(NeDevice)
class NeDeviceAdmin(admin.ModelAdmin):
    list_display = (
        "display_name", "ne_id", "gateway", "ne_ip",
        "status", "last_polled_at", "sort_order",
    )
    list_filter = ("status", "gateway")
    search_fields = ("name", "raw_name", "ne_ip", "gateway__name", "gateway__ip")
    list_editable = ("sort_order",)
    ordering = ("gateway", "sort_order", "ne_id")
    readonly_fields = ("created_at", "last_seen", "last_polled_at", "raw_name")
