"""Print a read-only inventory of Polypbase data and mutating audit events."""

import datetime
import os
import sys
from pathlib import Path


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    sys.path.insert(0, str(Path.cwd()))

    import django

    django.setup()

    from django.contrib.auth import get_user_model
    from django.db.models import Count, Max, Min
    from django.db.models.functions import TruncDate

    from apps.accounts.models import OrganizationMembership
    from apps.audit.models import Alert, AuditLog
    from apps.cultures.models import (
        Box,
        BoxLineage,
        BoxLocation,
        BoxMovement,
        SubcultureEvent,
        ThermalZone,
    )
    from apps.measurements.models import (
        BiologicalMeasurement,
        Observation,
        Probe,
        SalinityMeasurement,
        TemperatureMeasurement,
    )
    from apps.organizations.models import Organization
    from apps.taxonomy.models import Origin, Species, Strain

    models = {
        "users": get_user_model(),
        "organizations": Organization,
        "memberships": OrganizationMembership,
        "species": Species,
        "strains": Strain,
        "origins": Origin,
        "thermal_zones": ThermalZone,
        "boxes": Box,
        "box_locations": BoxLocation,
        "box_movements": BoxMovement,
        "subcultures": SubcultureEvent,
        "lineages": BoxLineage,
        "biological_measurements": BiologicalMeasurement,
        "observations": Observation,
        "probes": Probe,
        "temperature_measurements": TemperatureMeasurement,
        "salinity_measurements": SalinityMeasurement,
        "alerts": Alert,
        "audit_logs": AuditLog,
    }

    print("=== COUNTS ===")
    for name, model in models.items():
        print(f"{name}={model.objects.count()}")

    print("=== USERS ===")
    for user in get_user_model().objects.order_by("date_joined", "username"):
        print(
            " | ".join(
                [
                    user.username,
                    f"joined={user.date_joined.isoformat()}",
                    f"last_login={user.last_login.isoformat() if user.last_login else '-'}",
                    f"superuser={user.is_superuser}",
                    f"active={user.is_active}",
                ]
            )
        )

    print("=== AUDIT RANGE ===")
    print(AuditLog.objects.aggregate(first=Min("created_at"), last=Max("created_at")))

    print("=== MUTATIONS BY DAY ===")
    mutation_actions = [
        AuditLog.Action.CREATION,
        AuditLog.Action.UPDATE,
        AuditLog.Action.ARCHIVE,
        AuditLog.Action.ENTRY,
        AuditLog.Action.SUBCULTURE,
        AuditLog.Action.TRANSFER,
        AuditLog.Action.IMPORT,
    ]
    rows = (
        AuditLog.objects.filter(action__in=mutation_actions)
        .annotate(day=TruncDate("created_at"))
        .values("day", "action")
        .annotate(total=Count("id"))
        .order_by("day", "action")
    )
    for row in rows:
        print(f"{row['day']} | {row['action']} | {row['total']}")

    print("=== MUTATIONS BY USER ===")
    rows = (
        AuditLog.objects.filter(action__in=mutation_actions)
        .values("user__username", "action", "object_type")
        .annotate(total=Count("id"))
        .order_by("user__username", "action", "object_type")
    )
    for row in rows:
        print(
            f"{row['user__username'] or '-'} | {row['action']} | "
            f"{row['object_type'] or '-'} | {row['total']}"
        )

    print("=== RECENT MUTATIONS ===")
    rows = (
        AuditLog.objects.filter(action__in=mutation_actions)
        .select_related("user")
        .order_by("-created_at")[:200]
    )
    for row in reversed(list(rows)):
        print(
            " | ".join(
                [
                    row.created_at.isoformat(),
                    row.user.username if row.user else "-",
                    row.action,
                    row.object_type or "-",
                    row.object_id or "-",
                    row.description.replace("\n", " ")[:160] or "-",
                ]
            )
        )

    print("=== MEASUREMENT CREATION DAYS ===")
    rows = (
        BiologicalMeasurement.objects.annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Count("id"))
        .order_by("day")
    )
    for row in rows:
        print(f"{row['day']} | {row['total']}")

    import_day = datetime.date(2026, 7, 3)
    historical_measurements = BiologicalMeasurement.objects.filter(created_at__date=import_day)
    historical_ids = set(historical_measurements.values_list("id", flat=True))
    historical_box_ids = set(historical_measurements.values_list("box_id", flat=True))

    print("=== IMPORT BASELINE ===")
    print(f"import_day={import_day}")
    print(f"historical_measurements={len(historical_ids)}")
    print(f"historical_boxes={len(historical_box_ids)}")

    print("=== NON-HISTORICAL BOXES ===")
    for box in Box.objects.exclude(id__in=historical_box_ids).order_by("global_code"):
        print(f"{box.id} | {box.global_code} | {box.status} | zone={box.thermal_zone_id or '-'}")

    print("=== POST-IMPORT MEASUREMENTS ===")
    rows = (
        BiologicalMeasurement.objects.exclude(id__in=historical_ids)
        .select_related("box", "user")
        .order_by("created_at", "id")
    )
    for row in rows:
        print(
            " | ".join(
                [
                    str(row.id),
                    row.box.global_code,
                    row.measured_on.isoformat(),
                    row.created_at.isoformat(),
                    str(row.polyp_count),
                    str(row.ephyrae_count),
                    row.user.username if row.user else "-",
                ]
            )
        )

    print("=== HISTORICAL MEASUREMENT CHANGES ===")
    for log in AuditLog.objects.filter(action=AuditLog.Action.UPDATE).order_by("created_at"):
        measurement_id = log.metadata.get("measurement_id")
        if measurement_id not in historical_ids:
            continue
        print(
            " | ".join(
                [
                    str(measurement_id),
                    log.object_id,
                    log.created_at.isoformat(),
                    log.edited_at.isoformat() if log.edited_at else "-",
                    str(log.metadata.get("modifications", {})),
                ]
            )
        )

    print("=== BOX MOVEMENTS ===")
    for movement in BoxMovement.objects.select_related("box").order_by("moved_at"):
        print(
            f"{movement.id} | {movement.box.global_code} | {movement.moved_at.isoformat()} | "
            f"{movement.from_thermal_zone_id or '-'} -> {movement.to_thermal_zone_id}"
        )


if __name__ == "__main__":
    main()
