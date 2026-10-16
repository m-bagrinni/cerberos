from rest_framework.routers import DefaultRouter

from .views import OpticalLinkViewSet, StationViewSet

router = DefaultRouter()
router.register(r"stations", StationViewSet, basename="station")
router.register(r"optical-links", OpticalLinkViewSet, basename="optical-link")

urlpatterns = router.urls
