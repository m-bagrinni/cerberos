from rest_framework.routers import DefaultRouter

from django.urls import path
from .api import (
    DeviceProfileViewSet,
    DeviceViewSet,
    MetricDefinitionViewSet,
    VendorViewSet,
    device_statuses,
)

router = DefaultRouter()
router.register(r"vendors", VendorViewSet, basename="vendor")
router.register(r"profiles", DeviceProfileViewSet, basename="profile")
router.register(r"metrics", MetricDefinitionViewSet, basename="metric")
router.register(r"devices", DeviceViewSet, basename="device")

urlpatterns = router.urls + [
    path("devices/statuses/", device_statuses, name="device-statuses"),
]
