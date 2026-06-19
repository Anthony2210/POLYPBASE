from django.urls import path

from apps.accounts.api_views import (
    OrganizationMemberListCreateAPIView,
    OrganizationMembershipDetailAPIView,
    UserProfileAPIView,
)
from apps.cultures.api_views import (
    BoxDetailAPIView,
    BoxLineageGraphAPIView,
    BoxListAPIView,
    BoxMeasurementListCreateAPIView,
    BoxMoveAPIView,
    BoxSubcultureCreateAPIView,
    DashboardAPIView,
    HealthAPIView,
    ThermalZoneListAPIView,
)
from apps.exports.views import (
    MeasurementExportOptionsAPIView,
    WeeklyMeasurementCSVExportAPIView,
    WeeklyMeasurementPreviewAPIView,
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
        "boxes/<int:box_id>/move/",
        BoxMoveAPIView.as_view(),
        name="api_box_move",
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
    path(
        "exports/measurements/preview/",
        WeeklyMeasurementPreviewAPIView.as_view(),
        name="api_export_measurements_preview",
    ),
    path("profile/", UserProfileAPIView.as_view(), name="api_profile"),
    path(
        "accounts/members/",
        OrganizationMemberListCreateAPIView.as_view(),
        name="api_account_members",
    ),
    path(
        "accounts/members/<int:pk>/",
        OrganizationMembershipDetailAPIView.as_view(),
        name="api_account_member_detail",
    ),
]
