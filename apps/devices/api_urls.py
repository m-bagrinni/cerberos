from rest_framework.routers import DefaultRouter

from .api import (
    DeviceProfileViewSet,
    DeviceViewSet,
    MetricDefinitionViewSet,
    VendorViewSet,
)

router = DefaultRouter()
router.register(r"vendors", VendorViewSet, basename="vendor")
router.register(r"profiles", DeviceProfileViewSet, basename="profile")
router.register(r"metrics", MetricDefinitionViewSet, basename="metric")
router.register(r"devices", DeviceViewSet, basename="device")

urlpatterns = router.urls
