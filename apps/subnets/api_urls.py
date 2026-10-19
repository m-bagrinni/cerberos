"""URL-маршруты REST API приложения subnets."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api import IpAddressViewSet, SubnetScanViewSet, SubnetViewSet

router = DefaultRouter()
router.register("subnets", SubnetViewSet, basename="subnet")
router.register("ip-addresses", IpAddressViewSet, basename="ip-address")
router.register("subnet-scans", SubnetScanViewSet, basename="subnet-scan")

urlpatterns = [
    path("", include(router.urls)),
]
