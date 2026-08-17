"""Remove known application tests from the isolated VM staging database."""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path


EXPECTED_DATABASE = "polypbase_staging"
IMPORT_DAY = datetime.date(2026, 7, 3)
EXPECTED_HISTORICAL_BOXES = 555
EXPECTED_HISTORICAL_MEASUREMENTS = 36_779
EXPECTED_TEST_BOXES = 10
EXPECTED_TEST_MEASUREMENTS = 30
SOURCE_BACKUP_SHA256 = (
    "2a39adac80c51a1411fb46a09c6bf3acbbd64cac317e6b650c8597a790f7af83"
)
KEPT_USERNAMES = {"admin", "antho_ca"}
EXPECTED_USERNAMES = {
    "admin",
    "demo_admin",
    "demo_lab",
    "demo_viewer",
    "aaa",
    "antho_ca",
    "abou",
    "mob",
}


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument(
        "--apply",
        action="store_true",
        help="Apply the cleanup. Without this flag, only print the plan.",
    )
    return command


def main():
    args = parser().parse_args()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    sys.path.insert(0, str(Path.cwd()))

    import django

    django.setup()

    from django.contrib.auth import get_user_model
    from django.contrib.sessions.models import Session
    from django.db import connection, transaction

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

    database_name = connection.settings_dict["NAME"]
    if database_name != EXPECTED_DATABASE:
        raise RuntimeError(
            f"Refusing cleanup on {database_name!r}; expected {EXPECTED_DATABASE!r}."
        )

    User = get_user_model()
    historical_boxes = Box.objects.filter(created_on=IMPORT_DAY)
    test_boxes = Box.objects.exclude(created_on=IMPORT_DAY)
    historical_measurements = BiologicalMeasurement.objects.filter(
        created_at__date=IMPORT_DAY
    )
    test_measurements = BiologicalMeasurement.objects.exclude(
        created_at__date=IMPORT_DAY
    )
    usernames = set(User.objects.values_list("username", flat=True))

    checks = {
        "historical_boxes": historical_boxes.count(),
        "test_boxes": test_boxes.count(),
        "historical_measurements": historical_measurements.count(),
        "test_measurements": test_measurements.count(),
        "users": sorted(usernames),
    }
    expected = {
        "historical_boxes": EXPECTED_HISTORICAL_BOXES,
        "test_boxes": EXPECTED_TEST_BOXES,
        "historical_measurements": EXPECTED_HISTORICAL_MEASUREMENTS,
        "test_measurements": EXPECTED_TEST_MEASUREMENTS,
        "users": sorted(EXPECTED_USERNAMES),
    }
    if checks != expected:
        raise RuntimeError(
            "The restored database does not match the audited snapshot:\n"
            + json.dumps({"actual": checks, "expected": expected}, indent=2)
        )

    plan = {
        "database": database_name,
        "mode": "apply" if args.apply else "dry-run",
        "preserved": {
            "boxes": historical_boxes.count(),
            "biological_measurements": historical_measurements.count(),
            "thermal_zones": ThermalZone.objects.count(),
            "organization": "Aquarium de Paris",
            "users": sorted(KEPT_USERNAMES),
        },
        "removed": {
            "boxes": test_boxes.count(),
            "biological_measurements": test_measurements.count(),
            "box_movements": BoxMovement.objects.count(),
            "subcultures": SubcultureEvent.objects.count(),
            "lineages": BoxLineage.objects.count(),
            "box_transfers": BoxTransfer.objects.count(),
            "box_transfer_imports": BoxTransferImport.objects.count(),
            "daily_temperatures": DailyTemperature.objects.count(),
            "alerts": Alert.objects.count(),
            "audit_logs": AuditLog.objects.count(),
            "test_users": sorted(usernames - KEPT_USERNAMES),
            "test_organizations": list(
                Organization.objects.exclude(name="Aquarium de Paris")
                .order_by("id")
                .values_list("name", flat=True)
            ),
        },
    }
    print(json.dumps(plan, indent=2, ensure_ascii=True))
    if not args.apply:
        return

    with transaction.atomic():
        # Remove test-only relations before deleting their protected boxes.
        BoxLineage.objects.all().delete()
        SubcultureEvent.objects.all().delete()
        BoxTransferImport.objects.all().delete()
        BoxTransfer.objects.all().delete()
        BoxMovement.objects.all().delete()

        # The import created every historical measurement on IMPORT_DAY.
        test_measurements.delete()
        DailyTemperature.objects.all().delete()
        Alert.objects.all().delete()

        # Test moves created new stays and closed the imported active stays.
        BoxLocation.objects.filter(starts_at__date__gt=IMPORT_DAY).delete()
        BoxLocation.objects.filter(ends_at__date__gt=IMPORT_DAY).update(ends_at=None)

        # Remove every box created through the application after the import.
        test_boxes.delete()

        # Rebuild the current zone from the remaining historical location data.
        for box in Box.objects.filter(created_on=IMPORT_DAY).iterator():
            current_location = (
                box.locations.filter(ends_at__isnull=True)
                .order_by("-starts_at", "-id")
                .first()
            )
            current_zone_id = (
                current_location.thermal_zone_id if current_location else None
            )
            updates = []
            if box.thermal_zone_id != current_zone_id:
                box.thermal_zone_id = current_zone_id
                updates.append("thermal_zone")
            if box.status != Box.Status.ACTIVE:
                box.status = Box.Status.ACTIVE
                updates.append("status")
            if box.stop_reason:
                box.stop_reason = ""
                updates.append("stop_reason")
            if updates:
                box.save(update_fields=updates)

        # Capacity and salinity values were entered during interface tests.
        ThermalZone.objects.update(capacity=None, salinity_psu=None)

        # Keep only the real structure and the two deployment accounts.
        Organization.objects.exclude(name="Aquarium de Paris").delete()
        User.objects.exclude(username__in=KEPT_USERNAMES).delete()
        OrganizationMembership.objects.exclude(
            organization__name="Aquarium de Paris"
        ).delete()
        Session.objects.all().delete()

        # Replace the polluted test history with one transparent deployment entry.
        AuditLog.objects.all().delete()
        organization = Organization.objects.get(name="Aquarium de Paris")
        deployment_user = User.objects.get(username="antho_ca")
        AuditLog.objects.create(
            organization=organization,
            user=deployment_user,
            action=AuditLog.Action.IMPORT,
            object_type="database",
            object_id="historical-import-2026-07-03",
            description="Historical data restored from the verified Neon backup.",
            metadata={
                "source_backup_sha256": SOURCE_BACKUP_SHA256,
                "historical_boxes": EXPECTED_HISTORICAL_BOXES,
                "historical_measurements": EXPECTED_HISTORICAL_MEASUREMENTS,
                "test_data_removed": plan["removed"],
            },
        )

    result = {
        "boxes": Box.objects.count(),
        "biological_measurements": BiologicalMeasurement.objects.count(),
        "box_locations": BoxLocation.objects.count(),
        "box_movements": BoxMovement.objects.count(),
        "subcultures": SubcultureEvent.objects.count(),
        "lineages": BoxLineage.objects.count(),
        "box_transfers": BoxTransfer.objects.count(),
        "daily_temperatures": DailyTemperature.objects.count(),
        "alerts": Alert.objects.count(),
        "organizations": list(Organization.objects.values_list("name", flat=True)),
        "users": list(User.objects.order_by("id").values_list("username", flat=True)),
        "audit_logs": AuditLog.objects.count(),
    }
    print(json.dumps({"result": result}, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
