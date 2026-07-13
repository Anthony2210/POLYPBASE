from django.urls import path

from . import views

urlpatterns = [
    # QR code image for a box. The React app requests it with the browser origin,
    # so the printed code points at the app rather than at Django.
    path("boites/<int:box_id>/qr.svg", views.box_qr, name="qr_boite"),
    # Stable target printed on QR labels: records the scan, then hands over to
    # the React box sheet.
    path("bac/<int:box_id>/", views.scan_box, name="scan_boite"),
    path("politique-confidentialite/", views.privacy_policy, name="politique_confidentialite"),
]
