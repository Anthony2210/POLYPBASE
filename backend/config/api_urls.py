from django.urls import path

from apps.accounts.api_views import UserProfileAPIView
from apps.cultures.api_views import (
    BoxDetailAPIView,
    BoxListAPIView,
    BoxMeasurementListCreateAPIView,
    DashboardAPIView,
    HealthAPIView,
    ThermalZoneListAPIView,
)

urlpatterns = [
    path("health/", HealthAPIView.as_view(), name="api_health"),
    path("dashboard/", DashboardAPIView.as_view(), name="api_dashboard"),
    path("boxes/", BoxListAPIView.as_view(), name="api_box_list"),
    path("boxes/<int:pk>/", BoxDetailAPIView.as_view(), name="api_box_detail"),
    path(
        "boxes/<int:box_id>/measurements/",
        BoxMeasurementListCreateAPIView.as_view(),
        name="api_box_measurements",
    ),
    path("thermal-zones/", ThermalZoneListAPIView.as_view(), name="api_thermal_zone_list"),
    path("profile/", UserProfileAPIView.as_view(), name="api_profile"),
]
