from django.urls import path

from apps.accounts.api_views import (
    AdminAuditLogListAPIView,
    OrganizationMemberListCreateAPIView,
    OrganizationMembershipDetailAPIView,
    PasswordResetConfirmAPIView,
    PasswordResetRequestAPIView,
    SessionLoginAPIView,
    SessionLogoutAPIView,
    UserProfileAPIView,
)
from apps.cultures.api_views import (
    AlertResolveAPIView,
    BoxAccessAPIView,
    BoxActivateAPIView,
    BoxArchiveAPIView,
    BoxDetailAPIView,
    BoxLineageGraphAPIView,
    BoxListAPIView,
    BoxMeasurementDetailAPIView,
    BoxMeasurementListCreateAPIView,
    BoxMoveAPIView,
    BoxSubcultureCreateAPIView,
    BoxTransferCreateAPIView,
    BoxTransferImportAPIView,
    DashboardAPIView,
    HealthAPIView,
    OverviewActiveBoxesAPIView,
    ProbeCreateAPIView,
    ThermalZoneDetailAPIView,
    ThermalZoneListCreateAPIView,
    ThermalZoneManualTemperatureAPIView,
)
from apps.organizations.api_views import OrganizationCreateAPIView, OrganizationDetailAPIView
from apps.exports.views import (
    MeasurementExportOptionsAPIView,
    WeeklyMeasurementCSVExportAPIView,
    WeeklyMeasurementPreviewAPIView,
)

urlpatterns = [
    path("health/", HealthAPIView.as_view(), name="api_health"),
    path("auth/session/", SessionLoginAPIView.as_view(), name="api_session_login"),
    path("auth/logout/", SessionLogoutAPIView.as_view(), name="api_session_logout"),
    path(
        "auth/password-reset/",
        PasswordResetRequestAPIView.as_view(),
        name="api_password_reset_request",
    ),
    path(
        "auth/password-reset/confirm/",
        PasswordResetConfirmAPIView.as_view(),
        name="api_password_reset_confirm",
    ),
    path("dashboard/", DashboardAPIView.as_view(), name="api_dashboard"),
    path("overview/active-boxes/", OverviewActiveBoxesAPIView.as_view(), name="api_overview_active_boxes"),
    path("boxes/", BoxListAPIView.as_view(), name="api_box_list"),
    path("boxes/<int:pk>/", BoxDetailAPIView.as_view(), name="api_box_detail"),
    path(
        "boxes/<int:box_id>/access/",
        BoxAccessAPIView.as_view(),
        name="api_box_access",
    ),
    path(
        "boxes/<int:box_id>/archive/",
        BoxArchiveAPIView.as_view(),
        name="api_box_archive",
    ),
    path(
        "boxes/<int:box_id>/activate/",
        BoxActivateAPIView.as_view(),
        name="api_box_activate",
    ),
    path(
        "boxes/<int:box_id>/measurements/",
        BoxMeasurementListCreateAPIView.as_view(),
        name="api_box_measurements",
    ),
    path(
        "boxes/<int:box_id>/measurements/<int:pk>/",
        BoxMeasurementDetailAPIView.as_view(),
        name="api_box_measurement_detail",
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
    path("thermal-zones/", ThermalZoneListCreateAPIView.as_view(), name="api_thermal_zone_list"),
    path("thermal-zones/<int:pk>/", ThermalZoneDetailAPIView.as_view(), name="api_thermal_zone_detail"),
    path(
        "thermal-zones/<int:pk>/temperature/",
        ThermalZoneManualTemperatureAPIView.as_view(),
        name="api_thermal_zone_manual_temperature",
    ),
    path("probes/", ProbeCreateAPIView.as_view(), name="api_probe_create"),
    path("box-transfers/", BoxTransferCreateAPIView.as_view(), name="api_box_transfer_create"),
    path("box-transfer-imports/", BoxTransferImportAPIView.as_view(), name="api_box_transfer_import"),
    path("alerts/<int:pk>/resolve/", AlertResolveAPIView.as_view(), name="api_alert_resolve"),
    path("organizations/", OrganizationCreateAPIView.as_view(), name="api_organization_create"),
    path("organizations/<int:pk>/", OrganizationDetailAPIView.as_view(), name="api_organization_detail"),
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
    path(
        "accounts/audit-log/",
        AdminAuditLogListAPIView.as_view(),
        name="api_account_audit_log",
    ),
]
