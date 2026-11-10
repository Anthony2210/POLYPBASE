import type { Language, Translator } from '../i18n';
import type { BoxLocation, ReadableUserIdentity } from '../types';
import type { TrendEvent, TrendLocation } from '../components/BiologicalTrendChart';

export type HistoryEvent = {
  id: string;
  kind: 'ordinary' | 'historical_reconstruction' | 'deactivation' | 'reactivation' | 'initial_location' | 'qualification' | 'unclassified' | 'location_evidence' | 'historical_transition';
  temporal_precision: 'exact' | 'iso_week' | 'unresolved' | 'observation_interval';
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
    ordinary: 'movementKindOrdinary', historical_reconstruction: 'movementKindOrdinary',
    deactivation: 'historyDeactivation', reactivation: 'historyReactivation',
    initial_location: 'movementKindInitialLocation', qualification: 'movementKindQualification',
    unclassified: 'movementKindUnclassified', location_evidence: 'historyLocationEvidence',
    historical_transition: 'historyHistoricalMovement',
  } as const;
  if (event.kind === 'deactivation' || event.kind === 'reactivation') return t(keys[event.kind]);
  return event.provenance === 'box_movement' && event.evidence.recorded_via_polypbase === true
    ? t('historyRecordedViaPolypbase') : t(keys[event.kind]);
}

export function historyLocationLabel(event: HistoryEvent, t: Translator): string {
  if (event.kind === 'deactivation') return event.from_thermal_zone
    ? t('historyLocationExit').replace('{zone}', event.from_thermal_zone.name) : '';
  if (event.kind === 'reactivation') return event.to_thermal_zone
    ? t('historyLocationEntry').replace('{zone}', event.to_thermal_zone.name) : '';
  return [event.from_thermal_zone?.name, event.to_thermal_zone?.name].filter(Boolean).join(' → ');
}

export type StartingLocation = {
  thermal_zone: { id: number; name: string };
  represented_movement_id?: number | null;
} & (
  | { observed_on: string; temporal_precision?: 'exact_date' }
  | { observed_on: null; temporal_precision: 'iso_week'; iso_year: number; iso_week: number }
);

export function isStartingLocation(value: unknown): value is StartingLocation {
  if (value == null || typeof value !== 'object') return false;
  const initial = value as Partial<StartingLocation>;
  const exact = typeof initial.observed_on === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(initial.observed_on)
    && (initial.temporal_precision == null || initial.temporal_precision === 'exact_date');
  const week = initial.observed_on === null && initial.temporal_precision === 'iso_week'
    && validHistoryWeekInterval(initial.iso_year, initial.iso_week) !== null;
  return (exact || week) && initial.thermal_zone != null && Number.isSafeInteger(initial.thermal_zone.id)
    && initial.thermal_zone.id > 0 && typeof initial.thermal_zone.name === 'string'
    && (initial.represented_movement_id == null || Number.isSafeInteger(initial.represented_movement_id)
      && initial.represented_movement_id > 0);
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

function validHistoryWeekInterval(year: unknown, week: unknown) {
  if (typeof year !== 'number' || typeof week !== 'number' || !Number.isInteger(year) || !Number.isInteger(week)
    || year < 100 || year > 9998 || week < 1 || week > 53) return null;
  const interval = historyWeekInterval(year, week);
  return interval.start < historyWeekInterval(year + 1, 1).start ? interval : null;
}

export function establishedHistoryWeek(event: HistoryEvent) {
  if (event.kind !== 'historical_reconstruction' || event.reconciliation_status !== 'accepted'
    || event.temporal_precision !== 'iso_week') return null;
  return validHistoryWeekInterval(event.iso_year, event.iso_week);
}

export function historyReconstructionNote(event: HistoryEvent, language: Language, t: Translator) {
  const interval = establishedHistoryWeek(event);
  if (!interval) return t('historyReconstructionUnresolved');
  const start = new Date(`${interval.start}T00:00:00Z`), end = new Date(`${interval.end}T00:00:00Z`);
  const sameYear = start.getUTCFullYear() === end.getUTCFullYear();
  const sameMonth = sameYear && start.getUTCMonth() === end.getUTCMonth();
  const locale = language === 'fr' ? 'fr-FR' : language === 'ja' ? 'ja-JP' : 'en-GB';
  const format = (date: Date, month: boolean, year: boolean) => new Intl.DateTimeFormat(locale, {
    timeZone: 'UTC', day: 'numeric', ...(month ? { month: 'long' as const } : {}), ...(year ? { year: 'numeric' as const } : {}),
  }).format(date);
  const range = `${format(start, !sameMonth, !sameYear)} ${language === 'fr' ? 'au' : 'to'} ${format(end, true, true)}`;
  return `${t('historyAcceptedReconstruction')} ${range}.`;
}

export function isUnconfirmedHistoryTransition(event: HistoryEvent) {
  if (!['blocked', 'review_required'].includes(event.reconciliation_status)) return false;
  const ids = event.evidence.represented_movement_ids;
  if (!Array.isArray(ids) || ids.length > 0) return false;
  // A recorded movement linked to these periods may be this very transition.
  const mismatched = event.evidence.mismatched_movement_ids;
  if (Array.isArray(mismatched) && mismatched.length > 0) return false;
  if (event.provenance === 'transition_candidate') return true;
  const origin = event.evidence.origin_location_id, destination = event.evidence.destination_location_id;
  return event.provenance === 'transition_assessment' && event.evidence.classification !== 'initial_assignment'
    && typeof origin === 'number' && Number.isSafeInteger(origin) && origin > 0
    && typeof destination === 'number' && Number.isSafeInteger(destination) && destination > 0 && origin !== destination;
}

// Backend blockers that leave the origin record and its precedence over the
// stored boundary intact. Any other or unknown blocker removes the order claim.
const ORIGIN_RECORD_PRESERVING_BLOCKERS = new Set<unknown>([
  'adjacent_measurement_evidence_missing', 'whole_week_period_evidence_required',
  'destination_period_does_not_cover_week', 'recorded_event_in_week', 'lifecycle_event_in_week',
]);

function parisDateTime(value: string) {
  return new Date(value).toLocaleString('sv-SE', { timeZone: 'Europe/Paris', hourCycle: 'h23' });
}

// attributed_week_start alone is not proof: the backend also sets it when the
// origin record is missing. Only measurement_weeks shows that the record exists.
export function potentialTransitionOriginWeek(event: HistoryEvent, locations: readonly BoxLocation[]) {
  const { proposed_iso_year: year, proposed_iso_week: week, attributed_week_start: weekStart,
    measurement_weeks: weeks, blockers, origin_location_id: originId } = event.evidence;
  const interval = validHistoryWeekInterval(year, week);
  if (!interval || weekStart !== interval.start || !Array.isArray(weeks) || !weeks.includes(weekStart)
    || !Array.isArray(blockers) || !blockers.every(reason => ORIGIN_RECORD_PRESERVING_BLOCKERS.has(reason))) return null;
  // The record only belongs to the origin if that period covers its whole week.
  const origins = locations.filter(location => location.id === originId);
  if (origins.length !== 1 || !(parisDateTime(origins[0].starts_at) <= `${interval.start} 00:00:00`)) return null;
  return { year: year as number, week: week as number };
}

export function potentialTransitionNote(event: HistoryEvent, locations: readonly BoxLocation[], t: Translator) {
  const origin = potentialTransitionOriginWeek(event, locations);
  return origin
    ? t('historyPotentialAfterRecord').replace('{week}', String(origin.week)).replace('{year}', String(origin.year))
    : t('historyPotentialUnordered');
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

export type HistoricalObservation = {
  reading_id: number; biological_measurement_id: number; iso_year: number; iso_week: number; week_start: string;
  environment_temperature_c: string; association_status: string; sources: Record<string, unknown>[];
};

export type HistoricalTransition = {
  id: string; box_id: number; status: 'established' | 'environment_only' | 'already_represented' | 'review';
  from_environment_c: string; to_environment_c: string;
  from_thermal_zone: { id: number; name: string } | null; to_thermal_zone: { id: number; name: string } | null;
  origin_observation: HistoricalObservation; destination_observation: HistoricalObservation;
  representation: { kind: string; event_id?: string } | null; blockers: string[];
  planner_candidate_ids?: string[]; adds_location_history: boolean;
};

export function historicalTransitionOf(event: HistoryEvent) {
  return event.kind === 'historical_transition' ? event.evidence.transition as HistoricalTransition : null;
}

// Aquarium de Paris rule: a historical temperature change is a location change.
// A recorded movement that represents it, or that lies in the same interval with
// another route, stays the only movement shown. Readings outside the confirmed
// canonical series are not plotted, so they add no reconstructed movement.
const RECORDED_MOVEMENT_BLOCKERS = new Set(['conflicting_recorded_movement', 'multiple_recorded_candidates', 'recorded_location_data_not_comparable']);
const INVALID_MOVEMENT_IDENTITY = new Set(['observation_environment_from_other_box', 'source_box_identity_mismatch',
  'source_box_identity_unavailable', 'observation_week_inconsistent', 'observations_conflict_in_week']);

export function reconstructedHistoricalMovements(transitions: readonly HistoricalTransition[]) {
  return transitions.filter(transition => transition.status !== 'already_represented'
    && !transition.representation?.event_id
    && transition.origin_observation?.association_status === 'confirmed'
    && transition.destination_observation?.association_status === 'confirmed'
    && !transition.blockers.some(reason => RECORDED_MOVEMENT_BLOCKERS.has(reason) || INVALID_MOVEMENT_IDENTITY.has(reason)
          || reason === 'observation_measurement_not_canonical'));
}

function supportsRepresentedTemperatureStep(transition: HistoricalTransition) {
  // An explicit owner can survive source validation review, but not unresolved
  // identity, environment, canonicality or a blocker we do not understand.
  if (!transition.blockers.every(reason => reason === 'source_not_validated')) return false;
  const observations = [transition.origin_observation, transition.destination_observation];
  if (![transition.from_environment_c, transition.to_environment_c, ...observations.map(item => item?.environment_temperature_c)]
    .every(value => typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value)))) return false;
  if (!observations.every(observation => observation?.association_status === 'confirmed'
    && Number.isSafeInteger(observation.reading_id) && observation.reading_id > 0
    && Number.isSafeInteger(observation.biological_measurement_id) && observation.biological_measurement_id > 0
    && validHistoryWeekInterval(observation.iso_year, observation.iso_week)?.start === observation.week_start)) return false;
  return observations[0].reading_id !== observations[1].reading_id
    && observations[0].biological_measurement_id !== observations[1].biological_measurement_id
    && observations[0].week_start < observations[1].week_start;
}

export function historicalTransitionRoute(transition: HistoricalTransition) {
  return transition.from_thermal_zone && transition.to_thermal_zone
    ? `${transition.from_thermal_zone.name} → ${transition.to_thermal_zone.name}`
    : `${transition.from_environment_c} °C → ${transition.to_environment_c} °C`;
}

// Chart position only: between the origin week and the destination week, so the
// plotted temperature changes after the marker. Never displayed as a date.
export function historicalMovementMarkerDate(transition: HistoricalTransition) {
  const day = (value: string) => Date.parse(`${value}T00:00:00Z`);
  const originEnd = day(transition.origin_observation.week_start) + 7 * 86400000;
  const destination = day(transition.destination_observation.week_start);
  const days = Math.max(0, Math.floor((destination - originEnd) / 86400000 / 2));
  return new Date(originEnd + days * 86400000).toISOString().slice(0, 10);
}

export type HistoricalWeekInterval = { from_iso_year: number; from_iso_week: number; to_iso_year: number; to_iso_week: number };

function fillInterval(template: string, interval: HistoricalWeekInterval) {
  return template.replace('{from}', String(interval.from_iso_week)).replace('{to}', String(interval.to_iso_week))
    .replace('{year}', String(interval.to_iso_year)).replace('{fromYear}', String(interval.from_iso_year))
    .replace('{toYear}', String(interval.to_iso_year));
}

// Weeks come from the two source observations; the movement itself has no known date.
export function historicalTransitionInterval(transition: HistoricalTransition, t: Translator) {
  const interval = { from_iso_year: transition.origin_observation.iso_year, from_iso_week: transition.origin_observation.iso_week,
    to_iso_year: transition.destination_observation.iso_year, to_iso_week: transition.destination_observation.iso_week };
  return fillInterval(t(interval.from_iso_year === interval.to_iso_year ? 'historyBetweenRecords' : 'historyBetweenRecordsAcrossYears'), interval);
}

export function zoneHistoricalIntervalLabel(interval: HistoricalWeekInterval, t: Translator) {
  return fillInterval(t(interval.from_iso_year === interval.to_iso_year ? 'zoneMovementInterval' : 'zoneMovementIntervalAcrossYears'), interval);
}

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
  assessments: readonly HistoryAssessment[] = [], transitions: readonly HistoricalTransition[] = [],
) {
  const zone = (id: number | null | undefined) => locations.find(location => location.thermal_zone.id === id)?.thermal_zone ?? null;
  const pairs = uniqueLifecyclePairs(events);
  const pairedAudits = new Map([...pairs].map(([audit, movement]) => [movement, audit]));
  // Collapse only a unique explicit pair; preserve the recorded movement and
  // the full audit source, including correction capabilities and evidence.
  const rows: HistoryEvent[] = events.filter(event => !pairs.has(event) && event.kind !== 'location_evidence'
      && !['transition_candidate', 'transition_assessment'].includes(event.provenance)).map(event => {
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
  const attach = (index: number, field: 'reconciliation_diagnostics' | 'historical_source_transitions', value: unknown) => {
    const attached = rows[index].evidence[field];
    rows[index] = { ...rows[index], evidence: { ...rows[index].evidence, [field]: [...(Array.isArray(attached) ? attached : []), value] } };
  };
  // Exact period links supplied by the server: the planner row is the same transition.
  const plannerOwners = new Map<string, string>();
  const displayed = new Set(reconstructedHistoricalMovements(transitions));
  for (const transition of transitions) {
    const owner = transition.representation?.event_id
      ? rows.findIndex(row => row.id === transition.representation?.event_id) : -1;
    if (owner !== -1) { attach(owner, 'historical_source_transitions', transition); continue; }
    if (!displayed.has(transition)) continue;
    rows.push({ id: transition.id, kind: 'historical_transition', temporal_precision: 'observation_interval', occurred_at: null,
      iso_year: null, iso_week: null, from_thermal_zone: transition.from_thermal_zone, to_thermal_zone: transition.to_thermal_zone,
      actor: null, provenance: 'historical_tracking', evidence: { transition }, reconciliation_status: transition.status,
      recorded_movement_id: null, can_correct: false, notes: '' });
    for (const id of transition.planner_candidate_ids ?? []) plannerOwners.set(id, transition.id);
  }
  // A transition already persisted keeps one row: its diagnostic moves to that
  // row's provenance instead of reading as a second movement.
  const place = (entry: HistoryEvent, sourceId: string) => {
    const planned = plannerOwners.get(sourceId);
    if (planned) { attach(rows.findIndex(row => row.id === planned), 'reconciliation_diagnostics', entry); return; }
    const { represented_movement_ids: ids, origin_location_id: origin, destination_location_id: destination } = entry.evidence;
    const represents = (row: HistoryEvent) => (row.provenance === 'box_movement' && Array.isArray(ids)
        && ids.includes(row.recorded_movement_id))
      || (row.kind === 'historical_reconstruction' && typeof origin === 'number' && typeof destination === 'number'
        && row.evidence.origin_location_id === origin && row.evidence.destination_location_id === destination);
    if (!rows.some(represents)) return;
    rows.forEach((row, index) => { if (represents(row)) attach(index, 'reconciliation_diagnostics', entry); });
  };
  // Candidates describe reconciliation work, never an additional physical event.
  for (const candidate of candidates) {
    if (['review_required', 'blocked'].includes(candidate.reconciliation_status))
      place(diagnostic(candidate, 'transition_candidate'), candidate.id);
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
    place(diagnostic(assessment, 'transition_assessment'), assessment.id);
  }
  // Group by the movement calendar's ISO week (Paris). A week-only row is a
  // group-level item, not an event occurring at its Monday boundary.
  const group = (event: HistoryEvent) => {
    const transition = historicalTransitionOf(event);
    if (transition) return transition.destination_observation.week_start;
    if (event.kind === 'historical_reconstruction') return establishedHistoryWeek(event)?.start ?? '';
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


export function projectBoxHistory(
  events: readonly HistoryEvent[], locations: readonly BoxLocation[],
  _measurementDates: readonly string[], t: Translator, transitions: readonly HistoricalTransition[] = [],
): { events: TrendEvent[]; locations: TrendLocation[]; maskedLocationIds: Set<number> } {
  // Active location bands are scientific history, never a masking target.
  const maskedLocationIds = new Set<number>();
  const pairs = uniqueLifecyclePairs(events);
  const chartEvents: TrendEvent[] = [];
  const recordedIds = new Set<number>();
  for (const source of events) {
    if (pairs.has(source)) continue;
    const event = lifecyclePresentation(source, pairs);
    const detail = historyLocationLabel(event, t);
    const title = historyKindLabel(event, t);
    const interval = establishedHistoryWeek(event);
    if (interval) {
      // Thursday is only the visual centre of the ISO week, never an occurrence date.
      const midpoint = new Date(`${interval.start}T00:00:00Z`);
      midpoint.setUTCDate(midpoint.getUTCDate() + 3);
      chartEvents.push({ id: event.id, kind: 'historical_reconstruction', date: midpoint.toISOString().slice(0, 10),
        intervalStart: interval.start, intervalEnd: interval.end, temporalLabel: historyWeekLabel(event, t), title, detail,
        detailLines: [{ label: t('movementKindOrdinary'), value: detail },
          { label: t('historyReconstruction'), value: historyWeekLabel(event, t) },
          { label: t('historyPrecision'), value: t('historyWeekOnly') }] });
    } else if ((['ordinary', 'deactivation', 'reactivation'].includes(event.kind)
      || (event.kind === 'unclassified' && event.provenance === 'box_movement'
        && Number.isSafeInteger(event.recorded_movement_id) && event.recorded_movement_id! > 0))
      && Number.isFinite(exactInstant(event))) {
      if (['ordinary', 'unclassified'].includes(event.kind) && event.provenance === 'box_movement'
        && event.recorded_movement_id != null) {
        if (recordedIds.has(event.recorded_movement_id)) continue;
        recordedIds.add(event.recorded_movement_id);
      }
      chartEvents.push({ id: event.id, date: localHistoryDay(event.occurred_at!), instant: event.occurred_at!,
        title: ['ordinary', 'unclassified'].includes(event.kind) ? t('movementKindOrdinary') : title, detail,
        kind: ['ordinary', 'unclassified'].includes(event.kind) ? 'movement' : event.kind as 'deactivation' | 'reactivation' });
    }
  }
  const step = (transition: HistoricalTransition) => ({
    fromReadingId: transition.origin_observation.reading_id, toReadingId: transition.destination_observation.reading_id });
  for (const transition of transitions) {
    // The explicit representation, not review status or time proximity, owns the step.
    const representation = transition.representation;
    if (!representation?.event_id || !['box_movement', 'historical_box_movement'].includes(representation.kind)
      || !supportsRepresentedTemperatureStep(transition)
      || transitions.filter(item => item.representation?.event_id === representation.event_id).length !== 1) continue;
    const owners = chartEvents.filter(event => event.id === representation.event_id);
    if (owners.length !== 1) continue;
    const owner = chartEvents.indexOf(owners[0]);
    chartEvents[owner] = { ...chartEvents[owner], temperatureStep: step(transition) };
  }
  for (const transition of reconstructedHistoricalMovements(transitions)) {
    const route = historicalTransitionRoute(transition), interval = historicalTransitionInterval(transition, t);
    chartEvents.push({ id: transition.id, kind: 'historical_movement', date: historicalMovementMarkerDate(transition),
      title: t('movementEvent'), detail: route, temporalLabel: interval, temperatureStep: step(transition),
      detailLines: [{ label: t('historyHistoricalMovement'), value: route }, { label: '', value: interval }] });
  }
  return { events: chartEvents, maskedLocationIds, locations: locations.map(location => ({ id: location.id, name: location.thermal_zone.name, startsAt: location.starts_at,
      endsAt: location.ends_at, endDateUnknown: location.end_date_unknown })) };
}
