from rest_framework.routers import DefaultRouter

from .views import OpticalLinkViewSet, StationViewSet, route_between_points, poll_links_api

router = DefaultRouter()
router.register(r"stations", StationViewSet, basename="station")
router.register(r"optical-links", OpticalLinkViewSet, basename="optical-link")

urlpatterns = router.urls

# Дополнительные endpoints (не через router)
from django.urls import path
urlpatterns += [
    path("route/", route_between_points, name="route"),
]
