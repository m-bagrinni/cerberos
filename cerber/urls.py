from apps.accounts.views import profile_view, telegram_link_code, telegram_unlink, telegram_webapp_login
"""Главный URL-конфиг проекта."""
from django.contrib import admin
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from apps.stations.admin import draw_path_view, save_path_view
from django.urls import include, path
from django.conf import settings

from apps.core import views as core_views
from apps.alerts import views as alerts_views
from apps.devices.views import actions as device_actions
from apps.devices.views import modals as device_modals
from apps.devices.views import multiplexers as mux_views
from apps.devices.views import pages as device_pages
from apps.monitoring import views as monitoring_views
from apps.stations import views as stations_views
from apps.subnets import views as subnets_views
from apps.core import proxy_views

urlpatterns = [
    path("accounts/profile/", profile_view, name="profile"),
    path("accounts/profile/telegram-code/", telegram_link_code, name="telegram_link_code"),
    path("accounts/profile/telegram-unlink/", telegram_unlink, name="telegram_unlink"),
    path("accounts/telegram-webapp-login/", telegram_webapp_login, name="telegram_webapp_login"),
    path('admin/stations/opticallink/<int:link_id>/draw/', draw_path_view, name='draw_path'),
    path('admin/stations/opticallink/<int:link_id>/save-path/', save_path_view, name='save_path'),

    # ============================================================
    # Auth
    # ============================================================
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls", namespace="accounts")),

    # ============================================================
    # HOME — плитки модулей
    # ============================================================
    path("", login_required(core_views.home), name="home"),
    path("ui-theme/<str:name>/", core_views.set_ui_theme, name="set_ui_theme"),

    # ============================================================
    # Модуль «Локальная сеть» (/local/)
    # ============================================================
    path("local/", login_required(device_pages.dashboard), name="dashboard"),
    path("local/tree-status/", login_required(device_pages.dashboard_status), name="dashboard_status"),
    path("local/classic/", login_required(device_pages.dashboard_classic), name="dashboard_classic"),
    path("local/metrics/", login_required(monitoring_views.tab_metrics), name="tab_metrics"),
    path("local/sfp/", login_required(monitoring_views.tab_sfp), name="tab_sfp"),
    path("local/multiplexers/", login_required(mux_views.multiplexers_list), name="multiplexers_list"),
    path("local/stations/", login_required(stations_views.stations_list), name="stations_list"),
    path("local/subnets/", login_required(subnets_views.subnets_list), name="subnets_list"),
    path("local/subnets/<int:subnet_id>/", login_required(subnets_views.subnet_detail), name="subnet_detail"),

    # Devices (страницы)
    path("local/devices/", login_required(device_pages.devices_list), name="devices_list"),
    path("local/devices/move-category/", login_required(device_actions.move_device_to_category), name="move_device_to_category"),
    path("local/devices/move-subcategory/", login_required(device_actions.move_subcategory), name="move_device_to_subcategory"),
    path("local/subcategories/move-category/", login_required(device_actions.move_subcategory_to_category), name="move_subcategory_to_category"),
    path("local/devices/reorder/", login_required(device_actions.reorder_devices), name="reorder_devices"),
    path("local/devices/reorder-all/", login_required(device_actions.reorder_all), name="reorder_all"),
    path("api/v1/devices/statuses/", device_actions.devices_statuses, name="devices_statuses"),
    path("local/subcategories/reorder/", login_required(device_actions.reorder_subcategories), name="reorder_subcategories"),
    path("api/v1/devices/poll-all-now/", login_required(device_actions.poll_all_devices_now), name="poll_all_devices_now"),
    path("local/devices/<str:ip>/", login_required(device_pages.device_detail), name="device_detail"),
    path("local/devices/<str:ip>/metrics/", login_required(device_pages.device_metrics), name="device_metrics"),
    path("local/devices/<str:ip>/tab/<str:tab>/", login_required(device_pages.device_tab_partial), name="device_tab_partial"),
    path("local/devices/<str:ip>/interfaces/", login_required(device_pages.device_interfaces), name="device_interfaces"),
    path("local/devices/<str:ip>/vlans/", login_required(device_pages.device_vlans), name="device_vlans"),
    path("local/devices/<str:ip>/sfp/", login_required(device_pages.device_sfp), name="device_sfp"),

    # Alerts (глобально)
    path("alerts/", login_required(alerts_views.alerts_list), name="alerts_list"),
    path("alerts/clear/", login_required(alerts_views.clear_alerts), name="clear_alerts"),

    # ============================================================
    # Модуль «E-Docs» (/edocs/)
    # ============================================================
    path("edocs/", include("apps.edocs.urls")),
    path("analytics/", include("apps.analytics.urls")),

    # ============================================================
    # Админ-панель (глобально — все модули)
    # ============================================================
    path("admin-ui/", include("apps.adminui.urls", namespace="adminui")),

    # ============================================================
    # Платформенные сервисы
    # ============================================================
    path("", include("apps.services.urls")),

    # ============================================================
    # REST API (глобально)
    # ============================================================
    path("api/v1/", include("apps.devices.api_urls")),
    path("api/v1/", include("apps.stations.api_urls")),
    path("api/v1/", include("apps.monitoring.api_urls")),
    path("api/v1/", include("apps.subnets.api_urls")),
    path("api/v1/", include("apps.services.api_urls")),
    path("api/v1/", include("apps.alerts.api_urls")),
    path("api/v1/ne/<int:ne_id>/rename/", login_required(mux_views.ne_rename_api), name="ne_rename_api"),

    # ============================================================
    # Прокси тайлов (OpenRailwayMap)
    # ============================================================
    path(
        "tiles/orm/<str:style>/<int:z>/<int:x>/<int:y>.png",
        proxy_views.orm_tile, name="orm_tile",
    ),

    # ============================================================
    # Модалки / HTMX (глобально)
    # ============================================================
    path("modal/ne/<int:ne_id>/", login_required(mux_views.ne_modal), name="ne_modal"),
    path("modal/<str:ip>/overview/", login_required(device_modals.device_modal_overview), name="device_modal_overview"),
    path("modal/<str:ip>/interfaces/", login_required(device_modals.device_modal_interfaces), name="device_modal_interfaces"),
    path("modal/<str:ip>/charts/", login_required(device_modals.device_modal_charts), name="device_modal_charts"),
    path("modal/<str:ip>/ddm/", login_required(device_modals.device_modal_ddm), name="device_modal_ddm"),
    path("panel/<str:ip>/", login_required(device_pages.device_panel), name="device_panel"),

    # ============================================================
    # Суммарный трафик устройства
    # ============================================================
    path(
        "api/v1/devices/<str:ip>/traffic-summary/",
        __import__("apps.monitoring.api", fromlist=["device_traffic_summary"
]).device_traffic_summary,
        name="device-traffic-summary",
    ),

    # ============================================================
    # Legacy redirects (старые URL → /local/...)
    # ============================================================
    path("classic/", login_required(core_views.legacy_classic), name="legacy_classic"),
    path("metrics/", login_required(core_views.legacy_metrics), name="legacy_metrics"),
    path("sfp/", login_required(core_views.legacy_sfp), name="legacy_sfp"),
    path("multiplexers/", login_required(core_views.legacy_multiplexers), name="legacy_multiplexers"),
    path("stations/", login_required(core_views.legacy_stations), name="legacy_stations"),
    path("subnets/", login_required(core_views.legacy_subnets), name="legacy_subnets"),
    path("devices/", login_required(core_views.legacy_devices), name="legacy_devices"),
    path("discoveries/", login_required(core_views.legacy_discoveries), name="legacy_discoveries"),
    path("discoveries/<int:pk>/", login_required(device_pages.discovery_detail), name="discovery_detail"),

    path("servers/", include("apps.servers.urls")),
]

# Медиа-файлы (аватарки, вложения E-Docs) — только вошедшим и независимо от DEBUG.
# Раньше раздавались через static() лишь при DEBUG=True и БЕЗ проверки входа —
# через Cloudflare-туннель документы были доступны любому, кто знает путь.
from django.urls import re_path  # noqa: E402
from django.views.static import serve as _media_serve  # noqa: E402

urlpatterns += [
    re_path(r"^media/(?P<path>.*)$", login_required(_media_serve), {"document_root": settings.MEDIA_ROOT}),
]
