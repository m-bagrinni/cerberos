from django.urls import path

from apps.accounts import views as accounts_views

from . import api, views

app_name = "adminui"

urlpatterns = [
    # Категории
    path("categories/", views.CategoryListView.as_view(), name="categories"),
    path("categories/rename-api/", views.category_rename_api, name="category_rename_api"),
    path("categories/delete-api/", views.category_delete_api, name="category_delete_api"),
    path("subcategories/create-api/", views.subcategory_create_api, name="subcategory_create_api"),
    path("categories/new/", views.CategoryCreateView.as_view(), name="category_create"),
    path("categories/<int:pk>/edit/", views.CategoryUpdateView.as_view(), name="category_edit"),
    path("categories/<int:pk>/delete/", views.CategoryDeleteView.as_view(), name="category_delete"),

    path("", views.DashboardView.as_view(), name="dashboard"),

    # ========== Devices ==========
    path("devices/", views.DeviceListView.as_view(), name="devices"),
    path("devices/new/", views.DeviceCreateView.as_view(), name="device_create"),
    path("devices/<int:pk>/", views.DeviceDetailView.as_view(), name="device_detail"),
    path("devices/<int:pk>/edit/", views.DeviceUpdateView.as_view(), name="device_update"),
    path("devices/<int:pk>/delete/", views.DeviceDeleteView.as_view(), name="device_delete"),
    path("devices/<int:pk>/poll-now/", api.poll_now, name="device_poll_now"),

    # ========== Profiles ==========
    path("profiles/", views.ProfileListView.as_view(), name="profiles"),
    path("profiles/new/", views.ProfileCreateView.as_view(), name="profile_create"),
    path("profiles/<int:pk>/", views.ProfileDetailView.as_view(), name="profile_detail"),
    path("profiles/<int:pk>/edit/", views.ProfileUpdateView.as_view(), name="profile_update"),
    path("profiles/<int:pk>/delete/", views.ProfileDeleteView.as_view(), name="profile_delete"),
    path("profiles/<int:pk>/clone/", views.ProfileCloneView.as_view(), name="profile_clone"),
    path("profiles/<int:profile_pk>/metrics/new/", views.MetricCreateView.as_view(), name="metric_create"),
    path("profiles/<int:profile_pk>/metrics/<int:pk>/edit/", views.MetricUpdateView.as_view(), name="metric_update"),
    path("profiles/<int:profile_pk>/metrics/<int:pk>/delete/", views.MetricDeleteView.as_view(), name="metric_delete"),

    # ========== Vendors ==========
    # ========== Roles ==========
    # Роли заменены шаблонами доступа (права теперь задаются каждому пользователю)
    path("roles/", views.RolesMovedView.as_view(), name="roles"),
    path("roles/new/", views.RolesMovedView.as_view(), name="role_create"),
    path("roles/<int:pk>/edit/", views.RolesMovedView.as_view(), name="role_update"),
    path("roles/<int:pk>/delete/", views.RolesMovedView.as_view(), name="role_delete"),
    path("roles/<int:pk>/", views.RolesMovedView.as_view(), name="role_detail"),
    path("presets/", views.PresetListView.as_view(), name="presets"),
    path("presets/new/", views.PresetEditView.as_view(), name="preset_create"),
    path("presets/<int:pk>/edit/", views.PresetEditView.as_view(), name="preset_update"),
    path("presets/<int:pk>/delete/", views.PresetDeleteView.as_view(), name="preset_delete"),

    # ========== Объявления ==========
    path("announcements/", views.AnnouncementListView.as_view(), name="announcements"),
    path("announcements/new/", views.AnnouncementCreateView.as_view(), name="announcement_create"),
    path("announcements/<int:pk>/edit/", views.AnnouncementUpdateView.as_view(), name="announcement_update"),
    path("announcements/<int:pk>/delete/", views.AnnouncementDeleteView.as_view(), name="announcement_delete"),

    path("vendors/", views.VendorListView.as_view(), name="vendors"),
    path("vendors/new/", views.VendorCreateView.as_view(), name="vendor_create"),
    path("vendors/<int:pk>/edit/", views.VendorUpdateView.as_view(), name="vendor_update"),
    path("vendors/<int:pk>/delete/", views.VendorDeleteView.as_view(), name="vendor_delete"),

    # ========== NE ==========
    path("ne/", views.NeListView.as_view(), name="ne"),
    path("ne/<int:pk>/rename/", api.ne_rename, name="ne_rename"),
    path("ne/<int:pk>/", views.NeDetailView.as_view(), name="ne_detail"),

    # ========== Subnets ==========
    path("subnets/", views.SubnetListView.as_view(), name="subnets"),
    path("subnets/new/", views.SubnetCreateView.as_view(), name="subnet_create"),
    path("subnets/<int:pk>/edit/", views.SubnetUpdateView.as_view(), name="subnet_update"),
    path("subnets/<int:pk>/delete/", views.SubnetDeleteView.as_view(), name="subnet_delete"),
    path("subnets/<int:pk>/scan/", api.subnet_scan_now, name="subnet_scan"),

    # Муфты
    path("closures/", views.ClosureListView.as_view(), name="closures"),

    # Станции
    path("stations/", views.StationListView.as_view(), name="stations"),
    path("stations/new/", views.StationCreateView.as_view(), name="station_create"),
    path("stations/<int:pk>/edit/", views.StationUpdateView.as_view(), name="station_update"),
    path("stations/<int:pk>/delete/", views.StationDeleteView.as_view(), name="station_delete"),

    # Оптические линии
    path("links/", views.OpticalLinkListView.as_view(), name="links"),
    path("links/new/", views.OpticalLinkCreateView.as_view(), name="link_create"),
    path("links/<int:pk>/edit/", views.OpticalLinkUpdateView.as_view(), name="link_update"),
    path("links/<int:pk>/duplicate/", views.OpticalLinkDuplicateView.as_view(), name="link_duplicate"),
    path("links/<int:pk>/delete/", views.OpticalLinkDeleteView.as_view(), name="link_delete"),
    path("links/<int:link_id>/draw/", views.draw_path_adminui, name="link_draw"),
    path("closures/new/", views.ClosureCreateView.as_view(), name="closure_create"),
    path("closures/<int:pk>/edit/", views.ClosureUpdateView.as_view(), name="closure_update"),
    path("closures/<int:pk>/delete/", views.ClosureDeleteView.as_view(), name="closure_delete"),

    # ========== Alerts ==========
    path("alerts/", views.AlertListView.as_view(), name="alerts"),
    path("alerts/<int:pk>/", views.AlertDetailView.as_view(), name="alert_detail"),
    path("alerts/<int:pk>/resolve/", api.alert_resolve, name="alert_resolve"),
    path("alerts/bulk-resolve/", api.alert_bulk_resolve, name="alert_bulk_resolve"),

    # ========== Users ==========
    path("users/", views.UserListView.as_view(), name="users"),
    path("users/new/", views.UserCreateView.as_view(), name="user_create"),
    path("users/<int:pk>/edit/", views.UserUpdateView.as_view(), name="user_update"),
    path("users/<int:pk>/roles/", views.RolesMovedView.as_view(), name="user_roles"),
    path("users/<int:pk>/delete/", views.UserDeleteView.as_view(), name="user_delete"),
    path("users/pending/", accounts_views.PendingUsersListView.as_view(), name="users_pending"),
    path("users/<int:pk>/approve/", views.UserApproveView.as_view(), name="user_approve"),
    path("users/<int:pk>/reject/", accounts_views.RejectUserView.as_view(), name="user_reject"),

    # ========== AJAX API ==========
    path("api/test-snmp/", api.test_snmp, name="test_snmp"),
    path("api/test-oid/", api.test_oid, name="test_oid"),
]

# === Modules ===
urlpatterns += [
    path("modules/", views.ModuleListView.as_view(), name="modules"),
    path("modules/new/", views.ModuleCreateView.as_view(), name="module_create"),
    path("modules/<int:pk>/edit/", views.ModuleUpdateView.as_view(), name="module_update"),
]
