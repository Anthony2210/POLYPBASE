from django.urls import path

from . import views

urlpatterns = [
    path("profile/", views.account_settings, name="account_settings"),
]
