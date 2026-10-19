from django.urls import path

from . import api, views

app_name = "adminui"

urlpatterns = [
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

    # ========== Alerts ==========
    path("alerts/", views.AlertListView.as_view(), name="alerts"),
    path("alerts/<int:pk>/", views.AlertDetailView.as_view(), name="alert_detail"),
    path("alerts/<int:pk>/resolve/", api.alert_resolve, name="alert_resolve"),
    path("alerts/bulk-resolve/", api.alert_bulk_resolve, name="alert_bulk_resolve"),

    # ========== Users ==========
    path("users/", views.UserListView.as_view(), name="users"),
    path("users/new/", views.UserCreateView.as_view(), name="user_create"),
    path("users/<int:pk>/edit/", views.UserUpdateView.as_view(), name="user_update"),
    path("users/<int:pk>/delete/", views.UserDeleteView.as_view(), name="user_delete"),

    # ========== AJAX API ==========
    path("api/test-snmp/", api.test_snmp, name="test_snmp"),
    path("api/test-oid/", api.test_oid, name="test_oid"),
]
