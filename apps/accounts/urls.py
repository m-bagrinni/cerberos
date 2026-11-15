from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.CerberusLoginView.as_view(), name="login"),
    path("logout/", views.CerberusLogoutView.as_view(), name="logout"),
    path("register/", views.RegisterView.as_view(), name="register"),
]
