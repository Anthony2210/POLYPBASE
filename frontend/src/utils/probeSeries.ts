import type { ZoneProbeReadings } from '../types';
import {
  nearestProbePointIndex, probeTemperatureDomain, splitProbeSegments, type ProbeWindow,
} from './probeChart';

/** One stored individual observation: [measured instant in ms, °C]. */
export type RawReading = readonly [number, number];

/** One condensed period. Its statistics describe the whole stored interval. */
export type CondensedInterval = {
  start: number;
  end: number;
  mean: number;
  minimum: number;
  maximum: number;
  count: number;
  lastMeasured: number;
};

export type ProbeSeries = {
  id: number;
  code: string;
  location: string;
  isActive: boolean;
  /** Fixed by server order, so a probe keeps its look when others are hidden. */
  slot: number;
  cadenceMs: number;
  readings: RawReading[];
  intervals: CondensedInterval[];
  minimum: number | null;
  maximum: number | null;
  latest: { time: number; value: number; condensed: boolean } | null;
};

type ProbeIdentity = Pick<ZoneProbeReadings['probes'][number], 'id' | 'code' | 'location' | 'is_active'>;

export const SERIES_STYLE_COUNT = 10;

/** Curve hues by slot; must match `.probe-series-N` in probe-charts.css. */
export const SERIES_COLORS = [
  '#2a78d6', '#eb6834', '#1baf7a', '#6250d6', '#e87ba4', '#eda100', '#008300', '#e34948', '#2a78d6', '#eb6834',
] as const;

const AVERAGE_MAX_LIGHTNESS = 0.52;

function srgbToLinear(channel: number) {
  const value = channel / 255;
  return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
}

function linearToSrgb(value: number) {
  const encoded = value <= 0.0031308 ? 12.92 * value : 1.055 * value ** (1 / 2.4) - 0.055;
  return Math.round(Math.min(1, Math.max(0, encoded)) * 255);
}

function toOklab(hex: string): [number, number, number] {
  const [r, g, b] = [1, 3, 5].map((index) => srgbToLinear(Number.parseInt(hex.slice(index, index + 2), 16)));
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

function fromOklab([lightness, a, b]: [number, number, number]) {
  const l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (lightness - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const channels = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
  return `#${channels.map((channel) => linearToSrgb(channel).toString(16).padStart(2, '0')).join('')}`;
}

/**
 * Display color of the inter-probe average: the perceptual (OKLab) mean of the
 * probe hues, so it reads as lying between them, darkened when needed for
 * legibility. A display choice only.
 */
export function averageSeriesColor(series: readonly Pick<ProbeSeries, 'slot'>[]) {
  if (!series.length) return null;
  const sum: [number, number, number] = [0, 0, 0];
  for (const entry of series) {
    const lab = toOklab(SERIES_COLORS[entry.slot % SERIES_STYLE_COUNT]);
    for (let index = 0; index < 3; index += 1) sum[index] += lab[index];
  }
  const [lightness, a, b] = sum.map((value) => value / series.length);
  // Mixed hues lose chroma and drift light; keep the thick line legible on the surface.
  return fromOklab([Math.min(lightness, AVERAGE_MAX_LIGHTNESS), a, b]);
}
const DEFAULT_CADENCE_SECONDS = 60;
const VALID_CADENCES = [60, 600, 900];

/**
 * Display assumption of this chart, not a scientific rule: the curve breaks
 * when two readings are more than three configured cadences apart. It is
 * separate from the freshness of latest readings and needs a product decision.
 */
export const GAP_CADENCE_MULTIPLIER = 3;

function cadenceSeconds(value: number | undefined) {
  return value !== undefined && VALID_CADENCES.includes(value) ? value : DEFAULT_CADENCE_SECONDS;
}

/** Convert one API response. A response is entirely raw or entirely condensed. */
export function buildProbeSeries(
  data: ZoneProbeReadings | null,
  fallbackProbes: readonly ProbeIdentity[] = [],
): ProbeSeries[] {
  const probes: ReadonlyArray<ProbeIdentity & Partial<ZoneProbeReadings['probes'][number]>> = data?.probes ?? fallbackProbes;
  const raw = data?.resolution_minutes === 1;
  return probes.map((probe, slot) => {
    const readings: RawReading[] = [];
    const intervals: CondensedInterval[] = [];
    let minimum: number | null = null;
    let maximum: number | null = null;
    let latest: ProbeSeries['latest'] = null;
    for (const sources of probe.point_sources ?? []) {
      for (const [start, end, sum, count, low, high, last] of sources) {
        const mean = Number(sum) / count;
        minimum = minimum === null ? low : Math.min(minimum, low);
        maximum = maximum === null ? high : Math.max(maximum, high);
        if (latest === null || last >= latest.time) latest = { time: last, value: mean, condensed: !raw };
        if (raw) readings.push([start, mean]);
        else {
          intervals.push({
            start, end: end ?? start + data!.resolution_minutes * 60_000,
            mean, minimum: low, maximum: high, count, lastMeasured: last,
          });
        }
      }
    }
    return {
      id: probe.id, code: probe.code, location: probe.location, isActive: probe.is_active,
      slot, cadenceMs: cadenceSeconds(probe.sampling_interval_seconds) * 1000,
      readings, intervals, minimum, maximum, latest,
    };
  });
}

export type ProbeAveragePeriod = { start: number; end: number; mean: number; contributors: number };

/** Preserve server period means, including zero; never derive them from individual curves. */
export function buildProbeAverage(data: ZoneProbeReadings | null): ProbeAveragePeriod[] {
  return (data?.probe_average ?? []).map(([start, mean, contributors]) => ({
    start, end: start + data!.resolution_minutes * 60_000, mean, contributors,
  }));
}

/** One Y domain for every probe, server average and target, independent of hidden curves. */
export function seriesTemperatureDomain(series: readonly ProbeSeries[], target: number | null, average: readonly ProbeAveragePeriod[] = []) {
  const bounds: number[] = [];
  for (const entry of series) {
    if (entry.minimum !== null) bounds.push(entry.minimum);
    if (entry.maximum !== null) bounds.push(entry.maximum);
  }
  for (const period of average) bounds.push(period.mean);
  return probeTemperatureDomain(bounds, target);
}

/** Never draw a line between readings separated by a genuine missing-data gap. */
export function splitReadingsAtGaps(readings: readonly RawReading[], cadenceMs: number) {
  return splitProbeSegments(readings, (cadenceMs * GAP_CADENCE_MULTIPLIER) / 60_000);
}

/** Keep each pixel column's first, minimum, maximum and last real readings, in time order. */
export function reduceReadingsForPixels(readings: readonly RawReading[], project: (time: number) => number) {
  const kept: RawReading[] = [];
  let column: RawReading[] = [];
  let columnIndex: number | null = null;
  function flush() {
    if (!column.length) return;
    const lowest = column.reduce((a, b) => (b[1] < a[1] ? b : a));
    const highest = column.reduce((a, b) => (b[1] > a[1] ? b : a));
    const selected = new Set([column[0], lowest, highest, column[column.length - 1]]);
    kept.push(...[...selected].sort((a, b) => a[0] - b[0]));
    column = [];
  }
  for (const reading of readings) {
    const next = Math.floor(project(reading[0]));
    if (next !== columnIndex) { flush(); columnIndex = next; }
    column.push(reading);
  }
  flush();
  return kept;
}

/** Merge contiguous intervals sharing a pixel column. Extrema and exact weights are preserved. */
export function binIntervalsForPixels(intervals: readonly CondensedInterval[], project: (time: number) => number) {
  const bins: CondensedInterval[] = [];
  let column: number | null = null;
  for (const interval of intervals) {
    const next = Math.floor(project(interval.start));
    const current = bins[bins.length - 1];
    if (current && next === column && interval.start <= current.end) {
      const count = current.count + interval.count;
      current.mean = (current.mean * current.count + interval.mean * interval.count) / count;
      current.count = count;
      current.end = Math.max(current.end, interval.end);
      current.minimum = Math.min(current.minimum, interval.minimum);
      current.maximum = Math.max(current.maximum, interval.maximum);
      current.lastMeasured = Math.max(current.lastMeasured, interval.lastMeasured);
    } else {
      bins.push({ ...interval });
      column = next;
    }
  }
  return bins;
}

export type SeriesCursorEntry =
  | { series: ProbeSeries; kind: 'raw'; time: number; value: number }
  | { series: ProbeSeries; kind: 'condensed'; interval: CondensedInterval };

/** The actual observation or stored interval of one probe at this instant, or null. */
export function seriesEntryAt(series: ProbeSeries, time: number): SeriesCursorEntry | null {
  if (series.intervals.length) {
    const interval = series.intervals.find((entry) => time >= entry.start && time < entry.end);
    return interval ? { series, kind: 'condensed', interval } : null;
  }
  const index = nearestProbePointIndex(series.readings, time);
  if (index < 0 || Math.abs(series.readings[index][0] - time) > series.cadenceMs) return null;
  return { series, kind: 'raw', time: series.readings[index][0], value: series.readings[index][1] };
}

/** Snap a pointer to the closest real instant, or keep it inside condensed intervals. */
export function snapCursorTime(series: readonly ProbeSeries[], time: number) {
  let best: number | null = null;
  for (const entry of series) {
    const index = nearestProbePointIndex(entry.readings, time);
    if (index < 0) continue;
    const candidate = entry.readings[index][0];
    if (best === null || Math.abs(candidate - time) < Math.abs(best - time)) best = candidate;
  }
  return best ?? time;
}

/** The next actual instant or interval start in a direction, across visible probes. */
export function stepCursorTime(series: readonly ProbeSeries[], from: number | null, direction: -1 | 1) {
  const instants = series.flatMap((entry) => entry.intervals.length
    ? entry.intervals.map((interval) => interval.start)
    : entry.readings.map((reading) => reading[0]));
  let best: number | null = null;
  for (const instant of instants) {
    if (from !== null && (direction > 0 ? instant <= from : instant >= from)) continue;
    if (best === null || (direction > 0 ? instant < best : instant > best)) best = instant;
  }
  return best;
}

export function hasPartialCoverage(series: readonly ProbeSeries[], window: ProbeWindow) {
  return series.some((entry) => entry.intervals.some((interval) => interval.start < window.start || interval.end > window.end));
}

export type ProbeRecentChange = { id: number; code: string; delta: number; from: number; to: number };

/**
 * Factual change of each probe between its own first and last individual
 * readings in the window. No averaging across probes and no threshold.
 */
export function probeRecentChanges(series: readonly ProbeSeries[]): ProbeRecentChange[] {
  return series.flatMap((entry) => {
    if (entry.readings.length < 2) return [];
    const first = entry.readings[0];
    const last = entry.readings[entry.readings.length - 1];
    return [{
      id: entry.id, code: entry.code, delta: Math.round((last[1] - first[1]) * 100) / 100, from: first[0], to: last[0],
    }];
  });
}
