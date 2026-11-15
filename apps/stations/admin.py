from django.utils.html import format_html
import json
from django.shortcuts import get_object_or_404, render
from django.http import JsonResponse
from django.urls import path
from django.contrib import admin

from .models import OpticalClosure, OpticalLink, Station, StationDevice, StationNe


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
    list_display = (
"name", "slug", "sort_order", "x", "y", "devices_count", "nes_count")
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
        "draw_button",
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

    def draw_button(self, obj):
        if obj.path_geojson:
            return format_html('<a class="button" href="/admin/stations/opticallink/{}/draw/">✏️ Редактировать</a>', obj.id)
        return format_html('<a class="button" href="/admin/stations/opticallink/{}/draw/">🎨 Нарисовать</a>', obj.id)
    draw_button.short_description = 'Маршрут'
    draw_button.allow_tags = True

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
    list_display = (
"station", "ne", "role")
    list_filter = ("station",)
    autocomplete_fields = ("ne",)


@admin.register(OpticalClosure)
class OpticalClosureAdmin(admin.ModelAdmin):
    list_display = (
"name", "link", "lat", "lon")
    list_filter = ("link",)
    search_fields = ("name", "description")


# ── Рисование маршрута ──────────────────────────────────────
from django.contrib.admin.views.decorators import staff_member_required

@staff_member_required
def draw_path_view(request, link_id):
    link = get_object_or_404(OpticalLink, id=link_id)
    
    # Собираем координаты станций (если есть)
    points = []
    if link.station_a and link.station_a.latitude and link.station_a.longitude:
        points.append({
            'lat': float(link.station_a.latitude),
            'lon': float(link.station_a.longitude),
            'name': link.station_a.name,
            'type': 'station_a'
        })
    if link.station_b and link.station_b.latitude and link.station_b.longitude:
        points.append({
            'lat': float(link.station_b.latitude),
            'lon': float(link.station_b.longitude),
            'name': link.station_b.name,
            'type': 'station_b'
        })
    
    return render(request, 'admin/stations/opticallink/draw_path.html', {
        'link': link,
        'stations': points
    })

@staff_member_required
def save_path_view(request, link_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)
    
    try:
        data = json.loads(request.body)
        path_geojson = data.get('path_geojson', [])
        
        link = get_object_or_404(OpticalLink, id=link_id)
        link.path_geojson = path_geojson
        link.save(update_fields=['path_geojson'])
        
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
