"""Transactional channel linking and physical probe replacement."""

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.audit.models import AuditLog
from apps.cultures.models import ThermalZone
from apps.organizations.models import Organization

from .models import Probe


def _snapshot(probe):
    return {"code": probe.code, "thermal_zone_id": probe.thermal_zone_id,
            "location": probe.location, "is_active": probe.is_active}


def _audit(probe, action, before, **metadata):
    AuditLog.objects.create(
        organization_id=probe.organization_id, user=None, action=action,
        object_type="probe", object_id=probe.code,
        description=f"iMinilide probe linked: {probe.code} -> {probe.thermal_zone.name}",
        metadata={"probe_id": probe.pk, "thermal_zone_id": probe.thermal_zone_id,
                  "external_serial": probe.external_serial, "external_channel": probe.external_channel,
                  "before": before, "source": "management_command", **metadata},
    )


@transaction.atomic
def link_probe(*, organization, zone, serial, channel, code, location="", replace=False):
    # Serialize even the first link, when no channel row exists to lock yet.
    Organization.objects.select_for_update().get(pk=organization.pk)
    if zone.organization_id != organization.pk:
        raise ValidationError("The destination zone must belong to this organization.")
    channel_probes = Probe.objects.filter(
        probe_type=Probe.ProbeType.IMINILIDE, external_serial=serial, external_channel=channel,
    )
    if channel_probes.exclude(organization=organization).exists():
        raise ValidationError("This logger channel is already linked in another organization.")
    zone_ids = set(channel_probes.values_list("thermal_zone_id", flat=True)) | {zone.pk}
    list(ThermalZone.objects.select_for_update().filter(pk__in=zone_ids).order_by("pk"))
    predecessors = list(channel_probes.select_for_update().order_by("pk"))
    active = next((probe for probe in predecessors if probe.is_active), None)
    previous = active or (predecessors[-1] if predecessors else None)
    if replace and previous is None:
        raise ValidationError("There is no existing probe to replace for this logger channel.")
    created = active is None or replace
    probe = Probe(organization=organization, thermal_zone=zone,
                  probe_type=Probe.ProbeType.IMINILIDE, external_serial=serial,
                  external_channel=channel) if created else active
    if Probe.objects.filter(organization=organization, code=code).exclude(pk=probe.pk).exists():
        raise ValidationError(f"Another probe already uses the code {code}. Use a new unique code for replacement.")
    before = None if created else _snapshot(probe)
    previous_before = _snapshot(previous) if replace else None
    if replace:
        previous.is_active = False
        previous.save(update_fields=["is_active"])
    probe.thermal_zone = zone
    probe.code = code
    probe.location = location
    probe.is_active = True
    probe.save()
    if replace:
        _audit(previous, AuditLog.Action.UPDATE, previous_before, replacement_probe_id=probe.pk)
    _audit(probe, AuditLog.Action.CREATION if created else AuditLog.Action.UPDATE, before,
           **({"replaced_probe_id": previous.pk} if replace else {}))
    return probe, created
