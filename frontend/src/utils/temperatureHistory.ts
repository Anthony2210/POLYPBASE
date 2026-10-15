import { apiGet, getOrganizationResourceSignal } from '../api/client';
import type { BoxTemperaturePoint, PaginatedResponse } from '../types';

export type TemperatureRange = { startDate: string; endDate: string };
export type TemperatureHistoryState = {
  points: BoxTemperaturePoint[];
  loading: boolean;
  failed: boolean;
};
const DAY_MS = 86_400_000;
const PAGE_SIZE = 500;

function dateNumber(value: string) {
  const date = Date.parse(`${value}T00:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(date)
    || new Date(date).toISOString().slice(0, 10) !== value) throw new Error('Invalid chart date.');
  return date;
}

export function temperatureRangeChunks(range: TemperatureRange): TemperatureRange[] {
  const start = dateNumber(range.startDate);
  const end = dateNumber(range.endDate);
  if (start > end) throw new Error('Invalid chart range.');
  const chunks: TemperatureRange[] = [];
  for (let cursor = start; cursor <= end; cursor += 366 * DAY_MS) {
    chunks.push({
      startDate: new Date(cursor).toISOString().slice(0, 10),
      endDate: new Date(Math.min(end, cursor + 365 * DAY_MS)).toISOString().slice(0, 10),
    });
  }
  return chunks;
}

// Never join the client's shared GET cache: each chart has its own cancellable lifetime.
export async function loadTemperatureHistory(boxId: number, range: TemperatureRange, signal: AbortSignal) {
  const points: BoxTemperaturePoint[] = [];
  for (const chunk of temperatureRangeChunks(range)) {
    let offset = 0;
    while (true) {
      signal.throwIfAborted();
      const query = new URLSearchParams({ start: chunk.startDate, end: chunk.endDate,
        limit: String(PAGE_SIZE), offset: String(offset) });
      const page = await apiGet<PaginatedResponse<BoxTemperaturePoint>>(
        `/api/boxes/${boxId}/temperatures/?${query}`, { signal, cache: 'no-store' });
      signal.throwIfAborted();
      points.push(...page.results);
      if (!page.next) break;
      // Follow pagination without sending organization headers to a server-provided URL.
      offset += PAGE_SIZE;
      if (offset >= page.count) throw new Error('Invalid temperature pagination.');
    }
  }
  // Stable sorting retains separate zone observations on the same day, including zero/null.
  return points.sort((left, right) => left.date.localeCompare(right.date));
}

export function createTemperatureHistory(
  boxId: number,
  publish: (state: TemperatureHistoryState) => void,
  contextSignal = getOrganizationResourceSignal(),
  load = loadTemperatureHistory,
) {
  let generation = 0;
  let active = !contextSignal.aborted;
  let request: AbortController | null = null;
  const cancel = () => { generation += 1; request?.abort(); request = null; };
  const onContextAbort = () => {
    active = false;
    cancel();
    publish({ points: [], loading: false, failed: false });
  };
  contextSignal.addEventListener('abort', onContextAbort, { once: true });
  return {
    async select(range: TemperatureRange) {
      if (!active || contextSignal.aborted) return;
      cancel();
      const currentGeneration = generation;
      const current = request = new AbortController();
      const isCurrent = () => active && !contextSignal.aborted && !current.signal.aborted
        && generation === currentGeneration;
      publish({ points: [], loading: true, failed: false });
      try {
        const points = await load(boxId, range, current.signal);
        if (isCurrent()) publish({ points, loading: false, failed: false });
      } catch {
        if (isCurrent()) publish({ points: [], loading: false, failed: true });
      }
    },
    dispose() {
      active = false;
      cancel();
      contextSignal.removeEventListener('abort', onContextAbort);
    },
  };
}
