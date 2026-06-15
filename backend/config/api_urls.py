from django.urls import path

from apps.accounts.api_views import UserProfileAPIView
from apps.cultures.api_views import (
    BoxDetailAPIView,
    BoxLineageGraphAPIView,
    BoxListAPIView,
    BoxMeasurementListCreateAPIView,
    BoxSubcultureCreateAPIView,
    DashboardAPIView,
    HealthAPIView,
    ThermalZoneListAPIView,
)
from apps.exports.views import (
    MeasurementExportOptionsAPIView,
    WeeklyMeasurementCSVExportAPIView,
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
    path(
        "boxes/<int:box_id>/subcultures/",
        BoxSubcultureCreateAPIView.as_view(),
        name="api_box_subcultures",
    ),
    path(
        "boxes/<int:box_id>/lineage/",
        BoxLineageGraphAPIView.as_view(),
        name="api_box_lineage",
    ),
    path("thermal-zones/", ThermalZoneListAPIView.as_view(), name="api_thermal_zone_list"),
    path(
        "exports/options/",
        MeasurementExportOptionsAPIView.as_view(),
        name="api_export_options",
    ),
    path(
        "exports/measurements.csv",
        WeeklyMeasurementCSVExportAPIView.as_view(),
        name="api_export_measurements_csv",
    ),
    path("profile/", UserProfileAPIView.as_view(), name="api_profile"),
]
