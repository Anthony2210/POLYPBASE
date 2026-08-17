"""Verify the cleaned historical database before it is used by Polypbase."""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path


DEFAULT_EXPECTED_DATABASE = "polypbase_staging"
IMPORT_DAY = datetime.date(2026, 7, 3)
EXPECTED_BACKUP_SHA256 = (
    "2a39adac80c51a1411fb46a09c6bf3acbbd64cac317e6b650c8597a790f7af83"
)


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument(
        "--expected-database",
        default=DEFAULT_EXPECTED_DATABASE,
        help="Database name that must be active for the verification.",
    )
    return command


def main():
    args = parser().parse_args()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    sys.path.insert(0, str(Path.cwd()))

    import django

    django.setup()

    from django.contrib.auth import get_user_model
    from django.db import connection
    from django.db.models import Count, Q

    from apps.accounts.models import OrganizationMembership
    from apps.audit.models import Alert, AuditLog
    from apps.cultures.models import (
        Box,
        BoxLineage,
        BoxLocation,
        BoxMovement,
        BoxTransfer,
        BoxTransferImport,
        SubcultureEvent,
        ThermalZone,
    )
    from apps.measurements.models import BiologicalMeasurement, DailyTemperature
    from apps.organizations.models import Organization

    User = get_user_model()
    current_database = connection.settings_dict["NAME"]
    checks = {
        "database": current_database == args.expected_database,
        "boxes": Box.objects.count() == 555,
        "historical_boxes_only": not Box.objects.exclude(
            created_on=IMPORT_DAY
        ).exists(),
        "active_boxes": not Box.objects.exclude(
            status=Box.Status.ACTIVE, stop_reason=""
        ).exists(),
        "biological_measurements": BiologicalMeasurement.objects.count() == 36_779,
        "historical_measurements_only": not BiologicalMeasurement.objects.exclude(
            created_at__date=IMPORT_DAY
        ).exists(),
        "box_locations": BoxLocation.objects.count() == 1_391,
        "no_post_import_location_start": not BoxLocation.objects.filter(
            starts_at__date__gt=IMPORT_DAY
        ).exists(),
        "no_post_import_location_end": not BoxLocation.objects.filter(
            ends_at__date__gt=IMPORT_DAY
        ).exists(),
        "one_current_location_max": not Box.objects.annotate(
            current_locations=Count(
                "locations", filter=Q(locations__ends_at__isnull=True)
            )
        ).filter(current_locations__gt=1).exists(),
        "box_movements": BoxMovement.objects.count() == 0,
        "subcultures": SubcultureEvent.objects.count() == 0,
        "lineages": BoxLineage.objects.count() == 0,
        "box_transfers": BoxTransfer.objects.count() == 0,
        "box_transfer_imports": BoxTransferImport.objects.count() == 0,
        "daily_temperatures": DailyTemperature.objects.count() == 0,
        "alerts": Alert.objects.count() == 0,
        "zones": ThermalZone.objects.count() == 6,
        "zone_test_fields_cleared": not ThermalZone.objects.filter(
            Q(capacity__isnull=False) | Q(salinity_psu__isnull=False)
        ).exists(),
        "organization": list(Organization.objects.values_list("name", flat=True))
        == ["Aquarium de Paris"],
        "users": set(User.objects.values_list("username", flat=True))
        == {"admin", "antho_ca"},
        "memberships": OrganizationMembership.objects.count() == 2,
        "audit_log": AuditLog.objects.count() == 1,
    }

    mismatched_boxes = []
    for box in Box.objects.order_by("id").iterator():
        current_location = (
            box.locations.filter(ends_at__isnull=True)
            .order_by("-starts_at", "-id")
            .first()
        )
        expected_zone_id = current_location.thermal_zone_id if current_location else None
        if box.thermal_zone_id != expected_zone_id:
            mismatched_boxes.append(box.global_code)
    checks["current_zones_match_locations"] = not mismatched_boxes

    audit_entry = AuditLog.objects.first()
    checks["backup_traceability"] = bool(
        audit_entry
        and audit_entry.action == AuditLog.Action.IMPORT
        and audit_entry.metadata.get("source_backup_sha256")
        == EXPECTED_BACKUP_SHA256
    )

    result = {
        "status": "ok" if all(checks.values()) else "failed",
        "checks": checks,
        "mismatched_boxes": mismatched_boxes,
    }
    print(json.dumps(result, indent=2, ensure_ascii=True))
    if result["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
