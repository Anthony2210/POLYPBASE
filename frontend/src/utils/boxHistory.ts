import type { Translator } from '../i18n';
import type { BoxLocation, ReadableUserIdentity } from '../types';
import type { TrendEvent, TrendLocation } from '../components/BiologicalTrendChart';

export type HistoryEvent = {
  id: string;
  kind: 'ordinary' | 'historical_reconstruction' | 'deactivation' | 'reactivation' | 'initial_location' | 'qualification' | 'unclassified' | 'location_evidence';
  temporal_precision: 'exact' | 'iso_week' | 'unresolved';
  occurred_at: string | null;
  iso_year: number | null;
  iso_week: number | null;
  from_thermal_zone: { id: number; name: string } | null;
  to_thermal_zone: { id: number; name: string } | null;
  actor: ReadableUserIdentity | null;
  provenance: string;
  evidence: Record<string, unknown>;
  reconciliation_status: string;
  recorded_movement_id: number | null;
  can_correct: boolean;
  notes: string;
};

export function historyKindLabel(event: HistoryEvent, t: Translator): string {
  const keys = {
    ordinary: 'movementKindOrdinary', historical_reconstruction: 'historyReconstruction',
    deactivation: 'historyDeactivation', reactivation: 'movementKindReactivation',
    initial_location: 'movementKindInitialLocation', qualification: 'movementKindQualification',
    unclassified: 'movementKindUnclassified', location_evidence: 'historyLocationEvidence',
  } as const;
  return t(keys[event.kind]);
}

export function historyWeekLabel(event: Pick<HistoryEvent, 'iso_year' | 'iso_week'>, t: Translator) {
  return t('historyIsoWeek').replace('{year}', String(event.iso_year ?? '')).replace('{week}', String(event.iso_week ?? ''));
}

// These dates bound an ISO week on the daily axis; neither is an occurrence time.
export function historyWeekInterval(year: number, week: number) {
  const monday = new Date(Date.UTC(year, 0, 4));
  monday.setUTCDate(monday.getUTCDate() - (monday.getUTCDay() + 6) % 7 + (week - 1) * 7);
  const sunday = new Date(monday);
  sunday.setUTCDate(sunday.getUTCDate() + 6);
  return { start: monday.toISOString().slice(0, 10), end: sunday.toISOString().slice(0, 10) };
}

export function localHistoryDay(value: string) {
  if (value.length === 10) return value;
  const date = new Date(value);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function zonedInstant(value: unknown) {
  return typeof value === 'string' && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? Date.parse(value) : NaN;
}

function exactInstant(event: HistoryEvent) {
  return event.temporal_precision === 'exact' ? zonedInstant(event.occurred_at) : NaN;
}

type InactiveInterval = {
  deactivation_event_id: string; reactivation_event_id: string;
  previous_location_id: number; next_location_id: number; reactivation_movement_id: number;
  starts_at: string; ends_at: string; duration_seconds: number;
  measurement_overlap_possible: false; masking_eligible: true;
};

function inactiveInterval(event: HistoryEvent): InactiveInterval | null {
  const value = event.evidence.inactive_interval as Partial<InactiveInterval> | null;
  if (event.provenance !== 'lifecycle_audit' || !['recorded', 'paired'].includes(event.reconciliation_status)
      || event.evidence.inactive_pairing_reliable !== true
    || !value || typeof value !== 'object' || value.masking_eligible !== true
    || value.measurement_overlap_possible !== false
    || typeof value.deactivation_event_id !== 'string' || typeof value.reactivation_event_id !== 'string'
    || ![value.previous_location_id, value.next_location_id, value.reactivation_movement_id]
      .every(id => typeof id === 'number' && Number.isSafeInteger(id) && id > 0)
    || typeof value.duration_seconds !== 'number' || !Number.isFinite(value.duration_seconds)
    || !Number.isFinite(zonedInstant(value.starts_at)) || !Number.isFinite(zonedInstant(value.ends_at))) return null;
  return value as InactiveInterval;
}

export type HistoryCandidate = {
  id: string; box_id: number; iso_year: number; iso_week: number;
  from_thermal_zone_id: number | null; to_thermal_zone_id: number | null;
  evidence: Record<string, unknown>; blockers: string[]; reconciliation_status: string;
  classification?: string;
};

export type HistoryAssessment = {
  id: string; box_id: number; classification: string; reconciliation_status: string;
  blockers: string[]; evidence: Record<string, unknown>;
  iso_year?: number; iso_week?: number;
  from_thermal_zone_id?: number | null; to_thermal_zone_id?: number | null;
};

export function historyBlockerLabel(reason: string, t: Translator) {
  const keys = {
    source_location_provenance_unresolved: 'historyReasonSource', future_period_boundary: 'historyReasonFuture',
    whole_week_period_evidence_required: 'historyReasonWholeWeek', invalid_or_foreign_period: 'historyReasonInvalid',
    period_continuity_unresolved: 'historyReasonContinuity', adjacent_measurement_evidence_missing: 'historyReasonRecords',
    origin_measurement_outside_period: 'historyReasonOrigin', destination_period_does_not_cover_week: 'historyReasonDestination',
    recorded_event_in_week: 'historyReasonMovement', lifecycle_event_in_week: 'historyReasonLifecycle',
    historical_event_in_week: 'historyReasonHistorical', multiple_period_transitions_in_week: 'historyReasonMultiple',
    measurement_environment_attribution_unresolved: 'historyReasonEnvironment', measurement_week_inconsistent: 'historyReasonRecordWeek',
    recorded_period_link_mismatch: 'historyReasonPeriodLink', multiple_recorded_representations: 'historyReasonRepresentations',
    historical_receipt_evidence_changed: 'historyReasonReceiptChanged', historical_receipt_audit_unresolved: 'historyReasonReceiptAudit',
    historical_and_recorded_event_coexist: 'historyReasonCoexist', location_history_missing: 'historyReasonMissingLocations',
  } as const;
  return t(keys[reason as keyof typeof keys] ?? 'historyReasonUnknown');
}

function isMovementLifecycleAudit(event: HistoryEvent) {
  return event.provenance === 'lifecycle_audit' && ['reactivation', 'qualification'].includes(event.kind);
}

// Count all explicit claims before validating them: a contradictory claim must
// not let another audit certify the same movement by elimination.
function uniqueLifecyclePairs(events: readonly HistoryEvent[]) {
  const pairs = new Map<HistoryEvent, HistoryEvent>();
  for (const audit of events.filter(isMovementLifecycleAudit)) {
    const id = audit.evidence.paired_movement_id;
    if (typeof id !== 'number' || !Number.isSafeInteger(id) || id <= 0) continue;
    const claims = events.filter(event => event.provenance === 'lifecycle_audit'
      && event.evidence.paired_movement_id === id);
    const moves = events.filter(event => event.provenance === 'box_movement' && event.recorded_movement_id === id);
    if (claims.length !== 1 || moves.length !== 1) continue;
    const move = moves[0];
    if (events.filter(event => event.id === audit.id).length !== 1
      || events.filter(event => event.id === move.id).length !== 1
      || audit.evidence.lifecycle_pairing_reliable !== true
      || !['recorded', 'paired'].includes(audit.reconciliation_status)
      || move.reconciliation_status !== 'recorded' || move.kind !== audit.kind
      || !Number.isFinite(exactInstant(audit)) || exactInstant(move) !== exactInstant(audit)) continue;
    pairs.set(audit, move);
  }
  return pairs;
}

function lifecyclePresentation(event: HistoryEvent, pairs: Map<HistoryEvent, HistoryEvent>): HistoryEvent {
  if (!isMovementLifecycleAudit(event) || pairs.has(event)) return event;
  // This is a display projection only. Keep the entire original source,
  // including any claimed occurrence, without certifying it on the timeline.
  return { ...event, temporal_precision: 'unresolved', occurred_at: null, iso_year: null, iso_week: null,
    reconciliation_status: 'unresolved', evidence: { ...event.evidence, source_event: event } };
}

export function historyTimelineEntries(
  events: readonly HistoryEvent[], candidates: readonly HistoryCandidate[], locations: readonly BoxLocation[],
  assessments: readonly HistoryAssessment[] = [],
) {
  const zone = (id: number | null | undefined) => locations.find(location => location.thermal_zone.id === id)?.thermal_zone ?? null;
  const pairs = uniqueLifecyclePairs(events);
  const pairedAudits = new Map([...pairs].map(([audit, movement]) => [movement, audit]));
  // Collapse only a unique explicit pair; preserve the recorded movement and
  // the full audit source, including correction capabilities and evidence.
  const rows: HistoryEvent[] = events.filter(event => !pairs.has(event)).map(event => {
    const audit = pairedAudits.get(event);
    return audit ? { ...event, evidence: { ...event.evidence, paired_lifecycle_events: [audit] } }
      : lifecyclePresentation(event, pairs);
  });
  const diagnostic = (item: HistoryCandidate | HistoryAssessment, provenance: string): HistoryEvent => ({
    id: `${provenance}:${item.id}`, kind: 'location_evidence', temporal_precision: 'unresolved',
    occurred_at: null, iso_year: null, iso_week: null,
    from_thermal_zone: zone(item.from_thermal_zone_id), to_thermal_zone: zone(item.to_thermal_zone_id),
    actor: null, provenance, evidence: { ...item.evidence, blockers: item.blockers,
      classification: item.classification, proposed_iso_year: item.iso_year ?? null, proposed_iso_week: item.iso_week ?? null },
    reconciliation_status: item.reconciliation_status, recorded_movement_id: null, can_correct: false, notes: '',
  });
  // Candidates describe reconciliation work, never an additional physical event.
  for (const candidate of candidates) {
    if (['review_required', 'blocked'].includes(candidate.reconciliation_status))
      rows.push(diagnostic(candidate, 'transition_candidate'));
  }
  for (const assessment of assessments) {
    if (assessment.classification === 'initial_assignment') {
      const index = rows.findIndex(event => event.kind === 'location_evidence'
        && event.evidence.location_id === assessment.evidence.location_id && event.evidence.boundary === 'start');
      if (index !== -1) {
        rows[index] = { ...rows[index], evidence: { ...rows[index].evidence, assessment } };
        continue;
      }
    }
    rows.push(diagnostic(assessment, 'transition_assessment'));
  }
  // Group by the movement calendar's ISO week (Paris). A week-only row is a
  // group-level item, not an event occurring at its Monday boundary.
  const group = (event: HistoryEvent) => {
    if (event.temporal_precision === 'iso_week' && event.iso_year != null && event.iso_week != null)
      return historyWeekInterval(event.iso_year, event.iso_week).start;
    if (!Number.isFinite(exactInstant(event))) return '';
    const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Paris', year: 'numeric', month: '2-digit', day: '2-digit' })
      .formatToParts(new Date(event.occurred_at!));
    const part = (type: string) => parts.find(value => value.type === type)!.value;
    const monday = new Date(`${part('year')}-${part('month')}-${part('day')}T00:00:00Z`);
    monday.setUTCDate(monday.getUTCDate() - (monday.getUTCDay() + 6) % 7);
    return monday.toISOString().slice(0, 10);
  };
  return rows.sort((a, b) => group(b).localeCompare(group(a))
    || Number(a.temporal_precision === 'exact') - Number(b.temporal_precision === 'exact')
    || (Number.isFinite(exactInstant(a)) && Number.isFinite(exactInstant(b)) ? exactInstant(b) - exactInstant(a) : 0)
    || a.id.localeCompare(b.id));
}

function measurementCouldOverlap(date: string, start: number, end: number) {
  if (date.length !== 10) {
    const instant = Date.parse(date);
    return !Number.isFinite(instant) || (instant >= start && instant <= end);
  }
  // Biological records only prove a day, not an instant. Include either possible
  // UTC or browser-local day instead of assuming a midnight measurement.
  const localStart = new Date(`${date}T00:00:00`).getTime();
  const localEnd = new Date(`${date}T00:00:00`);
  localEnd.setDate(localEnd.getDate() + 1);
  const utcStart = Date.parse(`${date}T00:00:00Z`);
  return !Number.isFinite(localStart) || (Math.min(localStart, utcStart) <= end
    && Math.max(localEnd.getTime(), utcStart + 86400000) > start);
}

export function projectBoxHistory(
  events: readonly HistoryEvent[], locations: readonly BoxLocation[],
  measurementDates: readonly string[], t: Translator,
): { events: TrendEvent[]; locations: TrendLocation[]; maskedLocationIds: Set<number> } {
  // Active location bands are scientific history, never a masking target.
  const maskedLocationIds = new Set<number>();
  const maskedEventIds = new Set<string>();
  const pairs = uniqueLifecyclePairs(events);
  for (const stopped of events) {
    const interval = inactiveInterval(stopped);
    if (stopped.kind !== 'deactivation' || !interval || stopped.id !== interval.deactivation_event_id) continue;
    const resumedMatches = events.filter(event => event.id === interval.reactivation_event_id);
    if (events.filter(event => event.id === stopped.id).length !== 1 || resumedMatches.length !== 1) continue;
    const resumed = resumedMatches[0];
    const reciprocal = inactiveInterval(resumed);
    if (resumed.kind !== 'reactivation' || !reciprocal
      || Object.keys(interval).some(key => interval[key as keyof InactiveInterval] !== reciprocal[key as keyof InactiveInterval])) continue;
    const start = zonedInstant(interval.starts_at), end = zonedInstant(interval.ends_at);
    if (!(end > start && end - start < 86400000)
      || interval.duration_seconds !== (end - start) / 1000
      || exactInstant(stopped) !== start || exactInstant(resumed) !== end) continue;
    const previous = locations.filter(location => location.id === interval.previous_location_id);
    const next = locations.filter(location => location.id === interval.next_location_id);
    if (previous.length !== 1 || next.length !== 1 || previous[0].end_date_unknown || next[0].end_date_unknown
      || zonedInstant(previous[0].ends_at) !== start || zonedInstant(next[0].starts_at) !== end) continue;
    const moves = events.filter(event => event.provenance === 'box_movement'
      && event.recorded_movement_id === interval.reactivation_movement_id);
    if (moves.length !== 1 || moves[0].kind !== 'reactivation' || moves[0].reconciliation_status !== 'recorded'
          || exactInstant(moves[0]) !== end
      || pairs.get(resumed) !== moves[0]) continue;
    // Backend eligibility includes exact-date and legacy-week measurement precision.
    // This optional local guard can only retain more markers, never hide more.
    if (measurementDates.some(date => measurementCouldOverlap(date, start, end))) continue;
    [stopped, resumed, moves[0]].forEach(event => maskedEventIds.add(event.id));
  }
  const chartEvents: TrendEvent[] = [];
  for (const source of events) {
    if (maskedEventIds.has(source.id) || pairs.has(source)) continue;
    const event = lifecyclePresentation(source, pairs);
    const detail = [event.from_thermal_zone?.name, event.to_thermal_zone?.name].filter(Boolean).join(' → ');
    const title = historyKindLabel(event, t);
    if (event.kind === 'historical_reconstruction' && event.reconciliation_status === 'accepted' && event.temporal_precision === 'iso_week'
      && event.iso_year != null && event.iso_week != null) {
      const interval = historyWeekInterval(event.iso_year, event.iso_week);
      chartEvents.push({ id: event.id, kind: 'historical_reconstruction', date: interval.start,
        intervalEnd: interval.end, temporalLabel: historyWeekLabel(event, t), title, detail,
        detailLines: [{ label: historyWeekLabel(event, t), value: detail },
          { label: t('historyPrecision'), value: t('historyWeekOnly') }] });
    } else if (['ordinary', 'deactivation', 'reactivation'].includes(event.kind) && Number.isFinite(exactInstant(event))) {

      chartEvents.push({ id: event.id, date: localHistoryDay(event.occurred_at!), instant: event.occurred_at!,
        title, detail, kind: event.kind === 'ordinary' ? 'movement' : event.kind as 'deactivation' | 'reactivation' });
    }
  }
  return { events: chartEvents, maskedLocationIds, locations: locations.map(location => ({ id: location.id, name: location.thermal_zone.name, startsAt: location.starts_at,
      endsAt: location.ends_at, endDateUnknown: location.end_date_unknown })) };
}
