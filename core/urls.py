from django.urls import path

from . import views

urlpatterns = [
    path("boites/", views.liste_boites, name="liste_boites"),
    path("boites/<int:boite_id>/", views.detail_boite, name="detail_boite"),
    path("boites/<int:boite_id>/releve/", views.ajouter_releve, name="ajouter_releve"),
]