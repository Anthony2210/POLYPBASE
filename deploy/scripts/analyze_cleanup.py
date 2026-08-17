"""Build a read-only JSON report for cleaning a restored Polypbase database."""

import datetime
import json
import os
import sys
from pathlib import Path


IMPORT_DAY = datetime.date(2026, 7, 3)


def iso(value):
    return value.isoformat() if value is not None else None


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    sys.path.insert(0, str(Path.cwd()))

    import django

    django.setup()

    from django.contrib.auth import get_user_model
    from django.db.models import Max

    from apps.accounts.models import OrganizationMembership
    from apps.audit.models import Alert, AuditLog
    from apps.cultures.models import (
        Box,
        BoxLineage,
        BoxLocation,
        BoxMovement,
        BoxTransfer,
        BoxTransferImport,
        IdentificationTag,
        SubcultureEvent,
        ThermalZone,
    )
    from apps.measurements.models import (
        BiologicalMeasurement,
        DailyTemperature,
        Observation,
        Probe,
        SalinityMeasurement,
        TemperatureMeasurement,
        ThermalAnomaly,
    )
    from apps.organizations.models import Organization

    historical_measurements = BiologicalMeasurement.objects.filter(
        created_at__date=IMPORT_DAY
    )
    historical_measurement_ids = set(
        historical_measurements.values_list("id", flat=True)
    )
    historical_box_ids = set(historical_measurements.values_list("box_id", flat=True))
    import_box_max_id = Box.objects.filter(created_on=IMPORT_DAY).aggregate(
        max_id=Max("id")
    )["max_id"]

    model_counts = {
        "users": get_user_model().objects.count(),
        "organizations": Organization.objects.count(),
        "memberships": OrganizationMembership.objects.count(),
        "thermal_zones": ThermalZone.objects.count(),
        "boxes": Box.objects.count(),
        "box_locations": BoxLocation.objects.count(),
        "box_movements": BoxMovement.objects.count(),
        "subcultures": SubcultureEvent.objects.count(),
        "lineages": BoxLineage.objects.count(),
        "box_transfers": BoxTransfer.objects.count(),
        "box_transfer_imports": BoxTransferImport.objects.count(),
        "identification_tags": IdentificationTag.objects.count(),
        "biological_measurements": BiologicalMeasurement.objects.count(),
        "daily_temperatures": DailyTemperature.objects.count(),
        "observations": Observation.objects.count(),
        "probes": Probe.objects.count(),
        "temperature_measurements": TemperatureMeasurement.objects.count(),
        "salinity_measurements": SalinityMeasurement.objects.count(),
        "thermal_anomalies": ThermalAnomaly.objects.count(),
        "alerts": Alert.objects.count(),
        "audit_logs": AuditLog.objects.count(),
    }

    users = []
    for user in get_user_model().objects.order_by("id"):
        users.append(
            {
                "id": user.id,
                "username": user.username,
                "is_active": user.is_active,
                "is_staff": user.is_staff,
                "is_superuser": user.is_superuser,
                "date_joined": iso(user.date_joined),
                "last_login": iso(user.last_login),
            }
        )

    organizations = list(
        Organization.objects.order_by("id").values("id", "name", "slug")
    )
    memberships = list(
        OrganizationMembership.objects.order_by("id").values(
            "id", "user_id", "organization_id", "role", "is_active"
        )
    )

    zones = []
    for zone in ThermalZone.objects.order_by("id"):
        zones.append(
            {
                "id": zone.id,
                "organization_id": zone.organization_id,
                "name": zone.name,
                "zone_type": zone.zone_type,
                "target_temperature_c": str(zone.target_temperature_c)
                if zone.target_temperature_c is not None
                else None,
                "capacity": zone.capacity,
                "salinity_psu": str(zone.salinity_psu)
                if zone.salinity_psu is not None
                else None,
                "is_active": zone.is_active,
                "notes": zone.notes,
            }
        )

    post_import_boxes = []
    box_query = Box.objects.filter(created_on__gt=IMPORT_DAY).select_related(
        "strain__species", "thermal_zone"
    )
    for box in box_query.order_by("id"):
        post_import_boxes.append(
            {
                "id": box.id,
                "global_code": box.global_code,
                "created_on": iso(box.created_on),
                "status": box.status,
                "thermal_zone_id": box.thermal_zone_id,
                "species": box.strain.species.scientific_name,
                "measurements": box.biological_measurements.count(),
                "locations": box.locations.count(),
                "movements": box.movements.count(),
            }
        )

    daily_temperatures = []
    for row in DailyTemperature.objects.select_related("thermal_zone").order_by(
        "date", "thermal_zone_id"
    ):
        daily_temperatures.append(
            {
                "id": row.id,
                "thermal_zone_id": row.thermal_zone_id,
                "thermal_zone": row.thermal_zone.name,
                "date": iso(row.date),
                "min": str(row.min_temperature_c)
                if row.min_temperature_c is not None
                else None,
                "average": str(row.average_temperature_c),
                "max": str(row.max_temperature_c)
                if row.max_temperature_c is not None
                else None,
                "measurement_count": row.measurement_count,
            }
        )

    movements = []
    for movement in BoxMovement.objects.select_related("box").order_by("id"):
        movements.append(
            {
                "id": movement.id,
                "box_id": movement.box_id,
                "box": movement.box.global_code,
                "from_thermal_zone_id": movement.from_thermal_zone_id,
                "to_thermal_zone_id": movement.to_thermal_zone_id,
                "moved_at": iso(movement.moved_at),
                "user_id": movement.user_id,
            }
        )

    locations = []
    changed_box_ids = set(BoxMovement.objects.values_list("box_id", flat=True))
    changed_box_ids.update(
        Box.objects.filter(created_on__gt=IMPORT_DAY).values_list("id", flat=True)
    )
    for location in BoxLocation.objects.filter(box_id__in=changed_box_ids).order_by(
        "box_id", "starts_at", "id"
    ):
        locations.append(
            {
                "id": location.id,
                "box_id": location.box_id,
                "thermal_zone_id": location.thermal_zone_id,
                "starts_at": iso(location.starts_at),
                "ends_at": iso(location.ends_at),
                "notes": location.notes,
            }
        )

    audit_mutations = []
    mutation_actions = {
        AuditLog.Action.CREATION,
        AuditLog.Action.UPDATE,
        AuditLog.Action.ARCHIVE,
        AuditLog.Action.ENTRY,
        AuditLog.Action.SUBCULTURE,
        AuditLog.Action.TRANSFER,
        AuditLog.Action.IMPORT,
    }
    for log in (
        AuditLog.objects.filter(action__in=mutation_actions)
        .select_related("user")
        .order_by("created_at", "id")
    ):
        audit_mutations.append(
            {
                "id": log.id,
                "created_at": iso(log.created_at),
                "edited_at": iso(log.edited_at),
                "user": log.user.username if log.user else None,
                "action": log.action,
                "object_type": log.object_type,
                "object_id": log.object_id,
                "description": log.description,
                "metadata": log.metadata,
                "original_values": log.original_values,
            }
        )

    report = {
        "import_day": iso(IMPORT_DAY),
        "import_box_max_id": import_box_max_id,
        "historical_measurements": len(historical_measurement_ids),
        "historical_boxes_with_measurements": len(historical_box_ids),
        "model_counts": model_counts,
        "users": users,
        "organizations": organizations,
        "memberships": memberships,
        "zones": zones,
        "post_import_boxes": post_import_boxes,
        "daily_temperatures": daily_temperatures,
        "movements": movements,
        "locations_for_changed_boxes": locations,
        "audit_mutations": audit_mutations,
    }
    print(json.dumps(report, ensure_ascii=True, indent=2, default=str))


if __name__ == "__main__":
    main()
