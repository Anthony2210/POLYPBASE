/**
 * Form helpers for Administration > Laboratory configuration.
 *
 * Values stay strings while typed. An empty field means "not set" (null);
 * "0" is a real configured value and is always sent as 0.
 */

import { formatBiologicalSalinity, stepBiologicalSalinity } from './biologicalSalinity';

export type LabLocationSource = {
  name: string;
  capacity: number | null;
  target_temperature_c: string | null;
  salinity_psu: string | null;
};

export type LabLocationDraft = {
  name: string;
  capacity: string;
  targetTemperature: string;
  salinity: string;
};

export type LabLocationField = keyof LabLocationDraft;

export type LabLocationValues = {
  name: string;
  capacity: number | null;
  target_temperature_c: string | null;
  salinity_psu: string | null;
};

export type LabLocationValidationError = 'required' | 'capacity' | 'temperature' | 'salinity';

export type LabLocationParseResult =
  | { ok: true; values: LabLocationValues }
  | { ok: false; errors: Partial<Record<LabLocationField, LabLocationValidationError>> };

// Bounds of the model columns: capacity is a positive integer, the target
// temperature has 4 digits with 1 decimal, the salinity 5 digits with 2.
const CAPACITY_PATTERN = /^\d+$/;
const TEMPERATURE_PATTERN = /^-?\d{1,3}(\.\d)?$/;
const SALINITY_PATTERN = /^\d{1,3}(\.\d{1,2})?$/;
const MAX_CAPACITY = 2147483647;

export const EMPTY_LAB_LOCATION_DRAFT: LabLocationDraft = {
  name: '',
  capacity: '',
  targetTemperature: '',
  salinity: '',
};

export function buildLabLocationDraft(zone: LabLocationSource | null): LabLocationDraft {
  if (!zone) return { ...EMPTY_LAB_LOCATION_DRAFT };
  return {
    name: zone.name,
    capacity: zone.capacity == null ? '' : String(zone.capacity),
    targetTemperature: formatDecimalDraft(zone.target_temperature_c),
    salinity: formatDecimalDraft(zone.salinity_psu),
  };
}

/** "15.0" -> "15", "32.50" -> "32.5": the field shows what was entered. */
function formatDecimalDraft(value: string | null) {
  if (value == null || value === '') return '';
  const numeric = Number(value);
  return Number.isFinite(numeric) ? String(numeric) : value;
}

function normalizeDecimalInput(value: string) {
  return value.trim().replace(',', '.');
}

export function parseLabLocationDraft(draft: LabLocationDraft): LabLocationParseResult {
  const errors: Partial<Record<LabLocationField, LabLocationValidationError>> = {};
  const name = draft.name.trim();
  if (!name) errors.name = 'required';

  const capacityText = draft.capacity.trim();
  let capacity: number | null = null;
  if (capacityText) {
    if (CAPACITY_PATTERN.test(capacityText) && Number(capacityText) <= MAX_CAPACITY) {
      capacity = Number(capacityText);
    } else {
      errors.capacity = 'capacity';
    }
  }

  const temperatureText = normalizeDecimalInput(draft.targetTemperature);
  if (temperatureText && !TEMPERATURE_PATTERN.test(temperatureText)) errors.targetTemperature = 'temperature';

  const salinityText = normalizeDecimalInput(draft.salinity);
  if (salinityText && !SALINITY_PATTERN.test(salinityText)) errors.salinity = 'salinity';

  if (Object.keys(errors).length) return { ok: false, errors };
  return {
    ok: true,
    values: {
      name,
      capacity,
      target_temperature_c: temperatureText || null,
      salinity_psu: salinityText || null,
    },
  };
}

function sameDecimal(current: string | null, next: string | null) {
  if (current == null || current === '' || next == null) return (current == null || current === '') && next == null;
  return Number(current) === Number(next);
}

/** Only the fields whose value really changes, so an edit never rewrites the others. */
export function getChangedLabLocationValues(
  zone: LabLocationSource,
  values: LabLocationValues,
): Partial<LabLocationValues> {
  const changes: Partial<LabLocationValues> = {};
  if (values.name !== zone.name) changes.name = values.name;
  if (values.capacity !== zone.capacity) changes.capacity = values.capacity;
  if (!sameDecimal(zone.target_temperature_c, values.target_temperature_c)) {
    changes.target_temperature_c = values.target_temperature_c;
  }
  if (!sameDecimal(zone.salinity_psu, values.salinity_psu)) changes.salinity_psu = values.salinity_psu;
  return changes;
}

/** Maps a DRF field error from the API onto the form field that shows it. */
export const LAB_LOCATION_API_FIELDS: Record<string, LabLocationField> = {
  name: 'name',
  capacity: 'capacity',
  target_temperature_c: 'targetTemperature',
  salinity_psu: 'salinity',
};

export function getLabLocationApiFieldErrors(data: unknown): Partial<Record<LabLocationField, string>> {
  if (!data || typeof data !== 'object' || Array.isArray(data)) return {};
  const errors: Partial<Record<LabLocationField, string>> = {};
  for (const [apiField, field] of Object.entries(LAB_LOCATION_API_FIELDS)) {
    const value = (data as Record<string, unknown>)[apiField];
    const message = typeof value === 'string'
      ? value
      : Array.isArray(value) ? value.find((item): item is string => typeof item === 'string') : undefined;
    if (message) errors[field] = message;
  }
  return errors;
}

/** Same order as the Emplacements overview: by target temperature, then name. */
export function compareLabLocations(first: LabLocationSource, second: LabLocationSource) {
  const firstTemperature = Number.parseFloat(first.target_temperature_c ?? '');
  const secondTemperature = Number.parseFloat(second.target_temperature_c ?? '');
  const firstRank = Number.isFinite(firstTemperature) ? firstTemperature : Number.POSITIVE_INFINITY;
  const secondRank = Number.isFinite(secondTemperature) ? secondTemperature : Number.POSITIVE_INFINITY;
  if (firstRank !== secondRank) return firstRank - secondRank;
  return first.name.localeCompare(second.name, 'fr-FR', { numeric: true });
}

/**
 * Capacity -/+ buttons: whole boxes, never below zero. An unreadable entry is
 * left untouched so its validation message stays visible.
 */
export function stepLabCapacity(value: string, increment: number) {
  const trimmed = value.trim();
  if (!trimmed) return String(Math.max(increment, 0));
  if (!CAPACITY_PATTERN.test(trimmed)) return value;
  return String(Math.max(Number(trimmed) + increment, 0));
}

/**
 * Salinity -/+ buttons keep the two stored decimals (32.25 + 5 = 37.25), using
 * the same integer-hundredths stepping as the box measurement editor.
 */
export function stepLabSalinity(value: string, increment: number) {
  return stepBiologicalSalinity(value.trim().replace(',', '.'), increment);
}

/** Configured salinity as stored, without rounding: "32.25", "32.5", "0"; null shows "-". */
export function formatLabSalinity(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === '') return '-';
  return formatBiologicalSalinity(String(value));
}
