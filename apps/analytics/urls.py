from django.urls import path
from . import views, api

app_name = "analytics"

urlpatterns = [
    path("", views.analytics_dashboard, name="dashboard"),
    path("api/data/", api.analytics_data, name="api_data"),
    path("report/generate/", views.generate_report, name="generate_report"),
]
