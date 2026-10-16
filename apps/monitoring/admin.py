"""Админка приложения monitoring."""
from django.contrib import admin

from .models import Interface, InterfaceVlan, MetricValue, Vlan


@admin.register(MetricValue)
class MetricValueAdmin(admin.ModelAdmin):
    list_display = ("ts", "device", "metric", "instance", "value")
    list_filter = ("metric__profile__vendor", "device")
    search_fields = ("device__name", "device__ip", "metric__key")
    autocomplete_fields = ("device", "metric")
    date_hierarchy = "ts"
    ordering = ("-ts",)
    list_select_related = ("device", "metric")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Interface)
class InterfaceAdmin(admin.ModelAdmin):
    list_display = (
        "name", "alias", "device", "if_index", "if_type",
        "speed_mbps", "oper_status", "admin_status", "pvid",
    )
    list_filter = ("device", "if_type", "oper_status", "admin_status")
    search_fields = ("name", "alias", "descr")
    autocomplete_fields = ("device",)
    ordering = ("device", "if_index")


class InterfaceVlanInline(admin.TabularInline):
    model = InterfaceVlan
    extra = 0
    autocomplete_fields = ("vlan",)
    fields = ("vlan", "is_tagged")


@admin.register(Vlan)
class VlanAdmin(admin.ModelAdmin):
    list_display = ("vlan_id", "name", "device", "if_index")
    list_filter = ("device",)
    search_fields = ("name",)
    autocomplete_fields = ("device",)
    ordering = ("device", "vlan_id")


@admin.register(InterfaceVlan)
class InterfaceVlanAdmin(admin.ModelAdmin):
    list_display = ("interface", "vlan", "is_tagged")
    list_filter = ("is_tagged", "vlan__device")
    autocomplete_fields = ("interface", "vlan")