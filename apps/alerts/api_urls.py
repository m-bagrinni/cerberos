from django.urls import path
from .api import alert_ack, alert_release, alert_resolve_manual, alerts_list, storm_events_view

urlpatterns = [
    path("alerts/storm/", storm_events_view, name="alerts-storm"),
    path("alerts/", alerts_list, name="alerts-list"),
    path("alerts/<int:pk>/ack/", alert_ack, name="alerts-ack"),
    path("alerts/<int:pk>/resolve/", alert_resolve_manual, name="alerts-resolve"),
    path("alerts/<int:pk>/release/", alert_release, name="alerts-release"),
]
