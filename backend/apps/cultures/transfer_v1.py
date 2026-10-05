"""Legacy single-row transfers; callers authorize the active organization first."""

import re

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.taxonomy.models import Species, Strain

from .models import Box, BoxLocation, BoxTransfer, BoxTransferImport
from .box_codes import allocate_box_codes, locked_namespace


class TransferV1ValidationError(Exception):
    """A business validation failure with the existing v1 response detail."""

    def __init__(self, detail):
        self.detail = detail
        super().__init__(str(detail))


class StrainOwnershipError(Exception):
    """The legacy species/code pair is foreign-owned or unowned."""


def _reject_replay(source):
    if BoxTransferImport.objects.filter(
        format_version=source["format"],
        source_organization_name=source["source_organization_name"],
        source_transfer_id=str(source["transfer_id"]),
    ).exists():
        raise TransferV1ValidationError("Ce transfert a déjà été importé.")


def _is_replay_constraint_error(error):
    diagnostic = getattr(error.__cause__, "diag", None)
    if diagnostic is not None:
        return diagnostic.constraint_name == "unique_imported_box_transfer"
    # SQLite reports columns instead of the constraint name. Match only this key.
    table = BoxTransferImport._meta.db_table
    columns = ("format_version", "source_organization_name", "source_transfer_id")
    return str(error) == "UNIQUE constraint failed: " + ", ".join(
        f"{table}.{column}" for column in columns
    )


def _next_unique_box_identity(strain, *, reserve=True):
    """Use the shared Box allocator, including concurrent namespace first use."""
    if reserve:
        return allocate_box_codes(strain.code, 1)[0]
    row = locked_namespace(strain.code)
    number = str(row.high_water + 1).zfill(3)
    return f"{strain.code}.{number}", number


@transaction.atomic
def prepare_transfer_v1(*, box, to_organization, polyp_count, user, **transfer_fields):
    """Persist a validated preparation and its mandatory source audit together."""
    transfer = BoxTransfer.objects.create(
        box=box,
        from_organization=box.organization,
        to_organization=to_organization,
        polyp_count=polyp_count,
        user=user,
        **transfer_fields,
    )
    AuditLog.objects.create(
        organization=box.organization,
        user=user,
        action=AuditLog.Action.TRANSFER,
        object_type="box",
        object_id=box.global_code,
        description=f"Box transfer prepared: {box.global_code}",
        metadata={
            "transfer_id": transfer.id,
            "box_id": box.id,
            "code_global": box.global_code,
            "to_organization": transfer.to_organization.name,
            "date": transfer.transfer_date.isoformat(),
            "polypes": transfer.polyp_count,
            "note": transfer.notes,
        },
    )
    return transfer


def import_transfer_v1(*, source, organization, zone, user, global_code=""):
    """Accept one validated v1 row into an authorized destination and active zone.

    Roll back the whole operation before translating the legacy replay constraint.
    Other integrity errors must remain visible, not masquerade as duplicate imports.
    """
    try:
        with transaction.atomic():
            return _import_transfer_v1(
                source=source, organization=organization, zone=zone,
                user=user, global_code=global_code,
            )
    except IntegrityError as error:
        if _is_replay_constraint_error(error):
            raise TransferV1ValidationError("Ce transfert a déjà été importé.") from error
        raise


def _import_transfer_v1(*, source, organization, zone, user, global_code):
    _reject_replay(source)
    try:
        polyp_count = int(source["transferred_polyp_count"])
    except (TypeError, ValueError) as error:
        raise TransferV1ValidationError({"source_data": "Le nombre de polypes est invalide."}) from error
    if polyp_count < 1:
        raise TransferV1ValidationError({"source_data": "Le nombre de polypes doit être positif."})

    species, _ = Species.objects.get_or_create(
        scientific_name=str(source["species_scientific_name"]).strip(),
        defaults={
            "common_name": str(source.get("species_common_name", "")).strip(),
            "genus_species_code": str(source.get("species_code", "")).strip(),
        },
    )
    # Serialize imports for this species even when the strain does not exist yet.
    Species.objects.select_for_update().get(pk=species.pk)
    # An identical import may have committed while this request waited for the lock.
    _reject_replay(source)
    strain_code = str(source["strain_code"]).strip()
    strains = Strain.objects.filter(species=species, code=strain_code)
    if strains.exclude(organization=organization).exists():
        raise StrainOwnershipError()
    strain, _ = Strain.objects.get_or_create(
        species=species,
        code=strain_code,
        organization=organization,
        defaults={"origin_code": str(source.get("strain_origin_code", "")).strip()},
    )
    requested_global_code = str(global_code).strip()
    suggested_global_code, suggested_box_number = _next_unique_box_identity(strain, reserve=not requested_global_code)
    if requested_global_code:
        code_match = re.fullmatch(rf"{re.escape(strain.code)}\.(\d+)", requested_global_code)
        if not code_match:
            raise TransferV1ValidationError({
                "global_code": (
                    f"Le code doit commencer par {strain.code}. et finir par un numéro. "
                    f"Suggestion : {suggested_global_code}"
                )
            })
        if Box.objects.filter(global_code=requested_global_code).exists():
            raise TransferV1ValidationError({
                "global_code": f"Ce code existe déjà. Suggestion : {suggested_global_code}"
            })
        global_code = requested_global_code
        box_number = code_match.group(1)
    else:
        global_code, box_number = suggested_global_code, suggested_box_number
    box = Box.objects.create(
        organization=organization,
        global_code=global_code,
        local_code="",
        box_number=box_number,
        strain=strain,
        thermal_zone=zone,
        entered_on=timezone.localdate(),
        notes=(
            f"Import du transfert {source['transfer_id']} depuis "
            f"{source['source_organization_name']} (boîte source {source['source_global_code']})."
        ),
    )
    BoxLocation.objects.create(box=box, thermal_zone=zone, starts_at=timezone.now())
    BiologicalMeasurement.objects.create(
        box=box,
        measured_on=timezone.localdate(),
        polyp_count=polyp_count,
        ephyrae_count=0,
        culture_status=str(source.get("latest_culture_status") or "not_specified"),
        notes="Nombre initial reçu lors du transfert.",
        user=user,
    )
    transfer_import = BoxTransferImport.objects.create(
        format_version=source["format"],
        source_transfer_id=str(source["transfer_id"]),
        source_organization_name=str(source["source_organization_name"]),
        source_global_code=str(source["source_global_code"]),
        destination_organization=organization,
        created_box=box,
        imported_by=user,
        source_data=source,
    )
    AuditLog.objects.create(
        organization=organization,
        user=user,
        action=AuditLog.Action.IMPORT,
        object_type="box",
        object_id=box.global_code,
        description=f"Transfer imported from {source['source_organization_name']}",
        metadata={
            "transfer_import_id": transfer_import.id,
            "source_transfer_id": source["transfer_id"],
            "source_global_code": source["source_global_code"],
            "source_organization": source["source_organization_name"],
            "created_box_id": box.id,
        },
    )
    return box
