import type { ProbeReadingPoint, ProbeReadingSource, ZoneProbeReadings } from '../types';
import type { ProbeWindow } from './probeChart';

const CACHE_LIMIT = 24;
const CACHE_TTL_MS = 60_000;
export const PROBE_REQUEST_DEBOUNCE_MS = 140;
const keyOf = (window: ProbeWindow) => `${window.start}:${window.end}`;
const covers = (outer: ProbeWindow, inner: ProbeWindow) => outer.start <= inner.start && outer.end >= inner.end;
const overlaps = (a: ProbeWindow, b: ProbeWindow) => a.start < b.end && b.start < a.end;

export function requestedProbeResolution(window: ProbeWindow) {
  const days = (window.end - window.start) / 86_400_000;
  return days <= 2 ? 1 : days <= 31 ? 15 : 60;
}

function sourceOverlaps([start, end]: ProbeReadingSource, window: ProbeWindow) {
  return end === null ? start >= window.start && start < window.end : start < window.end && end > window.start;
}

function sourceSumCents(source: ProbeReadingSource): bigint {
  const value = source[2];
  const [whole, fraction = ''] = value.replace(/^-/, '').split('.');
  const cents = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'));
  return value.startsWith('-') ? -cents : cents;
}

/** Match the server's decimal HALF_UP rounding without accumulating floating-point sums. */
function sourceMean(sources: ProbeReadingSource[]) {
  const total = sources.reduce((sum, source) => sum + sourceSumCents(source), 0n);
  const count = BigInt(sources.reduce((sum, source) => sum + source[3], 0));
  const magnitude = total < 0n ? -total : total;
  const rounded = (magnitude * 2n + count) / (count * 2n);
  return Number(total < 0n ? -rounded : rounded) / 100;
}

export function projectProbeReadings(data: ZoneProbeReadings, window: ProbeWindow): ZoneProbeReadings {
  const originalWindow = { start: Date.parse(data.start), end: Date.parse(data.end) };
  return {
    ...data, start: new Date(window.start).toISOString(), end: new Date(window.end).toISOString(),
    coverage_complete: data.coverage_complete !== false && covers(originalWindow, window),
    probes: data.probes.map((probe) => {
      if (!probe.point_sources) return {
        ...probe, points: probe.points.filter(([time]) => time >= window.start && time < window.end),
      };
      const points: ProbeReadingPoint[] = [];
      const point_sources: ProbeReadingSource[][] = [];
      for (const [index, point] of probe.points.entries()) {
        const sources = (probe.point_sources[index] ?? []).filter((source) => sourceOverlaps(source, window));
        if (!sources.length) continue;
        const raw = data.resolution_minutes === 1 && sources.length === 1 && sources[0][1] === null;
        points.push([point[0], sourceMean(sources),
          raw ? null : Math.min(...sources.map((source) => source[4])),
          raw ? null : Math.max(...sources.map((source) => source[5]))]);
        point_sources.push(sources);
      }
      return { ...probe, points, point_sources };
    }),
  };
}

type Entry = { window: ProbeWindow; data: ZoneProbeReadings; receivedAt: number };
export class ProbeReadingsCache {
  private entries: Entry[] = [];
  constructor(private now: () => number = Date.now) {}

  clear() { this.entries = []; }

  put(window: ProbeWindow, data: ZoneProbeReadings) {
    this.entries = this.entries.filter((entry) => keyOf(entry.window) !== keyOf(window));
    this.entries.push({ window: { ...window }, data, receivedAt: this.now() });
    if (this.entries.length > CACHE_LIMIT) this.entries.shift();
  }

  ready(window: ProbeWindow) {
    return this.entries.some((entry) => this.now() - entry.receivedAt < CACHE_TTL_MS
      && covers(entry.window, window)
      && (keyOf(entry.window) === keyOf(window) || entry.data.resolution_minutes <= requestedProbeResolution(window)));
  }

  preview(window: ProbeWindow): ZoneProbeReadings | null {
    const candidates = this.entries.filter((entry) => overlaps(entry.window, window));
    candidates.sort((a, b) => {
      const complete = Number(covers(b.window, window)) - Number(covers(a.window, window));
      if (complete) return complete;
      const resolution = a.data.resolution_minutes - b.data.resolution_minutes;
      return resolution || b.receivedAt - a.receivedAt;
    });
    return candidates[0] ? projectProbeReadings(candidates[0].data, window) : null;
  }
}

export type ProbeReadingsView = {
  data: ZoneProbeReadings | null;
  window: ProbeWindow;
  isLoading: boolean;
  error: unknown | null;
};
type FetchReadings = (window: ProbeWindow, signal: AbortSignal) => Promise<ZoneProbeReadings>;

/** One modal lifetime; no cache is shared across zones or organization contexts. */
export class ProbeReadingsLoader {
  private cache: ProbeReadingsCache;
  private display: { window: ProbeWindow; data: ZoneProbeReadings } | null = null;
  private selected: ProbeWindow | null = null;
  private controller: AbortController | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private loading = false;
  private error: unknown | null = null;
  private disposed = false;
  private sequence = 0;
  private contextAborted = false;
  private abortContext = () => {
    this.contextAborted = true;
    this.dispose();
    this.notify();
  };

  constructor(
    private zoneId: number,
    private fetch: FetchReadings,
    private notify: () => void,
    private contextSignal: AbortSignal,
    now: () => number = Date.now,
  ) { this.cache = new ProbeReadingsCache(now); }

  activate() {
    this.disposed = false;
    this.contextAborted = this.contextSignal.aborted;
    this.contextSignal.addEventListener('abort', this.abortContext, { once: true });
  }

  read(window: ProbeWindow): ProbeReadingsView {
    if (this.contextAborted || this.contextSignal.aborted) return { data: null, window, isLoading: false, error: null };
    const preview = this.cache.preview(window);
    const changed = !this.selected || keyOf(this.selected) !== keyOf(window);
    const display = preview ? { data: preview, window } : this.display;
    return {
      data: display?.data ?? null, window: display?.window ?? window,
      isLoading: changed ? !this.cache.ready(window) : this.loading,
      error: changed ? null : this.error,
    };
  }

  select(window: ProbeWindow, force = false) {
    if (this.disposed || this.contextSignal.aborted) return;
    if (!force && this.selected && keyOf(this.selected) === keyOf(window)
      && (this.loading || this.cache.ready(window))) return;
    const first = this.selected === null;
    this.cancel();
    this.selected = { ...window };
    this.error = null;
    const preview = this.cache.preview(window);
    if (preview) this.display = { window: { ...window }, data: preview };
    if (!force && this.cache.ready(window)) {
      this.loading = false;
      this.notify();
      return;
    }
    this.loading = true;
    const sequence = this.sequence;
    const retrieve = () => {
      this.timer = null;
      if (this.disposed || this.contextSignal.aborted || sequence !== this.sequence) return;
      const controller = new AbortController();
      this.controller = controller;
      this.fetch(window, controller.signal).then((data) => {
        if (this.disposed || controller.signal.aborted || sequence !== this.sequence || this.contextSignal.aborted) return;
        if (data.zone_id !== this.zoneId) throw new Error('Unexpected probe response.');
        this.cache.put(window, data);
        this.display = { window: { ...window }, data: projectProbeReadings(data, window) };
        this.loading = false;
        this.error = null;
        this.controller = null;
        this.notify();
      }).catch((error: unknown) => {
        if (this.disposed || controller.signal.aborted || sequence !== this.sequence || this.contextSignal.aborted) return;
        this.loading = false;
        this.error = error;
        this.controller = null;
        this.notify();
      });
    };
    this.notify();
    if (first) retrieve();
    else this.timer = setTimeout(retrieve, force ? 0 : PROBE_REQUEST_DEBOUNCE_MS);
  }

  retry() { if (this.selected) this.select(this.selected, true); }

  private cancel() {
    this.sequence += 1;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    this.controller?.abort();
    this.controller = null;
  }

  dispose() {
    this.disposed = true;
    this.cancel();
    this.cache.clear();
    this.display = null;
    this.selected = null;
    this.loading = false;
    this.error = null;
    this.contextSignal.removeEventListener('abort', this.abortContext);
  }
}
