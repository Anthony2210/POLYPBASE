"""Read-only thermal evidence for one canonical Box and a finite UTC window.

Callers remain responsible for permissions; this internal adapter grants none.
All institutional zones are loaded, not only the Box's recorded locations, so a
snapshot can be reused for successive in-memory location drafts. No source is
combined with another, and no current target is a historical temperature.

The existing snapshot wrapper provides REPEATABLE READ, READ ONLY on PostgreSQL
only when it owns the outermost transaction. Nested calls inherit the caller's
isolation; SQLite has no equivalent guarantee. Independently, the two probe
storage tiers always share one SQL statement snapshot during condensation.
"""

from collections import defaultdict
from datetime import datetime, time, timedelta

from django.db.models import CharField, F, IntegerField, Q, Value

from apps.measurements.historical_temperature_identity import temperature_source_matches
from apps.measurements.historical_temperature_source import canonical_box
from apps.measurements.models import (
    DailyTemperature, HistoricalTemperatureEvidence, HistoricalTemperatureReading,
    Probe, TemperatureMeasurement,
)
from apps.measurements.probe_readings import overlapping_probe_buckets

from .effective_history import SourceFinding, SourceRef, TemporalPosition, ThermalSourceKind, project_history
from .effective_history_sources import HistorySourceNotFound, load_box_source_history
from .effective_temperature import ThermalEvidence, ThermalPreview, ThermalSourceHistory, project_temperature, utc
from .historical_location_transitions import (
    POLICY, _EVIDENCE_FIELDS, _READING_FIELDS, _approved_starting_evidence_ids,
    _before_cutoff, _observations, _zone_for, rule_applies,
)
from .location_history_diagnostics import _read_only_snapshot
from .models import MOVEMENT_WEEK_TIME_ZONE, Box, ThermalZone


def _midnight(day):
    return utc(datetime.combine(day, time.min, tzinfo=MOVEMENT_WEEK_TIME_ZONE))


def preview_box_temperature(*, organization_id, box_id, start, end, operations=(), anchors=()) -> ThermalPreview:
    """Run the standalone read/draft/thermal pipeline in one outer snapshot.

    Callers remain responsible for permissions; this helper grants none and is
    not wired into an endpoint. When it owns the PostgreSQL transaction, both
    loaders share one REPEATABLE READ, READ ONLY snapshot. Inside an existing
    transaction it inherits the caller's isolation and cannot guarantee a
    coherent cross-loader snapshot; SQLite has no equivalent guarantee.
    """
    with _read_only_snapshot():
        source = load_box_source_history(organization_id=organization_id, box_id=box_id)
        history = project_history(source, operations=operations, anchors=anchors)
        thermal = load_box_thermal_sources(organization_id=organization_id, box_id=box_id, start=start, end=end)
        return project_temperature(history, thermal)


def load_box_thermal_sources(*, organization_id, box_id, start, end) -> ThermalSourceHistory:
    """Load authorized original evidence, without applying a location draft."""
    start, end = utc(start), utc(end)
    if start >= end:
        raise ValueError("The thermal preview window must be nonempty.")
    with _read_only_snapshot():
        box = Box.objects.filter(
            pk=box_id, organization_id=organization_id, merged_into__isnull=True,
        ).first()
        if box is None:
            raise HistorySourceNotFound("No such Box in this institution.")
        zones = list(ThermalZone.objects.filter(organization_id=organization_id).order_by("pk").values(
            "id", "target_temperature_c",
        ))
        probes = dict(Probe.objects.filter(
            organization_id=organization_id, thermal_zone__organization_id=organization_id,
        ).order_by("pk").values_list("pk", "thermal_zone_id"))
        evidence = _probe_evidence(organization_id, probes, start, end)
        evidence.extend(_daily_evidence(organization_id, start, end))
        historical, findings = _historical_evidence(box, zones, start, end)
        evidence.extend(historical)
        return ThermalSourceHistory(
            organization_id=organization_id, box_id=box.pk, start=start, end=end,
            zone_ids=frozenset(zone["id"] for zone in zones), probe_ids=frozenset(probes),
            evidence=tuple(sorted(evidence, key=lambda item: (utc(item.temporal.earliest), item.key))),
            findings=tuple(findings),
        )


def _probe_rows(organization_id, probe_ids, start, end):
    """A sibling of _zone_series_rows retaining PKs, source and whole statistics.

    No grouping, averaging, rounding, row locks or separate tier queries. Each
    union column uses the bucket's field type, notably its wide exact sum.
    """
    if not probe_ids:
        return []
    scope = {
        "probe_id__in": probe_ids,
        "probe__organization_id": organization_id,
        "probe__thermal_zone__organization_id": organization_id,
    }
    buckets = overlapping_probe_buckets(start, end).filter(**scope).order_by().annotate(
        tier=Value("bucket", output_field=CharField()),
        source_basis=Value("stored_whole_bucket", output_field=CharField()),
    ).values_list(
        "pk", "probe_id", "tier", "period_start", "average_temperature_c", "min_temperature_c",
        "max_temperature_c", "temperature_sum_c", "measurement_count", "period_minutes",
        "last_measured_at", "last_temperature_c", "source_basis",
    )
    raw = TemperatureMeasurement.objects.filter(
        **scope, measured_at__gte=start, measured_at__lt=end,
    ).order_by().annotate(
        tier=Value("raw", output_field=CharField()),
        instant=F("measured_at"), mean=F("temperature_c"), low=F("temperature_c"),
        high=F("temperature_c"), total=F("temperature_c"),
        samples=Value(1, output_field=IntegerField()), minutes=Value(0, output_field=IntegerField()),
        last_at=F("measured_at"), last_value=F("temperature_c"), source_basis=F("source"),
    ).values_list(
        "pk", "probe_id", "tier", "instant", "mean", "low", "high", "total", "samples", "minutes",
        "last_at", "last_value", "source_basis",
    )
    return list(buckets.union(raw, all=True))


def _probe_evidence(organization_id, probes, start, end):
    evidence = []
    for (pk, probe_id, tier, instant, mean, low, high, total, count, minutes,
         last_at, last_value, basis) in _probe_rows(organization_id, tuple(probes), start, end):
        instant = utc(instant)
        is_raw = tier == "raw"
        source = "temperature_measurement" if is_raw else "probe_temperature_bucket"
        temporal = (TemporalPosition.exact(instant) if is_raw else
                    TemporalPosition.observation_interval(instant, instant + timedelta(minutes=minutes)))
        evidence.append(ThermalEvidence(
            key=f"{source}:{pk}", organization_id=organization_id, zone_id=probes[probe_id],
            source_kind=(ThermalSourceKind.RAW_PROBE_MEASUREMENT if is_raw else ThermalSourceKind.CONDENSED_BUCKET),
            temporal=temporal, temperature_c=mean, minimum_c=low, maximum_c=high,
            temperature_sum_c=total, measurement_count=count, probe_id=probe_id,
            last_measured_at=utc(last_at), last_temperature_c=last_value,
            association_basis="probe_recorded_zone",
            sources=(SourceRef(source, pk, "measurement" if is_raw else "whole_statistic", basis=basis),),
        ))
    return evidence


def _daily_evidence(organization_id, start, end):
    # Broad local-date selection followed by exact half-open overlap. In
    # particular an exclusive local midnight must not include the next day.
    days = DailyTemperature.objects.filter(
        thermal_zone__organization_id=organization_id,
        date__gte=start.astimezone(MOVEMENT_WEEK_TIME_ZONE).date(),
        date__lte=end.astimezone(MOVEMENT_WEEK_TIME_ZONE).date(),
    ).order_by("date", "pk")
    evidence = []
    for day in days:
        lower, upper = _midnight(day.date), _midnight(day.date + timedelta(days=1))
        if lower >= end or upper <= start:
            continue
        basis = "probe_derived" if day.computed_from_probes else "manual_or_mixed"
        evidence.append(ThermalEvidence(
            key=f"daily_temperature:{day.pk}", organization_id=organization_id, zone_id=day.thermal_zone_id,
            source_kind=ThermalSourceKind.MIXED_DAILY_AGGREGATE,
            temporal=TemporalPosition.observation_interval(lower, upper),
            temperature_c=day.average_temperature_c, minimum_c=day.min_temperature_c,
            maximum_c=day.max_temperature_c, measurement_count=day.measurement_count,
            daily_computed_from_probes=day.computed_from_probes, association_basis="daily_recorded_zone",
            sources=(SourceRef("daily_temperature", day.pk, "whole_statistic", basis=basis),),
        ))
    return evidence


def _historical_evidence(box, zones, start, end):
    first_year, first_week, _ = start.astimezone(MOVEMENT_WEEK_TIME_ZONE).isocalendar()
    last_year, last_week, _ = end.astimezone(MOVEMENT_WEEK_TIME_ZONE).isocalendar()
    readings = list(HistoricalTemperatureReading.objects.filter(
        biological_measurement__box_id=box.pk,
        biological_measurement__box__organization_id=box.organization_id,
        biological_measurement__box__merged_into__isnull=True,
    ).filter(
        Q(iso_year__gt=first_year) | Q(iso_year=first_year, iso_week__gte=first_week),
    ).filter(
        Q(iso_year__lt=last_year) | Q(iso_year=last_year, iso_week__lte=last_week),
    ).order_by("iso_year", "iso_week", "pk").values(*_READING_FIELDS))
    evidence, findings, eligible, positions = [], [], [], {}
    for reading in readings:
        try:
            position = TemporalPosition.week(reading["iso_year"], reading["iso_week"], MOVEMENT_WEEK_TIME_ZONE)
        except (ValueError, OverflowError):
            findings.append(SourceFinding("historical_setpoint_unestablished", {
                "reading_id": reading["id"], "reasons": ("observation_week_inconsistent",),
            }))
            continue
        if utc(position.not_before) >= end or utc(position.before) <= start:
            continue
        if not rule_applies(box.organization_id) or not _before_cutoff(reading):
            findings.append(SourceFinding("historical_setpoint_outside_approved_scope", {
                "reading_id": reading["id"], "policy": POLICY,
            }))
            continue
        positions[reading["id"]] = position
        eligible.append(reading)
    if not eligible:
        return evidence, findings
    sources = HistoricalTemperatureEvidence.objects.filter(
        reading_id__in=positions,
        reading__biological_measurement__box_id=box.pk,
        reading__biological_measurement__box__organization_id=box.organization_id,
    ).order_by("pk")
    by_reading, mismatched = defaultdict(list), set()
    for source in sources.values("id", "reading_id", *_EVIDENCE_FIELDS):
        by_reading[source["reading_id"]].append(source)
        if not temperature_source_matches(canonical_box(source["source_box"]), box.global_code):
            mismatched.add(source["id"])
    # Only persisted reviewed proof can exempt an existing identity mismatch;
    # this never creates an alias or extends the approved institution/cutoff.
    approved = _approved_starting_evidence_ids(mismatched, using=sources.db)
    by_temperature = defaultdict(list)
    for zone in zones:
        if zone["target_temperature_c"] is not None:
            by_temperature[zone["target_temperature_c"]].append(zone)
    for observation in _observations(eligible, by_reading, box.global_code, approved_evidence_ids=approved):
        reading = observation["reading"]
        reasons = set(observation["blockers"])
        zone = None
        if not reasons:
            zone, gap = _zone_for(observation["temperature"], by_temperature)
            if gap:
                reasons.add(gap)
        refs = (SourceRef("historical_temperature_reading", reading["id"], "observation", basis=POLICY),) + tuple(
            SourceRef("historical_temperature_evidence", source["id"], "source_evidence",
                      basis="persisted_reviewed_proof" if source["id"] in approved else "original_source")
            for source in observation["evidences"]
        )
        if reasons:
            findings.append(SourceFinding("historical_setpoint_unestablished", {
                "reading_id": reading["id"], "evidence_ids": tuple(source["id"] for source in observation["evidences"]),
                "temperature_c": reading["temperature_c"],
                "source_values": tuple(source["source_value"] for source in observation["evidences"]),
                "reasons": tuple(sorted(reasons)),
            }))
        evidence.append(ThermalEvidence(
            key=f"historical_temperature_reading:{reading['id']}", organization_id=box.organization_id,
            box_id=box.pk, zone_id=zone["id"] if zone is not None else None,
            source_kind=ThermalSourceKind.HISTORICAL_SETPOINT, temporal=positions[reading["id"]],
            temperature_c=observation["temperature"],
            association_basis=f"{POLICY}:unique_current_target_mapping" if zone is not None else POLICY,
            sources=refs, established=not reasons, uncertainty_reasons=tuple(sorted(reasons)),
        ))
    return evidence, findings
