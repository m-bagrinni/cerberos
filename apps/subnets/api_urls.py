"""URL-маршруты REST API приложения subnets."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api import (
    IpAddressViewSet,
    SubnetScanViewSet,
    SubnetViewSet,
    ping_ip,
    subnet_traffic_view,
    tcpdump_view,
    tcpdump_ifaces_view,
    tcpdump_subnet_iface_view,
    tcpdump_pcap_view,
    tcpdump_download_view,
    tcpdump_validate_view,
    capture_live_start_view,
    capture_live_poll_view,
    capture_live_stop_view,
    capture_identify_view,
    resolve_mac_view,
    resolve_ip_info_view,
    bulk_attach_station_view,
)

router = DefaultRouter()
router.register("subnets", SubnetViewSet, basename="subnet")
router.register("ip-addresses", IpAddressViewSet, basename="ip-address")
router.register("subnet-scans", SubnetScanViewSet, basename="subnet-scan")

urlpatterns = [
    path("subnets/ping/", ping_ip, name="subnet-ping"),
    path("subnets/<int:subnet_id>/traffic/", subnet_traffic_view, name="subnet-traffic"),

    # Захват трафика: всё под subnets/tcpdump/ — право traffic_capture (apps/accounts/access.py)
    path("subnets/tcpdump/ifaces/", tcpdump_ifaces_view, name="subnet-tcpdump-ifaces"),
    path("subnets/tcpdump/iface-for/", tcpdump_subnet_iface_view, name="subnet-tcpdump-iface-for"),
    path("subnets/tcpdump/validate/", tcpdump_validate_view, name="subnet-tcpdump-validate"),
    path("subnets/tcpdump/live/", capture_live_start_view, name="subnet-capture-start"),
    path("subnets/tcpdump/live/<str:sid>/", capture_live_poll_view, name="subnet-capture-poll"),
    path("subnets/tcpdump/live/<str:sid>/stop/", capture_live_stop_view, name="subnet-capture-stop"),
    path("subnets/tcpdump/identify/", capture_identify_view, name="subnet-capture-identify"),
    path("subnets/tcpdump/pcap/", tcpdump_pcap_view, name="subnet-tcpdump-pcap"),
    path("subnets/tcpdump/download/<str:file_id>/", tcpdump_download_view, name="subnet-tcpdump-download"),
    path("subnets/tcpdump/", tcpdump_view, name="subnet-tcpdump"),

    path("subnets/resolve-mac/", resolve_mac_view, name="resolve-mac"),
    path("subnets/resolve-ip/", resolve_ip_info_view, name="resolve-ip"),
    path("subnets/bulk-attach-station/", bulk_attach_station_view, name="bulk-attach-station"),
    path("", include(router.urls)),
]
