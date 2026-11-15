from django.urls import path
from rest_framework.routers import DefaultRouter

from .api import (
    InterfaceViewSet,
    MetricValueViewSet,
    VlanViewSet,
    interface_traffic_history,
    interface_ddm_history,
    ne_metric_history,
    ne_ddm_ports,
    ne_poll_now,
    gateway_poll_now,
    device_poll_now,
    device_poll_interfaces_now,
    ne_reorder,
    ne_reorder_bulk,
    ne_metrics_bulk,
    set_interface_alias,
)

router = DefaultRouter()
router.register(r"metric-values", MetricValueViewSet, basename="metricvalue")
router.register(r"interfaces", InterfaceViewSet, basename="interface")
router.register(r"vlans", VlanViewSet, basename="vlan")

urlpatterns = router.urls + [
    path("interfaces/<int:pk>/traffic-history/", interface_traffic_history, name="interface-traffic-history"),
    path("interfaces/<int:pk>/ddm-history/", interface_ddm_history, name="interface-ddm-history"),
    path("interfaces/<int:pk>/set-alias/", set_interface_alias, name="interface-set-alias"),
    path("ne/<int:ne_id>/metric-history/", ne_metric_history, name="ne-metric-history"),
    path("ne/<int:ne_id>/ddm-ports/", ne_ddm_ports, name="ne-ddm-ports"),
    path("ne/<int:ne_id>/poll-now/", ne_poll_now, name="ne-poll-now"),
    path("gateway/<str:gateway_ip>/poll-now/", gateway_poll_now, name="gateway-poll-now"),
    path("devices/<str:ip>/poll-now/", device_poll_now, name="device-poll-now"),
    path("devices/<str:ip>/poll-interfaces-now/", device_poll_interfaces_now, name="device-poll-interfaces-now"),
    path("ne/<int:ne_id>/reorder/", ne_reorder, name="ne-reorder"),
    path("ne/reorder-bulk/", ne_reorder_bulk, name="ne-reorder-bulk"),
    path("ne/metrics-bulk/", ne_metrics_bulk, name="ne-metrics-bulk"),
]
