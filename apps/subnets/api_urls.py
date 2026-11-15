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
    tcpdump_stream_view,
    tcpdump_poll_view,
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

    # tcpdump — интерфейсы + запуск
    path("subnets/tcpdump/ifaces/", tcpdump_ifaces_view, name="subnet-tcpdump-ifaces"),
    path("subnets/tcpdump/iface-for/", tcpdump_subnet_iface_view, name="subnet-tcpdump-iface-for"),

    # tcpdump — pcap + скачивание
    path("subnets/tcpdump/stream/", tcpdump_stream_view, name="subnet-tcpdump-stream"),
    path("subnets/tcpdump/poll/", tcpdump_poll_view, name="subnet-tcpdump-poll"),
    path("subnets/tcpdump/pcap/", tcpdump_pcap_view, name="subnet-tcpdump-pcap"),
    path("subnets/tcpdump/download/<str:file_id>/", tcpdump_download_view, name="subnet-tcpdump-download"),

    # tcpdump — текстовый вывод (должен быть ПОСЛЕ pcap, иначе pcap перехватится)
    path("subnets/tcpdump/", tcpdump_view, name="subnet-tcpdump"),

    path("subnets/resolve-mac/", resolve_mac_view, name="resolve-mac"),
    path("subnets/resolve-ip/", resolve_ip_info_view, name="resolve-ip"),
    path("subnets/bulk-attach-station/", bulk_attach_station_view, name="bulk-attach-station"),
    path("", include(router.urls)),
]
