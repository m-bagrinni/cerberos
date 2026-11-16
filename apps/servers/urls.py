from django.urls import path
from django.contrib.auth.decorators import login_required, user_passes_test
from . import views, api

from apps.accounts.access import section_required

# AD: чтение — «Серверы и AD» на просмотр, изменения — на изменение (по методу запроса)
staff_required = section_required("servers")


urlpatterns = [
    # ===== Агент (без login_required — свой X-Agent-Token, см. правку #9) =====
    path("api/agent/report/", api.agent_report_api, name="agent_report"),

    # ===== Страницы =====
    path("", login_required(views.servers_dashboard), name="servers_dashboard"),
    path("<int:server_id>/", login_required(views.server_detail_page), name="server_detail_page"),
    path("<int:server_id>/detail/", login_required(views.server_detail_fragment), name="server_detail_fragment"),

    # ===== API AD — чтение =====
    path("api/ad-users/", login_required(staff_required(api.ad_users_api)), name="ad_users_api"),
    path("api/ad-groups/", login_required(staff_required(api.ad_groups_api)), name="ad_groups_api"),
    path("api/ad-groups/<str:group_name>/members/", login_required(staff_required(api.ad_group_members_api)), name="ad_group_members"),

    # ===== API AD — изменение (раздел «Серверы и AD» на изменение) =====
    path("api/ad-users/create/", login_required(staff_required(api.ad_user_create_api)), name="ad_user_create"),
    path("api/ad-users/<str:login>/toggle/", login_required(staff_required(api.ad_user_toggle_api)), name="ad_user_toggle"),
    path("api/ad-users/<str:login>/update/", login_required(staff_required(api.ad_user_update_api)), name="ad_user_update"),
    path("api/ad-users/<str:login>/reset-password/", login_required(staff_required(api.ad_user_reset_password_api)), name="ad_user_reset_password"),
    path("api/ad-groups/create/", login_required(staff_required(api.ad_group_create_api)), name="ad_group_create"),
    path("api/ad-groups/<str:group_name>/add-user/", login_required(staff_required(api.ad_group_add_user_api)), name="ad_group_add_user"),
    path("api/ad-groups/<str:group_name>/remove-user/", login_required(staff_required(api.ad_group_remove_user_api)), name="ad_group_remove_user"),

    # ===== API метрик / служб (чтение) =====
    path("api/server/<int:server_id>/metrics/", login_required(api.server_metrics_api), name="server_metrics"),
    path("api/server/<int:server_id>/services/", login_required(api.server_services_api), name="server_services"),
    path("api/server/<int:server_id>/user-stats/", login_required(api.server_user_stats_api), name="server_user_stats"),
]
