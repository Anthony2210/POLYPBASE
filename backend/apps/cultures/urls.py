from django.urls import path

from . import views

urlpatterns = [
    # Protected SVG requests carry the active organization header.
    path("boites/<int:box_id>/qr.svg", views.box_qr, name="qr_boite"),
    # Stable printed entry only hands off the ID; the API resolves and audits it.
    path("bac/<int:box_id>/", views.scan_box, name="scan_boite"),
    path("politique-confidentialite/", views.privacy_policy, name="politique_confidentialite"),
]
