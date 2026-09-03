export type InventoryMeasurementFilter = '' | 'older_than' | 'none';

export type BoxInventoryFilters = {
  ageMonths: string;
  creationYear: string;
  location: string;
  measurementFilter: InventoryMeasurementFilter;
  referenceDate: string;
  search: string;
  status: string;
};

export function getActiveInventoryMeasurementFilter(
  status: string,
  measurementFilter: InventoryMeasurementFilter,
) {
  return status === 'pending_review' ? measurementFilter : '';
}

export function buildBoxInventoryQuery(
  filters: BoxInventoryFilters,
  pagination?: { limit: number; offset: number },
) {
  const params = new URLSearchParams();
  if (pagination) {
    params.set('limit', String(pagination.limit));
    params.set('offset', String(pagination.offset));
  }
  if (filters.status) params.set('status', filters.status);
  if (filters.location) params.set('location', filters.location);
  if (filters.search.trim()) params.set('q', filters.search.trim());
  if (filters.creationYear) params.set('creation_year', filters.creationYear);
  if (filters.referenceDate) params.set('reference_date', filters.referenceDate);
  if (filters.measurementFilter) {
    params.set('measurement_filter', filters.measurementFilter);
  }
  if (filters.measurementFilter === 'older_than') {
    params.set('age_months', filters.ageMonths);
  }
  return params.toString();
}

export function getMeasurementAgeInMonths(measuredOn: string, referenceDate: string) {
  const measurement = parseDateParts(measuredOn);
  const reference = parseDateParts(referenceDate);
  if (!measurement || !reference || compareDateParts(measurement, reference) > 0) {
    return null;
  }

  let months = (reference.year - measurement.year) * 12 + reference.month - measurement.month;
  if (reference.day < measurement.day) months -= 1;
  return Math.max(0, months);
}

export function isMeasurementOlderThanThreshold(
  measuredOn: string,
  referenceDate: string,
  ageMonths: number,
) {
  const measurement = parseDateParts(measuredOn);
  const reference = parseDateParts(referenceDate);
  if (!measurement || !reference || !Number.isInteger(ageMonths) || ageMonths < 1) {
    return false;
  }

  const targetMonthIndex = reference.year * 12 + reference.month - 1 - ageMonths;
  const cutoffYear = Math.floor(targetMonthIndex / 12);
  const cutoffMonth = targetMonthIndex - cutoffYear * 12 + 1;
  const cutoffDay = Math.min(reference.day, daysInMonth(cutoffYear, cutoffMonth));
  return compareDateParts(measurement, {
    day: cutoffDay,
    month: cutoffMonth,
    year: cutoffYear,
  }) < 0;
}

export function isZeroZeroMeasurement(
  measurement: { polyp_count: number; ephyrae_count: number } | null,
) {
  return measurement !== null
    && measurement.polyp_count === 0
    && measurement.ephyrae_count === 0;
}

export function getDeactivationSuggestionAge(
  status: string,
  measurement: { measured_on: string } | null,
  measurementFilter: InventoryMeasurementFilter,
  referenceDate: string,
  ageMonths: number,
) {
  if (
    status !== 'pending_review'
    || measurementFilter !== 'older_than'
    || !measurement
    || !isMeasurementOlderThanThreshold(measurement.measured_on, referenceDate, ageMonths)
  ) {
    return null;
  }
  return getMeasurementAgeInMonths(measurement.measured_on, referenceDate);
}

export function getLocalDateInputValue(date = new Date()) {
  const localDate = new Date(date);
  localDate.setMinutes(localDate.getMinutes() - localDate.getTimezoneOffset());
  return localDate.toISOString().slice(0, 10);
}

type DateParts = { day: number; month: number; year: number };

function parseDateParts(value: string): DateParts | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (!year || month < 1 || month > 12 || day < 1 || day > 31) return null;
  return { day, month, year };
}

function compareDateParts(left: DateParts, right: DateParts) {
  return left.year - right.year || left.month - right.month || left.day - right.day;
}

function daysInMonth(year: number, month: number) {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}
