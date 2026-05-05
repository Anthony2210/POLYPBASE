from django.urls import path

from . import views

urlpatterns = [
    path("boites/", views.liste_boites, name="liste_boites"),
    path("boites/<int:boite_id>/", views.detail_boite, name="detail_boite"),
    path("boites/<int:boite_id>/releve/", views.ajouter_releve, name="ajouter_releve"),
    path("politique-confidentialite/", views.politique_confidentialite, name="politique_confidentialite"),
    path("api/health/", views.api_health, name="api_health"),
    path("api/dashboard/", views.api_dashboard, name="api_dashboard"),
    path("api/boites/", views.api_boites, name="api_boites"),
    path("api/boites/<int:boite_id>/", views.api_boite_detail, name="api_boite_detail"),
    path("api/boites/<int:boite_id>/releves/", views.api_releves_boite, name="api_releves_boite"),
    path("api/zones/", views.api_zones, name="api_zones"),
]
