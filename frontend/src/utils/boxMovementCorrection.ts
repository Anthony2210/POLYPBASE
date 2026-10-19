import { ApiError } from '../api/client';
import type { Language, TranslationKey } from '../i18n';
import type { BoxDetail, BoxItem, BoxMovement } from '../types';

export const MOVEMENT_TIME_ZONE = 'Europe/Paris';

const parisParts = new Intl.DateTimeFormat('en-GB', {
  timeZone: MOVEMENT_TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
});

function hasTimestamp(value: string) {
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/i.test(value)
    && Number.isFinite(Date.parse(value));
}

export function toParisMovementInput(value: string): string | null {
  if (!hasTimestamp(value)) return null;
  const parts = Object.fromEntries(parisParts.formatToParts(new Date(value)).map((part) => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}`;
}

export type ParisMovementTimeResult =
  | { timestamp: string; error?: never }
  | { timestamp?: never; error: 'movementTimeInvalid' | 'movementTimeGap' | 'movementTimeAmbiguous' | 'movementTimeOutsideWeek' };

/** Monday 00:00:00 to Sunday 23:59:59 Paris wall time of the week holding the original movement. */
export function parisMovementWeekBounds(original: string): { min: string; max: string } | null {
  const local = toParisMovementInput(original);
  if (!local) return null;
  const day = Date.parse(`${local.slice(0, 10)}T00:00:00Z`);
  const monday = day - ((new Date(day).getUTCDay() + 6) % 7) * 86400000;
  const date = (offset: number) => new Date(monday + offset * 86400000).toISOString().slice(0, 10);
  return { min: `${date(0)}T00:00:00`, max: `${date(6)}T23:59:59` };
}

/** Reject folds as well as gaps: the editor never guesses which instant was intended. */
export function resolveParisMovementTime(value: string, original: string): ParisMovementTimeResult {
  if (value === toParisMovementInput(original)) return { timestamp: original };
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value);
  if (!match) return { error: 'movementTimeInvalid' };
  const [, year, month, day, hour, minute, seconds = '00'] = match;
  const normalized = `${year}-${month}-${day}T${hour}:${minute}:${seconds}`;
  if (normalized === toParisMovementInput(original)) return { timestamp: original };
  const wallTime = Date.parse(`${normalized}Z`);
  if (!Number.isFinite(wallTime) || new Date(wallTime).toISOString().slice(0, 19) !== normalized) {
    return { error: 'movementTimeInvalid' };
  }
  // Collect offsets on either side of a transition, independently of the browser timezone.
  const offsets = new Set<number>();
  for (const delta of [-48, -24, 0, 24, 48]) {
    const instant = wallTime + delta * 3600000;
    const local = toParisMovementInput(new Date(instant).toISOString());
    if (local) offsets.add(Date.parse(`${local}Z`) - instant);
  }
  const candidates = [...offsets].map((offset) => new Date(wallTime - offset).toISOString())
    .filter((instant) => toParisMovementInput(instant) === normalized);
  if (!candidates.length) return { error: 'movementTimeGap' };
  if (candidates.length > 1) return { error: 'movementTimeAmbiguous' };
  // Wall-time strings compare chronologically, so the week check is independent of DST and UTC days.
  const bounds = parisMovementWeekBounds(original);
  if (!bounds || normalized < bounds.min || normalized > bounds.max) return { error: 'movementTimeOutsideWeek' };
  return { timestamp: candidates[0] };
}

export function formatMovementTimestamp(value: string, language: Language): string {
  const locale = language === 'fr' ? 'fr-FR' : 'en-GB';
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const date = new Date(`${value}T00:00:00Z`);
    return Number.isFinite(date.getTime())
      ? new Intl.DateTimeFormat(locale, { timeZone: 'UTC', dateStyle: 'medium' }).format(date) : value;
  }
  if (!hasTimestamp(value)) return value;
  return new Intl.DateTimeFormat(locale, {
    timeZone: MOVEMENT_TIME_ZONE, dateStyle: 'medium', timeStyle: 'short',
  }).format(new Date(value));
}

export function latestPersistedMovement(
  box: BoxItem | BoxDetail, include: (movement: BoxMovement) => boolean = () => true,
): BoxMovement | null {
  if (!('movements' in box)) return null;
  return box.movements.filter(include).sort((a, b) => {
    const left = Date.parse(a.moved_at);
    const right = Date.parse(b.moved_at);
    return (Number.isFinite(right) ? right : -Infinity) - (Number.isFinite(left) ? left : -Infinity)
      || b.id - a.id;
  })[0] ?? null;
}

const KIND_KEYS: Record<string, TranslationKey> = {
  ordinary: 'movementKindOrdinary', initial_location: 'movementKindInitialLocation',
  qualification: 'movementKindQualification', reactivation: 'movementKindReactivation',
  unclassified: 'movementKindUnclassified',
};

export function movementKindKey(movement: BoxMovement): TranslationKey {
  return KIND_KEYS[movement.kind ?? 'unclassified'] ?? 'movementKindUnclassified';
}

// Lifecycle entries stay in the history; they never appear in the last-movement banner.
export function isLifecycleMovement(movement: BoxMovement) {
  return ['initial_location', 'qualification', 'reactivation'].includes(movement.kind ?? '');
}

/** The newest movement when it is a correctable lifecycle entry, reached from the movement history. */
export function correctableLifecycleMovement(box: BoxItem | BoxDetail): BoxMovement | null {
  const latest = latestPersistedMovement(box);
  return latest && isLifecycleMovement(latest) && canCorrectMovement(movementCorrectionCapabilities(box, latest)) ? latest : null;
}

/** A notes-only correction is never offered: the editor needs a date or a destination the server can verify. */
export function canCorrectMovement(capabilities: { notes: boolean; date: boolean; destination: boolean }) {
  return capabilities.notes && (capabilities.date || capabilities.destination);
}

export function movementCorrectionCapabilities(box: BoxItem | BoxDetail, movement: BoxMovement) {
  const notes = Number.isInteger(movement.revision) && (movement.revision ?? -1) >= 0;
  // The server exposes whether the movement recorded its periods; without them nothing but notes is safe.
  const classified = ['ordinary', 'initial_location', 'qualification', 'reactivation', 'unclassified'].includes(movement.kind ?? 'unclassified');
  const destinationLinked = movement.has_destination_link === true;
  const openPeriods = 'locations' in box
    ? box.locations.filter((period) => period.ends_at === null && !period.end_date_unknown) : [];
  // These are UI eligibility checks only. The server verifies recorded links and history.
  return {
    notes,
    date: notes && movement.kind === 'ordinary' && hasTimestamp(movement.moved_at)
      && destinationLinked && movement.has_origin_link === true,
    destination: notes && classified && destinationLinked && 'movements' in box
      && Number.isFinite(Date.parse(movement.moved_at))
      && box.status === 'active' && box.thermal_zone?.id === movement.to_thermal_zone.id
      && latestPersistedMovement(box)?.id === movement.id
      && !box.movements.some((other) => other.id !== movement.id
        && (!Number.isFinite(Date.parse(other.moved_at)) || Date.parse(other.moved_at) >= Date.parse(movement.moved_at)))
      && openPeriods.length === 1 && openPeriods[0].thermal_zone.id === box.thermal_zone?.id,
  };
}

const ERROR_KEYS: Record<string, TranslationKey> = {
  box_movement_changed: 'movementErrorChanged',
  box_movement_week_conflict: 'movementErrorWeekConflict',
  movement_provenance_unresolved: 'movementErrorProvenance',
  movement_date_not_correctable: 'movementErrorDateReadOnly',
  movement_period_link_missing: 'movementErrorMissingLink',
  movement_period_mismatch: 'movementErrorPeriodMismatch',
  movement_period_overlap: 'movementErrorOverlap',
  movement_chronology_conflict: 'movementErrorChronology',
  movement_not_terminal: 'movementErrorNotTerminal',
  movement_same_zone: 'movementErrorSameZone',
  movement_date_in_future: 'movementErrorFuture',
  movement_date_outside_week: 'movementErrorOutsideWeek',
  movement_correction_no_change: 'movementErrorNoChange',
};

export function movementCorrectionErrorCode(error: unknown): string | null {
  if (!(error instanceof ApiError) || !error.data || typeof error.data !== 'object') return null;
  const code = (error.data as Record<string, unknown>).code;
  return typeof code === 'string' ? code : null;
}

export function movementCorrectionErrorKey(error: unknown): TranslationKey {
  const code = movementCorrectionErrorCode(error);
  if (code && ERROR_KEYS[code]) return ERROR_KEYS[code];
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403) return 'movementErrorForbidden';
    if (error.status === 404) return 'movementErrorNotFound';
    if (error.status === 400) return 'movementErrorValidation';
    if (error.status === 409) return 'movementErrorConflict';
  }
  return 'movementErrorSave';
}
