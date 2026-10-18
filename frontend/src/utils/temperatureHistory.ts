import { apiGet, getOrganizationResourceSignal, subscribeTemperatureHistoryChanges } from '../api/client';
import type { BoxTemperaturePoint, PaginatedResponse } from '../types';

export type TemperatureRange = { startDate: string; endDate: string };
export type TemperatureHistoryState = {
  points: BoxTemperaturePoint[];
  loading: boolean;
  failed: boolean;
};
const DAY_MS = 86_400_000;
const PAGE_SIZE = 500;
// Bounded automatic recovery from transient failures: three retries, then stop quietly.
export const TEMPERATURE_RETRY_DELAYS_MS = [1_000, 3_000, 8_000];

// Network failures and temporary server states are retried; auth, validation and abort errors are not.
function isTransientFailure(error: unknown) {
  const failure = error as { name?: unknown; status?: unknown } | null;
  if (typeof failure?.status === 'number') {
    return failure.status >= 500 || failure.status === 408 || failure.status === 429;
  }
  return failure?.name === 'TypeError';
}

// Resolves early when the request is cancelled so an obsolete retry never lingers.
function waitForRetry(delay: number, signal: AbortSignal) {
  return new Promise<void>((resolve) => {
    const done = () => { clearTimeout(timer); signal.removeEventListener('abort', done); resolve(); };
    const timer = setTimeout(done, delay);
    signal.addEventListener('abort', done, { once: true });
  });
}

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
  retryDelays: readonly number[] = TEMPERATURE_RETRY_DELAYS_MS,
) {
  let generation = 0;
  let active = !contextSignal.aborted;
  let request: AbortController | null = null;
  let notify = publish;
  let selection: { range: TemperatureRange; publish: typeof publish } | null = null;
  // Coverage belongs to this Box/context lifetime, including successful empty ranges.
  const coverage: { start: number; end: number; points: BoxTemperaturePoint[] }[] = [];
  const cancel = () => { generation += 1; request?.abort(); request = null; };
  const missing = (range: TemperatureRange): TemperatureRange[] => {
    let cursor = dateNumber(range.startDate);
    const end = dateNumber(range.endDate);
    if (cursor > end) throw new Error('Invalid chart range.');
    const ranges: TemperatureRange[] = [];
    const append = (start: number, last: number) => ranges.push({
      startDate: new Date(start).toISOString().slice(0, 10),
      endDate: new Date(last).toISOString().slice(0, 10),
    });
    for (const cached of coverage) {
      if (cached.end < cursor) continue;
      if (cached.start > end) break;
      if (cached.start > cursor) append(cursor, cached.start - DAY_MS);
      cursor = Math.max(cursor, cached.end + DAY_MS);
      if (cursor > end) break;
    }
    if (cursor <= end) append(cursor, end);
    return ranges;
  };
  const snapshot = (range: TemperatureRange): TemperatureHistoryState => {
    if (!active || contextSignal.aborted) return { points: [], loading: false, failed: false };
    try {
      const loading = missing(range).length > 0;
      const points = coverage.flatMap((cached) => cached.points)
        .filter((point) => point.date >= range.startDate && point.date <= range.endDate)
        .sort((left, right) => left.date.localeCompare(right.date));
      return { points, loading, failed: false };
    } catch {
      return { points: [], loading: false, failed: true };
    }
  };
  const onContextAbort = () => {
    active = false;
    cancel();
    coverage.length = 0;
    selection = null;
    notify({ points: [], loading: false, failed: false });
  };
  contextSignal.addEventListener('abort', onContextAbort, { once: true });
  const unsubscribe = subscribeTemperatureHistoryChanges(() => {
    if (!active || contextSignal.aborted) return;
    cancel();
    coverage.length = 0;
    if (selection) void history.select(selection.range, selection.publish);
  });
  const history = {
    snapshot,
    cancel() { cancel(); selection = null; },
    async select(range: TemperatureRange, publishSelection = publish) {
      if (!active || contextSignal.aborted) return;
      cancel();
      notify = publishSelection;
      // Copy the range so a caller cannot change the meaning of an in-flight read.
      range = { ...range };
      selection = { range, publish: publishSelection };
      const currentGeneration = generation;
      const current = request = new AbortController();
      const isCurrent = () => active && !contextSignal.aborted && !current.signal.aborted
        && generation === currentGeneration;
      notify(snapshot(range));
      for (let attempt = 0; ; attempt += 1) {
        try {
          // Missing ranges are recomputed per attempt, so completed chunks are kept and not reloaded.
          for (const chunk of missing(range).flatMap(temperatureRangeChunks)) {
            if (!isCurrent()) return;
            const points = await load(boxId, chunk, current.signal);
            if (!isCurrent()) return;
            // Commit only a complete chunk, never an incomplete pagination result.
            coverage.push({ start: dateNumber(chunk.startDate), end: dateNumber(chunk.endDate), points });
            coverage.sort((left, right) => left.start - right.start);
            notify(snapshot(range));
          }
          return;
        } catch (error) {
          if (!isCurrent()) return;
          if (attempt >= retryDelays.length || !isTransientFailure(error)) {
            notify({ ...snapshot(range), loading: false, failed: true });
            return;
          }
          // Keep valid coverage visible while waiting; the snapshot reports no failure.
          notify(snapshot(range));
          await waitForRetry(retryDelays[attempt], current.signal);
          if (!isCurrent()) return;
        }
      }
    },
    dispose() {
      active = false;
      cancel();
      coverage.length = 0;
      selection = null;
      unsubscribe();
      contextSignal.removeEventListener('abort', onContextAbort);
    },
  };
  return history;
}
