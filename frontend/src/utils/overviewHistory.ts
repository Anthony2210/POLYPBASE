import type { BiologicalMeasurement, BoxDetail, BoxLocation, OverviewMeasurementPoint } from '../types';
import { addChartMonths, toChartDateString } from './chartWindow';

export type OverviewRange = { startDate: string; endDate: string };
export type OverviewHistoryState = {
  range: OverviewRange;
  measurements: OverviewMeasurementPoint[];
  locations: BoxLocation[];
  complete: boolean;
  loading: boolean;
  failedRange: OverviewRange | null;
};

export function overviewDefaultRange(today = new Date()): OverviewRange {
  return { startDate: toChartDateString(addChartMonths(today, -3)), endDate: toChartDateString(today) };
}

export function normalizeOverviewMeasurements(measurements: BiologicalMeasurement[]): OverviewMeasurementPoint[] {
  return measurements.map((point) => ({
    date: point.measured_on,
    polyp_count: point.polyp_count,
    ephyrae_count: point.ephyrae_count,
    salinity_psu: point.salinity_psu,
  })).sort((left, right) => left.date.localeCompare(right.date));
}

// Only commit a wider range once its complete history has arrived.
export function createOverviewHistory(
  initial: OverviewHistoryState,
  loadedStart: string,
  load: () => Promise<BoxDetail>,
  publish: (state: OverviewHistoryState) => void,
) {
  let state = initial;
  let generation = 0;
  let active = true;
  let pending: Promise<BoxDetail> | null = null;
  function update(next: OverviewHistoryState) { state = next; publish(state); }
  return {
    async select(range: OverviewRange) {
      if (!active) return;
      const requestGeneration = ++generation;
      if (state.complete || range.startDate >= loadedStart) {
        update({ ...state, range, loading: false, failedRange: null });
        return;
      }
      update({ ...state, loading: true, failedRange: null });
      const request = pending ??= load();
      try {
        const detail = await request;
        if (!active || requestGeneration !== generation) return;
        update({ ...state, range, measurements: normalizeOverviewMeasurements(detail.biological_measurements),
          locations: detail.locations, complete: true, loading: false, failedRange: null });
      } catch {
        if (!active || requestGeneration !== generation) return;
        update({ ...state, loading: false, failedRange: range });
      } finally {
        if (pending === request) pending = null;
      }
    },
    dispose() { active = false; generation += 1; },
  };
}
