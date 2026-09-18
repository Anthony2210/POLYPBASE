import type { BiologicalMeasurement } from '../types';

export type MeasurementEditorMode = 'create' | 'edit' | 'locked' | 'read_only';

export function getIsoWeekStart(dateValue: string): string {
  const [year, month, day] = dateValue.split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  const weekday = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() - weekday + 1);
  return date.toISOString().slice(0, 10);
}

export function findMeasurementForWeek(
  measurements: BiologicalMeasurement[],
  referenceDate: string,
): BiologicalMeasurement | null {
  const weekStart = getIsoWeekStart(referenceDate);
  return measurements.find(
    (measurement) => getIsoWeekStart(measurement.measured_on) === weekStart,
  ) ?? null;
}

export function getMeasurementEditorMode(input: {
  measurement: BiologicalMeasurement | null;
  canCreateMeasurement: boolean;
}): MeasurementEditorMode {
  const { measurement, canCreateMeasurement } = input;
  if (measurement) {
    return measurement.can_edit ? 'edit' : 'locked';
  }
  if (canCreateMeasurement) return 'create';
  return 'read_only';
}

export function getMeasurementFormValues(
  measurement: BiologicalMeasurement,
) {
  return {
    measuredOn: measurement.measured_on,
    polypCount: String(measurement.polyp_count),
    ephyraeCount: String(measurement.ephyrae_count),
    salinity: measurement.salinity_psu ?? '',
    notes: measurement.notes ?? '',
  };
}

export function formatMeasurementCount(value: number | null | undefined): string {
  return value == null ? '-' : String(value);
}

export function isMeasurementWeekConflict(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const candidate = error as { status?: unknown; data?: unknown };
  if (candidate.status !== 409 || !candidate.data || typeof candidate.data !== 'object') {
    return false;
  }
  return (candidate.data as { code?: unknown }).code === 'measurement_week_conflict';
}

export function isMeasurementEditWindowExpired(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const candidate = error as { status?: unknown; data?: unknown };
  if (candidate.status !== 403 || !candidate.data || typeof candidate.data !== 'object') {
    return false;
  }
  return (candidate.data as { code?: unknown }).code === 'edit_window_expired';
}
