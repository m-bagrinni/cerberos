from django.contrib import admin

from .models import OpticalLink, Station, StationDevice, StationNe


class StationDeviceInline(admin.TabularInline):
    model = StationDevice
    extra = 0
    autocomplete_fields = ("device",)


class StationNeInline(admin.TabularInline):
    model = StationNe
    extra = 0
    autocomplete_fields = ("ne",)


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "sort_order", "x", "y", "devices_count", "nes_count")
    search_fields = ("name", "slug", "description")
    list_editable = ("sort_order",)
    inlines = (StationDeviceInline, StationNeInline)

    @admin.display(description="Устройств")
    def devices_count(self, obj):
        return obj.devices.count()

    @admin.display(description="NE")
    def nes_count(self, obj):
        return obj.nes.count()


@admin.register(OpticalLink)
class OpticalLinkAdmin(admin.ModelAdmin):
    list_display = (
        "label", "end_a_label", "end_b_label",
        "link_status", "rx_a_dbm", "rx_b_dbm", "is_active", "updated_at",
    )
    list_filter = ("link_status", "is_active", "port_a_type", "port_b_type")
    search_fields = ("label", "description")
    autocomplete_fields = ("ne_a", "ne_b", "device_a", "device_b")
    readonly_fields = (
        "rx_a_dbm", "rx_b_dbm", "link_a_up", "link_b_up",
        "link_status", "updated_at", "created_at",
    )
    fieldsets = (
        ("Метаданные", {
            "fields": ("label", "description", "is_active"),
        }),
        ("Конец A", {
            "fields": ("station_a", "ne_a", "device_a", "port_a_type", "port_a_num"),
        }),
        ("Конец B", {
            "fields": ("station_b", "ne_b", "device_b", "port_b_type", "port_b_num"),
        }),
        ("Кэш DDM (read-only)", {
            "fields": (
                "rx_a_dbm", "rx_b_dbm", "link_a_up", "link_b_up",
                "link_status", "updated_at", "created_at",
            ),
        }),
    )

    def save_model(self, request, obj, form, change):
        obj.full_clean()
        super().save_model(request, obj, form, change)


@admin.register(StationDevice)
class StationDeviceAdmin(admin.ModelAdmin):
    list_display = ("station", "device", "role")
    list_filter = ("station",)
    autocomplete_fields = ("device",)


@admin.register(StationNe)
class StationNeAdmin(admin.ModelAdmin):
    list_display = ("station", "ne", "role")
    list_filter = ("station",)
    autocomplete_fields = ("ne",)
