from django.urls import path

from . import views

urlpatterns = [
    path("boites/", views.box_list, name="liste_boites"),
    path("boites/<int:box_id>/", views.box_detail, name="detail_boite"),
    path("boites/<int:box_id>/releve/", views.add_measurement, name="ajouter_releve"),
    path("boites/<int:box_id>/qr.svg", views.box_qr, name="qr_boite"),
    path("bac/<int:box_id>/", views.scan_box, name="scan_boite"),
    path("politique-confidentialite/", views.privacy_policy, name="politique_confidentialite"),
]
