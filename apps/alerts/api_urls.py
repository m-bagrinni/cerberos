from django.urls import path
from .api import alerts_list, storm_events_view

urlpatterns = [
    path("alerts/storm/", storm_events_view, name="alerts-storm"),
    path("alerts/", alerts_list, name="alerts-list"),
]
