import { apiGet } from '../api/client';
import type { HistoricalTemperaturePoint, PaginatedResponse } from '../types';
import { temperatureRangeChunks, type TemperatureRange } from './temperatureHistory';

const PAGE_SIZE = 500;
const DAY_MS = 86_400_000;

// Plot-ready weekly point. `date` only positions the marker (middle of the ISO week); it is
// never shown as an observation day.
export type WeeklyTrendTemperature = {
  date: string;
  average_temperature_c: number;
  zone_id?: undefined;
  zone_name?: undefined;
  week: {
    id: number;
    year: number;
    number: number;
    start: string;
    end: string;
    valueText: string;
  };
};

// Weeks overlapping two chunks are returned twice; the reading id is the identity.
export async function loadHistoricalTemperatures(boxId: number, range: TemperatureRange, signal: AbortSignal) {
  const points = new Map<number, HistoricalTemperaturePoint>();
  for (const chunk of temperatureRangeChunks(range)) {
    let offset = 0;
    while (true) {
      signal.throwIfAborted();
      const query = new URLSearchParams({ start: chunk.startDate, end: chunk.endDate,
        limit: String(PAGE_SIZE), offset: String(offset) });
      const page = await apiGet<PaginatedResponse<HistoricalTemperaturePoint>>(
        `/api/boxes/${boxId}/historical-temperatures/?${query}`, { signal, cache: 'no-store' });
      signal.throwIfAborted();
      for (const point of page.results) points.set(point.id, point);
      if (!page.next) break;
      offset += PAGE_SIZE;
      if (offset >= page.count) throw new Error('Invalid historical temperature pagination.');
    }
  }
  return [...points.values()].sort((left, right) => (
    left.interval_start.localeCompare(right.interval_start) || left.id - right.id));
}

const DECIMAL = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;

// Zero is a measurement. An invalid or missing value is dropped, never coerced to 0.
export function toWeeklyTrendTemperature(point: HistoricalTemperaturePoint): WeeklyTrendTemperature | null {
  if (typeof point.temperature_c !== 'string' || !DECIMAL.test(point.temperature_c.trim())) return null;
  const value = Number(point.temperature_c);
  const first = Date.parse(`${point.interval_start}T00:00:00Z`);
  const last = Date.parse(`${point.interval_end}T00:00:00Z`);
  if (!Number.isFinite(value) || !Number.isFinite(first) || !Number.isFinite(last) || last < first) return null;
  return {
    date: new Date(first + Math.floor((last - first) / 2 / DAY_MS) * DAY_MS).toISOString().slice(0, 10),
    average_temperature_c: value,
    week: {
      id: point.id,
      year: point.iso_year,
      number: point.iso_week,
      start: point.interval_start,
      end: point.interval_end,
      valueText: point.temperature_c.trim(),
    },
  };
}

export function weeklyTrendTemperatures(points: readonly HistoricalTemperaturePoint[]) {
  return points.flatMap((point) => toWeeklyTrendTemperature(point) ?? []);
}
