from django.urls import path
from . import api

urlpatterns = [
    path("services/containers/", api.containers_list, name="services_containers"),
    path("services/containers/<str:name>/<str:action>/", api.container_action, name="services_container_action"),
    path("services/tasks/", api.tasks_list, name="services_tasks"),
    path("services/tasks/<int:task_id>/<str:action>/", api.task_action, name="services_task_action"),
    path("services/health/", api.health, name="services_health"),
    path("services/events/", api.events_list, name="services_events"),
]
