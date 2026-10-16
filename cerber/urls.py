"""Главный URL-конфиг проекта."""
from django.contrib import admin
from django.urls import include, path

from apps.core import views as core_views

urlpatterns = [
    path("admin/", admin.site.urls),

    # REST API
    path("api/v1/", include("apps.devices.api_urls")),

    path("api/v1/", include("apps.stations.api_urls")),
    path("api/v1/", include("apps.monitoring.api_urls")),

    # Глобальные табы (верхний уровень)
    path("", core_views.dashboard, name="dashboard"),
    path("classic/", core_views.dashboard_classic, name="dashboard_classic"),
    path("metrics/", core_views.tab_metrics, name="tab_metrics"),
    path("sfp/", core_views.tab_sfp, name="tab_sfp"),
    path("multiplexers/", core_views.multiplexers_list, name="multiplexers_list"),
    path("stations/", core_views.stations_list, name="stations_list"),
    path("modal/ne/<int:ne_id>/", core_views.ne_modal, name="ne_modal"),
    path("api/v1/ne/<int:ne_id>/rename/", core_views.ne_rename_api, name="ne_rename_api"),

    # Партиал панели устройства (для HTMX/fetch)
    path("panel/<str:ip>/", core_views.device_panel, name="device_panel"),

    # Вкладки модального окна устройства
    path("modal/<str:ip>/overview/", core_views.device_modal_overview, name="device_modal_overview"),
    path("modal/<str:ip>/interfaces/", core_views.device_modal_interfaces, name="device_modal_interfaces"),
    path("modal/<str:ip>/charts/", core_views.device_modal_charts, name="device_modal_charts"),
    path("modal/<str:ip>/ddm/", core_views.device_modal_ddm, name="device_modal_ddm"),

    # Страницы устройства (старые, оставляем)
    path("devices/", core_views.devices_list, name="devices_list"),
    path("devices/<str:ip>/", core_views.device_detail, name="device_detail"),
    path("devices/<str:ip>/metrics/", core_views.device_metrics, name="device_metrics"),
    path("devices/<str:ip>/interfaces/", core_views.device_interfaces, name="device_interfaces"),
    path("devices/<str:ip>/vlans/", core_views.device_vlans, name="device_vlans"),
    path("devices/<str:ip>/sfp/", core_views.device_sfp, name="device_sfp"),

    # Суммарный трафик устройства (для Tab 2 «Метрики»)
    path(
        "api/v1/devices/<str:ip>/traffic-summary/",
        __import__("apps.monitoring.api", fromlist=["device_traffic_summary"]).device_traffic_summary,
        name="device-traffic-summary",
    ),

    # Алерты
    path("alerts/", core_views.alerts_list, name="alerts_list"),

    # Discovery
    path("discoveries/", core_views.discoveries_list, name="discoveries_list"),
    path("discoveries/<int:pk>/", core_views.discovery_detail, name="discovery_detail"),
]
